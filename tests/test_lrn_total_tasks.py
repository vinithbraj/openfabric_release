from __future__ import annotations

from pathlib import Path

from agent_runtime.core.types import TaskFrame
from agent_runtime.lrn_total_tasks import (
    AgentLrnTotalTaskStore,
    LrnTotalTaskLookupContext,
    LrnTotalTaskWrite,
)


def _classification() -> dict[str, object]:
    return {
        "prompt_type": "compound_tool_task",
        "requires_tools": True,
        "likely_domains": ["operator"],
        "risk_level": "low",
    }


def _task() -> TaskFrame:
    return TaskFrame(
        id="task_1",
        description="List changed files",
        semantic_verb="list",
        object_type="git.changes",
        intent_confidence=0.95,
        constraints={"global_constraints": {}},
        dependencies=[],
        risk_level="low",
    )


def _write(prompt: str = "list changed files and summarize them") -> LrnTotalTaskWrite:
    return LrnTotalTaskWrite(
        prompt=prompt,
        classification_context=_classification(),
        model_name="fake-operator",
        model_family="fake",
        workflow_mode="streaming",
        registry_contract_hash="registry-a",
        tasks=[_task().model_dump(mode="json")],
        global_constraints={"format": "summary"},
        routing_metadata={"streaming_operator_mode": True},
    )


def _lookup(prompt: str) -> LrnTotalTaskLookupContext:
    return LrnTotalTaskLookupContext(
        prompt=prompt,
        classification_context=_classification(),
        model_name="fake-operator",
        model_family="fake",
        workflow_mode="streaming",
        registry_contract_hash="registry-a",
        similarity_threshold=0.92,
    )


def test_lrn_total_task_store_writes_successful_typed_task_entries(tmp_path: Path) -> None:
    store = AgentLrnTotalTaskStore(tmp_path / "lrnt.db")

    entry = store.upsert_entry(_write())

    assert entry.status == "active"
    assert entry.success_count == 1
    assert entry.tasks[0]["id"] == "task_1"
    assert entry.global_constraints == {"format": "summary"}
    assert store.stats().active_entries == 1


def test_lrn_total_task_store_retrieves_exact_normalized_prompt(tmp_path: Path) -> None:
    store = AgentLrnTotalTaskStore(tmp_path / "lrnt.db")
    entry = store.upsert_entry(_write("List changed files and summarize them."))

    candidates = store.retrieve(_lookup("list changed files and summarize them"))

    assert len(candidates) == 1
    assert candidates[0].entry.entry_id == entry.entry_id
    assert candidates[0].score == 1.0


def test_lrn_total_task_store_retrieves_high_similarity_prompt(tmp_path: Path) -> None:
    store = AgentLrnTotalTaskStore(tmp_path / "lrnt.db")
    entry = store.upsert_entry(_write("list changed files and summarize them"))

    candidates = store.retrieve(_lookup("list changed file and summarize them"))

    assert len(candidates) == 1
    assert candidates[0].entry.entry_id == entry.entry_id
    assert candidates[0].score >= 0.92


def test_lrn_total_task_store_misses_low_similarity_prompt(tmp_path: Path) -> None:
    store = AgentLrnTotalTaskStore(tmp_path / "lrnt.db")
    store.upsert_entry(_write("list changed files and summarize them"))

    candidates = store.retrieve(_lookup("inspect docker images and calculate total size"))

    assert candidates == []


def test_lrn_total_task_store_excludes_quarantined_entries(tmp_path: Path) -> None:
    store = AgentLrnTotalTaskStore(tmp_path / "lrnt.db")
    entry = store.upsert_entry(_write())

    quarantined = store.mark_failed(entry.entry_id, failure_category="reuse_failed")

    assert quarantined is not None
    assert quarantined.status == "quarantined"
    assert store.retrieve(_lookup("list changed files and summarize them")) == []
    assert store.stats().quarantined_entries == 1
