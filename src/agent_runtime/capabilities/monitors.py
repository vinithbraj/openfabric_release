"""Agent-visible monitor runtime capabilities."""

from __future__ import annotations

from typing import Any

from agent_runtime.capabilities.base import BaseCapability
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.core.types import ExecutionResult
from agent_runtime.monitors import AgentMonitorCreate, AgentMonitorDraftRequest


def _context_payload(context: dict[str, Any]) -> dict[str, Any]:
    nested = context.get("execution_context")
    return nested if isinstance(nested, dict) else {}


def _is_monitor_manager(candidate: Any) -> bool:
    return all(
        callable(getattr(candidate, method, None))
        for method in (
            "create_monitor",
            "start_monitor",
            "cancel_monitor",
            "archive_monitor",
            "list_monitors",
            "get_monitor",
            "list_observations",
            "draft_monitor",
        )
    )


def _monitor_manager_from_context(context: dict[str, Any]) -> Any | None:
    candidate = context.get("monitor_manager")
    if _is_monitor_manager(candidate):
        return candidate
    nested = _context_payload(context)
    candidate = nested.get("monitor_manager")
    if _is_monitor_manager(candidate):
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


def _agent_mode(value: Any, fallback: str = "llm_operator") -> str:
    mode = str(value or fallback).strip()
    return mode if mode in {"standard", "llm_operator", "advisory"} else fallback


