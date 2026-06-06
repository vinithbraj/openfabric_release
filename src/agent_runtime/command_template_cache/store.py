"""SQLite-backed private cache for reusable shell command templates."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime.command_template_cache.models import (
    CommandTemplateCandidate,
    CommandTemplateEntry,
    CommandTemplateLookupContext,
    CommandTemplateStats,
    CommandTemplateWrite,
)
from agent_runtime.core.ids import new_id
from agent_runtime.memory import normalize_model_family
from agent_runtime.operator.command_exceptions import (
    normalize_operator_command,
    operator_command_hash,
)
from agent_runtime.plan_cache.store import (
    _stable_hash,
    _tokens,
    normalize_cache_text_shape,
    redact_and_clip,
)
from agent_runtime.storage_schema import ensure_store_schema_version

SCHEMA_VERSION = 1
MIN_SUPPORTED_SCHEMA_VERSION = 1
LR_MODE_DEFAULT = "lr"
LR_MODE_PAYLOAD = "lr_ex"
_VAR_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENV_REF_RE = re.compile(
    r"\$(?:\{OF_INPUT_([A-Z][A-Z0-9_]*)\}|OF_INPUT_([A-Z][A-Z0-9_]*))"
)


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def normalize_lr_mode(value: Any) -> str:
    """Return the persisted LR mode, defaulting old rows to standard LR."""

    text = str(value or "").strip().lower().replace("-", "_")
    return LR_MODE_PAYLOAD if text in {"lr_ex", "lrex"} else LR_MODE_DEFAULT


def _structured_value_is_specific(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text not in {"", "unknown", "none", "null", "n/a", "na", "unspecified"}


def normalize_payload_bindings(bindings: Any) -> list[dict[str, Any]]:
    """Return bounded payload-binding metadata without reusable payload values."""

    if not isinstance(bindings, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in bindings:
        if not isinstance(item, dict):
            continue
        input_name = normalize_template_variable_name(item.get("input_name"))
        if not input_name:
            continue
        source_field = (
            str(item.get("source_field") or "stdout").strip().lower() or "stdout"
        )
        if source_field not in {"stdout", "stderr", "exit_code", "output", "record", "value"}:
            source_field = "stdout"
        stdin_mode = str(item.get("stdin_mode") or "none").strip().lower() or "none"
        if stdin_mode not in {"none", "input_binding"}:
            stdin_mode = "none"
        generated_text = bool(item.get("generated_text"))
        source_role = str(item.get("source_role") or "").strip().lower() or (
            "generated_text" if generated_text else "runtime_output"
        )
        if source_role not in {"generated_text", "runtime_output", "user_macro"}:
            source_role = "generated_text" if generated_text else "runtime_output"
        key = (input_name, source_field, stdin_mode)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "input_name": input_name,
                "source_role": source_role,
                "source_field": source_field,
                "stdin_mode": stdin_mode,
                "generated_text": generated_text or source_role == "generated_text",
            }
        )
    return normalized


def exact_step_key(prompt: str) -> str:
    """Return the exact rendered streaming-step cache key."""

    return hashlib.sha256(
        str(prompt or "").encode("utf-8", errors="ignore")
    ).hexdigest()


_LRDIRECT_GENERIC_VALUES = {
    "",
    "generic",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "unspecified",
}


def normalize_lrdirect_step_text(value: Any) -> str:
    """Return stable LR Direct step text without domain-specific rewrites."""

    text = str(value or "").strip().lower()
    text = text.replace("`", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_lrdirect_field(value: Any) -> str:
    text = normalize_lrdirect_step_text(value)
    return "generic" if text in _LRDIRECT_GENERIC_VALUES else text


def lrdirect_canonical_step_key(
    *,
    step_description: Any,
    semantic_verb: Any = "",
    object_type: Any = "",
    step_locator: Any = "",
) -> str:
    """Return the stable LR Direct key for a streaming task identity."""

    payload = {
        "schema": "lrdirect-step-v2",
        "locator": _normalize_lrdirect_field(step_locator),
        "description": normalize_lrdirect_step_text(step_description),
        "semantic_verb": _normalize_lrdirect_field(semantic_verb),
        "object_type": _normalize_lrdirect_field(object_type),
    }
    return exact_step_key(_json_dumps(payload))


def lrdirect_canonical_step_excerpt(
    *,
    step_description: Any,
    semantic_verb: Any = "",
    object_type: Any = "",
    step_locator: Any = "",
) -> str:
    """Return a readable LR Direct canonical-key summary."""

    return (
        "LRD canonical step: "
        f"locator={_normalize_lrdirect_field(step_locator)}; "
        f"verb={_normalize_lrdirect_field(semantic_verb)}; "
        f"object={_normalize_lrdirect_field(object_type)}; "
        f"description={normalize_lrdirect_step_text(step_description)}"
    )


def _lrdirect_field_compatible(requested: Any, existing: Any) -> bool:
    requested_text = _normalize_lrdirect_field(requested)
    existing_text = _normalize_lrdirect_field(existing)
    if (
        requested_text in _LRDIRECT_GENERIC_VALUES
        or existing_text in _LRDIRECT_GENERIC_VALUES
    ):
        return True
    return requested_text == existing_text


def _replay_environment_compatible(requested: Any, existing: Any) -> bool:
    requested_text = str(requested or "").strip()
    existing_text = str(existing or "").strip()
    return not requested_text or not existing_text or requested_text == existing_text


def normalize_template_variable_name(value: Any) -> str:
    """Return a lowercase variable name safe for OF_INPUT_* rendering."""

    text = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text or not text[0].isalpha():
        return ""
    return text[:64]


def template_env_name(variable_name: str) -> str:
    """Return the shell environment name for one template variable."""

    name = normalize_template_variable_name(variable_name)
    return f"OF_INPUT_{name.upper()}" if name else ""


def extract_template_input_names(command_template: str) -> list[str]:
    """Return variable names referenced as OF_INPUT_* in one command template."""

    names: list[str] = []
    seen: set[str] = set()
    for match in _ENV_REF_RE.finditer(str(command_template or "")):
        raw = match.group(1) or match.group(2) or ""
        name = normalize_template_variable_name(raw.lower())
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def normalize_template_variables(
    variables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize and deduplicate template variable metadata."""

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in variables:
        if not isinstance(item, dict):
            continue
        name = normalize_template_variable_name(item.get("name"))
        if not name or not _VAR_NAME_RE.fullmatch(name) or name in seen:
            continue
        seen.add(name)
        normalized.append(
            {
                "name": name,
                "description": redact_and_clip(
                    item.get("description") or "", max_chars=300
                ),
                "observed_value": redact_and_clip(
                    item.get("observed_value") or "", max_chars=500
                ),
            }
        )
    return normalized


