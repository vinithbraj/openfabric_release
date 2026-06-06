"""SQLite store and fetcher for LLM-facing prompt templates."""

from __future__ import annotations

import json
import os
import sqlite3
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

from agent_runtime.storage_schema import ensure_store_schema_version


DEFAULT_PROMPT_TEMPLATES_PATH = Path(__file__).with_name("default_templates.json")
STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def default_prompt_db_path() -> Path:
    """Return the default prompt-template DB path for the current process."""

    return Path(
        os.getenv("AOR_AGENT_PROMPTS_DB_PATH", str(Path.cwd() / "artifacts" / "prompts.db"))
    ).expanduser()


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _coerce_body(raw: dict[str, Any]) -> str:
    body = raw.get("body")
    if isinstance(body, str):
        return body
    body_lines = raw.get("body_lines")
    if isinstance(body_lines, list):
        return "\n".join(str(line) for line in body_lines)
    return ""


def _load_default_template_rows(path: str | Path = DEFAULT_PROMPT_TEMPLATES_PATH) -> list[dict[str, Any]]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Default prompt template file must contain a list.")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        prompt_key = str(item.get("prompt_key") or "").strip()
        body = _coerce_body(item).strip("\n")
        if not prompt_key or not body:
            continue
        rows.append(
            {
                "prompt_key": prompt_key,
                "version": int(item.get("version") or 1),
                "body": body,
                "status": str(item.get("status") or "active").strip().lower() or "active",
                "metadata": dict(item.get("metadata") or {}),
            }
        )
    return rows


def load_default_templates(path: str | Path = DEFAULT_PROMPT_TEMPLATES_PATH) -> dict[str, "PromptTemplateRecord"]:
    """Load checked-in fallback templates keyed by prompt_key."""

    return {
        row["prompt_key"]: PromptTemplateRecord(
            prompt_key=row["prompt_key"],
            version=int(row["version"]),
            body=str(row["body"]),
            status=str(row["status"]),
            metadata=dict(row["metadata"]),
            created_at="",
            updated_at="",
        )
        for row in _load_default_template_rows(path)
    }


@dataclass(frozen=True)
class PromptTemplateRecord:
    """One prompt template record."""

    prompt_key: str
    version: int
    body: str
    status: str = "active"
    metadata: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""


class PromptTemplateRenderError(ValueError):
    """Raised when a prompt template cannot be rendered safely."""


class _StringVariables(dict[str, str]):
    def __missing__(self, key: str) -> str:
        raise KeyError(key)


def extract_template_variables(body: str) -> list[str]:
    """Return ordered placeholder names used by a prompt template body."""

    template = Template(str(body or ""))
    if not template.is_valid():
        raise PromptTemplateRenderError(
            "Prompt template has invalid $ placeholder syntax. Use ${name} for variables "
            "or $$ for a literal dollar sign."
        )
    return list(template.get_identifiers())


def validate_template_body(body: str) -> str:
    """Validate a prompt template body and return it unchanged."""

    text = str(body or "")
    if not text.strip():
        raise PromptTemplateRenderError("Prompt template body is required.")
    extract_template_variables(text)
    return text


def render_template_body(body: str, variables: dict[str, Any] | None = None) -> str:
    """Render one prompt template body with explicit variables."""

    text = validate_template_body(body)
    values = _StringVariables({key: str(value) for key, value in dict(variables or {}).items()})
    try:
        return Template(text).substitute(values)
    except (KeyError, ValueError) as exc:
        raise PromptTemplateRenderError(str(exc)) from exc


@dataclass(frozen=True)
class PromptTemplateComparison:
    """A stored prompt template compared with its checked-in default."""

    prompt_key: str
    stored: PromptTemplateRecord | None
    default: PromptTemplateRecord | None

    @property
    def active_record(self) -> PromptTemplateRecord | None:
        return self.stored or self.default

    @property
    def source(self) -> str:
        return "db" if self.stored is not None else "default"

    @property
    def edited(self) -> bool:
        return bool(
            self.stored is not None
            and self.default is not None
            and self.stored.body != self.default.body
        )


