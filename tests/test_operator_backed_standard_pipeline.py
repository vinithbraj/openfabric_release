from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_runtime.capabilities import build_default_registry
from agent_runtime.core.config import RuntimeConfig
from agent_runtime.core.errors import ValidationError
from agent_runtime.core.types import ActionDAG, ActionNode, CapabilityRef, TaskFrame, UserRequest
from agent_runtime.execution.engine import ExecutionEngine
from agent_runtime.execution.gateway_client import GatewayClient
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.execution.safety import evaluate_dag_safety
from agent_runtime.input_pipeline.argument_extraction import ArgumentExtractionResult
from agent_runtime.input_pipeline.dag_builder import build_action_dag
from agent_runtime.input_pipeline.dataflow_planning import plan_dataflow
from agent_runtime.input_pipeline.decomposition import DecompositionResult
from agent_runtime.input_pipeline.domain_selection import select_capabilities
from agent_runtime.input_pipeline.domain_selection import CapabilitySelectionResult
from agent_runtime.input_pipeline.argument_extraction import (
    _ArgumentExtractionResponse,
    _apply_deterministic_normalization,
    _build_argument_prompt,
    _validate_operator_arguments,
    extract_arguments,
)
from agent_runtime.input_pipeline.capability_fit import CapabilityFitDecision, finalize_capability_fit
from agent_runtime.llm.proposals import CapabilityFitProposal
from agent_runtime.llm.reproducibility import hash_action_dag
from agent_runtime.operator.models import OperatorAction, OperatorPlan
from agent_runtime.operator.pipeline import OperatorPlanValidator
from agent_runtime.operator.action_runtime import operator_action_requires_confirmation


class FakeSelectionLLM:
    model = "fake-selection"
    temperature = 0.0

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        assert "filesystem.search_files" not in prompt
        assert "operator.shell_command" in prompt
        return {
            "task_id": "task_1",
            "evaluations": [
                {
                    "capability_id": "operator.shell_command",
                    "operation_id": "shell_command",
                    "fits": True,
                    "confidence": 0.96,
                    "reason": "A concrete shell command can perform this ordinary file task.",
                },
                {
                    "capability_id": "operator.python_transform",
                    "operation_id": "python_transform",
                    "fits": False,
                    "confidence": 0.7,
                    "reason": "No upstream data is available to transform.",
                },
            ],
            "unresolved_reason": None,
        }


class FakeTransformSelectionLLM:
    model = "fake-transform-selection"
    temperature = 0.0

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        assert "operator.shell_command" in prompt
        assert "operator.python_transform" in prompt
        return {
            "task_id": "task_3",
            "evaluations": [
                {
                    "capability_id": "operator.shell_command",
                    "operation_id": "shell_command",
                    "fits": False,
                    "confidence": 0.7,
                    "reason": "This step should shape previously listed branch output, not run a new command.",
                },
                {
                    "capability_id": "operator.python_transform",
                    "operation_id": "python_transform",
                    "fits": True,
                    "confidence": 0.95,
                    "reason": "A Python transform can extract and filter branch names from upstream text.",
                },
            ],
            "unresolved_reason": None,
        }


class FailingSelectionLLM:
    model = "failing-selection"
    temperature = 0.0

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("capability selection LLM should have been skipped")


def test_execution_context_gateway_default_cwd_sets_operator_workspace(tmp_path: Path) -> None:
    host_workspace = tmp_path / "host-workspace"
    host_workspace.mkdir()
    engine = ExecutionEngine(
        build_default_registry(),
        RuntimeConfig(workspace_root="/data", allow_shell_execution=True),
        InMemoryResultStore(),
    )

    effective_config = engine._effective_config_for_context(
        {"gateway_default_cwd": str(host_workspace)}
    )
    validator = OperatorPlanValidator(effective_config)
    action = OperatorAction.model_validate(
        {
            "action_id": "action_1",
            "task_id": "task_1",
            "kind": "shell_command",
            "command": "pwd",
            "cwd": ".",
            "risk": "low",
            "effect_intent": "read_only",
            "reason": "Read cwd.",
        }
    )

    assert effective_config.workspace_root == str(host_workspace)
    assert validator.resolved_cwd(action) == host_workspace


class RejectingSelectionLLM:
    model = "rejecting-selection"
    temperature = 0.0

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        _ = schema
        assert "operator.shell_command" in prompt
        return {
            "task_id": "task_1",
            "evaluations": [
                {
                    "capability_id": "operator.shell_command",
                    "operation_id": "shell_command",
                    "fits": False,
                    "confidence": 0.9,
                    "reason": "The LLM incorrectly rejected the broad operator backend.",
                },
                {
                    "capability_id": "operator.python_transform",
                    "operation_id": "python_transform",
                    "fits": False,
                    "confidence": 0.9,
                    "reason": "The LLM incorrectly rejected the broad operator backend.",
                },
            ],
            "unresolved_reason": "No shortlisted capability was accepted.",
        }


class FailingDataflowLLM:
    model = "failing-dataflow"
    temperature = 0.0

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("legacy dataflow LLM should have been skipped for operator tasks")


class QueueArgumentLLM:
    model = "queue-argument"
    temperature = 0.0

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        _ = schema
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("argument extraction LLM received an unexpected extra call")
        return self.responses.pop(0)


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"command": command, "cwd": cwd, "context": dict(execution_context or {})})
        return {"stdout": "README.md\n", "stderr": "", "exit_code": 0, "gateway_node": "fake"}


class StandardZeroLikeGateway(FakeGateway):
    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"command": command, "cwd": cwd, "context": dict(execution_context or {})})
        if len(self.calls) == 1:
            return {
                "stdout": "postgres:15 633MB\nalpine:latest 13.1MB\nvllm/vllm-openai:latest 32.2GB\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        return {
            "stdout": "Total space consumed by Docker images: 0.00 MB\n",
            "stderr": "",
            "exit_code": 0,
            "gateway_node": "fake",
        }


def _execution_ready_dag(dag: ActionDAG, config: RuntimeConfig, registry) -> ActionDAG:
    decision = evaluate_dag_safety(dag, registry, config)
    assert decision.allowed is True
    prepared = dag.model_copy(
        update={
            "requires_confirmation": decision.requires_confirmation,
            "execution_ready": True,
            "safety_decision": decision.model_dump(mode="json"),
        }
    )
    return prepared.model_copy(update={"final_dag_hash": hash_action_dag(prepared)})


def test_default_llm_visible_registry_contains_only_runtime_and_operator() -> None:
    registry = build_default_registry()
    planning_ids = {manifest.capability_id for manifest in registry.list_planning_manifests()}

    assert "operator.shell_command" in planning_ids
    assert "operator.python_transform" in planning_ids
    assert "runtime.describe_capabilities" in planning_ids
    assert "filesystem.search_files" not in planning_ids
    assert "system.memory_status" not in planning_ids
    assert "sql.read_query" not in planning_ids


def test_operator_shell_manifest_covers_local_ip_inspection() -> None:
    manifest = build_default_registry().get("operator.shell_command").manifest

    assert "ip_address" in manifest.object_types
    assert "network.interface" in manifest.object_types
    assert "network_inspection" in manifest.semantic_tags
    assert any("IP address" in example.get("prompt", "") for example in manifest.examples)


def test_operator_shell_manifest_covers_git_repository_tasks() -> None:
    manifest = build_default_registry().get("operator.shell_command").manifest

    assert "git.repository" in manifest.object_types
    assert "git.status" in manifest.object_types
    assert "source_control" in manifest.semantic_tags
    assert any("git status" in example.get("prompt", "") for example in manifest.examples)


def test_operator_shell_manifest_covers_docker_container_inspection() -> None:
    manifest = build_default_registry().get("operator.shell_command").manifest

    assert "docker.container" in manifest.object_types
    assert "container_inspection" in manifest.semantic_tags
    assert any("Docker container" in example.get("prompt", "") for example in manifest.examples)
    assert "machine-readable CLI output" in manifest.argument_schema["command"]["description"]
    assert "stable ids or exact names" in manifest.argument_schema["command"]["description"]
    assert "exit 0 for both outcomes" in manifest.argument_schema["command"]["description"]
    assert any(
        "docker system df -v --format json" in example.get("arguments", {}).get("command", "")
        for example in manifest.examples
    )
    assert any(
        "grep -iF NAME || true" in example.get("arguments", {}).get("command", "")
        for example in manifest.examples
    )