def _positive_int(value: Any, fallback: int, *, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(fallback)
    return max(minimum, number)


class RuntimeStartMonitorCapability(BaseCapability):
    """Create and start a bounded background monitor."""

    manifest = CapabilityManifest(
        capability_id="runtime.start_monitor",
        domain="runtime",
        operation_id="start_monitor",
        name="Start Monitor",
        description=(
            "Create and start a bounded background monitor in an isolated terminal when the user "
            "asks to watch, monitor, wait for, track, alert on, or notify about a condition over time."
        ),
        semantic_verbs=["create", "execute"],
        object_types=["monitor", "system_signal", "terminal_output", "background_terminal"],
        semantic_tags=["monitoring", "asynchronous", "notifications", "durable_tasks"],
        argument_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "title": {"type": "string"},
                "mode": {"type": "string", "enum": ["sample_command", "raw_stream"]},
                "command": {"type": "string"},
                "interval_seconds": {"type": "integer", "minimum": 1},
                "duration_seconds": {"type": "integer", "minimum": 1},
                "condition": {"type": "string"},
                "natural_language_condition": {"type": "string"},
                "trigger_mode": {"type": "string", "enum": ["deterministic", "llm_judged", "hybrid"]},
                "action_prompt": {"type": "string"},
                "planner_rationale": {"type": "string"},
                "risk_notes": {"type": "string"},
                "judge_interval_seconds": {"type": "integer", "minimum": 1},
                "gateway_id": {"type": "string"},
                "conversation_id": {"type": "string"},
                "agent_mode": {"type": "string", "enum": ["standard", "llm_operator", "advisory"]},
                "context": {"type": "object"},
                "start_now": {"type": "boolean"},
            },
        },
        required_arguments=[],
        optional_arguments=[
            "prompt",
            "title",
            "mode",
            "command",
            "interval_seconds",
            "duration_seconds",
            "condition",
            "natural_language_condition",
            "trigger_mode",
            "action_prompt",
            "planner_rationale",
            "risk_notes",
            "judge_interval_seconds",
            "gateway_id",
            "conversation_id",
            "agent_mode",
            "context",
            "start_now",
        ],
        output_schema={"type": "object"},
        output_object_types=["monitor"],
        output_fields=["monitor_id", "status", "stream_url", "missing_details"],
        output_affordances=["open_monitor_stream", "inspect_monitor", "stop_monitor"],
        produced_roles=["monitor"],
        side_effect_type="background_terminal",
        execution_backend="internal",
        backend_operation="runtime.start_monitor",
        risk_level="medium",
        read_only=False,
        mutates_state=True,
        requires_confirmation=True,
        examples=[
            {"prompt": "monitor free RAM for 5 minutes and tell me if it drops below 2GB"},
            {"prompt": "watch nvidia-smi every second and alert if free GPU memory drops below 2GB"},
            {"prompt": "run `watch -n 1 nvidia-smi` and alert if OOM appears"},
        ],
        safety_notes=[
            "Runs in a monitor-specific background terminal session, not the visible user terminal.",
            "Monitors are bounded by duration and follow the existing confirmation and auto-approve policy.",
        ],
    )

    def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> ExecutionResult:
        args = self.validate_arguments(arguments)
        manager = _monitor_manager_from_context(context)
        if manager is None:
            return _error(context, "Monitor manager is not available in this runtime.")

        execution_context = _context_payload(context)
        monitor_context = dict(args.get("context") or {})
        prompt = str(args.get("prompt") or execution_context.get("raw_prompt") or "").strip()
        command = str(args.get("command") or "").strip()
        mode = str(args.get("mode") or "sample_command").strip()
        missing_details: list[str] = []

        if not command:
            if not prompt:
                missing_details.append("prompt_or_command")
            else:
                draft = manager.draft_monitor(
                    AgentMonitorDraftRequest(
                        prompt=prompt,
                        context=monitor_context,
                        agent_mode=_agent_mode(args.get("agent_mode") or execution_context.get("agent_mode")),
                    )
                )
                if not draft.is_monitor_request or not draft.drafts:
                    missing_details.append("command")
                else:
                    first = draft.drafts[0]
                    command = first.command
                    mode = first.mode
                    args.setdefault("title", first.title)
                    args.setdefault("interval_seconds", first.interval_seconds)
                    args.setdefault("duration_seconds", first.duration_seconds)
                    args.setdefault("condition", first.condition)
                    args.setdefault("natural_language_condition", first.natural_language_condition)
                    args.setdefault("trigger_mode", first.trigger_mode)
                    args.setdefault("action_prompt", first.action_prompt)
                    args.setdefault("planner_rationale", first.planner_rationale)
                    args.setdefault("risk_notes", first.risk_notes)
                    args.setdefault("judge_interval_seconds", first.judge_interval_seconds)
                    missing_details.extend(first.missing_details)

        if not command or missing_details:
            return _success(
                context,
                {
                    "needs_clarification": True,
                    "missing_details": sorted({item for item in missing_details if item}),
                    "message": "I need a monitor command or enough detail to draft one safely.",
                },
            )

        gateway_id = str(args.get("gateway_id") or monitor_context.get("gateway_id") or execution_context.get("gateway_id") or "").strip()
        if gateway_id:
            monitor_context["gateway_id"] = gateway_id
        conversation_id = str(args.get("conversation_id") or execution_context.get("conversation_id") or "").strip()
        create = AgentMonitorCreate(
            prompt=prompt or command,
            title=str(args.get("title") or "").strip(),
            mode=mode if mode in {"sample_command", "raw_stream"} else "sample_command",  # type: ignore[arg-type]
            command=command,
            interval_seconds=_positive_int(args.get("interval_seconds"), 5),
            duration_seconds=_positive_int(args.get("duration_seconds"), 300),
            condition=str(args.get("condition") or "").strip(),
            natural_language_condition=str(args.get("natural_language_condition") or "").strip(),
            trigger_mode=str(args.get("trigger_mode") or "deterministic").strip(),  # type: ignore[arg-type]
            action_prompt=str(args.get("action_prompt") or "").strip(),
            planner_rationale=str(args.get("planner_rationale") or "").strip(),
            risk_notes=str(args.get("risk_notes") or "").strip(),
            judge_interval_seconds=_positive_int(args.get("judge_interval_seconds"), 5),
            status="queued",
            agent_mode=_agent_mode(args.get("agent_mode") or execution_context.get("agent_mode")),
            conversation_id=conversation_id,
            gateway_id=gateway_id,
            context=monitor_context,
        )
        try:
            result = manager.create_monitor(create, start_now=bool(args.get("start_now", True)))
        except Exception as exc:
            return _error(context, str(exc))
        monitor = dict(result.get("monitor") or {})
        monitor_id = str(monitor.get("monitor_id") or "")
        return _success(
            context,
            {
                **result,
                "monitor_id": monitor_id,
                "stream_url": f"/api/agent/monitors/{monitor_id}/stream" if monitor_id else "",
                "message": "Monitor started asynchronously." if bool(args.get("start_now", True)) else "Monitor created.",
            },
        )


