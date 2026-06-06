"""SQLite-backed local Agent Parameter Store."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.parameters.models import (
    AgentParameterAuditEvent,
    AgentParameterCreate,
    AgentParameterMatch,
    AgentParameterRecord,
    AgentParameterSummary,
    AgentParameterUpdate,
)
from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1
PARAMETER_CONTEXT_KEY = "agent_parameter_store_matches"
PARAMETER_ENV_CONTEXT_KEY = "agent_parameter_shell_env"
PARAMETER_VALUE_CONTEXT_KEY = "agent_parameter_execution_values"
PARAMETER_AGENT_CONTEXT_KEY = "agent_parameter_context_json"

_SENSITIVE_FIELD_RE = re.compile(
    r"(password|passphrase|passwd|token|secret|credential|credentials|private[_-]?key|api[_-]?key)",
    re.IGNORECASE,
)
_PARAMETER_STOPWORDS = {
    "against",
    "and",
    "connection",
    "details",
    "for",
    "from",
    "info",
    "information",
    "parameter",
    "parameters",
    "query",
    "run",
    "store",
    "the",
    "this",
    "use",
    "using",
    "with",
}
_DB_NAME_FIELDS = {
    "database",
    "database_name",
    "db",
    "db_name",
    "dbname",
    "schema",
    "service_name",
}
_DB_CONNECTION_FIELDS = {
    "host",
    "hostname",
    "port",
    "server",
}
_DB_USER_FIELDS = {
    "user",
    "username",
}
_DB_SECRET_FIELDS = {
    "pass",
    "passwd",
    "password",
    "passphrase",
}
_DB_ENGINE_FIELDS = {
    "database_type",
    "db_type",
    "driver",
    "engine",
    "type",
}
_DB_SQLITE_PATH_FIELDS = {
    "database_path",
    "db_file",
    "db_path",
    "file",
    "filename",
    "path",
    "sqlite_path",
}
_DB_SAFE_IDENTITY_FIELDS = (
    _DB_NAME_FIELDS
    | _DB_CONNECTION_FIELDS
    | _DB_ENGINE_FIELDS
    | {"alias", "connection_name", "name"}
)
_DB_PROMPT_IDENTITY_FIELDS = (
    _DB_NAME_FIELDS | _DB_ENGINE_FIELDS | {"alias", "connection_name", "name"}
)
_DB_INTENT_TOKENS = {
    "catalog",
    "column",
    "columns",
    "database",
    "databases",
    "db",
    "namespace",
    "query",
    "queries",
    "schema",
    "schemas",
    "sql",
    "table",
    "tables",
}
_DB_ENGINE_ALIASES = {
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "psql": "postgresql",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlite": "sqlite",
    "sqlite3": "sqlite",
}


def default_parameter_context_json() -> dict[str, Any]:
    """Return the canonical empty user-editable agent context payload."""

    return {
        "version": 1,
        "user_provided_domain_context": {
            "summary": "",
            "prompt_guidance": "",
            "clarification_guidance": "",
            "concepts": [],
            "metrics": [],
            "relationships": [],
        },
    }


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def normalize_parameter_key(key: str) -> str:
    """Return a case-insensitive stable key used for lookup and uniqueness."""

    text = " ".join(str(key or "").strip().split()).lower()
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"[^a-z0-9_.:]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_.:")
    return text


def parameter_env_prefix(key: str) -> str:
    """Return the OF_PARAM_* prefix for one parameter key."""

    normalized = normalize_parameter_key(key)
    slug = re.sub(r"[^A-Z0-9]+", "_", normalized.upper()).strip("_")
    return f"OF_PARAM_{slug or 'VALUE'}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _compact_strings(values: list[Any], *, limit: int = 24) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        text = " ".join(str(item or "").strip().split())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text[:160])
        if len(result) >= limit:
            break
    return result


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 1 and token not in _PARAMETER_STOPWORDS
    }


def _context_domain(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    domain = value.get("user_provided_domain_context")
    return domain if isinstance(domain, dict) else {}


def _safe_context_texts(value: dict[str, Any] | None, *, limit: int = 40) -> list[str]:
    """Return non-secret-looking context snippets for matching and prompts."""

    if not isinstance(value, dict):
        return []
    texts: list[str] = []

    def visit(node: Any, path: str = "") -> None:
        if len(texts) >= limit:
            return
        leaf = _field_leaf(path)
        if leaf and _SENSITIVE_FIELD_RE.search(leaf):
            return
        if isinstance(node, dict):
            for key, child in sorted(node.items()):
                visit(child, f"{path}.{key}" if path else str(key))
            return
        if isinstance(node, list):
            for index, child in enumerate(node[:12]):
                visit(child, f"{path}.{index}" if path else str(index))
            return
        text = " ".join(str(node or "").strip().split())
        if not text:
            return
        if _SENSITIVE_FIELD_RE.search(text):
            return
        texts.append(text[:500])

    visit(value)
    return texts


def parameter_context_summary(context_json: dict[str, Any] | None, *, max_items: int = 12) -> dict[str, Any]:
    """Return a bounded, prompt-safe summary of context_json."""

    domain = _context_domain(context_json)
    summary = str(domain.get("summary") or "").strip()
    prompt_guidance = str(domain.get("prompt_guidance") or "").strip()
    clarification_guidance = str(domain.get("clarification_guidance") or "").strip()

    def compact_named(items: Any, id_field: str) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        compacted: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            entry: dict[str, Any] = {}
            for field in (id_field, "display_name", "one_line_definition", "definition"):
                text = str(item.get(field) or "").strip()
                if text:
                    entry[field] = text[:240]
            phrases = [
                str(value).strip()[:120]
                for value in item.get("user_phrases") or []
                if str(value).strip()
            ]
            synonyms = [
                str(value).strip()[:120]
                for value in item.get("synonyms") or []
                if str(value).strip()
            ]
            if phrases:
                entry["user_phrases"] = phrases[:8]
            if synonyms:
                entry["synonyms"] = synonyms[:8]
            if entry:
                compacted.append(entry)
            if len(compacted) >= max_items:
                break
        return compacted

    result: dict[str, Any] = {}
    if summary:
        result["summary"] = summary[:1000]
    if prompt_guidance:
        result["prompt_guidance"] = prompt_guidance[:1600]
    if clarification_guidance:
        result["clarification_guidance"] = clarification_guidance[:1600]
    concepts = compact_named(domain.get("concepts"), "concept_id")
    metrics = compact_named(domain.get("metrics"), "metric_id")
    relationships = compact_named(domain.get("relationships"), "relationship_id")
    if concepts:
        result["concepts"] = concepts
    if metrics:
        result["metrics"] = metrics
    if relationships:
        result["relationships"] = relationships
    return result


def _field_key(path: str) -> str:
    return normalize_parameter_key(str(path or "").replace(".", "_")).replace(":", "_")


def _field_leaf(path: str) -> str:
    return _field_key(str(path or "").split(".")[-1])


def _iter_scalar_paths(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        paths: list[tuple[str, Any]] = []
        for key, child in sorted(value.items()):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_iter_scalar_paths(child, child_prefix))
        return paths
    if isinstance(value, list) or value is None:
        return []
    return [(prefix, value)] if prefix else []


def _field_env_name(record: AgentParameterRecord, path: str) -> str:
    field_slug = re.sub(r"[^A-Z0-9]+", "_", str(path or "").upper()).strip("_")
    return f"{parameter_env_prefix(record.key)}_{field_slug}" if field_slug else ""


def _safe_identity_pairs(value: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for path, scalar in _iter_scalar_paths(value):
        key = _field_key(path)
        leaf = _field_leaf(path)
        if key not in _DB_SAFE_IDENTITY_FIELDS and leaf not in _DB_SAFE_IDENTITY_FIELDS:
            continue
        text = " ".join(str(scalar or "").strip().split())
        if not text:
            continue
        pairs.append((path, text[:160]))
    return pairs


def _engine_from_texts(values: list[Any]) -> tuple[str, str]:
    for value in values:
        text = str(value or "").strip().lower()
        for token in re.findall(r"[a-z0-9]+", text):
            if token in _DB_ENGINE_ALIASES:
                return _DB_ENGINE_ALIASES[token], "explicit"
    return "", ""


def _database_profile_engine(record: AgentParameterRecord) -> tuple[str, str]:
    scalar_by_field = {
        _field_leaf(path): value
        for path, value in _iter_scalar_paths(record.value_json)
    }
    engine, source = _engine_from_texts(
        [
            *(record.tags or []),
            *(record.aliases or []),
            record.description,
            *[
                value
                for field, value in scalar_by_field.items()
                if field in _DB_ENGINE_FIELDS
            ],
        ]
    )
    if engine:
        return engine, source
    if any(field in scalar_by_field for field in _DB_SQLITE_PATH_FIELDS):
        return "sqlite", "path_field"
    if any(field in scalar_by_field for field in _DB_CONNECTION_FIELDS):
        return "postgresql", "inferred_host_connection"
    return "", ""


def _is_database_profile(record: AgentParameterRecord) -> bool:
    fields = {_field_leaf(path) for path, _value in _iter_scalar_paths(record.value_json)}
    tags = {normalize_parameter_key(tag) for tag in record.tags}
    has_sqlite_path = bool(fields & _DB_SQLITE_PATH_FIELDS)
    has_host = bool(fields & _DB_CONNECTION_FIELDS)
    has_name = bool(fields & _DB_NAME_FIELDS)
    has_auth = bool(fields & (_DB_USER_FIELDS | _DB_SECRET_FIELDS))
    if has_sqlite_path:
        return True
    if has_host and (has_name or has_auth or "database" in tags or "db" in tags):
        return True
    if has_name and has_auth and ("database" in tags or "db" in tags):
        return True
    return False


def parameter_database_profile(record: AgentParameterRecord) -> dict[str, Any]:
    """Return masked database profile metadata for planner prompts."""

    if not _is_database_profile(record):
        return {}
    engine, engine_source = _database_profile_engine(record)
    scalar_paths = _iter_scalar_paths(record.value_json)
    env_by_field = {
        _field_leaf(path): _field_env_name(record, path)
        for path, _value in scalar_paths
        if _field_env_name(record, path)
    }
    identity = {
        _field_leaf(path): text
        for path, text in _safe_identity_pairs(record.value_json)
        if _field_leaf(path) in _DB_PROMPT_IDENTITY_FIELDS
    }
    return {
        "profile_type": "database_connection",
        "engine": engine or "unknown_sql",
        "engine_source": engine_source or "unspecified",
        "identity": identity,
        "env": env_by_field,
        "read_only_auto_use": True,
    }


def _prompt_identifier_candidates(prompt: str) -> set[str]:
    candidates = set(_explicit_key_candidates(prompt))
    for match in re.finditer(r"(?<![A-Za-z0-9_.:-])([A-Za-z0-9][A-Za-z0-9_.:-]{2,})(?![A-Za-z0-9_.:-])", str(prompt or "")):
        normalized = normalize_parameter_key(match.group(1))
        if normalized and normalized not in _PARAMETER_STOPWORDS:
            candidates.add(normalized)
    return candidates


def _near_candidate_matches(prompt_candidates: set[str], record_candidates: set[str]) -> list[str]:
    matches: list[str] = []
    for prompt_candidate in prompt_candidates:
        if len(prompt_candidate) < 5:
            continue
        for record_candidate in record_candidates:
            if len(record_candidate) < 5 or prompt_candidate == record_candidate:
                continue
            ratio = SequenceMatcher(None, prompt_candidate, record_candidate).ratio()
            if ratio >= 0.88:
                matches.append(f"{prompt_candidate}~{record_candidate}")
    return list(dict.fromkeys(matches))[:4]


def _value_field_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in sorted(value.items()):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_value_field_paths(child, child_prefix))
        return paths
    if isinstance(value, list):
        return [prefix] if prefix else []
    return [prefix] if prefix else []


def _value_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _value_schema(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return ["array"] if value else []
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _mask_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _mask_value(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    if value in (None, ""):
        return ""
    return "••••"


def _flatten_scalar_env(value: Any, *, prefix: str, field_prefix: str = "") -> dict[str, str]:
    env: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in sorted(value.items()):
            child_prefix = f"{field_prefix}_{key}" if field_prefix else str(key)
            env.update(_flatten_scalar_env(child, prefix=prefix, field_prefix=child_prefix))
        return env
    if isinstance(value, list):
        return env
    if value is None:
        return env
    field_slug = re.sub(r"[^A-Z0-9]+", "_", field_prefix.upper()).strip("_")
    if not field_slug:
        return env
    env[f"{prefix}_{field_slug}"] = str(value)
    return env


def parameter_shell_env(record: AgentParameterRecord) -> dict[str, str]:
    """Return scalar JSON leaves as OF_PARAM_* environment values."""

    return _flatten_scalar_env(record.value_json, prefix=parameter_env_prefix(record.key))


def masked_parameter_summary(record: AgentParameterRecord) -> AgentParameterSummary:
    """Return a UI/LLM-safe parameter summary without raw values."""

    return AgentParameterSummary(
        key=record.key,
        normalized_key=record.normalized_key,
        description=record.description,
        aliases=list(record.aliases),
        tags=list(record.tags),
        sensitive=bool(record.sensitive),
        value_schema=_value_schema(record.value_json),
        masked_value_json=_mask_value(record.value_json),
        context_json=parameter_context_summary(record.context_json),
        context_schema=_value_schema(record.context_json),
        env={name: "••••" for name in sorted(parameter_shell_env(record))},
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_used_at=record.last_used_at,
        use_count=int(record.use_count or 0),
    )


def _parameter_clarification_choice(
    record: AgentParameterRecord,
    *,
    group: str,
    exact: bool = False,
    score: int = 0,
    match_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Return one masked Parameter Store dropdown choice."""

    env_names = sorted(parameter_shell_env(record))
    return {
        "choice_id": f"param:{record.normalized_key}",
        "key": record.key,
        "normalized_key": record.normalized_key,
        "label": record.key,
        "description": record.description,
        "aliases": list(record.aliases),
        "tags": list(record.tags),
        "sensitive": bool(record.sensitive),
        "field_paths": _value_field_paths(record.value_json),
        "context_summary": parameter_context_summary(record.context_json),
        "context_field_paths": _value_field_paths(record.context_json),
        "env_names": env_names,
        "exact": bool(exact),
        "score": int(score or 0),
        "match_reasons": list(match_reasons or []),
        "group": "relevant" if group == "relevant" else "all",
    }


