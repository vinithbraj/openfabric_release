"""Execution pipeline engine."""

from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime
from typing import Any

from agent_runtime.capabilities.registry import CapabilityRegistry
from agent_runtime.core.config import RuntimeConfig
from agent_runtime.core.errors import ValidationError
from agent_runtime.core.types import ActionDAG, ActionNode, ExecutionResult, InputRef, ResultBundle
from agent_runtime.execution.errors import ExecutionError
from agent_runtime.execution.gateway_client import GatewayClient
from agent_runtime.execution.gateway_metadata import merge_gateway_metadata
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.execution.safety import (
    SafetyDecision,
    SafetyPolicy,
    evaluate_dag_safety,
)
from agent_runtime.llm.reproducibility import hash_action_dag
from agent_runtime.operator.action_runtime import (
    operator_action_from_node,
    python_action_command,
    validate_operator_python_output,
)
from agent_runtime.operator.models import OperatorAction, OperatorPythonCodeProposal
from agent_runtime.observability import (
    EVENT_EXECUTION_NODE_COMPLETED,
    EVENT_EXECUTION_NODE_FAILED,
    EVENT_EXECUTION_NODE_STARTED,
    ObservabilityContext,
    STAGE_EXECUTION,
    observability_from_context,
)
from agent_runtime.prompts import prompt_lines


def _coerce_safety_policy(safety_policy: RuntimeConfig | SafetyPolicy | dict[str, Any] | None) -> SafetyPolicy:
    """Normalize execution safety input into a SafetyPolicy instance."""

    if isinstance(safety_policy, SafetyPolicy):
        return safety_policy
    if isinstance(safety_policy, RuntimeConfig):
        return SafetyPolicy(safety_policy)
    if isinstance(safety_policy, dict):
        return SafetyPolicy(RuntimeConfig.model_validate(safety_policy))
    return SafetyPolicy(RuntimeConfig())


def _cancel_requested(context: dict[str, Any] | None) -> bool:
    """Return whether the request-scoped cancellation token has been set."""

    token = dict(context or {}).get("cancel_event")
    return bool(getattr(token, "is_set", lambda: False)())


def _background_terminal_error_context(context: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(context or {})
    background_task = bool(
        str(payload.get("durable_task_id") or "").strip()
        or str(payload.get("scheduled_event_run_id") or "").strip()
        or str(payload.get("scheduled_event_id") or "").strip()
    )
    if not background_task:
        return {}
    background_terminal_error = str(
        payload.get("background_terminal_error")
        or payload.get("scheduled_event_background_terminal_error")
        or payload.get("durable_task_background_terminal_error")
        or "No background terminal session was attached to this background task."
    ).strip()
    return {
        "background_task": True,
        "background_terminal_unavailable": True,
        "background_terminal_error": background_terminal_error,
        "retryable_after_terminal_config": True,
    }


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, indent=2, default=str)
    except Exception:
        return str(value)


def _truncate(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars)] + "\n...[truncated]"


def _topological_sort(dag: ActionDAG) -> list[ActionNode]:
    """Return DAG nodes in dependency order while preserving stable input order."""

    node_by_id = {node.id: node for node in dag.nodes}
    adjacency: dict[str, set[str]] = {node.id: set() for node in dag.nodes}
    indegree: dict[str, int] = {node.id: 0 for node in dag.nodes}

    for node in dag.nodes:
        for dependency in node.depends_on:
            if node.id not in adjacency[dependency]:
                adjacency[dependency].add(node.id)
                indegree[node.id] += 1

    for source, target in dag.edges:
        if target not in adjacency[source]:
            adjacency[source].add(target)
            indegree[target] += 1

    stable_order = {node.id: index for index, node in enumerate(dag.nodes)}
    queue = deque(
        sorted(
            [node_id for node_id, degree in indegree.items() if degree == 0],
            key=lambda node_id: stable_order[node_id],
        )
    )
    ordered_ids: list[str] = []

    while queue:
        node_id = queue.popleft()
        ordered_ids.append(node_id)
        for child_id in sorted(adjacency[node_id], key=lambda item: stable_order[item]):
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                queue.append(child_id)

    if len(ordered_ids) != len(dag.nodes):
        raise ExecutionError("Unable to topologically sort DAG.")

    return [node_by_id[node_id] for node_id in ordered_ids]


def _dependency_ids(node: ActionNode, dag: ActionDAG) -> set[str]:
    """Return direct dependency ids for one node from both node and edge declarations."""

    dependencies = set(node.depends_on)
    dependencies.update(source for source, target in dag.edges if target == node.id)
    return dependencies


def _all_dependency_ids(node_id: str, dag: ActionDAG) -> set[str]:
    """Return the full transitive dependency chain for one node id."""

    parents_by_node: dict[str, set[str]] = {node.id: set(node.depends_on) for node in dag.nodes}
    for source, target in dag.edges:
        parents_by_node.setdefault(target, set()).add(source)

    ancestors: set[str] = set()
    frontier = list(parents_by_node.get(node_id, set()))
    while frontier:
        current = frontier.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        frontier.extend(parent for parent in parents_by_node.get(current, set()) if parent not in ancestors)
    return ancestors


def _iter_input_refs(value: Any) -> list[InputRef]:
    """Return all nested InputRef instances from one value."""

    if isinstance(value, InputRef):
        return [value]
    if isinstance(value, list):
        refs: list[InputRef] = []
        for item in value:
            refs.extend(_iter_input_refs(item))
        return refs
    if isinstance(value, dict):
        refs = []
        for item in value.values():
            refs.extend(_iter_input_refs(item))
        return refs
    return []


