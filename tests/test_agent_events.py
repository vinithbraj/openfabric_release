from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_runtime.events import AgentEventCreate, AgentEventStore, AgentEventUpdate


def _iso(delta_seconds: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=delta_seconds)).isoformat()


def test_event_store_creates_lists_updates_and_deletes(tmp_path) -> None:
    store = AgentEventStore(tmp_path / "events.db")

    event = store.create_event(
        AgentEventCreate(
            title="Check disk",
            prompt="check disk space",
            interval_seconds=3600,
            context={"gateway_id": "gw-local"},
        )
    )

    assert event.event_id.startswith("evt-")
    assert event.status == "active"
    assert event.auto_approve_confirmations is True
    assert event.context["gateway_id"] == "gw-local"
    assert store.list_events()[0].event_id == event.event_id

    updated = store.update_event(
        event.event_id,
        AgentEventUpdate(status="paused", interval_seconds=7200),
    )

    assert updated is not None
    assert updated.status == "paused"
    assert updated.interval_seconds == 7200

    deleted = store.delete_event(event.event_id)

    assert deleted is not None
    assert deleted.status == "deleted"
    assert store.list_events() == []
    assert store.list_events(include_deleted=True)[0].event_id == event.event_id


def test_event_store_persists_and_claims_due_events_once(tmp_path) -> None:
    path = tmp_path / "events.db"
    store = AgentEventStore(path)
    event = store.create_event(
        AgentEventCreate(
            title="Heartbeat",
            prompt="say heartbeat",
            interval_seconds=3600,
            next_run_at=_iso(-10),
        )
    )

    reopened = AgentEventStore(path)
    due = reopened.claim_due_events(now=_iso())

    assert [item.event_id for item in due] == [event.event_id]
    claimed = reopened.get_event(event.event_id)
    assert claimed is not None
    assert claimed.last_run_at
    assert claimed.next_run_at > claimed.last_run_at
    assert reopened.claim_due_events(now=_iso()) == []


def test_event_store_records_runs_and_skips_overlapping_due_event(tmp_path) -> None:
    store = AgentEventStore(tmp_path / "events.db")
    event = store.create_event(
        AgentEventCreate(
            title="Check",
            prompt="check state",
            interval_seconds=3600,
            next_run_at=_iso(-10),
        )
    )
    run = store.create_run(event_id=event.event_id, scheduled_for=_iso(-10), request_id="req-open")

    assert run.status == "running"
    assert store.claim_due_events(now=_iso()) == []
    runs = store.list_runs(event.event_id)
    assert {item.status for item in runs} == {"running", "skipped"}

    store.update_run(run.event_run_id, status="completed", completed_at=_iso())
    event = store.update_event(event.event_id, AgentEventUpdate(next_run_at=_iso(-5)))
    assert event is not None

    assert [item.event_id for item in store.claim_due_events(now=_iso())] == [event.event_id]


def test_event_store_preserves_run_preview_markdown_layout(tmp_path) -> None:
    store = AgentEventStore(tmp_path / "events.db")
    event = store.create_event(
        AgentEventCreate(
            title="Git status",
            prompt="check git status",
            interval_seconds=3600,
        )
    )
    run = store.create_run(event_id=event.event_id, scheduled_for=_iso(), request_id="req-git")

    preview = (
        "| Status | Path |\n"
        "|--------|------|\n"
        "| M | src/app.py |\n"
        "\n"
        "```text\n"
        " M src/app.py\n"
        "?? tests/test_app.py\n"
        "```"
    )

    updated = store.update_run(run.event_run_id, final_response_preview=preview)

    assert updated is not None
    assert updated.final_response_preview == preview
