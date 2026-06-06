from __future__ import annotations

from agent_runtime.capabilities import CapabilityRegistry, build_default_registry
from agent_runtime.capabilities.base import BaseCapability
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.core.config import RuntimeConfig
from agent_runtime.core.types import ActionDAG, ActionNode, ExecutionResult, InputRef
from agent_runtime.execution.engine import ExecutionEngine
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.llm.reproducibility import hash_action_dag
from agent_runtime.observability import InMemoryEventSink


def _ready_dag(dag: ActionDAG, allowed: bool = True) -> ActionDAG:
    prepared = dag.model_copy(
        update={
            "execution_ready": allowed,
            "safety_decision": {"allowed": allowed},
        }
    )
    return prepared.model_copy(update={"final_dag_hash": hash_action_dag(prepared)})


class CountingReadCapability(BaseCapability):
    manifest = CapabilityManifest(
        capability_id="counting.read",
        domain="generic",
        operation_id="read",
        name="Counting Read",
        description="Return a tracked value.",
        semantic_verbs=["read"],
        object_types=["generic"],
        argument_schema={"value": {"type": "string"}},
        required_arguments=["value"],
        optional_arguments=["path"],
        output_schema={"value": {"type": "string"}},
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[{"arguments": {"value": "hello"}}],
        safety_notes=[],
    )

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, arguments: dict[str, object], context: dict[str, object]) -> ExecutionResult:
        self.calls += 1
        validated = self.validate_arguments(arguments)
        return ExecutionResult(
            node_id=str(context.get("node_id") or ""),
            status="success",
            data_preview={"value": validated["value"]},
            metadata={"calls": self.calls},
        )


class FailingCapability(BaseCapability):
    manifest = CapabilityManifest(
        capability_id="counting.fail",
        domain="generic",
        operation_id="read",
        name="Failing Read",
        description="Always fail.",
        semantic_verbs=["read"],
        object_types=["generic"],
        argument_schema={"value": {"type": "string"}},
        required_arguments=["value"],
        optional_arguments=[],
        output_schema={"value": {"type": "string"}},
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[{"arguments": {"value": "hello"}}],
        safety_notes=[],
    )

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, arguments: dict[str, object], context: dict[str, object]) -> ExecutionResult:
        self.calls += 1
        raise RuntimeError("simulated failure")


