"""SQLite-backed private cache for reusable operator plan structure."""

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
from agent_runtime.plan_cache.models import (
    PlanCacheCandidate,
    PlanCacheEntry,
    PlanCacheLookupContext,
    PlanCacheStats,
    PlanCacheWrite,
)
from agent_runtime.storage_schema import ensure_store_schema_version


SCHEMA_VERSION = 1
MIN_SUPPORTED_SCHEMA_VERSION = 1


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


_CACHE_STOPWORDS = {
    "about",
    "after",
    "again",
    "agent",
    "all",
    "also",
    "and",
    "any",
    "are",
    "branch",
    "command",
    "commands",
    "current",
    "does",
    "done",
    "for",
    "from",
    "get",
    "has",
    "have",
    "how",
    "into",
    "just",
    "list",
    "local",
    "make",
    "mode",
    "not",
    "now",
    "one",
    "only",
    "operator",
    "output",
    "request",
    "requested",
    "result",
    "results",
    "run",
    "shell",
    "should",
    "status",
    "task",
    "that",
    "the",
    "then",
    "there",
    "this",
    "to",
    "use",
    "using",
    "verify",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "with",
}


_SECRET_PATTERNS = [
    re.compile(r"(?i)(passphrase|password|token|api[_-]?key|secret|authorization)(\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(Enter passphrase for key ')[^']+(':)"),
    re.compile(r"(?i)(ssh-add\s+)(\S+)"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+"),
]
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_PATH_RE = re.compile(r"(?<!\w)(?:~|/)[^\s'\"`]+")
_ENV_RE = re.compile(r"\$[A-Z_][A-Z0-9_]*")
_PROVIDED_PAYLOAD_RE = re.compile(r"\[provided\s+[a-z0-9_.-]+\s+payload\]", re.IGNORECASE)
_LONG_IDENTIFIER_RE = re.compile(r"\b[a-f0-9]{7,}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")


def _replace_quoted_and_code_literals(value: str) -> str:
    """Replace literal payload regions with generic markers for cache matching."""

    text = str(value or "")
    result: list[str] = []
    quote: str | None = None
    in_inline_code = False
    in_fence = False
    escaped = False
    index = 0
    while index < len(text):
        if text.startswith("```", index) and quote is None and not in_inline_code:
            result.append(" [code_block] ")
            in_fence = not in_fence
            index += 3
            escaped = False
            continue
        char = text[index]
        if in_fence:
            index += 1
            continue
        if quote is not None:
            if escaped:
                escaped = False
                index += 1
                continue
            if char == "\\":
                escaped = True
                index += 1
                continue
            if char == quote:
                result.append(" [quoted_value] ")
                quote = None
            index += 1
            continue
        if in_inline_code:
            if char == "`":
                result.append(" [inline_literal] ")
                in_inline_code = False
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            escaped = False
            index += 1
            continue
        if char == "`":
            in_inline_code = True
            index += 1
            continue
        result.append(char)
        index += 1
    if quote is not None:
        result.append(" [quoted_value] ")
    if in_inline_code:
        result.append(" [inline_literal] ")
    return "".join(result)


def normalize_cache_text_shape(value: str) -> str:
    """Return a generic operation-shape string for cache signatures/similarity."""

    text = str(value or "")
    text = _PROVIDED_PAYLOAD_RE.sub(" [provided_payload] ", text)
    text = _replace_quoted_and_code_literals(text)
    text = _URL_RE.sub(" [url] ", text)
    text = _PATH_RE.sub(" [path] ", text)
    text = _ENV_RE.sub(" $ENV ", text)
    text = _LONG_IDENTIFIER_RE.sub(" [id] ", text)
    text = _NUMBER_RE.sub(" [number] ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _tokens(value: str) -> set[str]:
    """Return meaningful lowercase tokens for cache similarity."""

    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._/-]*", str(value or "").lower())
        if len(token) > 1 and token not in _CACHE_STOPWORDS
    }


def _stable_hash(value: Any, *, length: int = 24) -> str:
    raw = _json_dumps(value) if not isinstance(value, str) else value
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:length]


