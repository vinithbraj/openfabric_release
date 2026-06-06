"""SQL discovery runtime context, gateway execution, and observability helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.discovery_support.common import *
from agent_runtime.capabilities.sql_support.discovery_support.sql_text import *

def _node_id(context: dict[str, Any]) -> str:
    return str(context.get("node_id") or "")


def _success(context: dict[str, Any], payload: dict[str, Any]) -> ExecutionResult:
    return ExecutionResult(
        node_id=_node_id(context),
        status="success" if payload.get("status") == "success" else "error",
        data_preview=payload,
        error=str(payload.get("error") or "") or None,
        metadata={"data_type": "sql_result", "engine": payload.get("engine") or "postgresql"},
    )


def _context_payload(context: dict[str, Any]) -> dict[str, Any]:
    nested = context.get("execution_context")
    return nested if isinstance(nested, dict) else {}


def _config_from_context(context: dict[str, Any]) -> RuntimeConfig:
    config = context.get("config")
    if isinstance(config, RuntimeConfig):
        return config
    nested = _context_payload(context)
    config = nested.get("config")
    if isinstance(config, RuntimeConfig):
        return config
    return RuntimeConfig()


def _llm_from_context(context: dict[str, Any]) -> Any:
    nested = _context_payload(context)
    return context.get("llm_client") or nested.get("llm_client")


def _gateway_from_context(context: dict[str, Any]) -> GatewayClient:
    config = _config_from_context(context)
    gateway = context.get("gateway_client")
    if type(gateway) is GatewayClient:
        return GatewayClient(config)
    if hasattr(gateway, "execute_raw_command"):
        return gateway
    nested = _context_payload(context)
    gateway = nested.get("gateway_client")
    if type(gateway) is GatewayClient:
        return GatewayClient(config)
    if hasattr(gateway, "execute_raw_command"):
        return gateway
    return GatewayClient(config)


def _memory_store_from_context(context: dict[str, Any]) -> AgentMemoryStore | None:
    candidate = context.get("memory_store")
    if isinstance(candidate, AgentMemoryStore):
        return candidate
    nested = _context_payload(context)
    candidate = nested.get("memory_store")
    if isinstance(candidate, AgentMemoryStore):
        return candidate
    return None


def _schema_cache_from_context(context: dict[str, Any]) -> dict[str, Any]:
    cache = context.setdefault(SQL_SCHEMA_CACHE_CONTEXT_KEY, {})
    if isinstance(cache, dict):
        return cache
    context[SQL_SCHEMA_CACHE_CONTEXT_KEY] = {}
    return context[SQL_SCHEMA_CACHE_CONTEXT_KEY]


def _sql_gateway_execution_context(
    context: dict[str, Any],
    *,
    shell_env: dict[str, str],
) -> dict[str, Any]:
    """Return the minimal execution context needed for gateway-routed SQL commands."""

    execution_context: dict[str, Any] = {}
    for key in _SQL_GATEWAY_CONTEXT_KEYS:
        if key not in context:
            continue
        value = context.get(key)
        if value in (None, ""):
            continue
        if key == "gateway_endpoints" and not isinstance(value, dict):
            continue
        execution_context[key] = value
    execution_context["shell_env"] = dict(shell_env)
    execution_context[SQL_GATEWAY_REQUIRED_CONTEXT_KEY] = True
    execution_context[SQL_EXECUTION_SOURCE_CONTEXT_KEY] = "sql_agent"
    return execution_context


def _sql_command_stream_id(
    *,
    operation: str,
    profile_label: str,
    sql: str,
) -> str:
    """Return a per-execution stream id for SQL chat output capsules."""

    fingerprint = hashlib.sha1(_compact_sql(sql).encode("utf-8")).hexdigest()[:12]
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    safe_operation = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(operation or "query"))[:48]
    safe_profile = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(profile_label or "profile"))[
        :80
    ]
    return f"sql:{safe_operation or 'query'}:{safe_profile or 'profile'}:{fingerprint}:{stamp}"


def _sql_event_title(channel: str) -> str:
    if channel == "started":
        return "SQL started"
    if channel in {"stdout", "stderr"}:
        return "SQL output"
    if channel == "completed":
        return "SQL completed"
    if channel == "cancelled":
        return "SQL cancelled"
    return "SQL error"


def _sql_event_summary(channel: str) -> str:
    if channel == "started":
        return "A SQL command is executing through the selected gateway."
    if channel == "completed":
        return "The selected gateway completed a SQL command."
    if channel == "cancelled":
        return "The selected gateway cancelled a SQL command."
    if channel == "error":
        return "The selected gateway reported a SQL execution error."
    return "A SQL gateway stream event was received."


def _emit_sql_command_event(
    context: dict[str, Any],
    *,
    stream_id: str,
    channel: str,
    operation: str,
    profile_label: str,
    sql: str,
    generated_sql: str = "",
    executed_sql: str = "",
    cwd: str,
    text: str = "",
    exit_code: int | None = None,
    gateway_metadata: dict[str, Any] | None = None,
) -> None:
    """Emit a shell/Python-compatible command event for SQL chat capsules."""

    observability = observability_from_context(context)
    if observability is None:
        return
    metadata = merge_gateway_metadata(context, gateway_metadata or {})
    details = {
        **metadata,
        "command_stream_id": stream_id,
        "capability_id": "sql.query",
        "operation_id": operation or "query",
        "command_kind": "sql",
        "language": "sql",
        "mode": "sql",
        "source": "sql_agent",
        "label": f"SQL {operation or 'query'} ({profile_label or 'profile'})",
        "db_profile": profile_label,
        "channel": channel,
        "text": text,
        "exit_code": exit_code,
        "cwd": cwd,
        "command": executed_sql or sql,
        "generated_sql": generated_sql or sql,
        "executed_sql": executed_sql or sql,
    }
    emitter = observability.error if channel in {"error", "cancelled"} else observability.info
    emitter(
        STAGE_EXECUTION,
        f"execution.command.{channel}",
        _sql_event_title(channel),
        _sql_event_summary(channel),
        details=details,
    )


def _execute_gateway_sql_command(
    *,
    gateway_client: Any,
    command: str,
    cwd: str,
    execution_context: dict[str, Any],
    observability_context: dict[str, Any],
    sql: str,
    profile_label: str,
    operation: str,
    secrets: list[Any],
    generated_sql: str = "",
    executed_sql: str = "",
) -> dict[str, Any]:
    """Execute a gateway command and mirror SQL output into command SSE events."""

    stream_id = _sql_command_stream_id(
        operation=operation,
        profile_label=profile_label,
        sql=sql,
    )
    gateway_metadata = merge_gateway_metadata(observability_context, execution_context)
    _emit_sql_command_event(
        observability_context,
        stream_id=stream_id,
        channel="started",
        operation=operation,
        profile_label=profile_label,
        sql=sql,
        generated_sql=generated_sql or sql,
        executed_sql=executed_sql or sql,
        cwd=cwd,
        gateway_metadata=gateway_metadata,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    exit_code = 1
    try:
        stream_raw_command = getattr(gateway_client, "stream_raw_command", None)
        if callable(stream_raw_command):
            try:
                completed_seen = False
                for chunk in stream_raw_command(
                    command=command,
                    cwd=cwd,
                    execution_context=execution_context,
                ):
                    chunk_type = str(chunk.get("type") or "")
                    gateway_metadata = merge_gateway_metadata(gateway_metadata, chunk)
                    raw_text = str(chunk.get("text") or "")
                    text = _sanitize_text(raw_text, secrets)
                    if chunk_type == "stdout":
                        stdout_parts.append(text)
                    elif chunk_type in {"stderr", "error"}:
                        stderr_parts.append(text)
                    elif chunk_type == "cancelled":
                        stderr_parts.append(text or "SQL command cancelled by user.\n")
                        exit_code = int(
                            chunk.get("exit_code")
                            if chunk.get("exit_code") is not None
                            else 130
                        )
                    elif chunk_type == "completed":
                        completed_seen = True
                        exit_code = int(
                            chunk.get("exit_code")
                            if chunk.get("exit_code") is not None
                            else 1
                        )
                    if chunk_type in {
                        "stdout",
                        "stderr",
                        "error",
                        "cancelled",
                        "completed",
                    }:
                        _emit_sql_command_event(
                            observability_context,
                            stream_id=stream_id,
                            channel=chunk_type,
                            operation=operation,
                            profile_label=profile_label,
                            sql=sql,
                            generated_sql=generated_sql or sql,
                            executed_sql=executed_sql or sql,
                            cwd=cwd,
                            text=text,
                            exit_code=(
                                int(chunk.get("exit_code"))
                                if chunk.get("exit_code") is not None
                                else None
                            ),
                            gateway_metadata=gateway_metadata,
                        )
                if not completed_seen:
                    exit_code = 0 if not stderr_parts else 1
                    _emit_sql_command_event(
                        observability_context,
                        stream_id=stream_id,
                        channel="completed",
                        operation=operation,
                        profile_label=profile_label,
                        sql=sql,
                        generated_sql=generated_sql or sql,
                        executed_sql=executed_sql or sql,
                        cwd=cwd,
                        exit_code=exit_code,
                        gateway_metadata=gateway_metadata,
                    )
                return {
                    "stdout": "".join(stdout_parts),
                    "stderr": "".join(stderr_parts),
                    "exit_code": exit_code,
                    **gateway_metadata,
                }
            except Exception:
                if stdout_parts or stderr_parts:
                    raise

        result = gateway_client.execute_raw_command(
            command=command,
            cwd=cwd,
            execution_context=execution_context,
        )
    except Exception as exc:
        text = _sanitize_text(str(exc), secrets)
        _emit_sql_command_event(
            observability_context,
            stream_id=stream_id,
            channel="error",
            operation=operation,
            profile_label=profile_label,
            sql=sql,
            generated_sql=generated_sql or sql,
            executed_sql=executed_sql or sql,
            cwd=cwd,
            text=text,
            exit_code=1,
            gateway_metadata=gateway_metadata,
        )
        return {"stdout": "", "stderr": text, "exit_code": 1, **gateway_metadata}

    gateway_metadata = merge_gateway_metadata(gateway_metadata, result)
    exit_code = int(result.get("exit_code") or 0)
    stdout = _sanitize_text(result.get("stdout") or "", secrets)
    stderr = _sanitize_text(result.get("stderr") or "", secrets)
    if stdout:
        _emit_sql_command_event(
            observability_context,
            stream_id=stream_id,
            channel="stdout",
            operation=operation,
            profile_label=profile_label,
            sql=sql,
            generated_sql=generated_sql or sql,
            executed_sql=executed_sql or sql,
            cwd=cwd,
            text=stdout,
            gateway_metadata=gateway_metadata,
        )
    if stderr:
        _emit_sql_command_event(
            observability_context,
            stream_id=stream_id,
            channel="stderr",
            operation=operation,
            profile_label=profile_label,
            sql=sql,
            generated_sql=generated_sql or sql,
            executed_sql=executed_sql or sql,
            cwd=cwd,
            text=stderr,
            gateway_metadata=gateway_metadata,
        )
    _emit_sql_command_event(
        observability_context,
        stream_id=stream_id,
        channel="completed",
        operation=operation,
        profile_label=profile_label,
        sql=sql,
        generated_sql=generated_sql or sql,
        executed_sql=executed_sql or sql,
        cwd=cwd,
        exit_code=exit_code,
        gateway_metadata=gateway_metadata,
    )
    return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code, **gateway_metadata}


def _emit_sql_agent_event(
    context: dict[str, Any],
    *,
    event_type: str,
    title: str,
    summary: str,
    details: dict[str, Any],
    level: str = "info",
    debug_only: bool = False,
) -> None:
    """Emit SQL-agent loop events without exposing connection credentials."""

    observability = observability_from_context(context)
    if observability is None:
        return
    emitter = observability.warning if level == "warning" else observability.info
    emitter(
        STAGE_EXECUTION,
        event_type,
        title,
        summary,
        details=details,
        debug_only=debug_only,
    )


def _schema_trace_details(
    *,
    parameter_key: str,
    schema: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Return safe, credential-free SQL context details for observability."""

    relation = (
        schema.get("relation_foreign_scheme")
        if isinstance(schema.get("relation_foreign_scheme"), dict)
        else {}
    )
    domain_context = (
        schema.get("domain_context_summary")
        if isinstance(schema.get("domain_context_summary"), dict)
        else parameter_context_summary(schema.get("context_json") if isinstance(schema.get("context_json"), dict) else {})
    )
    warning_values = [str(item) for item in warnings or [] if str(item).strip()]
    if "stored_schema" in warning_values:
        source = "parameter_store.schema_catalog"
    elif "schema_cache_hit" in warning_values:
        source = "session_schema_cache"
    else:
        source = "live_schema_discovery"
    tables = schema.get("tables") if isinstance(schema.get("tables"), list) else []
    schemas = schema.get("schemas") if isinstance(schema.get("schemas"), list) else []
    approved = relation.get("approved_foreign_keys") if isinstance(relation, dict) else []
    inferred = relation.get("inferred_secondary_keys") if isinstance(relation, dict) else []
    return {
        "parameter_key": parameter_key,
        "engine": "postgresql",
        "schema_source": source,
        "schema_cache_warnings": warning_values,
        "schema_count": len(schemas),
        "table_count": len(tables),
        "relation_foreign_scheme_present": bool(relation),
        "approved_foreign_key_count": len(approved) if isinstance(approved, list) else 0,
        "inferred_secondary_key_count": len(inferred) if isinstance(inferred, list) else 0,
        "domain_context_present": bool(domain_context),
        "domain_context_summary": domain_context,
    }


__all__ = [name for name in globals() if not name.startswith("__")]
