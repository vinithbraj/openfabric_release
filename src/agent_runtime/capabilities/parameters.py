"""Agent-visible runtime capabilities for the local Parameter Store."""

from __future__ import annotations

from typing import Any

from agent_runtime.capabilities.base import BaseCapability
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.core.types import ExecutionResult
from agent_runtime.parameters import (
    AgentParameterCreate,
    AgentParameterStore,
    AgentParameterUpdate,
    masked_parameter_summary,
)


def _context_payload(context: dict[str, Any]) -> dict[str, Any]:
    nested = context.get("execution_context")
    return nested if isinstance(nested, dict) else {}


def _parameter_store_from_context(context: dict[str, Any]) -> AgentParameterStore | None:
    candidate = context.get("parameter_store")
    if isinstance(candidate, AgentParameterStore):
        return candidate
    nested = _context_payload(context)
    candidate = nested.get("parameter_store")
    if isinstance(candidate, AgentParameterStore):
        return candidate
    return None


def _node_id(context: dict[str, Any]) -> str:
    return str(context.get("node_id") or "")


def _success(context: dict[str, Any], payload: dict[str, Any], *, data_type: str = "summary") -> ExecutionResult:
    return ExecutionResult(
        node_id=_node_id(context),
        status="success",
        data_preview=payload,
        metadata={"data_type": data_type},
    )


def _error(context: dict[str, Any], message: str) -> ExecutionResult:
    return ExecutionResult(
        node_id=_node_id(context),
        status="error",
        error=message,
        data_preview={"message": message},
        metadata={"data_type": "summary"},
    )


class RuntimeInspectParameterStoreCapability(BaseCapability):
    """Read parameter-store metadata and masked summaries."""

    manifest = CapabilityManifest(
        capability_id="runtime.inspect_parameter_store",
        domain="runtime",
        operation_id="inspect_parameter_store",
        name="Inspect Parameter Store",
        description="List, search, or inspect local Agent Parameter Store entries without revealing raw values.",
        semantic_verbs=["read", "list", "search", "summarize"],
        object_types=["parameter_store", "parameter", "credential", "connection_details"],
        semantic_tags=["parameters", "credentials", "local_store"],
        argument_schema={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list", "get", "search"]},
                "key": {"type": "string"},
                "query": {"type": "string"},
                "include_audit": {"type": "boolean"},
            },
        },
        required_arguments=[],
        optional_arguments=["operation", "key", "query", "include_audit"],
        output_schema={"type": "object"},
        output_object_types=["parameter"],
        output_fields=["parameters", "count", "audit_events"],
        execution_backend="internal",
        backend_operation="runtime.inspect_parameter_store",
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[
            {"prompt": "show saved DICOM DB parameter summaries"},
            {"prompt": "list my parameter store keys"},
            {"prompt": "what fields are available for parameter dicom_db?"},
        ],
        safety_notes=["Returns masked values and environment variable names only."],
    )

    def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> ExecutionResult:
        args = self.validate_arguments(arguments)
        store = _parameter_store_from_context(context)
        if store is None:
            return _error(context, "Parameter store is not available in this runtime.")
        key = str(args.get("key") or "").strip()
        query = str(args.get("query") or "").strip()
        operation = str(args.get("operation") or ("get" if key else "list")).strip().lower()
        try:
            if operation == "get" and key:
                payload = store.inspect_payload(key=key, include_audit=bool(args.get("include_audit", False)))
            else:
                payload = store.inspect_payload(query=query, include_audit=False)
        except Exception as exc:
            return _error(context, str(exc))
        data_type = "table" if len(payload.get("parameters") or []) != 1 else "summary"
        return _success(context, payload, data_type=data_type)


class RuntimeManageParameterStoreCapability(BaseCapability):
    """Create, update, or delete parameter-store entries after confirmation."""

    manifest = CapabilityManifest(
        capability_id="runtime.manage_parameter_store",
        domain="runtime",
        operation_id="manage_parameter_store",
        name="Manage Parameter Store",
        description="Create, update, or delete local Agent Parameter Store entries from chat after user confirmation.",
        semantic_verbs=["create", "update", "delete"],
        object_types=["parameter_store", "parameter", "credential", "connection_details"],
        semantic_tags=["parameters", "credentials", "local_store", "confirmation_required"],
        argument_schema={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["create", "update", "delete"]},
                "key": {"type": "string"},
                "value_json": {"type": "object"},
                "context_json": {"type": "object"},
                "description": {"type": "string"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "sensitive": {"type": "boolean"},
            },
        },
        required_arguments=["operation", "key"],
        optional_arguments=["value_json", "context_json", "description", "aliases", "tags", "sensitive"],
        output_schema={"type": "object"},
        output_object_types=["parameter"],
        output_fields=["parameter", "deleted", "message"],
        execution_backend="internal",
        backend_operation="runtime.manage_parameter_store",
        risk_level="medium",
        read_only=False,
        mutates_state=True,
        requires_confirmation=True,
        examples=[
            {"prompt": "store this DICOM DB connection info as dicom_db"},
            {"prompt": "update parameter dicom_db with the new host"},
            {"prompt": "delete parameter old_token"},
        ],
        safety_notes=[
            "Mutations require existing approval policy.",
            "Responses mask values; raw values are not returned in capability output.",
        ],
    )

    def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> ExecutionResult:
        args = self.validate_arguments(arguments)
        store = _parameter_store_from_context(context)
        if store is None:
            return _error(context, "Parameter store is not available in this runtime.")
        operation = str(args.get("operation") or "").strip().lower()
        key = str(args.get("key") or "").strip()
        try:
            if operation == "delete":
                deleted = store.delete(key, actor="agent")
                return _success(
                    context,
                    {
                        "deleted": deleted,
                        "key": key,
                        "message": "Parameter deleted." if deleted else "Parameter was not found.",
                    },
                )
            if operation == "update":
                payload = AgentParameterUpdate.model_validate(
                    {
                        field: args[field]
                        for field in (
                            "key",
                            "value_json",
                            "context_json",
                            "description",
                            "aliases",
                            "tags",
                            "sensitive",
                        )
                        if field in args
                    }
                )
                record = store.update(key, payload, actor="agent")
                if record is None:
                    return _error(context, "Parameter was not found.")
                return _success(
                    context,
                    {
                        "parameter": masked_parameter_summary(record).model_dump(mode="json"),
                        "message": "Parameter updated.",
                    },
                )
            value_json = args.get("value_json")
            if not isinstance(value_json, dict):
                return _error(context, "value_json must be a non-empty JSON object for create.")
            payload = AgentParameterCreate.model_validate(
                {
                    "key": key,
                    "value_json": value_json,
                    "context_json": args.get("context_json") if isinstance(args.get("context_json"), dict) else {},
                    "description": args.get("description") or "",
                    "aliases": args.get("aliases") or [],
                    "tags": args.get("tags") or [],
                    "sensitive": bool(args.get("sensitive", True)),
                }
            )
            record = store.create(payload, actor="agent")
            return _success(
                context,
                {
                    "parameter": masked_parameter_summary(record).model_dump(mode="json"),
                    "message": "Parameter created.",
                },
            )
        except Exception as exc:
            return _error(context, str(exc))
