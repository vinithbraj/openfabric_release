"""LLM client wrapper that emits operational trace events."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from agent_runtime.core.user_errors import user_error_detail
from agent_runtime.observability.agent_trace import AgentTraceEvent, AgentTraceStore


def infer_stage_from_prompt(prompt: str) -> str:
    """Infer the runtime stage from the structured prompt text."""

    lowered = str(prompt or "").lower()
    if "openfabric advisory mode" in lowered or "advisory mode" in lowered:
        return "advisory"
    if "llm operator" in lowered or "operatorplan" in lowered or "operatorrepair" in lowered:
        return "llm_operator"
    if "directly answering a user prompt" in lowered:
        return "direct_answer"
    if "non-authoritative prompt normalization" in lowered:
        return "prompt_rephrase"
    if "classifying a user prompt" in lowered:
        return "prompt_classification"
    if "decomposing a user prompt" in lowered or (
        "decomposition" in lowered and "critique" in lowered
    ):
        return "decomposition"
    if "assigning semantic verbs" in lowered:
        return "verb_assignment"
    if "selecting capability candidates" in lowered:
        return "capability_selection"
    if "downstream task can be satisfied" in lowered or "upstream declared outputs" in lowered:
        return "output_contract_overlap_review"
    if "selected capability truly fits" in lowered:
        return "capability_fit"
    if "extracting typed arguments" in lowered:
        return "argument_extraction"
    if "sanitized action dag" in lowered or "dag review" in lowered:
        return "dag_review"
    if "dataflow" in lowered:
        return "dataflow_planning"
    if "failure repair" in lowered or "repair proposal" in lowered:
        return "failure_repair"
    if "safe display plan" in lowered:
        return "output_planning"
    return "llm"


class TracingLLMClient:
    """Wrap an LLM client and record prompts, responses, and errors."""

    def __init__(
        self,
        inner: Any,
        store: AgentTraceStore,
        request_id: str,
        *,
        streaming_enabled: bool = False,
    ) -> None:
        self.inner = inner
        self.store = store
        self.request_id = request_id
        self.model = str(getattr(inner, "model", "unknown") or "unknown")
        self.temperature = getattr(inner, "temperature", None)
        self.streaming_enabled = bool(streaming_enabled)

    @staticmethod
    def _stable_json(value: Any) -> str:
        """Serialize one payload for approximate token accounting."""

        try:
            return json.dumps(value, sort_keys=True, default=str, ensure_ascii=True)
        except Exception:
            return str(value)

    @classmethod
    def _estimate_tokens(cls, value: Any) -> int:
        """Return a simple token estimate for local operational tracing."""

        text = cls._stable_json(value) if not isinstance(value, str) else value
        stripped = str(text or "").strip()
        if not stripped:
            return 0
        return max(1, round(len(stripped) / 4))

    @staticmethod
    def _stats_markdown(
        *,
        model: str,
        input_tokens: int,
        output_tokens: int | None,
        duration_ms: float | None,
        status: str,
    ) -> str:
        """Return a compact Markdown table for one LLM exchange."""

        output_value = "~" + str(output_tokens) if output_tokens is not None else "-"
        total_value = (
            "~" + str(input_tokens + output_tokens)
            if output_tokens is not None
            else "-"
        )
        duration_value = f"{duration_ms:.0f} ms" if duration_ms is not None else "-"
        return "\n".join(
            [
                "| metric | value |",
                "| --- | --- |",
                f"| model | `{model}` |",
                f"| input tokens | ~{input_tokens} |",
                f"| output tokens | {output_value} |",
                f"| total tokens | {total_value} |",
                f"| time | {duration_value} |",
                f"| status | `{status}` |",
            ]
        )

    @staticmethod
    def _partial_json_string_field(text: str, field: str) -> str:
        """Best-effort extraction of one JSON string field from partial model text."""

        source = str(text or "")
        match = re.search(rf'"{re.escape(field)}"\s*:\s*"', source)
        if not match:
            return ""
        index = match.end()
        chars: list[str] = []
        escaped = False
        while index < len(source):
            char = source[index]
            index += 1
            if escaped:
                if char == "n":
                    chars.append("\n")
                elif char == "r":
                    chars.append("\r")
                elif char == "t":
                    chars.append("\t")
                elif char == "u" and index + 4 <= len(source):
                    hex_value = source[index : index + 4]
                    try:
                        chars.append(chr(int(hex_value, 16)))
                        index += 4
                    except ValueError:
                        chars.append("\\u")
                else:
                    chars.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                break
            chars.append(char)
        return "".join(chars)

    @staticmethod
    def _streaming_supported(inner: Any) -> bool:
        """Return whether the wrapped client exposes streaming structured calls."""

        return callable(getattr(inner, "complete_json_stream", None))

    def _complete_json_inner(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        stage: str,
        schema_name: str,
        call_id: str,
        input_tokens: int,
        started_at: float,
    ) -> dict[str, Any]:
        """Run the inner LLM call, streaming trace deltas when enabled."""

        if not self.streaming_enabled or not self._streaming_supported(self.inner):
            return self.inner.complete_json(prompt, schema)

        pending_chunks: list[str] = []
        accumulated_chunks: list[str] = []
        last_emit_at = started_at
        user_facing_stream = stage == "direct_answer" and schema_name == "DirectAnswerProposal"

        def emit_delta(*, force: bool = False) -> None:
            nonlocal last_emit_at
            if not pending_chunks:
                return
            now = time.perf_counter()
            pending_text = "".join(pending_chunks)
            if not force and len(pending_text) < 512 and (now - last_emit_at) < 0.15:
                return
            accumulated_text = "".join(accumulated_chunks)
            duration_ms = (now - started_at) * 1000
            detail: dict[str, Any] = {
                "call_id": call_id,
                "model": self.model,
                "temperature": self.temperature,
                "schema_name": schema_name,
                "streaming_enabled": True,
                "user_facing_stream": user_facing_stream,
                "delta_char_count": len(pending_text),
                "accumulated_char_count": len(accumulated_text),
                "delta": pending_text,
                "accumulated_text": accumulated_text[-20000:],
                "input_tokens_estimate": input_tokens,
                "output_tokens_estimate": self._estimate_tokens(accumulated_text),
                "total_tokens_estimate": input_tokens + self._estimate_tokens(accumulated_text),
                "duration_ms": round(duration_ms, 2),
                "llm_stats_markdown": self._stats_markdown(
                    model=self.model,
                    input_tokens=input_tokens,
                    output_tokens=self._estimate_tokens(accumulated_text),
                    duration_ms=duration_ms,
                    status="streaming",
                ),
            }
            if user_facing_stream:
                detail["user_facing_text"] = self._partial_json_string_field(accumulated_text, "answer")
            self.store.append_event(
                AgentTraceEvent(
                    request_id=self.request_id,
                    stage=stage,
                    level="debug",
                    event_type="llm.response.delta",
                    title="LLM response streaming",
                    summary="The model streamed a partial response chunk.",
                    detail=detail,
                    llm_prompt=prompt,
                    llm_response=accumulated_text[-20000:],
                )
            )
            pending_chunks.clear()
            last_emit_at = now

        def on_delta(delta: str) -> None:
            text = str(delta or "")
            if not text:
                return
            accumulated_chunks.append(text)
            pending_chunks.append(text)
            emit_delta()

        response = self.inner.complete_json_stream(prompt, schema, on_delta)
        emit_delta(force=True)
        return response

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Forward one structured LLM call and emit debug trace events."""

        stage = infer_stage_from_prompt(prompt)
        input_tokens = self._estimate_tokens(prompt) + self._estimate_tokens(schema)
        started_at = time.perf_counter()
        schema_name = str(schema.get("title") or schema.get("$id") or "json_schema")
        call_id = f"{self.request_id}:{stage}:{schema_name}:{time.perf_counter_ns()}"
        self.store.append_event(
            AgentTraceEvent(
                request_id=self.request_id,
                stage=stage,
                level="debug",
                event_type="llm.request",
                title="LLM request sent",
                summary="The runtime sent a structured prompt to the configured model.",
                detail={
                    "call_id": call_id,
                    "model": self.model,
                    "temperature": self.temperature,
                    "schema_name": schema_name,
                    "streaming_enabled": self.streaming_enabled and self._streaming_supported(self.inner),
                    "input_tokens_estimate": input_tokens,
                    "output_tokens_estimate": None,
                    "total_tokens_estimate": None,
                    "duration_ms": None,
                    "llm_stats_markdown": self._stats_markdown(
                        model=self.model,
                        input_tokens=input_tokens,
                        output_tokens=None,
                        duration_ms=None,
                        status="sent",
                    ),
                },
                llm_prompt=prompt,
            )
        )
        try:
            response = self._complete_json_inner(
                prompt=prompt,
                schema=schema,
                stage=stage,
                schema_name=schema_name,
                call_id=call_id,
                input_tokens=input_tokens,
                started_at=started_at,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            error_detail = user_error_detail(
                exc,
                stage=stage,
                category="llm_error",
                metadata={
                    "call_id": call_id,
                    "model": self.model,
                    "schema_name": schema_name,
                },
                request_id=self.request_id,
            )
            self.store.append_event(
                AgentTraceEvent(
                    request_id=self.request_id,
                    stage=stage,
                    level="error",
                    event_type="llm.error",
                    title="LLM request failed",
                    summary="The structured LLM request failed before producing a usable response.",
                    detail={
                        "call_id": call_id,
                        "model": self.model,
                        "temperature": self.temperature,
                        "schema_name": schema_name,
                        "streaming_enabled": self.streaming_enabled and self._streaming_supported(self.inner),
                        "error_class": type(exc).__name__,
                        "error_detail": error_detail,
                        "input_tokens_estimate": input_tokens,
                        "output_tokens_estimate": None,
                        "total_tokens_estimate": None,
                        "duration_ms": round(duration_ms, 2),
                        "llm_stats_markdown": self._stats_markdown(
                            model=self.model,
                            input_tokens=input_tokens,
                            output_tokens=None,
                            duration_ms=duration_ms,
                            status="error",
                        ),
                    },
                    llm_prompt=prompt,
                    error=str(exc),
                )
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        output_tokens = self._estimate_tokens(response)
        self.store.append_event(
            AgentTraceEvent(
                request_id=self.request_id,
                stage=stage,
                level="debug",
                event_type="llm.response",
                title="LLM response received",
                summary="The runtime received a structured response from the model.",
                detail={
                    "call_id": call_id,
                    "model": self.model,
                    "temperature": self.temperature,
                    "schema_name": schema_name,
                    "streaming_enabled": self.streaming_enabled and self._streaming_supported(self.inner),
                    "input_tokens_estimate": input_tokens,
                    "output_tokens_estimate": output_tokens,
                    "total_tokens_estimate": input_tokens + output_tokens,
                    "duration_ms": round(duration_ms, 2),
                    "llm_stats_markdown": self._stats_markdown(
                        model=self.model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        duration_ms=duration_ms,
                        status="ok",
                    ),
                },
                llm_prompt=prompt,
                llm_response=response,
                parsed_output=response,
            )
        )
        return response


__all__ = ["TracingLLMClient", "infer_stage_from_prompt"]
