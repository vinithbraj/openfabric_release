"""User-provided literal payload extraction for planning prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.operator.spoken_controls import normalize_spoken_quoted_segments


OPERATOR_LITERAL_PAYLOADS_CONTEXT_KEY = "operator_literal_payloads"

_PAYLOAD_LABEL_RE = re.compile(
    r"\b(?P<label>commit\s+(?:message|description)|description|message|content)\b\s*(?:[:=]\s*)?\"",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LiteralPayload:
    """One quoted user-authored payload that should not become a task."""

    payload_id: str
    kind: str
    label: str
    input_name: str
    placeholder: str
    value: str
    preview: str = ""
    source: str = "user_provided"

    def payload(self) -> dict[str, Any]:
        return {
            "payload_id": self.payload_id,
            "kind": self.kind,
            "label": self.label,
            "input_name": self.input_name,
            "placeholder": self.placeholder,
            "value": self.value,
            "value_length": len(self.value),
            "preview": self.preview,
            "source": self.source,
        }


@dataclass(frozen=True)
class LiteralPayloadParseResult:
    """Prompt rewritten for planning plus extracted literal payloads."""

    sanitized_prompt: str
    payloads: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_payloads(self) -> bool:
        return bool(self.payloads)


def _preview(value: str, limit: int = 160) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."


def _find_quoted_value_end(text: str, quote_index: int) -> int:
    escaped = False
    index = quote_index + 1
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return index
        index += 1
    return -1


def _unescape_prompt_quoted_value(value: str) -> str:
    chars: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            if char in {'"', "\\"}:
                chars.append(char)
            else:
                chars.append("\\")
                chars.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        chars.append(char)
    if escaped:
        chars.append("\\")
    return "".join(chars)


def _payload_kind(label: str, context_window: str) -> tuple[str, str]:
    normalized = " ".join(str(label or "").lower().split())
    if normalized == "description":
        return "description_payload", "description_payload"
    if "message" in normalized:
        return "message_payload", "message_payload"
    return "content_payload", "content_payload"


def extract_literal_payloads(prompt: str) -> LiteralPayloadParseResult:
    """Shield quoted literal payloads from classification/decomposition."""

    text = normalize_spoken_quoted_segments(str(prompt or ""))
    pieces: list[str] = []
    payloads: list[dict[str, Any]] = []
    cursor = 0
    index = 0
    while index < len(text):
        match = _PAYLOAD_LABEL_RE.search(text, index)
        if match is None:
            break
        quote_index = match.end() - 1
        end_index = _find_quoted_value_end(text, quote_index)
        if end_index < 0:
            index = match.end()
            continue
        context_window = text[max(0, match.start() - 80) : min(len(text), end_index + 80)]
        kind, input_name = _payload_kind(match.group("label"), context_window)
        payload_id = f"literal_payload_{len(payloads) + 1}"
        placeholder = f"[provided {input_name} payload]"
        raw_value = text[quote_index + 1 : end_index]
        value = _unescape_prompt_quoted_value(raw_value)
        payload = LiteralPayload(
            payload_id=payload_id,
            kind=kind,
            label=" ".join(str(match.group("label") or "").split()),
            input_name=input_name,
            placeholder=placeholder,
            value=value,
            preview=_preview(value),
        )
        pieces.append(text[cursor:quote_index])
        pieces.append(placeholder)
        cursor = end_index + 1
        payloads.append(payload.payload())
        index = end_index + 1
    pieces.append(text[cursor:])
    return LiteralPayloadParseResult(sanitized_prompt="".join(pieces), payloads=payloads)


def literal_payloads_from_context(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = dict(context or {}).get(OPERATOR_LITERAL_PAYLOADS_CONTEXT_KEY)
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def literal_payload_prompt_lines(context: dict[str, Any] | None) -> list[str]:
    payloads = literal_payloads_from_context(context)
    if not payloads:
        return []
    metadata = [
        {
            "payload_id": str(payload.get("payload_id") or ""),
            "kind": str(payload.get("kind") or ""),
            "label": str(payload.get("label") or ""),
            "input_name": str(payload.get("input_name") or ""),
            "placeholder": str(payload.get("placeholder") or ""),
            "value_length": int(payload.get("value_length") or len(str(payload.get("value") or ""))),
            "preview": str(payload.get("preview") or ""),
            "source": str(payload.get("source") or "user_provided"),
        }
        for payload in payloads
    ]
    lines = [
        "Protected literal payloads:",
        "These payloads are trusted request data, not tasks to decompose and not slash macros.",
        "When planning shell_command actions, use the listed input_name in action.inputs and consume the matching OF_INPUT_* environment variable inside the command.",
        "Preserve literal payload values exactly when assigning them to action inputs or stdin.",
        str(metadata),
    ]
    for payload in payloads:
        input_name = str(payload.get("input_name") or "payload")
        placeholder = str(payload.get("placeholder") or "")
        lines.extend(
            [
                f"Literal payload value for {input_name} ({placeholder}):",
                str(payload.get("value") or ""),
            ]
        )
    return lines