def test_operator_python_manifest_covers_git_data_shaping() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest

    assert "search" in manifest.semantic_verbs
    assert "calculate" in manifest.semantic_verbs
    assert "count" in manifest.semantic_verbs
    assert "filter" in manifest.semantic_verbs
    assert "sort" in manifest.semantic_verbs
    assert "git.branch" in manifest.object_types
    assert "data_shaping" in manifest.semantic_tags
    assert "source_control" in manifest.semantic_tags
    assert "def transform(inputs)" in manifest.argument_schema["code"]["description"]
    assert any("branch names" in example.get("prompt", "") for example in manifest.examples)


def test_operator_python_manifest_covers_datetime_uptime_transforms() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest

    assert "datetime" in manifest.semantic_tags
    assert "uptime_days" in manifest.semantic_tags
    assert any("uptime in days" in example.get("prompt", "") for example in manifest.examples)


def test_operator_python_manifest_covers_docker_data_shaping_variants() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest

    assert "docker.container" in manifest.object_types
    assert "docker_container" in manifest.object_types
    assert "container_name" in manifest.object_types
    assert "docker.image" in manifest.object_types
    assert "docker_image" in manifest.object_types


def test_operator_python_transform_fit_accepts_docker_container_variants() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest
    candidate = CapabilityRef(
        capability_id="operator.python_transform",
        operation_id="python_transform",
        confidence=0.96,
        reason="Filter upstream Docker container output.",
    )

    for object_type in ("docker.container", "docker_container", "docker container", "container_name"):
        task = TaskFrame(
            id="task_2",
            description="Search upstream Docker container output for pgadmin",
            semantic_verb="search",
            object_type=object_type,
            intent_confidence=0.95,
            dependencies=["task_1"],
        )
        llm_fit = CapabilityFitProposal(
            task_id=task.id,
            candidate_capability_id=candidate.capability_id,
            candidate_operation_id=candidate.operation_id,
            fits=True,
            confidence=0.95,
            primary_failure_mode=None,
            semantic_reason="The transform filters prior command output.",
            domain_reason="Docker output can be shaped by the generic operator transform.",
            object_type_reason="Docker container labels are covered by operator transform metadata.",
            argument_reason="Code and input bindings are authored during argument extraction.",
            risk_reason="The transform is constrained and approval gated.",
        )

        decision = finalize_capability_fit(
            task,
            candidate,
            manifest,
            llm_fit,
            classification_context={"likely_domains": ["docker", "operator"]},
        )

        assert decision.is_fit is True
        assert decision.deterministic_rejections == []


def test_operator_fit_accepts_generic_backend_despite_llm_object_rejection() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest
    task = TaskFrame(
        id="task_2",
        description="Search upstream Docker container output for pgadmin",
        semantic_verb="search",
        object_type="docker.container",
        intent_confidence=0.95,
        dependencies=["task_1"],
    )
    candidate = CapabilityRef(
        capability_id="operator.python_transform",
        operation_id="python_transform",
        confidence=0.96,
        reason="Filter upstream Docker container output.",
    )
    llm_fit = CapabilityFitProposal(
        task_id=task.id,
        candidate_capability_id=candidate.capability_id,
        candidate_operation_id=candidate.operation_id,
        fits=False,
        confidence=0.95,
        primary_failure_mode="object_type_mismatch",
        semantic_reason="The LLM incorrectly treated operator transforms as narrow object handlers.",
        domain_reason="",
        object_type_reason="",
        argument_reason="",
        risk_reason="",
    )

    decision = finalize_capability_fit(
        task,
        candidate,
        manifest,
        llm_fit,
        classification_context={"likely_domains": ["docker", "operator"]},
    )

    assert decision.is_fit is True
    assert decision.status == "fit"
    assert decision.deterministic_rejections == []
    assert any("Generic operator execution manifest compatibility" in reason for reason in decision.reasons)


def test_operator_shell_manifest_covers_calculation_tasks() -> None:
    manifest = build_default_registry().get("operator.shell_command").manifest

    assert "calculate" in manifest.semantic_verbs
    assert "count" in manifest.semantic_verbs
    assert "filter" in manifest.semantic_verbs
    assert "sort" in manifest.semantic_verbs


def test_operator_confirmation_requirement_does_not_make_fit_fail() -> None:
    registry = build_default_registry()
    manifest = registry.get("operator.shell_command").manifest
    task = TaskFrame(
        id="task_1",
        description="Check for a Docker container named pgadmin",
        semantic_verb="execute",
        object_type="command",
        intent_confidence=0.95,
        constraints={"command": "docker ps -a --format '{{.Names}}'"},
        requires_confirmation=False,
    )
    candidate = CapabilityRef(
        capability_id="operator.shell_command",
        operation_id="shell_command",
        confidence=0.98,
        reason="The task already has a concrete operator command.",
    )
    llm_fit = CapabilityFitProposal(
        task_id=task.id,
        candidate_capability_id=candidate.capability_id,
        candidate_operation_id=candidate.operation_id,
        fits=True,
        confidence=0.95,
        primary_failure_mode=None,
        semantic_reason="Shell command can inspect Docker containers.",
        domain_reason="Operator shell command handles command-line inspection.",
        object_type_reason="Command object is supported.",
        argument_reason="The command argument is present.",
        risk_reason="Runtime safety will require confirmation.",
    )

    decision = finalize_capability_fit(task, candidate, manifest, llm_fit)

    assert decision.is_fit is True
    assert not any("requires confirmation" in reason for reason in decision.deterministic_rejections)


def test_ordinary_task_selects_operator_shell_from_planning_view() -> None:
    registry = build_default_registry().planning_view()
    task = TaskFrame(
        id="task_1",
        description="list all *.txt files",
        semantic_verb="search",
        object_type="filesystem.file",
        intent_confidence=0.95,
        constraints={},
        raw_evidence="list all *.txt files",
    )

    results = select_capabilities(
        [task],
        registry,
        FakeSelectionLLM(),
        classification_context={"likely_domains": ["operator"]},
    )

    assert results[0].selected is not None
    assert results[0].selected.capability_id == "operator.shell_command"


def test_git_task_selects_operator_shell_from_planning_view() -> None:
    registry = build_default_registry().planning_view()
    task = TaskFrame(
        id="task_1",
        description="show git status",
        semantic_verb="read",
        object_type="git.repository",
        intent_confidence=0.95,
        constraints={},
        raw_evidence="show git status",
    )

    results = select_capabilities(
        [task],
        registry,
        FakeSelectionLLM(),
        classification_context={"likely_domains": ["operator"]},
    )

    assert results[0].selected is not None
    assert results[0].selected.capability_id == "operator.shell_command"


def test_git_branch_filter_task_selects_operator_python_transform() -> None:
    registry = build_default_registry().planning_view()
    task = TaskFrame(
        id="task_3",
        description="extract branch names and select a branch containing distil",
        semantic_verb="search",
        object_type="git.branch",
        intent_confidence=0.95,
        constraints={"input_source": "task_2"},
        dependencies=["task_2"],
        raw_evidence="extract the branch names and check for a branch name that has distil",
    )

    results = select_capabilities(
        [task],
        registry,
        FakeTransformSelectionLLM(),
        classification_context={"likely_domains": ["git", "operator"]},
    )

    assert results[0].selected is not None
    assert results[0].selected.capability_id == "operator.python_transform"


def test_concrete_operator_command_skips_advisory_capability_selection() -> None:
    registry = build_default_registry().planning_view()
    task = TaskFrame(
        id="task_1",
        description="Determine which branch the current folder is on",
        semantic_verb="read",
        object_type="git.branch",
        intent_confidence=0.95,
        constraints={"command": "git branch --show-current"},
        raw_evidence="which branch is this folder on",
    )

    results = select_capabilities(
        [task],
        registry,
        FailingSelectionLLM(),
        classification_context={"likely_domains": ["git", "operator"]},
    )

    assert results[0].selected is not None
    assert results[0].selected.capability_id == "operator.shell_command"
    assert results[0].selected.operation_id == "shell_command"


