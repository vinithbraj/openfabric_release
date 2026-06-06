from __future__ import annotations

from fastapi.testclient import TestClient

from agent_runtime.api.app import create_app
from agent_runtime.api.config import Settings
from agent_runtime.command_template_cache import (
    AgentCommandTemplateCacheStore,
    CommandTemplateLookupContext,
    CommandTemplateWrite,
)
from agent_runtime.computation_cache import AgentComputationCacheStore, ComputationCacheWrite
from agent_runtime.learning_ledger import AgentLearningLedgerStore
from agent_runtime.lrn_total_tasks import AgentLrnTotalTaskStore, LrnTotalTaskWrite
from agent_runtime.plan_cache import AgentPlanCacheStore, PlanCacheLookupContext, PlanCacheWrite


class _Runtime:
    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        return raw_prompt


def _settings(tmp_path):
    return Settings(
        openai_compat_model_name="OpenFABRIC Echo",
        agent_chats_db_path=tmp_path / "chats.db",
        agent_events_db_path=tmp_path / "events.db",
        agent_tasks_db_path=tmp_path / "tasks.db",
        agent_monitors_db_path=tmp_path / "monitors.db",
        agent_gateways_db_path=tmp_path / "gateways.db",
        agent_ui_settings_db_path=tmp_path / "ui-settings.db",
        agent_memory_db_path=tmp_path / "memory.db",
        agent_learning_ledger_db_path=tmp_path / "ledger.db",
        agent_command_allowlist_db_path=tmp_path / "allowlist.db",
        agent_command_template_cache_db_path=tmp_path / "command-cache.db",
        agent_plan_cache_db_path=tmp_path / "plan-cache.db",
        agent_computation_cache_db_path=tmp_path / "computation-cache.db",
        agent_lrn_total_tasks_db_path=tmp_path / "lrnt.db",
    )


def _client(tmp_path, runtime):
    return TestClient(create_app(_settings(tmp_path), agent_runtime=runtime))


