"""LLM-first parameter-store draft extraction with deterministic fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from agent_runtime.parameters.models import (
    AgentParameterDraft,
    AgentParameterDraftRequest,
    AgentParameterDraftResponse,
)
from agent_runtime.parameters.store import infer_sensitive, normalize_parameter_key
from agent_runtime.prompts import prompt_lines


_PARAMETER_REFERENCE_RE = re.compile(
    r"\b(?:parameter\s+store|param\s+store|parameters\s+store)\b"
    r"|\b(?:from|in)\s+(?:the\s+)?(?:parameter\s+)?store\b"
    r"|\b(?:stored|saved)\s+(?:in|under)\b",
    re.IGNORECASE,
)
_STORE_READ_INTENT_RE = re.compile(
    r"\b(?:use|using|retrieve|get|read|fetch|load|lookup|look\s+up|pull)\b[\s\S]{0,180}"
    r"(?:\b(?:from|in)\s+(?:the\s+)?(?:parameter\s+)?store\b|\b(?:stored|saved)\s+(?:in|under)\b)",
    re.IGNORECASE,
)
_STORE_WRITE_INTENT_RE = re.compile(
    r"\b(?:(?<!parameter )(?<!param )store|save|keep|stash)\b[\s\S]{0,180}"
    r"\b(?:parameter|param|credential|password|passphrase|secret|token|connection(?:\s+string)?|database|db|datasource|config|key|info|information)\b"
    r"|\b(?:store|save)\s+(?:as|to|in|into|under)\s+(?:the\s+)?(?:parameter\s+)?store\b"
    r"|\b(?:add|create|set|update)\b[\s\S]{0,120}\b(?:to|in|into|under)\s+(?:the\s+)?(?:parameter\s+)?store\b"
    r"|\badd\b[\s\S]{0,120}"
    r"\b(?:credential|password|passphrase|secret|token|connection(?:\s+string)?|database|db|datasource|config\s+(?:value|json|entry)|parameter\s+(?:called|named|key|entry)|param\s+(?:called|named|key|entry))\b"
    r"|\b(?:create|set|update)\b[\s\S]{0,120}\b(?:parameter|param|credential|password|secret|token)\b[\s\S]{0,80}"
    r"\b(?:store|value|json|key)\b",
    re.IGNORECASE,
)
_CONNECTION_RE = re.compile(
    r"(?P<host>\b(?:\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9][a-z0-9_.-]*[a-z0-9]))\s*:\s*"
    r"(?P<port>\d{2,5})(?:/(?P<database>[A-Za-z0-9_.:-]+))?",
    re.IGNORECASE,
)
_URI_RE = re.compile(
    r"\b(?P<uri>(?:postgres(?:ql)?|mysql|mariadb|mongodb|redis|amqp|http|https|ssh|sftp)://[^\s`'\"]+)",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(r"\b(password|passphrase|token|secret|api[_ -]?key|private[_ -]?key)\b", re.IGNORECASE)
_KEY_VALUE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?[`'\"]?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_. -]{0,79})"
    r"[`'\"]?\s*[:=]\s*(?P<value>.+?)\s*,?\s*$"
)
_INLINE_KEY_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<key>[A-Za-z_][A-Za-z0-9_.-]{0,79})\s*[:=]\s*"
)
_URI_SCHEMES = {
    "amqp",
    "http",
    "https",
    "mariadb",
    "mongodb",
    "mysql",
    "postgres",
    "postgresql",
    "redis",
    "sftp",
    "ssh",
}
_INVALID_PARAMETER_KEYS = {
    "a",
    "an",
    "as",
    "config",
    "entry",
    "information",
    "into",
    "json",
    "parameter",
    "parameter_store",
    "parameters",
    "parameters_store",
    "param",
    "param_store",
    "store",
    "the",
    "this",
    "to",
    "value",
}
_GENERIC_PARAMETER_KEYS = _INVALID_PARAMETER_KEYS | {
    "connection",
    "connection_details",
    "database",
    "database_config",
    "db",
    "db_config",
}
_IMPOSSIBLE_HOST_VALUES = {
    "batch_size",
    "database",
    "dbname",
    "db_name",
    "host",
    "parameter",
    "password",
    "port",
    "save_dir",
    "store",
    "user",
    "username",
}


def looks_like_parameter_store_request(prompt: str) -> bool:
    """Return whether the prompt appears to ask for a parameter-store set draft."""

    text = str(prompt or "").strip()
    if not text:
        return False
    if text.lower().startswith(("/parameter", "/param", "/store ")):
        return True
    if _STORE_WRITE_INTENT_RE.search(text):
        return True
    if _STORE_READ_INTENT_RE.search(text):
        return False
    return False


def _mentions_parameter_store(prompt: str) -> bool:
    text = str(prompt or "").strip()
    if not text:
        return False
    if text.lower().startswith(("/parameter", "/param", "/store ")):
        return True
    return bool(_STORE_WRITE_INTENT_RE.search(text) or _PARAMETER_REFERENCE_RE.search(text))


def _compact(text: Any, *, limit: int = 2000) -> str:
    return " ".join(str(text or "").strip().split())[:limit]


def _dedupe(values: list[Any], *, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _compact(value, limit=160)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _valid_key_candidate(value: str, *, allow_generic: bool = False) -> bool:
    normalized = normalize_parameter_key(value)
    if not normalized:
        return False
    if normalized in (_INVALID_PARAMETER_KEYS if allow_generic else _GENERIC_PARAMETER_KEYS):
        return False
    if len(normalized) < 2:
        return False
    return True


def _is_generic_key(value: str) -> bool:
    normalized = normalize_parameter_key(value)
    return not normalized or normalized in _GENERIC_PARAMETER_KEYS


def _key_from_prompt(prompt: str, *, fallback: str = "") -> str:
    patterns = [
        r"\b(?:key|parameter|param)\s+[`'\"]?([A-Za-z0-9_.:\- ]{1,80})[`'\"]?",
        r"\b(?:as|called|named)\s+[`'\"]?([A-Za-z0-9_.:\- ]{1,80})[`'\"]?",
        r"\b(?:for|about)\s+(?:the\s+)?([A-Za-z0-9_.:\- ]{1,80}?)(?:\s+(?:database|db|connection|credential|password|token|secret|info|information))\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if not match:
            continue
        raw = str(match.group(1) or "").strip(" `'\").,;:")
        raw = re.split(
            r"\b(?:from|with|and|then|please|value|is|password|passphrase|token|secret)\b",
            raw,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        normalized = normalize_parameter_key(raw)
        if _valid_key_candidate(normalized, allow_generic=False):
            return normalized
    lowered = prompt.lower()
    if "dicom" in lowered and ("db" in lowered or "database" in lowered):
        return "dicom_db"
    if "git" in lowered and "ssh" in lowered and "password" in lowered:
        return "git_ssh_password"
    if "database" in lowered or re.search(r"\bdb\b", lowered):
        return "database"
    if fallback and _valid_key_candidate(fallback, allow_generic=True):
        return normalize_parameter_key(fallback) or fallback
    return ""


def _strip_wrapping_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _parse_scalar_value(value: str) -> Any:
    text = str(value or "").strip().rstrip(",")
    if not text:
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        if text[0] == '"':
            try:
                return json.loads(text)
            except Exception:
                pass
        return text[1:-1]
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"[-+]?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)", text):
        try:
            return float(text)
        except ValueError:
            return text
    if (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        try:
            return json.loads(text)
        except Exception:
            return text
    return _strip_wrapping_quotes(text)


def _normalize_field_name(value: str) -> str:
    text = " ".join(str(value or "").strip().strip("`'\"").split())
    text = re.sub(r"\s+", "_", text)
    return text.strip("_.:")


def _looks_like_field_name(value: str) -> bool:
    raw = str(value or "").strip().strip("`'\"")
    if " " in raw:
        parts = raw.split()
        if len(parts) > 3 or any(re.search(r"\d", part) for part in parts):
            return False
    normalized = _normalize_field_name(raw)
    if not normalized:
        return False
    if normalize_parameter_key(normalized) in _INVALID_PARAMETER_KEYS:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,79}", normalized))


def _inline_key_value_pairs(text: str) -> dict[str, Any]:
    matches = []
    for match in _INLINE_KEY_VALUE_RE.finditer(text):
        key = str(match.group("key") or "")
        if key.lower() in _URI_SCHEMES and text[match.end() : match.end() + 2] == "//":
            continue
        if not _looks_like_field_name(key):
            continue
        matches.append(match)
    if len(matches) < 2:
        return {}
    pairs: dict[str, Any] = {}
    for index, match in enumerate(matches):
        key = _normalize_field_name(match.group("key"))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw_value = text[start:end].strip(" \t\r\n,;")
        if "\n" in raw_value:
            raw_value = raw_value.splitlines()[0].strip(" \t,;")
        if raw_value:
            pairs[key] = _parse_scalar_value(raw_value)
    return pairs


def _extract_key_value_block(prompt: str) -> dict[str, Any] | None:
    pairs: dict[str, Any] = {}
    for raw_line in str(prompt or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        match = _KEY_VALUE_LINE_RE.match(line)
        if not match:
            continue
        raw_key = match.group("key")
        if not _looks_like_field_name(raw_key):
            continue
        key = _normalize_field_name(raw_key)
        pairs[key] = _parse_scalar_value(match.group("value"))
    if pairs:
        return pairs
    inline = _inline_key_value_pairs(str(prompt or ""))
    return inline or None


def _key_from_value(prompt: str, value: dict[str, Any], *, fallback: str = "") -> str:
    explicit = _key_from_prompt(prompt)
    if explicit and not _is_generic_key(explicit):
        return explicit
    for field in (
        "parameter_key",
        "param_key",
        "key",
        "dbname",
        "db_name",
        "database_name",
        "database",
        "db",
        "connection_name",
        "name",
        "host",
    ):
        candidate = value.get(field)
        if isinstance(candidate, (str, int)) and _valid_key_candidate(
            str(candidate),
            allow_generic=False,
        ):
            return normalize_parameter_key(str(candidate))
    if fallback and _valid_key_candidate(fallback, allow_generic=True):
        return normalize_parameter_key(fallback)
    return ""


def _extract_json_object(prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except Exception:
        return None
    return value if isinstance(value, dict) and value else None


def _secret_value(prompt: str) -> tuple[str, str]:
    key = _key_from_prompt(prompt)
    candidates = [
        r"\b(?:password|passphrase|token|secret|api[_ -]?key)\b\s*(?:is|=|:)\s*([^\n\r]+)",
        r"\b(?:password|passphrase|token|secret|api[_ -]?key)\b\s+(?!with\s+key\b)([^\n\r]+)",
        r"\b(?:value)\s*(?:is|=|:)\s*([^\n\r]+)",
    ]
    for pattern in candidates:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if not match:
            continue
        raw = str(match.group(1) or "").strip(" `'\").,;")
        raw = re.split(r"\b(?:with\s+key|under\s+key|as\s+key)\b", raw, maxsplit=1, flags=re.IGNORECASE)[0]
        if raw:
            return key, raw[:4000]
    return key, ""


def _parameter_tags_for_value(
    prompt: str,
    value: dict[str, Any],
    *,
    base: list[str] | None = None,
) -> list[str]:
    tags = list(base or [])
    lowered = str(prompt or "").lower()
    fields = {normalize_parameter_key(field) for field in value}
    if (
        fields & {"dbname", "db_name", "database", "database_name", "host", "port"}
        or "database" in lowered
        or re.search(r"\bdb\b", lowered)
    ):
        tags.append("database")
    if fields & {"password", "passphrase", "token", "secret", "api_key"}:
        tags.append("credential")
    if "dicom" in lowered:
        tags.append("dicom")
    return _dedupe(tags or ["config"])


def _valid_connection_match(match: re.Match[str]) -> bool:
    host = normalize_parameter_key(str(match.group("host") or ""))
    if not host or host in _IMPOSSIBLE_HOST_VALUES:
        return False
    return True


def _safe_connection_match(prompt: str) -> re.Match[str] | None:
    for match in _CONNECTION_RE.finditer(prompt):
        if _valid_connection_match(match):
            return match
    return None


def _value_has_impossible_connection(value: dict[str, Any]) -> bool:
    host = value.get("host")
    if isinstance(host, str) and normalize_parameter_key(host) in _IMPOSSIBLE_HOST_VALUES:
        return True
    connection_string = value.get("connection_string")
    if isinstance(connection_string, str):
        first = connection_string.split(":", 1)[0]
        if normalize_parameter_key(first) in _IMPOSSIBLE_HOST_VALUES:
            return True
    return False


def _heuristic_draft(request: AgentParameterDraftRequest) -> AgentParameterDraftResponse:
    prompt = str(request.prompt or "").strip()
    if not looks_like_parameter_store_request(prompt):
        return AgentParameterDraftResponse(
            is_parameter_request=False,
            rationale="No parameter-store intent detected.",
        )

    json_value = _extract_json_object(prompt)
    if json_value is not None:
        key = _key_from_prompt(prompt, fallback="parameter")
        sensitive = infer_sensitive(json_value, key=key, description=prompt)
        draft = AgentParameterDraft(
            operation="create",
            key=key,
            value=json_value,
            description=_compact(prompt, limit=280),
            aliases=[],
            tags=_dedupe(["config", "parameter"]),
            sensitive=sensitive,
            confidence=0.64,
            missing_details=[] if key else ["key"],
            rationale="Extracted a JSON object from the storage request.",
        )
        return AgentParameterDraftResponse(
            is_parameter_request=True,
            drafts=[draft],
            missing_details=draft.missing_details,
            rationale=draft.rationale,
        )

    key_value = _extract_key_value_block(prompt)
    if key_value is not None:
        key = _key_from_value(prompt, key_value)
        tags = _parameter_tags_for_value(prompt, key_value, base=["config", "parameter"])
        sensitive = infer_sensitive(key_value, key=key, description=prompt, tags=tags)
        draft = AgentParameterDraft(
            operation="create",
            key=key,
            value=key_value,
            description=_compact(prompt, limit=280),
            aliases=[],
            tags=tags,
            sensitive=sensitive,
            confidence=0.78 if key else 0.58,
            missing_details=[] if key else ["key"],
            rationale="Extracted key/value configuration fields from the storage request.",
        )
        return AgentParameterDraftResponse(
            is_parameter_request=True,
            drafts=[draft],
            missing_details=draft.missing_details,
            rationale=draft.rationale,
        )

    uri_match = _URI_RE.search(prompt)
    connection_match = _safe_connection_match(prompt)
    if connection_match is not None or uri_match is not None:
        key = _key_from_prompt(prompt)
        value: dict[str, Any] = {}
        tags = ["database" if "db" in prompt.lower() or "database" in prompt.lower() else "connection"]
        aliases: list[str] = []
        if "dicom" in prompt.lower():
            tags.append("dicom")
            aliases.append("DICOM DB")
            key = key or "dicom_db"
        if uri_match is not None:
            value["connection_string"] = uri_match.group("uri")
        if connection_match is not None:
            host = str(connection_match.group("host") or "").strip()
            port_text = str(connection_match.group("port") or "").strip()
            database = str(connection_match.group("database") or "").strip()
            value["host"] = host
            try:
                value["port"] = int(port_text)
            except ValueError:
                value["port"] = port_text
            if database:
                value["database"] = database
                value.setdefault("connection_string", f"{host}:{port_text}/{database}")
            else:
                value.setdefault("connection_string", f"{host}:{port_text}")
        key = key or "connection"
        sensitive = True if uri_match is not None else infer_sensitive(value, key=key, description=prompt, tags=tags)
        draft = AgentParameterDraft(
            operation="create",
            key=key,
            value=value,
            description=_compact(prompt, limit=280),
            aliases=_dedupe(aliases),
            tags=_dedupe(tags),
            sensitive=sensitive,
            confidence=0.72 if value else 0.25,
            missing_details=[] if key and value else ["key" if not key else "value"],
            rationale="Extracted connection details from the storage request.",
        )
        return AgentParameterDraftResponse(
            is_parameter_request=True,
            drafts=[draft],
            missing_details=draft.missing_details,
            rationale=draft.rationale,
        )

    if _SECRET_RE.search(prompt):
        key, secret = _secret_value(prompt)
        if not key:
            key = _key_from_prompt(prompt, fallback="credential")
        field_name = "password" if re.search(r"\b(pass|password|passphrase)\b", prompt, re.IGNORECASE) else "secret"
        if re.search(r"\btoken\b", prompt, re.IGNORECASE):
            field_name = "token"
        if re.search(r"\bapi[_ -]?key\b", prompt, re.IGNORECASE):
            field_name = "api_key"
        draft = AgentParameterDraft(
            operation="create",
            key=key,
            value={field_name: secret} if secret else {},
            description=_compact(prompt, limit=280),
            aliases=[],
            tags=_dedupe(["credential", field_name]),
            sensitive=True,
            confidence=0.7 if key and secret else 0.28,
            missing_details=[item for item, missing in (("key", not key), ("value", not secret)) if missing],
            rationale="Extracted credential-like data from the storage request.",
        )
        return AgentParameterDraftResponse(
            is_parameter_request=True,
            drafts=[draft],
            missing_details=draft.missing_details,
            rationale=draft.rationale,
        )

    key = _key_from_prompt(prompt)
    draft = AgentParameterDraft(
        operation="create",
        key=key,
        value={},
        description=_compact(prompt, limit=280),
        aliases=[],
        tags=_dedupe(["parameter"]),
        sensitive=True,
        confidence=0.2,
        missing_details=[item for item, missing in (("key", not key), ("value", True)) if missing],
        rationale="The request appears to store information, but no structured value was safely extracted.",
    )
    return AgentParameterDraftResponse(
        is_parameter_request=True,
        drafts=[draft],
        missing_details=draft.missing_details,
        rationale=draft.rationale,
    )


def _planner_prompt(request: AgentParameterDraftRequest) -> str:
    return "\n".join(
        [
            *prompt_lines("parameters.draft"),
            "AgentParameterDraftResponse schema:",
            json.dumps(AgentParameterDraftResponse.model_json_schema(), default=str, ensure_ascii=True),
            "User prompt:",
            request.prompt,
            "Request context:",
            json.dumps(request.context, default=str, ensure_ascii=True),
            "Agent mode:",
            request.agent_mode,
            "LLM model:",
            request.llm_model or "",
        ]
    )


def _normalize_llm_response(response: AgentParameterDraftResponse, prompt: str) -> AgentParameterDraftResponse:
    if not response.is_parameter_request:
        return AgentParameterDraftResponse(
            is_parameter_request=False,
            drafts=[],
            missing_details=[],
            rationale=_compact(response.rationale or "The prompt intends to use/read stored parameters, not save a new one.", limit=1000),
        )
    drafts: list[AgentParameterDraft] = []
    missing = _dedupe(list(response.missing_details))
    for draft in response.drafts:
        normalized = _normalize_llm_draft(draft, prompt)
        if normalized is None:
            continue
        drafts.append(normalized)
        missing.extend(normalized.missing_details)
    missing = _dedupe(missing)
    return AgentParameterDraftResponse(
        is_parameter_request=True,
        drafts=drafts,
        missing_details=missing,
        rationale=_compact(response.rationale, limit=1000),
    )


def llm_parameter_draft_response(payload: AgentParameterDraftRequest, *, llm_client: Any) -> AgentParameterDraftResponse:
    """Ask an LLM client whether this is a get or set request, then draft set operations."""

    complete_json = getattr(llm_client, "complete_json", None)
    if not callable(complete_json):
        raise RuntimeError("LLM client is unavailable for parameter drafting.")
    raw = complete_json(_planner_prompt(payload), AgentParameterDraftResponse.model_json_schema())
    try:
        response = AgentParameterDraftResponse.model_validate(raw)
    except ValidationError:
        draft = AgentParameterDraft.model_validate(raw)
        response = AgentParameterDraftResponse(
            is_parameter_request=True,
            drafts=[draft],
            missing_details=list(draft.missing_details),
            rationale=draft.rationale,
        )
    return _normalize_llm_response(response, str(payload.prompt or ""))


def llm_parameter_draft(payload: AgentParameterDraftRequest, *, llm_client: Any) -> AgentParameterDraft:
    """Ask an LLM client for one structured parameter-store draft."""

    response = llm_parameter_draft_response(payload, llm_client=llm_client)
    if not response.is_parameter_request or not response.drafts:
        raise ValueError("The prompt does not contain a parameter-store set request.")
    return response.drafts[0]


def _normalize_llm_draft(draft: AgentParameterDraft, prompt: str) -> AgentParameterDraft | None:
    key = normalize_parameter_key(draft.key)
    value = draft.value if isinstance(draft.value, dict) else {}
    context_json = draft.context_json if isinstance(draft.context_json, dict) else {}
    key_value = _extract_key_value_block(prompt)
    if key_value is not None:
        if not value or _value_has_impossible_connection(value) or len(key_value) >= len(value):
            value = key_value
        if _is_generic_key(key):
            key = _key_from_value(prompt, value)
    elif value and _value_has_impossible_connection(value):
        value = {}
    missing = _dedupe(list(draft.missing_details))
    if not key and value:
        key = _key_from_value(prompt, value)
    if not key and "key" not in missing:
        missing.append("key")
    if not value and "value" not in missing:
        missing.append("value")
    if key and "key" in missing:
        missing = [item for item in missing if item != "key"]
    if value and "value" in missing:
        missing = [item for item in missing if item != "value"]
    if not key and not value:
        return None
    sensitive = bool(draft.sensitive)
    if value and infer_sensitive(value, key=key, description=draft.description or prompt, tags=draft.tags):
        sensitive = True
    return draft.model_copy(
        update={
            "key": key,
            "value": value,
            "context_json": context_json,
            "description": _compact(draft.description or prompt, limit=2000),
            "aliases": _dedupe(list(draft.aliases)),
            "tags": _dedupe(list(draft.tags)),
            "sensitive": sensitive,
            "confidence": max(0.0, min(1.0, float(draft.confidence or 0.0))),
            "missing_details": missing,
            "rationale": _compact(draft.rationale, limit=1000),
        }
    )


def draft_parameter_from_prompt(
    payload: AgentParameterDraftRequest | dict[str, Any],
    *,
    llm_client: Any | None = None,
    planner_enabled: bool = True,
) -> AgentParameterDraftResponse:
    """Draft a parameter-store operation from natural language."""

    request = payload if isinstance(payload, AgentParameterDraftRequest) else AgentParameterDraftRequest.model_validate(payload)
    prompt = str(request.prompt or "").strip()

    if planner_enabled and llm_client is not None and _mentions_parameter_store(prompt):
        try:
            return llm_parameter_draft_response(request, llm_client=llm_client)
        except (RuntimeError, ValidationError, ValueError):
            pass
        except Exception:
            pass

    if not looks_like_parameter_store_request(prompt):
        return AgentParameterDraftResponse(is_parameter_request=False, rationale="No parameter-store set intent detected.")

    return _heuristic_draft(request)
