from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_runtime.api.app import create_app
from agent_runtime.api.config import Settings
from agent_runtime.reliability import (
    AgentReliabilityStore,
    ReliabilityController,
    classify_failure,
    run_reliability_eval,
)


def test_failure_taxonomy_and_decisions_are_stable(tmp_path):
    store = AgentReliabilityStore(tmp_path / "reliability.db")
    controller = ReliabilityController(store=store, model_id="weak-local")

    assert classify_failure({"error": "unresolved_shell_placeholder"}) == "placeholder_command"
    assert classify_failure("Traceback: Python exception failed") == "python_failed"
    assert classify_failure("command not found") == "command_not_found"
    assert classify_failure("schema_validation_error") == "schema_failure"
    assert classify_failure("formatter_dropped_authoritative_result") == "authoritative_value_dropped"

    assert controller.decide("placeholder_command").action == "repair_plan"
    assert controller.decide("cwd_not_found").action == "run_probe"
    assert controller.decide("command_failed").action == "repair_plan"
    assert controller.decide("formatting_failed").action == "finalize"
    assert controller.decide("authoritative_value_dropped").action == "finalize"


def test_store_persists_events_profiles_envelopes_and_evals(tmp_path):
    db_path = tmp_path / "agent_reliability.db"
    store = AgentReliabilityStore(db_path)
    store.record_event(
        request_id="req-1",
        event_kind="failure_detected",
        failure_kind="json_failure",
        title="Bad JSON",
        model_id="tiny-model",
    )
    store.record_event(
        request_id="req-1",
        event_kind="recovery_accepted",
        recovery_action="retry_same",
        title="Retry accepted",
        model_id="tiny-model",
    )
    envelope = store.create_approval_envelope(
        request_id="req-1",
        goal="edit file",
        cwd_values=["."],
        max_risk="medium",
        mutation_budget=2,
    )
    result = run_reliability_eval(store)

    restarted = AgentReliabilityStore(db_path)
    profile = restarted.get_profile("tiny-model")

    assert profile is not None
    assert profile.json_failures == 1
    assert profile.json_validity_rate < 1.0
    assert restarted.get_latest_envelope("req-1").envelope_id == envelope.envelope_id
    assert restarted.list_evals()[0].eval_id == result.eval_id
    assert restarted.summary()["run_count"] >= 1


def test_approval_envelope_accepts_in_scope_repair_and_rejects_expansion(tmp_path):
    store = AgentReliabilityStore(tmp_path / "reliability.db")
    controller = ReliabilityController(store=store, model_id="weak-local", approval_envelope_budget=1)
    original_plan = SimpleNamespace(
        actions=[
            SimpleNamespace(action_id="a1", cwd=".", risk="medium"),
        ]
    )
    controller.create_approval_envelope(
        request_id="req-1",
        goal="modify one file",
        plan=original_plan,
        context={"gateway_node": "localhost"},
    )

    accepted = controller.check_and_consume_approval_envelope(
        request_id="req-1",
        plan=original_plan,
        mutating_action_ids=["a1"],
    )
    expanded_plan = SimpleNamespace(
        actions=[
            SimpleNamespace(action_id="a2", cwd="../outside", risk="high"),
        ]
    )
    rejected = controller.check_and_consume_approval_envelope(
        request_id="req-1",
        plan=expanded_plan,
        mutating_action_ids=["a2"],
    )

    assert accepted["allowed"] is True
    assert rejected["allowed"] is False
    assert "new cwd outside envelope" in rejected["reason"]
    events = store.list_events(request_id="req-1", limit=20)
    assert any(event.event_kind == "approval_envelope_violation" for event in events)