class RecordingGatewayClient:
    """Tiny gateway double for operator command execution tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def stream_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        self.calls.append(
            {
                "command": command,
                "cwd": cwd,
                "execution_context": dict(execution_context or {}),
            }
        )
        return [
            {"type": "stdout", "text": f"{cwd}\n"},
            {"type": "completed", "exit_code": 0, "gateway_node": "fake"},
        ]

    def stream_terminal_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, object] | None = None,
        display_command: str | None = None,
    ) -> list[dict[str, object]]:
        self.calls.append(
            {
                "command": command,
                "cwd": cwd,
                "display_command": display_command,
                "execution_context": dict(execution_context or {}),
                "terminal": True,
            }
        )
        return [
            {
                "type": "stdout",
                "text": f"terminal:{cwd}\n",
                "gateway_node": "fake",
                "terminal_dispatch": True,
                "terminal_session_id": dict(execution_context or {}).get("terminal_session_id"),
            },
            {"type": "completed", "exit_code": 0, "gateway_node": "fake", "terminal_dispatch": True},
        ]

    def start_terminal_detached_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "command": command,
                "cwd": cwd,
                "execution_context": dict(execution_context or {}),
                "terminal": True,
                "detached": True,
            }
        )
        return {
            "ok": True,
            "message": "Command started in terminal.",
            "gateway_node": "fake",
            "terminal_dispatch": True,
            "terminal_detached": True,
            "terminal_session_id": dict(execution_context or {}).get("terminal_session_id"),
        }


class QueueCodeLLM:
    model = "fake-codegen"
    temperature = 0.0

    def __init__(self, *responses: dict[str, object]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict[str, object]) -> dict[str, object]:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("Unexpected LLM call")
        return self.responses.pop(0)


class DeleteCapability(BaseCapability):
    manifest = CapabilityManifest(
        capability_id="generic.delete",
        domain="generic",
        operation_id="delete",
        name="Delete Value",
        description="Delete a generic value.",
        semantic_verbs=["delete"],
        object_types=["generic"],
        argument_schema={"value": {"type": "string"}},
        required_arguments=["value"],
        optional_arguments=[],
        output_schema={"deleted": {"type": "boolean"}},
        risk_level="high",
        read_only=False,
        mutates_state=True,
        requires_confirmation=True,
        examples=[{"arguments": {"value": "old"}}],
        safety_notes=["Mutates state."],
    )

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, arguments: dict[str, object], context: dict[str, object]) -> ExecutionResult:
        self.calls += 1
        validated = self.validate_arguments(arguments)
        return ExecutionResult(
            node_id=str(context.get("node_id") or ""),
            status="success",
            data_preview={"deleted": validated["value"]},
        )


class LargeOutputCapability(BaseCapability):
    manifest = CapabilityManifest(
        capability_id="generic.large",
        domain="generic",
        operation_id="read",
        name="Large Output",
        description="Return a large payload.",
        semantic_verbs=["read"],
        object_types=["generic"],
        argument_schema={},
        required_arguments=[],
        optional_arguments=[],
        output_schema={"blob": {"type": "string"}},
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[{"arguments": {}}],
        safety_notes=[],
    )

    def execute(self, arguments: dict[str, object], context: dict[str, object]) -> ExecutionResult:
        return ExecutionResult(
            node_id=str(context.get("node_id") or ""),
            status="success",
            data_preview={"blob": "x" * 200},
        )


class ProduceRowsCapability(BaseCapability):
    manifest = CapabilityManifest(
        capability_id="produce.rows",
        domain="generic",
        operation_id="read",
        name="Produce Rows",
        description="Produce tabular rows for downstream tests.",
        semantic_verbs=["read"],
        object_types=["table"],
        argument_schema={"rows": {"type": "array"}},
        required_arguments=["rows"],
        optional_arguments=[],
        output_schema={"rows": {"type": "array"}},
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[{"arguments": {"rows": [{"value": 1}]}}],
        safety_notes=[],
    )

    def execute(self, arguments: dict[str, object], context: dict[str, object]) -> ExecutionResult:
        validated = self.validate_arguments(arguments)
        return ExecutionResult(
            node_id=str(context.get("node_id") or ""),
            status="success",
            data_preview={"rows": list(validated["rows"])},
            metadata={"data_type": "table"},
        )


class CountRowsCapability(BaseCapability):
    manifest = CapabilityManifest(
        capability_id="count.rows",
        domain="generic",
        operation_id="analyze",
        name="Count Rows",
        description="Count rows from resolved upstream input.",
        semantic_verbs=["analyze"],
        object_types=["table", "summary"],
        argument_schema={"input_ref": {"type": "string"}},
        required_arguments=["input_ref"],
        optional_arguments=[],
        output_schema={"value": {"type": "integer"}},
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[{"arguments": {"input_ref": "node-a.output"}}],
        safety_notes=[],
    )

    def execute(self, arguments: dict[str, object], context: dict[str, object]) -> ExecutionResult:
        validated = self.validate_arguments(arguments)
        input_data = validated["input_ref"]
        if not isinstance(input_data, dict) or not isinstance(input_data.get("rows"), list):
            raise RuntimeError("resolved input_ref did not produce row data")
        return ExecutionResult(
            node_id=str(context.get("node_id") or ""),
            status="success",
            data_preview={"value": len(input_data["rows"])},
            metadata={"data_type": "scalar"},
        )


class FormatValueCapability(BaseCapability):
    manifest = CapabilityManifest(
        capability_id="format.value",
        domain="generic",
        operation_id="render",
        name="Format Value",
        description="Format one upstream scalar value.",
        semantic_verbs=["render"],
        object_types=["summary"],
        argument_schema={"input_ref": {"type": "string"}},
        required_arguments=["input_ref"],
        optional_arguments=[],
        output_schema={"text": {"type": "string"}},
        risk_level="low",
        read_only=True,
        mutates_state=False,
        requires_confirmation=False,
        examples=[{"arguments": {"input_ref": "node-b.value"}}],
        safety_notes=[],
    )

    def execute(self, arguments: dict[str, object], context: dict[str, object]) -> ExecutionResult:
        validated = self.validate_arguments(arguments)
        return ExecutionResult(
            node_id=str(context.get("node_id") or ""),
            status="success",
            data_preview={"text": f"count={validated['input_ref']}"},
            metadata={"data_type": "text"},
        )


def _registry() -> tuple[CapabilityRegistry, CountingReadCapability, FailingCapability, DeleteCapability, LargeOutputCapability]:
    registry = CapabilityRegistry()
    counting = CountingReadCapability()
    failing = FailingCapability()
    deleting = DeleteCapability()
    large = LargeOutputCapability()
    registry.register(counting)
    registry.register(failing)
    registry.register(deleting)
    registry.register(large)
    return registry, counting, failing, deleting, large


def test_successful_linear_dag_executes_in_dependency_order() -> None:
    registry, counting, _, _, _ = _registry()
    engine = ExecutionEngine(registry)
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-a",
                task_id="task-a",
                description="first read",
                semantic_verb="read",
                capability_id="counting.read",
                operation_id="read",
                arguments={"value": "a"},
                safety_labels=[],
            ),
            ActionNode(
                id="node-b",
                task_id="task-b",
                description="second read",
                semantic_verb="read",
                capability_id="counting.read",
                operation_id="read",
                arguments={"value": "b"},
                depends_on=["node-a"],
                safety_labels=[],
            ),
        ],
        edges=[("node-a", "node-b")],
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True})

    assert bundle.status == "success"
    assert [result.node_id for result in bundle.results] == ["node-a", "node-b"]
    assert counting.calls == 2


def test_failed_dependency_causes_downstream_skip() -> None:
    registry, counting, failing, _, _ = _registry()
    engine = ExecutionEngine(registry)
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-fail",
                task_id="task-fail",
                description="fail first",
                semantic_verb="read",
                capability_id="counting.fail",
                operation_id="read",
                arguments={"value": "x"},
                safety_labels=[],
            ),
            ActionNode(
                id="node-after",
                task_id="task-after",
                description="read after failure",
                semantic_verb="read",
                capability_id="counting.read",
                operation_id="read",
                arguments={"value": "after"},
                depends_on=["node-fail"],
                safety_labels=[],
            ),
        ],
        edges=[("node-fail", "node-after")],
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True, "stop_on_error": False})

    assert bundle.status == "error"
    assert failing.calls == 1
    assert counting.calls == 0
    assert bundle.results[0].status == "error"
    assert bundle.results[1].status == "skipped"


def test_blocked_dag_does_not_execute() -> None:
    registry, counting, _, _, _ = _registry()
    engine = ExecutionEngine(registry, RuntimeConfig(workspace_root="."))
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-blocked",
                task_id="task-blocked",
                description="blocked traversal",
                semantic_verb="read",
                capability_id="counting.read",
                operation_id="read",
                arguments={"value": "x", "path": "../secret.txt"},
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True})

    assert bundle.status == "error"
    assert counting.calls == 0
    assert bundle.metadata["blocked_reasons"]


def test_operator_cwd_outside_workspace_is_allowed_when_guard_disabled(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    outside_cwd = str(tmp_path.parent.resolve())
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-shell",
                task_id="task-shell",
                description="pwd",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={"command": "pwd", "cwd": outside_cwd, "risk": "low"},
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True})

    assert bundle.status == "success"
    assert gateway.calls[0]["cwd"] == outside_cwd


def test_operator_cwd_outside_workspace_requires_trusted_terminal_context_when_guard_enabled(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    outside_cwd = str(tmp_path.parent.resolve())
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
            operator_workspace_cwd_guard_enabled=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-shell",
                task_id="task-shell",
                description="pwd",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={"command": "pwd", "cwd": outside_cwd, "risk": "low"},
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True})

    assert bundle.status == "error"
    assert gateway.calls == []
    assert any("cwd" in reason for reason in bundle.metadata["blocked_reasons"])


def test_operator_cwd_can_follow_trusted_terminal_context(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    terminal_cwd = str(tmp_path.parent.resolve())
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-shell",
                task_id="task-shell",
                description="pwd",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={"command": "pwd", "cwd": ".", "risk": "low"},
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(
        _ready_dag(dag),
        {
            "confirmation": True,
            "terminal_session_id": "term-test",
            "terminal_cwd": terminal_cwd,
        },
    )

    assert bundle.status == "success"
    assert gateway.calls[0]["cwd"] == terminal_cwd


def test_operator_shell_uses_capsule_even_with_terminal_context(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    terminal_cwd = str(tmp_path.parent.resolve())
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-shell",
                task_id="task-shell",
                description="pwd",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={"command": "pwd", "cwd": ".", "risk": "low"},
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(
        _ready_dag(dag),
        {
            "confirmation": True,
            "terminal_session_id": "term-test",
            "terminal_cwd": terminal_cwd,
            "execute_in_terminal": True,
        },
    )

    assert bundle.status == "success"
    assert "terminal" not in gateway.calls[0]
    assert bundle.results[0].metadata["terminal_dispatch"] is False
    assert bundle.results[0].metadata["terminal_dispatch_requested"] is True


def test_operator_shell_command_trace_events_include_gateway_metadata(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    sink = InMemoryEventSink()
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-shell",
                task_id="task-shell",
                description="pwd",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={"command": "pwd", "cwd": ".", "risk": "low"},
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(
        _ready_dag(dag),
        {
            "confirmation": True,
            "gateway_routing": {
                "mode": "selected",
                "gateway_display_name": "Bench GPU",
                "gateway_node": "bench-node",
                "gateway_platform_label": "Linux",
            },
            "observability": {"enabled": True, "debug": True, "sinks": [sink]},
        },
    )

    assert bundle.status == "success"
    command_events = [event for event in sink.events if event.event_type.startswith("execution.command.")]
    assert command_events
    assert {event.details.get("gateway_display_name") for event in command_events} == {"Bench GPU"}
    assert {event.details.get("gateway_platform_label") for event in command_events} == {"Linux"}
    assert command_events[-1].details["gateway_node"] == "fake"


def test_operator_may_prompt_dispatches_through_terminal_context_without_global_terminal_mode(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    terminal_cwd = str(tmp_path.parent.resolve())
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-push",
                task_id="task-push",
                description="push current branch",
                semantic_verb="update",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": "git push origin $(git branch --show-current)",
                    "cwd": ".",
                    "risk": "high",
                    "interaction_mode": "may_prompt",
                },
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(
        _ready_dag(dag),
        {
            "confirmation": True,
            "terminal_session_id": "term-test",
            "terminal_cwd": terminal_cwd,
        },
    )

    assert bundle.status == "success"
    assert gateway.calls[0]["terminal"] is True
    assert bundle.results[0].metadata["terminal_dispatch"] is True
    assert bundle.results[0].metadata["terminal_required"] is True


def test_operator_may_prompt_without_terminal_context_is_resumable_error(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-push",
                task_id="task-push",
                description="push current branch",
                semantic_verb="update",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": "git push origin $(git branch --show-current)",
                    "cwd": ".",
                    "risk": "high",
                    "interaction_mode": "may_prompt",
                },
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True})

    assert bundle.status == "error"
    assert gateway.calls == []
    assert bundle.results[0].metadata["terminal_context_required"] is True
    assert bundle.results[0].metadata["resumable"] is True
    assert str(bundle.results[0].error).startswith("terminal_context_required")


def test_operator_may_prompt_background_task_without_terminal_is_background_error(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-ssh",
                task_id="task-ssh",
                description="check remote host",
                semantic_verb="inspect",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": "ssh deploy@example.com uptime",
                    "cwd": ".",
                    "risk": "high",
                    "interaction_mode": "may_prompt",
                },
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(
        _ready_dag(dag),
        {
            "confirmation": True,
            "durable_task_id": "task-bg",
            "background_terminal_error": "Terminal cwd is not configured.",
        },
    )

    assert bundle.status == "error"
    assert gateway.calls == []
    assert bundle.results[0].metadata["background_task"] is True
    assert bundle.results[0].metadata["background_terminal_unavailable"] is True
    assert bundle.results[0].metadata["background_terminal_error"] == "Terminal cwd is not configured."
    assert bundle.results[0].metadata["retryable_after_terminal_config"] is True
    assert str(bundle.results[0].error).startswith("background_terminal_unavailable:")
    assert "Open the Agent UI terminal" not in str(bundle.results[0].error)


def test_operator_auth_prompt_command_auto_dispatches_through_terminal_context(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    terminal_cwd = str(tmp_path.parent.resolve())
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-push",
                task_id="task-push",
                description="push current branch",
                semantic_verb="update",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": "git push origin $(git branch --show-current)",
                    "cwd": ".",
                    "risk": "high",
                },
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(
        _ready_dag(dag),
        {
            "confirmation": True,
            "terminal_session_id": "term-test",
            "terminal_cwd": terminal_cwd,
        },
    )

    assert bundle.status == "success"
    assert gateway.calls[0]["terminal"] is True
    assert bundle.results[0].metadata["terminal_dispatch"] is True
    assert bundle.results[0].metadata["terminal_required"] is True


def test_operator_shell_can_start_detached_terminal_command(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    terminal_cwd = str(tmp_path.parent.resolve())
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-watch",
                task_id="task-watch",
                description="watch gpu",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": "watch -n 1 nvidia-smi",
                    "cwd": ".",
                    "execution_mode": "terminal_detached",
                    "declared_output_shape": "interactive_stream",
                    "risk": "low",
                },
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(
        _ready_dag(dag),
        {
            "confirmation": True,
            "terminal_session_id": "term-test",
            "terminal_cwd": terminal_cwd,
            "execute_in_terminal": True,
        },
    )

    assert bundle.status == "success"
    assert gateway.calls[0]["detached"] is True
    assert bundle.results[0].metadata["terminal_detached"] is True


def test_standard_operator_deferred_python_uses_runtime_input_shape(tmp_path) -> None:
    registry = build_default_registry()
    gateway = RecordingGatewayClient()
    llm = QueueCodeLLM(
        {
            "code": "def transform(inputs):\n    return 'line_count=' + str(len(inputs['stdout'].splitlines()))",
            "declared_output_shape": "text",
            "allow_zero_result": False,
            "reason": "Count lines after seeing the actual stdout shape.",
            "confidence": 0.95,
        }
    )
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            gateway_url="http://gateway",
            allow_shell_execution=True,
        ),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-shell",
                task_id="task-shell",
                description="produce text",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={"command": "printf 'a\\nb\\n'", "cwd": ".", "risk": "low"},
                safety_labels=[],
            ),
            ActionNode(
                id="node-transform",
                task_id="task-transform",
                description="count stdout lines",
                semantic_verb="calculate",
                capability_id="operator.python_transform",
                operation_id="python_transform",
                arguments={
                    "cwd": ".",
                    "defer_code_generation": True,
                    "input_bindings": [
                        {
                            "input_name": "stdout",
                            "source_action_id": "node-shell",
                            "source_field": "stdout",
                            "required": True,
                            "fallback_value": None,
                        }
                    ],
                    "risk": "low",
                    "reason": "Count after upstream output is available.",
                },
                depends_on=["node-shell"],
                safety_labels=[],
            ),
        ],
        edges=[("node-shell", "node-transform")],
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True, "llm_client": llm})

    assert bundle.status == "success"
    assert bundle.results[1].data_preview == {"output": "line_count=1"}
    assert "Runtime input contract" in llm.prompts[0]
    assert "Authoring input preview packet" in llm.prompts[0]
    assert "inputs['stdout'] is a string" in llm.prompts[0]
    assert "not passed into inputs at execution time" in llm.prompts[0]
    assert str(tmp_path) in llm.prompts[0]


def test_confirmation_required_dag_does_not_execute_without_confirmation() -> None:
    registry, _, _, deleting, _ = _registry()
    engine = ExecutionEngine(registry)
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-delete",
                task_id="task-delete",
                description="delete value",
                semantic_verb="delete",
                capability_id="generic.delete",
                operation_id="delete",
                arguments={"value": "old"},
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(_ready_dag(dag), {})

    assert bundle.status == "confirmation_required"
    assert bundle.metadata["confirmation_required"] is True
    assert deleting.calls == 0


def test_large_output_is_stored_by_reference() -> None:
    registry, _, _, _, large = _registry()
    store = InMemoryResultStore()
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(max_output_preview_bytes=64),
        store,
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-large",
                task_id="task-large",
                description="read large output",
                semantic_verb="read",
                capability_id="generic.large",
                operation_id="read",
                arguments={},
                safety_labels=[],
            )
        ]
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True})

    assert bundle.status == "success"
    result = bundle.results[0]
    assert result.data_ref is not None
    assert result.data_preview is not None
    assert result.data_preview["truncated"] is True
    assert store.get(result.data_ref.ref_id) == {"blob": "x" * 200}


def test_node_b_consumes_node_a_output_successfully() -> None:
    registry = CapabilityRegistry()
    registry.register(ProduceRowsCapability())
    registry.register(CountRowsCapability())
    engine = ExecutionEngine(registry)
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-a",
                task_id="task-a",
                description="produce rows",
                semantic_verb="read",
                capability_id="produce.rows",
                operation_id="read",
                arguments={"rows": [{"value": 1}, {"value": 2}]},
                safety_labels=[],
            ),
            ActionNode(
                id="node-b",
                task_id="task-b",
                description="count rows",
                semantic_verb="analyze",
                capability_id="count.rows",
                operation_id="analyze",
                arguments={"input_ref": InputRef(source_node_id="node-a", expected_data_type="table")},
                depends_on=["node-a"],
                safety_labels=[],
            ),
        ],
        edges=[("node-a", "node-b")],
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True})

    assert bundle.status == "success"
    assert bundle.results[1].data_preview == {"value": 2}


def test_node_c_consumes_node_b_output_successfully() -> None:
    registry = CapabilityRegistry()
    registry.register(ProduceRowsCapability())
    registry.register(CountRowsCapability())
    registry.register(FormatValueCapability())
    engine = ExecutionEngine(registry)
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-a",
                task_id="task-a",
                description="produce rows",
                semantic_verb="read",
                capability_id="produce.rows",
                operation_id="read",
                arguments={"rows": [{"value": 1}, {"value": 2}, {"value": 3}]},
                safety_labels=[],
            ),
            ActionNode(
                id="node-b",
                task_id="task-b",
                description="count rows",
                semantic_verb="analyze",
                capability_id="count.rows",
                operation_id="analyze",
                arguments={"input_ref": InputRef(source_node_id="node-a", expected_data_type="table")},
                depends_on=["node-a"],
                safety_labels=[],
            ),
            ActionNode(
                id="node-c",
                task_id="task-c",
                description="format count",
                semantic_verb="render",
                capability_id="format.value",
                operation_id="render",
                arguments={"input_ref": InputRef(source_node_id="node-b", output_key="value", expected_data_type="scalar")},
                depends_on=["node-b"],
                safety_labels=[],
            ),
        ],
        edges=[("node-a", "node-b"), ("node-b", "node-c")],
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True})

    assert bundle.status == "success"
    assert bundle.results[2].data_preview == {"text": "count=3"}


def test_input_ref_to_failed_node_causes_dependent_skip() -> None:
    registry = CapabilityRegistry()
    registry.register(FailingCapability())
    registry.register(CountRowsCapability())
    engine = ExecutionEngine(registry)
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-fail",
                task_id="task-fail",
                description="fail first",
                semantic_verb="read",
                capability_id="counting.fail",
                operation_id="read",
                arguments={"value": "x"},
                safety_labels=[],
            ),
            ActionNode(
                id="node-b",
                task_id="task-b",
                description="count rows",
                semantic_verb="analyze",
                capability_id="count.rows",
                operation_id="analyze",
                arguments={"input_ref": InputRef(source_node_id="node-fail", expected_data_type="table")},
                depends_on=["node-fail"],
                safety_labels=[],
            ),
        ],
        edges=[("node-fail", "node-b")],
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True, "stop_on_error": False})

    assert bundle.results[0].status == "error"
    assert bundle.results[1].status == "skipped"


def test_input_ref_data_type_mismatch_is_rejected() -> None:
    registry = CapabilityRegistry()
    registry.register(ProduceRowsCapability())
    registry.register(CountRowsCapability())
    engine = ExecutionEngine(registry)
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node-a",
                task_id="task-a",
                description="produce rows",
                semantic_verb="read",
                capability_id="produce.rows",
                operation_id="read",
                arguments={"rows": [{"value": 1}]},
                safety_labels=[],
            ),
            ActionNode(
                id="node-b",
                task_id="task-b",
                description="count rows",
                semantic_verb="analyze",
                capability_id="count.rows",
                operation_id="analyze",
                arguments={"input_ref": InputRef(source_node_id="node-a", expected_data_type="scalar")},
                depends_on=["node-a"],
                safety_labels=[],
            ),
        ],
        edges=[("node-a", "node-b")],
    )

    bundle = engine.execute(_ready_dag(dag), {"confirmation": True, "stop_on_error": False})

    assert bundle.status == "partial"
    assert bundle.results[1].status == "error"
    assert "expected data_type" in (bundle.results[1].error or "")
