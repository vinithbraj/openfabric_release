"""Formatting and display helpers for AgentRuntime orchestration."""

from __future__ import annotations

from agent_runtime.core.user_errors import user_error_detail, user_error_message

from .common import Any, _RAW_SYSTEM_MESSAGE_MARKERS, json, re

def _looks_like_raw_system_message(message: Any) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _RAW_SYSTEM_MESSAGE_MARKERS):
        return True
    return "\n" in text and len(text) > 120


def _markdown_code_block(text: Any, *, language: str = "text") -> str:
    raw = str(text or "").strip()
    fence = "````" if "```" in raw else "```"
    return f"{fence}{language}\n{raw}\n{fence}"


def _pretty_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _parse_json_display_value(text: str) -> Any | None:
    stripped = str(text or "").strip()
    if not stripped or stripped[0] not in "{[":
        return None
    decoder = json.JSONDecoder()
    values: list[Any] = []
    index = 0
    try:
        while index < len(stripped):
            while index < len(stripped) and stripped[index].isspace():
                index += 1
            if index >= len(stripped):
                break
            value, index = decoder.raw_decode(stripped, index)
            values.append(value)
    except Exception:
        return None
    if not values:
        return None
    if len(values) == 1 and isinstance(values[0], (dict, list)):
        return values[0]
    if all(isinstance(value, (dict, list)) for value in values):
        return values
    return None


