"""SQLite-backed private cache for reusable computation-code templates."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.memory import normalize_model_family
from agent_runtime.plan_cache.store import redact_and_clip
from agent_runtime.computation_cache.models import (
    ComputationCacheCandidate,
    ComputationCacheEntry,
    ComputationCacheLookupContext,
    ComputationCacheStats,
    ComputationCacheWrite,
)
from agent_runtime.storage_schema import ensure_store_schema_version


SCHEMA_VERSION = 1
MIN_SUPPORTED_SCHEMA_VERSION = 1


_STOPWORDS = {
    "all",
    "and",
    "calculate",
    "current",
    "for",
    "from",
    "get",
    "in",
    "into",
    "list",
    "of",
    "output",
    "request",
    "result",
    "show",
    "size",
    "task",
    "the",
    "this",
    "to",
    "total",
    "use",
    "with",
}


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


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._/-]*", str(value or "").lower())
        if len(token) > 1 and token not in _STOPWORDS
    }


def _stable_hash(value: Any, *, length: int = 24) -> str:
    raw = _json_dumps(value) if not isinstance(value, str) else value
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:length]


def exact_step_key(prompt: str) -> str:
    """Return the exact rendered streaming-step cache key."""

    return hashlib.sha256(str(prompt or "").encode("utf-8", errors="ignore")).hexdigest()


def _line_bucket(count: int) -> str:
    if count <= 0:
        return "0"
    if count <= 3:
        return "1-3"
    if count <= 10:
        return "4-10"
    if count <= 50:
        return "11-50"
    return "50+"


def _string_profile(value: str) -> dict[str, Any]:
    lines = [line for line in str(value or "").splitlines() if line.strip()]
    sample = lines[:8]
    delimiters = {
        "tab": any("\t" in line for line in sample),
        "comma": any("," in line for line in sample),
        "pipe": any("|" in line for line in sample),
        "multi_space": any(re.search(r"\S\s{2,}\S", line) for line in sample),
    }
    column_counts: list[int] = []
    unit_tokens: set[str] = set()
    numeric_tokens = 0
    for line in sample:
        if "\t" in line:
            parts = line.split("\t")
        elif "|" in line:
            parts = line.split("|")
        elif "," in line:
            parts = line.split(",")
        else:
            parts = re.split(r"\s+", line.strip())
        column_counts.append(len([part for part in parts if part != ""]))
        for number, unit in re.findall(r"(?i)(\d+(?:\.\d+)?)\s*([kmgt]?i?b|bytes?)\b", line):
            if number:
                numeric_tokens += 1
            if unit:
                unit_tokens.add(unit.lower())
    return {
        "type": "string",
        "line_bucket": _line_bucket(len(lines)),
        "has_headerish_first_line": bool(sample and re.search(r"[A-Za-z]", sample[0])),
        "delimiters": delimiters,
        "column_counts": sorted(set(column_counts))[:8],
        "numeric_token_bucket": _line_bucket(numeric_tokens),
        "unit_tokens": sorted(unit_tokens)[:12],
    }


def input_profile(inputs: dict[str, Any]) -> dict[str, Any]:
    """Return a stable shape summary for runtime computation inputs."""

    profile: dict[str, Any] = {}
    for name, value in sorted(dict(inputs or {}).items()):
        if isinstance(value, str):
            profile[name] = _string_profile(value)
        elif isinstance(value, dict):
            profile[name] = {"type": "object", "keys": sorted(map(str, value.keys()))[:24]}
        elif isinstance(value, (list, tuple)):
            profile[name] = {"type": "array", "length_bucket": _line_bucket(len(value))}
        else:
            profile[name] = {"type": type(value).__name__}
    return profile


def input_signature(profile: dict[str, Any]) -> str:
    """Return a hash over input shape, not exact runtime evidence."""

    return _stable_hash(profile)


def request_signature(context: ComputationCacheLookupContext | ComputationCacheWrite) -> str:
    """Return a stable signature over reusable computation request shape."""

    payload = {
        "prompt_tokens": sorted(_tokens(context.prompt)),
        "mode": str(context.mode or "").strip().lower(),
        "model_family": str(context.model_family or normalize_model_family(context.model_name)).strip().lower(),
        "action_kind": str(context.action_kind or "").strip().lower(),
        "input_signature": str(context.input_signature or "").strip(),
    }
    return _stable_hash(payload)


class AgentComputationCacheStore:
    """Private durable cache for computation templates generated from live data."""

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
                CREATE TABLE IF NOT EXISTS computation_cache_entries (
                    cache_id TEXT PRIMARY KEY,
                    request_signature TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    model_family TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT '',
                    tool_type TEXT NOT NULL DEFAULT '',
                    intent_type TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    action_kind TEXT NOT NULL DEFAULT '',
                    action_reason TEXT NOT NULL DEFAULT '',
                    input_profile_json TEXT NOT NULL DEFAULT '{}',
                    input_signature TEXT NOT NULL DEFAULT '',
                    exact_step_key TEXT NOT NULL DEFAULT '',
                    exact_step_prompt_excerpt TEXT NOT NULL DEFAULT '',
                    prompt_excerpt TEXT NOT NULL DEFAULT '',
                    code_template TEXT NOT NULL DEFAULT '',
                    direct_action_json TEXT NOT NULL DEFAULT '{}',
                    declared_output_shape TEXT NOT NULL DEFAULT 'text',
                    allow_zero_result INTEGER NOT NULL DEFAULT 0,
                    template_reason TEXT NOT NULL DEFAULT '',
                    output_preview TEXT NOT NULL DEFAULT '',
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
                CREATE TABLE IF NOT EXISTS computation_cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_computation_cache_shape
                ON computation_cache_entries(mode, model_family, task_type, tool_type, intent_type, action_kind);
                """
            )
            self._ensure_schema_columns(conn)
            ensure_store_schema_version(
                conn,
                store_name="agent_computation_cache",
                current_version=SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_SCHEMA_VERSION,
            )

    @staticmethod
    def _ensure_schema_columns(conn: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(computation_cache_entries)").fetchall()}
        migrations = {
            "exact_step_key": "ALTER TABLE computation_cache_entries ADD COLUMN exact_step_key TEXT NOT NULL DEFAULT ''",
            "exact_step_prompt_excerpt": (
                "ALTER TABLE computation_cache_entries ADD COLUMN exact_step_prompt_excerpt TEXT NOT NULL DEFAULT ''"
            ),
            "direct_action_json": "ALTER TABLE computation_cache_entries ADD COLUMN direct_action_json TEXT NOT NULL DEFAULT '{}'",
        }
        for name, statement in migrations.items():
            if name not in columns:
                conn.execute(statement)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_computation_cache_exact_step ON computation_cache_entries(exact_step_key)"
        )

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> ComputationCacheEntry:
        payload = dict(row)
        payload["tags"] = _json_loads(payload.pop("tags_json", "[]"), [])
        payload["input_profile"] = _json_loads(payload.pop("input_profile_json", "{}"), {})
        payload["direct_action"] = _json_loads(payload.pop("direct_action_json", "{}"), {})
        payload["allow_zero_result"] = bool(payload.get("allow_zero_result"))
        return ComputationCacheEntry.model_validate(payload)

    def _prune(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            DELETE FROM computation_cache_entries
            WHERE cache_id IN (
                SELECT cache_id FROM computation_cache_entries
                ORDER BY updated_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.max_entries,),
        )

    def upsert_entry(self, payload: ComputationCacheWrite) -> ComputationCacheEntry:
        now = utc_now_iso()
        model_family = payload.model_family or normalize_model_family(payload.model_name)
        signature = request_signature(payload.model_copy(update={"model_family": model_family}))
        success_inc = 1 if payload.status == "success" else 0
        failure_inc = 1 if payload.status == "failure" else 0
        values = {
            "request_signature": signature,
            "mode": str(payload.mode or ""),
            "model_name": str(payload.model_name or ""),
            "model_family": str(model_family or ""),
            "task_type": str(payload.task_type or ""),
            "tool_type": str(payload.tool_type or ""),
            "intent_type": str(payload.intent_type or ""),
            "tags_json": _json_dumps(payload.tags),
            "action_kind": str(payload.action_kind or ""),
            "action_reason": redact_and_clip(payload.action_reason, max_chars=500),
            "input_profile_json": _json_dumps(payload.input_profile),
            "input_signature": str(payload.input_signature or ""),
            "exact_step_key": str(payload.exact_step_key or ""),
            "exact_step_prompt_excerpt": redact_and_clip(
                payload.exact_step_prompt_excerpt or payload.prompt,
                max_chars=1000,
            ),
            "prompt_excerpt": redact_and_clip(payload.prompt, max_chars=1000),
            "code_template": redact_and_clip(payload.code_template, max_chars=20000),
            "direct_action_json": _json_dumps(payload.direct_action),
            "declared_output_shape": str(payload.declared_output_shape or "text"),
            "allow_zero_result": 1 if payload.allow_zero_result else 0,
            "template_reason": redact_and_clip(payload.template_reason, max_chars=1000),
            "output_preview": redact_and_clip(payload.output, max_chars=1200),
            "status": payload.status,
            "failure_category": str(payload.failure_category or ""),
            "repair_notes": redact_and_clip(payload.repair_notes, max_chars=1000),
            "schema_version": SCHEMA_VERSION,
        }
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT cache_id, status, exact_step_key, exact_step_prompt_excerpt, direct_action_json
                FROM computation_cache_entries
                WHERE request_signature = ?
                """,
                (signature,),
            ).fetchone()
            if existing is None:
                cache_id = new_id("compcache")
                conn.execute(
                    """
                    INSERT INTO computation_cache_entries
                    (cache_id, request_signature, mode, model_name, model_family, task_type,
                     tool_type, intent_type, tags_json, action_kind, action_reason,
                     input_profile_json, input_signature, exact_step_key,
                     exact_step_prompt_excerpt, prompt_excerpt, code_template,
                     direct_action_json, declared_output_shape, allow_zero_result,
                     template_reason, output_preview,
                     status, failure_category, repair_notes, success_count, failure_count,
                     use_count, created_at, updated_at, last_used_at, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, '', ?)
                    """,
                    (
                        cache_id,
                        values["request_signature"],
                        values["mode"],
                        values["model_name"],
                        values["model_family"],
                        values["task_type"],
                        values["tool_type"],
                        values["intent_type"],
                        values["tags_json"],
                        values["action_kind"],
                        values["action_reason"],
                        values["input_profile_json"],
                        values["input_signature"],
                        values["exact_step_key"],
                        values["exact_step_prompt_excerpt"],
                        values["prompt_excerpt"],
                        values["code_template"],
                        values["direct_action_json"],
                        values["declared_output_shape"],
                        values["allow_zero_result"],
                        values["template_reason"],
                        values["output_preview"],
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
                cache_id = str(existing["cache_id"])
                if payload.status == "failure" and str(existing["status"] or "") == "success":
                    conn.execute(
                        """
                        UPDATE computation_cache_entries
                        SET failure_count = failure_count + 1,
                            failure_category = ?,
                            repair_notes = ?,
                            updated_at = ?
                        WHERE cache_id = ?
                        """,
                        (values["failure_category"], values["repair_notes"], now, cache_id),
                    )
                    self._prune(conn)
                    conn.commit()
                    entry = self.get_entry(cache_id)
                    if entry is None:  # pragma: no cover - defensive
                        raise RuntimeError("Computation cache entry was not updated.")
                    return entry
                if not values["exact_step_key"]:
                    values["exact_step_key"] = str(existing["exact_step_key"] or "")
                if not values["exact_step_prompt_excerpt"]:
                    values["exact_step_prompt_excerpt"] = str(existing["exact_step_prompt_excerpt"] or "")
                if values["direct_action_json"] == "{}":
                    values["direct_action_json"] = str(existing["direct_action_json"] or "{}")
                conn.execute(
                    """
                    UPDATE computation_cache_entries
                    SET mode = ?, model_name = ?, model_family = ?, task_type = ?,
                        tool_type = ?, intent_type = ?, tags_json = ?, action_kind = ?,
                        action_reason = ?, input_profile_json = ?, input_signature = ?,
                        exact_step_key = ?, exact_step_prompt_excerpt = ?,
                        prompt_excerpt = ?, code_template = ?, direct_action_json = ?,
                        declared_output_shape = ?, allow_zero_result = ?, template_reason = ?,
                        output_preview = ?,
                        status = ?, failure_category = ?, repair_notes = ?,
                        success_count = success_count + ?, failure_count = failure_count + ?,
                        updated_at = ?, schema_version = ?
                    WHERE cache_id = ?
                    """,
                    (
                        values["mode"],
                        values["model_name"],
                        values["model_family"],
                        values["task_type"],
                        values["tool_type"],
                        values["intent_type"],
                        values["tags_json"],
                        values["action_kind"],
                        values["action_reason"],
                        values["input_profile_json"],
                        values["input_signature"],
                        values["exact_step_key"],
                        values["exact_step_prompt_excerpt"],
                        values["prompt_excerpt"],
                        values["code_template"],
                        values["direct_action_json"],
                        values["declared_output_shape"],
                        values["allow_zero_result"],
                        values["template_reason"],
                        values["output_preview"],
                        values["status"],
                        values["failure_category"],
                        values["repair_notes"],
                        success_inc,
                        failure_inc,
                        now,
                        values["schema_version"],
                        cache_id,
                    ),
                )
            self._prune(conn)
        entry = self.get_entry(cache_id)
        if entry is None:  # pragma: no cover - defensive
            raise RuntimeError("Computation cache entry was not written.")
        return entry

    def get_entry(self, cache_id: str) -> ComputationCacheEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM computation_cache_entries WHERE cache_id = ?",
                (str(cache_id or ""),),
            ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def retrieve(
        self,
        context: ComputationCacheLookupContext,
        *,
        record_use: bool = False,
    ) -> list[ComputationCacheCandidate]:
        model_name = str(context.model_name or "").strip()
        family = str(context.model_family or normalize_model_family(model_name)).strip()
        query_tokens = _tokens(
            " ".join(
                [
                    context.prompt,
                    context.mode,
                    context.task_type,
                    context.tool_type,
                    context.intent_type,
                    context.action_kind,
                    context.action_reason,
                    " ".join(context.tags),
                ]
            )
        )
        requested_tags = {token for tag in context.tags for token in _tokens(tag)}
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM computation_cache_entries ORDER BY updated_at DESC").fetchall()
        candidates: list[ComputationCacheCandidate] = []
        current_signature = request_signature(context.model_copy(update={"model_family": family}))
        excluded_cache_ids = {str(item or "").strip() for item in context.excluded_cache_ids if str(item or "").strip()}
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.cache_id in excluded_cache_ids:
                continue
            if entry.status != "success" or entry.failure_count > 0:
                continue
            if entry.schema_version != SCHEMA_VERSION:
                continue
            if context.action_kind and entry.action_kind and context.action_kind != entry.action_kind:
                continue
            if context.input_signature and entry.input_signature and context.input_signature != entry.input_signature:
                continue
            for attr in ("mode", "task_type", "tool_type", "intent_type"):
                requested = str(getattr(context, attr) or "").strip()
                existing = str(getattr(entry, attr) or "").strip()
                if requested and existing and requested != existing:
                    break
            else:
                model_score = 0.0
                if model_name and entry.model_name == model_name:
                    model_score = 1.0
                elif family and entry.model_family == family:
                    model_score = 0.8
                elif not entry.model_name and not entry.model_family:
                    model_score = 0.5
                entry_tags = {token for tag in entry.tags for token in _tokens(tag)}
                matched_tags = sorted(requested_tags & entry_tags)
                entry_tokens = _tokens(
                    " ".join(
                        [
                            entry.prompt_excerpt,
                            entry.mode,
                            entry.task_type,
                            entry.tool_type,
                            entry.intent_type,
                            entry.action_kind,
                            entry.action_reason,
                            entry.template_reason,
                            " ".join(entry.tags),
                        ]
                    )
                )
                overlap = query_tokens & entry_tokens
                if not overlap and current_signature != entry.request_signature:
                    continue
                prompt_score = len(overlap) / max(1, len(query_tokens))
                tag_score = 1.0 if matched_tags else 0.0
                score = min(1.0, (0.62 * prompt_score) + (0.20 * tag_score) + (0.18 * model_score))
                if current_signature == entry.request_signature:
                    score = 1.0
                if score < context.similarity_threshold:
                    continue
                candidates.append(
                    ComputationCacheCandidate(
                        entry=entry,
                        score=score,
                        matched_tags=matched_tags,
                        reason=(
                            f"prompt overlap {len(overlap)} token(s), "
                            f"tag score {tag_score:.2f}, model score {model_score:.2f}"
                        ),
                    )
                )
        candidates.sort(key=lambda item: (item.score, item.entry.updated_at), reverse=True)
        selected = candidates[: context.limit]
        if record_use and selected:
            for candidate in selected:
                self.mark_used(candidate.entry.cache_id)
        return selected

    def retrieve_exact_step(
        self,
        key: str,
        *,
        action_kind: str = "",
        input_signature: str = "",
        require_direct_action: bool = True,
        record_use: bool = False,
        limit: int = 3,
    ) -> list[ComputationCacheCandidate]:
        """Return successful computation entries for an exact streaming-step key."""

        exact_key = str(key or "").strip()
        if not exact_key:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM computation_cache_entries
                WHERE exact_step_key = ?
                  AND exact_step_key != ''
                  AND status = 'success'
                  AND failure_count = 0
                  AND schema_version = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (exact_key, SCHEMA_VERSION, max(1, int(limit))),
            ).fetchall()
        requested_kind = str(action_kind or "").strip()
        requested_signature = str(input_signature or "").strip()
        candidates: list[ComputationCacheCandidate] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if requested_kind and entry.action_kind and entry.action_kind != requested_kind:
                continue
            if requested_signature and entry.input_signature and entry.input_signature != requested_signature:
                continue
            if require_direct_action and not entry.direct_action:
                continue
            if not require_direct_action and not entry.code_template:
                continue
            candidates.append(
                ComputationCacheCandidate(
                    entry=entry,
                    score=1.0,
                    matched_tags=[],
                    reason="exact streaming step key match",
                )
            )
        if record_use and candidates:
            for candidate in candidates:
                self.mark_used(candidate.entry.cache_id)
        return candidates

    def retrieve_direct_shape_candidates(
        self,
        context: ComputationCacheLookupContext,
        *,
        exact_key: str = "",
        excluded_cache_ids: list[str] | None = None,
        limit: int = 5,
        pool_limit: int = 25,
    ) -> list[ComputationCacheCandidate]:
        """Return direct Python entries worth LLM shape judging."""

        excluded_ids = {
            str(item or "").strip()
            for item in list(excluded_cache_ids or [])
            if str(item or "").strip()
        }
        if context.excluded_cache_ids:
            excluded_ids.update(
                str(item or "").strip()
                for item in context.excluded_cache_ids
                if str(item or "").strip()
            )
        requested_exact_key = str(exact_key or "").strip()
        requested_mode = str(context.mode or "").strip()
        requested_family = str(
            context.model_family or normalize_model_family(context.model_name)
        ).strip()
        requested_tokens = _tokens(
            " ".join(
                [
                    context.prompt,
                    context.mode,
                    context.task_type,
                    context.tool_type,
                    context.intent_type,
                    context.action_kind,
                    context.action_reason,
                    " ".join(context.tags),
                ]
            )
        )
        requested_kind = str(context.action_kind or "").strip()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM computation_cache_entries
                WHERE status = 'success'
                  AND failure_count = 0
                  AND schema_version = ?
                  AND direct_action_json != ''
                  AND direct_action_json != '{}'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (SCHEMA_VERSION, max(1, int(pool_limit))),
            ).fetchall()
        candidates: list[ComputationCacheCandidate] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.cache_id in excluded_ids:
                continue
            if requested_exact_key and entry.exact_step_key == requested_exact_key:
                continue
            if requested_mode and entry.mode and requested_mode != entry.mode:
                continue
            if requested_kind and entry.action_kind and requested_kind != entry.action_kind:
                continue
            payload = dict(entry.direct_action or {})
            payload_kind = str(payload.get("kind") or entry.action_kind or "").strip()
            if payload_kind not in {"python_action", "python_transform"}:
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
                for item in list(payload.get("input_bindings") or [])
                if isinstance(item, dict)
            )
            entry_tokens = _tokens(
                " ".join(
                    [
                        entry.prompt_excerpt,
                        entry.exact_step_prompt_excerpt,
                        entry.task_type,
                        entry.tool_type,
                        entry.intent_type,
                        entry.action_kind,
                        entry.action_reason,
                        entry.declared_output_shape,
                        entry.template_reason,
                        entry.output_preview,
                        binding_names,
                        " ".join(entry.tags),
                    ]
                )
            )
            overlap = requested_tokens & entry_tokens
            structured_total = 0
            structured_hits = 0
            for requested, existing in (
                (context.task_type, entry.task_type),
                (context.tool_type, entry.tool_type),
                (context.intent_type, entry.intent_type),
                (context.action_kind, entry.action_kind),
            ):
                requested_text = str(requested or "").strip()
                existing_text = str(existing or "").strip()
                if requested_text and existing_text:
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
                ComputationCacheCandidate(
                    entry=entry,
                    score=score,
                    matched_tags=[],
                    reason=(
                        "LR Direct Python shape shortlist: "
                        f"token overlap {len(overlap)}, "
                        f"structured score {structured_score:.2f}, "
                        f"model score {model_score:.2f}"
                    ),
                )
            )
        candidates.sort(key=lambda item: (item.score, item.entry.updated_at), reverse=True)
        return candidates[: max(1, int(limit))]

    def clear_lr_direct(self) -> ComputationCacheStats:
        """Clear exact-step LR Direct metadata while retaining computation templates."""

        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE computation_cache_entries
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
                INSERT INTO computation_cache_meta (key, value)
                VALUES ('last_lrdirect_cleared_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (now,),
            )
        return self.stats()

    def mark_used(self, cache_id: str) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE computation_cache_entries
                SET use_count = use_count + 1, last_used_at = ?
                WHERE cache_id = ?
                """,
                (now, str(cache_id or "")),
            )

    def mark_failed(self, cache_id: str, *, failure_category: str = "", repair_notes: str = "") -> None:
        """Record a failed reuse of one learned computation template."""

        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE computation_cache_entries
                SET failure_count = failure_count + 1,
                    failure_category = ?,
                    repair_notes = ?,
                    updated_at = ?
                WHERE cache_id = ?
                """,
                (
                    redact_and_clip(failure_category, max_chars=300),
                    redact_and_clip(repair_notes, max_chars=1000),
                    now,
                    str(cache_id or ""),
                ),
            )

    def delete_entry(self, cache_id: str) -> bool:
        """Delete exactly one learned computation cache entry."""

        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM computation_cache_entries WHERE cache_id = ?",
                (str(cache_id or ""),),
            )
        return int(cursor.rowcount or 0) > 0

    def stats(self) -> ComputationCacheStats:
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
                FROM computation_cache_entries
                """
            ).fetchone()
            cleared = conn.execute(
                "SELECT value FROM computation_cache_meta WHERE key = 'last_cleared_at'"
            ).fetchone()
        payload = dict(row or {})
        payload["last_cleared_at"] = str(cleared["value"]) if cleared is not None else ""
        return ComputationCacheStats.model_validate(payload)

    def clear(self) -> ComputationCacheStats:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute("DELETE FROM computation_cache_entries")
            conn.execute(
                """
                INSERT INTO computation_cache_meta (key, value)
                VALUES ('last_cleared_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (now,),
            )
        return self.stats()