class PromptTemplateStore:
    """SQLite-backed store for active prompt templates."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_templates (
                    prompt_key TEXT PRIMARY KEY,
                    version INTEGER NOT NULL DEFAULT 1,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_templates_status ON prompt_templates(status)"
            )
            ensure_store_schema_version(
                connection,
                store_name="agent_prompts",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> PromptTemplateRecord:
        return PromptTemplateRecord(
            prompt_key=str(row["prompt_key"]),
            version=int(row["version"]),
            body=str(row["body"]),
            status=str(row["status"]),
            metadata=_json_loads(str(row["metadata_json"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def get(self, prompt_key: str, *, include_inactive: bool = False) -> PromptTemplateRecord | None:
        """Return one template record by key."""

        key = str(prompt_key or "").strip()
        if not key:
            return None
        query = "SELECT * FROM prompt_templates WHERE prompt_key = ?"
        params: tuple[Any, ...] = (key,)
        if not include_inactive:
            query += " AND status = 'active'"
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list(self, *, include_inactive: bool = False) -> list[PromptTemplateRecord]:
        """Return stored prompt templates."""

        query = "SELECT * FROM prompt_templates"
        if not include_inactive:
            query += " WHERE status = 'active'"
        query += " ORDER BY prompt_key"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._row_to_record(row) for row in rows]

    def upsert(self, record: PromptTemplateRecord) -> PromptTemplateRecord:
        """Insert or replace a prompt template record."""

        now = utc_now_iso()
        key = str(record.prompt_key or "").strip()
        if not key:
            raise ValueError("prompt_key is required.")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT created_at FROM prompt_templates WHERE prompt_key = ?",
                (key,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing is not None else now
            connection.execute(
                """
                INSERT INTO prompt_templates (
                    prompt_key, version, body, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prompt_key) DO UPDATE SET
                    version = excluded.version,
                    body = excluded.body,
                    status = excluded.status,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    int(record.version),
                    str(record.body),
                    str(record.status or "active"),
                    _stable_json(record.metadata or {}),
                    created_at,
                    now,
                ),
            )
        stored = self.get(key, include_inactive=True)
        if stored is None:
            raise RuntimeError(f"Prompt template {key!r} was not stored.")
        return stored

    def seed_defaults(
        self,
        defaults_path: str | Path = DEFAULT_PROMPT_TEMPLATES_PATH,
    ) -> list[PromptTemplateRecord]:
        """Seed checked-in default templates when missing or version-outdated."""

        changed: list[PromptTemplateRecord] = []
        for row in _load_default_template_rows(defaults_path):
            incoming = PromptTemplateRecord(
                prompt_key=row["prompt_key"],
                version=int(row["version"]),
                body=str(row["body"]),
                status=str(row["status"]),
                metadata=dict(row["metadata"]),
            )
            existing = self.get(incoming.prompt_key, include_inactive=True)
            if existing is None or int(existing.version) < int(incoming.version):
                changed.append(self.upsert(incoming))
        return changed

    def list_for_editor(
        self,
        defaults_path: str | Path = DEFAULT_PROMPT_TEMPLATES_PATH,
    ) -> list[PromptTemplateComparison]:
        """Return stored/default template comparisons for the prompt editor."""

        defaults = load_default_templates(defaults_path)
        stored = {record.prompt_key: record for record in self.list(include_inactive=True)}
        keys = sorted(set(defaults) | set(stored))
        return [
            PromptTemplateComparison(
                prompt_key=key,
                stored=stored.get(key),
                default=defaults.get(key),
            )
            for key in keys
        ]

    def get_for_editor(
        self,
        prompt_key: str,
        defaults_path: str | Path = DEFAULT_PROMPT_TEMPLATES_PATH,
    ) -> PromptTemplateComparison | None:
        """Return one stored/default template comparison for the prompt editor."""

        key = str(prompt_key or "").strip()
        if not key:
            return None
        defaults = load_default_templates(defaults_path)
        stored = self.get(key, include_inactive=True)
        default = defaults.get(key)
        if stored is None and default is None:
            return None
        return PromptTemplateComparison(prompt_key=key, stored=stored, default=default)

    def update_body(
        self,
        prompt_key: str,
        body: str,
        defaults_path: str | Path = DEFAULT_PROMPT_TEMPLATES_PATH,
    ) -> PromptTemplateRecord:
        """Update one existing prompt template body and increment its version."""

        key = str(prompt_key or "").strip()
        if not key:
            raise KeyError("prompt_key is required.")
        text = validate_template_body(body)
        comparison = self.get_for_editor(key, defaults_path)
        if comparison is None or comparison.active_record is None:
            raise KeyError(f"Prompt template {key!r} was not found.")
        base = comparison.stored or comparison.default
        if base is None:
            raise KeyError(f"Prompt template {key!r} was not found.")
        return self.upsert(
            PromptTemplateRecord(
                prompt_key=key,
                version=int(base.version) + 1,
                body=text,
                status=str(base.status or "active"),
                metadata=dict(base.metadata or {}),
            )
        )

    def reset_to_default(
        self,
        prompt_key: str,
        defaults_path: str | Path = DEFAULT_PROMPT_TEMPLATES_PATH,
    ) -> PromptTemplateRecord:
        """Restore one prompt template from the checked-in defaults."""

        key = str(prompt_key or "").strip()
        defaults = load_default_templates(defaults_path)
        default = defaults.get(key)
        if default is None:
            raise KeyError(f"Prompt template {key!r} has no checked-in default.")
        return self.upsert(default)

    def render_for_editor(
        self,
        prompt_key: str,
        variables: dict[str, Any] | None = None,
        defaults_path: str | Path = DEFAULT_PROMPT_TEMPLATES_PATH,
    ) -> str:
        """Render the editor-visible body for one prompt template."""

        comparison = self.get_for_editor(prompt_key, defaults_path)
        record = comparison.active_record if comparison is not None else None
        if record is None:
            raise KeyError(f"Prompt template {prompt_key!r} was not found.")
        return render_template_body(record.body, variables)


