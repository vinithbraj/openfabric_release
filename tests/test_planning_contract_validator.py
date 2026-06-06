from __future__ import annotations

from agent_runtime.core.types import TaskFrame
from agent_runtime.input_pipeline.validators import PlanningContractValidator


def _task(**updates) -> TaskFrame:
    payload = {
        "id": "task_1",
        "description": "Read README.md",
        "semantic_verb": "read",
        "object_type": "file",
        "intent_confidence": 0.9,
        "constraints": {},
        "dependencies": [],
        "risk_level": "low",
    }
    payload.update(updates)
    return TaskFrame.model_validate(payload)


def test_planning_contract_rejects_unknown_semantic_verb_without_mapping() -> None:
    task = TaskFrame.model_construct(
        id="task_1",
        description="Tally files",
        semantic_verb="tally",
        object_type="file",
        intent_confidence=0.9,
        constraints={},
        dependencies=[],
        raw_evidence=None,
        requires_confirmation=False,
        risk_level="low",
        operation_intent=None,
        side_effect_type=None,
        dependency_hints=[],
    )

    result = PlanningContractValidator().validate_tasks([task], original_prompt="tally files")

    assert not result.accepted
    assert result.issues[0].error == "invalid_semantic_verb"
    assert "Use one of" in result.feedback()[0]["message"]


def test_planning_contract_accepts_count_semantic_verb() -> None:
    task = _task(description="Count files", semantic_verb="count")

    result = PlanningContractValidator().validate_tasks([task], original_prompt="count files")

    assert result.accepted


def test_planning_contract_accepts_list_semantic_verb() -> None:
    task = _task(description="List files", semantic_verb="list")

    result = PlanningContractValidator().validate_tasks([task], original_prompt="list files")

    assert result.accepted


def test_planning_contract_rejects_user_explicit_value_missing_from_prompt() -> None:
    task = _task(
        constraints={
            "constraint_provenance": {
                "path": {
                    "value": "src/README.md",
                    "source_text": "src/README.md",
                    "source_type": "user_explicit",
                }
            }
        }
    )

    result = PlanningContractValidator().validate_tasks([task], original_prompt="read README.md")

    assert not result.accepted
    assert result.issues[0].error == "invalid_user_explicit_provenance"


def test_planning_contract_accepts_declared_dependency_and_visible_provenance() -> None:
    producer = _task(id="task_1", description="Find README.md", semantic_verb="search")
    consumer = _task(
        id="task_2",
        description="Read README.md",
        dependencies=["task_1"],
        constraints={
            "constraint_provenance": {
                "file_name": {
                    "value": "README.md",
                    "source_text": "README.md",
                    "source_type": "user_explicit",
                }
            }
        },
    )

    result = PlanningContractValidator().validate_tasks(
        [producer, consumer],
        original_prompt="find README.md and read README.md",
    )

    assert result.accepted
    assert result.feedback() == []


def test_planning_contract_rejects_unknown_dependency() -> None:
    task = _task(dependencies=["missing_task"])

    result = PlanningContractValidator().validate_tasks([task], original_prompt="read README.md")

    assert not result.accepted
    assert result.issues[0].error == "unknown_dependency"


def test_planning_contract_rejects_executable_payload_in_decomposition_constraints() -> None:
    task = _task(
        description="Calculate uptime for a Docker container",
        semantic_verb="calculate",
        object_type="docker.container",
        constraints={
            "code": "def transform(inputs):\n    return inputs",
            "input_bindings": [
                {
                    "input_name": "stdout",
                    "source_action_id": "task_1",
                    "source_field": "stdout",
                }
            ],
        },
    )

    result = PlanningContractValidator().validate_tasks(
        [task],
        original_prompt="calculate uptime for a Docker container",
    )

    assert not result.accepted
    assert {issue.error for issue in result.issues} == {"decomposition_downstream_artifact"}
    assert {issue.details["forbidden_key"] for issue in result.issues} == {
        "code",
        "input_bindings",
    }


def test_planning_contract_accepts_semantic_decomposition_constraints() -> None:
    task = _task(
        description="Find a Docker container named pgadmin",
        semantic_verb="search",
        object_type="docker.container",
        constraints={
            "target_name": "pgadmin",
            "match_mode": "similar_name",
            "output_preference": "uptime_days",
        },
    )

    result = PlanningContractValidator().validate_tasks(
        [task],
        original_prompt="check if there is a docker container named pgadmin or sounds like that",
    )

    assert result.accepted


def test_planning_contract_rejects_unrequested_destructive_storage_reset() -> None:
    task = _task(
        id="format_usb_device",
        description="Format the USB device with a compatible filesystem type.",
        semantic_verb="create",
        object_type="filesystem",
        constraints={"device_name": "/dev/sdc2", "filesystem_type": "vfat"},
        risk_level="high",
    )

    result = PlanningContractValidator().validate_tasks(
        [task],
        original_prompt=(
            "How do I fix the error mounting /dev/sdc2? "
            "wrong fs type, bad option, bad superblock"
        ),
    )

    assert not result.accepted
    assert result.issues[0].error == "unrequested_destructive_storage_reset"


def test_planning_contract_allows_explicit_destructive_storage_reset() -> None:
    task = _task(
        id="format_usb_device",
        description="Format the USB device with a compatible filesystem type.",
        semantic_verb="create",
        object_type="filesystem",
        constraints={"device_name": "/dev/sdc2", "filesystem_type": "vfat"},
        risk_level="high",
    )

    result = PlanningContractValidator().validate_tasks(
        [task],
        original_prompt="Format the USB drive /dev/sdc2 as a new filesystem.",
    )

    assert result.accepted


def test_planning_contract_does_not_confuse_output_formatting_with_storage_reset() -> None:
    task = _task(
        id="format_report",
        description="Format the analysis result as a markdown table.",
        semantic_verb="render",
        object_type="markdown_table",
        constraints={"output_format": "markdown_table"},
    )

    result = PlanningContractValidator().validate_tasks(
        [task],
        original_prompt="Analyze the data and format it as a markdown table.",
    )

    assert result.accepted


def test_planning_contract_allows_formatting_storage_metadata_output() -> None:
    task = _task(
        id="format_metadata",
        description="Format filesystem metadata as JSON.",
        semantic_verb="render",
        object_type="filesystem_metadata",
        constraints={"output_format": "json"},
    )

    result = PlanningContractValidator().validate_tasks(
        [task],
        original_prompt="Show filesystem metadata as JSON.",
    )

    assert result.accepted
