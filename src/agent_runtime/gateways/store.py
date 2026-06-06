"""SQLite-backed gateway registry used by the Agent UI."""

from __future__ import annotations

import json
import ipaddress
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, Field

from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_scheme(value: str | None) -> str:
    scheme = str(value or "http").strip().lower() or "http"
    return scheme if scheme in {"http", "https"} else "http"


def _normalize_base_url(raw_url: str | None) -> str:
    raw = str(raw_url or "").strip().rstrip("/")
    if not raw:
        return ""
    for suffix in ("/exec/stream", "/exec/cancel", "/exec"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].rstrip("/")
    parts = urlsplit(raw if "://" in raw else f"http://{raw}")
    scheme = _normalize_scheme(parts.scheme)
    netloc = parts.netloc or parts.path
    path = "" if parts.netloc else ""
    if parts.netloc and parts.path not in {"", "/"}:
        path = parts.path.rstrip("/")
    return f"{scheme}://{netloc}{path}".rstrip("/")


def _host_port_from_base_url(base_url: str) -> tuple[str, int]:
    parts = urlsplit(base_url)
    host = str(parts.hostname or "").strip()
    port = int(parts.port or (443 if parts.scheme == "https" else 80))
    return host, port


def _collapse_spaces(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _is_ip_address(value: str) -> bool:
    candidate = str(value or "").strip().strip("[]")
    if not candidate:
        return False
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _is_url_like(value: str) -> bool:
    text = str(value or "").strip()
    lowered = text.lower()
    if not text:
        return False
    if "://" in text or "/" in text or "?" in text or "#" in text:
        return True
    return lowered.startswith(("http:", "https:", "www."))


def _normalize_gateway_label(value: str | None) -> str:
    label = _collapse_spaces(value)
    if not label:
        raise ValueError("Gateway nickname is required.")
    if _is_ip_address(label):
        raise ValueError("Gateway nickname cannot be an IP address.")
    if _is_url_like(label):
        raise ValueError("Gateway nickname cannot be a URL.")
    return label


def _gateway_label_key(value: str | None) -> str:
    return _collapse_spaces(value).casefold()


class AgentGatewayRecord(BaseModel):
    """One configured gateway endpoint shown in the Agent UI."""

    gateway_id: str
    label: str
    scheme: str = "http"
    host: str
    port: int = Field(ge=1, le=65535)
    base_url: str
    node: str = ""
    terminal_cwd: str = ""
    platform: str = "unknown"
    platform_label: str = "Unknown"
    platform_version: str = ""
    architecture: str = ""
    shell: str = ""
    command_profile: str = ""
    capability_tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    status: str = "unknown"
    last_checked_at: str = ""
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""
    source: str = "user"


class AgentGatewayStore:
    """Persist and health-check gateway endpoints for the local Agent UI."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self.repair_nicknames()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gateways (
                    gateway_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    scheme TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    base_url TEXT NOT NULL,
                    node TEXT NOT NULL DEFAULT '',
                    terminal_cwd TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT 'unknown',
                    platform_label TEXT NOT NULL DEFAULT 'Unknown',
                    platform_version TEXT NOT NULL DEFAULT '',
                    architecture TEXT NOT NULL DEFAULT '',
                    shell TEXT NOT NULL DEFAULT '',
                    command_profile TEXT NOT NULL DEFAULT '',
                    capability_tags_json TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    last_checked_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'user'
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(gateways)").fetchall()
            }
            if "terminal_cwd" not in columns:
                connection.execute("ALTER TABLE gateways ADD COLUMN terminal_cwd TEXT NOT NULL DEFAULT ''")
            if "platform" not in columns:
                connection.execute("ALTER TABLE gateways ADD COLUMN platform TEXT NOT NULL DEFAULT 'unknown'")
            if "platform_label" not in columns:
                connection.execute("ALTER TABLE gateways ADD COLUMN platform_label TEXT NOT NULL DEFAULT 'Unknown'")
            if "platform_version" not in columns:
                connection.execute("ALTER TABLE gateways ADD COLUMN platform_version TEXT NOT NULL DEFAULT ''")
            if "architecture" not in columns:
                connection.execute("ALTER TABLE gateways ADD COLUMN architecture TEXT NOT NULL DEFAULT ''")
            if "shell" not in columns:
                connection.execute("ALTER TABLE gateways ADD COLUMN shell TEXT NOT NULL DEFAULT ''")
            if "command_profile" not in columns:
                connection.execute("ALTER TABLE gateways ADD COLUMN command_profile TEXT NOT NULL DEFAULT ''")
            if "capability_tags_json" not in columns:
                connection.execute("ALTER TABLE gateways ADD COLUMN capability_tags_json TEXT NOT NULL DEFAULT '[]'")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_gateways_node ON gateways(node)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_gateways_enabled ON gateways(enabled)")
            ensure_store_schema_version(
                connection,
                store_name="agent_gateways",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> AgentGatewayRecord:
        payload = dict(row)
        payload["enabled"] = bool(payload.get("enabled"))
        raw_tags = payload.pop("capability_tags_json", "[]")
        try:
            tags = json.loads(str(raw_tags or "[]"))
        except json.JSONDecodeError:
            tags = []
        payload["capability_tags"] = [str(tag) for tag in tags] if isinstance(tags, list) else []
        return AgentGatewayRecord.model_validate(payload)

    @staticmethod
    def _id_from_node(node: str) -> str:
        safe = "".join(ch if ch.isalnum() else "-" for ch in str(node or "gateway").lower()).strip("-")
        return f"config-{safe or 'gateway'}"

    @staticmethod
    def _generated_label(gateway_id: str) -> str:
        suffix = "".join(ch for ch in str(gateway_id or "") if ch.isalnum())[-6:] or uuid4().hex[:6]
        return f"Gateway {suffix.upper()}"

    @staticmethod
    def _is_valid_label(value: str | None) -> bool:
        try:
            _normalize_gateway_label(value)
        except ValueError:
            return False
        return True

    @staticmethod
    def _unique_label_from_candidates(
        *,
        candidates: list[str],
        gateway_id: str,
        used_keys: set[str],
    ) -> str:
        for raw_candidate in candidates:
            if not AgentGatewayStore._is_valid_label(raw_candidate):
                continue
            base = _normalize_gateway_label(raw_candidate)
            for index in range(1, 1000):
                candidate = base if index == 1 else f"{base} {index}"
                key = _gateway_label_key(candidate)
                if key not in used_keys:
                    used_keys.add(key)
                    return candidate
        generated = AgentGatewayStore._generated_label(gateway_id)
        for index in range(1, 1000):
            candidate = generated if index == 1 else f"{generated} {index}"
            key = _gateway_label_key(candidate)
            if key not in used_keys:
                used_keys.add(key)
                return candidate
        raise ValueError("Unable to generate a unique gateway nickname.")

    def _ensure_unique_label(
        self,
        connection: sqlite3.Connection,
        label: str,
        *,
        gateway_id: str | None = None,
    ) -> None:
        row = connection.execute(
            """
            SELECT gateway_id
            FROM gateways
            WHERE lower(label) = lower(?) AND gateway_id != ?
            LIMIT 1
            """,
            (label, str(gateway_id or "")),
        ).fetchone()
        if row is not None:
            raise ValueError("Gateway nickname must be unique.")

    def repair_nicknames(self) -> int:
        """Repair old gateway rows whose labels are missing, IP-like, URL-like, or duplicated."""

        repaired = 0
        used_keys: set[str] = set()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT gateway_id, label, node
                FROM gateways
                ORDER BY created_at, gateway_id
                """
            ).fetchall()
            for row in rows:
                gateway_id = str(row["gateway_id"] or "").strip()
                current = _collapse_spaces(str(row["label"] or ""))
                current_key = _gateway_label_key(current)
                current_valid = self._is_valid_label(current) and current_key not in used_keys
                if current_valid:
                    used_keys.add(current_key)
                    if current != str(row["label"] or ""):
                        connection.execute(
                            "UPDATE gateways SET label = ?, updated_at = ? WHERE gateway_id = ?",
                            (current, _utc_now(), gateway_id),
                        )
                        repaired += 1
                    continue
                repaired_label = self._unique_label_from_candidates(
                    candidates=[str(row["node"] or ""), current],
                    gateway_id=gateway_id,
                    used_keys=used_keys,
                )
                connection.execute(
                    "UPDATE gateways SET label = ?, updated_at = ? WHERE gateway_id = ?",
                    (repaired_label, _utc_now(), gateway_id),
                )
                repaired += 1
        return repaired

    def seed_from_config(
        self,
        *,
        default_node: str,
        gateway_url: str | None,
        gateway_endpoints: dict[str, str] | None = None,
        terminal_cwd: str | None = None,
    ) -> None:
        endpoints = dict(gateway_endpoints or {})
        normalized_default_node = str(default_node or "localhost").strip() or "localhost"
        if gateway_url and normalized_default_node not in endpoints:
            endpoints[normalized_default_node] = str(gateway_url)
        if not endpoints:
            endpoints[normalized_default_node] = "http://127.0.0.1:8787"
        for node, url in endpoints.items():
            base_url = _normalize_base_url(url)
            if not base_url:
                continue
            host, port = _host_port_from_base_url(base_url)
            if not host:
                continue
            scheme = _normalize_scheme(urlsplit(base_url).scheme)
            gateway_id = self._id_from_node(node)
            now = _utc_now()
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT gateway_id FROM gateways WHERE gateway_id = ?",
                    (gateway_id,),
                ).fetchone()
                if existing is not None:
                    continue
                label = self._unique_label_from_candidates(
                    candidates=[str(node or "")],
                    gateway_id=gateway_id,
                    used_keys={
                        _gateway_label_key(str(row["label"] or ""))
                        for row in connection.execute("SELECT label FROM gateways").fetchall()
                    },
                )
                connection.execute(
                    """
                    INSERT INTO gateways (
                        gateway_id, label, scheme, host, port, base_url, node, terminal_cwd,
                        platform, platform_label, platform_version, architecture, shell,
                        command_profile, capability_tags_json, enabled, status, last_checked_at,
                        last_error, created_at, updated_at, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 'Unknown', '', '', '', '', '[]',
                        1, 'unknown', '', '', ?, ?, 'config')
                    """,
                    (
                        gateway_id,
                        label,
                        scheme,
                        host,
                        port,
                        base_url,
                        str(node or ""),
                        str(terminal_cwd or "").strip(),
                        now,
                        now,
                    ),
                )
        self.repair_nicknames()

    def list(self, *, include_disabled: bool = True) -> list[AgentGatewayRecord]:
        query = "SELECT * FROM gateways"
        params: tuple[Any, ...] = ()
        if not include_disabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY enabled DESC, lower(label), gateway_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get(self, gateway_id: str) -> AgentGatewayRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM gateways WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def get_default(self) -> AgentGatewayRecord | None:
        enabled = self.list(include_disabled=False)
        return enabled[0] if enabled else None

    def find_by_node(self, node: str) -> AgentGatewayRecord | None:
        normalized_node = str(node or "").strip()
        if not normalized_node:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM gateways WHERE node = ? AND enabled = 1 ORDER BY source = 'config' DESC LIMIT 1",
                (normalized_node,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def create(
        self,
        *,
        label: str,
        scheme: str,
        host: str,
        port: int,
        node: str = "",
        terminal_cwd: str = "",
        enabled: bool = True,
    ) -> AgentGatewayRecord:
        normalized_scheme = _normalize_scheme(scheme)
        normalized_host = str(host or "").strip().strip("/")
        if "://" in normalized_host:
            parsed = urlsplit(normalized_host)
            normalized_scheme = _normalize_scheme(parsed.scheme)
            normalized_host = str(parsed.hostname or "").strip()
            if parsed.port:
                port = parsed.port
        normalized_port = max(1, min(65535, int(port or (443 if normalized_scheme == "https" else 80))))
        if not normalized_host:
            raise ValueError("Gateway host is required.")
        normalized_label = _normalize_gateway_label(label)
        base_url = f"{normalized_scheme}://{normalized_host}:{normalized_port}"
        gateway_id = f"gw-{uuid4().hex[:12]}"
        now = _utc_now()
        record = AgentGatewayRecord(
            gateway_id=gateway_id,
            label=normalized_label,
            scheme=normalized_scheme,
            host=normalized_host,
            port=normalized_port,
            base_url=base_url,
            node=str(node or "").strip(),
            terminal_cwd=str(terminal_cwd or "").strip(),
            enabled=bool(enabled),
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            self._ensure_unique_label(connection, record.label)
            connection.execute(
                """
                INSERT INTO gateways (
                    gateway_id, label, scheme, host, port, base_url, node, terminal_cwd,
                    platform, platform_label, platform_version, architecture, shell,
                    command_profile, capability_tags_json, enabled, status, last_checked_at,
                    last_error, created_at, updated_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.gateway_id,
                    record.label,
                    record.scheme,
                    record.host,
                    record.port,
                    record.base_url,
                    record.node,
                    record.terminal_cwd,
                    record.platform,
                    record.platform_label,
                    record.platform_version,
                    record.architecture,
                    record.shell,
                    record.command_profile,
                    json.dumps(record.capability_tags),
                    1 if record.enabled else 0,
                    record.status,
                    record.last_checked_at,
                    record.last_error,
                    record.created_at,
                    record.updated_at,
                    record.source,
                ),
            )
        return record

    def update(self, gateway_id: str, updates: dict[str, Any]) -> AgentGatewayRecord | None:
        current = self.get(gateway_id)
        if current is None:
            return None
        payload = current.model_dump()
        allowed = {"label", "scheme", "host", "port", "node", "terminal_cwd", "enabled"}
        for key in allowed:
            if key in updates:
                payload[key] = updates[key]
        payload["scheme"] = _normalize_scheme(payload.get("scheme"))
        payload["host"] = str(payload.get("host") or "").strip().strip("/")
        payload["port"] = max(1, min(65535, int(payload.get("port") or 80)))
        if not payload["host"]:
            raise ValueError("Gateway host is required.")
        payload["base_url"] = f"{payload['scheme']}://{payload['host']}:{payload['port']}"
        payload["label"] = _normalize_gateway_label(payload.get("label"))
        payload["node"] = str(payload.get("node") or "").strip()
        payload["terminal_cwd"] = str(payload.get("terminal_cwd") or "").strip()
        payload["enabled"] = bool(payload.get("enabled"))
        payload["updated_at"] = _utc_now()
        record = AgentGatewayRecord.model_validate(payload)
        with self._connect() as connection:
            self._ensure_unique_label(connection, record.label, gateway_id=record.gateway_id)
            connection.execute(
                """
                UPDATE gateways
                SET label = ?, scheme = ?, host = ?, port = ?, base_url = ?, node = ?, terminal_cwd = ?,
                    enabled = ?, updated_at = ?
                WHERE gateway_id = ?
                """,
                (
                    record.label,
                    record.scheme,
                    record.host,
                    record.port,
                    record.base_url,
                    record.node,
                    record.terminal_cwd,
                    1 if record.enabled else 0,
                    record.updated_at,
                    record.gateway_id,
                ),
            )
        return self.get(record.gateway_id)

    def delete(self, gateway_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM gateways WHERE gateway_id = ?",
                (str(gateway_id or "").strip(),),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _normalize_platform(value: Any) -> str:
        platform = str(value or "unknown").strip().lower()
        return platform if platform in {"linux", "macos", "windows"} else "unknown"

    @staticmethod
    def _platform_label(platform: str, raw_label: Any = "") -> str:
        label = str(raw_label or "").strip()
        if label:
            return label
        return {"linux": "Linux", "macos": "macOS", "windows": "Windows"}.get(platform, "Unknown")

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _discovery_payload(
        self,
        record: AgentGatewayRecord,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        request = urllib_request.Request(f"{record.base_url}/capabilities", method="GET")
        with urllib_request.urlopen(request, timeout=max(0.25, float(timeout_seconds))) as response:
            raw_body = response.read().decode("utf-8")
        body = json.loads(raw_body)
        if not isinstance(body, dict):
            raise ValueError("Gateway returned a non-object capabilities payload.")
        platform = self._normalize_platform(body.get("platform"))
        return {
            "node": str(body.get("node") or record.node or "").strip(),
            "status": "connected",
            "platform": platform,
            "platform_label": self._platform_label(platform, body.get("platform_label")),
            "platform_version": str(body.get("platform_version") or "").strip(),
            "architecture": str(body.get("architecture") or "").strip(),
            "shell": str(body.get("shell") or "").strip(),
            "command_profile": str(body.get("command_profile") or "").strip(),
            "capability_tags": self._string_list(body.get("capability_tags")),
        }

    def _legacy_health_payload(
        self,
        record: AgentGatewayRecord,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        request = urllib_request.Request(f"{record.base_url}/healthz", method="GET")
        with urllib_request.urlopen(request, timeout=max(0.25, float(timeout_seconds))) as response:
            raw_body = response.read().decode("utf-8")
        body = json.loads(raw_body)
        if not isinstance(body, dict):
            raise ValueError("Gateway returned a non-object health payload.")
        return {
            "node": str(body.get("node") or record.node or "").strip(),
            "status": "connected" if str(body.get("status") or "").lower() == "ok" else "degraded",
            "platform": "unknown",
            "platform_label": "Unknown",
            "platform_version": "",
            "architecture": "",
            "shell": "",
            "command_profile": "",
            "capability_tags": [],
        }

    def health_check(self, gateway_id: str, *, timeout_seconds: float = 5.0) -> AgentGatewayRecord | None:
        record = self.get(gateway_id)
        if record is None:
            return None
        status = "offline"
        last_error = ""
        node = record.node
        platform = self._normalize_platform(record.platform)
        platform_label = self._platform_label(platform, record.platform_label)
        platform_version = record.platform_version
        architecture = record.architecture
        shell = record.shell
        command_profile = record.command_profile
        capability_tags = list(record.capability_tags)
        try:
            try:
                payload = self._discovery_payload(record, timeout_seconds=timeout_seconds)
            except urllib_error.HTTPError as exc:
                if exc.code not in {404, 405}:
                    raise
                payload = self._legacy_health_payload(record, timeout_seconds=timeout_seconds)
            node = str(payload.get("node") or node or "").strip()
            status = str(payload.get("status") or "connected").strip() or "connected"
            platform = self._normalize_platform(payload.get("platform"))
            platform_label = self._platform_label(platform, payload.get("platform_label"))
            platform_version = str(payload.get("platform_version") or "").strip()
            architecture = str(payload.get("architecture") or "").strip()
            shell = str(payload.get("shell") or "").strip()
            command_profile = str(payload.get("command_profile") or "").strip()
            capability_tags = self._string_list(payload.get("capability_tags"))
        except urllib_error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
        except (urllib_error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE gateways
                SET status = ?, node = ?, platform = ?, platform_label = ?, platform_version = ?,
                    architecture = ?, shell = ?, command_profile = ?, capability_tags_json = ?,
                    last_checked_at = ?, last_error = ?, updated_at = ?
                WHERE gateway_id = ?
                """,
                (
                    status,
                    node,
                    platform,
                    platform_label,
                    platform_version,
                    architecture,
                    shell,
                    command_profile,
                    json.dumps(capability_tags),
                    now,
                    last_error,
                    now,
                    record.gateway_id,
                ),
            )
        return self.get(record.gateway_id)