def _bounded_text(value: Any, *, limit: int) -> str:
    text = "\n".join(line.rstrip() for line in str(value or "").splitlines()).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _raw_value_markdown(value: Any, *, limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        if not value.strip():
            return ""
        parsed = _parse_json_display_value(value)
        if parsed is not None:
            body = _bounded_text(_pretty_json(parsed), limit=limit)
            return _markdown_code_block(body, language="json")
        text = _bounded_text(value, limit=limit)
        return _markdown_code_block(text) if text else ""
    if isinstance(value, list) and not value:
        return ""
    if isinstance(value, (dict, list, tuple)):
        body = _bounded_text(_pretty_json(value), limit=limit)
        return _markdown_code_block(body, language="json")
    return _markdown_code_block(_bounded_text(value, limit=limit))


def _raw_markdown_section(title: str, raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return f"**{title}**\n{_markdown_code_block(text)}"


def _safe_failure_response(stage: str, message: str) -> str:
    return user_error_message(user_error_detail(message, stage=stage, category="runtime_error"))


def _validation_errors_for_user(errors: list[dict[str, Any]], *, limit: int = 5) -> str:
    """Render structured validation errors into a concise user-facing detail block."""

    if not errors:
        return ""
    lines = ["Validation details:"]
    for error in errors[:limit]:
        code = str(error.get("error") or "validation_error")
        action_id = str(error.get("action_id") or "").strip()
        message = str(error.get("message") or error)
        label = f"{action_id} [{code}]" if action_id else f"[{code}]"
        details: list[str] = []
        for key in ("input_name", "source_action_id", "dependency", "module"):
            value = error.get(key)
            if value not in (None, "", []):
                details.append(f"{key}={value}")
        placeholders = error.get("placeholders")
        if placeholders:
            details.append(f"placeholders={', '.join(map(str, placeholders))}")
        suffix = f" ({'; '.join(details)})" if details else ""
        if _looks_like_raw_system_message(message):
            lines.append(f"- {label}{suffix}")
            lines.append(_raw_markdown_section("Raw Validation Detail", message))
        else:
            lines.append(f"- {label}: {message}{suffix}")
    remaining = len(errors) - limit
    if remaining > 0:
        lines.append(f"- ...and {remaining} more validation error(s).")
    return "\n".join(lines)


_STANDARD_OPERATOR_ORDINARY_DOMAINS = frozenset(
    {
        "operator",
        "filesystem",
        "shell",
        "system",
        "git",
        "docker",
        "data",
        "python_data",
        "markdown",
        "network",
    }
)
_STANDARD_OPERATOR_STRUCTURED_DOMAINS = frozenset({"sql", "database"})

_CREDENTIAL_INPUT_KIND_PATTERNS: tuple[tuple[str, str], ...] = (
    ("passphrase", r"\bpass\s*phrase\b|\bpassphrase\b"),
    ("private_key", r"\bprivate\s+key\b|\bssh\s+key\b|\bkey\s+file\b"),
    ("token", r"\bapi\s*key\b|\bapi[_ -]?token\b|\baccess\s+token\b|\btoken\b"),
    ("password", r"\bpassword\b|\bpasswd\b|\bpasscode\b|\bpin\b"),
    ("credential", r"\bcredential\b|\bcredentials\b|\bauth\b|\blogin\b|\bsecret\b"),
)
_EXACT_PARAMETER_CREDENTIAL_CONTEXT_RE = re.compile(
    r"\b("
    r"git\s+push|push|ssh|sudo|login|log\s+in|authenticate|authentication|"
    r"auth|credential|secret|password|passphrase|token|api\s*key"
    r")\b",
    re.IGNORECASE,
)
_EXACT_PARAMETER_CREDENTIAL_DELIVERY_RE = re.compile(
    r"\b(?:use|using|with|enter|input|type|provide|pass|authenticate|"
    r"authentication|login|log\s+in|connect|push)\b",
    re.IGNORECASE,
)
_EXPLANATORY_CREDENTIAL_QUERY_RE = re.compile(
    r"^\s*(?:what\s+(?:is|are|does|happens?|would|will)|what\s+happens\s+when|"
    r"explain|describe|summari[sz]e|tell\s+me\s+about)\b",
    re.IGNORECASE,
)


def _credential_input_kind_from_text(value: Any) -> str:
    text = " ".join(str(value or "").lower().split())
    if not text:
        return "unknown"
    for kind, pattern in _CREDENTIAL_INPUT_KIND_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return kind
    return "unknown"


_PLAIN_CLARIFICATION_PRIMARY_RE = re.compile(
    r"\b("
    r"branch\s+name|commit\s+message|commit\s+title|device\s+label|disk\s+label|"
    r"drive\s+label|file\s+name|filename|file\s+path|mount\s*point|mountpoint|"
    r"path|directory|python\s+version|usb\s+label|version|volume\s+label"
    r")\b",
    re.IGNORECASE,
)


def _clarification_primary_text_is_plain_answer(request: Any) -> bool:
    """Return whether the actual question asks for a non-secret value."""

    primary_text = " ".join(
        str(getattr(request, key, "") or "")
        for key in ("question", "missing_information")
    )
    if not primary_text.strip():
        return False
    if _credential_input_kind_from_text(primary_text) != "unknown":
        return False
    return bool(_PLAIN_CLARIFICATION_PRIMARY_RE.search(primary_text))


def _credential_field_hint(input_kind: str) -> str:
    kind = str(input_kind or "").strip().lower()
    if kind in {"password", "passphrase", "token"}:
        return kind
    return ""


def _prompt_suggests_exact_parameter_credential_use(prompt: str, record: Any) -> bool:
    if not bool(getattr(record, "sensitive", False)):
        return False
    text = " ".join(str(prompt or "").lower().split())
    if _EXPLANATORY_CREDENTIAL_QUERY_RE.search(text):
        return False
    return bool(
        _EXACT_PARAMETER_CREDENTIAL_CONTEXT_RE.search(text)
        and _EXACT_PARAMETER_CREDENTIAL_DELIVERY_RE.search(text)
    )

STAGE_MEMORY_CHECK = "memory_check"
STAGE_PARAMETER_CHECK = "parameter_check"


def _agent_display_name_from_context(context: dict[str, Any] | None) -> str:
    """Return the user-selected local agent display name."""

    text = " ".join(str(dict(context or {}).get("agent_display_name") or "Agent").split()).strip()
    return text[:40] or "Agent"

__all__ = [
    '_RAW_SYSTEM_MESSAGE_MARKERS',
    '_looks_like_raw_system_message',
    '_markdown_code_block',
    '_pretty_json',
    '_parse_json_display_value',
    '_bounded_text',
    '_raw_value_markdown',
    '_raw_markdown_section',
    '_safe_failure_response',
    '_validation_errors_for_user',
    '_STANDARD_OPERATOR_ORDINARY_DOMAINS',
    '_STANDARD_OPERATOR_STRUCTURED_DOMAINS',
    '_CREDENTIAL_INPUT_KIND_PATTERNS',
    '_credential_input_kind_from_text',
    '_PLAIN_CLARIFICATION_PRIMARY_RE',
    '_clarification_primary_text_is_plain_answer',
    '_credential_field_hint',
    '_prompt_suggests_exact_parameter_credential_use',
    'STAGE_MEMORY_CHECK',
    'STAGE_PARAMETER_CHECK',
    '_agent_display_name_from_context',
]
