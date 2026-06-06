"""Request-scoped sudo authorization helpers for the operator runtime."""

from __future__ import annotations

import re
import shlex
from typing import Any

from agent_runtime.core.types import UserRequest
from agent_runtime.operator.command_exceptions import (
    normalize_operator_command,
    operator_command_hash,
)


REQUEST_SCOPED_SUDO_OVERRIDES_CONTEXT_KEY = "operator_request_scoped_sudo_overrides"

_SUDO_NEGATION_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|never|avoid|skip|without|no)\s+(?:use\s+)?sudo\b"
    r"|\bsudo\b(?:\W+\w+){0,3}\W+\b(?:not|never|disallow(?:ed)?|forbidden|blocked)\b",
    re.IGNORECASE,
)
_SUDO_POSITIVE_RE = re.compile(
    r"\b(?:use|using|via|run|execute|try|retry|allow|allowed|authorize|"
    r"authorized|approve|approved|grant|enable|ok|okay|please)\b"
    r"(?:\W+\w+){0,5}\W+\bsudo\b"
    r"|\busing\W+sudo\b"
    r"|\bsudo\b(?:\W+\w+){0,5}\W+\b(?:ok|okay|allowed|authorized|approved|"
    r"please)\b"
    r"|\bsudo\b(?:\W+\w+){0,3}\W+\b(?:privileges?|permissions?|access|"
    r"rights|elevation|elevated|root)\b"
    r"|\b(?:use|using)\b(?:\W+\w+){0,3}\W+\b[A-Za-z0-9_.:-]{1,160}\b"
    r"(?:\W+\w+){0,3}\W+\b(?:macro|parameter|param|key)\b(?:\W+\w+){0,5}"
    r"\W+\b(?:elevated|elevation|permissions?|privileges?|sudo|root)\b",
    re.IGNORECASE,
)
_SUDO_DIRECTIVE_RE = re.compile(
    r"\b(?:use|using|via|run|execute|try|retry|allow|allowed|authorize|"
    r"authorized|approve|approved|grant|enable|ok|okay|please)\b",
    re.IGNORECASE,
)
_SUDO_EXPLANATORY_QUERY_RE = re.compile(
    r"^\s*(?:what\s+(?:is|are|does|happens?|would|will)|what\s+happens\s+when|"
    r"explain|describe|summari[sz]e|tell\s+me\s+about)\b",
    re.IGNORECASE,
)
_ELEVATION_MARKERS = (
    "sudo",
    "elevated",
    "privilege",
    "privileges",
    "permission",
    "permissions",
    "root",
    "administrator",
    "admin",
)
_POSITIVE_CLARIFICATION_RE = re.compile(
    r"\b(?:yes|y|use|retry|allow|allowed|grant|approve|approved|ok|okay|go\s+ahead|"
    r"authorized|please)\b",
    re.IGNORECASE,
)
_NEGATIVE_CLARIFICATION_RE = re.compile(
    r"\b(?:no|n|without|cancel|do\s+not|don't|dont|never|avoid|skip)\b",
    re.IGNORECASE,
)
_CREDENTIAL_CLARIFICATION_RE = re.compile(
    r"\b(?:password|passphrase|pass\s+phrase|credential|secret|parameter|param|key|token|pin)\b",
    re.IGNORECASE,
)
_SUDO_TOKEN_RE = re.compile(r"(^|[\s;&|()])sudo(?:\s|$)", re.IGNORECASE)
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _has_negated_sudo(value: Any) -> bool:
    return bool(_SUDO_NEGATION_RE.search(_compact_text(value)))


def _has_positive_sudo_intent(value: Any) -> bool:
    text = _compact_text(value)
    if _SUDO_EXPLANATORY_QUERY_RE.search(text) and not _SUDO_DIRECTIVE_RE.search(text):
        return False
    return bool(text and not _has_negated_sudo(text) and _SUDO_POSITIVE_RE.search(text))


def _clarification_entry_has_credential(entry: dict[str, Any]) -> bool:
    return bool(
        entry.get("answer_is_secret")
        or _compact_text(entry.get("parameter_key"))
        or _compact_text(entry.get("parameter_choice_id")).startswith("param:")
        or _compact_text(entry.get("source")).lower() == "parameter_store"
    )


def _clarification_entry_is_credential_sudo_prompt(
    entry: dict[str, Any],
    full_text: str,
) -> bool:
    return bool(
        "sudo" in full_text.lower()
        and _CREDENTIAL_CLARIFICATION_RE.search(full_text)
        and _compact_text(entry.get("selected_option_id")).lower() in {
            "retry_with_sudo",
            "retry with sudo in terminal",
        }
    )