def _infer_data_type(result: ExecutionResult, node: ActionNode) -> str:
    """Infer a coarse data type for one successful execution payload."""

    if isinstance(result.metadata.get("data_type"), str) and result.metadata["data_type"].strip():
        return str(result.metadata["data_type"]).strip()

    preview = result.data_preview
    if isinstance(preview, dict):
        if any(key in preview for key in {"rows", "entries", "matches", "processes", "listeners"}):
            return "table"
        if any(key in preview for key in {"content_preview", "markdown", "stdout_lines", "stderr_lines"}):
            return "text"
        if "summary" in preview:
            return "summary"
        if "value" in preview:
            return "scalar"
        return "object"
    if isinstance(preview, list):
        return "list"
    return f"{node.capability_id}.output"


def _result_preview_details(result: ExecutionResult, node: ActionNode) -> dict[str, Any]:
    """Return a concise preview summary for one normalized result."""

    preview = result.data_preview
    details: dict[str, Any] = {
        "node_id": node.id,
        "capability_id": node.capability_id,
        "operation_id": node.operation_id,
        "data_type": result.data_ref.data_type if result.data_ref is not None else None,
    }
    if isinstance(preview, dict):
        details["keys"] = sorted(preview.keys())
        for key in ("rows", "entries", "matches", "processes", "listeners"):
            value = preview.get(key)
            if isinstance(value, list):
                details["row_count"] = len(value)
                break
        if "truncated" in preview:
            details["truncated"] = bool(preview.get("truncated"))
    return details