def test_concrete_operator_code_skips_advisory_capability_selection() -> None:
    registry = build_default_registry().planning_view()
    task = TaskFrame(
        id="task_4",
        description="Find a branch name containing distil",
        semantic_verb="search",
        object_type="git.branch",
        intent_confidence=0.95,
        constraints={
            "code": (
                "def transform(inputs):\n"
                "    branches = inputs.get('branches') or []\n"
                "    return [branch for branch in branches if 'distil' in str(branch).lower()]"
            )
        },
        dependencies=["task_3"],
        raw_evidence="check for a branch name that has distil",
    )

    results = select_capabilities(
        [task],
        registry,
        FailingSelectionLLM(),
        classification_context={"likely_domains": ["git", "operator"]},
    )

    assert results[0].selected is not None
    assert results[0].selected.capability_id == "operator.python_transform"
    assert results[0].selected.operation_id == "python_transform"


def test_dependent_operator_calculation_uses_llm_capability_selection() -> None:
    registry = build_default_registry().planning_view()
    task = TaskFrame(
        id="task_3",
        description="Calculate the uptime of the container in days",
        semantic_verb="calculate",
        object_type="system.uptime",
        intent_confidence=0.95,
        constraints={},
        dependencies=["task_2"],
        raw_evidence="give me the total uptime of that container in days",
    )

    results = select_capabilities(
        [task],
        registry,
        FakeTransformSelectionLLM(),
        classification_context={"likely_domains": ["operator"]},
    )

    assert results[0].selected is not None
    assert results[0].selected.capability_id == "operator.python_transform"
    assert results[0].selected.operation_id == "python_transform"


def test_operator_selection_falls_back_to_generic_backend_when_llm_rejects_all() -> None:
    registry = build_default_registry().planning_view()
    task = TaskFrame(
        id="task_1",
        description="Check whether a docker container named pgadmin exists",
        semantic_verb="search",
        object_type="docker.container",
        intent_confidence=0.95,
        constraints={},
        raw_evidence="check if there is a docker container named pgadmin",
    )

    results = select_capabilities(
        [task],
        registry,
        RejectingSelectionLLM(),
        classification_context={"likely_domains": ["docker", "operator"]},
    )

    assert results[0].selected is not None
    assert results[0].selected.capability_id == "operator.shell_command"
    assert "operator fallback" in results[0].selected.reason.lower()


def test_operator_python_argument_prompt_requires_complete_transform_function() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest
    upstream = TaskFrame(
        id="task_2",
        description="Check Docker image disk usage",
        semantic_verb="execute",
        object_type="docker.image",
        intent_confidence=0.95,
        constraints={"command": "docker system df -v"},
    )
    task = TaskFrame(
        id="task_3",
        description="Calculate the exact space used by Docker images and remove output noise",
        semantic_verb="calculate",
        object_type="text",
        intent_confidence=0.95,
        constraints={},
        dependencies=["task_2"],
    )

    prompt = _build_argument_prompt(task, manifest, task_index={"task_2": upstream})

    assert "Always include every top-level response field required by the schema" in prompt
    assert "confidence must be a number from 0.0 to 1.0" in prompt
    assert "depends on previous task output, so do not author arguments.code now" in prompt
    assert "Set arguments.defer_code_generation to true" in prompt
    assert "execution stage will ask for def transform(inputs):" in prompt
    assert "Do not guess columns, units, table layout, JSON shape, or text format" in prompt
    assert "input_bindings must source from the task frame dependencies" in prompt
    assert "recompute that lookup inside the same concrete command" in prompt
    assert "'dependencies': ['task_2']" in prompt
    assert "Dependency task context:" in prompt
    assert "Downstream dependent task context:" in prompt
    assert "Check Docker image disk usage" in prompt


def test_operator_shell_argument_prompt_includes_downstream_context() -> None:
    manifest = build_default_registry().get("operator.shell_command").manifest
    producer = TaskFrame(
        id="task_1",
        description="Check if pgadmin container exists",
        semantic_verb="search",
        object_type="docker.container",
        intent_confidence=0.95,
        constraints={"container_name": "pgadmin"},
    )
    consumer = TaskFrame(
        id="task_2",
        description="Calculate uptime in days from the container data",
        semantic_verb="calculate",
        object_type="system.uptime",
        intent_confidence=0.95,
        dependencies=["task_1"],
    )

    prompt = _build_argument_prompt(
        producer,
        manifest,
        task_index={"task_1": producer, "task_2": consumer},
    )

    assert "Downstream dependent task context:" in prompt
    assert "Calculate uptime in days from the container data" in prompt
    assert "command output that contains the raw fields those downstream tasks need" in prompt
    assert "do not output only an identifier/name" in prompt
    assert "arguments.command must print the final requested result" in prompt
    assert "Do not put xargs replacement tokens" in prompt
    assert "confidence must be a number from 0.0 to 1.0" in prompt


def test_operator_python_argument_validation_rejects_bare_code() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest

    errors = _validate_operator_arguments(
        manifest,
        {
            "code": (
                "import datetime\n"
                "started = str(inputs.get('started_at', '')).strip()\n"
                "return started"
            ),
            "input_bindings": [
                {
                    "input_name": "started_at",
                    "source_action_id": "task_2",
                    "source_field": "stdout",
                }
            ],
        },
    )

    assert any("python_transform_contract_shape" in error for error in errors)
    assert any("missing_transform_function" in error for error in errors)


def test_operator_python_argument_validation_rejects_top_level_import_before_function() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest

    errors = _validate_operator_arguments(
        manifest,
        {
            "code": (
                "import datetime\n"
                "def transform(inputs):\n"
                "    return inputs.get('started_at', '')"
            ),
            "input_bindings": [
                {
                    "input_name": "started_at",
                    "source_action_id": "task_2",
                    "source_field": "stdout",
                }
            ],
        },
    )

    assert any("python_transform_contract_shape" in error for error in errors)


def test_operator_python_argument_validation_rejects_unbound_input_key() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest

    errors = _validate_operator_arguments(
        manifest,
        {
            "code": "def transform(inputs):\n    return inputs.get('container_info', {})",
            "input_bindings": [
                {
                    "input_name": "stdout",
                    "source_action_id": "task_2",
                    "source_field": "stdout",
                }
            ],
        },
    )

    assert any("python_input_unbound" in error for error in errors)
    assert any("container_info" in error for error in errors)