def _clarification_sudo_authorization(user_request: UserRequest) -> dict[str, str] | None:
    raw = dict(user_request.session_context or {}).get("clarifications")
    if not isinstance(raw, list) or not raw:
        return None
    for entry in reversed(raw[-5:]):
        if not isinstance(entry, dict):
            continue
        answer_text = _compact_text(
            " ".join(
                str(entry.get(key) or "")
                for key in ("answer", "selected_option_id")
            )
        )
        full_text = _compact_text(
            " ".join(
                str(entry.get(key) or "")
                for key in (
                    "question",
                    "answer",
                    "missing_information",
                    "reason",
                    "selected_option_id",
                )
            )
        )
        lowered_full = full_text.lower()
        if "sudo" not in lowered_full and not any(
            marker in lowered_full for marker in _ELEVATION_MARKERS
        ):
            continue
        if _has_negated_sudo(answer_text) or (
            _NEGATIVE_CLARIFICATION_RE.search(answer_text)
            and any(marker in lowered_full for marker in _ELEVATION_MARKERS)
        ):
            return None
        if (
            _clarification_entry_is_credential_sudo_prompt(entry, full_text)
            and not _clarification_entry_has_credential(entry)
        ):
            continue
        if _has_positive_sudo_intent(answer_text):
            return {"source": "clarification", "reason": "clarification_explicit_sudo"}
        lowered_answer = answer_text.lower()
        if "sudo" in lowered_answer and not _NEGATIVE_CLARIFICATION_RE.search(answer_text):
            return {"source": "clarification", "reason": "clarification_mentions_sudo"}
        if (
            bool(entry.get("answer_is_secret"))
            and any(marker in lowered_full for marker in _ELEVATION_MARKERS)
            and not _NEGATIVE_CLARIFICATION_RE.search(answer_text)
        ):
            return {"source": "clarification", "reason": "clarification_secret_sudo_credential"}
        if (
            any(marker in lowered_full for marker in _ELEVATION_MARKERS)
            and _POSITIVE_CLARIFICATION_RE.search(answer_text)
        ):
            return {"source": "clarification", "reason": "clarification_approved_elevation"}
    return None


def _clarification_sudo_denial(user_request: UserRequest) -> dict[str, str] | None:
    raw = dict(user_request.session_context or {}).get("clarifications")
    if not isinstance(raw, list) or not raw:
        return None
    for entry in reversed(raw[-5:]):
        if not isinstance(entry, dict):
            continue
        answer_text = _compact_text(
            " ".join(
                str(entry.get(key) or "")
                for key in ("answer", "selected_option_id")
            )
        )
        full_text = _compact_text(
            " ".join(
                str(entry.get(key) or "")
                for key in (
                    "question",
                    "answer",
                    "missing_information",
                    "reason",
                    "selected_option_id",
                )
            )
        )
        lowered_full = full_text.lower()
        if "sudo" not in lowered_full and not any(
            marker in lowered_full for marker in _ELEVATION_MARKERS
        ):
            continue
        if _has_negated_sudo(answer_text) or _NEGATIVE_CLARIFICATION_RE.search(answer_text):
            return {"source": "clarification", "reason": "clarification_denied_sudo"}
    return None


def _sudo_prompt_candidates(user_request: UserRequest) -> list[tuple[str, str]]:
    """Return current and preserved prompt text that may carry sudo authorization."""

    session_context = dict(user_request.session_context or {})
    candidates: list[tuple[str, str]] = [("raw_prompt", _compact_text(user_request.raw_prompt))]
    for key in (
        "original_user_prompt",
        "operator_auto_rephrase_original_prompt",
        "operator_original_prompt",
        "original_prompt",
        "streaming_original_prompt",
    ):
        text = _compact_text(session_context.get(key))
        if text and all(text != existing for _source, existing in candidates):
            candidates.append((key, text))
    return [(source, text) for source, text in candidates if text]


def request_sudo_authorization(user_request: UserRequest) -> dict[str, str] | None:
    """Return request-scoped sudo authorization metadata when explicit and positive."""

    clarification = _clarification_sudo_authorization(user_request)
    if clarification is not None:
        return clarification
    candidates = _sudo_prompt_candidates(user_request)
    if any(_has_negated_sudo(text) for _source, text in candidates):
        return None
    for source, text in candidates:
        if _has_positive_sudo_intent(text):
            return {"source": source, "reason": f"{source}_explicit_sudo"}
    return None


def request_authorizes_sudo(user_request: UserRequest) -> bool:
    """Return whether this request explicitly authorizes sudo for this request only."""

    return request_sudo_authorization(user_request) is not None