def redact_and_clip(value: Any, *, max_chars: int = 2000) -> str:
    """Return a bounded string safe enough for advisory cache storage."""

    text = str(value or "")
    lines: list[str] = []
    for line in text.splitlines():
        if re.search(r"(?i)(password|passphrase|api[_-]?key|secret|authorization|bearer\s+|token)", line):
            lines.append("[redacted]")
        else:
            lines.append(line)
    text = "\n".join(lines)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[redacted]", text)
    text = re.sub(r"(?i)(password|passphrase|token|secret).*", r"\1 [redacted]", text)
    if len(text) > max_chars:
        return text[: max(0, max_chars - 20)] + "\n[truncated]"
    return text


def request_signature(context: PlanCacheLookupContext | PlanCacheWrite) -> str:
    """Return a stable signature over reusable request shape, not runtime output."""

    exact_step_key = str(getattr(context, "exact_step_key", "") or "").strip()
    direct_plan = getattr(context, "direct_plan", None)
    if exact_step_key and isinstance(direct_plan, dict) and direct_plan:
        payload = {
            "cache_type": "streaming_step_tree",
            "exact_step_key": exact_step_key,
            "intent_signature": str(getattr(context, "intent_signature", "") or "").strip(),
            "mode": str(context.mode or "").strip().lower(),
            "model_family": str(
                context.model_family or normalize_model_family(context.model_name)
            ).strip().lower(),
        }
        return _stable_hash(payload)

    payload = {
        "prompt_tokens": sorted(_tokens(normalize_cache_text_shape(context.prompt))),
        "mode": str(context.mode or "").strip().lower(),
        "model_family": str(context.model_family or normalize_model_family(context.model_name)).strip().lower(),
    }
    return _stable_hash(payload)