def test_operator_python_transform_allows_broad_exception_handlers_by_contract(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Transform with hidden error handling.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Parse output",
                    "semantic_verb": "transform",
                    "object_type": "text",
                    "dependencies": [],
                    "reason": "Parse.",
                }
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "python_transform",
                    "code": (
                        "def transform(inputs):\n"
                        "    try:\n"
                        "        return inputs['stdout'].strip()\n"
                        "    except Exception as exc:\n"
                        "        return 'Error: ' + str(exc)\n"
                    ),
                    "cwd": ".",
                    "inputs": {"stdout": "ok"},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Swallow errors.",
                    "depends_on": [],
                }
            ],
            "dependencies": [],
            "expected_outputs": ["parsed"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert not [error for error in errors if error["error"] == "python_broad_exception_handler_blocked"]
    assert errors == []


def test_operator_python_argument_validation_rejects_source_outside_task_dependencies() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest
    task = TaskFrame(
        id="task_3",
        description="Calculate uptime",
        semantic_verb="calculate",
        object_type="docker.container",
        intent_confidence=0.9,
        dependencies=["task_2"],
    )

    errors = _validate_operator_arguments(
        manifest,
        {
            "code": "def transform(inputs):\n    return inputs.get('started_at', '')",
            "input_bindings": [
                {
                    "input_name": "started_at",
                    "source_action_id": "task_1",
                    "source_field": "stdout",
                }
            ],
        },
        task,
    )

    assert any("input_binding_source_not_declared_dependency" in error for error in errors)
    assert any("task_2" in error for error in errors)


def test_operator_python_transform_defaults_stdout_binding_from_single_dependency(tmp_path: Path) -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest
    task = TaskFrame(
        id="task_3",
        description="Calculate uptime in days",
        semantic_verb="calculate",
        object_type="system.uptime",
        intent_confidence=0.95,
        dependencies=["task_2"],
    )
    response = _ArgumentExtractionResponse(
        task_id=task.id,
        capability_id=manifest.capability_id,
        operation_id=manifest.operation_id,
        arguments={
            "code": (
                "def transform(inputs):\n"
                "    return inputs['stdout'].strip()"
            )
        },
        confidence=0.9,
    )

    result = _apply_deterministic_normalization(task, manifest, response, tmp_path)

    assert result.arguments["input_bindings"] == [
        {
            "input_name": "stdout",
            "source_action_id": "task_2",
            "source_field": "stdout",
            "required": True,
            "fallback_value": None,
        }
    ]
    assert any("input_bindings" in assumption for assumption in result.assumptions)


def test_operator_python_with_upstream_defers_code_until_execution() -> None:
    registry = build_default_registry()
    task = TaskFrame(
        id="task_2",
        description="Calculate the uptime of the pgadmin container in days",
        semantic_verb="calculate",
        object_type="docker.container",
        intent_confidence=0.95,
        constraints={},
        dependencies=["task_1"],
    )
    selected = CapabilityRef(
        capability_id="operator.python_transform",
        operation_id="python_transform",
        confidence=0.95,
        reason="Use a Python transform to calculate from upstream Docker output.",
    )
    llm = QueueArgumentLLM(
        {
            "task_id": "task_2",
            "capability_id": "operator.python_transform",
            "operation_id": "python_transform",
            "arguments": {},
            "missing_required_arguments": ["code"],
            "assumptions": ["Need prior output."],
            "confidence": 0.7,
        },
        {
            "task_id": "task_2",
            "capability_id": "operator.python_transform",
            "operation_id": "python_transform",
            "arguments": {
                "code": "def transform(inputs):\n    return inputs['stdout'].strip()"
            },
            "missing_required_arguments": [],
            "assumptions": [],
            "confidence": 0.95,
        },
    )

    results = extract_arguments(
        [task],
        [CapabilitySelectionResult(task_id="task_2", selected=selected)],
        registry,
        llm,
        n_best=2,
    )

    assert len(llm.prompts) == 1
    assert results[0].missing_required_arguments == []
    assert "code" not in results[0].arguments
    assert results[0].arguments["defer_code_generation"] is True
    assert results[0].arguments["input_bindings"] == [
        {
            "input_name": "stdout",
            "source_action_id": "task_1",
            "source_field": "stdout",
            "required": True,
            "fallback_value": None,
        }
    ]


def test_operator_shell_input_bindings_retry_before_safety() -> None:
    registry = build_default_registry()
    task = TaskFrame(
        id="task_2",
        description="Show the contents of the first requirements.txt file found",
        semantic_verb="read",
        object_type="file",
        intent_confidence=0.95,
        constraints={},
        dependencies=["task_1"],
    )
    selected = CapabilityRef(
        capability_id="operator.shell_command",
        operation_id="shell_command",
        confidence=0.95,
        reason="Use a shell command.",
    )
    llm = QueueArgumentLLM(
        {
            "task_id": "task_2",
            "capability_id": "operator.shell_command",
            "operation_id": "shell_command",
            "arguments": {
                "command": "cat \"$stdout\"",
                "input_bindings": [{"source_action_id": "task_1", "output_name": "stdout"}],
            },
            "missing_required_arguments": [],
            "assumptions": [],
            "confidence": 0.7,
        },
        {
            "task_id": "task_2",
            "capability_id": "operator.shell_command",
            "operation_id": "shell_command",
            "arguments": {
                "command": "find . -name requirements.txt -type f | sort | head -n 1 | xargs -r sed -n '1,200p'",
                "input_bindings": [],
            },
            "missing_required_arguments": [],
            "assumptions": [],
            "confidence": 0.95,
        },
    )

    results = extract_arguments(
        [task],
        [CapabilitySelectionResult(task_id="task_2", selected=selected)],
        registry,
        llm,
        n_best=2,
    )

    assert len(llm.prompts) == 1
    assert results[0].missing_required_arguments == []
    assert results[0].arguments["input_bindings"] == [
        {
            "input_name": "stdout",
            "source_action_id": "task_1",
            "source_field": "stdout",
            "required": True,
            "fallback_value": None,
        }
    ]
    assert results[0].arguments["command"] == 'cat "$stdout"'


def test_operator_shell_calculation_arguments_are_llm_reviewed_before_approval() -> None:
    registry = build_default_registry()
    task = TaskFrame(
        id="task_2",
        description="Calculate the uptime for the pgadmin container and provide that uptime in days",
        semantic_verb="calculate",
        object_type="docker.container",
        intent_confidence=0.95,
        constraints={"container_name": "pgadmin"},
    )
    selected = CapabilityRef(
        capability_id="operator.shell_command",
        operation_id="shell_command",
        confidence=0.95,
        reason="Use a shell command.",
    )
    bad_command = "docker inspect --format='{{.State.StartedAt}}' pgadmin"
    fixed_command = (
        "started=$(docker inspect --format='{{.State.StartedAt}}' pgadmin); "
        "start_epoch=$(date -u -d \"$started\" +%s); "
        "now_epoch=$(date -u +%s); "
        "echo $(( (now_epoch - start_epoch) / 86400 ))"
    )
    llm = QueueArgumentLLM(
        {
            "task_id": "task_2",
            "capability_id": "operator.shell_command",
            "operation_id": "shell_command",
            "arguments": {"command": bad_command},
            "missing_required_arguments": [],
            "assumptions": [],
            "confidence": 0.9,
        },
        {
            "accepted": False,
            "corrected_arguments": {"command": fixed_command},
            "feedback": [
                "The original command prints only an intermediate timestamp instead of the requested uptime in days."
            ],
            "reason": "The original command is not executable as written.",
            "confidence": 0.95,
        },
    )

    results = extract_arguments(
        [task],
        [CapabilitySelectionResult(task_id="task_2", selected=selected)],
        registry,
        llm,
        n_best=1,
    )

    assert len(llm.prompts) == 2
    assert "auditing one argument payload" in llm.prompts[1]
    assert "xargs/template tokens" in llm.prompts[1]
    assert results[0].missing_required_arguments == []
    assert results[0].arguments["command"] == fixed_command
    assert any("self-review" in assumption for assumption in results[0].assumptions)


def test_operator_shell_upstream_for_calculation_is_llm_reviewed() -> None:
    registry = build_default_registry()
    producer = TaskFrame(
        id="task_1",
        description="Check if pgadmin container exists",
        semantic_verb="search",
        object_type="docker.container",
        intent_confidence=0.95,
        constraints={"container_name": "pgadmin"},
    )
    consumer = TaskFrame(
        id="task_2",
        description="Calculate uptime in days from the container data",
        semantic_verb="calculate",
        object_type="system.uptime",
        intent_confidence=0.95,
        dependencies=["task_1"],
    )
    selected = CapabilityRef(
        capability_id="operator.shell_command",
        operation_id="shell_command",
        confidence=0.95,
        reason="Use a shell command.",
    )
    fixed_command = "docker inspect --format '{{.Name}} {{.State.StartedAt}}' pgadmin"
    llm = QueueArgumentLLM(
        {
            "task_id": "task_1",
            "capability_id": "operator.shell_command",
            "operation_id": "shell_command",
            "arguments": {"command": "docker ps --filter name=pgadmin --format '{{.Names}}'"},
            "generated_arguments": [],
            "missing_required_arguments": [],
            "assumptions": [],
            "confidence": 0.9,
        },
        {
            "accepted": False,
            "corrected_arguments": {"command": fixed_command},
            "feedback": [
                "The downstream uptime calculation needs a start timestamp, not only the container name."
            ],
            "reason": "The original stdout is insufficient for the dependent calculation.",
            "confidence": 0.95,
        },
    )

    results = extract_arguments(
        [producer, consumer],
        [CapabilitySelectionResult(task_id="task_1", selected=selected)],
        registry,
        llm,
        n_best=1,
    )

    assert len(llm.prompts) == 2
    assert "If a downstream task computes from this action" in llm.prompts[1]
    assert results[0].arguments["command"] == fixed_command
    assert any("self-review" in assumption for assumption in results[0].assumptions)


def test_operator_input_binding_shape_is_canonicalized_at_model_boundary() -> None:
    action = OperatorAction.model_validate(
        {
            "action_id": "action_2",
            "task_id": "task_2",
            "kind": "python_transform",
            "code": "def transform(inputs):\n    return inputs['stdout'].strip()",
            "cwd": ".",
            "inputs": {},
            "input_bindings": [
                {
                    "source_task_id": "action_1",
                    "output_name": "stdout",
                }
            ],
            "declared_output_shape": "text",
            "risk": "low",
            "timeout_seconds": 5,
            "reason": "Transform upstream output.",
            "depends_on": [],
        }
    )

    assert action.input_bindings[0].input_name == "stdout"
    assert action.input_bindings[0].source_action_id == "action_1"
    assert action.input_bindings[0].source_field == "stdout"
    assert action.input_bindings[0].required is True


def test_standard_operator_capability_returns_normalized_binding_arguments() -> None:
    capability = build_default_registry().get("operator.python_transform")

    arguments = capability.validate_arguments(
        {
            "code": "def transform(inputs):\n    return inputs['stdout'].strip()",
            "input_bindings": [
                {
                    "source_action_id": "task_1",
                    "output_name": "stdout",
                }
            ],
        }
    )

    assert arguments["input_bindings"] == [
        {
            "input_name": "stdout",
            "source_action_id": "task_1",
            "source_field": "stdout",
            "required": True,
            "fallback_value": None,
        }
    ]


def test_standard_registry_exposes_gateway_python_action() -> None:
    capability = build_default_registry().get("operator.python_action")

    arguments = capability.validate_arguments(
        {
            "code": "def main(inputs):\n    import subprocess\n    return subprocess.check_output(['printf', 'ok'], text=True)",
            "reason": "Run one Python action.",
        }
    )

    assert arguments["code"].startswith("def main(inputs):")
    assert arguments["declared_output_shape"] == "text"
    medium_risk_action = OperatorAction.model_validate(
        {
            "action_id": "action_1",
            "task_id": "task_1",
            "kind": "python_action",
            "code": arguments["code"],
            "cwd": ".",
            "inputs": {},
            "input_bindings": [],
            "declared_output_shape": "text",
            "risk": "medium",
            "reason": "Run one Python action.",
        }
    )
    high_risk_action = medium_risk_action.model_copy(update={"risk": "high"})

    assert operator_action_requires_confirmation(medium_risk_action) is False
    assert operator_action_requires_confirmation(high_risk_action) is True


def test_operator_python_action_argument_validation_allows_subprocess() -> None:
    errors = _validate_operator_arguments(
        build_default_registry().get("operator.python_action").manifest,
        {
            "code": (
                "def main(inputs):\n"
                "    import subprocess\n"
                "    return subprocess.check_output(['printf', 'ok'], text=True)"
            )
        },
    )

    assert errors == []


def test_operator_tasks_skip_legacy_dataflow_planning() -> None:
    registry = build_default_registry()
    tasks = [
        TaskFrame(
            id="task_1",
            description="show current git branch",
            semantic_verb="read",
            object_type="git.branch",
            intent_confidence=0.95,
        ),
        TaskFrame(
            id="task_2",
            description="list git branches",
            semantic_verb="read",
            object_type="git.branch",
            intent_confidence=0.95,
        ),
    ]
    selected = CapabilityRef(
        capability_id="operator.shell_command",
        operation_id="shell_command",
        confidence=0.95,
        reason="Use shell command.",
    )

    validated = plan_dataflow(
        original_prompt="which branch is this folder on and list all the branches?",
        tasks=tasks,
        capability_selections=[
            CapabilitySelectionResult(task_id="task_1", selected=selected),
            CapabilitySelectionResult(task_id="task_2", selected=selected),
        ],
        registry=registry,
        llm_client=FailingDataflowLLM(),
    )

    assert validated.refs == []
    assert validated.bindings == []
    assert validated.derived_tasks == []
    assert validated.rejected_bindings == []


def test_dag_construction_reports_filtered_upstream_dependency() -> None:
    registry = build_default_registry()
    selected = CapabilityRef(
        capability_id="operator.shell_command",
        operation_id="shell_command",
        confidence=0.95,
        reason="Use a concrete shell command.",
    )
    task_1 = TaskFrame(
        id="task_1",
        description="List Docker containers",
        semantic_verb="search",
        object_type="docker.container",
        intent_confidence=0.95,
    )
    task_2 = TaskFrame(
        id="task_2",
        description="Inspect the pgadmin container",
        semantic_verb="read",
        object_type="docker.container",
        intent_confidence=0.95,
        dependencies=["task_1"],
    )
    fit_decision = CapabilityFitDecision(
        task_id="task_1",
        candidate_capability_id="operator.shell_command",
        candidate_operation_id="shell_command",
        status="semantic_mismatch",
        confidence=0.9,
        reasons=["The upstream command task was rejected by capability fit."],
        deterministic_rejections=["task object type is incompatible with candidate capability object types"],
    )

    try:
        build_action_dag(
            UserRequest(
                raw_prompt="list docker containers and inspect pgadmin",
                safety_context={"capability_registry": registry},
            ),
            DecompositionResult(tasks=[task_1, task_2]),
            [CapabilitySelectionResult(task_id="task_2", selected=selected)],
            [
                ArgumentExtractionResult(
                    task_id="task_2",
                    capability_id="operator.shell_command",
                    operation_id="shell_command",
                    arguments={
                        "command": "docker inspect pgadmin",
                        "cwd": ".",
                        "inputs": {},
                        "input_bindings": [],
                        "declared_output_shape": "text",
                        "risk": "low",
                        "timeout_seconds": 5,
                        "reason": "Inspect pgadmin.",
                    },
                    confidence=0.95,
                )
            ],
            capability_fit_decisions=[fit_decision],
        )
    except ValidationError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected unavailable upstream dependency to fail clearly")

    assert "Task task_2 depends on unavailable upstream task(s): task_1" in message
    assert "capability fit status is semantic_mismatch" in message
    assert "operator.shell_command" in message


def test_operator_duplicate_edges_do_not_create_false_cycle(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Duplicate equivalent edges should be canonicalized.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Produce output",
                    "semantic_verb": "execute",
                    "object_type": "shell",
                    "dependencies": [],
                    "reason": "Producer.",
                },
                {
                    "task_id": "task_2",
                    "goal": "Transform output",
                    "semantic_verb": "transform",
                    "object_type": "text",
                    "dependencies": ["task_1"],
                    "reason": "Consumer.",
                },
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "command": "printf hello",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Produce text.",
                    "depends_on": [],
                },
                {
                    "action_id": "action_2",
                    "task_id": "task_2",
                    "kind": "python_transform",
                    "code": "def transform(inputs):\n    return inputs['stdout'].strip()",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [
                        {
                            "input_name": "stdout",
                            "source_action_id": "action_1",
                            "source_field": "stdout",
                        }
                    ],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Transform text.",
                    "depends_on": ["action_1"],
                },
            ],
            "dependencies": [
                {
                    "producer_action_id": "action_1",
                    "consumer_action_id": "action_2",
                    "reason": "Same edge as depends_on and input binding.",
                }
            ],
            "expected_outputs": ["hello"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert not [error for error in errors if error["error"] == "cycle_detected"]


def test_operator_python_transform_allows_datetime_for_time_calculation(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Calculate uptime.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Calculate uptime in days",
                    "semantic_verb": "calculate",
                    "object_type": "text",
                    "dependencies": [],
                    "reason": "Time calculation.",
                }
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "python_transform",
                    "code": (
                        "def transform(inputs):\n"
                        "    import datetime\n"
                        "    started = datetime.datetime.fromisoformat(inputs['started_at'])\n"
                        "    now = datetime.datetime.fromisoformat(inputs['now'])\n"
                        "    return (now - started).days\n"
                    ),
                    "cwd": ".",
                    "inputs": {
                        "started_at": "2026-05-01T00:00:00+00:00",
                        "now": "2026-05-08T00:00:00+00:00",
                    },
                    "input_bindings": [],
                    "declared_output_shape": "json",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Calculate elapsed days.",
                    "depends_on": [],
                }
            ],
            "dependencies": [],
            "expected_outputs": ["Days"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert errors == []


def test_operator_validator_allows_runtime_to_clamp_large_positive_timeout(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Run a slow but valid command.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Run command",
                    "semantic_verb": "execute",
                    "object_type": "command",
                    "dependencies": [],
                    "reason": "Test timeout validation.",
                }
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "command": "printf ok",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 60,
                    "reason": "Positive timeout remains governed by runtime config.",
                    "depends_on": [],
                }
            ],
            "dependencies": [],
            "expected_outputs": ["ok"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(
            workspace_root=str(tmp_path),
            allow_shell_execution=True,
            gateway_timeout_seconds=30,
        )
    ).validate(plan)

    assert errors == []