def parameter_clarification_choices(
    parameter_store: "AgentParameterStore",
    prompt: str,
    *,
    relevant_limit: int = 6,
    total_limit: int = 20,
) -> list[dict[str, Any]]:
    """Return masked dropdown choices for credential clarifications."""

    total = max(1, min(50, int(total_limit or 20)))
    relevant_cap = max(0, min(total, int(relevant_limit or 6)))
    choices: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        matches = parameter_store.retrieve_matches(
            str(prompt or ""),
            limit=relevant_cap or 1,
            record_use=False,
        )
    except Exception:
        matches = []

    for match in matches[:relevant_cap]:
        record = match.record
        if record.normalized_key in seen:
            continue
        seen.add(record.normalized_key)
        choices.append(
            _parameter_clarification_choice(
                record,
                group="relevant",
                exact=match.exact,
                score=match.score,
                match_reasons=list(match.match_reasons),
            )
        )

    remaining = total - len(choices)
    if remaining <= 0:
        return choices

    try:
        records = parameter_store.list(limit=total)
    except Exception:
        records = []
    records.sort(
        key=lambda record: (
            bool(getattr(record, "sensitive", False)),
            str(getattr(record, "updated_at", "") or ""),
        ),
        reverse=True,
    )
    for record in records:
        if len(choices) >= total:
            break
        if record.normalized_key in seen:
            continue
        seen.add(record.normalized_key)
        choices.append(_parameter_clarification_choice(record, group="all"))
    return choices


