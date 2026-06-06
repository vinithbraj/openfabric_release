from __future__ import annotations

from typing import Any

from agent_runtime.capabilities import build_default_registry
from agent_runtime.core.types import TaskFrame
from agent_runtime.input_pipeline.verb_classification import VerbClassifier, assign_semantic_verbs


class FakeLLMClient:
    """Fake structured LLM client for semantic verb assignment tests."""

    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.last_prompt = ""
        self.last_schema: dict[str, Any] | None = None

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.last_prompt = prompt
        self.last_schema = schema
        for marker, payload in self.payloads.items():
            if marker in prompt:
                return dict(payload)
        raise AssertionError(f"no fake payload configured for prompt: {prompt}")


def _task(task_id: str, description: str, *, constraints: dict[str, Any] | None = None) -> TaskFrame:
    return TaskFrame(
        id=task_id,
        description=description,
        semantic_verb="unknown",
        object_type="unknown",
        intent_confidence=0.0,
        constraints=constraints or {},
        raw_evidence=description,
    )


def _client() -> FakeLLMClient:
    return FakeLLMClient(
        {
            "list files": {
                "assignments": [
                    {
                        "task_id": "task-list",
                        "semantic_verb": "search",
                        "object_type": "filesystem",
                        "intent_confidence": 0.94,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            },
            "remove files": {
                "assignments": [
                    {
                        "task_id": "task-remove",
                        "semantic_verb": "delete",
                        "object_type": "filesystem",
                        "intent_confidence": 0.93,
                        "risk_level": "medium",
                        "requires_confirmation": False,
                    }
                ]
            },
            "create report": {
                "assignments": [
                    {
                        "task_id": "task-report",
                        "semantic_verb": "create",
                        "object_type": "report",
                        "intent_confidence": 0.92,
                        "risk_level": "medium",
                        "requires_confirmation": False,
                    }
                ]
            },
            "run tests": {
                "assignments": [
                    {
                        "task_id": "task-tests",
                        "semantic_verb": "execute",
                        "object_type": "test_suite",
                        "intent_confidence": 0.96,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            },
            "summarize logs": {
                "assignments": [
                    {
                        "task_id": "task-logs",
                        "semantic_verb": "analyze",
                        "object_type": "logs",
                        "intent_confidence": 0.91,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            },
            "calculate docker image space": {
                "assignments": [
                    {
                        "task_id": "task-calculate",
                        "semantic_verb": "calculate",
                        "object_type": "system.disk",
                        "intent_confidence": 0.91,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            },
            "count txt files": {
                "assignments": [
                    {
                        "task_id": "task-count",
                        "semantic_verb": "count",
                        "object_type": "filesystem.file",
                        "intent_confidence": 0.91,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            },
            "sort docker containers by uptime": {
                "assignments": [
                    {
                        "task_id": "task-sort",
                        "semantic_verb": "sort",
                        "object_type": "docker.container",
                        "intent_confidence": 0.91,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            },
            "filter docker containers named pgadmin": {
                "assignments": [
                    {
                        "task_id": "task-filter",
                        "semantic_verb": "filter",
                        "object_type": "docker.container",
                        "intent_confidence": 0.91,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            },
        }
    )


def test_deterministic_verb_classifier_uses_count_vocabulary() -> None:
    assert VerbClassifier().classify("count txt files") == "count"


def test_assign_semantic_verbs_for_list_files() -> None:
    client = _client()
    registry = build_default_registry()

    tasks = assign_semantic_verbs([_task("task-list", "list files in this folder")], client, registry)

    assert tasks[0].semantic_verb in {"read", "search"}
    assert tasks[0].object_type == "filesystem.directory"
    assert tasks[0].risk_level == "low"
    assert "Do not generate commands, shell syntax, SQL, code, or executable plans." in client.last_prompt
    assert "read, list, search, create, update, delete, transform, analyze, calculate, count, filter, sort, summarize, compare, execute, render, unknown" in client.last_prompt
    assert "Choose object_type from this runtime-owned controlled vocabulary only:" in client.last_prompt


def test_assign_semantic_verbs_for_remove_files_forces_confirmation() -> None:
    tasks = assign_semantic_verbs(
        [_task("task-remove", "remove files older than a week")],
        _client(),
        build_default_registry(),
    )

    assert tasks[0].semantic_verb == "delete"
    assert tasks[0].requires_confirmation is True


def test_assign_semantic_verbs_for_create_report() -> None:
    tasks = assign_semantic_verbs(
        [_task("task-report", "create report from daily metrics")],
        _client(),
        build_default_registry(),
    )

    assert tasks[0].semantic_verb in {"create", "render"}
    assert tasks[0].object_type in {"report", "filesystem.file"}
    assert tasks[0].requires_confirmation is True


def test_assign_semantic_verbs_for_run_tests_bumps_risk() -> None:
    tasks = assign_semantic_verbs(
        [_task("task-tests", "run tests for this project")],
        _client(),
        build_default_registry(),
    )

    assert tasks[0].semantic_verb == "execute"
    assert tasks[0].risk_level in {"medium", "high"}


def test_assign_semantic_verbs_for_summarize_logs() -> None:
    tasks = assign_semantic_verbs(
        [_task("task-logs", "summarize logs from today")],
        _client(),
        build_default_registry(),
    )

    assert tasks[0].semantic_verb in {"summarize", "analyze"}
    assert tasks[0].object_type == "unknown"
    assert tasks[0].risk_level == "low"


def test_assign_semantic_verbs_accepts_calculate() -> None:
    tasks = assign_semantic_verbs(
        [_task("task-calculate", "calculate docker image space")],
        _client(),
        build_default_registry(),
        likely_domains=["operator"],
    )

    assert tasks[0].semantic_verb == "calculate"
    assert tasks[0].object_type == "system.disk"
    assert tasks[0].risk_level == "low"


def test_assign_semantic_verbs_accepts_count() -> None:
    tasks = assign_semantic_verbs(
        [_task("task-count", "count txt files")],
        _client(),
        build_default_registry(),
        likely_domains=["operator"],
    )

    assert tasks[0].semantic_verb == "count"
    assert tasks[0].object_type == "filesystem.file"
    assert tasks[0].risk_level == "low"


def test_assign_semantic_verbs_accepts_sort_and_filter() -> None:
    sort_tasks = assign_semantic_verbs(
        [_task("task-sort", "sort docker containers by uptime")],
        _client(),
        build_default_registry(),
        likely_domains=["operator"],
    )
    filter_tasks = assign_semantic_verbs(
        [_task("task-filter", "filter docker containers named pgadmin")],
        _client(),
        build_default_registry(),
        likely_domains=["operator"],
    )

    assert sort_tasks[0].semantic_verb == "sort"
    assert filter_tasks[0].semantic_verb == "filter"
    assert sort_tasks[0].object_type == "docker.container"
    assert filter_tasks[0].object_type == "docker.container"
    assert sort_tasks[0].risk_level == "low"
    assert filter_tasks[0].risk_level == "low"


def test_assign_semantic_verbs_repairs_duplicate_llm_task_ids_by_task_order() -> None:
    client = FakeLLMClient(
        {
            "List all running docker containers": {
                "assignments": [
                    {
                        "task_id": "task-1",
                        "semantic_verb": "list",
                        "object_type": "docker.container",
                        "intent_confidence": 0.95,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    },
                    {
                        "task_id": "task-1",
                        "semantic_verb": "list",
                        "object_type": "docker.image",
                        "intent_confidence": 0.95,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    },
                    {
                        "task_id": "task-1",
                        "semantic_verb": "calculate",
                        "object_type": "docker.image",
                        "intent_confidence": 0.9,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    },
                ]
            }
        }
    )

    tasks = assign_semantic_verbs(
        [
            _task("task-1", "List all running docker containers"),
            _task("task-2", "List all docker images"),
            _task("task-3", "Calculate the total size of all docker images"),
        ],
        client,
        build_default_registry(),
        likely_domains=["operator"],
    )

    assert [task.id for task in tasks] == ["task-1", "task-2", "task-3"]
    assert [task.semantic_verb for task in tasks] == ["list", "list", "calculate"]
    assert tasks[0].object_type == "docker.container"
    assert tasks[1].object_type == "docker.image"
    assert tasks[2].object_type == "docker.image"
    assert all(task.risk_level == "low" for task in tasks)
    assert "Copy each task_id exactly as written and do not reuse a task_id." in client.last_prompt


def test_assign_semantic_verbs_normalizes_invalid_tool_word() -> None:
    client = FakeLLMClient(
        {
            "compose the discovered compose files down": {
                "assignments": [
                    {
                        "task_id": "task-compose-down",
                        "semantic_verb": "compose",
                        "object_type": "docker",
                        "intent_confidence": 0.91,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            }
        }
    )

    tasks = assign_semantic_verbs(
        [
            _task(
                "task-compose-down",
                "compose the discovered compose files down with docker compose down",
            )
        ],
        client,
        build_default_registry(),
        likely_domains=["operator", "docker"],
    )

    assert tasks[0].semantic_verb == "execute"
    assert tasks[0].risk_level == "medium"


def test_assign_semantic_verbs_normalizes_free_form_memory_information() -> None:
    client = FakeLLMClient(
        {
            "free memory": {
                "assignments": [
                    {
                        "task_id": "task-memory",
                        "semantic_verb": "read",
                        "object_type": "memory information",
                        "intent_confidence": 0.95,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            }
        }
    )

    tasks = assign_semantic_verbs(
        [_task("task-memory", "retrieve free memory available on the system")],
        client,
        build_default_registry(),
        likely_domains=["system_administration", "operating_system", "system"],
    )

    assert tasks[0].object_type == "system.memory"


def test_assign_semantic_verbs_for_save_report_prefers_filesystem_file() -> None:
    client = FakeLLMClient(
        {
            "save the report": {
                "assignments": [
                    {
                        "task_id": "task-save",
                        "semantic_verb": "create",
                        "object_type": "system memory report file",
                        "intent_confidence": 0.93,
                        "risk_level": "low",
                        "requires_confirmation": False,
                    }
                ]
            }
        }
    )

    tasks = assign_semantic_verbs(
        [_task("task-save", "save the report to report.txt")],
        client,
        build_default_registry(),
    )

    assert tasks[0].object_type == "filesystem.file"
    assert tasks[0].requires_confirmation is True