def test_operator_python_transform_allows_string_replace_for_timestamp_normalization(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Normalize timestamp.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Normalize timestamp",
                    "semantic_verb": "transform",
                    "object_type": "text",
                    "dependencies": [],
                    "reason": "String cleanup.",
                }
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "python_transform",
                    "code": "def transform(inputs):\n    return inputs['started_at'].replace('Z', '+00:00')",
                    "cwd": ".",
                    "inputs": {"started_at": "2026-05-08T00:00:00Z"},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Normalize timestamp suffix.",
                    "depends_on": [],
                }
            ],
            "dependencies": [],
            "expected_outputs": ["Normalized timestamp"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert errors == []


def test_operator_python_transform_rejects_unbound_input_key(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Calculate uptime.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Get container start time",
                    "semantic_verb": "read",
                    "object_type": "docker.container",
                    "dependencies": [],
                    "reason": "Collect the timestamp.",
                },
                {
                    "task_id": "task_2",
                    "goal": "Calculate uptime in days",
                    "semantic_verb": "calculate",
                    "object_type": "docker.container",
                    "dependencies": ["task_1"],
                    "reason": "Transform the timestamp.",
                },
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "command": "docker inspect --format '{{.State.StartedAt}}' pgadmin",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Read start time.",
                    "depends_on": [],
                },
                {
                    "action_id": "action_2",
                    "task_id": "task_2",
                    "kind": "python_transform",
                    "code": (
                        "def transform(inputs):\n"
                        "    container_info = inputs.get('container_info', {})\n"
                        "    return container_info.get('started_at', '')\n"
                    ),
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [
                        {
                            "input_name": "stdout",
                            "source_action_id": "action_1",
                            "source_field": "stdout",
                        }
                    ],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Calculate elapsed days.",
                    "depends_on": ["action_1"],
                },
            ],
            "dependencies": [],
            "expected_outputs": ["Days"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    input_errors = [error for error in errors if error["error"] == "python_input_unbound"]
    assert input_errors
    assert input_errors[0]["input_name"] == "container_info"
    assert input_errors[0]["available_inputs"] == ["stdout"]


def test_operator_python_transform_allows_declared_input_key(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Calculate uptime.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Get container start time",
                    "semantic_verb": "read",
                    "object_type": "docker.container",
                    "dependencies": [],
                    "reason": "Collect the timestamp.",
                },
                {
                    "task_id": "task_2",
                    "goal": "Calculate uptime in days",
                    "semantic_verb": "calculate",
                    "object_type": "docker.container",
                    "dependencies": ["task_1"],
                    "reason": "Transform the timestamp.",
                },
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "command": "docker inspect --format '{{.State.StartedAt}}' pgadmin",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Read start time.",
                    "depends_on": [],
                },
                {
                    "action_id": "action_2",
                    "task_id": "task_2",
                    "kind": "python_transform",
                    "code": "def transform(inputs):\n    return inputs['stdout'].strip()",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [
                        {
                            "input_name": "stdout",
                            "source_action_id": "action_1",
                            "source_field": "stdout",
                        }
                    ],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Calculate elapsed days.",
                    "depends_on": ["action_1"],
                },
            ],
            "dependencies": [],
            "expected_outputs": ["Days"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert [error for error in errors if error["error"] == "python_input_unbound"] == []


def test_operator_python_transform_allows_get_for_required_bound_input(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Calculate total size.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "List Docker image sizes",
                    "semantic_verb": "read",
                    "object_type": "docker.image",
                    "dependencies": [],
                    "reason": "Collect size rows.",
                },
                {
                    "task_id": "task_2",
                    "goal": "Calculate total image size",
                    "semantic_verb": "calculate",
                    "object_type": "docker.image",
                    "dependencies": ["task_1"],
                    "reason": "Aggregate sizes.",
                },
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "command": "docker images --format '{{.Size}}'",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Read image sizes.",
                    "depends_on": [],
                },
                {
                    "action_id": "action_2",
                    "task_id": "task_2",
                    "kind": "python_transform",
                    "code": (
                        "def transform(inputs):\n"
                        "    stdout = inputs.get('stdout', '')\n"
                        "    return 0 if not stdout else len(stdout.splitlines())\n"
                    ),
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [
                        {
                            "input_name": "stdout",
                            "source_action_id": "action_1",
                            "source_field": "stdout",
                            "required": True,
                            "fallback_value": None,
                        }
                    ],
                    "declared_output_shape": "json",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Sum image sizes.",
                    "depends_on": ["action_1"],
                },
            ],
            "dependencies": [],
            "expected_outputs": ["Total size"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert not [error for error in errors if error["error"] == "python_required_input_defaulted"]
    assert errors == []


def test_operator_validator_rejects_unresolved_shell_placeholders(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Use a branch placeholder.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Show latest branch change",
                    "semantic_verb": "execute",
                    "object_type": "git",
                    "dependencies": [],
                    "reason": "User requested a Git lookup.",
                }
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "command": "git log -1 --oneline {branch_name}",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "medium",
                    "timeout_seconds": 5,
                    "reason": "Uses a placeholder.",
                    "depends_on": [],
                }
            ],
            "dependencies": [],
            "expected_outputs": ["latest change"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert any(error["error"] == "unresolved_shell_placeholder" for error in errors)
    assert any("{branch_name}" in error.get("placeholders", []) for error in errors)


def test_operator_validator_allows_curl_write_out_brace_tokens(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Check site status with curl.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Check site status",
                    "semantic_verb": "execute",
                    "object_type": "http",
                    "dependencies": [],
                    "reason": "User requested a site availability check.",
                }
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "command": 'curl -sS -o /dev/null -w "%{http_code}" https://example.com',
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 10,
                    "reason": "Uses curl write-out status code syntax.",
                    "depends_on": [],
                }
            ],
            "dependencies": [],
            "expected_outputs": ["HTTP status code"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert not [error for error in errors if error["error"] == "unresolved_shell_placeholder"]


def test_operator_validator_rejects_shell_input_bindings(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Try to inject a value into shell.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Find a container",
                    "semantic_verb": "search",
                    "object_type": "docker.container",
                    "dependencies": [],
                    "reason": "Find candidate container.",
                },
                {
                    "task_id": "task_2",
                    "goal": "Inspect the found container",
                    "semantic_verb": "read",
                    "object_type": "docker.container",
                    "dependencies": ["task_1"],
                    "reason": "Inspect candidate container.",
                },
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "command": "docker ps -a --format '{{.Names}}' | grep -i pgadmin | head -n 1",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Find the container name.",
                    "depends_on": [],
                },
                {
                    "action_id": "action_2",
                    "task_id": "task_2",
                    "kind": "shell_command",
                    "command": "docker inspect {{container_name}}",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [
                        {
                            "input_name": "container_name",
                            "source_action_id": "action_1",
                            "source_field": "stdout",
                        }
                    ],
                    "declared_output_shape": "text",
                    "risk": "medium",
                    "timeout_seconds": 5,
                    "reason": "Incorrectly tries to interpolate an input binding into shell.",
                    "depends_on": ["action_1"],
                },
            ],
            "dependencies": [],
            "expected_outputs": ["container details"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert any(error["error"] == "unresolved_shell_placeholder" for error in errors)


def test_operator_validator_allows_awk_braces_and_assigned_shell_variables(tmp_path: Path) -> None:
    plan = OperatorPlan.model_validate(
        {
            "summary": "Use concrete shell syntax.",
            "tasks": [
                {
                    "task_id": "task_1",
                    "goal": "Show transformed output",
                    "semantic_verb": "execute",
                    "object_type": "shell",
                    "dependencies": [],
                    "reason": "User requested shell work.",
                }
            ],
            "actions": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "command": "branch_name=$(printf main); printf '%s\\n' \"$branch_name\" | awk 'NR==1 {print $1}'",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Uses local shell assignment and awk syntax.",
                    "depends_on": [],
                }
            ],
            "dependencies": [],
            "expected_outputs": ["main"],
            "assumptions": [],
            "confidence": 0.9,
        }
    )

    errors = OperatorPlanValidator(
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True)
    ).validate(plan)

    assert not [error for error in errors if error["error"] == "unresolved_shell_placeholder"]