def _redacted_audit_payload(record_or_payload: Any) -> dict[str, Any]:
    payload = (
        record_or_payload.model_dump(mode="json")
        if hasattr(record_or_payload, "model_dump")
        else dict(record_or_payload or {})
    )
    value = payload.pop("value_json", payload.pop("value", None))
    if isinstance(value, dict):
        payload["value_fields"] = _value_field_paths(value)
        payload["value_schema"] = _value_schema(value)
    context = payload.pop("context_json", None)
    if isinstance(context, dict):
        payload["context_fields"] = _value_field_paths(context)
        payload["context_schema"] = _value_schema(context)
        summary = parameter_context_summary(context)
        if summary:
            payload["context_summary"] = summary
    return payload


def _explicit_key_candidates(prompt: str) -> list[str]:
    text = str(prompt or "")
    candidates: list[str] = []
    patterns = [
        r"\b(?:key|parameter|param)\s+[`'\"]?([A-Za-z0-9_.:\- ]{1,160})[`'\"]?",
        r"\buse\s+[`'\"]?([A-Za-z0-9_.:\- ]{1,160})[`'\"]?\s+(?:from|in)\s+(?:the\s+)?(?:parameter\s+)?store\b",
        r"\b(?:stored|saved)\s+(?:in|under|as)\s+(?:key\s+)?[`'\"]?([A-Za-z0-9_.:\- ]{1,160})[`'\"]?",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw = str(match.group(1) or "").strip(" `'\").,;:")
            raw = re.split(r"\b(?:from|for|to|with|and|then|please)\b", raw, maxsplit=1, flags=re.IGNORECASE)[0]
            normalized = normalize_parameter_key(raw)
            if normalized and normalized not in _PARAMETER_STOPWORDS:
                candidates.append(normalized)
    for match in re.finditer(r"`([^`]{1,160})`", text):
        normalized = normalize_parameter_key(match.group(1))
        if normalized:
            candidates.append(normalized)
    return list(dict.fromkeys(candidates))


def _text_contains_parameter_key(prompt: str, *keys: str) -> bool:
    """Return whether the prompt directly names one normalized parameter key."""

    text = str(prompt or "")
    if not text:
        return False
    for key in keys:
        normalized = normalize_parameter_key(key)
        if not normalized or normalized in _PARAMETER_STOPWORDS:
            continue
        variants = {
            normalized,
            normalized.replace("_", "-"),
            normalized.replace("_", " "),
        }
        for variant in variants:
            if not variant:
                continue
            pattern = rf"(?<![A-Za-z0-9_.:-]){re.escape(variant)}(?![A-Za-z0-9_.:-])"
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
    return False


class AgentParameterStore:
    """Durable SQLite store for user-approved agent parameters."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS parameter_entries (
                    normalized_key TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    description TEXT NOT NULL DEFAULT '',
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    sensitive INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL DEFAULT '',
                    use_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS parameter_audit_events (
                    event_id TEXT PRIMARY KEY,
                    key TEXT NOT NULL DEFAULT '',
                    normalized_key TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'user',
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_parameter_entries_updated ON parameter_entries(updated_at);
                CREATE INDEX IF NOT EXISTS idx_parameter_entries_sensitive ON parameter_entries(sensitive);
                CREATE INDEX IF NOT EXISTS idx_parameter_audit_key ON parameter_audit_events(normalized_key);
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(parameter_entries)").fetchall()
            }
            if "context_json" not in columns:
                conn.execute("ALTER TABLE parameter_entries ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}'")
            ensure_store_schema_version(
                conn,
                store_name="agent_parameters",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> AgentParameterRecord:
        payload = dict(row)
        payload["value_json"] = _json_loads(payload.get("value_json"), {})
        payload["context_json"] = _json_loads(payload.get("context_json"), {})
        payload["aliases"] = _json_loads(payload.pop("aliases_json", "[]"), [])
        payload["tags"] = _json_loads(payload.pop("tags_json", "[]"), [])
        payload["sensitive"] = bool(payload.get("sensitive"))
        return AgentParameterRecord.model_validate(payload)

    @staticmethod
    def _row_to_audit(row: sqlite3.Row) -> AgentParameterAuditEvent:
        payload = dict(row)
        payload["payload"] = _json_loads(payload.pop("payload_json", "{}"), {})
        return AgentParameterAuditEvent.model_validate(payload)

    def _audit(
        self,
        conn: sqlite3.Connection,
        *,
        event_type: str,
        key: str = "",
        normalized_key: str = "",
        actor: str = "user",
        payload: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO parameter_audit_events
            (event_id, key, normalized_key, event_type, actor, timestamp, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("param_evt"),
                str(key or ""),
                str(normalized_key or normalize_parameter_key(key)),
                str(event_type or "parameter.event"),
                str(actor or "user"),
                utc_now_iso(),
                _json_dumps(payload or {}),
            ),
        )

    def create(self, payload: AgentParameterCreate, *, actor: str = "user") -> AgentParameterRecord:
        key = " ".join(str(payload.key or "").split()).strip()
        normalized = normalize_parameter_key(key)
        if not normalized:
            raise ValueError("Parameter key cannot be empty.")
        aliases = _compact_strings(payload.aliases)
        tags = _compact_strings(payload.tags)
        context_json = payload.context_json if payload.context_json else default_parameter_context_json()
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO parameter_entries
                (normalized_key, key, value_json, context_json, description, aliases_json, tags_json,
                 sensitive, created_at, updated_at, last_used_at, use_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0)
                """,
                (
                    normalized,
                    key,
                    _json_dumps(payload.value_json),
                    _json_dumps(context_json),
                    str(payload.description or "").strip(),
                    _json_dumps(aliases),
                    _json_dumps(tags),
                    1 if payload.sensitive else 0,
                    now,
                    now,
                ),
            )
            self._audit(
                conn,
                event_type="parameter.created",
                key=key,
                normalized_key=normalized,
                actor=actor,
                payload=_redacted_audit_payload(payload),
            )
        record = self.get(key)
        if record is None:  # pragma: no cover - defensive
            raise RuntimeError("Parameter entry was not created.")
        return record

    def get(self, key: str) -> AgentParameterRecord | None:
        normalized = normalize_parameter_key(key)
        if not normalized:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM parameter_entries WHERE normalized_key = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM parameter_entries WHERE lower(aliases_json) LIKE ?",
                    (f"%{normalized.lower()}%",),
                ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list(self, *, query: str = "", tag: str = "", limit: int = 200) -> list[AgentParameterRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        normalized_query = str(query or "").strip().lower()
        if normalized_query:
            like = f"%{normalized_query}%"
            normalized_like = f"%{normalize_parameter_key(normalized_query)}%"
            clauses.append(
                "(lower(key) LIKE ? OR normalized_key LIKE ? OR lower(description) LIKE ? "
                "OR lower(aliases_json) LIKE ? OR lower(tags_json) LIKE ? "
                "OR lower(value_json) LIKE ? OR lower(context_json) LIKE ?)"
            )
            params.extend([like, normalized_like, like, like, like, like, like])
        normalized_tag = str(tag or "").strip().lower()
        if normalized_tag:
            clauses.append("lower(tags_json) LIKE ?")
            params.append(f"%{normalized_tag}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM parameter_entries {where} ORDER BY updated_at DESC LIMIT ?",
                [*params, max(1, min(1000, int(limit or 200)))],
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def update(
        self,
        key: str,
        payload: AgentParameterUpdate,
        *,
        actor: str = "user",
    ) -> AgentParameterRecord | None:
        current = self.get(key)
        if current is None:
            return None
        data = payload.model_dump(exclude_unset=True)
        if not data:
            return current
        new_key = " ".join(str(data.get("key") or current.key).split()).strip()
        new_normalized = normalize_parameter_key(new_key)
        if not new_normalized:
            raise ValueError("Parameter key cannot be empty.")
        assignments: list[str] = []
        params: list[Any] = []
        if new_normalized != current.normalized_key:
            assignments.extend(["normalized_key = ?", "key = ?"])
            params.extend([new_normalized, new_key])
        elif new_key != current.key:
            assignments.append("key = ?")
            params.append(new_key)
        if "value_json" in data and data["value_json"] is not None:
            assignments.append("value_json = ?")
            params.append(_json_dumps(data["value_json"]))
        if "context_json" in data and data["context_json"] is not None:
            assignments.append("context_json = ?")
            params.append(_json_dumps(data["context_json"] or default_parameter_context_json()))
        if "description" in data and data["description"] is not None:
            assignments.append("description = ?")
            params.append(str(data["description"]).strip())
        if "aliases" in data and data["aliases"] is not None:
            assignments.append("aliases_json = ?")
            params.append(_json_dumps(_compact_strings(data["aliases"])))
        if "tags" in data and data["tags"] is not None:
            assignments.append("tags_json = ?")
            params.append(_json_dumps(_compact_strings(data["tags"])))
        if "sensitive" in data and data["sensitive"] is not None:
            assignments.append("sensitive = ?")
            params.append(1 if data["sensitive"] else 0)
        if not assignments:
            return current
        assignments.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(current.normalized_key)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE parameter_entries SET {', '.join(assignments)} WHERE normalized_key = ?",
                params,
            )
            self._audit(
                conn,
                event_type="parameter.updated",
                key=new_key,
                normalized_key=new_normalized,
                actor=actor,
                payload=_redacted_audit_payload(data),
            )
        return self.get(new_key)

    def delete(self, key: str, *, actor: str = "user") -> bool:
        current = self.get(key)
        if current is None:
            return False
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM parameter_entries WHERE normalized_key = ?",
                (current.normalized_key,),
            )
            self._audit(
                conn,
                event_type="parameter.deleted",
                key=current.key,
                normalized_key=current.normalized_key,
                actor=actor,
                payload={
                    "key": current.key,
                    "normalized_key": current.normalized_key,
                    "aliases": list(current.aliases),
                    "tags": list(current.tags),
                    "sensitive": bool(current.sensitive),
                    "value_fields": _value_field_paths(current.value_json),
                    "context_fields": _value_field_paths(current.context_json),
                },
            )
        return True

    def audit_events(self, key: str) -> list[AgentParameterAuditEvent]:
        normalized = normalize_parameter_key(key)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM parameter_audit_events
                WHERE normalized_key = ?
                ORDER BY timestamp ASC
                """,
                (normalized,),
            ).fetchall()
        return [self._row_to_audit(row) for row in rows]

    def reveal(self, key: str, *, actor: str = "user") -> AgentParameterRecord | None:
        record = self.get(key)
        if record is None:
            return None
        with self._connect() as conn:
            self._audit(
                conn,
                event_type="parameter.revealed",
                key=record.key,
                normalized_key=record.normalized_key,
                actor=actor,
                payload={"key": record.key, "value_fields": _value_field_paths(record.value_json)},
            )
        return record

    def record_use(self, records: list[AgentParameterRecord], *, actor: str = "runtime") -> list[AgentParameterRecord]:
        if not records:
            return []
        now = utc_now_iso()
        with self._connect() as conn:
            for record in records:
                conn.execute(
                    """
                    UPDATE parameter_entries
                    SET use_count = use_count + 1, last_used_at = ?, updated_at = updated_at
                    WHERE normalized_key = ?
                    """,
                    (now, record.normalized_key),
                )
                self._audit(
                    conn,
                    event_type="parameter.used",
                    key=record.key,
                    normalized_key=record.normalized_key,
                    actor=actor,
                    payload={
                        "key": record.key,
                        "env_names": sorted(parameter_shell_env(record)),
                        "value_fields": _value_field_paths(record.value_json),
                        "context_fields": _value_field_paths(record.context_json),
                    },
                )
        return [
            record.model_copy(
                update={"use_count": int(record.use_count or 0) + 1, "last_used_at": now}
            )
            for record in records
        ]

    def retrieve_matches(
        self,
        prompt: str,
        *,
        limit: int = 6,
        record_use: bool = True,
    ) -> list[AgentParameterMatch]:
        prompt_text = str(prompt or "")
        prompt_tokens = _tokens(prompt_text)
        explicit = set(_explicit_key_candidates(prompt_text))
        prompt_candidates = _prompt_identifier_candidates(prompt_text)
        scored: list[AgentParameterMatch] = []
        for record in self.list(limit=1000):
            key_tokens = _tokens(record.key.replace("_", " "))
            alias_tokens = {token for alias in record.aliases for token in _tokens(alias.replace("_", " "))}
            tag_tokens = {token for tag in record.tags for token in _tokens(tag)}
            description_tokens = _tokens(record.description)
            field_tokens = {token for field in _value_field_paths(record.value_json) for token in _tokens(field)}
            context_tokens = {token for text in _safe_context_texts(record.context_json) for token in _tokens(text)}
            identity_pairs = _safe_identity_pairs(record.value_json)
            identity_tokens = {token for _path, value in identity_pairs for token in _tokens(value)}
            profile = parameter_database_profile(record)
            profile_tokens = _tokens(
                " ".join(
                    [
                        str(profile.get("profile_type") or ""),
                        str(profile.get("engine") or ""),
                        "database" if profile else "",
                    ]
                )
            )
            candidates = {record.normalized_key, normalize_parameter_key(record.key)}
            candidates.update(normalize_parameter_key(alias) for alias in record.aliases)
            candidates.update(
                normalize_parameter_key(value)
                for _path, value in identity_pairs
                if normalize_parameter_key(value)
            )
            exact = bool(explicit & candidates) or _text_contains_parameter_key(prompt_text, *candidates)
            near_matches = _near_candidate_matches(prompt_candidates, candidates)
            overlap = prompt_tokens & (
                key_tokens
                | alias_tokens
                | tag_tokens
                | description_tokens
                | field_tokens
                | identity_tokens
                | profile_tokens
                | context_tokens
            )
            score = 0
            reasons: list[str] = []
            if exact:
                score += 1000
                reasons.append("explicit_key_or_identity")
            if near_matches:
                score += 180
                reasons.append("near:" + ",".join(near_matches))
            if key_tokens & prompt_tokens:
                score += 120 * len(key_tokens & prompt_tokens)
                reasons.append("key:" + ",".join(sorted(key_tokens & prompt_tokens)))
            if alias_tokens & prompt_tokens:
                score += 90 * len(alias_tokens & prompt_tokens)
                reasons.append("alias:" + ",".join(sorted(alias_tokens & prompt_tokens)))
            if identity_tokens & prompt_tokens:
                score += 80 * len(identity_tokens & prompt_tokens)
                reasons.append("identity:" + ",".join(sorted(identity_tokens & prompt_tokens)))
            if tag_tokens & prompt_tokens:
                score += 60 * len(tag_tokens & prompt_tokens)
                reasons.append("tag:" + ",".join(sorted(tag_tokens & prompt_tokens)))
            if profile and (prompt_tokens & _DB_INTENT_TOKENS):
                score += 40
                reasons.append("database_profile")
            if description_tokens & prompt_tokens:
                score += 20 * len(description_tokens & prompt_tokens)
            if field_tokens & prompt_tokens:
                score += 12 * len(field_tokens & prompt_tokens)
            if context_tokens & prompt_tokens:
                score += 18 * len(context_tokens & prompt_tokens)
                reasons.append("context:" + ",".join(sorted((context_tokens & prompt_tokens))[:6]))
            if not exact and not near_matches and len(overlap) < 2:
                continue
            if score <= 0:
                continue
            scored.append(
                AgentParameterMatch(
                    record=record,
                    summary=masked_parameter_summary(record),
                    score=score,
                    exact=exact,
                    match_reasons=reasons or ["text_overlap"],
                )
            )
        scored.sort(key=lambda item: (item.exact, item.score, item.record.updated_at), reverse=True)
        selected = scored[: max(1, min(50, int(limit or 6)))]
        if selected and record_use:
            updated_records = self.record_use([match.record for match in selected])
            by_key = {record.normalized_key: record for record in updated_records}
            selected = [
                match.model_copy(
                    update={
                        "record": by_key.get(match.record.normalized_key, match.record),
                        "summary": masked_parameter_summary(
                            by_key.get(match.record.normalized_key, match.record)
                        ),
                    }
                )
                for match in selected
            ]
        return selected

    def inspect_payload(
        self,
        *,
        query: str = "",
        key: str = "",
        include_audit: bool = False,
    ) -> dict[str, Any]:
        records = [self.get(key)] if key else self.list(query=query)
        records = [record for record in records if record is not None]
        payload: dict[str, Any] = {
            "parameters": [masked_parameter_summary(record).model_dump(mode="json") for record in records],
            "count": len(records),
        }
        if include_audit and key:
            payload["audit_events"] = [
                event.model_dump(mode="json") for event in self.audit_events(key)
            ]
        return payload


def parameter_matches_context(matches: list[AgentParameterMatch]) -> dict[str, Any]:
    """Return masked summaries, env names, and execution env for runtime context."""

    shell_env: dict[str, str] = {}
    execution_values: dict[str, Any] = {}
    summaries: list[dict[str, Any]] = []
    for match in matches:
        record = match.record
        env = parameter_shell_env(record)
        shell_env.update(env)
        execution_values[record.normalized_key] = record.value_json
        summaries.append(
            {
                **match.summary.model_dump(mode="json"),
                "score": match.score,
                "exact": match.exact,
                "match_reasons": list(match.match_reasons),
                "env_names": sorted(env),
                "database_profile": parameter_database_profile(record),
                "context_json": parameter_context_summary(record.context_json),
            }
        )
    return {
        PARAMETER_CONTEXT_KEY: summaries,
        PARAMETER_ENV_CONTEXT_KEY: {name: "••••" for name in sorted(shell_env)},
        PARAMETER_VALUE_CONTEXT_KEY: execution_values,
        PARAMETER_AGENT_CONTEXT_KEY: {
            match.record.normalized_key: parameter_context_summary(match.record.context_json)
            for match in matches
            if parameter_context_summary(match.record.context_json)
        },
        "shell_env": shell_env,
    }


def _schema_field_paths(schema: Any, prefix: str = "") -> list[str]:
    if isinstance(schema, dict):
        paths: list[str] = []
        for key, child in sorted(schema.items()):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_schema_field_paths(child, child_prefix))
        return paths or ([prefix] if prefix else [])
    if isinstance(schema, list):
        return [prefix] if prefix else []
    return [prefix] if prefix else []


def parameter_prompt_lines_from_context(context: Any, *, stage: str = "") -> list[str]:
    """Render LLM-safe parameter summaries from request context."""

    payload = dict(getattr(context, "session_context", context) or {})
    matches = payload.get(PARAMETER_CONTEXT_KEY)
    if not isinstance(matches, list) or not matches:
        return []
    lines = [
        "Relevant Agent Parameter Store entries are available as masked summaries. "
        "Do not ask for or inline raw secret values; use the listed environment variable names during execution. "
        "If the user named a key exactly, treat that entry as the requested parameter. "
        "If credentials are needed and no exact key or compatible field is clear, ask one credential clarification. "
        "Database-shaped entries may be used automatically for read-only SELECT, schema, table, and introspection tasks."
    ]
    adjudication = payload.get("agent_parameter_db_match_adjudication")
    if isinstance(adjudication, dict) and adjudication.get("decision"):
        fragments = [f"decision={adjudication.get('decision')}"]
        selected_key = str(adjudication.get("selected_normalized_key") or "").strip()
        reason = str(adjudication.get("reason") or "").strip()
        confidence = adjudication.get("confidence")
        if selected_key:
            fragments.append(f"selected={selected_key}")
        if isinstance(confidence, (int, float)):
            fragments.append(f"confidence={confidence:.2f}")
        if reason:
            fragments.append(f"reason={reason[:240]}")
        lines.append("DB parameter match adjudication: " + "; ".join(fragments))
    max_items = 6
    database_profile_count = 0
    exact_database_profile_count = 0
    for item in matches[:max_items]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        exact = bool(item.get("exact"))
        description = str(item.get("description") or "").strip()
        aliases = [str(value) for value in item.get("aliases") or [] if str(value).strip()]
        tags = [str(value) for value in item.get("tags") or [] if str(value).strip()]
        schema = item.get("value_schema") if isinstance(item.get("value_schema"), dict) else {}
        fields = _schema_field_paths(schema)
        env_names = item.get("env_names")
        if not isinstance(env_names, list):
            env_payload = item.get("env")
            env_names = sorted(env_payload) if isinstance(env_payload, dict) else []
        database_profile = item.get("database_profile")
        if not isinstance(database_profile, dict):
            database_profile = {}
        fragments = [f"key={key}"]
        if exact:
            fragments.append("match=exact")
        if description:
            fragments.append(f"description={description[:240]}")
        if aliases:
            fragments.append("aliases=" + ", ".join(aliases[:6]))
        if tags:
            fragments.append("tags=" + ", ".join(tags[:8]))
        if fields:
            fragments.append("fields=" + ", ".join(fields[:12]))
        if env_names:
            fragments.append("env=" + ", ".join(str(name) for name in env_names[:16]))
        context_summary = item.get("context_json") if isinstance(item.get("context_json"), dict) else {}
        if context_summary:
            context_fragments: list[str] = []
            summary_text = str(context_summary.get("summary") or "").strip()
            prompt_guidance = str(context_summary.get("prompt_guidance") or "").strip()
            if summary_text:
                context_fragments.append(f"summary={summary_text[:240]}")
            if prompt_guidance:
                context_fragments.append(f"prompt_guidance={prompt_guidance[:320]}")
            for collection_name in ("concepts", "metrics", "relationships"):
                values = context_summary.get(collection_name)
                if not isinstance(values, list) or not values:
                    continue
                names = [
                    str(
                        value.get("display_name")
                        or value.get("concept_id")
                        or value.get("metric_id")
                        or value.get("relationship_id")
                        or ""
                    ).strip()
                    for value in values
                    if isinstance(value, dict)
                ]
                names = [name for name in names if name]
                if names:
                    context_fragments.append(f"{collection_name}=" + ", ".join(names[:6]))
            if context_fragments:
                fragments.append("context=" + " | ".join(context_fragments))
        if database_profile.get("profile_type") == "database_connection":
            database_profile_count += 1
            if exact:
                exact_database_profile_count += 1
            engine = str(database_profile.get("engine") or "unknown_sql").strip()
            fragments.append("profile=database_connection")
            if engine:
                fragments.append(f"engine={engine}")
            identity = database_profile.get("identity")
            if isinstance(identity, dict) and identity:
                safe_identity = [
                    f"{field}={value}"
                    for field, value in sorted(identity.items())
                    if str(field).strip() and str(value).strip()
                ]
                if safe_identity:
                    fragments.append("identity=" + ", ".join(safe_identity[:6]))
            env_by_field = database_profile.get("env")
            if isinstance(env_by_field, dict) and env_by_field:
                preferred_order = [
                    "host",
                    "hostname",
                    "port",
                    "dbname",
                    "database",
                    "db",
                    "user",
                    "username",
                    "password",
                    "passwd",
                    "pass",
                    "engine",
                    "db_type",
                    "path",
                    "database_path",
                    "db_path",
                ]
                ordered_fields = [
                    field for field in preferred_order if field in env_by_field
                ]
                ordered_fields.extend(
                    sorted(field for field in env_by_field if field not in ordered_fields)
                )
                db_env = [
                    f"{field}:${env_by_field[field]}"
                    for field in ordered_fields[:18]
                    if str(env_by_field.get(field) or "").strip()
                ]
                if db_env:
                    fragments.append("db_env=" + ", ".join(db_env))
        lines.append("- " + "; ".join(fragments))
    if database_profile_count:
        lines.append(
            "DB profile guidance: for read-only schema/list/table/introspection tasks, use the matched "
            "database_connection env vars directly. Prefer PostgreSQL-style introspection for host-based or "
            "unknown SQL profiles, for example information_schema.schemata via psql with PGPASSWORD from the "
            "listed password env var; use mysql SHOW DATABASES/SCHEMAS when engine=mysql. Mutating SQL still "
            "requires the normal confirmation path."
        )
        lines.append(
            "DB client safety: probe available gateway clients with command -v psql/mysql/sqlite3 when needed, "
            "but do not invent OF_INPUT_DBNAME, OF_INPUT_HOST, OF_INPUT_USER, OF_INPUT_PASSWORD, OF_INPUT_PORT, "
            "or OF_INPUT_DB_PATH for saved parameters; specifically, do not invent OF_INPUT_DB_PATH. "
            "OF_INPUT_* is only for action inputs; saved parameters "
            "must use the listed OF_PARAM_* names. Do not use sqlite3 unless the matched profile is engine=sqlite "
            "or exposes an explicit sqlite/file/path database field."
        )
    if database_profile_count > 1 and exact_database_profile_count == 0:
        lines.append(
            "DB ambiguity guidance: multiple database_connection entries are plausible; ask the user which "
            "parameter key to use unless one candidate is clearly selected by the user's requested identity."
        )
    return lines


def infer_sensitive(value: dict[str, Any], *, key: str = "", description: str = "", tags: list[str] | None = None) -> bool:
    """Return whether an entry should default to sensitive."""

    text = " ".join([key, description, " ".join(tags or []), " ".join(_value_field_paths(value))])
    return bool(_SENSITIVE_FIELD_RE.search(text))