def render_template_with_values(command_template: str, values: dict[str, Any]) -> str:
    """Render a template by replacing OF_INPUT_* references with shell-quoted values."""

    value_by_env = {
        template_env_name(name): str(value)
        for name, value in values.items()
        if template_env_name(name)
    }

    def replace(match: re.Match[str]) -> str:
        env_name = f"OF_INPUT_{match.group(1) or match.group(2) or ''}"
        return shlex.quote(value_by_env.get(env_name, ""))

    return _ENV_REF_RE.sub(replace, str(command_template or ""))


def command_template_signature(
    context: CommandTemplateLookupContext | CommandTemplateWrite,
) -> str:
    """Return a stable signature over reusable command intent shape."""

    payload = {
        "lr_mode": normalize_lr_mode(getattr(context, "lr_mode", LR_MODE_DEFAULT)),
        "prompt_tokens": sorted(
            _tokens(
                normalize_cache_text_shape(
                    f"{context.prompt} {context.step_description}"
                )
            )
        ),
        "mode": str(context.mode or "").strip().lower(),
        "model_family": str(
            context.model_family or normalize_model_family(context.model_name)
        )
        .strip()
        .lower(),
        "tool_type": str(context.tool_type or "").strip().lower(),
        "intent_type": str(context.intent_type or "").strip().lower(),
    }
    if isinstance(context, CommandTemplateWrite):
        payload["command_template"] = normalize_operator_command(
            context.command_template
        )
    return _stable_hash(payload)


