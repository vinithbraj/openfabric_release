"""Runtime SQL capability wrappers."""

from __future__ import annotations

from agent_runtime.capabilities.sql_support.common import *
from agent_runtime.capabilities.sql_support.discovery import *
from agent_runtime.capabilities.sql_support.analysis import *
from agent_runtime.capabilities.sql_support.service import *

class RuntimeSqlDiscoverCapability(BaseCapability):
    """Discover database schema metadata."""

    manifest = CapabilityManifest(
        capability_id="sql.discover",
        domain="sql",
        operation_id="discover",
        name="Discover SQL Database",
        description=(
            "Discover PostgreSQL schemas, tables, and columns from a Parameter Store DB profile."
        ),
        semantic_verbs=["read", "list", "search", "summarize"],
        object_types=["sql.database", "database", "schema", "table"],
        semantic_tags=["sql", "database", "postgresql", "schema_discovery"],
        argument_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "parameter_key": {"type": "string"},
                "refresh_schema": {"type": "boolean"},
            },
        },
        required_arguments=[],
        optional_arguments=["prompt", "parameter_key", "refresh_schema"],
        output_schema={"type": "object"},
        output_object_types=["sql.schema"],
        output_fields=["schemas", "tables", "columns"],
        execution_backend="internal",
        backend_operation="sql.discover",
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[{"prompt": "list all schemas in canonical_v1"}],
        safety_notes=["Uses Parameter Store credentials execution-only; returns metadata only."],
    )

    def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> ExecutionResult:
        args = self.validate_arguments(arguments)
        payload = _service_from_context(context).run(
            prompt=str(args.get("prompt") or _context_payload(context).get("raw_prompt") or ""),
            parameter_key=str(args.get("parameter_key") or ""),
            operation="discover",
            refresh_schema=bool(args.get("refresh_schema", False)),
        )
        return _success(context, payload)


class RuntimeSqlQueryCapability(BaseCapability):
    """Ask the SQL LLM loop for SQL, then safely execute it."""

    manifest = CapabilityManifest(
        capability_id="sql.query",
        domain="sql",
        operation_id="query",
        name="NLP to SQL Query",
        description=(
            "Ask the SQL agent LLM loop to produce PostgreSQL SQL from a natural-language "
            "database request, then safely execute exact generated SQL."
        ),
        semantic_verbs=[
            "read",
            "list",
            "search",
            "summarize",
            "calculate",
            "count",
            "filter",
            "sort",
        ],
        object_types=["sql.database", "database", "table", "records"],
        semantic_tags=["sql", "database", "postgresql", "nlp_to_sql"],
        argument_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "parameter_key": {"type": "string"},
                "limit": {"type": "integer"},
                "refresh_schema": {"type": "boolean"},
            },
        },
        required_arguments=["prompt"],
        optional_arguments=["parameter_key", "limit", "refresh_schema"],
        output_schema={"type": "object"},
        output_object_types=["sql.result", "table"],
        output_fields=[
            "summary",
            "columns",
            "rows",
            "generated_sql",
            "executed_sql",
            "attempts",
            "safety_classification",
        ],
        execution_backend="internal",
        backend_operation="sql.query",
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[{"prompt": "show patient counts by schema in canonical_v1"}],
        safety_notes=[
            "Runtime discovers schema, validates SQL safety, executes read-only SQL, "
            "and pauses for confirmation before mutation."
        ],
    )

    def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> ExecutionResult:
        args = self.validate_arguments(arguments)
        payload = _service_from_context(context).run(
            prompt=str(args.get("prompt") or _context_payload(context).get("raw_prompt") or ""),
            parameter_key=str(args.get("parameter_key") or ""),
            operation="query",
            limit=args.get("limit"),
            refresh_schema=bool(args.get("refresh_schema", False)),
        )
        return _success(context, payload)

__all__ = [name for name in globals() if not name.startswith("__")]