def test_command_step_correction_replaces_template_and_records_learning(tmp_path):
    runtime = _Runtime()
    store = AgentCommandTemplateCacheStore(tmp_path / "command-cache.db")
    runtime.command_template_cache_store = store
    entry = store.upsert_entry(
        CommandTemplateWrite(
            prompt="list python files",
            step_description="list python files",
            model_name="model-a",
            command_template="ls *.py",
            observed_command="ls *.py",
            lr_mode="lr",
        )
    )
    client = _client(tmp_path, runtime)

    snapshot = client.get(
        f"/api/agent/corrections/artifact/lr_d/{entry.template_id}"
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["editable"]["command"] == "ls *.py"

    response = client.post(
        "/api/agent/corrections/step",
        json={
            "artifact_ref": {
                "artifact_kind": "lr_d",
                "artifact_id": entry.template_id,
                "request_id": "req-correction-1",
            },
            "correction_kind": "command",
            "corrected_command": "find . -maxdepth 1 -name '*.py' -print",
            "instruction": "Use find when shell glob expansion can fail.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    replacement_id = payload["replacement_ids"]["command_template"]
    assert replacement_id != entry.template_id
    assert payload["lesson_id"]
    assert payload["memory_id"]
    assert store.get_entry(entry.template_id).failure_count == 1
    replacement = store.get_entry(replacement_id)
    assert replacement is not None
    assert replacement.command_template == "find . -maxdepth 1 -name '*.py' -print"

    candidates = store.retrieve(
        CommandTemplateLookupContext(
            prompt="list python files",
            step_description="list python files",
            model_name="model-a",
            lr_mode="lr",
        )
    )
    assert [candidate.entry.template_id for candidate in candidates] == [replacement_id]


def test_plan_cache_mark_failed_excludes_original_from_retrieval(tmp_path):
    store = AgentPlanCacheStore(tmp_path / "plan-cache.db")
    entry = store.upsert_entry(
        PlanCacheWrite(
            prompt="show repo status",
            model_name="model-a",
            plan={"summary": "status", "tasks": [], "actions": [], "dependencies": []},
        )
    )

    store.mark_failed(entry.cache_id, failure_category="user_corrected", repair_notes="Wrong plan.")

    failed = store.get_entry(entry.cache_id)
    assert failed is not None
    assert failed.failure_count == 1
    assert failed.failure_category == "user_corrected"
    assert not store.retrieve(
        PlanCacheLookupContext(prompt="show repo status", model_name="model-a")
    )


def test_lr_t_instruction_only_correction_quarantines_and_saves_guidance(tmp_path):
    runtime = _Runtime()
    store = AgentLrnTotalTaskStore(tmp_path / "lrnt.db")
    runtime.lrn_total_task_store = store
    entry = store.upsert_entry(
        LrnTotalTaskWrite(
            prompt="summarize README",
            model_name="model-a",
            tasks=[
                {
                    "id": "task_1",
                    "description": "summarize README",
                    "semantic_verb": "summarize",
                    "object_type": "file",
                    "intent_confidence": 0.9,
                }
            ],
        )
    )
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/agent/corrections/step",
        json={
            "artifact_ref": {
                "artifact_kind": "lr_t",
                "artifact_id": entry.entry_id,
                "request_id": "req-correction-2",
            },
            "correction_kind": "instruction",
            "instruction": "Relearn this decomposition because README requests must read the file before summarizing.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["quarantined_ids"] == [entry.entry_id]
    assert payload["replacement_ids"] == {}
    assert payload["lesson_id"]
    assert store.get_entry(entry.entry_id).status == "quarantined"


def test_computation_correction_rejects_unsafe_code_without_replacement(tmp_path):
    runtime = _Runtime()
    store = AgentComputationCacheStore(tmp_path / "computation-cache.db")
    runtime.computation_cache_store = store
    entry = store.upsert_entry(
        ComputationCacheWrite(
            prompt="count rows",
            model_name="model-a",
            action_kind="python_compute",
            input_signature="rows",
            code_template="def compute(inputs):\n    return len(inputs.get('rows', []))",
        )
    )
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/agent/corrections/step",
        json={
            "artifact_ref": {
                "artifact_kind": "computation_cache",
                "artifact_id": entry.cache_id,
                "request_id": "req-correction-3",
            },
            "correction_kind": "code",
            "corrected_code": "def compute(inputs):\n    open('/tmp/out', 'w').write('x')\n    return 1",
        },
    )

    assert response.status_code == 400
    assert "side-effect free" in response.text
    assert store.get_entry(entry.cache_id).failure_count == 0


def test_delete_learned_command_step_removes_only_selected_row_and_records_audit(tmp_path):
    runtime = _Runtime()
    store = AgentCommandTemplateCacheStore(tmp_path / "command-cache.db")
    runtime.command_template_cache_store = store
    entry = store.upsert_entry(
        CommandTemplateWrite(
            prompt="create cleanup branch",
            step_description="append cleanup suffix",
            model_name="model-a",
            command_template='echo "$OF_INPUT_BRANCH_NAME"',
            observed_command='echo "$OF_INPUT_BRANCH_NAME"',
            lr_mode="lr_ex",
            payload_bindings=[{"input_name": "branch_name", "binding_kind": "env"}],
            direct_action={"kind": "shell_command", "command": 'echo "$OF_INPUT_BRANCH_NAME"'},
        )
    )
    sibling = store.upsert_entry(
        CommandTemplateWrite(
            prompt="create cleanup branch sibling",
            step_description="append cleanup suffix",
            model_name="model-a",
            command_template='printf "%s_cleanup\\n" "$OF_INPUT_BRANCH_NAME"',
            observed_command='printf "%s_cleanup\\n" "$OF_INPUT_BRANCH_NAME"',
            lr_mode="lr_ex",
            payload_bindings=[{"input_name": "branch_name", "binding_kind": "env"}],
            direct_action={"kind": "shell_command", "command": 'printf "%s_cleanup\\n" "$OF_INPUT_BRANCH_NAME"'},
        )
    )
    client = _client(tmp_path, runtime)
    trace = client.app.state.agent_trace_store.create_request("create cleanup branch")

    response = client.post(
        "/api/agent/corrections/step/delete",
        json={
            "artifact_ref": {
                "artifact_kind": "lr_ex",
                "artifact_id": entry.template_id,
                "request_id": trace.request_id,
            },
            "reason": "Bad LR-EX template echoed the original value.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "deleted"
    assert payload["deleted_ids"] == [entry.template_id]
    assert payload["lesson_id"] == ""
    assert payload["memory_id"] == ""
    assert store.get_entry(entry.template_id) is None
    assert store.get_entry(sibling.template_id) is not None
    candidates = store.retrieve_lrex_shape_candidates(
        CommandTemplateLookupContext(
            prompt="create cleanup branch",
            step_description="append cleanup suffix",
            model_name="model-a",
            lr_mode="lr_ex",
        )
    )
    assert entry.template_id not in [candidate.entry.template_id for candidate in candidates]
    assert sibling.template_id in [candidate.entry.template_id for candidate in candidates]

    ledger = AgentLearningLedgerStore(tmp_path / "ledger.db")
    events = ledger.run_cache_events(trace.request_id)
    assert [(event.event, event.status, event.cache_id) for event in events] == [
        ("user_deleted", "deleted", entry.template_id)
    ]
    updated_trace = client.app.state.agent_trace_store.get_trace(trace.request_id)
    assert updated_trace is not None
    assert any(
        event.event_type == "learning.step_correction.deleted"
        and event.detail["deleted_ids"] == [entry.template_id]
        for event in updated_trace.events
    )


def test_delete_lr_d_step_removes_selected_row_from_retrieval(tmp_path):
    runtime = _Runtime()
    store = AgentCommandTemplateCacheStore(tmp_path / "command-cache.db")
    runtime.command_template_cache_store = store
    entry = store.upsert_entry(
        CommandTemplateWrite(
            prompt="list python files",
            step_description="list python files",
            model_name="model-a",
            command_template="ls *.py",
            observed_command="ls *.py",
            lr_mode="lr",
        )
    )
    client = _client(tmp_path, runtime)

    response = client.post(
        "/api/agent/corrections/step/delete",
        json={
            "artifact_ref": {
                "artifact_kind": "lr_d",
                "artifact_id": entry.template_id,
                "request_id": "req-delete-lrd",
            }
        },
    )

    assert response.status_code == 200
    assert store.get_entry(entry.template_id) is None
    assert not store.retrieve(
        CommandTemplateLookupContext(
            prompt="list python files",
            step_description="list python files",
            model_name="model-a",
            lr_mode="lr",
        )
    )


def test_delete_plan_cache_lr_t_and_computation_artifacts(tmp_path):
    runtime = _Runtime()
    plan_store = AgentPlanCacheStore(tmp_path / "plan-cache.db")
    lr_t_store = AgentLrnTotalTaskStore(tmp_path / "lrnt.db")
    computation_store = AgentComputationCacheStore(tmp_path / "computation-cache.db")
    runtime.plan_cache_store = plan_store
    runtime.lrn_total_task_store = lr_t_store
    runtime.computation_cache_store = computation_store
    plan = plan_store.upsert_entry(
        PlanCacheWrite(
            prompt="show repo status",
            model_name="model-a",
            plan={"summary": "status", "tasks": [], "actions": [], "dependencies": []},
        )
    )
    lr_t = lr_t_store.upsert_entry(
        LrnTotalTaskWrite(
            prompt="summarize README",
            model_name="model-a",
            tasks=[
                {
                    "id": "task_1",
                    "description": "summarize README",
                    "semantic_verb": "summarize",
                    "object_type": "file",
                    "intent_confidence": 0.9,
                }
            ],
        )
    )
    computation = computation_store.upsert_entry(
        ComputationCacheWrite(
            prompt="count rows",
            model_name="model-a",
            action_kind="python_compute",
            input_signature="rows",
            code_template="def compute(inputs):\n    return len(inputs.get('rows', []))",
        )
    )
    client = _client(tmp_path, runtime)

    for kind, artifact_id in [
        ("plan_cache", plan.cache_id),
        ("lr_t", lr_t.entry_id),
        ("computation_cache", computation.cache_id),
    ]:
        response = client.post(
            "/api/agent/corrections/step/delete",
            json={"artifact_ref": {"artifact_kind": kind, "artifact_id": artifact_id}},
        )
        assert response.status_code == 200
        assert response.json()["deleted_ids"] == [artifact_id]

    assert plan_store.get_entry(plan.cache_id) is None
    assert lr_t_store.get_entry(lr_t.entry_id) is None
    assert computation_store.get_entry(computation.cache_id) is None


def test_delete_missing_learned_artifact_returns_404(tmp_path):
    client = _client(tmp_path, _Runtime())

    response = client.post(
        "/api/agent/corrections/step/delete",
        json={"artifact_ref": {"artifact_kind": "lr_d", "artifact_id": "cmdtpl-missing"}},
    )

    assert response.status_code == 404