class RuntimeStopMonitorCapability(BaseCapability):
    """Stop or otherwise control an existing monitor."""

    manifest = CapabilityManifest(
        capability_id="runtime.stop_monitor",
        domain="runtime",
        operation_id="stop_monitor",
        name="Stop Monitor",
        description="Cancel, pause, archive, or restart an existing background monitor by monitor id.",
        semantic_verbs=["update", "delete"],
        object_types=["monitor", "background_terminal"],
        semantic_tags=["monitoring", "control", "cancellation"],
        argument_schema={
            "type": "object",
            "properties": {
                "monitor_id": {"type": "string"},
                "action": {"type": "string", "enum": ["cancel", "stop", "pause", "archive", "restart", "start"]},
            },
        },
        required_arguments=["monitor_id"],
        optional_arguments=["action"],
        output_schema={"type": "object"},
        output_object_types=["monitor"],
        output_fields=["monitor_id", "status"],
        output_affordances=["inspect_monitor", "restart_monitor"],
        side_effect_type="background_terminal_control",
        execution_backend="internal",
        backend_operation="runtime.stop_monitor",
        risk_level="medium",
        read_only=False,
        mutates_state=True,
        requires_confirmation=True,
        examples=[
            {"prompt": "stop monitor mon_123"},
            {"prompt": "pause the GPU monitor"},
            {"prompt": "archive monitor mon_123"},
        ],
        safety_notes=["Stop maps to cancel by default; pause only pauses when explicitly requested."],
    )

    def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> ExecutionResult:
        args = self.validate_arguments(arguments)
        manager = _monitor_manager_from_context(context)
        if manager is None:
            return _error(context, "Monitor manager is not available in this runtime.")
        monitor_id = str(args.get("monitor_id") or "").strip()
        action = str(args.get("action") or "cancel").strip().lower()
        try:
            if action in {"pause"}:
                result = manager.cancel_monitor(monitor_id, status="paused")
            elif action in {"archive"}:
                result = manager.archive_monitor(monitor_id)
            elif action in {"restart", "start"}:
                result = manager.start_monitor(monitor_id)
            else:
                result = manager.cancel_monitor(monitor_id, status="cancelled")
        except Exception as exc:
            return _error(context, str(exc))
        monitor = dict(result.get("monitor") or {})
        return _success(
            context,
            {
                **result,
                "monitor_id": str(monitor.get("monitor_id") or monitor_id),
                "message": f"Monitor {result.get('status') or action}.",
            },
        )


class RuntimeInspectMonitorsCapability(BaseCapability):
    """Read monitor status and observations."""

    manifest = CapabilityManifest(
        capability_id="runtime.inspect_monitors",
        domain="runtime",
        operation_id="inspect_monitors",
        name="Inspect Monitors",
        description="List monitors, fetch a monitor status, or read recent observations and trigger records.",
        semantic_verbs=["read", "list", "summarize"],
        object_types=["monitor", "monitor_observation", "monitor_trigger"],
        semantic_tags=["monitoring", "status", "observations"],
        argument_schema={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["list", "status", "observations"]},
                "monitor_id": {"type": "string"},
                "status": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
                "after_sequence": {"type": "integer", "minimum": 0},
                "include_archived": {"type": "boolean"},
            },
        },
        required_arguments=[],
        optional_arguments=["operation", "monitor_id", "status", "limit", "after_sequence", "include_archived"],
        output_schema={"type": "object"},
        output_object_types=["monitor", "monitor_observation"],
        output_fields=["monitors", "monitor", "observations", "counts", "triggers"],
        output_affordances=["open_monitor_stream", "stop_monitor"],
        execution_backend="internal",
        backend_operation="runtime.inspect_monitors",
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[
            {"prompt": "show my running monitors"},
            {"prompt": "what did monitor mon_123 observe?"},
            {"prompt": "summarize recent monitor triggers"},
        ],
        safety_notes=["Reads persisted monitor metadata and truncated observation previews only."],
    )

    def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> ExecutionResult:
        args = self.validate_arguments(arguments)
        manager = _monitor_manager_from_context(context)
        if manager is None:
            return _error(context, "Monitor manager is not available in this runtime.")
        operation = str(args.get("operation") or "").strip().lower()
        monitor_id = str(args.get("monitor_id") or "").strip()
        limit = _positive_int(args.get("limit"), 50)
        try:
            if operation == "observations" or (monitor_id and operation not in {"list"}):
                if operation == "observations":
                    result = manager.list_observations(
                        monitor_id,
                        limit=limit,
                        after_sequence=_positive_int(args.get("after_sequence"), 0, minimum=0),
                    )
                else:
                    result = manager.get_monitor(monitor_id)
            else:
                result = manager.list_monitors(
                    status=str(args.get("status") or "").strip() or None,
                    limit=limit,
                    include_archived=bool(args.get("include_archived", False)),
                )
        except Exception as exc:
            return _error(context, str(exc))
        data_type = "table" if "monitors" in result or "observations" in result else "summary"
        return _success(context, result, data_type=data_type)
