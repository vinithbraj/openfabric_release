from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.core.errors import ValidationError
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.core.types import ActionDAG, ActionNode, DataRef, DisplayPlan, ExecutionResult, ResultBundle, UserRequest
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.observability import AgentTraceSink, AgentTraceStore, build_observability_context
from agent_runtime.output_pipeline.display_document import build_display_document
from agent_runtime.output_pipeline.display_selection import (
    DisplaySelectionInput,
    select_display_plan,
)
from agent_runtime.output_pipeline.display_primitives import build_default_display_primitive_registry
from agent_runtime.output_pipeline.orchestrator import OutputPipelineOrchestrator, compose_output
from agent_runtime.output_pipeline.renderers import LocalAgentUIRenderer, render_result_shape
from agent_runtime.output_pipeline.result_shapes import (
    AggregateResult,
    CapabilityListResult,
    CommandOutputResult,
    DirectoryListingResult,
    JsonResult,
    MarkdownResult,
    RecordListResult,
    TextResult,
    normalize_execution_result,
)
from agent_runtime.output_pipeline.shape_adapters import (
    ShapeAdapterContext,
    build_default_shape_adapter_registry,
)


class FakeLLMClient:
    """Fake LLM client for display plan selection tests."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.last_prompt = ""
        self.prompts: list[str] = []
        self.last_schema: dict[str, Any] | None = None

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.last_prompt = prompt
        self.prompts.append(prompt)
        self.last_schema = schema
        return dict(self.payload)


class RaisingLLMClient:
    """Fake LLM client that simulates advisor failure."""

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("advisor unavailable")


def _request(store: InMemoryResultStore | None = None, allow_full: bool = False) -> UserRequest:
    return UserRequest(
        raw_prompt="test prompt",
        safety_context={
            "result_store": store,
            "allow_full_output_access": allow_full,
        },
    )


def _dag(node_id: str = "node-1", capability_id: str = "filesystem.list_directory", operation_id: str = "list_directory") -> ActionDAG:
    return ActionDAG(
        dag_id="dag-1",
        nodes=[
            ActionNode(
                id=node_id,
                task_id="task-1",
                description="task",
                semantic_verb="read",
                capability_id=capability_id,
                operation_id=operation_id,
                arguments={},
                safety_labels=[],
            )
        ],
    )


def test_directory_listing_renders_as_markdown_table() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={
                    "entries": [
                        {"name": "README.md", "path": "README.md", "type": "file", "size": 10, "modified_time": "now"},
                        {"name": "src", "path": "src", "type": "directory", "size": 0, "modified_time": "now"},
                    ]
                },
            )
        ],
    )
    llm = FakeLLMClient(
        {
            "title": "Workspace Files",
            "evaluations": [
                {
                    "primitive_id": "table",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.94,
                    "reason": "Directory-style rows are best presented as a table.",
                    "title": "Workspace Files",
                }
            ],
        }
    )

    output = compose_output(_request(), _dag(), bundle, llm)

    assert "## Workspace Files" in output
    assert "| name | path | type | size | modified_time |" in output
    assert "README.md" in output


def test_read_file_renders_as_code_block() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={"content_preview": "print('hello')\n", "truncated": False},
            )
        ],
    )
    llm = FakeLLMClient(
        {
            "title": "File Preview",
            "evaluations": [
                {
                    "primitive_id": "code_block",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.96,
                    "reason": "File content is best shown in a code block.",
                    "title": "File Preview",
                    "parameters": {"language": "python"},
                }
            ],
        }
    )

    output = compose_output(_request(), _dag("node-1", "filesystem.read_file", "read_file"), bundle, llm)

    assert "```python" in output
    assert "print('hello')" in output


def test_operator_shell_stdout_normalizes_as_command_output() -> None:
    result = ExecutionResult(
        node_id="node-1",
        status="success",
        data_preview={
            "stdout": "V10_distillation_1\n",
            "stderr": "",
            "exit_code": 0,
            "output": "V10_distillation_1\n",
        },
        metadata={"capability_id": "operator.shell_command", "operation_id": "shell_command"},
    )

    shape = normalize_execution_result(result)

    assert isinstance(shape, CommandOutputResult)
    assert shape.shape_type == "command_output"
    assert shape.text == "V10_distillation_1"
    assert shape.metadata["exit_code"] == 0


def test_operator_shell_stdout_renders_as_code_block_not_json() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={
                    "stdout": "* V10_distillation_1\n  main\n",
                    "stderr": "",
                    "exit_code": 0,
                    "output": "* V10_distillation_1\n  main\n",
                },
                metadata={"capability_id": "operator.shell_command", "operation_id": "shell_command"},
            )
        ],
    )
    llm = FakeLLMClient(
        {
            "title": "Branches",
            "evaluations": [
                {
                    "primitive_id": "code_block",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.96,
                    "reason": "Shell output is best preserved as text.",
                    "title": "Branches",
                }
            ],
        }
    )

    output = compose_output(
        _request(),
        _dag("node-1", "operator.shell_command", "shell_command"),
        bundle,
        llm,
    )

    assert "## Branches" in output
    assert "```text" in output
    assert "* V10_distillation_1" in output
    assert '"stdout"' not in output
    assert '"exit_code"' not in output


def test_agent_ui_operator_output_uses_final_formatter_without_raw_prompt() -> None:
    class FormatterLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(prompt)
            assert schema.get("title") == "OperatorFinalFormatter"
            return {
                "code": (
                    "def transform(inputs):\n"
                    "    stdout = inputs['records'][0]['stdout'].strip()\n"
                    "    return '| Environment |\\n|---|\\n| ' + stdout + ' |'"
                ),
                "declared_output_shape": "markdown",
                "reason": "Render command output as a table.",
                "confidence": 0.94,
            }

    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={
                    "stdout": "secret-conda-env\n",
                    "stderr": "",
                    "exit_code": 0,
                    "output": "secret-conda-env\n",
                },
                metadata={"capability_id": "operator.shell_command", "operation_id": "shell_command"},
            )
        ],
    )
    request = UserRequest(
        raw_prompt="list all conda environments",
        session_context={
            "target_ui": "agent_ui",
            "config": {"workspace_root": ".", "reasoning_profile": "deep"},
        },
        safety_context={},
    )
    llm = FormatterLLM()

    output = compose_output(
        request,
        _dag("node-1", "operator.shell_command", "shell_command"),
        bundle,
        llm,
    )

    assert "| Environment |" in output
    assert "secret-conda-env" in output
    assert "secret-conda-env" not in llm.prompts[0]
    assert "stdout_line_count" in llm.prompts[0]
    assert "primary_field" in llm.prompts[0]
    assert "For shell_command records, stdout is the primary command result." in llm.prompts[0]
    assert request.safety_context["display_document"]["sections"][0]["content"] == output


def test_command_output_rejects_markdown_choice_and_falls_back_to_code_block() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={
                    "stdout": "* V10_distillation_1\n  main\n",
                    "stderr": "",
                    "exit_code": 0,
                    "output": "* V10_distillation_1\n  main\n",
                },
                metadata={"capability_id": "operator.shell_command", "operation_id": "shell_command"},
            )
        ],
    )
    llm = FakeLLMClient(
        {
            "title": "Branches",
            "evaluations": [
                {
                    "primitive_id": "markdown",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.96,
                    "reason": "This bad choice should be rejected for command output.",
                    "title": "Branches",
                }
            ],
        }
    )

    output = compose_output(
        _request(),
        _dag("node-1", "operator.shell_command", "shell_command"),
        bundle,
        llm,
    )

    assert "```text" in output
    assert "* V10_distillation_1\n  main" in output
    assert "<p>* V10" not in output


def test_process_rows_render_as_markdown_table() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={
                    "pattern": "python",
                    "processes": [
                        {"pid": 1, "command": "python3", "cpu_percent": 10.2, "memory_percent": 1.5},
                        {"pid": 2, "command": "uvicorn", "cpu_percent": 5.0, "memory_percent": 0.8},
                    ],
                    "truncated": False,
                },
                metadata={"capability_id": "shell.list_processes", "operation_id": "list_processes"},
            )
        ],
    )
    llm = FakeLLMClient(
        {
            "title": "Running Python Processes",
            "evaluations": [
                {
                    "primitive_id": "table",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.94,
                    "reason": "Process rows are best presented as a table.",
                    "title": "Running Python Processes",
                }
            ],
        }
    )

    output = compose_output(_request(), _dag("node-1", "shell.list_processes", "list_processes"), bundle, llm)

    assert "## Running Python Processes" in output
    assert "| pid | command | cpu_percent | memory_percent |" in output
    assert "python3" in output


def test_errors_render_cleanly() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="error",
        results=[],
        safe_summary="Execution blocked.",
        metadata={"blocked_reasons": ["Shell execution is disabled."]},
    )

    output = compose_output(_request(), _dag(), bundle, FakeLLMClient({}))

    assert "Execution failed." in output
    assert "Shell execution is disabled." in output
    assert "Execution blocked." in output


def test_partial_failures_render_clearly() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="partial",
        safe_summary="One node failed and one node was skipped.",
        results=[
            ExecutionResult(node_id="node-ok", status="success", data_preview={"markdown": "done"}),
            ExecutionResult(node_id="node-fail", status="error", error="simulated failure"),
            ExecutionResult(node_id="node-skip", status="skipped", error="Skipped because dependency failed."),
        ],
    )
    dag = ActionDAG(
        dag_id="dag-1",
        nodes=[
            ActionNode(
                id="node-ok",
                task_id="task-ok",
                description="ok",
                semantic_verb="read",
                capability_id="markdown.render",
                operation_id="render",
                arguments={},
                safety_labels=[],
            )
        ],
    )

    output = compose_output(_request(), dag, bundle, FakeLLMClient({}))

    assert "Partial results" in output
    assert "Completed:" in output
    assert "Errors:" in output
    assert "Skipped:" in output


def test_partial_success_matches_render_as_markdown_table() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="partial",
        safe_summary="Execution completed with one or more errors.",
        results=[
            ExecutionResult(
                node_id="node::task_1",
                status="success",
                data_preview={
                    "path": ".",
                    "pattern": "*.txt",
                    "matches": ["README.txt", "src/aor_runtime.egg-info/requires.txt"],
                    "total_matches": 3,
                    "preview_count": 2,
                    "truncated": True,
                },
                metadata={"capability_id": "filesystem.search_files", "operation_id": "search_files"},
            ),
            ExecutionResult(
                node_id="node::task_2",
                status="error",
                error="file already exists and overwrite is disabled: files.txt",
                metadata={"capability_id": "filesystem.write_file", "operation_id": "write_file"},
            ),
        ],
    )
    dag = ActionDAG(
        dag_id="dag-1",
        nodes=[
            ActionNode(
                id="node::task_1",
                task_id="task_1",
                description="search text files",
                semantic_verb="search",
                capability_id="filesystem.search_files",
                operation_id="search_files",
                arguments={},
                safety_labels=[],
            ),
            ActionNode(
                id="node::task_2",
                task_id="task_2",
                description="write report",
                semantic_verb="create",
                capability_id="filesystem.write_file",
                operation_id="write_file",
                arguments={},
                safety_labels=[],
                depends_on=["node::task_1"],
            ),
        ],
        edges=[("node::task_1", "node::task_2")],
    )

    output = compose_output(_request(), dag, bundle, FakeLLMClient({}))

    assert "Partial results" in output
    assert "| path |" in output
    assert "README.txt" in output
    assert "requires.txt" in output
    assert "_Showing 2 of 3 rows._" in output
    assert "file already exists and overwrite is disabled: files.txt" in output
    assert "{'path':" not in output


def test_confirmation_required_bundle_renders_from_status_without_metadata() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="confirmation_required",
        results=[],
        safe_summary="Execution requires confirmation before proceeding.",
        metadata={},
    )

    rendered = OutputPipelineOrchestrator().render(bundle)

    assert "## Confirmation Required" in rendered.content
    assert "approval" in rendered.content.lower()


def test_operator_confirmation_bundle_renders_compact_action_review() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="confirmation_required",
        results=[],
        safe_summary="Execution requires confirmation before proceeding.",
        metadata={
            "confirmation_actions": [
                {
                    "task_id": "task_1",
                    "capability_id": "operator.shell_command",
                    "operation_id": "shell_command",
                    "description": "Determine the current Git branch for this folder",
                    "arguments": {
                        "command": "git branch --show-current",
                        "cwd": ".",
                        "inputs": "<object with 0 fields>",
                        "input_bindings": "<list with 0 items>",
                        "declared_output_shape": "text",
                        "risk": "medium",
                        "reason": "LLM-authored operator action.",
                    },
                },
                {
                    "task_id": "task_2",
                    "capability_id": "operator.python_transform",
                    "operation_id": "python_transform",
                    "description": "Extract branch names from the Git branch listing",
                    "arguments": {
                        "code": "def transform(inputs):\n    return inputs['branch_listing'].splitlines()",
                        "cwd": ".",
                        "inputs": "<object with 0 fields>",
                        "input_bindings": [
                            {
                                "input_name": "branch_name",
                                "source_action_id": "task_1",
                                "source_field": "stdout",
                                "required": True,
                            }
                        ],
                        "declared_output_shape": "text",
                        "risk": "high",
                        "reason": "LLM-authored operator action.",
                    },
                },
            ]
        },
    )

    rendered = OutputPipelineOrchestrator().render(bundle)

    assert "### Actions To Run" in rendered.content
    assert "#### Action 1: Determine the current Git branch for this folder" in rendered.content
    assert "`operator.shell_command` · shell command · risk `medium` · cwd `.`" in rendered.content
    assert "Command:\n```bash\ngit branch --show-current\n```" in rendered.content
    assert "#### Action 2: Extract branch names from the Git branch listing" in rendered.content
    assert "`operator.python_transform` · python transform · risk `high` · cwd `.`" in rendered.content
    assert "Code:\n```python\ndef transform(inputs):" in rendered.content
    assert "### Output Flow" in rendered.content
    assert "- Action 1 `stdout` -> Action 2 input `branch_name`" in rendered.content
    assert "Inputs:" not in rendered.content
    assert "Input Bindings:" not in rendered.content
    assert "Declared Output Shape:" not in rendered.content
    assert "LLM-authored operator action" not in rendered.content


def test_confirmation_sanitizer_preserves_operator_command_and_code_previews() -> None:
    sanitized = AgentRuntime._safe_confirmation_arguments(
        {
            "command": "git branch --show-current",
            "code": "def transform(inputs):\n    return inputs",
            "content": "secret write content",
        }
    )

    assert sanitized["command"] == "git branch --show-current"
    assert sanitized["code"].startswith("def transform")
    assert sanitized["content"] == "<omitted>"


def test_display_plan_cannot_reference_missing_data() -> None:
    selection_input = DisplaySelectionInput(
        original_prompt="list files",
        dag_summary={"dag_id": "dag-1"},
        result_summary={"status": "success"},
        safe_previews=[
            {
                "node_id": "node-1",
                "data_ref": "data-1",
                "preview": {"entries": []},
                "shape_type": "record_list",
            }
        ],
        available_display_types=["markdown", "table", "code_block", "multi_section", "json", "plain_text"],
    )
    llm = FakeLLMClient(
        {
            "title": "Broken Plan",
            "evaluations": [
                {
                    "primitive_id": "table",
                    "source_ref": "node:missing-node",
                    "fits": True,
                    "confidence": 0.92,
                    "reason": "This source ref was not declared.",
                    "title": "Broken Plan",
                }
            ],
        }
    )

    with pytest.raises(ValidationError, match="not declared"):
        select_display_plan(selection_input, llm)


def test_default_display_primitive_registry_exports_initial_primitives() -> None:
    registry = build_default_display_primitive_registry()

    manifests = {manifest.primitive_id: manifest for manifest in registry.list_manifests()}

    assert set(manifests) == {
        "directory_listing",
        "table",
        "record_list",
        "markdown",
        "code_block",
        "json_view",
        "file_tree",
        "scalar",
        "aggregate",
        "error",
        "raw_payload",
        "multi_section",
    }
    assert manifests["table"].renderer == "table"
    assert manifests["raw_payload"].safety_policy["debug_only"] is True


def test_display_primitive_selection_rejects_unknown_primitive() -> None:
    selection_input = DisplaySelectionInput(
        original_prompt="list files",
        dag_summary={"dag_id": "dag-1"},
        result_summary={"status": "success"},
        safe_previews=[
            {
                "node_id": "node-1",
                "preview": {"entries": []},
                "shape_type": "record_list",
            }
        ],
    )
    llm = FakeLLMClient(
        {
            "evaluations": [
                {
                    "primitive_id": "invented_grid",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.91,
                    "reason": "This primitive was not declared.",
                }
            ]
        }
    )

    with pytest.raises(ValidationError, match="not declared"):
        select_display_plan(selection_input, llm)


def test_display_primitive_selection_rejects_incompatible_source_pair() -> None:
    selection_input = DisplaySelectionInput(
        original_prompt="show uptime",
        dag_summary={"dag_id": "dag-1"},
        result_summary={"status": "success"},
        safe_previews=[
            {
                "node_id": "node-1",
                "preview": {"value": "1 day"},
                "shape_type": "scalar",
            }
        ],
    )
    llm = FakeLLMClient(
        {
            "evaluations": [
                {
                    "primitive_id": "table",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.91,
                    "reason": "Scalar sources were not declared as table candidates.",
                }
            ]
        }
    )

    with pytest.raises(ValidationError, match="not declared"):
        select_display_plan(selection_input, llm)


def test_display_primitive_selection_requires_accepted_candidate() -> None:
    selection_input = DisplaySelectionInput(
        original_prompt="show uptime",
        dag_summary={"dag_id": "dag-1"},
        result_summary={"status": "success"},
        safe_previews=[
            {
                "node_id": "node-1",
                "preview": {"value": "1 day"},
                "shape_type": "scalar",
            }
        ],
    )
    llm = FakeLLMClient(
        {
            "evaluations": [
                {
                    "primitive_id": "scalar",
                    "source_ref": "node:node-1",
                    "fits": False,
                    "confidence": 0.88,
                    "reason": "The model declined this declared candidate.",
                }
            ]
        }
    )

    with pytest.raises(ValidationError, match="No display primitive candidate was accepted"):
        select_display_plan(selection_input, llm)


def test_display_validation_rejects_missing_requested_columns() -> None:
    selection_input = DisplaySelectionInput(
        original_prompt="show rows",
        dag_summary={"dag_id": "dag-1"},
        result_summary={"status": "success"},
        safe_previews=[
            {
                "node_id": "node-1",
                "preview": {"rows": [{"name": "a.txt"}]},
                "shape_type": "table",
            }
        ],
    )
    llm = FakeLLMClient(
        {
            "evaluations": [
                {
                    "primitive_id": "table",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.9,
                    "reason": "Rows are tabular.",
                    "parameters": {"columns": ["name", "missing"]},
                }
            ]
        }
    )

    with pytest.raises(ValidationError, match="missing columns"):
        select_display_plan(selection_input, llm)


def test_display_validation_rejects_raw_payload_when_policy_forbids_it() -> None:
    selection_input = DisplaySelectionInput(
        original_prompt="debug raw",
        dag_summary={"dag_id": "dag-1"},
        result_summary={"status": "success"},
        safe_previews=[
            {
                "node_id": "node-1",
                "preview": {"rows": [{"name": "a.txt"}]},
                "shape_type": "table",
            }
        ],
        allow_raw_preview=False,
    )
    llm = FakeLLMClient(
        {
            "evaluations": [
                {
                    "primitive_id": "raw_payload",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.9,
                    "reason": "Raw payload requested.",
                }
            ]
        }
    )

    with pytest.raises(ValidationError, match="not declared"):
        select_display_plan(selection_input, llm)


def test_display_validation_rejects_preview_rows_above_policy() -> None:
    selection_input = DisplaySelectionInput(
        original_prompt="show many rows",
        dag_summary={"dag_id": "dag-1"},
        result_summary={"status": "success"},
        safe_previews=[
            {
                "node_id": "node-1",
                "preview": {"rows": [{"idx": index} for index in range(101)], "preview_count": 101},
                "shape_type": "table",
            }
        ],
    )
    llm = FakeLLMClient(
        {
            "evaluations": [
                {
                    "primitive_id": "table",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.9,
                    "reason": "Rows are tabular.",
                }
            ]
        }
    )

    with pytest.raises(ValidationError, match="exceeds its max_preview_rows"):
        select_display_plan(selection_input, llm)


def test_output_planning_sees_safe_previews_not_full_data() -> None:
    store = InMemoryResultStore()
    full_payload = {"blob": "x" * 5000}
    data_ref = store.put("node-1", full_payload, "object", {})
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_ref=DataRef.model_validate(data_ref.model_dump()),
                data_preview={"preview_text": "x" * 64, "truncated": True, "bytes": 5000},
            )
        ],
    )
    llm = FakeLLMClient(
        {
            "display_type": "plain_text",
            "title": "Preview",
            "sections": [
                {
                    "title": "Preview",
                    "display_type": "plain_text",
                    "source_node_id": "node-1",
                }
            ],
            "constraints": {},
            "redaction_policy": "standard",
        }
    )

    compose_output(_request(store=store, allow_full=False), _dag(), bundle, llm)

    assert "preview_text" in llm.prompts[0]
    assert '"blob"' not in llm.prompts[0]
    assert "x" * 5000 not in llm.prompts[0]


def test_entries_payload_normalizes_to_record_list_result() -> None:
    shape = normalize_execution_result(
        ExecutionResult(
            node_id="node-1",
            status="success",
            data_preview={
                "path": ".",
                "entries": [
                    {"name": "README.md", "path": "README.md", "type": "file", "size": 10, "modified_time": "now"}
                ],
            },
            metadata={"capability_id": "filesystem.list_directory", "operation_id": "list_directory"},
        )
    )

    assert isinstance(shape, RecordListResult)
    assert shape.metadata["path"] == "."
    assert shape.records[0]["name"] == "README.md"


def test_aggregate_normalizes_to_aggregate_result() -> None:
    shape = normalize_execution_result(
        ExecutionResult(
            node_id="node-agg",
            status="success",
            data_preview={
                "operation": "sum",
                "field": "size",
                "value": 4777,
                "unit": "bytes",
                "row_count": 15,
                "used_count": 5,
                "skipped_count": 0,
                "label": "Total File Size",
            },
            metadata={"capability_id": "data.aggregate", "operation_id": "aggregate"},
        )
    )

    assert isinstance(shape, AggregateResult)
    assert shape.value == 4777
    assert shape.unit == "bytes"


def test_runtime_capabilities_normalize_to_capability_list_result() -> None:
    shape = normalize_execution_result(
        ExecutionResult(
            node_id="node-runtime",
            status="success",
            data_preview={
                "grouped_capabilities": {
                    "filesystem": [
                        {
                            "capability_id": "filesystem.list_directory",
                            "operation_id": "list_directory",
                            "name": "List Directory",
                            "description": "List files in a directory.",
                            "semantic_verbs": ["read"],
                            "object_types": ["filesystem.path"],
                            "read_only": True,
                            "risk_level": "low",
                        }
                    ]
                },
                "capability_count": 1,
            },
            metadata={
                "capability_id": "runtime.describe_capabilities",
                "operation_id": "describe_capabilities",
            },
        )
    )

    assert isinstance(shape, CapabilityListResult)
    assert "filesystem" in shape.grouped_capabilities
    assert shape.capability_count == 1


def test_capability_list_display_document_carries_markdown_content() -> None:
    shape = CapabilityListResult(
        node_id="node-runtime",
        capability_id="runtime.describe_capabilities",
        operation_id="describe_capabilities",
        title="Runtime Capabilities",
        grouped_capabilities={
            "filesystem": ["filesystem.read_file", "filesystem.search_files"],
            "runtime": ["runtime.describe_capabilities"],
        },
        capability_count=3,
        data_ref="data-runtime",
    )
    document = build_display_document(
        display_plan=DisplayPlan(
            display_type="markdown",
            primitive_id="markdown",
            title="Runtime Capabilities",
            sections=[
                {
                    "title": "Runtime Capabilities",
                    "primitive_id": "markdown",
                    "display_type": "markdown",
                    "source_node_id": "node-runtime",
                }
            ],
        ),
        source_lookup={
            "node:node-runtime": {
                "node_id": "node-runtime",
                "data_ref": "data-runtime",
                "shape": shape,
            }
        },
    )

    assert document.sections[0].content
    assert "filesystem.read_file" in document.sections[0].content
    assert "runtime.describe_capabilities" in document.sections[0].content


def test_aggregate_result_renders_scalar_value() -> None:
    rendered = render_result_shape(
        AggregateResult(
            node_id="node-agg",
            capability_id="data.aggregate",
            operation_id="aggregate",
            label="Total File Size",
            operation="sum",
            field="size",
            value=4777,
            unit="bytes",
            row_count=15,
            used_count=15,
            skipped_count=0,
        )
    )

    assert "Total File Size: 4777 bytes" in rendered
    assert "README.md" not in rendered


def test_directory_listing_result_renders_table() -> None:
    rendered = render_result_shape(
        DirectoryListingResult(
            node_id="node-list",
            capability_id="filesystem.list_directory",
            operation_id="list_directory",
            entries=[
                {"name": "README.md", "path": "README.md", "type": "file", "size": 10, "modified_time": "now"},
                {"name": "src", "path": "src", "type": "directory", "size": 0, "modified_time": "now"},
            ],
        )
    )

    assert "| name | path | type | size | modified_time |" in rendered
    assert "README.md" in rendered


def test_mixed_directory_and_aggregate_render_two_sections() -> None:
    request = UserRequest(raw_prompt="list all files in this directory and compute the total file size")
    dag = ActionDAG(
        dag_id="dag-mixed",
        nodes=[
            ActionNode(
                id="node-list",
                task_id="task-list",
                description="list files",
                semantic_verb="read",
                capability_id="filesystem.list_directory",
                operation_id="list_directory",
                arguments={},
                safety_labels=[],
            ),
            ActionNode(
                id="node-agg",
                task_id="task-agg",
                description="aggregate file size",
                semantic_verb="analyze",
                capability_id="data.aggregate",
                operation_id="aggregate",
                arguments={},
                depends_on=["node-list"],
                safety_labels=[],
            ),
        ],
        edges=[("node-list", "node-agg")],
    )
    bundle = ResultBundle(
        dag_id="dag-mixed",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-list",
                status="success",
                data_preview={
                    "entries": [
                        {"name": "README.md", "path": "README.md", "type": "file", "size": 10, "modified_time": "now"},
                        {"name": "src", "path": "src", "type": "directory", "size": 0, "modified_time": "now"},
                    ]
                },
                metadata={"capability_id": "filesystem.list_directory", "operation_id": "list_directory"},
            ),
            ExecutionResult(
                node_id="node-agg",
                status="success",
                data_preview={
                    "operation": "sum",
                    "field": "size",
                    "value": 10,
                    "unit": "bytes",
                    "row_count": 2,
                    "used_count": 1,
                    "skipped_count": 0,
                    "label": "Total File Size",
                },
                metadata={"capability_id": "data.aggregate", "operation_id": "aggregate"},
            ),
        ],
    )
    invalid_llm = FakeLLMClient(
        {
            "display_type": "multi_section",
            "title": "Broken Plan",
            "sections": [
                {
                    "title": "Broken Plan",
                    "display_type": "table",
                    "source_node_id": "missing-node",
                }
            ],
            "constraints": {},
            "redaction_policy": "standard",
        }
    )

    output = compose_output(request, dag, bundle, invalid_llm)

    assert "README.md" in output
    assert "Total File Size: 10 bytes" in output
    assert output.index("README.md") < output.index("Total File Size: 10 bytes")


def test_total_file_size_does_not_render_file_names() -> None:
    request = UserRequest(raw_prompt="total file size in this directory")
    bundle = ResultBundle(
        dag_id="dag-agg",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-agg",
                status="success",
                data_preview={
                    "operation": "sum",
                    "field": "size",
                    "value": 4777,
                    "unit": "bytes",
                    "row_count": 15,
                    "used_count": 5,
                    "skipped_count": 0,
                    "label": "Total File Size",
                },
                metadata={"capability_id": "data.aggregate", "operation_id": "aggregate"},
            )
        ],
    )
    invalid_llm = FakeLLMClient(
        {
            "display_type": "table",
            "title": "Broken Aggregate Plan",
            "sections": [{"display_type": "table", "source_node_id": "missing-node"}],
            "constraints": {},
            "redaction_policy": "standard",
        }
    )

    output = compose_output(
        request,
        _dag("node-agg", "data.aggregate", "aggregate"),
        bundle,
        invalid_llm,
    )

    assert "Total File Size: 4777 bytes" in output
    assert "README.md" not in output


def test_deterministic_fallback_works_if_llm_display_plan_fails() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={
                    "entries": [
                        {"name": "README.md", "path": "README.md", "type": "file", "size": 10, "modified_time": "now"}
                    ]
                },
                metadata={"capability_id": "filesystem.list_directory", "operation_id": "list_directory"},
            )
        ],
    )
    invalid_llm = FakeLLMClient(
        {
            "display_type": "table",
            "title": "Broken Plan",
            "sections": [{"display_type": "table", "source_node_id": "missing-node"}],
            "constraints": {},
            "redaction_policy": "standard",
        }
    )

    output = compose_output(_request(), _dag(), bundle, invalid_llm)

    assert "| name | path | type | size | modified_time |" in output
    assert "README.md" in output




def test_fallback_policy_handles_advisor_failure() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={"rows": [{"name": "README.md"}]},
            )
        ],
    )

    output = compose_output(_request(), _dag(), bundle, RaisingLLMClient())

    assert "| name |" in output
    assert "README.md" in output


def test_fallback_policy_handles_validation_rejection() -> None:
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={"rows": [{"name": "README.md"}]},
            )
        ],
    )
    llm = FakeLLMClient(
        {
            "evaluations": [
                {
                    "primitive_id": "table",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.91,
                    "reason": "Rows are tabular.",
                    "parameters": {"columns": ["missing"]},
                }
            ]
        }
    )

    output = compose_output(_request(), _dag(), bundle, llm)

    assert "| name |" in output
    assert "README.md" in output


def test_fallback_policy_handles_renderer_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_runtime.output_pipeline import orchestrator as orchestrator_module

    def _raise_render(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(orchestrator_module, "render_display_plan", _raise_render)
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={"rows": [{"name": "README.md"}]},
            )
        ],
    )

    rendered = OutputPipelineOrchestrator().render(bundle)

    assert rendered.metadata["fallback"] is True
    assert "| name |" in rendered.content
    assert "README.md" in rendered.content


def test_fallback_policy_handles_empty_success_bundle() -> None:
    rendered = OutputPipelineOrchestrator().render(
        ResultBundle(dag_id="dag-empty", status="success", results=[])
    )

    assert rendered.content == "No results available."


def test_no_raw_stack_traces_are_rendered() -> None:
    bundle = ResultBundle(
        dag_id="dag-err",
        status="error",
        safe_summary=(
            "Traceback (most recent call last):\n"
            "  File \"runtime.py\", line 10, in run\n"
            "ValueError: boom"
        ),
        results=[],
    )

    output = compose_output(_request(), _dag(), bundle, FakeLLMClient({}))

    assert "Traceback (most recent call last)" not in output
    assert "ValueError: boom" in output


def test_large_matches_preview_preserves_renderable_structure() -> None:
    store = InMemoryResultStore()
    payload = {
        "path": ".",
        "pattern": "*.txt",
        "matches": [f"folder/file_{index}.txt" for index in range(200)],
    }

    preview = store.preview(payload, max_bytes=512)

    assert "preview_text" not in preview
    assert preview["total_matches"] == 200
    assert preview["total_count"] == 200
    assert preview["preview_count"] == len(preview["matches"])
    assert preview["preview_count"] > 0
    assert preview["truncated"] is True


def test_large_rows_preview_preserves_renderable_structure() -> None:
    store = InMemoryResultStore()
    payload = {"rows": [{"idx": index, "value": f"row-{index}"} for index in range(200)]}

    preview = store.preview(payload, max_bytes=512)

    assert "preview_text" not in preview
    assert preview["rows"]
    assert preview["total_rows"] == 200
    assert preview["preview_count"] == len(preview["rows"])
    assert preview["truncated"] is True


def test_unknown_large_payload_still_falls_back_to_preview_text() -> None:
    store = InMemoryResultStore()

    preview = store.preview({"blob": "x" * 5000}, max_bytes=128)

    assert set(preview) == {"preview_text", "truncated", "bytes"}
    assert preview["truncated"] is True


def test_search_files_preview_normalizes_string_matches_to_path_records() -> None:
    preview = {
        "path": ".",
        "pattern": "*.txt",
        "matches": ["README.txt", "notes/todo.txt"],
        "total_matches": 20,
        "preview_count": 2,
        "truncated": True,
    }

    shape = normalize_execution_result(
        ExecutionResult(
            node_id="node-search",
            status="success",
            data_preview=preview,
            metadata={
                "capability_id": "filesystem.search_files",
                "operation_id": "search_files",
            },
        )
    )
    rendered = render_result_shape(shape)

    assert isinstance(shape, RecordListResult)
    assert shape.records == [{"path": "README.txt"}, {"path": "notes/todo.txt"}]
    assert shape.total_count == 20
    assert shape.preview_count == 2
    assert "README.txt" in rendered
    assert "_No rows available._" not in rendered


def test_shape_adapter_registry_matches_by_payload_keys() -> None:
    registry = build_default_shape_adapter_registry()
    context = ShapeAdapterContext(
        result=ExecutionResult(
            node_id="node-search",
            status="success",
            data_preview={"matches": ["README.txt"]},
        ),
        payload={"matches": ["README.txt"]},
    )

    adapter = registry.select_adapter(context)
    shape = registry.normalize(context)

    assert adapter.adapter_id == "payload.matches"
    assert isinstance(shape, RecordListResult)
    assert shape.records == [{"path": "README.txt"}]


def test_shape_adapter_registry_matches_entries_payload() -> None:
    registry = build_default_shape_adapter_registry()
    context = ShapeAdapterContext(
        result=ExecutionResult(
            node_id="node-list",
            status="success",
            data_preview={"path": ".", "entries": [{"name": "README.md"}]},
            metadata={"capability_id": "filesystem.list_directory", "operation_id": "list_directory"},
        ),
        payload={"path": ".", "entries": [{"name": "README.md"}]},
        capability_id="filesystem.list_directory",
        operation_id="list_directory",
    )

    adapter = registry.select_adapter(context)
    shape = registry.normalize(context)

    assert adapter.adapter_id == "payload.entries"
    assert isinstance(shape, RecordListResult)
    assert shape.metadata["path"] == "."


def test_compose_output_search_files_uses_structured_preview_without_full_access() -> None:
    store = InMemoryResultStore()
    payload = {
        "path": ".",
        "pattern": "*.txt",
        "matches": [f"folder/file_{index}.txt" for index in range(80)],
    }
    data_ref = store.put("node-search", payload, "object", {})
    preview = store.preview(payload, max_bytes=512)
    bundle = ResultBundle(
        dag_id="dag-search",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-search",
                status="success",
                data_ref=DataRef.model_validate(data_ref.model_dump()),
                data_preview=preview,
                metadata={
                    "capability_id": "filesystem.search_files",
                    "operation_id": "search_files",
                },
            )
        ],
    )
    llm = FakeLLMClient(
        {
            "title": "Matching Text Files",
            "evaluations": [
                {
                    "primitive_id": "record_list",
                    "source_ref": "node:node-search",
                    "fits": True,
                    "confidence": 0.95,
                    "reason": "The source is a repeated list of file path records.",
                    "title": "Matching Text Files",
                }
            ],
        }
    )
    request = UserRequest(
        raw_prompt="list all *.txt files",
        safety_context={
            "result_store": store,
            "allow_full_output_access": False,
            "target_ui": "agent_ui",
        },
    )

    output = compose_output(
        request,
        _dag("node-search", "filesystem.search_files", "search_files"),
        bundle,
        llm,
    )
    display_document = request.safety_context["display_document"]

    assert "folder/file_0.txt" in output
    assert "_No rows available._" not in output
    assert display_document["sections"][0]["rows"]
    assert display_document["sections"][0]["rows"][0] == {"path": "folder/file_0.txt"}
    assert display_document["sections"][0]["truncated"] is True


def test_unknown_nested_dict_normalizes_to_json_result() -> None:
    shape = normalize_execution_result(
        ExecutionResult(
            node_id="node-json",
            status="success",
            data_preview={"nested": {"ok": True}, "items": [1, 2, 3]},
        )
    )
    rendered = render_result_shape(shape)

    assert isinstance(shape, JsonResult)
    assert "```json" in rendered
    assert '"nested"' in rendered


def test_markdown_and_code_rendering_are_deterministic() -> None:
    markdown = render_result_shape(
        MarkdownResult(node_id="node-md", markdown="## Ready\n\n| a | b |\n| --- | --- |\n| 1 | 2 |")
    )
    code = render_result_shape(
        TextResult(node_id="node-code", text="print('ok')"),
        display_type="code_block",
        parameters={"language": "python"},
    )

    assert "## Ready" in markdown
    assert "| a | b |" in markdown
    assert "```python" in code
    assert "print('ok')" in code


def test_local_agent_ui_renderer_returns_structured_display_document() -> None:
    display_plan = DisplayPlan(
        display_type="table",
        primitive_id="table",
        sections=[
            {
                "source_node_id": "node-1",
                "primitive_id": "table",
                "display_type": "table",
                "parameters": {"columns": ["name"]},
            }
        ],
    )
    source_lookup = {
        "node:node-1": {
            "node_id": "node-1",
            "data_ref": "data-1",
            "shape": normalize_execution_result(
                ExecutionResult(
                    node_id="node-1",
                    status="success",
                    data_preview={"rows": [{"name": "README.md", "size": 10}]},
                )
            ),
        }
    }

    document = LocalAgentUIRenderer().render(
        display_plan,
        source_lookup,
        request_id="req-1",
        allow_raw_access=True,
    )

    assert document.request_id == "req-1"
    assert document.sections[0].rows == [{"name": "README.md", "size": 10}]
    assert document.sections[0].columns == ["name"]
    assert document.sections[0].raw_available is True


def test_output_trace_event_generation_includes_display_phases() -> None:
    store = AgentTraceStore()
    trace = store.create_request("list rows")
    sink = AgentTraceSink(store, trace.request_id)
    observability = build_observability_context(
        trace.request_id,
        {
            "observability": {
                "enabled": True,
                "debug": True,
                "sinks": [sink],
            }
        },
    )
    request = UserRequest(
        raw_prompt="show rows",
        safety_context={
            "observability": observability,
            "target_ui": "agent_ui",
            "allow_raw_preview": True,
        },
    )
    bundle = ResultBundle(
        dag_id="dag-1",
        status="success",
        results=[
            ExecutionResult(
                node_id="node-1",
                status="success",
                data_preview={"rows": [{"name": "README.md"}]},
                metadata={"capability_id": "data.head", "operation_id": "head"},
            )
        ],
    )
    llm = FakeLLMClient(
        {
            "evaluations": [
                {
                    "primitive_id": "table",
                    "source_ref": "node:node-1",
                    "fits": True,
                    "confidence": 0.9,
                    "reason": "Rows are tabular.",
                }
            ]
        }
    )

    compose_output(request, _dag("node-1", "data.head", "head"), bundle, llm)

    stored = store.get_trace(trace.request_id)
    assert stored is not None
    traces = [
        event.detail.get("output_trace")
        for event in stored.events
        if isinstance(event.detail, dict) and event.detail.get("output_trace")
    ]
    assert {item["phase"] for item in traces} >= {
        "result_collection",
        "shape_normalization",
        "display_validation",
        "display_document_generation",
        "rendering",
    }