def request_denies_sudo(user_request: UserRequest) -> bool:
    """Return whether this request explicitly rejects sudo for this request."""

    if _clarification_sudo_denial(user_request) is not None:
        return True
    return any(_has_negated_sudo(text) for _source, text in _sudo_prompt_candidates(user_request))


def shell_command_uses_sudo(command: str | None) -> bool:
    """Return whether a shell command contains a sudo invocation token."""

    return bool(_SUDO_TOKEN_RE.search(str(command or "")))


def _segment_tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return segment.split()


def _strip_env_and_wrappers(tokens: list[str]) -> list[str]:
    remaining = list(tokens)
    while remaining and _ENV_ASSIGNMENT_RE.match(remaining[0]):
        remaining.pop(0)
    if remaining and remaining[0] == "env":
        remaining.pop(0)
        while remaining and (remaining[0].startswith("-") or _ENV_ASSIGNMENT_RE.match(remaining[0])):
            remaining.pop(0)
    return remaining


def _split_shell_on_top_level_separators(command: str) -> list[tuple[str, bool]]:
    pieces: list[tuple[str, bool]] = []
    start = 0
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = quote != "'"
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        separator = ""
        if command.startswith("&&", index) or command.startswith("||", index):
            separator = command[index : index + 2]
        elif char in {";", "|"}:
            separator = char
        if separator:
            if start < index:
                pieces.append((command[start:index], False))
            pieces.append((separator, True))
            index += len(separator)
            start = index
            continue
        index += 1
    if start < len(command):
        pieces.append((command[start:], False))
    return pieces


def sudo_command_for(command: str | None) -> str:
    """Return a conservative sudo-prefixed version of a shell command."""

    raw = str(command or "").strip()
    if not raw:
        return ""
    pieces = _split_shell_on_top_level_separators(raw)
    rewritten: list[str] = []
    for piece, is_separator in pieces:
        if is_separator:
            rewritten.append(piece)
            continue
        segment = piece.strip()
        if not segment:
            rewritten.append(piece)
            continue
        tokens = _strip_env_and_wrappers(_segment_tokens(segment))
        if tokens and tokens[0] == "sudo":
            rewritten.append(piece)
        else:
            leading = piece[: len(piece) - len(piece.lstrip())]
            trailing = piece[len(piece.rstrip()):]
            rewritten.append(f"{leading}sudo {segment}{trailing}")
    return "".join(rewritten).strip()


def request_scoped_sudo_override_for_error(
    user_request: UserRequest,
    action: Any,
    error: dict[str, Any],
) -> dict[str, Any] | None:
    """Return metadata for a safe request-scoped sudo validation override."""

    if str(error.get("error") or "") != "forbidden_command":
        return None
    if not bool(error.get("overrideable")):
        return None
    if str(getattr(action, "kind", "") or "") != "shell_command":
        return None
    command = str(getattr(action, "command", "") or "")
    if not shell_command_uses_sudo(command):
        return None
    authorization = request_sudo_authorization(user_request)
    if authorization is None:
        return None
    command_hash = operator_command_hash(command)
    if str(error.get("command_hash") or "").strip().lower() != command_hash:
        return None
    return {
        "action_id": str(getattr(action, "action_id", "") or ""),
        "command_hash": command_hash,
        "normalized_command": normalize_operator_command(command),
        "block_reason": str(error.get("block_reason") or ""),
        "authorization_source": authorization["source"],
        "authorization_reason": authorization["reason"],
    }


def record_request_scoped_sudo_override(
    user_request: UserRequest,
    override: dict[str, Any],
) -> None:
    """Persist non-secret request-scoped sudo override metadata in request context."""

    if not override:
        return
    key = REQUEST_SCOPED_SUDO_OVERRIDES_CONTEXT_KEY
    for context in (user_request.session_context, user_request.safety_context):
        existing = context.get(key)
        entries = [dict(item) for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
        identity = (
            str(override.get("action_id") or ""),
            str(override.get("command_hash") or ""),
        )
        entries = [
            item
            for item in entries
            if (
                str(item.get("action_id") or ""),
                str(item.get("command_hash") or ""),
            )
            != identity
        ]
        context[key] = [*entries, dict(override)]


__all__ = [
    "REQUEST_SCOPED_SUDO_OVERRIDES_CONTEXT_KEY",
    "record_request_scoped_sudo_override",
    "request_authorizes_sudo",
    "request_denies_sudo",
    "request_scoped_sudo_override_for_error",
    "request_sudo_authorization",
    "shell_command_uses_sudo",
    "sudo_command_for",
]
