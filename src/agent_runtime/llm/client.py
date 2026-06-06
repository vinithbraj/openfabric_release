"""LLM client interfaces.

The foundation defines the boundary and includes a small OpenAI-compatible JSON
client so the agent runtime can call a real model without extra dependencies.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent_runtime.prompts import render_prompt


StreamingDeltaCallback = Callable[[str], None]


class LLMClient(Protocol):
    """Protocol for structured LLM calls."""

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON-compatible object matching the requested schema."""

def _truncate_preview(value: Any, max_length: int = 1000) -> str | None:
    """Return a safe truncated preview for diagnostics."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...[truncated]"


class LLMClientError(RuntimeError):
    """Typed structured-call transport/formatting failure from the LLM client."""

    def __init__(
        self,
        *,
        error_kind: str,
        error_message: str,
        raw_response_preview: str | None = None,
        raw_payload_preview: str | None = None,
    ) -> None:
        super().__init__(error_message)
        self.error_kind = error_kind
        self.error_message = error_message
        self.raw_response_preview = raw_response_preview
        self.raw_payload_preview = raw_payload_preview


class StaticLLMClient:
    """Test placeholder that always returns a configured payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return the static payload without contacting an external model."""

        return dict(self.payload)


def _coerce_message_content(content: Any) -> str:
    """Normalize OpenAI-compatible message content into one text string."""

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _strip_json_fences(text: str) -> str:
    """Remove common markdown code fences around JSON output."""

    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from model output text."""

    candidate = _strip_json_fences(text)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(candidate[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("LLM response did not contain a valid JSON object.")


class OpenAICompatLLMClient:
    """Tiny OpenAI-compatible structured JSON client using stdlib HTTP."""

    AUTO_MODEL_NAMES = {"", "auto"}

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "").strip()
        self._configured_model = str(model or "").strip() or "auto"
        self._resolved_model: str | None = None
        self.timeout_seconds = float(timeout_seconds)
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens) if max_tokens and int(max_tokens) > 0 else None

    @property
    def model(self) -> str:
        """Configured or discovered model id used for LLM requests."""

        return self._resolved_model or self._configured_model

    @model.setter
    def model(self, value: str) -> None:
        self._configured_model = str(value or "").strip() or "auto"
        self._resolved_model = None

    def _extract_model_id(self, payload: Any) -> str:
        """Extract the first model id from an OpenAI-compatible models payload."""

        candidates: list[Any]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                candidates = data
            else:
                models = payload.get("models")
                candidates = models if isinstance(models, list) else []
        elif isinstance(payload, list):
            candidates = payload
        else:
            candidates = []

        for item in candidates:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                for key in ("id", "name", "model"):
                    value = str(item.get(key) or "").strip()
                    if value:
                        return value
        raise LLMClientError(
            error_kind="empty_response",
            error_message="LLM model auto-discovery did not find a usable model id.",
            raw_payload_preview=_truncate_preview(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)),
        )

    def _discover_model(self) -> str:
        """Ask the OpenAI-compatible server which model id it is serving."""

        request = Request(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise LLMClientError(
                error_kind="transport_error",
                error_message=f"LLM model auto-discovery failed with HTTP {exc.code}.",
            ) from exc
        except URLError as exc:
            raise LLMClientError(
                error_kind="transport_error",
                error_message=f"LLM model auto-discovery failed: {exc.reason}",
            ) from exc
        except json.JSONDecodeError as exc:
            raise LLMClientError(
                error_kind="invalid_json",
                error_message="LLM model auto-discovery returned invalid JSON.",
            ) from exc
        return self._extract_model_id(payload)

    def _resolve_model(self) -> str:
        configured = self._configured_model.strip()
        if configured.lower() not in self.AUTO_MODEL_NAMES:
            return configured
        if self._resolved_model:
            return self._resolved_model
        self._resolved_model = self._discover_model()
        return self._resolved_model

    def _request_payload(self, prompt: str, schema: dict[str, Any], include_response_format: bool) -> dict[str, Any]:
        """Build an OpenAI-compatible chat completion payload."""

        schema_json = json.dumps(schema, separators=(",", ":"))
        payload: dict[str, Any] = {
            "model": self._resolve_model(),
            "temperature": self.temperature,
            "messages": [
                {
                    "role": "system",
                    "content": render_prompt("llm.complete_json.system"),
                },
                {
                    "role": "user",
                    "content": render_prompt(
                        "llm.complete_json.user",
                        {"prompt": prompt, "schema_json": schema_json},
                    ),
                },
            ],
        }
        if include_response_format:
            payload["response_format"] = {"type": "json_object"}
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        return payload

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST one payload to the configured chat-completions endpoint."""

        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _iter_response_lines(response: Any):
        """Yield raw response lines from stdlib or test response objects."""

        if hasattr(response, "__iter__"):
            for line in response:
                yield bytes(line) if isinstance(line, bytearray) else line
            return
        body = response.read()
        for line in body.splitlines():
            yield line

    @staticmethod
    def _stream_delta_from_payload(payload: Any) -> str:
        """Extract one text delta from an OpenAI-compatible streaming payload."""

        if not isinstance(payload, dict):
            return ""
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        parts.append(str(item.get("text") or item.get("content") or ""))
                return "".join(parts)
            return str(content or "")
        message = choice.get("message")
        if isinstance(message, dict):
            return _coerce_message_content(message.get("content"))
        return ""

    def _post_stream(self, payload: dict[str, Any], on_delta: StreamingDeltaCallback) -> str:
        """POST one streaming chat-completions request and return full text."""

        stream_payload = dict(payload)
        stream_payload["stream"] = True
        body = json.dumps(stream_payload).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        chunks: list[str] = []
        with urlopen(request, timeout=self.timeout_seconds) as response:
            for raw_line in self._iter_response_lines(response):
                if isinstance(raw_line, str):
                    line = raw_line.strip()
                else:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = self._stream_delta_from_payload(payload)
                if not delta:
                    continue
                chunks.append(delta)
                on_delta(delta)
        return "".join(chunks)

    def complete_json_stream(
        self,
        prompt: str,
        schema: dict[str, Any],
        on_delta: StreamingDeltaCallback,
    ) -> dict[str, Any]:
        """Call the model with OpenAI-compatible streaming and parse the final JSON."""

        content = ""
        last_error: Exception | None = None
        for include_response_format in (True, False):
            try:
                content = self._post_stream(
                    self._request_payload(prompt, schema, include_response_format),
                    on_delta,
                )
                break
            except HTTPError as exc:
                last_error = exc
                if include_response_format and exc.code in {400, 404, 415, 422, 500}:
                    continue
                if exc.code in {400, 404, 405, 415, 422, 500, 501}:
                    return self.complete_json(prompt, schema)
                raise LLMClientError(
                    error_kind="transport_error",
                    error_message=f"LLM streaming request failed with HTTP {exc.code}.",
                ) from exc
            except URLError as exc:
                raise LLMClientError(
                    error_kind="transport_error",
                    error_message=f"LLM streaming request failed: {exc.reason}",
                ) from exc
        if not content:
            raise LLMClientError(
                error_kind="empty_response",
                error_message="LLM streaming response message content was empty.",
            ) from last_error
        try:
            return _extract_json_object(content)
        except ValueError as exc:
            raise LLMClientError(
                error_kind="invalid_json",
                error_message=str(exc),
                raw_response_preview=_truncate_preview(content, max_length=6000),
            ) from exc

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Call the model and parse a JSON object from the assistant message."""

        response_payload: dict[str, Any] | None = None
        last_error: Exception | None = None
        for include_response_format in (True, False):
            try:
                response_payload = self._post(self._request_payload(prompt, schema, include_response_format))
                break
            except HTTPError as exc:
                last_error = exc
                if include_response_format and exc.code in {400, 404, 415, 422, 500}:
                    continue
                raise LLMClientError(
                    error_kind="transport_error",
                    error_message=f"LLM request failed with HTTP {exc.code}.",
                ) from exc
            except URLError as exc:
                raise LLMClientError(
                    error_kind="transport_error",
                    error_message=f"LLM request failed: {exc.reason}",
                ) from exc
        if response_payload is None:
            raise LLMClientError(
                error_kind="transport_error",
                error_message="LLM request failed.",
            ) from last_error

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMClientError(
                error_kind="empty_response",
                error_message="LLM response did not include choices.",
                raw_payload_preview=_truncate_preview(
                    json.dumps(response_payload, sort_keys=True, default=str, ensure_ascii=True)
                ),
            )
        message = choices[0].get("message", {})
        content = _coerce_message_content(message.get("content"))
        if not content:
            raise LLMClientError(
                error_kind="empty_response",
                error_message="LLM response message content was empty.",
                raw_payload_preview=_truncate_preview(
                    json.dumps(response_payload, sort_keys=True, default=str, ensure_ascii=True)
                ),
            )
        try:
            return _extract_json_object(content)
        except ValueError as exc:
            raise LLMClientError(
                error_kind="invalid_json",
                error_message=str(exc),
                raw_response_preview=_truncate_preview(content, max_length=6000),
                raw_payload_preview=_truncate_preview(
                    json.dumps(response_payload, sort_keys=True, default=str, ensure_ascii=True)
                ),
            ) from exc