class ExecutionEngine:
    """Execute a typed DAG through registered capabilities."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        safety_policy: RuntimeConfig | SafetyPolicy | dict[str, Any] | None = None,
        result_store: InMemoryResultStore | None = None,
        gateway_client: GatewayClient | None = None,
    ) -> None:
        self.registry = registry
        self.safety_policy = _coerce_safety_policy(safety_policy)
        self.result_store = result_store or InMemoryResultStore()
        self.gateway_client = gateway_client or GatewayClient(self.safety_policy.config)
        self.python_executor = None

    def _safety_bundle(
        self,
        dag: ActionDAG,
        decision: SafetyDecision,
        safe_summary: str,
        metadata: dict[str, Any],
        status: str = "error",
    ) -> ResultBundle:
        """Return a normalized error bundle for blocked or gated execution."""

        return ResultBundle(
            dag_id=dag.dag_id,
            results=[],
            status=status,
            safe_summary=safe_summary,
            metadata={
                "blocked_reasons": list(decision.blocked_reasons),
                "warnings": list(decision.warnings),
                "requires_confirmation": decision.requires_confirmation,
                **metadata,
            },
        )

    def _normalize_result(
        self,
        node: ActionNode,
        raw_result: ExecutionResult,
    ) -> ExecutionResult:
        """Store large payloads by reference and emit only safe previews."""

        preview_source = raw_result.data_preview
        data_ref = raw_result.data_ref
        safe_preview = preview_source

        if preview_source is not None:
            safe_preview = self.result_store.preview(
                preview_source,
                self.safety_policy.config.max_output_preview_bytes,
            )
            data_ref = self.result_store.put(
                node.id,
                preview_source,
                _infer_data_type(raw_result, node),
                metadata={
                    "capability_id": node.capability_id,
                    "operation_id": node.operation_id,
                    **dict(raw_result.metadata),
                },
            )

        normalized_metadata = {
            "capability_id": node.capability_id,
            "operation_id": node.operation_id,
            **dict(raw_result.metadata),
        }
        normalized_metadata.setdefault("completed_at", datetime.now(UTC).isoformat())

        normalized = ExecutionResult(
            node_id=node.id,
            status=raw_result.status,
            data_ref=data_ref,
            data_preview=safe_preview,
            error=raw_result.error,
            metadata=normalized_metadata,
        )
        self.result_store.add(normalized)
        return normalized

    def _resolve_argument_value(
        self,
        value: Any,
        node: ActionNode,
        dag: ActionDAG,
        results_by_node: dict[str, ExecutionResult],
        observability: ObservabilityContext | None = None,
    ) -> Any:
        """Resolve nested InputRef values against completed dependencies."""

        if isinstance(value, InputRef):
            dependency_chain = _all_dependency_ids(node.id, dag)
            if value.source_node_id not in dependency_chain:
                raise ValidationError(
                    f"InputRef source {value.source_node_id} is not in the dependency chain for node {node.id}."
                )
            producer_result = results_by_node.get(value.source_node_id)
            if producer_result is None:
                raise ValidationError(
                    f"Producer output has not been executed yet for node {value.source_node_id}."
                )
            if producer_result.status != "success":
                raise ValidationError(
                    f"Producer node {value.source_node_id} did not succeed and cannot satisfy InputRef."
                )
            return self.result_store.resolve_input_ref(value)
        if isinstance(value, list):
            return [
                self._resolve_argument_value(item, node, dag, results_by_node, observability)
                for item in value
            ]
        if isinstance(value, dict):
            return {
                key: self._resolve_argument_value(item, node, dag, results_by_node, observability)
                for key, item in value.items()
            }
        return value

    def _resolve_node_arguments(
        self,
        node: ActionNode,
        dag: ActionDAG,
        results_by_node: dict[str, ExecutionResult],
        observability: ObservabilityContext | None = None,
    ) -> dict[str, Any]:
        """Resolve all typed input references for one node's arguments."""

        return {
            key: self._resolve_argument_value(value, node, dag, results_by_node, observability)
            for key, value in node.arguments.items()
        }

    @staticmethod
    def _operator_record_field(result: ExecutionResult, source_field: str) -> Any:
        """Return one operator field from a normalized upstream result."""

        preview = result.data_preview or {}
        if not isinstance(preview, dict):
            preview = {"output": preview}
        field = str(source_field or "stdout").strip()
        if field == "stdout":
            return preview.get("stdout") or preview.get("output") or ""
        if field == "stderr":
            return preview.get("stderr") or ""
        if field == "exit_code":
            return preview.get("exit_code")
        if field == "output":
            return preview.get("output")
        if field == "record":
            return result.model_dump(mode="json")
        raise RuntimeError(f"Unsupported operator input binding source_field: {field}")

    def _resolve_operator_inputs(
        self,
        action,
        results_by_node: dict[str, ExecutionResult],
        observability: ObservabilityContext | None = None,
    ) -> dict[str, Any]:
        """Resolve literal and upstream-bound inputs for one operator action."""

        resolved = dict(action.inputs or {})
        binding_details: list[dict[str, Any]] = []
        for binding in action.input_bindings:
            source = results_by_node.get(binding.source_action_id)
            if source is None:
                if binding.required:
                    raise RuntimeError(
                        f"Required operator input {binding.input_name!r} depends on "
                        f"unavailable action {binding.source_action_id!r}."
                    )
                resolved[binding.input_name] = binding.fallback_value
                continue
            if source.status != "success" and binding.required:
                raise RuntimeError(
                    f"Required operator input {binding.input_name!r} depends on failed "
                    f"action {binding.source_action_id!r}."
                )
            value = self._operator_record_field(source, binding.source_field)
            if (value is None or value == "") and not binding.required:
                value = binding.fallback_value
            resolved[binding.input_name] = value
            binding_details.append(
                {
                    "input_name": binding.input_name,
                    "source_action_id": binding.source_action_id,
                    "source_field": binding.source_field,
                    "value_preview": str(value)[:240],
                }
            )
        if action.input_bindings and isinstance(observability, ObservabilityContext):
            observability.info(
                STAGE_EXECUTION,
                "operator.input_bindings.resolved",
                "Operator input bindings resolved",
                "The runtime injected declared upstream action outputs into this operator action.",
                details={
                    "action_id": action.action_id,
                    "bindings": binding_details,
                    "input_names": sorted(resolved.keys()),
                },
                debug_only=True,
            )
        return resolved

    @staticmethod
    def _operator_input_preview(value: Any, *, max_chars: int = 6000) -> dict[str, Any]:
        """Return a compact runtime shape preview for deferred Python codegen."""

        if isinstance(value, str):
            lines = value.splitlines()
            return {
                "type": "string",
                "chars": len(value),
                "line_count": len(lines),
                "preview": _truncate(value, max_chars),
            }
        if isinstance(value, dict):
            return {
                "type": "object",
                "key_count": len(value),
                "keys": list(value.keys())[:50],
                "preview": _truncate(_stable_json(value), max_chars),
            }
        if isinstance(value, (list, tuple)):
            sample = value[:5] if isinstance(value, list) else list(value[:5])
            return {
                "type": "array",
                "item_count": len(value),
                "sample": sample,
                "preview": _truncate(_stable_json(value), max_chars),
            }
        return {"type": type(value).__name__, "preview": _truncate(repr(value), max_chars)}

    @staticmethod
    def _operator_runtime_input_contract(value: Any, *, max_chars: int = 6000) -> dict[str, Any]:
        """Return the exact runtime access shape plus a bounded sample value."""

        if isinstance(value, str):
            return {
                "runtime_type": "string",
                "sample_value": _truncate(value, max_chars),
            }
        if isinstance(value, dict):
            sample = {
                str(key): ExecutionEngine._operator_runtime_input_contract(item, max_chars=max_chars)
                for key, item in list(value.items())[:20]
            }
            return {
                "runtime_type": "object",
                "sample_value": sample,
                "sample_truncated": len(value) > 20,
            }
        if isinstance(value, (list, tuple)):
            sample_items = [
                ExecutionEngine._operator_runtime_input_contract(item, max_chars=max_chars)
                for item in list(value)[:5]
            ]
            return {
                "runtime_type": "array",
                "sample_value": sample_items,
                "sample_truncated": len(value) > 5,
            }
        return {
            "runtime_type": type(value).__name__,
            "sample_value": value if value is None or isinstance(value, (bool, int, float)) else repr(value),
        }

    def _complete_deferred_operator_python_code(
        self,
        *,
        action: OperatorAction,
        node: ActionNode,
        action_inputs: dict[str, Any],
        context: dict[str, Any],
        observability: ObservabilityContext | None = None,
    ) -> OperatorAction:
        """Generate Python code for a standard DAG operator node after inputs exist."""

        llm_client = context.get("llm_client")
        if llm_client is None:
            raise ExecutionError("Deferred operator Python code generation requires an llm_client.")
        function_name = "main" if action.kind == "python_action" else "transform"
        input_packet = {
            name: self._operator_input_preview(value)
            for name, value in sorted(action_inputs.items())
        }
        runtime_contract = {
            name: self._operator_runtime_input_contract(value)
            for name, value in sorted(action_inputs.items())
        }
        prompt = "\n".join(
            [
                *prompt_lines("execution.deferred_python_code"),
                "This is the code-authoring stage; planning intentionally deferred Python because upstream data was not available yet.",
                "Use the runtime input contract below. It shows the exact shape your code receives in inputs.",
                "The authoring preview packet is metadata for you only; it is not passed into inputs at execution time.",
                "If the runtime contract says inputs['stdout'] is a string, write code that parses inputs['stdout'] directly.",
                "Do not write inputs['stdout']['preview'], inputs['stdout'].get('preview'), inputs['stdout'].get('chars'), or similar metadata access unless the runtime contract says that input is an object.",
                f"The code must start with def {function_name}(inputs): and read only from the provided inputs dict.",
                "If your code uses a module name such as re, json, math, os, pathlib, glob, subprocess, pandas, numpy, or yaml, import it inside the function before first use.",
                (
                    "This is a python_action, so stdlib subprocess/os/pathlib/glob are allowed when needed. Prefer parsing provided inputs when they contain the needed data."
                    if action.kind == "python_action"
                    else "This is a python_transform. Python imports, subprocess, os, pathlib, glob, and data-processing libraries are permitted, but prefer parsing provided inputs when they contain the needed data."
                ),
                "If required input is missing, empty, or unparsable, raise ValueError with a precise message.",
                "Do not return zero, an empty table, or a success string from non-empty required inputs unless zero is semantically valid; if so set allow_zero_result true and explain why.",
                "Schema for OperatorPythonCodeProposal:",
                _stable_json(OperatorPythonCodeProposal.model_json_schema()),
                "Node needing code:",
                _stable_json(
                    {
                        "node_id": node.id,
                        "task_id": node.task_id,
                        "description": node.description,
                        "semantic_verb": node.semantic_verb,
                        "capability_id": node.capability_id,
                        "operation_id": node.operation_id,
                        "action": action.model_dump(mode="json"),
                    }
                ),
                "Runtime input contract:",
                _stable_json(runtime_contract),
                "Authoring input preview packet:",
                _stable_json(input_packet),
            ]
        )
        if isinstance(observability, ObservabilityContext):
            observability.info(
                STAGE_EXECUTION,
                "operator.python_code.generation_started",
                "Deferred Python code generation started",
                "The runtime is asking the LLM for Python code using real upstream input shape.",
                details={
                    "node_id": node.id,
                    "task_id": node.task_id,
                    "kind": action.kind,
                    "input_packet": input_packet,
                    "runtime_input_contract": runtime_contract,
                },
            )
        payload = llm_client.complete_json(prompt, OperatorPythonCodeProposal.model_json_schema())
        proposal = OperatorPythonCodeProposal.model_validate(payload)
        generated = action.model_copy(
            update={
                "code": proposal.code,
                "defer_code_generation": False,
                "declared_output_shape": proposal.declared_output_shape or action.declared_output_shape,
                "allow_zero_result": proposal.allow_zero_result,
                "reason": proposal.reason or action.reason,
            }
        )
        from agent_runtime.operator.pipeline import OperatorPlanValidator

        validator = OperatorPlanValidator(self._effective_config_for_context(context))
        errors = (
            validator._validate_python_program_action(generated)
            if generated.kind == "python_action"
            else validator._validate_python_action(generated)
        )
        if errors:
            raise ExecutionError(f"Deferred Python code failed validation: {_stable_json(errors)}")
        if isinstance(observability, ObservabilityContext):
            observability.info(
                STAGE_EXECUTION,
                "operator.python_code.generated",
                "Deferred Python code generated",
                "The runtime generated Python code after upstream input shape was known.",
                details={
                    "node_id": node.id,
                    "task_id": node.task_id,
                    "kind": generated.kind,
                    "input_names": sorted(action_inputs),
                    "declared_output_shape": generated.declared_output_shape,
                },
            )
        return generated

    def _execute_operator_node(
        self,
        node: ActionNode,
        arguments: dict[str, Any],
        dag: ActionDAG,
        results_by_node: dict[str, ExecutionResult],
        context: dict[str, Any],
        observability: ObservabilityContext | None = None,
    ) -> ExecutionResult:
        """Execute one validated operator-backed ActionDAG node."""

        task_to_node_id = {candidate.task_id: candidate.id for candidate in dag.nodes}
        action = operator_action_from_node(node, arguments, task_to_node_id=task_to_node_id)
        if action.kind in {"shell_command", "python_action"}:
            from agent_runtime.operator.pipeline import OperatorPlanValidator

            cwd = OperatorPlanValidator(self._effective_config_for_context(context)).resolved_cwd(action)
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            exit_code = 1
            gateway_node = None
            gateway_url = None
            gateway_metadata = merge_gateway_metadata(context)
            action_inputs: dict[str, Any] = {}
            if action.kind == "python_action":
                action_inputs = self._resolve_operator_inputs(action, results_by_node, observability)
                if action.defer_code_generation:
                    action = self._complete_deferred_operator_python_code(
                        action=action,
                        node=node,
                        action_inputs=action_inputs,
                        context=context,
                        observability=observability,
                    )
            command = (
                str(action.command or "")
                if action.kind == "shell_command"
                else python_action_command(str(action.code or ""), action_inputs)
            )
            display_command = str(action.command or "") if action.kind == "shell_command" else ""
            detached = action.execution_mode == "terminal_detached"
            terminal_session_id = str(context.get("terminal_session_id") or "").strip()
            terminal_required = action.interaction_mode == "may_prompt"
            terminal_dispatch_requested = bool(context.get("execute_in_terminal") and terminal_session_id)
            terminal_dispatch = bool(terminal_required and terminal_session_id) or detached
            if isinstance(observability, ObservabilityContext):
                observability.info(
                    STAGE_EXECUTION,
                    "execution.command.started",
                    "Command started",
                    "The runtime started streaming one command.",
                    details={
                        **gateway_metadata,
                        "node_id": node.id,
                        "task_id": node.task_id,
                        "action_id": action.action_id,
                        "label": node.description or action.reason or node.id,
                        "command": display_command,
                        "kind": action.kind,
                        "code": str(action.code or "")[:1200] if action.kind == "python_action" else None,
                        "cwd": str(cwd),
                        "terminal_dispatch": terminal_dispatch,
                        "terminal_dispatch_requested": terminal_dispatch_requested,
                        "terminal_session_id": terminal_session_id if terminal_dispatch else None,
                        "execution_mode": action.execution_mode,
                        "interaction_mode": action.interaction_mode,
                    },
                )
            if detached:
                start_terminal_detached_command = getattr(
                    self.gateway_client,
                    "start_terminal_detached_command",
                    None,
                )
                if not callable(start_terminal_detached_command):
                    raise ExecutionError("Gateway client does not support detached terminal execution.")
                response = start_terminal_detached_command(
                    command=command,
                    cwd=str(cwd),
                    execution_context=context,
                )
                gateway_node = response.get("gateway_node")
                gateway_url = response.get("gateway_url")
                gateway_metadata = merge_gateway_metadata(
                    gateway_metadata,
                    {"gateway_node": gateway_node, "gateway_url": gateway_url},
                    response,
                )
                message = str(response.get("message") or "Command started in terminal.")
                if isinstance(observability, ObservabilityContext):
                    observability.info(
                        STAGE_EXECUTION,
                        "execution.command.completed",
                        "Command started in terminal",
                        "A terminal-owned command was started and detached from runtime capture.",
                        details={
                            **gateway_metadata,
                            "node_id": node.id,
                            "task_id": node.task_id,
                            "action_id": action.action_id,
                            "label": node.description or action.reason or node.id,
                            "channel": "completed",
                            "text": message,
                            "exit_code": 0,
                            "cwd": str(cwd),
                            "terminal_dispatch": True,
                            "terminal_detached": True,
                            "terminal_session_id": response.get("terminal_session_id")
                            or context.get("terminal_session_id"),
                        },
                    )
                return ExecutionResult(
                    node_id=node.id,
                    status="success",
                    data_preview={
                        "status": "started",
                        "output": message,
                        "exit_code": 0,
                    },
                    metadata={
                        "operator_mode": True,
                        "declared_output_shape": action.declared_output_shape,
                        "cwd": str(cwd),
                        **gateway_metadata,
                        "gateway_node": gateway_node,
                        "gateway_url": gateway_url,
                        "terminal_dispatch": True,
                        "terminal_detached": True,
                    },
                )
            stream_terminal_command = getattr(self.gateway_client, "stream_terminal_command", None)
            stream_raw_command = getattr(self.gateway_client, "stream_raw_command", None)
            if terminal_dispatch and callable(stream_terminal_command):
                stream_events = stream_terminal_command(
                    command=command,
                    cwd=str(cwd),
                    execution_context=context,
                    display_command=(
                        display_command
                        if action.kind == "shell_command"
                        else f"[python_action] {node.description or action.reason or node.id}"
                    ),
                )
            elif terminal_required:
                background_terminal_error = _background_terminal_error_context(context)
                if background_terminal_error:
                    message = str(
                        background_terminal_error.get("background_terminal_error") or ""
                    ).strip()
                    return ExecutionResult(
                        node_id=node.id,
                        status="error",
                        error=(
                            "background_terminal_unavailable: "
                            f"{message or 'No background terminal session was attached to this background task.'}"
                        ),
                        metadata={
                            "operator_mode": True,
                            "declared_output_shape": action.declared_output_shape,
                            "cwd": str(cwd),
                            "terminal_dispatch": False,
                            "terminal_required": True,
                            "terminal_context_required": True,
                            "resumable": True,
                            "interaction_mode": action.interaction_mode,
                            **background_terminal_error,
                        },
                    )
                return ExecutionResult(
                    node_id=node.id,
                    status="error",
                    error=(
                        "terminal_context_required: Open the Agent UI terminal and continue "
                        "this run so the command can receive any required user input."
                    ),
                    metadata={
                        "operator_mode": True,
                        "declared_output_shape": action.declared_output_shape,
                        "cwd": str(cwd),
                        "terminal_dispatch": False,
                        "terminal_required": True,
                        "terminal_context_required": True,
                        "resumable": True,
                        "interaction_mode": action.interaction_mode,
                    },
                )
            elif callable(stream_raw_command):
                stream_events = stream_raw_command(
                    command=command,
                    cwd=str(cwd),
                    execution_context=context,
                )
            else:
                response = self.gateway_client.execute_raw_command(
                    command=command,
                    cwd=str(cwd),
                    execution_context=context,
                )
                gateway_node = response.get("gateway_node")
                gateway_url = response.get("gateway_url")
                gateway_metadata = merge_gateway_metadata(
                    gateway_metadata,
                    {"gateway_node": gateway_node, "gateway_url": gateway_url},
                    response,
                )
                stream_events = (
                    {"type": "stdout", "text": str(response.get("stdout", ""))},
                    {"type": "stderr", "text": str(response.get("stderr", ""))},
                    {"type": "completed", "exit_code": int(response.get("exit_code", 1))},
                )
            for chunk in stream_events:
                if _cancel_requested(context):
                    return ExecutionResult(
                        node_id=node.id,
                        status="error",
                        error="Execution cancelled by user.",
                        metadata={
                            "operator_mode": True,
                            "declared_output_shape": action.declared_output_shape,
                            "cwd": str(cwd),
                            **gateway_metadata,
                            "gateway_node": gateway_node,
                            "gateway_url": gateway_url,
                            "cancelled": True,
                        },
                    )
                chunk_type = str(chunk.get("type") or "")
                text = str(chunk.get("text") or "")
                gateway_node = chunk.get("gateway_node", gateway_node)
                gateway_url = chunk.get("gateway_url", gateway_url)
                gateway_metadata = merge_gateway_metadata(
                    gateway_metadata,
                    {"gateway_node": gateway_node, "gateway_url": gateway_url},
                    chunk,
                )
                terminal_dispatch = bool(chunk.get("terminal_dispatch") or terminal_dispatch)
                if chunk_type == "stdout":
                    stdout_parts.append(text)
                elif chunk_type == "stderr":
                    stderr_parts.append(text)
                elif chunk_type == "error":
                    stderr_parts.append(text)
                elif chunk_type == "cancelled":
                    stderr_parts.append(text or "Command cancelled by user.\n")
                    exit_code = int(chunk.get("exit_code") if chunk.get("exit_code") is not None else 130)
                elif chunk_type == "terminal_input_required":
                    pass
                elif chunk_type == "completed":
                    exit_code = int(chunk.get("exit_code") if chunk.get("exit_code") is not None else 1)
                if isinstance(observability, ObservabilityContext) and chunk_type in {
                    "stdout",
                    "stderr",
                    "completed",
                    "error",
                    "cancelled",
                    "terminal_input_required",
                }:
                    emitter = (
                        observability.warning
                        if chunk_type == "terminal_input_required"
                        else observability.error
                        if chunk_type in {"error", "cancelled"}
                        else observability.info
                    )
                    emitter(
                        STAGE_EXECUTION,
                        f"execution.command.{chunk_type}",
                        (
                            "Command output"
                            if chunk_type in {"stdout", "stderr"}
                            else "Terminal input required"
                            if chunk_type == "terminal_input_required"
                            else "Command cancelled"
                            if chunk_type == "cancelled"
                            else "Command completed"
                        ),
                        (
                            "The command is waiting for user input in the terminal."
                            if chunk_type == "terminal_input_required"
                            else "A command stream event was received."
                        ),
                        details={
                            **gateway_metadata,
                            "node_id": node.id,
                            "task_id": node.task_id,
                            "action_id": action.action_id,
                            "label": node.description or action.reason or node.id,
                            "channel": chunk_type,
                            "text": text,
                            "exit_code": chunk.get("exit_code"),
                            "cwd": str(cwd),
                            "terminal_dispatch": terminal_dispatch,
                            "terminal_dispatch_requested": terminal_dispatch_requested,
                            "terminal_session_id": chunk.get("terminal_session_id")
                            or (context.get("terminal_session_id") if terminal_dispatch else None),
                        },
                    )
            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)
            status = "success" if exit_code == 0 else "error"
            output_validation_error: str | None = None
            if action.kind == "python_action" and status == "success":
                try:
                    validate_operator_python_output(action, action_inputs, stdout)
                except ValueError as exc:
                    status = "error"
                    output_validation_error = str(exc)
                    stderr = f"{stderr}{'' if stderr.endswith(chr(10)) or not stderr else chr(10)}{exc}\n"
            return ExecutionResult(
                node_id=node.id,
                status=status,
                data_preview={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code,
                    "output": stdout,
                },
                error=None if status == "success" else (output_validation_error or stderr or stdout or "Command failed."),
                metadata={
                    "operator_mode": True,
                    "declared_output_shape": action.declared_output_shape,
                    "cwd": str(cwd),
                    **gateway_metadata,
                    "gateway_node": gateway_node,
                    "gateway_url": gateway_url,
                    "terminal_dispatch": terminal_dispatch,
                    "terminal_dispatch_requested": terminal_dispatch_requested,
                    "terminal_required": terminal_required,
                    "interaction_mode": action.interaction_mode,
                    "bound_inputs_preview": (
                        {
                            name: self._operator_input_preview(value)
                            for name, value in sorted(action_inputs.items())
                        }
                        if action.kind == "python_action"
                        else None
                    ),
                },
            )

        action_inputs = self._resolve_operator_inputs(action, results_by_node, observability)
        if action.defer_code_generation:
            action = self._complete_deferred_operator_python_code(
                action=action,
                node=node,
                action_inputs=action_inputs,
                context=context,
                observability=observability,
            )
        if self.python_executor is None:
            from agent_runtime.operator.pipeline import PythonTransformExecutor

            self.python_executor = PythonTransformExecutor()
        output = self.python_executor.execute(action, action_inputs)
        validate_operator_python_output(action, action_inputs, output)
        terminal_dispatch_requested = bool(context.get("execute_in_terminal") and context.get("terminal_session_id"))
        return ExecutionResult(
            node_id=node.id,
            status="success",
            data_preview={"output": output},
            metadata={
                "operator_mode": True,
                "declared_output_shape": action.declared_output_shape,
                "terminal_dispatch": False,
                "terminal_dispatch_requested": terminal_dispatch_requested,
            },
        )

    def _enforce_execution_ready_dag(self, dag: ActionDAG) -> None:
        """Reject untrusted or unprepared DAGs before execution."""

        if not isinstance(dag, ActionDAG):
            raise ValidationError("ExecutionEngine accepts only trusted ActionDAG instances.")
        if not dag.execution_ready:
            raise ValidationError("ExecutionEngine refuses DAGs that are not execution_ready.")
        if not dag.final_dag_hash:
            raise ValidationError("ExecutionEngine refuses DAGs without final_dag_hash.")
        if hash_action_dag(dag) != dag.final_dag_hash:
            raise ValidationError("ExecutionEngine refuses DAGs whose final_dag_hash does not match the payload.")
        if not dag.safety_decision:
            raise ValidationError("ExecutionEngine refuses DAGs without a recorded safety decision.")
        if not bool(dag.safety_decision.get("allowed", False)):
            raise ValidationError("ExecutionEngine refuses DAGs whose recorded safety decision is not allowed.")
        unresolved = [node.id for node in dag.nodes if node.is_unresolved]
        if unresolved:
            raise ValidationError(
                f"ExecutionEngine refuses DAGs with unresolved nodes: {', '.join(unresolved)}"
            )

    def _effective_config_for_context(
        self,
        context: dict[str, Any],
        *,
        confirmation_granted: bool = False,
    ) -> RuntimeConfig:
        """Return request-scoped runtime config overlays for execution."""

        config_updates: dict[str, Any] = {
            "confirmation_granted": (
                self.safety_policy.config.confirmation_granted or confirmation_granted
            ),
            "allow_mutating_capabilities": (
                self.safety_policy.config.allow_mutating_capabilities or confirmation_granted
            ),
        }
        terminal_session_id = str(context.get("terminal_session_id") or "").strip()
        terminal_cwd = str(context.get("terminal_cwd") or "").strip()
        if terminal_session_id and terminal_cwd:
            config_updates["terminal_session_id"] = terminal_session_id
            config_updates["terminal_cwd"] = terminal_cwd
        gateway_default_cwd = str(context.get("gateway_default_cwd") or "").strip()
        if gateway_default_cwd and not (terminal_session_id and terminal_cwd):
            config_updates["workspace_root"] = gateway_default_cwd
        gateway_node = str(context.get("gateway_node") or context.get("node") or "").strip()
        gateway_url = str(context.get("gateway_url") or "").strip()
        if gateway_node:
            config_updates["gateway_default_node"] = gateway_node
            endpoints = dict(getattr(self.safety_policy.config, "gateway_endpoints", {}) or {})
            context_endpoints = context.get("gateway_endpoints")
            if isinstance(context_endpoints, dict):
                for raw_node, raw_url in context_endpoints.items():
                    node = str(raw_node or "").strip()
                    url = str(raw_url or "").strip()
                    if node and url:
                        endpoints[node] = url
            if gateway_url:
                endpoints[gateway_node] = gateway_url
                config_updates["gateway_url"] = gateway_url
            if endpoints:
                config_updates["gateway_endpoints"] = endpoints
        config_updates["terminal_execution_enabled"] = bool(
            context.get("execute_in_terminal")
            and terminal_session_id
            and terminal_cwd
        )
        return self.safety_policy.config.model_copy(update=config_updates)

    def execute(self, dag: ActionDAG, context: dict[str, Any] | None = None) -> ResultBundle:
        """Execute a DAG in dependency order and return a normalized bundle."""

        self._enforce_execution_ready_dag(dag)
        context = dict(context or {})
        observability = observability_from_context(context)
        confirmation_granted = bool(context.get("confirmation", False))
        effective_config = self._effective_config_for_context(
            context,
            confirmation_granted=confirmation_granted,
        )
        decision = evaluate_dag_safety(dag, self.registry, effective_config)
        if not decision.allowed:
            return self._safety_bundle(
                dag,
                decision,
                safe_summary="Execution blocked by safety policy.",
                metadata={"confirmation_required": False},
                status="error",
            )

        if decision.requires_confirmation and not confirmation_granted:
            return self._safety_bundle(
                dag,
                decision,
                safe_summary="Execution requires confirmation before proceeding.",
                metadata={"confirmation_required": True},
                status="confirmation_required",
            )

        if decision.sanitized_dag is not None:
            executable_dag = dag.model_copy(
                update={
                    "nodes": decision.sanitized_dag.nodes,
                    "edges": decision.sanitized_dag.edges,
                    "global_constraints": decision.sanitized_dag.global_constraints,
                    "requires_confirmation": decision.requires_confirmation,
                    "safety_decision": decision.model_dump(mode="json"),
                }
            )
        else:
            executable_dag = dag
        ordered_nodes = _topological_sort(executable_dag)
        stop_on_error = bool(context.get("stop_on_error", self.safety_policy.config.stop_on_error))
        runtime_policy = SafetyPolicy(effective_config)

        results: list[ExecutionResult] = []
        node_status: dict[str, str] = {}
        results_by_node: dict[str, ExecutionResult] = {}
        hard_stop = False

        for node in ordered_nodes:
            if _cancel_requested(context):
                raise ExecutionError("Execution cancelled by user.")
            if isinstance(observability, ObservabilityContext):
                observability.info(
                    STAGE_EXECUTION,
                    EVENT_EXECUTION_NODE_STARTED,
                    "Execution node started",
                    "The runtime started executing one node.",
                    details={
                        "node_id": node.id,
                        "task_id": node.task_id,
                        "capability_id": node.capability_id,
                        "operation_id": node.operation_id,
                        "depends_on": list(node.depends_on),
                    },
                )
            dependency_statuses = {
                dependency_id: node_status.get(dependency_id)
                for dependency_id in _dependency_ids(node, executable_dag)
            }
            if hard_stop:
                skipped = ExecutionResult(
                    node_id=node.id,
                    status="skipped",
                    error="Skipped because stop_on_error halted downstream execution.",
                    metadata={
                        "capability_id": node.capability_id,
                        "operation_id": node.operation_id,
                    },
                )
                results.append(skipped)
                node_status[node.id] = skipped.status
                results_by_node[node.id] = skipped
                self.result_store.add(skipped)
                if isinstance(observability, ObservabilityContext):
                    observability.warning(
                        STAGE_EXECUTION,
                        EVENT_EXECUTION_NODE_FAILED,
                        "Execution node skipped",
                        "The node was skipped because stop_on_error halted downstream execution.",
                        details={
                            "node_id": node.id,
                            "reason": skipped.error,
                        },
                    )
                continue

            if any(status in {"error", "skipped"} for status in dependency_statuses.values()):
                skipped = ExecutionResult(
                    node_id=node.id,
                    status="skipped",
                    error="Skipped because one or more dependencies did not succeed.",
                    metadata={
                        "capability_id": node.capability_id,
                        "operation_id": node.operation_id,
                        "dependency_statuses": dependency_statuses,
                    },
                )
                results.append(skipped)
                node_status[node.id] = skipped.status
                results_by_node[node.id] = skipped
                self.result_store.add(skipped)
                if isinstance(observability, ObservabilityContext):
                    observability.warning(
                        STAGE_EXECUTION,
                        EVENT_EXECUTION_NODE_FAILED,
                        "Execution node skipped",
                        "The node was skipped because an upstream dependency did not succeed.",
                        details={
                            "node_id": node.id,
                            "dependency_statuses": dependency_statuses,
                        },
                    )
                continue

            capability = self.registry.get(node.capability_id)
            if capability.manifest.execution_backend != "operator":
                runtime_policy.assert_allowed(capability, node.operation_id)
            try:
                resolved_arguments = self._resolve_node_arguments(
                    node,
                    executable_dag,
                    results_by_node,
                    observability,
                )
                if isinstance(observability, ObservabilityContext):
                    observability.info(
                        STAGE_EXECUTION,
                        "execution.tool.input_resolved",
                        "Tool input resolved",
                        "The runtime resolved validated arguments for the selected capability.",
                        details={
                            "node_id": node.id,
                            "task_id": node.task_id,
                            "capability_id": node.capability_id,
                            "operation_id": node.operation_id,
                            "tool_input": resolved_arguments,
                        },
                        debug_only=True,
                    )
                execution_context = {
                    "node_id": node.id,
                    "task_id": node.task_id,
                    "execution_context": context,
                    "result_store": self.result_store,
                    "config": effective_config,
                    "registry": self.registry,
                    "runtime_state": context.get("runtime_state"),
                    "gateway_client": self.gateway_client,
                }
                if capability.manifest.execution_backend == "operator":
                    raw_result = self._execute_operator_node(
                        node,
                        resolved_arguments,
                        executable_dag,
                        results_by_node,
                        context,
                        observability,
                    )
                else:
                    raw_result = capability.execute(resolved_arguments, execution_context)
                if isinstance(observability, ObservabilityContext):
                    observability.info(
                        STAGE_EXECUTION,
                        "execution.tool.output_received",
                        "Tool output received",
                        "The selected capability returned an execution result.",
                        details={
                            "node_id": node.id,
                            "task_id": node.task_id,
                            "capability_id": node.capability_id,
                            "operation_id": node.operation_id,
                            "tool_output": (
                                raw_result.model_dump(mode="json")
                                if hasattr(raw_result, "model_dump")
                                else raw_result
                            ),
                        },
                        debug_only=True,
                    )
            except Exception as exc:
                errored = ExecutionResult(
                    node_id=node.id,
                    status="error",
                    error=str(exc),
                    metadata={
                        "capability_id": node.capability_id,
                        "operation_id": node.operation_id,
                        "error_class": type(exc).__name__,
                    },
                )
                results.append(errored)
                node_status[node.id] = errored.status
                results_by_node[node.id] = errored
                self.result_store.add(errored)
                if isinstance(observability, ObservabilityContext):
                    observability.error(
                        STAGE_EXECUTION,
                        EVENT_EXECUTION_NODE_FAILED,
                        "Execution node failed",
                        "The node failed during execution.",
                        details={
                            "node_id": node.id,
                            "capability_id": node.capability_id,
                            "operation_id": node.operation_id,
                            "error": errored.error,
                            "error_class": errored.metadata.get("error_class"),
                        },
                    )
                if stop_on_error:
                    hard_stop = True
                continue

            normalized = self._normalize_result(node, raw_result)
            results.append(normalized)
            node_status[node.id] = normalized.status
            results_by_node[node.id] = normalized
            if isinstance(observability, ObservabilityContext):
                if normalized.status == "success":
                    observability.info(
                        STAGE_EXECUTION,
                        EVENT_EXECUTION_NODE_COMPLETED,
                        "Execution node completed",
                        "The node completed successfully.",
                        details=_result_preview_details(normalized, node),
                    )
                else:
                    observability.error(
                        STAGE_EXECUTION,
                        EVENT_EXECUTION_NODE_FAILED,
                        "Execution node failed",
                        "The node returned an error status.",
                        details={
                            **_result_preview_details(normalized, node),
                            "error": normalized.error,
                        },
                    )
            if normalized.status == "error" and stop_on_error:
                hard_stop = True

        if results and all(result.status == "success" for result in results):
            bundle_status = "success"
            safe_summary = "All DAG nodes executed successfully."
        elif any(result.status == "error" for result in results):
            bundle_status = "partial" if any(
                result.status == "success" for result in results
            ) else "error"
            safe_summary = "Execution completed with one or more errors."
        elif any(result.status == "skipped" for result in results):
            bundle_status = "partial"
            safe_summary = "Execution completed with skipped downstream nodes."
        else:
            bundle_status = "error"
            safe_summary = "No executable results were produced."

        return ResultBundle(
            dag_id=executable_dag.dag_id,
            results=results,
            status=bundle_status,
            safe_summary=safe_summary,
            metadata={
                "warnings": list(decision.warnings),
                "confirmation_required": False,
                "stop_on_error": stop_on_error,
            },
        )