def test_operator_safety_block_reason_includes_validation_details(tmp_path: Path) -> None:
    registry = build_default_registry()
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node::task_1",
                task_id="task_1",
                description="Transform Docker output",
                semantic_verb="transform",
                capability_id="operator.python_transform",
                operation_id="python_transform",
                arguments={
                    "code": "import os\ndef transform(inputs):\n    return 'bad'",
                    "cwd": ".",
                    "inputs": {},
                    "input_bindings": [],
                    "declared_output_shape": "text",
                    "risk": "medium",
                    "timeout_seconds": 5,
                    "reason": "Try a blocked import.",
                },
                depends_on=[],
            )
        ],
        edges=[],
    )

    decision = evaluate_dag_safety(
        dag,
        registry,
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True),
    )

    assert decision.allowed is False
    reason = "\n".join(decision.blocked_reasons)
    assert "Operator plan rejected for node::task_1" in reason
    assert "python_transform_contract_shape" in reason
    assert "python_import_blocked" not in reason


def test_standard_operator_read_only_shell_does_not_require_confirmation(tmp_path: Path) -> None:
    registry = build_default_registry()
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node::task_1",
                task_id="task_1",
                description="List Docker containers",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": "docker ps --format '{{.Names}}'",
                    "cwd": ".",
                    "declared_output_shape": "text",
                    "risk": "medium",
                    "timeout_seconds": 5,
                    "reason": "List containers.",
                },
                depends_on=[],
            )
        ],
        edges=[],
        requires_confirmation=True,
    )

    decision = evaluate_dag_safety(
        dag,
        registry,
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True, gateway_url="http://gateway"),
    )

    assert decision.allowed is True
    assert decision.requires_confirmation is False
    assert decision.sanitized_dag is not None
    assert "read-only" in decision.sanitized_dag.nodes[0].safety_labels


