"""Confirmation payload helpers for AgentRuntime."""

from __future__ import annotations

from .common import *
from .formatting import *


class _ConfirmationMixin:
    """Confirmation payload helpers for AgentRuntime."""

    @staticmethod
    def _safe_confirmation_argument_value(value: Any) -> Any:
        """Return a compact, non-sensitive preview for one confirmation-gated argument."""

        if isinstance(value, InputRef):
            source = str(value.source_node_id)
            if value.output_key:
                return f"upstream data from {source}.{value.output_key}"
            return f"upstream data from {source}"
        if isinstance(value, dict):
            if "source_node_id" in value:
                source = str(value.get("source_node_id") or "upstream")
                output_key = value.get("output_key")
                return (
                    f"upstream data from {source}.{output_key}"
                    if output_key
                    else f"upstream data from {source}"
                )
            return f"<object with {len(value)} fields>"
        if isinstance(value, list):
            return f"<list with {len(value)} items>"
        if isinstance(value, str):
            trimmed = value.strip()
            if len(trimmed) > 120:
                return f"<{len(trimmed)} chars>"
            return trimmed
        return value

    @staticmethod
    def _safe_confirmation_text_preview(value: Any, max_chars: int = 1200) -> str:
        """Return a bounded approval-time preview for LLM-authored command/code text."""

        text = str(value or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n...[truncated]"

    @staticmethod
    def _safe_confirmation_input_bindings(value: Any) -> list[dict[str, Any]]:
        """Return non-sensitive upstream-flow summaries for operator input bindings."""

        if not isinstance(value, list):
            return []
        bindings: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            input_name = str(item.get("input_name") or "").strip()
            source_action_id = str(item.get("source_action_id") or "").strip()
            source_field = str(item.get("source_field") or "stdout").strip()
            if not input_name or not source_action_id:
                continue
            bindings.append(
                {
                    "input_name": input_name,
                    "source_action_id": source_action_id,
                    "source_field": source_field,
                    "required": bool(item.get("required", True)),
                }
            )
        return bindings

    @classmethod
    def _safe_confirmation_arguments(cls, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return one user-safe confirmation preview of action arguments."""

        sensitive_keys = {
            "content",
            "query",
            "query_intent",
            "stdin",
            "stdout",
            "stderr",
        }
        sanitized: dict[str, Any] = {}
        for key, value in dict(arguments or {}).items():
            lowered = str(key).strip().lower()
            if lowered == "input_bindings":
                sanitized[key] = cls._safe_confirmation_input_bindings(value)
                continue
            if lowered in {"command", "code"}:
                sanitized[key] = cls._safe_confirmation_text_preview(value)
                continue
            if lowered in sensitive_keys:
                sanitized[key] = "<omitted>"
                continue
            sanitized[key] = cls._safe_confirmation_argument_value(value)
        return sanitized

    @classmethod
    def _confirmation_actions_for_dag(cls, dag: ActionDAG) -> list[dict[str, Any]]:
        """Return safe summaries of confirmation-gated nodes in one DAG."""

        actions: list[dict[str, Any]] = []
        for node in dag.nodes:
            labels = {label.strip().lower() for label in node.safety_labels}
            if "requires-confirmation" not in labels:
                continue
            actions.append(
                {
                    "node_id": node.id,
                    "task_id": node.task_id,
                    "capability_id": node.capability_id,
                    "operation_id": node.operation_id,
                    "semantic_verb": node.semantic_verb,
                    "description": node.description,
                    "arguments": cls._safe_confirmation_arguments(node.arguments),
                }
            )
        return actions
