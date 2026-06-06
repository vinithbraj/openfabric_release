"""User-facing error classification for agent-run failures."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.llm.client import LLMClientError
from agent_runtime.llm.structured_call import StructuredCallError


_SECRET_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s,;]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(api[_ -]?key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(token['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(password['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;]+"), r"\1[redacted]"),
)
_HTTP_STATUS_RE = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)
_LLM_TEXT_RE = re.compile(r"\b(llm|model|openai|vllm|structured)\b", re.IGNORECASE)
_VALIDATION_TEXT_RE = re.compile(
    r"\b(validation error|schema_validation_error|pydantic|expected schema|contract)\b",
    re.IGNORECASE,
)
_EXECUTION_TEXT_RE = re.compile(
    r"\b(command failed|exit code|stderr|gateway|operator execution|execution failed)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UserFacingErrorDetail:
    """Structured, sanitized detail shown in agent-run views and traces."""

    title: str
    category: str
    stage: str
    message_markdown: str
    likely_cause: str
    fix_hint: str
    technical_detail: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "category": self.category,
            "stage": self.stage,
            "message_markdown": self.message_markdown,
            "likely_cause": self.likely_cause,
            "fix_hint": self.fix_hint,
            "technical_detail": self.technical_detail,
            "metadata": dict(self.metadata),
        }


def _bounded_text(value: Any, *, limit: int = 2000) -> str:
    text = "\n".join(line.rstrip() for line in str(value or "").splitlines()).strip()
    for pattern, replacement in _SECRET_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _one_line(value: Any, *, limit: int = 500) -> str:
    return " ".join(_bounded_text(value, limit=limit).split())[:limit].strip()


def _markdown_code_block(value: Any, *, language: str = "text", limit: int = 2000) -> str:
    text = _bounded_text(value, limit=limit)
    if not text:
        return ""
    fence = "````" if "```" in text else "```"
    return f"{fence}{language}\n{text}\n{fence}"


def _metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        return _bounded_text(value, limit=1000)
    if isinstance(value, list):
        return [_metadata_value(item) for item in value[:20]]
    if isinstance(value, tuple):
        return [_metadata_value(item) for item in list(value)[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _metadata_value(item)
            for key, item in list(value.items())[:40]
            if str(key).lower() not in {"authorization", "api_key", "token", "password"}
        }
    return _bounded_text(value, limit=1000)


def _safe_metadata(
    *,
    error: Any,
    context: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    request_id: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if request_id:
        result["request_id"] = _one_line(request_id, limit=120)
    if isinstance(context, dict):
        for key in (
            "llm_base_url",
            "llm_model",
            "active_llm_model",
            "gateway_id",
            "gateway_node",
            "workspace_root",
            "conversation_id",
        ):
            value = context.get(key)
            if value not in (None, "", []):
                result[key] = _metadata_value(value)
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if str(key).lower() in {"authorization", "api_key", "token", "password"}:
                continue
            if value not in (None, "", []):
                result[str(key)[:120]] = _metadata_value(value)
    if isinstance(error, BaseException):
        result.setdefault("exception_type", type(error).__name__)
    return result


def _http_status(text: str) -> int | None:
    match = _HTTP_STATUS_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _diagnostics_from_error(error: Any) -> tuple[str, str, dict[str, Any]]:
    if isinstance(error, StructuredCallError):
        diagnostics = error.diagnostics
        metadata = diagnostics.model_dump(mode="json")
        detail = diagnostics.error_message
        if diagnostics.schema_name:
            detail = f"{detail} Schema: {diagnostics.schema_name}."
        return diagnostics.error_kind, detail, metadata
    if isinstance(error, LLMClientError):
        return (
            str(error.error_kind or "transport_error"),
            str(error.error_message or error),
            {
                "error_kind": error.error_kind,
                "raw_response_preview": error.raw_response_preview,
                "raw_payload_preview": error.raw_payload_preview,
            },
        )
    return "", "", {}


def _llm_detail(
    *,
    stage: str,
    text: str,
    error_kind: str,
    metadata: dict[str, Any],
) -> tuple[str, str, str, str, str, dict[str, Any]]:
    http_status = _http_status(text)
    lower = text.lower()
    category = "llm_response_error"
    title = "Model returned an unusable response"
    likely = "The configured model responded, but the runtime could not use the response safely."
    fix = "Retry the request. If this repeats, switch models or lower output size/settings."
    if (
        error_kind == "transport_error"
        or "transport" in lower
        or "connection refused" in lower
        or "not reachable" in lower
        or "unavailable" in lower
        or "requires a configured llm client" in lower
    ):
        category = "llm_transport_error"
        title = "LLM service unavailable or rejected the request"
        likely = "The configured LLM endpoint was unreachable, unavailable, or rejected the call."
        fix = "Check Mission Control LLM settings, confirm the model server is running, then retry."
    elif error_kind == "empty_response":
        category = "llm_empty_response"
        title = "Model returned an empty response"
        likely = "The model call completed without usable assistant content."
        fix = "Retry the request. If it repeats, check the selected model and max-token settings."
    elif error_kind in {"invalid_json", "schema_validation_error", "parsing_error"}:
        category = f"llm_{error_kind}"
        if error_kind == "schema_validation_error":
            title = "Model response did not match the expected schema"
            likely = "The model returned JSON, but it did not match the typed runtime contract."
        elif error_kind == "invalid_json":
            title = "Model returned invalid JSON"
            likely = "The model response could not be parsed as the structured JSON the runtime expected."
        else:
            title = "Model response could not be parsed"
            likely = "The runtime could not parse the model response into the requested structure."
        fix = "Retry once. If this repeats, choose a stronger instruction-following model or simplify the request."
    if http_status is not None:
        metadata["http_status"] = http_status
    technical = _one_line(text, limit=1000) or "LLM request failed."
    return title, category, likely, fix, technical, metadata


def _classify_error(
    error: Any,
    *,
    stage: str,
    category: str | None,
    metadata: dict[str, Any],
) -> tuple[str, str, str, str, str, dict[str, Any]]:
    diagnostic_kind, diagnostic_message, diagnostic_metadata = _diagnostics_from_error(error)
    if diagnostic_metadata:
        metadata.update(diagnostic_metadata)
    raw_text = diagnostic_message or str(error or "")
    text = _bounded_text(raw_text, limit=2000)
    normalized_category = str(category or "").strip()
    lower = text.lower()

    if diagnostic_kind or normalized_category.startswith("llm") or _LLM_TEXT_RE.search(text):
        return _llm_detail(
            stage=stage,
            text=text,
            error_kind=diagnostic_kind,
            metadata=metadata,
        )
    if isinstance(error, PermissionError) or "permission denied" in lower:
        return (
            "Permission error while handling the request",
            "permission_error",
            "The runtime could not access a required file, directory, command, or service.",
            "Check permissions for the referenced resource, then retry.",
            _one_line(text or error, limit=1000),
            metadata,
        )
    if normalized_category in {"execution_error"} or _EXECUTION_TEXT_RE.search(text):
        return (
            "Command or gateway execution failed",
            "execution_error",
            "A planned action ran but returned an error, failed exit code, or gateway failure.",
            "Open the trace output, fix the command/gateway condition shown there, then retry or continue.",
            _one_line(text or error, limit=1000),
            metadata,
        )
    if isinstance(error, FileNotFoundError) or "no such file" in lower or "not found" in lower:
        return (
            "Required file or resource was not found",
            "not_found_error",
            "The request referenced a path or resource that was missing.",
            "Verify the path/resource exists and is inside the configured workspace, then retry.",
            _one_line(text or error, limit=1000),
            metadata,
        )
    if normalized_category == "safety_block":
        return (
            "Request blocked by safety policy",
            "safety_block",
            "The runtime policy blocked one or more planned actions before execution.",
            "Review the blocked action details and rerun with a safer or more specific request.",
            _one_line(text or error, limit=1000),
            metadata,
        )
    if (
        normalized_category in {"validation_error"}
        or isinstance(error, ValueError)
        or _VALIDATION_TEXT_RE.search(text)
    ):
        return (
            "Runtime validation failed",
            "validation_error",
            "The runtime rejected an intermediate plan or payload because it did not match a typed contract.",
            "Retry the request. If this repeats, simplify the request or inspect the validation detail in the trace.",
            _one_line(text or error, limit=1000),
            metadata,
        )
    exception_name = type(error).__name__ if isinstance(error, BaseException) else ""
    title = "Runtime error while handling the request"
    technical = _one_line(
        f"{exception_name}: {text}" if exception_name and exception_name not in text else text,
        limit=1000,
    )
    likely = (
        f"The runtime raised {exception_name} before producing a final response."
        if exception_name
        else "The runtime stopped before producing a final response."
    )
    fix = "Open the trace for this request, use the technical detail below, then retry after fixing the cause."
    return title, normalized_category or "unexpected_error", likely, fix, technical, metadata


def _message_markdown(
    *,
    title: str,
    likely_cause: str,
    fix_hint: str,
    technical_detail: str,
    stage: str,
    category: str,
) -> str:
    lines = [
        f"## {title}",
        "",
        f"**Stage:** `{_one_line(stage, limit=80) or 'runtime'}`",
        f"**Category:** `{_one_line(category, limit=120) or 'unexpected_error'}`",
        "",
        f"**Likely cause:** {likely_cause}",
        "",
        f"**How to fix:** {fix_hint}",
    ]
    if technical_detail:
        lines.extend(["", "**Technical detail**", _markdown_code_block(technical_detail, limit=1200)])
    return "\n".join(lines).strip()


def user_error_detail(
    error: Any,
    *,
    stage: str = "runtime",
    category: str | None = None,
    context: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    """Return sanitized, actionable error detail for agent-run surfaces."""

    safe_stage = _one_line(stage, limit=120) or "runtime"
    base_metadata = _safe_metadata(
        error=error,
        context=context,
        metadata=metadata,
        request_id=request_id,
    )
    title, final_category, likely, fix, technical, final_metadata = _classify_error(
        error,
        stage=safe_stage,
        category=category,
        metadata=base_metadata,
    )
    final_category = _one_line(final_category, limit=120) or "unexpected_error"
    detail = UserFacingErrorDetail(
        title=title,
        category=final_category,
        stage=safe_stage,
        message_markdown=_message_markdown(
            title=title,
            likely_cause=likely,
            fix_hint=fix,
            technical_detail=technical,
            stage=safe_stage,
            category=final_category,
        ),
        likely_cause=likely,
        fix_hint=fix,
        technical_detail=technical,
        metadata=final_metadata,
    )
    return detail.as_dict()


def user_error_message(error_detail: dict[str, Any] | None, fallback: Any = "") -> str:
    """Return the markdown message from one structured error detail."""

    if isinstance(error_detail, dict):
        message = str(error_detail.get("message_markdown") or "").strip()
        if message:
            return message
    return _bounded_text(fallback or "Request failed.", limit=4000) or "Request failed."


__all__ = ["user_error_detail", "user_error_message", "UserFacingErrorDetail"]