class AgentCommandTemplateCacheStore:
    """Private durable cache for learned shell command templates."""

    def __init__(self, db_path: str | Path, *, max_entries: int = 5000) -> None:
        self.db_path = Path(db_path).expanduser()
        self.max_entries = max(1, int(max_entries))
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
                CREATE TABLE IF NOT EXISTS command_template_entries (
                    template_id TEXT PRIMARY KEY,
                    request_signature TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    model_family TEXT NOT NULL DEFAULT '',
                    cwd TEXT NOT NULL DEFAULT '',
                    gateway_platform TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT '',
                    tool_type TEXT NOT NULL DEFAULT '',
                    intent_type TEXT NOT NULL DEFAULT '',
                    interaction_mode TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    lr_mode TEXT NOT NULL DEFAULT 'lr',
                    payload_bindings_json TEXT NOT NULL DEFAULT '[]',
                    prompt_excerpt TEXT NOT NULL DEFAULT '',
                    step_excerpt TEXT NOT NULL DEFAULT '',
                    exact_step_key TEXT NOT NULL DEFAULT '',
                    exact_step_prompt_excerpt TEXT NOT NULL DEFAULT '',
                    command_template TEXT NOT NULL,
                    variables_json TEXT NOT NULL DEFAULT '[]',
                    direct_action_json TEXT NOT NULL DEFAULT '{}',
                    observed_command_hash TEXT NOT NULL DEFAULT '',
                    observed_command_excerpt TEXT NOT NULL DEFAULT '',
                    risk TEXT NOT NULL DEFAULT '',
                    effect_intent TEXT NOT NULL DEFAULT '',
                    effect_summary TEXT NOT NULL DEFAULT '',
                    requires_confirmation INTEGER NOT NULL DEFAULT 0,
                    approval_observed INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'success',
                    failure_category TEXT NOT NULL DEFAULT '',
                    repair_notes TEXT NOT NULL DEFAULT '',
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL DEFAULT '',
                    schema_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS command_template_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_command_template_shape
                ON command_template_entries(mode, model_family, gateway_platform, task_type, tool_type, intent_type);
                CREATE INDEX IF NOT EXISTS idx_command_template_updated
                ON command_template_entries(updated_at);
                """
            )
            self._ensure_schema_columns(conn)
            ensure_store_schema_version(
                conn,
                store_name="agent_command_template_cache",
                current_version=SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_SCHEMA_VERSION,
            )

    @staticmethod
    def _ensure_schema_columns(conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute(
                "PRAGMA table_info(command_template_entries)"
            ).fetchall()
        }
        migrations = {
            "exact_step_key": "ALTER TABLE command_template_entries ADD COLUMN exact_step_key TEXT NOT NULL DEFAULT ''",
            "exact_step_prompt_excerpt": "ALTER TABLE command_template_entries ADD COLUMN exact_step_prompt_excerpt TEXT NOT NULL DEFAULT ''",
            "direct_action_json": "ALTER TABLE command_template_entries ADD COLUMN direct_action_json TEXT NOT NULL DEFAULT '{}'",
            "lr_mode": "ALTER TABLE command_template_entries ADD COLUMN lr_mode TEXT NOT NULL DEFAULT 'lr'",
            "payload_bindings_json": "ALTER TABLE command_template_entries ADD COLUMN payload_bindings_json TEXT NOT NULL DEFAULT '[]'",
            "interaction_mode": "ALTER TABLE command_template_entries ADD COLUMN interaction_mode TEXT NOT NULL DEFAULT ''",
        }
        for name, statement in migrations.items():
            if name not in columns:
                conn.execute(statement)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_command_template_exact_step ON command_template_entries(exact_step_key)"
        )

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> CommandTemplateEntry:
        payload = dict(row)
        payload["tags"] = _json_loads(payload.pop("tags_json", "[]"), [])
        payload["lr_mode"] = normalize_lr_mode(payload.get("lr_mode", LR_MODE_DEFAULT))
        payload["payload_bindings"] = normalize_payload_bindings(
            _json_loads(payload.pop("payload_bindings_json", "[]"), [])
        )
        payload["variables"] = _json_loads(payload.pop("variables_json", "[]"), [])
        payload["direct_action"] = _json_loads(
            payload.pop("direct_action_json", "{}"), {}
        )
        payload["requires_confirmation"] = bool(payload.get("requires_confirmation"))
        payload["approval_observed"] = bool(payload.get("approval_observed"))
        return CommandTemplateEntry.model_validate(payload)

    def _prune(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            DELETE FROM command_template_entries
            WHERE template_id IN (
                SELECT template_id FROM command_template_entries
                ORDER BY updated_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.max_entries,),
        )

    def upsert_entry(self, payload: CommandTemplateWrite) -> CommandTemplateEntry:
        now = utc_now_iso()
        model_family = payload.model_family or normalize_model_family(
            payload.model_name
        )
        lr_mode = normalize_lr_mode(payload.lr_mode)
        payload_bindings = normalize_payload_bindings(payload.payload_bindings)
        normalized_payload = payload.model_copy(
            update={"model_family": model_family, "lr_mode": lr_mode}
        )
        signature = command_template_signature(normalized_payload)
        variables = normalize_template_variables(payload.variables)
        values = {
            "request_signature": signature,
            "mode": str(payload.mode or ""),
            "model_name": str(payload.model_name or ""),
            "model_family": str(model_family or ""),
            "cwd": str(payload.cwd or ""),
            "gateway_platform": str(payload.gateway_platform or ""),
            "task_type": str(payload.task_type or ""),
            "tool_type": str(payload.tool_type or ""),
            "intent_type": str(payload.intent_type or ""),
            "interaction_mode": str(payload.interaction_mode or ""),
            "tags_json": _json_dumps(payload.tags),
            "lr_mode": lr_mode,
            "payload_bindings_json": _json_dumps(payload_bindings),
            "prompt_excerpt": redact_and_clip(payload.prompt, max_chars=1000),
            "step_excerpt": redact_and_clip(payload.step_description, max_chars=1000),
            "exact_step_key": str(payload.exact_step_key or ""),
            "exact_step_prompt_excerpt": redact_and_clip(
                payload.exact_step_prompt_excerpt or payload.prompt,
                max_chars=1000,
            ),
            "command_template": redact_and_clip(
                payload.command_template, max_chars=2000
            ),
            "variables_json": _json_dumps(variables),
            "direct_action_json": _json_dumps(payload.direct_action),
            "observed_command_hash": operator_command_hash(payload.observed_command),
            "observed_command_excerpt": redact_and_clip(
                payload.observed_command, max_chars=1000
            ),
            "risk": str(payload.risk or ""),
            "effect_intent": str(payload.effect_intent or ""),
            "effect_summary": redact_and_clip(payload.effect_summary, max_chars=500),
            "requires_confirmation": 1 if payload.requires_confirmation else 0,
            "approval_observed": 1 if payload.approval_observed else 0,
            "status": payload.status,
            "failure_category": str(payload.failure_category or ""),
            "repair_notes": redact_and_clip(payload.repair_notes, max_chars=1000),
            "schema_version": SCHEMA_VERSION,
        }
        success_inc = 1 if payload.status == "success" else 0
        failure_inc = 1 if payload.status == "failure" else 0
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT template_id, status, exact_step_key, exact_step_prompt_excerpt, direct_action_json
                FROM command_template_entries
                WHERE request_signature = ?
                """,
                (signature,),
            ).fetchone()
            if existing is None:
                template_id = new_id("cmdtpl")
                conn.execute(
                    """
                    INSERT INTO command_template_entries
                    (template_id, request_signature, mode, model_name, model_family, cwd,
                     gateway_platform, task_type, tool_type, intent_type, interaction_mode, tags_json,
                     lr_mode, payload_bindings_json,
                     prompt_excerpt, step_excerpt, exact_step_key, exact_step_prompt_excerpt,
                     command_template, variables_json, direct_action_json,
                     observed_command_hash, observed_command_excerpt, risk, effect_intent,
                     effect_summary, requires_confirmation, approval_observed, status,
                     failure_category, repair_notes, success_count, failure_count, use_count,
                     created_at, updated_at, last_used_at, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, '', ?)
                    """,
                    (
                        template_id,
                        values["request_signature"],
                        values["mode"],
                        values["model_name"],
                        values["model_family"],
                        values["cwd"],
                        values["gateway_platform"],
                        values["task_type"],
                        values["tool_type"],
                        values["intent_type"],
                        values["interaction_mode"],
                        values["tags_json"],
                        values["lr_mode"],
                        values["payload_bindings_json"],
                        values["prompt_excerpt"],
                        values["step_excerpt"],
                        values["exact_step_key"],
                        values["exact_step_prompt_excerpt"],
                        values["command_template"],
                        values["variables_json"],
                        values["direct_action_json"],
                        values["observed_command_hash"],
                        values["observed_command_excerpt"],
                        values["risk"],
                        values["effect_intent"],
                        values["effect_summary"],
                        values["requires_confirmation"],
                        values["approval_observed"],
                        values["status"],
                        values["failure_category"],
                        values["repair_notes"],
                        success_inc,
                        failure_inc,
                        now,
                        now,
                        values["schema_version"],
                    ),
                )
            else:
                template_id = str(existing["template_id"])
                if (
                    payload.status == "failure"
                    and str(existing["status"] or "") == "success"
                ):
                    conn.execute(
                        """
                        UPDATE command_template_entries
                        SET failure_count = failure_count + 1,
                            failure_category = ?,
                            repair_notes = ?,
                            updated_at = ?
                        WHERE template_id = ?
                        """,
                        (
                            values["failure_category"],
                            values["repair_notes"],
                            now,
                            template_id,
                        ),
                    )
                    self._prune(conn)
                    conn.commit()
                    entry = self.get_entry(template_id)
                    if entry is None:  # pragma: no cover - defensive
                        raise RuntimeError("Command template entry was not updated.")
                    return entry
                if not values["exact_step_key"]:
                    values["exact_step_key"] = str(existing["exact_step_key"] or "")
                if not values["exact_step_prompt_excerpt"]:
                    values["exact_step_prompt_excerpt"] = str(
                        existing["exact_step_prompt_excerpt"] or ""
                    )
                if values["direct_action_json"] == "{}":
                    values["direct_action_json"] = str(
                        existing["direct_action_json"] or "{}"
                    )
                conn.execute(
                    """
                    UPDATE command_template_entries
                    SET mode = ?, model_name = ?, model_family = ?, cwd = ?,
                        gateway_platform = ?, task_type = ?, tool_type = ?, intent_type = ?,
                        tags_json = ?, lr_mode = ?, payload_bindings_json = ?,
                        interaction_mode = ?,
                        prompt_excerpt = ?, step_excerpt = ?,
                        exact_step_key = ?, exact_step_prompt_excerpt = ?,
                        command_template = ?, variables_json = ?, direct_action_json = ?,
                        observed_command_hash = ?,
                        observed_command_excerpt = ?, risk = ?, effect_intent = ?,
                        effect_summary = ?, requires_confirmation = ?, approval_observed = ?,
                        status = ?, failure_category = ?, repair_notes = ?,
                        success_count = success_count + ?, failure_count = failure_count + ?,
                        updated_at = ?, schema_version = ?
                    WHERE template_id = ?
                    """,
                    (
                        values["mode"],
                        values["model_name"],
                        values["model_family"],
                        values["cwd"],
                        values["gateway_platform"],
                        values["task_type"],
                        values["tool_type"],
                        values["intent_type"],
                        values["tags_json"],
                        values["lr_mode"],
                        values["payload_bindings_json"],
                        values["interaction_mode"],
                        values["prompt_excerpt"],
                        values["step_excerpt"],
                        values["exact_step_key"],
                        values["exact_step_prompt_excerpt"],
                        values["command_template"],
                        values["variables_json"],
                        values["direct_action_json"],
                        values["observed_command_hash"],
                        values["observed_command_excerpt"],
                        values["risk"],
                        values["effect_intent"],
                        values["effect_summary"],
                        values["requires_confirmation"],
                        values["approval_observed"],
                        values["status"],
                        values["failure_category"],
                        values["repair_notes"],
                        success_inc,
                        failure_inc,
                        now,
                        values["schema_version"],
                        template_id,
                    ),
                )
            self._prune(conn)
        entry = self.get_entry(template_id)
        if entry is None:  # pragma: no cover - defensive
            raise RuntimeError("Command template entry was not written.")
        return entry

    def get_entry(self, template_id: str) -> CommandTemplateEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM command_template_entries WHERE template_id = ?",
                (str(template_id or ""),),
            ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def retrieve(
        self,
        context: CommandTemplateLookupContext,
        *,
        record_use: bool = False,
    ) -> list[CommandTemplateCandidate]:
        model_name = str(context.model_name or "").strip()
        family = str(context.model_family or normalize_model_family(model_name)).strip()
        requested_lr_mode = normalize_lr_mode(context.lr_mode)
        primary_query_tokens = _tokens(
            normalize_cache_text_shape(
                " ".join(
                    [
                        context.step_description,
                        context.task_type,
                        context.tool_type,
                        context.intent_type,
                    ]
                )
            )
        )
        background_query_tokens = _tokens(
            normalize_cache_text_shape(
                " ".join(
                    [
                        context.prompt,
                        context.step_description,
                        context.mode,
                        context.task_type,
                        context.tool_type,
                        context.intent_type,
                        context.gateway_platform,
                        " ".join(context.tags),
                    ]
                )
            )
        )
        if not primary_query_tokens:
            primary_query_tokens = background_query_tokens
        requested_tags = {
            token
            for tag in context.tags
            for token in _tokens(normalize_cache_text_shape(tag))
        }
        excluded_ids = {
            str(item or "").strip()
            for item in context.excluded_template_ids
            if str(item or "").strip()
        }
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM command_template_entries ORDER BY updated_at DESC"
            ).fetchall()
        candidates: list[CommandTemplateCandidate] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.template_id in excluded_ids:
                continue
            if entry.status != "success" or entry.failure_count > 0:
                continue
            if entry.schema_version != SCHEMA_VERSION:
                continue
            if normalize_lr_mode(entry.lr_mode) != requested_lr_mode:
                continue
            if context.mode and entry.mode and context.mode != entry.mode:
                continue
            if (
                context.gateway_platform
                and entry.gateway_platform
                and context.gateway_platform != entry.gateway_platform
            ):
                continue
            if (
                context.cwd
                and entry.cwd
                and context.cwd != entry.cwd
            ):
                continue
            if (
                _structured_value_is_specific(context.task_type)
                and _structured_value_is_specific(entry.task_type)
                and context.task_type != entry.task_type
            ):
                continue
            if (
                _structured_value_is_specific(context.tool_type)
                and _structured_value_is_specific(entry.tool_type)
                and context.tool_type != entry.tool_type
            ):
                continue
            if (
                _structured_value_is_specific(context.intent_type)
                and _structured_value_is_specific(entry.intent_type)
                and context.intent_type != entry.intent_type
            ):
                continue
            model_score = 0.0
            if model_name and entry.model_name == model_name:
                model_score = 1.0
            elif family and entry.model_family == family:
                model_score = 0.8
            elif not entry.model_name and not entry.model_family:
                model_score = 0.5
            entry_tags = {
                token
                for tag in entry.tags
                for token in _tokens(normalize_cache_text_shape(tag))
            }
            matched_tags = sorted(requested_tags & entry_tags)
            primary_entry_tokens = _tokens(
                normalize_cache_text_shape(
                    " ".join(
                        [
                            entry.step_excerpt,
                            entry.command_template,
                            entry.task_type,
                            entry.tool_type,
                            entry.intent_type,
                        ]
                    )
                )
            )
            background_entry_tokens = _tokens(
                normalize_cache_text_shape(
                    " ".join(
                        [
                            entry.prompt_excerpt,
                            entry.step_excerpt,
                            entry.command_template,
                            entry.gateway_platform,
                            entry.task_type,
                            entry.tool_type,
                            entry.intent_type,
                            " ".join(entry.tags),
                        ]
                    )
                )
            )
            primary_overlap = primary_query_tokens & primary_entry_tokens
            background_overlap = background_query_tokens & background_entry_tokens
            if not primary_overlap and not background_overlap:
                continue
            primary_score = len(primary_overlap) / max(1, len(primary_query_tokens))
            background_score = (
                len(background_overlap) / max(1, len(background_query_tokens))
                if background_query_tokens
                else 1.0
            )
            structured_total = 0
            structured_hits = 0
            for attr in ("gateway_platform", "task_type", "tool_type", "intent_type"):
                requested = str(getattr(context, attr) or "").strip()
                existing = str(getattr(entry, attr) or "").strip()
                requested_specific = _structured_value_is_specific(requested)
                existing_specific = _structured_value_is_specific(existing)
                if requested_specific and existing_specific:
                    structured_total += 1
                    if requested == existing:
                        structured_hits += 1
            if requested_tags or entry_tags:
                structured_total += 1
                if matched_tags:
                    structured_hits += 1
            structured_score = (
                structured_hits / structured_total if structured_total else 0.5
            )
            exact_signature_match = (
                command_template_signature(
                    context.model_copy(update={"model_family": family})
                )
                == entry.request_signature
            )
            score = min(
                1.0,
                (0.70 * primary_score)
                + (0.15 * background_score)
                + (0.10 * structured_score)
                + (0.05 * model_score),
            )
            if exact_signature_match:
                score = 1.0
            if (
                not exact_signature_match
                and background_score < context.secondary_similarity_threshold
            ):
                continue
            if score < context.similarity_threshold:
                continue
            candidates.append(
                CommandTemplateCandidate(
                    entry=entry,
                    score=score,
                    matched_tags=matched_tags,
                    reason=(
                        f"primary overlap {len(primary_overlap)} token(s), "
                        f"background overlap {len(background_overlap)} token(s), "
                        f"background score {background_score:.2f}, "
                        f"structured score {structured_score:.2f}, model score {model_score:.2f}"
                    ),
                )
            )
        candidates.sort(
            key=lambda item: (item.score, item.entry.updated_at), reverse=True
        )
        selected = candidates[: context.limit]
        if record_use and selected:
            for candidate in selected:
                self.mark_used(candidate.entry.template_id)
        return selected

    def retrieve_exact_step(
        self,
        key: str,
        *,
        lr_mode: str = LR_MODE_DEFAULT,
        cwd: str = "",
        gateway_platform: str = "",
        record_use: bool = False,
        limit: int = 3,
    ) -> list[CommandTemplateCandidate]:
        """Return successful direct command actions for an exact streaming-step key."""

        exact_key = str(key or "").strip()
        if not exact_key:
            return []
        requested_lr_mode = normalize_lr_mode(lr_mode)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM command_template_entries
                WHERE exact_step_key = ?
                  AND lr_mode = ?
                  AND exact_step_key != ''
                  AND status = 'success'
                  AND failure_count = 0
                  AND schema_version = ?
                  AND direct_action_json != ''
                  AND direct_action_json != '{}'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (exact_key, requested_lr_mode, SCHEMA_VERSION, max(1, int(limit))),
            ).fetchall()
        candidates: list[CommandTemplateCandidate] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if not _replay_environment_compatible(cwd, entry.cwd):
                continue
            if not _replay_environment_compatible(
                gateway_platform,
                entry.gateway_platform,
            ):
                continue
            candidates.append(
                CommandTemplateCandidate(
                    entry=entry,
                    score=1.0,
                    matched_tags=[],
                    reason="exact streaming step key match",
                )
            )
        if record_use and candidates:
            for candidate in candidates:
                self.mark_used(candidate.entry.template_id)
        return candidates

    def retrieve_lrex_shape_candidates(
        self,
        context: CommandTemplateLookupContext,
        *,
        exact_key: str = "",
        excluded_template_ids: list[str] | None = None,
        limit: int = 5,
        pool_limit: int = 25,
    ) -> list[CommandTemplateCandidate]:
        """Return payload-aware direct entries worth LLM shape judging."""

        excluded_ids = {
            str(item or "").strip()
            for item in list(excluded_template_ids or [])
            if str(item or "").strip()
        }
        if context.excluded_template_ids:
            excluded_ids.update(
                str(item or "").strip()
                for item in context.excluded_template_ids
                if str(item or "").strip()
            )
        requested_exact_key = str(exact_key or "").strip()
        requested_mode = str(context.mode or "").strip()
        requested_gateway = str(context.gateway_platform or "").strip()
        requested_cwd = str(context.cwd or "").strip()
        requested_family = str(
            context.model_family or normalize_model_family(context.model_name)
        ).strip()
        requested_tokens = _tokens(
            normalize_cache_text_shape(
                " ".join(
                    [
                        context.prompt,
                        context.step_description,
                        context.task_type,
                        context.tool_type,
                        context.intent_type,
                        " ".join(context.tags),
                    ]
                )
            )
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM command_template_entries
                WHERE lr_mode = ?
                  AND status = 'success'
                  AND failure_count = 0
                  AND schema_version = ?
                  AND direct_action_json != ''
                  AND direct_action_json != '{}'
                  AND payload_bindings_json != ''
                  AND payload_bindings_json != '[]'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (LR_MODE_PAYLOAD, SCHEMA_VERSION, max(1, int(pool_limit))),
            ).fetchall()
        candidates: list[CommandTemplateCandidate] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.template_id in excluded_ids:
                continue
            if requested_exact_key and entry.exact_step_key == requested_exact_key:
                continue
            if requested_mode and entry.mode and requested_mode != entry.mode:
                continue
            if (
                requested_gateway
                and entry.gateway_platform
                and requested_gateway != entry.gateway_platform
            ):
                continue
            if requested_cwd and entry.cwd and requested_cwd != entry.cwd:
                continue
            model_score = 0.0
            if context.model_name and entry.model_name == context.model_name:
                model_score = 1.0
            elif requested_family and entry.model_family == requested_family:
                model_score = 0.8
            elif not entry.model_name and not entry.model_family:
                model_score = 0.5
            binding_names = " ".join(
                str(item.get("input_name") or "")
                for item in normalize_payload_bindings(entry.payload_bindings)
            )
            entry_tokens = _tokens(
                normalize_cache_text_shape(
                    " ".join(
                        [
                            entry.prompt_excerpt,
                            entry.step_excerpt,
                            entry.exact_step_prompt_excerpt,
                            entry.command_template,
                            entry.task_type,
                            entry.tool_type,
                            entry.intent_type,
                            entry.effect_summary,
                            binding_names,
                            " ".join(entry.tags),
                        ]
                    )
                )
            )
            overlap = requested_tokens & entry_tokens
            structured_total = 0
            structured_hits = 0
            for requested, existing in (
                (context.task_type, entry.task_type),
                (context.tool_type, entry.tool_type),
                (context.intent_type, entry.intent_type),
            ):
                requested_text = str(requested or "").strip()
                existing_text = str(existing or "").strip()
                if _structured_value_is_specific(
                    requested_text
                ) and _structured_value_is_specific(existing_text):
                    structured_total += 1
                    if requested_text == existing_text:
                        structured_hits += 1
            structured_score = (
                structured_hits / structured_total if structured_total else 0.4
            )
            token_score = len(overlap) / max(1, len(requested_tokens))
            if not overlap and structured_hits == 0 and model_score <= 0.0:
                continue
            score = min(
                1.0,
                (0.58 * token_score)
                + (0.27 * structured_score)
                + (0.15 * model_score),
            )
            candidates.append(
                CommandTemplateCandidate(
                    entry=entry,
                    score=score,
                    matched_tags=[],
                    reason=(
                        "LR-EX shape shortlist: "
                        f"token overlap {len(overlap)}, "
                        f"structured score {structured_score:.2f}, "
                        f"model score {model_score:.2f}"
                    ),
                )
            )
        candidates.sort(
            key=lambda item: (item.score, item.entry.updated_at), reverse=True
        )
        return candidates[: max(1, int(limit))]

    def retrieve_legacy_exact_step(
        self,
        *,
        step_description: Any,
        task_type: Any = "",
        tool_type: Any = "",
        intent_type: Any = "",
        lr_mode: str = LR_MODE_DEFAULT,
        cwd: str = "",
        gateway_platform: str = "",
        record_use: bool = False,
        limit: int = 3,
    ) -> list[CommandTemplateCandidate]:
        """Return old LR Direct rows whose exact key used brittle prompt text."""

        normalized_step = normalize_lrdirect_step_text(step_description)
        if not normalized_step:
            return []
        requested_lr_mode = normalize_lr_mode(lr_mode)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM command_template_entries
                WHERE lr_mode = ?
                  AND exact_step_key != ''
                  AND status = 'success'
                  AND failure_count = 0
                  AND schema_version = ?
                  AND direct_action_json != ''
                  AND direct_action_json != '{}'
                ORDER BY updated_at DESC
                """,
                (requested_lr_mode, SCHEMA_VERSION),
            ).fetchall()
        candidates: list[CommandTemplateCandidate] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if normalize_lrdirect_step_text(entry.step_excerpt) != normalized_step:
                continue
            if not _lrdirect_field_compatible(intent_type, entry.intent_type):
                continue
            if not _lrdirect_field_compatible(task_type, entry.task_type):
                continue
            if tool_type and not _lrdirect_field_compatible(tool_type, entry.tool_type):
                continue
            if not _replay_environment_compatible(cwd, entry.cwd):
                continue
            if not _replay_environment_compatible(
                gateway_platform,
                entry.gateway_platform,
            ):
                continue
            candidates.append(
                CommandTemplateCandidate(
                    entry=entry,
                    score=0.98,
                    matched_tags=[],
                    reason="legacy normalized streaming step match",
                )
            )
            if len(candidates) >= max(1, int(limit)):
                break
        if record_use and candidates:
            for candidate in candidates:
                self.mark_used(candidate.entry.template_id)
        return candidates

    def clear_lr_direct(self) -> CommandTemplateStats:
        """Clear exact-step LR Direct metadata while retaining reusable command templates."""

        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE command_template_entries
                SET exact_step_key = '',
                    exact_step_prompt_excerpt = '',
                    direct_action_json = '{}',
                    updated_at = ?
                WHERE exact_step_key != ''
                   OR exact_step_prompt_excerpt != ''
                   OR direct_action_json != '{}'
                """,
                (now,),
            )
            conn.execute(
                """
                INSERT INTO command_template_meta (key, value)
                VALUES ('last_lrdirect_cleared_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (now,),
            )
        return self.stats()

    def mark_used(self, template_id: str) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE command_template_entries
                SET use_count = use_count + 1, last_used_at = ?
                WHERE template_id = ?
                """,
                (now, str(template_id or "")),
            )

    def mark_failed(
        self, template_id: str, *, failure_category: str = "", repair_notes: str = ""
    ) -> None:
        """Record a failed reuse of one learned command template."""

        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE command_template_entries
                SET failure_count = failure_count + 1,
                    failure_category = ?,
                    repair_notes = ?,
                    updated_at = ?
                WHERE template_id = ?
                """,
                (
                    redact_and_clip(failure_category, max_chars=300),
                    redact_and_clip(repair_notes, max_chars=1000),
                    now,
                    str(template_id or ""),
                ),
            )

    def delete_entry(self, template_id: str) -> bool:
        """Delete exactly one learned command template entry."""

        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM command_template_entries WHERE template_id = ?",
                (str(template_id or ""),),
            )
        return int(cursor.rowcount or 0) > 0

    def stats(self) -> CommandTemplateStats:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_entries,
                    COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0) AS success_entries,
                    COALESCE(SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END), 0) AS failure_entries,
                    COALESCE(SUM(use_count), 0) AS total_uses,
                    COALESCE(MAX(last_used_at), '') AS last_used_at,
                    COALESCE(MAX(updated_at), '') AS last_updated_at
                FROM command_template_entries
                """
            ).fetchone()
            cleared = conn.execute(
                "SELECT value FROM command_template_meta WHERE key = 'last_cleared_at'"
            ).fetchone()
        payload = dict(row or {})
        payload["last_cleared_at"] = (
            str(cleared["value"]) if cleared is not None else ""
        )
        return CommandTemplateStats.model_validate(payload)

    def clear(self) -> CommandTemplateStats:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute("DELETE FROM command_template_entries")
            conn.execute(
                """
                INSERT INTO command_template_meta (key, value)
                VALUES ('last_cleared_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (now,),
            )
        return self.stats()


__all__ = [
    "AgentCommandTemplateCacheStore",
    "LR_MODE_DEFAULT",
    "LR_MODE_PAYLOAD",
    "command_template_signature",
    "exact_step_key",
    "extract_template_input_names",
    "lrdirect_canonical_step_excerpt",
    "lrdirect_canonical_step_key",
    "normalize_lr_mode",
    "normalize_lrdirect_step_text",
    "normalize_payload_bindings",
    "normalize_operator_command",
    "normalize_template_variable_name",
    "normalize_template_variables",
    "operator_command_hash",
    "redact_and_clip",
    "render_template_with_values",
    "template_env_name",
]
