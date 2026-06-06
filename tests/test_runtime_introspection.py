from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime.capabilities import build_default_registry
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.core.types import TaskFrame
from agent_runtime.execution.engine import ExecutionEngine
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.input_pipeline.domain_selection import select_capabilities
from agent_runtime.output_pipeline.orchestrator import OutputPipelineOrchestrator


class RuntimeIntrospectionLLMClient:
    """Small fake LLM for runtime introspection flows."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        _ = schema
        if "You are classifying a user prompt" in prompt:
            return {
                "prompt_type": "simple_tool_task",
                "requires_tools": True,
                "likely_domains": ["runtime"],
                "risk_level": "low",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "This is a runtime introspection request.",
                "confidence": 0.92,
                "assumptions": [],
                "domain_evaluations": [
                    {
                        "domain": "runtime",
                        "fits": True,
                        "confidence": 0.95,
                        "reason": "The runtime domain declares capability introspection.",
                    }
                ],
            }
        if "You are decomposing a user prompt" in prompt:
            return {
                "tasks": [
                    {
                        "id": "task-capabilities",
                        "description": "what are my capabilities?",
                        "semantic_verb": "read",
                        "object_type": "capabilities",
                        "intent_confidence": 0.95,
                        "constraints": {},
                        "dependencies": [],
                        "raw_evidence": "what are my capabilities?",
                        "requires_confirmation": False,
                        "risk_level": "low",
                    }
                ],
                "global_constraints": {},
                "unresolved_references": [],
                "assumptions": [],
                "confidence": 0.95,
            }
        if "You are assigning semantic verbs" in prompt:
            return {
                "assignments": [
                    {
                        "task_id": "task-capabilities",
                        "semantic_verb": "read",
                        "object_type": "runtime.capabilities",
                        "intent_confidence": 0.96,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            }
        if "You are selecting capability candidates" in prompt:
            return {
                "task_id": "task-capabilities",
                "evaluations": [
                    {
                        "capability_id": "runtime.describe_capabilities",
                        "operation_id": "describe_capabilities",
                        "fits": True,
                        "confidence": 0.99,
                        "reason": "This task asks for the runtime capability registry.",
                        "domain_reason": "Runtime is the declared domain.",
                        "object_type_reason": "The object is runtime capabilities.",
                        "argument_reason": "No required arguments.",
                        "risk_reason": "Read-only introspection.",
                        "missing_arguments_likely": [],
                    }
                ],
            }
        if "You are assessing whether a selected capability truly fits a task" in prompt:
            return {
                "task_id": "task-capabilities",
                "candidate_capability_id": "runtime.describe_capabilities",
                "candidate_operation_id": "describe_capabilities",
                "proposed_status": "fit",
                "confidence": 0.99,
                "semantic_reason": "This task is explicitly about runtime capabilities.",
                "domain_reason": "Runtime introspection is the correct domain.",
                "object_type_reason": "The object type is runtime capabilities/tools.",
                "argument_reason": "No required arguments are needed.",
                "risk_reason": "Read-only and low risk.",
                "missing_capability_description": None,
                "suggested_domain": "runtime",
                "suggested_object_type": "runtime.capabilities",
                "requires_clarification": False,
                "clarification_question": None,
            }
        if "You are extracting typed arguments" in prompt:
            return {
                "task_id": "task-capabilities",
                "capability_id": "runtime.describe_capabilities",
                "operation_id": "describe_capabilities",
                "arguments": {},
                "missing_required_arguments": [],
                "assumptions": [],
                "confidence": 0.97,
            }
        if "You are selecting a safe display plan" in prompt:
            return {
                "display_type": "table",
                "title": "Runtime Capabilities",
                "sections": [
                    {
                        "title": "Runtime Capabilities",
                        "display_type": "table",
                        "source_node_id": "node::task-capabilities",
                    }
                ],
                "constraints": {},
                "redaction_policy": "standard",
            }
        if "You are reviewing a sanitized action DAG" in prompt:
            return {
                "missing_user_intents": [],
                "suspicious_nodes": [],
                "dependency_warnings": [],
                "output_expectation_warnings": [],
                "recommended_repair": None,
                "confidence": 0.0,
            }
        if "You are critiquing a proposed task decomposition" in prompt:
            return {
                "missing_tasks": [],
                "hallucinated_tasks": [],
                "dependency_issues": [],
                "overly_broad_tasks": [],
                "unsafe_tasks": [],
                "unresolved_references": [],
                "confidence": 0.0,
            }
        raise AssertionError(f"Unexpected prompt: {prompt}")


def _runtime(tmp_path: Path) -> AgentRuntime:
    registry = build_default_registry()
    store = InMemoryResultStore()
    engine = ExecutionEngine(
        registry,
        {
            "workspace_root": str(tmp_path),
            "allow_shell_execution": False,
            "allow_network_operations": False,
            "gateway_url": "http://gateway",
        },
        store,
    )
    return AgentRuntime(
        llm_client=RuntimeIntrospectionLLMClient(),
        registry=registry,
        execution_engine=engine,
        output_orchestrator=OutputPipelineOrchestrator(),
    )


def test_registry_includes_runtime_describe_capabilities() -> None:
    registry = build_default_registry()

    manifest = registry.get("runtime.describe_capabilities").manifest

    assert manifest.domain == "runtime"
    assert manifest.execution_backend == "internal"


def test_registry_includes_agent_monitor_capabilities() -> None:
    registry = build_default_registry()
    planning_ids = {manifest.capability_id for manifest in registry.list_planning_manifests()}

    assert {
        "runtime.start_monitor",
        "runtime.stop_monitor",
        "runtime.inspect_monitors",
    } <= planning_ids
    assert registry.get("runtime.start_monitor").manifest.requires_confirmation is True
    assert registry.get("runtime.inspect_monitors").manifest.read_only is True


def test_runtime_describe_capabilities_returns_grouped_capabilities() -> None:
    registry = build_default_registry()
    capability = registry.get("runtime.describe_capabilities")

    result = capability.execute({"include_details": True}, {"node_id": "node-runtime"})

    assert "grouped_capabilities" in result.data_preview
    assert "operator" in result.data_preview["grouped_capabilities"]
    assert "filesystem" not in result.data_preview["grouped_capabilities"]
    assert any(
        row["capability_id"] == "operator.shell_command"
        for row in result.data_preview["rows"]
    )


def test_what_are_my_capabilities_selects_runtime_describe_capabilities() -> None:
    registry = build_default_registry()
    tasks = [
        TaskFrame(
            id="task-capabilities",
            description="what are my capabilities?",
            semantic_verb="read",
            object_type="capabilities",
            intent_confidence=0.95,
            constraints={},
            dependencies=[],
        )
    ]

    results = select_capabilities(tasks, registry, RuntimeIntrospectionLLMClient())

    assert results[0].selected is not None
    assert results[0].selected.capability_id == "runtime.describe_capabilities"


def test_runtime_capabilities_prompt_renders_registry_data(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    response = runtime.handle_request("what are my capabilities?", {"workspace_root": str(tmp_path)})

    assert "operator.shell_command" in response
    assert "filesystem.list_directory" not in response
    assert "Direct answering without tools is not implemented yet" not in response
    assert "python3 -m gateway_agent.remote_runner" not in response
    assert "git -C" not in response


def test_runtime_describe_pipeline_returns_pipeline_stages() -> None:
    registry = build_default_registry()
    capability = registry.get("runtime.describe_pipeline")

    result = capability.execute({}, {"node_id": "node-runtime"})

    rows = result.data_preview["rows"]
    assert any(row["stage"] == "prompt_classification" for row in rows)
    assert any(row["stage"] == "execution" for row in rows)
    assert any(row["type"].startswith("llm_assisted") for row in rows)


def test_runtime_show_last_plan_returns_empty_state_when_none_exists() -> None:
    registry = build_default_registry()
    capability = registry.get("runtime.show_last_plan")

    result = capability.execute({}, {"node_id": "node-runtime", "runtime_state": {}})

    assert result.data_preview["available"] is False
    assert "No previous plan" in result.data_preview["message"]


def test_runtime_explain_last_failure_returns_empty_state_when_none_exists() -> None:
    registry = build_default_registry()
    capability = registry.get("runtime.explain_last_failure")

    result = capability.execute({}, {"node_id": "node-runtime", "runtime_state": {}})

    assert result.data_preview["available"] is False
    assert "No previous failure" in result.data_preview["message"]