def test_standard_operator_mutating_shell_requires_confirmation(tmp_path: Path) -> None:
    registry = build_default_registry()
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node::task_1",
                task_id="task_1",
                description="Remove Docker image",
                semantic_verb="execute",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": "docker rmi image:tag",
                    "cwd": ".",
                    "declared_output_shape": "text",
                    "risk": "medium",
                    "timeout_seconds": 5,
                    "reason": "Remove an image.",
                },
                depends_on=[],
            )
        ],
        edges=[],
    )

    decision = evaluate_dag_safety(
        dag,
        registry,
        RuntimeConfig(workspace_root=str(tmp_path), allow_shell_execution=True, gateway_url="http://gateway"),
    )

    assert decision.allowed is True
    assert decision.requires_confirmation is True
    assert decision.sanitized_dag is not None
    assert "requires-confirmation" in decision.sanitized_dag.nodes[0].safety_labels


def test_operator_shell_action_executes_inside_standard_action_dag(tmp_path: Path) -> None:
    registry = build_default_registry()
    gateway = FakeGateway()
    config = RuntimeConfig(
        workspace_root=str(tmp_path),
        allow_shell_execution=True,
        gateway_url="http://gateway",
        gateway_default_node="fake",
    )
    engine = ExecutionEngine(
        registry,
        config,
        InMemoryResultStore(),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node::task_1",
                task_id="task_1",
                description="List text files",
                semantic_verb="search",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": "find \"$PWD\" -name '*.txt' -type f",
                    "cwd": ".",
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "List text files.",
                },
                depends_on=[],
            )
        ],
        edges=[],
        requires_confirmation=True,
    )
    prepared = _execution_ready_dag(dag, config, registry)

    result = engine.execute(prepared, {"confirmation": True})

    assert result.status == "success"
    assert gateway.calls[0]["command"] == "find \"$PWD\" -name '*.txt' -type f"
    assert result.results[0].data_preview["stdout"] == "README.md\n"


def test_read_only_operator_shell_executes_without_confirmation_in_standard_dag(tmp_path: Path) -> None:
    registry = build_default_registry()
    gateway = FakeGateway()
    config = RuntimeConfig(
        workspace_root=str(tmp_path),
        allow_shell_execution=True,
        gateway_url="http://gateway",
        gateway_default_node="fake",
    )
    engine = ExecutionEngine(
        registry,
        config,
        InMemoryResultStore(),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node::task_1",
                task_id="task_1",
                description="List Docker images",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": "docker images --format '{{.Repository}}\\t{{.Tag}}\\t{{.Size}}'",
                    "cwd": ".",
                    "declared_output_shape": "table",
                    "risk": "medium",
                    "timeout_seconds": 5,
                    "reason": "List Docker images.",
                },
                depends_on=[],
            )
        ],
        edges=[],
    )
    prepared = _execution_ready_dag(dag, config, registry)

    result = engine.execute(prepared, {})

    assert result.status == "success"
    assert gateway.calls[0]["command"] == "docker images --format '{{.Repository}}\\t{{.Tag}}\\t{{.Size}}'"