class AgentPlanCacheStore:
    """Private durable cache for typed operator plan skeletons."""

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
                CREATE TABLE IF NOT EXISTS plan_cache_entries (
                    cache_id TEXT PRIMARY KEY,
                    request_signature TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    model_family TEXT NOT NULL DEFAULT '',
                    cwd TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT '',
                    tool_type TEXT NOT NULL DEFAULT '',
                    intent_type TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    prompt_excerpt TEXT NOT NULL DEFAULT '',
                    self_brief_json TEXT NOT NULL DEFAULT '{}',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    plan_fingerprint TEXT NOT NULL DEFAULT '',
                    action_summaries_json TEXT NOT NULL DEFAULT '[]',
                    risk_summary TEXT NOT NULL DEFAULT '',
                    records_summary_json TEXT NOT NULL DEFAULT '[]',
                    final_response_preview TEXT NOT NULL DEFAULT '',
                    exact_step_key TEXT NOT NULL DEFAULT '',
                    exact_step_prompt_excerpt TEXT NOT NULL DEFAULT '',
                    intent_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    intent_signature TEXT NOT NULL DEFAULT '',
                    direct_plan_json TEXT NOT NULL DEFAULT '{}',
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
                CREATE TABLE IF NOT EXISTS plan_cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_plan_cache_shape
                ON plan_cache_entries(mode, model_family, task_type, tool_type, intent_type);
                CREATE INDEX IF NOT EXISTS idx_plan_cache_updated
                ON plan_cache_entries(updated_at);
                """
            )
            self._ensure_schema_columns(conn)
            ensure_store_schema_version(
                conn,
                store_name="agent_plan_cache",
                current_version=SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_SCHEMA_VERSION,
            )

    @staticmethod
    def _ensure_schema_columns(conn: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(plan_cache_entries)").fetchall()}
        migrations = {
            "exact_step_key": "ALTER TABLE plan_cache_entries ADD COLUMN exact_step_key TEXT NOT NULL DEFAULT ''",
            "exact_step_prompt_excerpt": (
                "ALTER TABLE plan_cache_entries ADD COLUMN exact_step_prompt_excerpt TEXT NOT NULL DEFAULT ''"
            ),
            "intent_snapshot_json": (
                "ALTER TABLE plan_cache_entries ADD COLUMN intent_snapshot_json TEXT NOT NULL DEFAULT '{}'"
            ),
            "intent_signature": "ALTER TABLE plan_cache_entries ADD COLUMN intent_signature TEXT NOT NULL DEFAULT ''",
            "direct_plan_json": "ALTER TABLE plan_cache_entries ADD COLUMN direct_plan_json TEXT NOT NULL DEFAULT '{}'",
        }
        for name, statement in migrations.items():
            if name not in columns:
                conn.execute(statement)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plan_cache_exact_step ON plan_cache_entries(exact_step_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plan_cache_intent_signature ON plan_cache_entries(intent_signature)"
        )

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> PlanCacheEntry:
        payload = dict(row)
        payload["tags"] = _json_loads(payload.pop("tags_json", "[]"), [])
        payload["self_brief"] = _json_loads(payload.pop("self_brief_json", "{}"), {})
        payload["plan"] = _json_loads(payload.pop("plan_json", "{}"), {})
        payload["action_summaries"] = _json_loads(payload.pop("action_summaries_json", "[]"), [])
        payload["records_summary"] = _json_loads(payload.pop("records_summary_json", "[]"), [])
        payload["intent_snapshot"] = _json_loads(payload.pop("intent_snapshot_json", "{}"), {})
        payload["direct_plan"] = _json_loads(payload.pop("direct_plan_json", "{}"), {})
        return PlanCacheEntry.model_validate(payload)

    @staticmethod
    def _action_summaries(plan: dict[str, Any]) -> list[dict[str, Any]]:
        actions = plan.get("actions") if isinstance(plan, dict) else []
        summaries: list[dict[str, Any]] = []
        if not isinstance(actions, list):
            return summaries
        for action in actions:
            if not isinstance(action, dict):
                continue
            summaries.append(
                {
                    "kind": action.get("kind") or "",
                    "summary": redact_and_clip(action.get("summary") or "", max_chars=240),
                    "risk": action.get("risk_level") or "",
                    "interaction": action.get("interaction") or "",
                    "defer_code_generation": bool(action.get("defer_code_generation")),
                    "command": redact_and_clip(action.get("command") or "", max_chars=240),
                }
            )
        return summaries[:12]

    @staticmethod
    def _records_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for record in records[:12]:
            if not isinstance(record, dict):
                continue
            summaries.append(
                {
                    "kind": record.get("kind") or "",
                    "status": record.get("status") or "",
                    "exit_code": record.get("exit_code"),
                    "stdout": redact_and_clip(record.get("stdout") or "", max_chars=500),
                    "stderr": redact_and_clip(record.get("stderr") or "", max_chars=500),
                    "error": redact_and_clip(record.get("error") or "", max_chars=500),
                }
            )
        return summaries

    @staticmethod
    def _risk_summary(plan: dict[str, Any]) -> str:
        actions = plan.get("actions") if isinstance(plan, dict) else []
        if not isinstance(actions, list):
            return ""
        risks = [str(action.get("risk_level") or "").strip() for action in actions if isinstance(action, dict)]
        return ", ".join(risk for risk in risks if risk)

    def _prune(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            DELETE FROM plan_cache_entries
            WHERE cache_id IN (
                SELECT cache_id FROM plan_cache_entries
                ORDER BY updated_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.max_entries,),
        )

    def upsert_entry(self, payload: PlanCacheWrite) -> PlanCacheEntry:
        now = utc_now_iso()
        model_family = payload.model_family or normalize_model_family(payload.model_name)
        signature = request_signature(payload.model_copy(update={"model_family": model_family}))
        plan = payload.plan or {}
        fingerprint = _stable_hash(
            {
                "tasks": plan.get("tasks", []) if isinstance(plan, dict) else [],
                "actions": self._action_summaries(plan),
                "dependencies": plan.get("dependencies", []) if isinstance(plan, dict) else [],
            }
        )
        values = {
            "request_signature": signature,
            "mode": str(payload.mode or ""),
            "model_name": str(payload.model_name or ""),
            "model_family": str(model_family or ""),
            "cwd": str(payload.cwd or ""),
            "task_type": str(payload.task_type or ""),
            "tool_type": str(payload.tool_type or ""),
            "intent_type": str(payload.intent_type or ""),
            "tags_json": _json_dumps(payload.tags),
            "prompt_excerpt": redact_and_clip(payload.prompt, max_chars=1000),
            "self_brief_json": _json_dumps(payload.self_brief),
            "plan_json": _json_dumps(plan),
            "plan_fingerprint": fingerprint,
            "action_summaries_json": _json_dumps(self._action_summaries(plan)),
            "risk_summary": self._risk_summary(plan),
            "records_summary_json": _json_dumps(self._records_summary(payload.records)),
            "final_response_preview": redact_and_clip(payload.final_response, max_chars=1200),
            "exact_step_key": str(payload.exact_step_key or ""),
            "exact_step_prompt_excerpt": redact_and_clip(payload.exact_step_prompt_excerpt, max_chars=1000),
            "intent_snapshot_json": _json_dumps(payload.intent_snapshot),
            "intent_signature": str(payload.intent_signature or ""),
            "direct_plan_json": _json_dumps(payload.direct_plan),
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
                SELECT cache_id, status, success_count, failure_count, created_at,
                       exact_step_key, exact_step_prompt_excerpt, intent_snapshot_json,
                       intent_signature, direct_plan_json
                FROM plan_cache_entries
                WHERE request_signature = ?
                """,
                (signature,),
            ).fetchone()
            if existing is None:
                cache_id = new_id("plancache")
                conn.execute(
                    """
                    INSERT INTO plan_cache_entries
                    (cache_id, request_signature, mode, model_name, model_family, cwd, task_type,
                     tool_type, intent_type, tags_json, prompt_excerpt, self_brief_json, plan_json,
                     plan_fingerprint, action_summaries_json, risk_summary, records_summary_json,
                     final_response_preview, exact_step_key, exact_step_prompt_excerpt,
                     intent_snapshot_json, intent_signature, direct_plan_json, status,
                     failure_category, repair_notes, success_count,
                     failure_count, use_count, created_at, updated_at, last_used_at, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, '', ?)
                    """,
                    (
                        cache_id,
                        values["request_signature"],
                        values["mode"],
                        values["model_name"],
                        values["model_family"],
                        values["cwd"],
                        values["task_type"],
                        values["tool_type"],
                        values["intent_type"],
                        values["tags_json"],
                        values["prompt_excerpt"],
                        values["self_brief_json"],
                        values["plan_json"],
                        values["plan_fingerprint"],
                        values["action_summaries_json"],
                        values["risk_summary"],
                        values["records_summary_json"],
                        values["final_response_preview"],
                        values["exact_step_key"],
                        values["exact_step_prompt_excerpt"],
                        values["intent_snapshot_json"],
                        values["intent_signature"],
                        values["direct_plan_json"],
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
                        UPDATE plan_cache_entries
                        SET failure_count = failure_count + 1,
                            failure_category = ?,
                            repair_notes = ?,
                            records_summary_json = ?,
                            updated_at = ?
                        WHERE cache_id = ?
                        """,
                        (
                            values["failure_category"],
                            values["repair_notes"],
                            values["records_summary_json"],
                            now,
                            cache_id,
                        ),
                    )
                    self._prune(conn)
                    conn.commit()
                    entry = self.get_entry(cache_id)
                    if entry is None:  # pragma: no cover - defensive
                        raise RuntimeError("Plan cache entry was not updated.")
                    return entry
                if not values["exact_step_key"]:
                    values["exact_step_key"] = str(existing["exact_step_key"] or "")
                if not values["exact_step_prompt_excerpt"]:
                    values["exact_step_prompt_excerpt"] = str(existing["exact_step_prompt_excerpt"] or "")
                if values["intent_snapshot_json"] == "{}":
                    values["intent_snapshot_json"] = str(existing["intent_snapshot_json"] or "{}")
                if not values["intent_signature"]:
                    values["intent_signature"] = str(existing["intent_signature"] or "")
                if values["direct_plan_json"] == "{}":
                    values["direct_plan_json"] = str(existing["direct_plan_json"] or "{}")
                conn.execute(
                    """
                    UPDATE plan_cache_entries
                    SET mode = ?, model_name = ?, model_family = ?, cwd = ?, task_type = ?,
                        tool_type = ?, intent_type = ?, tags_json = ?, prompt_excerpt = ?,
                        self_brief_json = ?, plan_json = ?, plan_fingerprint = ?,
                        action_summaries_json = ?, risk_summary = ?, records_summary_json = ?,
                        final_response_preview = ?, exact_step_key = ?,
                        exact_step_prompt_excerpt = ?, intent_snapshot_json = ?,
                        intent_signature = ?, direct_plan_json = ?, status = ?,
                        failure_category = ?, repair_notes = ?,
                        success_count = success_count + ?, failure_count = failure_count + ?,
                        updated_at = ?, schema_version = ?
                    WHERE cache_id = ?
                    """,
                    (
                        values["mode"],
                        values["model_name"],
                        values["model_family"],
                        values["cwd"],
                        values["task_type"],
                        values["tool_type"],
                        values["intent_type"],
                        values["tags_json"],
                        values["prompt_excerpt"],
                        values["self_brief_json"],
                        values["plan_json"],
                        values["plan_fingerprint"],
                        values["action_summaries_json"],
                        values["risk_summary"],
                        values["records_summary_json"],
                        values["final_response_preview"],
                        values["exact_step_key"],
                        values["exact_step_prompt_excerpt"],
                        values["intent_snapshot_json"],
                        values["intent_signature"],
                        values["direct_plan_json"],
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
            raise RuntimeError("Plan cache entry was not written.")
        return entry

    def get_entry(self, cache_id: str) -> PlanCacheEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM plan_cache_entries WHERE cache_id = ?",
                (str(cache_id or ""),),
            ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def retrieve(
        self,
        context: PlanCacheLookupContext,
        *,
        record_use: bool = False,
    ) -> list[PlanCacheCandidate]:
        model_name = str(context.model_name or "").strip()
        family = str(context.model_family or normalize_model_family(model_name)).strip()
        query_tokens = _tokens(
            normalize_cache_text_shape(
                " ".join(
                    [
                        context.prompt,
                        context.mode,
                        context.task_type,
                        context.tool_type,
                        context.intent_type,
                        " ".join(context.tags),
                    ]
                )
            )
        )
        requested_tags = {
            token for tag in context.tags for token in _tokens(normalize_cache_text_shape(tag))
        }
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM plan_cache_entries ORDER BY updated_at DESC").fetchall()
        candidates: list[PlanCacheCandidate] = []
        excluded_cache_ids = {str(item or "").strip() for item in context.excluded_cache_ids if str(item or "").strip()}
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.cache_id in excluded_cache_ids:
                continue
            if entry.status != "success":
                continue
            if entry.failure_count > 0:
                continue
            if any(
                isinstance(action, dict)
                and (
                    str(action.get("kind") or "").startswith("python_")
                    and not bool(action.get("defer_code_generation"))
                )
                for action in entry.action_summaries
            ):
                continue
            if entry.schema_version != SCHEMA_VERSION:
                continue
            if context.mode and entry.mode and context.mode != entry.mode:
                continue
            if context.task_type and entry.task_type and context.task_type != entry.task_type:
                continue
            if context.tool_type and entry.tool_type and context.tool_type != entry.tool_type:
                continue
            if context.intent_type and entry.intent_type and context.intent_type != entry.intent_type:
                continue
            model_score = 0.0
            if model_name and entry.model_name == model_name:
                model_score = 1.0
            elif family and entry.model_family == family:
                model_score = 0.8
            elif not entry.model_name and not entry.model_family:
                model_score = 0.5
            entry_tags = {
                token for tag in entry.tags for token in _tokens(normalize_cache_text_shape(tag))
            }
            matched_tags = sorted(requested_tags & entry_tags)
            entry_tokens = _tokens(
                normalize_cache_text_shape(
                    " ".join(
                        [
                            entry.prompt_excerpt,
                            entry.mode,
                            entry.task_type,
                            entry.tool_type,
                            entry.intent_type,
                            " ".join(entry.tags),
                            " ".join(
                                str(item.get("summary") or item.get("command") or "")
                                for item in entry.action_summaries
                                if isinstance(item, dict)
                            ),
                        ]
                    )
                )
            )
            overlap = query_tokens & entry_tokens
            if not overlap:
                continue
            prompt_score = len(overlap) / max(1, len(query_tokens))
            structured_total = 0
            structured_hits = 0
            for attr in ("task_type", "tool_type", "intent_type"):
                requested = str(getattr(context, attr) or "").strip()
                existing = str(getattr(entry, attr) or "").strip()
                if requested or existing:
                    structured_total += 1
                    if requested and existing and requested == existing:
                        structured_hits += 1
            if requested_tags or entry_tags:
                structured_total += 1
                if matched_tags:
                    structured_hits += 1
            structured_score = structured_hits / structured_total if structured_total else 0.5
            score = min(1.0, (0.62 * prompt_score) + (0.28 * structured_score) + (0.10 * model_score))
            if request_signature(context.model_copy(update={"model_family": family})) == entry.request_signature:
                score = 1.0
            if score < context.similarity_threshold:
                continue
            candidates.append(
                PlanCacheCandidate(
                    entry=entry,
                    score=score,
                    matched_tags=matched_tags,
                    reason=(
                        f"prompt overlap {len(overlap)} token(s), "
                        f"structured score {structured_score:.2f}, model score {model_score:.2f}"
                    ),
                )
            )
        candidates.sort(key=lambda item: (item.score, item.entry.updated_at), reverse=True)
        selected = candidates[: context.limit]
        if record_use and selected:
            for candidate in selected:
                self.mark_used(candidate.entry.cache_id)
        return selected

    def retrieve_exact_step_tree(
        self,
        key: str,
        *,
        intent_signature: str = "",
        record_use: bool = False,
        limit: int = 3,
    ) -> list[PlanCacheCandidate]:
        """Return successful replayable action trees for an exact streaming-step key."""

        exact_key = str(key or "").strip()
        if not exact_key:
            return []
        signature = str(intent_signature or "").strip()
        with self._connect() as conn:
            if signature:
                rows = conn.execute(
                    """
                    SELECT * FROM plan_cache_entries
                    WHERE exact_step_key = ?
                      AND exact_step_key != ''
                      AND intent_signature = ?
                      AND status = 'success'
                      AND failure_count = 0
                      AND schema_version = ?
                      AND direct_plan_json != ''
                      AND direct_plan_json != '{}'
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (exact_key, signature, SCHEMA_VERSION, max(1, int(limit))),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM plan_cache_entries
                    WHERE exact_step_key = ?
                      AND exact_step_key != ''
                      AND status = 'success'
                      AND failure_count = 0
                      AND schema_version = ?
                      AND direct_plan_json != ''
                      AND direct_plan_json != '{}'
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (exact_key, SCHEMA_VERSION, max(1, int(limit))),
                ).fetchall()
        candidates = [
            PlanCacheCandidate(
                entry=self._row_to_entry(row),
                score=1.0,
                matched_tags=[],
                reason="exact streaming step action-tree key match",
            )
            for row in rows
        ]
        if record_use and candidates:
            for candidate in candidates:
                self.mark_used(candidate.entry.cache_id)
        return candidates

    def retrieve_step_tree_by_intent_signature(
        self,
        intent_signature: str,
        *,
        record_use: bool = False,
        limit: int = 3,
    ) -> list[PlanCacheCandidate]:
        """Return successful replayable action trees for a stable step intent signature."""

        signature = str(intent_signature or "").strip()
        if not signature:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM plan_cache_entries
                WHERE intent_signature = ?
                  AND intent_signature != ''
                  AND status = 'success'
                  AND failure_count = 0
                  AND schema_version = ?
                  AND direct_plan_json != ''
                  AND direct_plan_json != '{}'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (signature, SCHEMA_VERSION, max(1, int(limit))),
            ).fetchall()
        candidates = [
            PlanCacheCandidate(
                entry=self._row_to_entry(row),
                score=0.96,
                matched_tags=[],
                reason="stable streaming step intent-signature match",
            )
            for row in rows
        ]
        if record_use and candidates:
            for candidate in candidates:
                self.mark_used(candidate.entry.cache_id)
        return candidates

    def mark_used(self, cache_id: str) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE plan_cache_entries
                SET use_count = use_count + 1, last_used_at = ?
                WHERE cache_id = ?
                """,
                (now, str(cache_id or "")),
            )

    def mark_failed(
        self,
        cache_id: str,
        *,
        failure_category: str = "",
        repair_notes: str = "",
    ) -> None:
        """Record a failed reuse of one learned operator plan cache entry."""

        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE plan_cache_entries
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
        """Delete exactly one learned operator plan cache entry."""

        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM plan_cache_entries WHERE cache_id = ?",
                (str(cache_id or ""),),
            )
        return int(cursor.rowcount or 0) > 0

    def stats(self) -> PlanCacheStats:
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
                FROM plan_cache_entries
                """
            ).fetchone()
            cleared = conn.execute(
                "SELECT value FROM plan_cache_meta WHERE key = 'last_cleared_at'"
            ).fetchone()
        payload = dict(row or {})
        payload["last_cleared_at"] = str(cleared["value"]) if cleared is not None else ""
        return PlanCacheStats.model_validate(payload)

    def clear(self) -> PlanCacheStats:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute("DELETE FROM plan_cache_entries")
            conn.execute(
                """
                INSERT INTO plan_cache_meta (key, value)
                VALUES ('last_cleared_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (now,),
            )
        return self.stats()