def test_plan_compiler_and_outcome_verifier_record_typed_events(tmp_path):
    store = AgentReliabilityStore(tmp_path / "reliability.db")
    controller = ReliabilityController(
        store=store,
        model_id="weak-local",
        mode="aggressive",
        weak_model_plan_action_cap=1,
    )
    plan = SimpleNamespace(
        actions=[
            SimpleNamespace(action_id="a1", cwd=".", command="echo ok"),
            SimpleNamespace(action_id="a2", cwd="missing-dir", command="cat {{file}}"),
        ]
    )
    diagnostics = controller.compile_plan(
        request_id="req-2",
        plan=plan,
        workspace_root=str(tmp_path),
    )
    verification = controller.verify_outcome(
        request_id="req-2",
        status="success",
        records=[SimpleNamespace(action_id="a1", status="success")],
        final_response="done",
    )

    assert {item["kind"] for item in diagnostics} >= {
        "weak_model_plan_action_cap",
        "cwd_not_found",
        "placeholder_command",
    }
    assert verification.status == "satisfied"
    assert store.get_profile("weak-local").total_events >= 2


class _SchemaQueueLLM:
    model = "semantic-test-model"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.schemas: list[str] = []
        self.prompts: list[str] = []

    def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        self.schemas.append(str(schema.get("title") or ""))
        if not self.responses:
            raise AssertionError("Unexpected reliability LLM call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _version_obligation_payload(request_id="req-evidence"):
    return {
        "request_id": request_id,
        "facts": [
            {
                "obligation_id": "fact_runtime_version",
                "source_action_id": "action_1",
                "label": "Runtime version",
                "value_summary": "9.7.13",
                "raw_evidence_ref": "action_1.stdout",
                "must_report": True,
                "sensitivity": "public",
                "coverage_hint": "Mention the runtime version value or a faithful paraphrase.",
                "confidence": 0.96,
            }
        ],
        "forbidden_claims": [],
        "postconditions": [],
        "source_record_ids": ["action_1"],
        "audit_status": "complete",
        "reason": "The user asked for the runtime version.",
        "confidence": 0.94,
    }


def _version_record():
    return SimpleNamespace(
        action_id="action_1",
        task_id="task_1",
        kind="shell_command",
        status="success",
        stdout="OPENFABRIC_RUNTIME_VERSION=9.7.13\n",
        stderr="",
        exit_code=0,
        output=None,
        error="",
        metadata={},
    )


def test_llm_coverage_marks_dropped_authoritative_value_unsatisfied(tmp_path):
    store = AgentReliabilityStore(tmp_path / "reliability.db")
    controller = ReliabilityController(store=store, model_id="semantic-test-model")
    record = _version_record()
    llm = _SchemaQueueLLM(
        _version_obligation_payload(),
        {
            "verdict": "repair_required",
            "covered_obligations": [],
            "missing_obligations": ["fact_runtime_version"],
            "contradicted_obligations": [],
            "unsupported_claims": [],
            "repair_instruction": "State that the runtime version is 9.7.13.",
            "reason": "The answer says the check completed but omits the required value.",
            "confidence": 0.92,
        },
    )

    obligations = controller.audit_evidence(
        request_id="req-evidence",
        llm_client=llm,
        user_prompt="What OpenFABRIC runtime version is installed?",
        plan=SimpleNamespace(summary="Read the runtime version.", expected_outputs=["version"]),
        records=[record],
    )
    review = controller.review_answer_coverage(
        request_id="req-evidence",
        llm_client=llm,
        user_prompt="What OpenFABRIC runtime version is installed?",
        final_response="The version check completed successfully.",
        obligation_set=obligations,
        records=[record],
    )
    verification = controller.verify_outcome(
        request_id="req-evidence",
        status="success",
        records=[record],
        final_response="The version check completed successfully.",
        obligation_set=obligations,
        coverage_review=review,
    )

    assert verification.status == "unsatisfied"
    assert verification.obligation_status == "missing"
    assert verification.missing_obligations == ["fact_runtime_version"]
    assert controller.decide("authoritative_value_dropped").action == "finalize"
    events = store.list_events(request_id="req-evidence", limit=20)
    assert any(event.event_kind == "evidence_audited" for event in events)
    assert any(event.event_kind == "coverage_reviewed" for event in events)
    assert any(event.failure_kind == "authoritative_value_dropped" for event in events)


def test_llm_coverage_accepts_semantic_paraphrase(tmp_path):
    store = AgentReliabilityStore(tmp_path / "reliability.db")
    controller = ReliabilityController(store=store, model_id="semantic-test-model")
    record = _version_record()
    llm = _SchemaQueueLLM(
        _version_obligation_payload(),
        {
            "verdict": "accept",
            "covered_obligations": ["fact_runtime_version"],
            "missing_obligations": [],
            "contradicted_obligations": [],
            "unsupported_claims": [],
            "repair_instruction": "",
            "reason": "The answer faithfully states the same runtime version.",
            "confidence": 0.95,
        },
    )

    obligations = controller.audit_evidence(
        request_id="req-paraphrase",
        llm_client=llm,
        user_prompt="Which release is running?",
        plan=SimpleNamespace(summary="Read release value.", expected_outputs=["release"]),
        records=[record],
    )
    review = controller.review_answer_coverage(
        request_id="req-paraphrase",
        llm_client=llm,
        user_prompt="Which release is running?",
        final_response="It is running release 9.7.13.",
        obligation_set=obligations,
        records=[record],
    )
    verification = controller.verify_outcome(
        request_id="req-paraphrase",
        status="success",
        records=[record],
        final_response="It is running release 9.7.13.",
        obligation_set=obligations,
        coverage_review=review,
    )

    assert verification.status == "satisfied"
    assert verification.obligation_status == "covered"
    assert verification.coverage_review.covered_obligations == ["fact_runtime_version"]


def test_llm_coverage_flags_unsupported_claims(tmp_path):
    store = AgentReliabilityStore(tmp_path / "reliability.db")
    controller = ReliabilityController(store=store, model_id="semantic-test-model")
    record = _version_record()
    llm = _SchemaQueueLLM(
        _version_obligation_payload(),
        {
            "verdict": "fail",
            "covered_obligations": ["fact_runtime_version"],
            "missing_obligations": [],
            "contradicted_obligations": [],
            "unsupported_claims": ["The answer claims the service was upgraded."],
            "repair_instruction": "Remove the unsupported upgrade claim.",
            "reason": "The version is covered, but the upgrade claim is not supported by execution evidence.",
            "confidence": 0.9,
        },
    )

    obligations = controller.audit_evidence(
        request_id="req-unsupported",
        llm_client=llm,
        user_prompt="Report the version.",
        plan=SimpleNamespace(summary="Read version.", expected_outputs=["version"]),
        records=[record],
    )
    review = controller.review_answer_coverage(
        request_id="req-unsupported",
        llm_client=llm,
        user_prompt="Report the version.",
        final_response="The runtime is 9.7.13 and the service was upgraded successfully.",
        obligation_set=obligations,
        records=[record],
    )
    verification = controller.verify_outcome(
        request_id="req-unsupported",
        status="success",
        records=[record],
        final_response="The runtime is 9.7.13 and the service was upgraded successfully.",
        obligation_set=obligations,
        coverage_review=review,
    )

    assert verification.status == "unsatisfied"
    assert verification.coverage_review.unsupported_claims == [
        "The answer claims the service was upgraded."
    ]


def test_redacted_obligations_do_not_require_secret_verbatim_text(tmp_path):
    store = AgentReliabilityStore(tmp_path / "reliability.db")
    controller = ReliabilityController(store=store, model_id="semantic-test-model")
    obligations = _version_obligation_payload("req-redacted")
    obligations["facts"][0]["sensitivity"] = "redacted"
    obligations["facts"][0]["value_summary"] = "[redacted]"
    llm = _SchemaQueueLLM(obligations)

    obligation_set = controller.audit_evidence(
        request_id="req-redacted",
        llm_client=llm,
        user_prompt="Check whether the token is configured.",
        plan=SimpleNamespace(summary="Read redacted token state.", expected_outputs=["token state"]),
        records=[_version_record()],
    )
    review = controller.review_answer_coverage(
        request_id="req-redacted",
        llm_client=llm,
        user_prompt="Check whether the token is configured.",
        final_response="A token-like value is configured, but it was redacted.",
        obligation_set=obligation_set,
        records=[_version_record()],
    )
    verification = controller.verify_outcome(
        request_id="req-redacted",
        status="success",
        records=[_version_record()],
        final_response="A token-like value is configured, but it was redacted.",
        obligation_set=obligation_set,
        coverage_review=review,
    )

    assert review.verdict == "accept"
    assert verification.status == "satisfied"
    assert verification.obligation_status == "not_applicable"


def test_evidence_auditor_schema_failure_is_not_silent_success(tmp_path):
    store = AgentReliabilityStore(tmp_path / "reliability.db")
    controller = ReliabilityController(store=store, model_id="semantic-test-model")
    llm = _SchemaQueueLLM(ValueError("bad auditor json"))

    obligations = controller.audit_evidence(
        request_id="req-audit-fail",
        llm_client=llm,
        user_prompt="Report the version.",
        plan=SimpleNamespace(summary="Read version.", expected_outputs=["version"]),
        records=[_version_record()],
    )
    verification = controller.verify_outcome(
        request_id="req-audit-fail",
        status="success",
        records=[_version_record()],
        final_response="The version is available.",
        obligation_set=obligations,
    )

    assert obligations.audit_status == "failed"
    assert verification.status == "unknown"
    assert verification.obligation_status == "unknown"
    assert any(
        event.event_kind == "failure_detected" and event.failure_kind == "schema_failure"
        for event in store.list_events(request_id="req-audit-fail", limit=20)
    )


class _FakeAgentRuntime:
    def __init__(self) -> None:
        self.execution_engine = SimpleNamespace(gateway_client=None, result_store=None)


def _settings(tmp_path):
    return Settings(
        workspace_root=tmp_path,
        openai_compat_model_name="OpenFABRIC Echo",
        audio_transcriber_enabled=False,
        agent_reliability_db_path=tmp_path / "agent_reliability.db",
        agent_ui_settings_db_path=tmp_path / "ui_settings.db",
        agent_chats_db_path=tmp_path / "chats.db",
        agent_gateways_db_path=tmp_path / "gateways.db",
        agent_tasks_db_path=tmp_path / "tasks.db",
        agent_monitors_db_path=tmp_path / "monitors.db",
        agent_events_db_path=tmp_path / "events.db",
        agent_command_allowlist_db_path=tmp_path / "allowlist.db",
        agent_learning_ledger_db_path=tmp_path / "learning.db",
        agent_memory_db_path=tmp_path / "memory.db",
        agent_prompts_db_path=tmp_path / "prompts.db",
        agent_plan_cache_db_path=tmp_path / "plan_cache.db",
        agent_computation_cache_db_path=tmp_path / "computation_cache.db",
        agent_parameters_db_path=tmp_path / "parameters.db",
    )


def test_reliability_api_ui_and_trace_summary(tmp_path):
    client = TestClient(create_app(_settings(tmp_path), agent_runtime=_FakeAgentRuntime()))
    page = client.get("/reliability")

    assert page.status_code == 200
    assert "reliability.js" in page.text

    eval_response = client.post("/api/agent/reliability/evals/run")
    assert eval_response.status_code == 200
    assert eval_response.json()["eval"]["score"] >= 0.9

    controls = client.post(
        "/api/agent/runtime-controls",
        json={
            "reliability_mode": "standard",
            "reliability_verifier_enforced": False,
            "reliability_max_recovery_probes": 5,
            "reliability_max_autonomous_repair_attempts": 4,
            "reliability_weak_model_plan_action_cap": 3,
            "reliability_approval_envelope_budget": 1,
        },
    )
    assert controls.status_code == 200
    assert controls.json()["reliability_mode"] == "standard"
    assert controls.json()["reliability_max_recovery_probes"] == 5

    trace_store = client.app.state.agent_trace_store
    reliability_store = client.app.state.agent_reliability_store
    trace = trace_store.create_request("recover this")
    reliability_store.record_event(
        request_id=trace.request_id,
        event_kind="failure_detected",
        failure_kind="command_failed",
        recovery_action="repair_plan",
        title="Command failed",
        model_id="tiny-model",
    )
    trace_store.complete_request(trace.request_id, "done")

    run_response = client.get(f"/api/agent/reliability/runs/{trace.request_id}")
    trace_response = client.get(f"/api/agent/trace/{trace.request_id}")
    report_response = client.get(f"/api/agent/reliability/runs/{trace.request_id}/report.md")

    assert run_response.status_code == 200
    assert run_response.json()["event_count"] == 1
    assert trace_response.json()["reliability_summary"]["failure_count"] == 1
    assert "Reliability Recovery Report" in report_response.text
