"""/discoverdb macro parsing, draft payloads, and discovery service."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.discovery_support.common import *
from agent_runtime.capabilities.sql_support.discovery_support.sql_text import *
from agent_runtime.capabilities.sql_support.discovery_support.runtime_context import *
from agent_runtime.capabilities.sql_support.discovery_support.schema_payloads import *

def _database_parameter_clarification(
    *,
    prompt: str,
    choices: list[dict[str, str]],
    summary: str,
) -> dict[str, Any]:
    options = [
        {
            "option_id": "sql_param:" + str(item.get("normalized_key") or item.get("key") or ""),
            "label": str(item.get("key") or item.get("normalized_key") or ""),
            "description": "Database Parameter Store profile",
        }
        for item in choices[:3]
    ]
    return {
        "status": "clarification_required",
        "parameter_key": "",
        "engine": "",
        "sql": "",
        "summary": summary,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "schema": {},
        "warnings": [],
        "error": "ambiguous_database_parameter",
        "parameter_choices": choices,
        "clarification_request": {
            "question": "Which database parameter should I use?",
            "reason": summary,
            "missing_information": "Database Parameter Store key",
            "options": options,
            "input_kind": "unknown",
            "parameter_choices": [],
            "secret_input": False,
            "allow_freeform": True,
            "confidence": 0.0,
        },
        "sql_pending_state": {
            "original_prompt": prompt,
            "parameter_key": "",
            "schema_cache_key": "",
            "unresolved_term": "database_parameter",
            "parameter_choices": choices,
        },
    }


def _normalize_discoverdb_engine(engine: str) -> str:
    text = str(engine or "").strip().lower()
    if text in {"postgres", "postgresql"}:
        return "postgresql"
    return text


def _parse_discoverdb_bool(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid_boolean: include_system must be true or false, not {value!r}.")


def _validate_database_discovery_request(
    request: DatabaseDiscoveryRequest,
) -> tuple[DatabaseDiscoveryRequest | None, str, str]:
    """Validate and normalize a /discoverdb request.

    Returns:
        ``(request, "", "")`` on success, otherwise ``(None, code, message)``.
    """

    engine = _normalize_discoverdb_engine(request.engine)
    if not engine:
        return None, "missing_engine", "Missing required engine. Use engine=postgres."
    if engine != "postgresql":
        return (
            None,
            "unsupported_engine",
            f"Unsupported database engine {request.engine!r}; v1 supports postgres.",
        )
    host = str(request.host or "").strip()
    if not host:
        return None, "missing_host", "Missing required host."
    if not _DISCOVERDB_HOST_RE.match(host):
        return None, "invalid_host", "Host must be a hostname or IP address without spaces."
    try:
        port = int(request.port or 0)
    except (TypeError, ValueError):
        return None, "invalid_port", "Port must be an integer between 1 and 65535."
    if port < 1 or port > 65535:
        return None, "invalid_port", "Port must be an integer between 1 and 65535."
    user = str(request.user or "").strip()
    if not user:
        return None, "missing_user", "Missing required user."
    password = str(request.password or "")
    if not password:
        return None, "missing_password", "Missing required password."
    maintenance_db = str(request.maintenance_db or "postgres").strip() or "postgres"
    sslmode = str(request.sslmode or "").strip().lower()
    if sslmode and sslmode not in _DISCOVERDB_SSLMODES:
        return None, "invalid_sslmode", f"Unsupported sslmode {sslmode!r}."
    return (
        request.model_copy(
            update={
                "engine": engine,
                "host": host,
                "port": port,
                "user": user,
                "password": password,
                "maintenance_db": maintenance_db,
                "sslmode": sslmode,
                "include_system": bool(request.include_system),
            }
        ),
        "",
        "",
    )


def parse_discoverdb_macro(prompt: str) -> DatabaseDiscoveryRequest | None:
    """Parse the deterministic /discoverdb key=value macro."""

    match = _DISCOVERDB_MACRO_RE.match(str(prompt or ""))
    if not match:
        return None
    try:
        tokens = shlex.split(str(match.group("body") or ""), posix=True)
    except ValueError as exc:
        raise ValueError(f"malformed_quotes: {exc}") from exc
    values: dict[str, Any] = {"prompt": str(prompt or "")}
    for token in tokens:
        if "=" not in token:
            raise ValueError(f"malformed_field: expected key=value near {token!r}.")
        key, value = token.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        if key not in _DISCOVERDB_ALLOWED_KEYS:
            raise ValueError(f"unsupported_field: {key!r} is not supported for /discoverdb.")
        if key == "include_system":
            values[key] = _parse_discoverdb_bool(value)
        else:
            values[key] = value
    request = DatabaseDiscoveryRequest.model_validate(values)
    normalized, code, message = _validate_database_discovery_request(request)
    if normalized is None:
        raise ValueError(f"{code}: {message}")
    return normalized


def _database_discovery_error(
    code: str,
    message: str,
    *,
    engine: str = "",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "error",
        "engine": engine,
        "discovered": [],
        "skipped_existing": [],
        "created": [],
        "updated": [],
        "errors": [str(message or code)],
        "warnings": list(warnings or []),
        "error": str(code or "database_discovery_failed"),
    }


def _transient_parameter_record(payload: AgentParameterCreate) -> AgentParameterRecord:
    now = datetime.now(UTC).isoformat()
    return AgentParameterRecord(
        key=payload.key,
        normalized_key=normalize_parameter_key(payload.key),
        value_json=dict(payload.value_json),
        context_json=dict(payload.context_json),
        description=payload.description,
        aliases=list(payload.aliases),
        tags=list(payload.tags),
        sensitive=bool(payload.sensitive),
        created_at=now,
        updated_at=now,
    )


def _draft_preview_payload(
    payload: AgentParameterCreate,
    *,
    operation: str = "create",
    existing: AgentParameterRecord | None = None,
) -> dict[str, Any]:
    record = _transient_parameter_record(payload)
    result = {
        "operation": operation,
        "key": payload.key,
        "value_json": dict(payload.value_json),
        "context_json": dict(payload.context_json),
        "description": payload.description,
        "aliases": list(payload.aliases),
        "tags": list(payload.tags),
        "sensitive": bool(payload.sensitive),
        "summary": masked_parameter_summary(record).model_dump(mode="json"),
    }
    if existing is not None:
        result["existing_key"] = existing.key
        result["normalized_key"] = existing.normalized_key
        result["existing_summary"] = masked_parameter_summary(existing).model_dump(mode="json")
    return result


def _skipped_existing_payload(
    record: AgentParameterRecord,
    *,
    reason: str = "existing_key",
) -> dict[str, Any]:
    return {
        "key": record.key,
        "normalized_key": record.normalized_key,
        "reason": reason,
        "summary": masked_parameter_summary(record).model_dump(mode="json"),
    }


def _parameter_create_from_discovery_draft(item: dict[str, Any]) -> AgentParameterCreate:
    return AgentParameterCreate(
        key=str(item.get("key") or ""),
        value_json=dict(item.get("value_json") or item.get("value") or {}),
        context_json=dict(item.get("context_json") or {}),
        description=str(item.get("description") or "Discovered PostgreSQL database."),
        aliases=[str(value) for value in item.get("aliases") or [] if str(value).strip()],
        tags=[str(value) for value in item.get("tags") or [] if str(value).strip()],
        sensitive=bool(item.get("sensitive", True)),
    )


def _connection_value_json_from_discoverdb(
    request: DatabaseDiscoveryRequest,
    *,
    dbname: str,
) -> dict[str, Any]:
    value_json: dict[str, Any] = {
        "engine": "postgresql",
        "dbname": dbname,
        "host": request.host,
        "port": int(request.port or 0),
        "user": request.user,
        "password": request.password,
    }
    if request.sslmode:
        value_json["sslmode"] = request.sslmode
    return value_json


class SqlDatabaseDiscoveryService:
    """Discover databases on a PostgreSQL server and draft Parameter Store profiles."""

    def __init__(
        self,
        *,
        parameter_store: AgentParameterStore | None,
        gateway_client: Any,
        config: RuntimeConfig,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.parameter_store = parameter_store
        self.gateway_client = gateway_client
        self.config = config
        self.context = context if isinstance(context, dict) else {}

    def discover(self, request: DatabaseDiscoveryRequest) -> dict[str, Any]:
        normalized, code, message = _validate_database_discovery_request(request)
        if normalized is None:
            return _database_discovery_error(
                code,
                message,
                engine=_normalize_discoverdb_engine(request.engine),
            )

        shell_env = {
            "OF_DISCOVERDB_ENGINE": normalized.engine,
            "OF_DISCOVERDB_HOST": normalized.host,
            "OF_DISCOVERDB_PORT": str(normalized.port or ""),
            "OF_DISCOVERDB_USER": normalized.user,
            "OF_DISCOVERDB_PASSWORD": normalized.password,
            "OF_DISCOVERDB_MAINTENANCE_DB": normalized.maintenance_db,
            "OF_DISCOVERDB_QUERY": _DISCOVERDB_DATABASE_QUERY,
        }
        command_prefix = 'PGPASSWORD="$OF_DISCOVERDB_PASSWORD"'
        if normalized.sslmode:
            shell_env["OF_DISCOVERDB_SSLMODE"] = normalized.sslmode
            command_prefix += ' PGSSLMODE="$OF_DISCOVERDB_SSLMODE"'
        command = (
            f"{command_prefix} psql --no-psqlrc --csv -v ON_ERROR_STOP=1 "
            '-h "$OF_DISCOVERDB_HOST" -p "$OF_DISCOVERDB_PORT" '
            '-U "$OF_DISCOVERDB_USER" -d "$OF_DISCOVERDB_MAINTENANCE_DB" '
            '-c "$OF_DISCOVERDB_QUERY"'
        )
        secrets = [
            normalized.host,
            normalized.port,
            normalized.user,
            normalized.password,
            normalized.maintenance_db,
        ]
        result = _execute_gateway_sql_command(
            gateway_client=self.gateway_client,
            command=command,
            cwd=str(getattr(self.config, "workspace_root", ".") or "."),
            execution_context=_sql_gateway_execution_context(
                self.context,
                shell_env=shell_env,
            ),
            observability_context=self.context,
            sql=_DISCOVERDB_DATABASE_QUERY,
            profile_label="discoverdb",
            operation="database_discovery",
            secrets=secrets,
        )
        exit_code = int(result.get("exit_code") or 0)
        if exit_code != 0:
            stderr = _sanitize_text(result.get("stderr") or result.get("stdout") or "", secrets)
            return _database_discovery_error(
                "connection_failed",
                stderr or f"psql exited with code {exit_code}",
                engine=normalized.engine,
            )

        _columns, rows = _parse_csv(_sanitize_text(result.get("stdout") or "", secrets))
        discovered_names = [
            str(row.get("datname") or next(iter(row.values()), "") or "").strip()
            for row in rows
            if str(row.get("datname") or next(iter(row.values()), "") or "").strip()
        ]
        if not normalized.include_system:
            discovered_names = [
                name
                for name in discovered_names
                if name.strip().lower() not in _DISCOVERDB_SYSTEM_DBS
            ]

        warnings: list[str] = []
        discovered: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []
        skipped_existing: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        from agent_runtime.capabilities.sql_support.service import SqlAgentService

        schema_service = SqlAgentService(
            parameter_store=self.parameter_store,
            gateway_client=self.gateway_client,
            llm_client=None,
            memory_store=None,
            config=self.config,
            context=self.context,
        )
        for dbname in discovered_names:
            key = normalize_parameter_key(dbname)
            if not key:
                warnings.append(f"Skipped database with unsupported name: {dbname!r}")
                continue
            if key in seen_keys:
                warnings.append(f"Skipped duplicate normalized database key: {key}")
                continue
            seen_keys.add(key)
            existing = self.parameter_store.get(key) if self.parameter_store is not None else None
            value_json = _connection_value_json_from_discoverdb(normalized, dbname=dbname)
            transient = _transient_parameter_record(
                AgentParameterCreate(
                    key=key,
                    value_json=value_json,
                    description=f"PostgreSQL database discovered by /discoverdb: {dbname}",
                    aliases=_dedupe_strings([dbname, key]),
                    tags=["database", "postgresql", "discoverdb"],
                    sensitive=True,
                )
            )
            schema_result = schema_service.discover_schema(
                _ResolvedProfile(
                    transient,
                    {"profile_type": "database_connection", "engine": "postgresql"},
                    _iter_scalar_values(value_json),
                ),
                refresh=True,
            )
            value_json.update(
                _schema_value_json_from_schema_result(
                    schema_result,
                    parameter_key=key,
                )
            )
            if schema_result.get("status") != "success":
                warnings.append(
                    f"Schema discovery failed for {dbname}: "
                    f"{schema_result.get('error') or 'unknown error'}"
                )
            draft = AgentParameterCreate(
                key=key,
                value_json=value_json,
                context_json=existing.context_json if existing is not None else default_parameter_context_json(),
                description=f"PostgreSQL database discovered by /discoverdb: {dbname}",
                aliases=_dedupe_strings([dbname, key]),
                tags=["database", "postgresql", "discoverdb"],
                sensitive=True,
            )
            if existing is not None:
                discovered.append(
                    _draft_preview_payload(draft, operation="update", existing=existing)
                )
                updated.append(
                    _draft_preview_payload(draft, operation="update", existing=existing)
                )
            else:
                discovered.append(_draft_preview_payload(draft, operation="create"))

        if not discovered and not skipped_existing:
            warnings.append("No connectable databases were discovered.")
        return {
            "status": "preview",
            "engine": "postgresql",
            "discovered": discovered,
            "updated": updated,
            "skipped_existing": skipped_existing,
            "created": [],
            "errors": [],
            "warnings": warnings,
            "error": "",
        }

    def commit(self, request: DatabaseDiscoveryCommitRequest) -> dict[str, Any]:
        if self.parameter_store is None:
            return _database_discovery_error(
                "parameter_store_unavailable",
                "Parameter Store is not available.",
                engine="postgresql",
            )
        created: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []
        skipped_existing: list[dict[str, Any]] = []
        errors: list[str] = []
        for item in request.drafts:
            try:
                draft = _parameter_create_from_discovery_draft(item)
            except Exception as exc:
                errors.append(f"Invalid discovery draft: {exc}")
                continue
            existing = self.parameter_store.get(draft.key)
            if existing is not None:
                if str(item.get("operation") or "create").strip().lower() != "update":
                    skipped_existing.append(_skipped_existing_payload(existing))
                    continue
                try:
                    merged_value = _merge_discovery_update_value_json(
                        existing,
                        dict(draft.value_json),
                    )
                    record = self.parameter_store.update(
                        existing.normalized_key,
                        AgentParameterUpdate(
                            value_json=merged_value,
                            context_json=existing.context_json,
                            description=existing.description or draft.description,
                            aliases=_dedupe_strings([*existing.aliases, *draft.aliases]),
                            tags=_dedupe_strings([*existing.tags, *draft.tags]),
                            sensitive=True,
                        ),
                        actor="discoverdb",
                    )
                    if record is None:
                        errors.append(f"Failed to update {draft.key!r}: parameter not found")
                    else:
                        updated.append(masked_parameter_summary(record).model_dump(mode="json"))
                except Exception as exc:
                    errors.append(f"Failed to update {draft.key!r}: {exc}")
                continue
            try:
                record = self.parameter_store.create(draft, actor="discoverdb")
                created.append(masked_parameter_summary(record).model_dump(mode="json"))
            except Exception as exc:
                errors.append(f"Failed to save {draft.key!r}: {exc}")
        return {
            "status": "success" if not errors else ("partial_success" if created or updated else "error"),
            "engine": "postgresql",
            "discovered": [],
            "created": created,
            "updated": updated,
            "skipped_existing": skipped_existing,
            "errors": errors,
            "warnings": [],
            "error": "" if not errors else "commit_failed",
        }


__all__ = [name for name in globals() if not name.startswith("__")]
