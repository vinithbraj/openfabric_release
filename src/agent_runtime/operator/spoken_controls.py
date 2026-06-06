"""Speech-friendly prompt control normalization."""

from __future__ import annotations

import json
import re


_SPOKEN_QUOTED_SEGMENT_RE = re.compile(
    r"(?<!\w)(?P<starter>"
    r"commit\s+message|commit\s+description|description|message|content|"
    r"slash\s+type\s*in|slash\s+typein|/\s*type\s*in|/\s*typein|type\s*in|typein|"
    r"slash\s+tpe\s*in|slash\s+tpein|/\s*tpe\s*in|/\s*tpein|tpe\s*in|tpein|"
    r"slash\s+check\s+online\s+a\.?\s*i\.?|slash\s+checkonlineai|"
    r"/\s*check\s+online\s+a\.?\s*i\.?|/\s*checkonlineai|"
    r"check\s+online\s+a\.?\s*i\.?|checkonlineai"
    r")\s+(?:begin|start)\s+(?P<value>[\s\S]+?)\s+(?:end|stop)(?!\w)",
    re.IGNORECASE,
)


def _syntax_mask(text: str) -> list[bool]:
    """Return a mask for quoted strings and markdown code spans/fences."""

    value = str(text or "")
    mask = [False] * len(value)
    in_single_quote = False
    in_double_quote = False
    in_inline_code = False
    in_fenced_code = False
    escaped = False
    index = 0
    while index < len(value):
        if value.startswith("```", index) and not in_single_quote and not in_double_quote:
            for offset in range(3):
                if index + offset < len(mask):
                    mask[index + offset] = True
            in_fenced_code = not in_fenced_code
            index += 3
            escaped = False
            continue
        char = value[index]
        if in_fenced_code:
            mask[index] = True
            index += 1
            continue
        if char == "`" and not in_single_quote and not in_double_quote:
            mask[index] = True
            in_inline_code = not in_inline_code
            index += 1
            escaped = False
            continue
        if in_inline_code:
            mask[index] = True
            index += 1
            continue
        if in_single_quote:
            mask[index] = True
            if char == "'" and not escaped:
                in_single_quote = False
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            index += 1
            continue
        if in_double_quote:
            mask[index] = True
            if char == '"' and not escaped:
                in_double_quote = False
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            index += 1
            continue
        if char == "'":
            mask[index] = True
            in_single_quote = True
            escaped = False
        elif char == '"':
            mask[index] = True
            in_double_quote = True
            escaped = False
        index += 1
    return mask


def _canonical_quoted_starter(value: str) -> str:
    normalized = re.sub(r"[\s_/-]+", " ", str(value or "").lower()).strip()
    if normalized.startswith("slash "):
        normalized = normalized.removeprefix("slash ").strip()
    normalized = normalized.replace(".", "")
    if normalized in {"typein", "type in", "tpein", "tpe in"}:
        return "typein"
    if normalized in {"checkonlineai", "check online ai", "check online a i"}:
        return "checkonlineai"
    return " ".join(str(value or "").lower().split())


def normalize_spoken_quoted_segments(prompt: str) -> str:
    """Convert voice-friendly begin/end payload spans into quoted prompt syntax."""

    text = str(prompt or "")
    if not text:
        return text
    mask = _syntax_mask(text)
    pieces: list[str] = []
    cursor = 0
    changed = False
    for match in _SPOKEN_QUOTED_SEGMENT_RE.finditer(text):
        if mask[match.start()]:
            continue
        starter = _canonical_quoted_starter(match.group("starter"))
        value = " ".join(str(match.group("value") or "").split()).strip()
        if not value:
            continue
        pieces.append(text[cursor : match.start()])
        pieces.append(f"{starter} {json.dumps(value)}")
        cursor = match.end()
        changed = True
    if not changed:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)
