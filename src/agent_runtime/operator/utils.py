"""Small shared utility helpers for the operator pipeline."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator._shared import *

def _cancel_requested(context: dict[str, Any] | None) -> bool:
    """Return whether the request-scoped cancellation token has been set."""

    token = dict(context or {}).get("cancel_event")
    return bool(getattr(token, "is_set", lambda: False)())

def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, indent=2, default=str)
    except Exception:
        return str(value)


def _operator_planning_trace_metadata(user_request: UserRequest) -> dict[str, Any] | None:
    """Return mutable planning trace metadata if a request is trace-bound."""

    trace = dict(getattr(user_request, "safety_context", {}) or {}).get("planning_trace")
    metadata = getattr(trace, "metadata", None)
    return metadata if isinstance(metadata, dict) else None


def _memory_tag_tokens(value: Any, *, limit: int = 32) -> list[str]:
    """Return compact tags used only for targeted memory retrieval."""

    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", str(value or "").lower()):
        if len(token) <= 1 or token in _MEMORY_TAG_STOPWORDS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


def _execution_learning_safe_text(value: Any) -> str:
    """Return bounded text suitable for memory digest consideration."""

    return _truncate(str(value or "").strip(), 4000)


def _execution_learning_has_sensitive_text(*values: Any) -> bool:
    """Return whether text contains likely private credentials or auth material."""

    return any(_EXECUTION_LEARNING_SENSITIVE_RE.search(str(value or "")) for value in values)


_GENERATED_TEXT_VERB_RE = re.compile(
    r"\b(?:generate|generated|draft|drafted|compose|composed|write|written|summari[sz]e)\b",
    re.IGNORECASE,
)
_GENERATED_TEXT_NOUN_RE = re.compile(
    r"\b(?:message|description|summary|title|subject|body|text)\b",
    re.IGNORECASE,
)


def _operator_generated_text_context(user_request: UserRequest) -> str:
    context = dict(getattr(user_request, "session_context", {}) or {})
    fields = (
        "description",
        "goal",
        "semantic_verb",
        "object_type",
        "operation_intent",
        "side_effect_type",
    )
    current_task = context.get("operator_streaming_current_task")
    if isinstance(current_task, dict):
        parts = [str(current_task.get(field) or "") for field in fields]
        return " ".join(part for part in parts if part)

    parts: list[str] = [str(getattr(user_request, "raw_prompt", "") or "")]
    intent_block = context.get("operator_intent_block")
    if isinstance(intent_block, dict):
        parts.extend(str(intent_block.get(field) or "") for field in fields)
    return " ".join(part for part in parts if part)


def operator_text_generation_requested_in_text(text: str) -> bool:
    """Return whether free text asks for generated natural-language text."""

    text = re.sub(r"""(["']).*?\1""", " ", str(text or ""))
    return bool(
        _GENERATED_TEXT_VERB_RE.search(text)
        and _GENERATED_TEXT_NOUN_RE.search(text)
    )


def operator_request_requires_generated_text(user_request: UserRequest) -> bool:
    """Return whether this step is asking for generated natural-language text."""

    return operator_text_generation_requested_in_text(_operator_generated_text_context(user_request))


def _shell_tool_name(command: str) -> str:
    """Return the first useful executable token from a shell command."""

    try:
        parts = shlex.split(str(command or ""), comments=False, posix=True)
    except ValueError:
        parts = str(command or "").split()
    skip_tokens = {
        "cd",
        "command",
        "env",
        "if",
        "then",
        "test",
        "timeout",
        "time",
        "sudo",
    }
    for part in parts:
        token = str(part or "").strip()
        if not token or "=" in token:
            continue
        name = Path(token).name.lower()
        if name in skip_tokens:
            continue
        return name
    return ""


def _truncate(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars)] + "\n...[truncated]"

__all__ = [name for name in globals() if not name.startswith("__")]