class PromptFetcher:
    """Fetch and render prompt templates with checked-in fallback behavior."""

    def __init__(
        self,
        store: PromptTemplateStore | None = None,
        *,
        defaults_path: str | Path = DEFAULT_PROMPT_TEMPLATES_PATH,
    ) -> None:
        self.store = store
        self.defaults_path = Path(defaults_path)
        self._defaults: dict[str, PromptTemplateRecord] | None = None

    @property
    def defaults(self) -> dict[str, PromptTemplateRecord]:
        if self._defaults is None:
            self._defaults = load_default_templates(self.defaults_path)
        return self._defaults

    def _warn(self, message: str) -> None:
        warnings.warn(message, RuntimeWarning, stacklevel=3)

    def _get_stored(self, key: str) -> PromptTemplateRecord | None:
        if self.store is None:
            return None
        try:
            return self.store.get(key)
        except sqlite3.Error as exc:
            self._warn(f"Prompt template DB unavailable for {key!r}; using checked-in default. {exc}")
            return None

    def get(self, key: str) -> str:
        """Return the body for one active prompt template."""

        prompt_key = str(key or "").strip()
        record = self._get_stored(prompt_key)
        if record is not None:
            return record.body
        fallback = self.defaults.get(prompt_key)
        if fallback is not None:
            self._warn(f"Prompt template {prompt_key!r} missing from DB; using checked-in default.")
            return fallback.body
        raise KeyError(f"Prompt template {prompt_key!r} was not found.")

    @staticmethod
    def _render_body(body: str, variables: dict[str, Any]) -> str:
        values = _StringVariables({key: str(value) for key, value in dict(variables or {}).items()})
        try:
            return Template(body).substitute(values)
        except (KeyError, ValueError) as exc:
            raise PromptTemplateRenderError(str(exc)) from exc

    def render(self, key: str, variables: dict[str, Any] | None = None) -> str:
        """Render a template using ``string.Template`` placeholders."""

        prompt_key = str(key or "").strip()
        variables = dict(variables or {})
        record = self._get_stored(prompt_key)
        body = record.body if record is not None else None
        if body is not None:
            try:
                return self._render_body(body, variables)
            except PromptTemplateRenderError as exc:
                fallback = self.defaults.get(prompt_key)
                if fallback is None or fallback.body == body:
                    raise
                self._warn(
                    f"Prompt template {prompt_key!r} from DB could not render; "
                    f"using checked-in default. {exc}"
                )
                return self._render_body(fallback.body, variables)
        fallback = self.defaults.get(prompt_key)
        if fallback is None:
            raise KeyError(f"Prompt template {prompt_key!r} was not found.")
        self._warn(f"Prompt template {prompt_key!r} missing from DB; using checked-in default.")
        return self._render_body(fallback.body, variables)

    def lines(self, key: str, variables: dict[str, Any] | None = None) -> list[str]:
        """Render a template and split it into prompt lines."""

        return self.render(key, variables).splitlines()


_FETCHERS: dict[str, PromptFetcher] = {}


def get_prompt_fetcher(db_path: str | Path | None = None) -> PromptFetcher:
    """Return a cached process-local prompt fetcher."""

    path = Path(db_path).expanduser() if db_path is not None else default_prompt_db_path()
    key = str(path.resolve() if path.is_absolute() else path)
    fetcher = _FETCHERS.get(key)
    if fetcher is None:
        store = PromptTemplateStore(path)
        store.seed_defaults()
        fetcher = PromptFetcher(store)
        _FETCHERS[key] = fetcher
    return fetcher


def configure_prompt_fetcher(db_path: str | Path) -> PromptFetcher:
    """Create, seed, and cache the process-local fetcher for a DB path."""

    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    key_path = Path(db_path).expanduser()
    key = str(key_path.resolve() if key_path.is_absolute() else key_path)
    fetcher = PromptFetcher(store)
    _FETCHERS[key] = fetcher
    return fetcher


def seed_default_prompt_templates(db_path: str | Path) -> list[PromptTemplateRecord]:
    """Seed checked-in prompt templates into ``db_path``."""

    store = PromptTemplateStore(db_path)
    changed = store.seed_defaults()
    key_path = Path(db_path).expanduser()
    key = str(key_path.resolve() if key_path.is_absolute() else key_path)
    _FETCHERS[key] = PromptFetcher(store)
    return changed


def render_prompt(key: str, variables: dict[str, Any] | None = None) -> str:
    """Render one prompt template through the default process fetcher."""

    return get_prompt_fetcher().render(key, variables)


def prompt_lines(key: str, variables: dict[str, Any] | None = None) -> list[str]:
    """Render one prompt template as lines through the default process fetcher."""

    return get_prompt_fetcher().lines(key, variables)