def test_standard_operator_python_rejects_zero_like_output_from_optional_input(
    tmp_path: Path,
) -> None:
    registry = build_default_registry()
    gateway = StandardZeroLikeGateway()
    config = RuntimeConfig(
        workspace_root=str(tmp_path),
        allow_shell_execution=True,
        gateway_url="http://gateway",
        gateway_default_node="fake",
    )
    engine = ExecutionEngine(
        registry,
        config,
        InMemoryResultStore(),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    dag = ActionDAG(
        nodes=[
            ActionNode(
                id="node::task_1",
                task_id="task_1",
                description="List Docker images",
                semantic_verb="read",
                capability_id="operator.shell_command",
                operation_id="shell_command",
                arguments={
                    "command": 'docker images --format "{{.Repository}}:{{.Tag}} {{.Size}}"',
                    "cwd": ".",
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "List Docker images.",
                },
                depends_on=[],
            ),
            ActionNode(
                id="node::task_2",
                task_id="task_2",
                description="Calculate Docker image size",
                semantic_verb="calculate",
                capability_id="operator.python_action",
                operation_id="python_action",
                arguments={
                    "code": (
                        "def main(inputs):\n"
                        "    return 'Total space consumed by Docker images: 0.00 MB'"
                    ),
                    "cwd": ".",
                    "input_bindings": [
                        {
                            "input_name": "stdout",
                            "source_action_id": "node::task_1",
                            "source_field": "stdout",
                            "required": False,
                            "fallback_value": "",
                        }
                    ],
                    "declared_output_shape": "text",
                    "risk": "low",
                    "timeout_seconds": 5,
                    "reason": "Calculate total image size.",
                },
                depends_on=["node::task_1"],
            ),
        ],
        edges=[("node::task_1", "node::task_2")],
    )
    prepared = _execution_ready_dag(dag, config, registry)

    result = engine.execute(prepared, {"confirmation": True})

    assert result.status == "partial"
    assert result.results[1].status == "error"
    assert "zero-like result" in str(result.results[1].error)
    assert "32.2GB" in str(result.results[1].metadata["bound_inputs_preview"])


def test_gateway_client_routes_operator_shell_commands_to_exec_endpoint(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"stdout": "ok", "stderr": "", "exit_code": 0}'

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(
        "agent_runtime.execution.gateway_client.urllib_request.urlopen",
        fake_urlopen,
    )
    client = GatewayClient(
        RuntimeConfig(
            gateway_url="http://gateway.example/",
            gateway_default_node="worker-a",
            gateway_timeout_seconds=17,
        )
    )

    response = client.execute_raw_command(command="printf hello", cwd=".", execution_context={})

    assert response["stdout"] == "ok"
    assert captured["url"] == "http://gateway.example/exec"
    assert captured["timeout"] is None
    assert json.loads(captured["data"].decode("utf-8")) == {
        "node": "worker-a",
        "command": "cd . && printf hello",
    }


def test_gateway_client_routes_operator_shell_stdin_to_exec_endpoint(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"stdout": "ok", "stderr": "", "exit_code": 0}'

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        captured["data"] = request.data
        return FakeHTTPResponse()

    monkeypatch.setattr(
        "agent_runtime.execution.gateway_client.urllib_request.urlopen",
        fake_urlopen,
    )
    client = GatewayClient(
        RuntimeConfig(
            gateway_url="http://gateway.example/",
            gateway_default_node="worker-a",
            gateway_timeout_seconds=17,
        )
    )

    response = client.execute_raw_command(
        command="cat",
        cwd=".",
        execution_context={"shell_stdin": "hello\n"},
    )

    assert response["stdout"] == "ok"
    assert json.loads(captured["data"].decode("utf-8")) == {
        "node": "worker-a",
        "command": "cd . && cat",
        "stdin": "hello\n",
    }


def test_gateway_client_streams_operator_shell_command_events(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(
                [
                    b'event: stdout\n',
                    b'data: {"type": "stdout", "text": "hello\\n"}\n',
                    b"\n",
                    b'event: stderr\n',
                    b'data: {"type": "stderr", "text": "warn\\n"}\n',
                    b"\n",
                    b'event: completed\n',
                    b'data: {"type": "completed", "exit_code": 0}\n',
                    b"\n",
                ]
            )

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(
        "agent_runtime.execution.gateway_client.urllib_request.urlopen",
        fake_urlopen,
    )
    client = GatewayClient(
        RuntimeConfig(
            gateway_url="http://gateway.example/",
            gateway_default_node="worker-a",
            gateway_timeout_seconds=17,
        )
    )

    events = list(
        client.stream_raw_command(
            command="printf hello",
            cwd=".",
            execution_context={"request_id": "req-123"},
        )
    )

    assert [event["type"] for event in events] == ["stdout", "stderr", "completed"]
    assert events[0]["text"] == "hello\n"
    assert events[0]["gateway_node"] == "worker-a"
    assert events[2]["exit_code"] == 0
    assert captured["url"] == "http://gateway.example/exec/stream"
    assert captured["timeout"] is None
    assert json.loads(captured["data"].decode("utf-8")) == {
        "node": "worker-a",
        "command": "cd . && printf hello",
        "execution_id": "req-123",
    }


def test_gateway_client_streams_operator_shell_command_to_request_scoped_gateway(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(
                [
                    b"event: completed\n",
                    b'data: {"type": "completed", "exit_code": 0}\n',
                    b"\n",
                ]
            )

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(
        "agent_runtime.execution.gateway_client.urllib_request.urlopen",
        fake_urlopen,
    )
    client = GatewayClient(
        RuntimeConfig(
            gateway_url="http://localhost-gateway.example/",
            gateway_default_node="localhost",
            gateway_timeout_seconds=17,
        )
    )

    events = list(
        client.stream_raw_command(
            command="df -h /",
            cwd=".",
            execution_context={
                "request_id": "req-mac",
                "gateway_node": "Vinith-Mac",
                "gateway_url": "http://192.168.1.121:8787",
                "gateway_endpoints": {"Vinith-Mac": "http://192.168.1.121:8787"},
            },
        )
    )

    assert events[0]["gateway_node"] == "Vinith-Mac"
    assert events[0]["gateway_url"] == "http://192.168.1.121:8787"
    assert captured["url"] == "http://192.168.1.121:8787/exec/stream"
    assert json.loads(captured["data"].decode("utf-8")) == {
        "node": "Vinith-Mac",
        "command": "cd . && df -h /",
        "execution_id": "req-mac",
    }


def test_gateway_client_streams_terminal_shell_command_events(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self):  # type: ignore[no-untyped-def]
            return iter(
                [
                    b"event: stdout\n",
                    b'data: {"type": "stdout", "text": "hello\\n"}\n',
                    b"\n",
                    b"event: completed\n",
                    b'data: {"type": "completed", "exit_code": 0}\n',
                    b"\n",
                ]
            )

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(
        "agent_runtime.execution.gateway_client.urllib_request.urlopen",
        fake_urlopen,
    )
    client = GatewayClient(
        RuntimeConfig(
            gateway_url="http://gateway.example/",
            gateway_default_node="worker-a",
            gateway_timeout_seconds=17,
        )
    )

    events = list(
        client.stream_terminal_command(
            command="printf hello",
            cwd="/tmp",
            execution_context={
                "request_id": "req-123",
                "terminal_session_id": "term-1",
                "terminal_cwd": "/tmp",
            },
        )
    )

    assert [event["type"] for event in events] == ["stdout", "completed"]
    assert events[0]["terminal_dispatch"] is True
    assert events[0]["terminal_session_id"] == "term-1"
    assert events[0]["gateway_node"] == "worker-a"
    assert events[0]["gateway_url"] == "http://gateway.example"
    assert captured["url"] == "http://gateway.example/terminal/exec/stream"
    assert captured["timeout"] is None
    assert json.loads(captured["data"].decode("utf-8")) == {
        "node": "worker-a",
        "command": "printf hello",
        "session_id": "term-1",
        "execution_id": "req-123",
    }


def test_gateway_client_mirrors_transform_output_to_terminal(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(
        "agent_runtime.execution.gateway_client.urllib_request.urlopen",
        fake_urlopen,
    )
    client = GatewayClient(
        RuntimeConfig(
            gateway_url="http://gateway.example/",
            gateway_default_node="worker-a",
            gateway_timeout_seconds=17,
        )
    )

    response = client.write_terminal_output(
        text="formatted result\n",
        execution_context={"terminal_session_id": "term-1"},
    )

    assert response["ok"] is True
    assert captured["url"] == "http://gateway.example/terminal/write"
    assert json.loads(captured["data"].decode("utf-8")) == {
        "node": "worker-a",
        "session_id": "term-1",
        "text": "formatted result\n",
    }


def test_gateway_client_creates_background_terminal_session(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true, "session_id": "term-bg", "cwd": "/tmp"}'

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(
        "agent_runtime.execution.gateway_client.urllib_request.urlopen",
        fake_urlopen,
    )
    client = GatewayClient(
        RuntimeConfig(
            gateway_url="http://gateway.example/",
            gateway_default_node="worker-a",
            gateway_timeout_seconds=17,
        )
    )

    response = client.create_terminal_session(
        session_id="term-bg",
        initial_cwd="/tmp",
        rows=24,
        cols=100,
    )

    assert response["ok"] is True
    assert response["gateway_node"] == "worker-a"
    assert captured["url"] == "http://gateway.example/terminal/session"
    assert json.loads(captured["data"].decode("utf-8")) == {
        "node": "worker-a",
        "session_id": "term-bg",
        "initial_cwd": "/tmp",
        "rows": 24,
        "cols": 100,
    }


def test_gateway_client_starts_detached_terminal_command(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true, "session_id": "term-1", "message": "Command started in terminal."}'

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(
        "agent_runtime.execution.gateway_client.urllib_request.urlopen",
        fake_urlopen,
    )
    client = GatewayClient(
        RuntimeConfig(
            gateway_url="http://gateway.example/",
            gateway_default_node="worker-a",
            gateway_timeout_seconds=17,
        )
    )

    response = client.start_terminal_detached_command(
        command="watch -n 1 nvidia-smi",
        cwd="/tmp",
        execution_context={"terminal_session_id": "term-1", "terminal_cwd": "/tmp"},
    )

    assert response["ok"] is True
    assert response["terminal_detached"] is True
    assert captured["url"] == "http://gateway.example/terminal/exec/detach"
    assert json.loads(captured["data"].decode("utf-8")) == {
        "node": "worker-a",
        "session_id": "term-1",
        "command": "watch -n 1 nvidia-smi",
    }


def test_gateway_client_cancels_operator_shell_command(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"cancelled": true, "execution_id": "req-123", "message": "Cancellation signal sent."}'

    def fake_urlopen(request, timeout: int):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(
        "agent_runtime.execution.gateway_client.urllib_request.urlopen",
        fake_urlopen,
    )
    client = GatewayClient(
        RuntimeConfig(
            gateway_url="http://gateway.example/",
            gateway_default_node="worker-a",
            gateway_timeout_seconds=17,
        )
    )

    response = client.cancel_raw_command(execution_id="req-123", execution_context={})

    assert response["cancelled"] is True
    assert response["gateway_node"] == "worker-a"
    assert captured["url"] == "http://gateway.example/exec/cancel"
    assert captured["timeout"] == 17
    assert json.loads(captured["data"].decode("utf-8")) == {
        "node": "worker-a",
        "execution_id": "req-123",
    }
