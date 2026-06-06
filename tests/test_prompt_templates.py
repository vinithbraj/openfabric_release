from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_runtime.api.agent_ui import AgentEventDraftRequest, _advisory_prompt, _event_draft_prompt
from agent_runtime.api.config import Settings
from agent_runtime.capabilities.sql import SqlAgentService
from agent_runtime.clarification import build_agent_clarification_resolution_prompt
from agent_runtime.core.config import RuntimeConfig
from agent_runtime.core.orchestrator_support.memory_parameters import _MemoryParameterMixin
from agent_runtime.core.types import UserRequest
from agent_runtime.input_pipeline.decomposition import _build_classification_prompt
from agent_runtime.llm.proposals import FailureRepairProposal
from agent_runtime.llm.structured_call import structured_call
from agent_runtime.monitors.drafting import _planner_prompt as _monitor_planner_prompt
from agent_runtime.monitors.manager import AgentMonitorManager
from agent_runtime.monitors.models import AgentMonitorDraftRequest, AgentMonitorRecord
from agent_runtime.operator.memory_compliance_support.lrex_shape_match import (
    build_lrex_shape_match_prompt,
)
from agent_runtime.operator.models import OperatorPlan
from agent_runtime.operator.pipeline import build_operator_clarification_prompt
from agent_runtime.operator.prompts import (
    build_operator_plan_prompt,
    build_operator_tryout_capsule_prompt,
    build_operator_tryout_result_review_prompt,
)
from agent_runtime.parameters.drafting import _planner_prompt as _parameter_planner_prompt
from agent_runtime.parameters.models import (
    AgentParameterDraftRequest,
    AgentParameterMatch,
    AgentParameterRecord,
    AgentParameterSummary,
)
from agent_runtime.prompts import (
    DEFAULT_PROMPT_TEMPLATES_PATH,
    PromptFetcher,
    PromptTemplateRecord,
    PromptTemplateRenderError,
    PromptTemplateStore,
    configure_prompt_fetcher,
    extract_template_variables,
    render_template_body,
)
from agent_runtime.reliability.controller import ReliabilityController


def _defaults(path: Path, *rows: dict[str, Any]) -> Path:
    path.write_text(json.dumps(list(rows)), encoding="utf-8")
    return path


def test_prompt_store_creates_schema_and_seeds_defaults(tmp_path: Path) -> None:
    defaults = _defaults(
        tmp_path / "defaults.json",
        {
            "prompt_key": "unit.example",
            "version": 1,
            "body_lines": ["Hello ${name}"],
            "metadata": {"owner": "test"},
        },
    )
    store = PromptTemplateStore(tmp_path / "prompts.db")

    changed = store.seed_defaults(defaults)

    assert [item.prompt_key for item in changed] == ["unit.example"]
    record = store.get("unit.example")
    assert record is not None
    assert record.version == 1
    assert record.body == "Hello ${name}"
    assert record.metadata == {"owner": "test"}


def test_prompt_store_seed_updates_outdated_versions(tmp_path: Path) -> None:
    store = PromptTemplateStore(tmp_path / "prompts.db")
    store.upsert(PromptTemplateRecord(prompt_key="unit.example", version=1, body="old"))
    defaults = _defaults(
        tmp_path / "defaults.json",
        {"prompt_key": "unit.example", "version": 2, "body_lines": ["new"]},
    )

    changed = store.seed_defaults(defaults)

    assert [item.version for item in changed] == [2]
    assert store.get("unit.example").body == "new"  # type: ignore[union-attr]


def test_git_commit_message_prompt_template_is_not_seeded(tmp_path: Path) -> None:
    store = PromptTemplateStore(tmp_path / "prompts.db")

    changed = store.seed_defaults()
    record = store.get("operator.git_commit_message")

    assert not any(item.prompt_key == "operator.git_commit_message" for item in changed)
    assert record is None
    assert store.get("operator.git_commit_message.approval_question") is None
    assert store.get("operator.git_commit_message.approval_decision_reason") is None
    assert store.get("operator.git_commit_message.user_message_question") is None
    assert store.get("operator.llm_text") is not None
    plan = store.get("operator.plan")
    assert plan is not None
    assert plan.version >= 2
    assert "git commit -F -" in plan.body
    assert "handle exceptions inside each iteration" in plan.body
    assert "operator.git_commit_message" not in plan.body
    python_codegen = store.get("operator.python_code_generation")
    assert python_codegen is not None
    assert "wrap each item iteration in its own try/except" in python_codegen.body


def test_prompt_fetcher_renders_variables(tmp_path: Path) -> None:
    store = PromptTemplateStore(tmp_path / "prompts.db")
    store.upsert(PromptTemplateRecord(prompt_key="unit.render", version=1, body="Hello ${name}"))
    fetcher = PromptFetcher(store)

    assert fetcher.render("unit.render", {"name": "Ada"}) == "Hello Ada"
    assert fetcher.lines("unit.render", {"name": "Ada"}) == ["Hello Ada"]


def test_prompt_fetcher_uses_checked_in_default_when_db_key_missing(tmp_path: Path) -> None:
    fetcher = PromptFetcher(PromptTemplateStore(tmp_path / "prompts.db"))

    with pytest.warns(RuntimeWarning, match="missing from DB"):
        body = fetcher.get("direct_answer")

    assert "You are directly answering a user prompt" in body


def test_prompt_fetcher_falls_back_when_db_template_is_invalid(tmp_path: Path) -> None:
    store = PromptTemplateStore(tmp_path / "prompts.db")
    store.upsert(PromptTemplateRecord(prompt_key="direct_answer", version=99, body="broken ${missing}"))
    fetcher = PromptFetcher(store)

    with pytest.warns(RuntimeWarning, match="could not render"):
        body = fetcher.render("direct_answer")

    assert "DirectAnswerProposal fields" in body


def test_failure_repair_proposal_accepts_repair_alias() -> None:
    proposal = FailureRepairProposal.model_validate(
        {
            "failed_node_id": "node::task_1",
            "proposed_action": "repair",
            "corrected_arguments": {"prompt": "Try the SQL task again with the error context."},
            "confidence": 0.9,
            "reason": "The failed node can be retried with corrected arguments.",
        }
    )

    assert proposal.proposed_action == "retry_with_arguments"


def test_prompt_fetcher_raises_when_template_variable_is_missing(tmp_path: Path) -> None:
    store = PromptTemplateStore(tmp_path / "prompts.db")
    store.upsert(PromptTemplateRecord(prompt_key="unit.render", version=1, body="Hello ${name}"))
    fetcher = PromptFetcher(store)

    with pytest.raises(PromptTemplateRenderError):
        fetcher.render("unit.render")


def test_prompt_store_editor_helpers_update_reset_and_render(tmp_path: Path) -> None:
    defaults = _defaults(
        tmp_path / "defaults.json",
        {
            "prompt_key": "unit.editor",
            "version": 1,
            "body_lines": ["Hello ${name}"],
            "metadata": {"scope": "unit"},
        },
    )
    store = PromptTemplateStore(tmp_path / "prompts.db")
    store.seed_defaults(defaults)

    updated = store.update_body("unit.editor", "Hi ${name}", defaults)

    assert updated.version == 2
    assert updated.body == "Hi ${name}"
    comparison = store.get_for_editor("unit.editor", defaults)
    assert comparison is not None
    assert comparison.edited is True
    assert extract_template_variables(updated.body) == ["name"]
    assert store.render_for_editor("unit.editor", {"name": "Ada"}, defaults) == "Hi Ada"
    assert render_template_body("Bye ${name}", {"name": "Ada"}) == "Bye Ada"

    reset = store.reset_to_default("unit.editor", defaults)

    assert reset.version == 1
    assert reset.body == "Hello ${name}"
    reset_comparison = store.get_for_editor("unit.editor", defaults)
    assert reset_comparison is not None
    assert reset_comparison.edited is False


def test_prompt_store_editor_rejects_invalid_template_body(tmp_path: Path) -> None:
    defaults = _defaults(
        tmp_path / "defaults.json",
        {"prompt_key": "unit.editor", "version": 1, "body_lines": ["Hello"]},
    )
    store = PromptTemplateStore(tmp_path / "prompts.db")
    store.seed_defaults(defaults)

    with pytest.raises(PromptTemplateRenderError):
        store.update_body("unit.editor", "Cost is $5", defaults)

    assert store.get("unit.editor").body == "Hello"  # type: ignore[union-attr]


def test_classification_prompt_uses_db_template_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="input.classification",
            version=99,
            body="DB CLASSIFICATION SENTINEL\nYou are classifying a user prompt for an intelligent agent runtime.",
        )
    )
    configure_prompt_fetcher(db_path)

    prompt = _build_classification_prompt(UserRequest(raw_prompt="what can you do?"))

    assert "DB CLASSIFICATION SENTINEL" in prompt
    assert "User prompt:\nwhat can you do?" in prompt


def test_default_classification_prompt_routes_live_observable_state_to_tools() -> None:
    rows = json.loads(DEFAULT_PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8"))
    classification = next(
        row for row in rows if row.get("prompt_key") == "input.classification"
    )
    body = "\n".join(classification["body_lines"])

    assert "live observable facts that can change while answering" in body
    assert "require tools instead of no-tool limitation answers" in body


def test_event_draft_prompt_uses_db_template_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="events.draft",
            version=99,
            body="DB EVENT SENTINEL\nExtract persistent scheduled Agent UI events from the user's prompt.",
        )
    )
    configure_prompt_fetcher(db_path)

    prompt = _event_draft_prompt(
        AgentEventDraftRequest(prompt="remind me to stretch after 5 minutes"),
        Settings(agent_prompts_db_path=db_path),
    )

    assert "DB EVENT SENTINEL" in prompt
    assert "User prompt:\nremind me to stretch after 5 minutes" in prompt


def test_advisory_prompt_uses_db_template_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="advisory.answer",
            version=99,
            body="DB ADVISORY SENTINEL\nYou are answering in OpenFabric Advisory mode.",
        )
    )
    configure_prompt_fetcher(db_path)

    prompt = _advisory_prompt(
        prompt="show clipboard",
        request_context={
            "gateway_platform_label": "macOS",
            "gateway_shell": "/bin/bash",
            "terminal_cwd": "/Users/vinith/project",
        },
        settings=Settings(agent_prompts_db_path=db_path, workspace_root=tmp_path),
    )

    assert "DB ADVISORY SENTINEL" in prompt
    assert "show clipboard" in prompt
    assert "macOS" in prompt
    assert "/Users/vinith/project" in prompt
    assert "product_identity" in prompt
    assert "local-first typed agent runtime" in prompt
    assert "advisory_terminal_output" not in prompt


def test_advisory_prompt_grounds_openfabric_product_questions(tmp_path: Path) -> None:
    prompt = _advisory_prompt(
        prompt="What can OpenFabric do?",
        request_context={"terminal_cwd": str(tmp_path)},
        settings=Settings(workspace_root=tmp_path),
    )

    assert "What can OpenFabric do?" in prompt
    assert "product_identity" in prompt
    assert "OpenFabric" in prompt
    assert "local-first typed agent runtime" in prompt
    assert "safe local execution" in prompt
    assert "generic cloud orchestration platform" in prompt


def test_advisory_prompt_includes_enabled_terminal_output_and_caps_it(tmp_path: Path) -> None:
    terminal_output = "DROP-ME-" + ("x" * 8100) + "-KEEP-ME"

    prompt = _advisory_prompt(
        prompt="what just happened?",
        request_context={
            "terminal_cwd": "/Users/vinith/project",
            "advisory_terminal_context_enabled": True,
            "advisory_terminal_output": terminal_output,
        },
        settings=Settings(workspace_root=tmp_path),
    )

    assert "advisory_terminal_context_enabled" in prompt
    assert "advisory_terminal_output" in prompt
    assert "-KEEP-ME" in prompt
    assert "DROP-ME" not in prompt


def test_advisory_prompt_omits_terminal_output_when_disabled(tmp_path: Path) -> None:
    prompt = _advisory_prompt(
        prompt="what just happened?",
        request_context={
            "advisory_terminal_context_enabled": False,
            "advisory_terminal_output": "this should stay out of the prompt",
        },
        settings=Settings(workspace_root=tmp_path),
    )

    assert "advisory_terminal_output" not in prompt
    assert "this should stay out of the prompt" not in prompt


def test_operator_plan_prompt_uses_db_template_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="operator.plan",
            version=99,
            body="DB OPERATOR PLAN SENTINEL\nYou are creating an explicit ${mode_label} operator plan for the OpenFabric agent runtime.",
        )
    )
    configure_prompt_fetcher(db_path)

    prompt = build_operator_plan_prompt(UserRequest(raw_prompt="list files"))

    assert "DB OPERATOR PLAN SENTINEL" in prompt
    assert "User prompt:\nlist files" in prompt


def test_operator_gateway_context_prompt_uses_db_template_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="operator.gateway_context",
            version=99,
            body="DB GATEWAY CONTEXT SENTINEL\n${gateway_context_json}\n${platform_guidance}",
        )
    )
    configure_prompt_fetcher(db_path)

    prompt = build_operator_plan_prompt(
        UserRequest(
            raw_prompt="copy screenshot to clipboard",
            session_context={
                "gateway_platform": "macos",
                "gateway_platform_label": "macOS",
                "gateway_shell": "/bin/bash",
                "gateway_command_profile": "posix-bash-macos",
            },
        )
    )

    assert "DB GATEWAY CONTEXT SENTINEL" in prompt
    assert '"gateway_platform": "macos"' in prompt
    assert "osascript" in prompt
    assert "pbcopy" in prompt


def test_operator_tryout_prompts_use_db_template_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="operator.tryout_capsule",
            version=99,
            body="DB TRYOUT CAPSULE SENTINEL\n${schema_json}",
        )
    )
    store.upsert(
        PromptTemplateRecord(
            prompt_key="operator.tryout_result_review",
            version=99,
            body="DB TRYOUT REVIEW SENTINEL\n${schema_json}",
        )
    )
    configure_prompt_fetcher(db_path)
    request = UserRequest(raw_prompt="list files")

    capsule_prompt = build_operator_tryout_capsule_prompt(request)
    review_prompt = build_operator_tryout_result_review_prompt(
        request,
        OperatorPlan(summary="empty"),
        [],
    )

    assert "DB TRYOUT CAPSULE SENTINEL" in capsule_prompt
    assert "OperatorTryoutCapsuleBatch" in capsule_prompt
    assert "User prompt:\nlist files" in capsule_prompt
    assert "DB TRYOUT REVIEW SENTINEL" in review_prompt
    assert "OperatorTryoutResultReview" in review_prompt
    assert "User prompt:\nlist files" in review_prompt


class _SqlPromptClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        if schema.get("title") == "SqlResultContractReview":
            return {
                "decision": "accept",
                "confidence": 1.0,
                "request_intent": "count rows",
                "expected_result_shape": "one count row",
                "observed_result_shape": "one count row",
                "missing_requirements": [],
                "retry_instruction": "",
                "reason": "The result matches.",
            }
        return {"summary": "ok", "confidence": 1.0}


def test_sql_agent_prompts_use_db_template_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_runtime.capabilities.sql_support.common import _ResolvedProfile
    from agent_runtime.parameters.models import AgentParameterRecord

    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="sql.agent_action",
            version=99,
            body="DB SQL ACTION SENTINEL ${limit} ${agent_clarification_mode}",
        )
    )
    store.upsert(
        PromptTemplateRecord(
            prompt_key="sql.result_contract_review",
            version=99,
            body="DB SQL CONTRACT SENTINEL",
        )
    )
    store.upsert(
        PromptTemplateRecord(
            prompt_key="sql.result_summary",
            version=99,
            body="DB SQL SUMMARY SENTINEL",
        )
    )
    store.upsert(
        PromptTemplateRecord(
            prompt_key="sql.discovery_summary",
            version=99,
            body="DB SQL DISCOVERY SENTINEL",
        )
    )
    configure_prompt_fetcher(db_path)
    client = _SqlPromptClient()
    service = SqlAgentService(
        parameter_store=None,
        gateway_client=None,
        llm_client=client,
        memory_store=None,
        config=RuntimeConfig(),
    )
    resolved = _ResolvedProfile(
        record=AgentParameterRecord(
            key="pg",
            normalized_key="pg",
            value_json={},
            context_json={},
            created_at="",
            updated_at="",
        ),
        profile={"engine": "postgresql"},
        values={},
    )

    action_prompt = service._build_sql_action_prompt(
        user_request="count patients",
        resolved=resolved,
        schema={"tables": []},
        limit=25,
        attempts=[],
        warnings=[],
    )
    review = service._review_sql_result_contract(
        prompt="count patients",
        generated_sql="select count(*) as count from patients limit 25",
        executed_sql="select count(*) as count from patients limit 25",
        result_strategy="final_step",
        columns=["count"],
        rows=[{"count": 1}],
        truncated=False,
    )
    summary = service._summarize_result(
        prompt="count patients",
        sql="select count(*) as count from patients limit 25",
        columns=["count"],
        rows=[{"count": 1}],
        assumptions=[],
    )
    discovery = service._summarize_discovery(
        {"schemas": ["public"], "tables": []},
        prompt="show schemas",
        columns=["schema"],
        rows=[{"schema": "public"}],
    )

    assert "DB SQL ACTION SENTINEL 25 balanced" in action_prompt
    assert review["decision"] == "accept"
    assert summary == "ok"
    assert discovery == "ok"
    assert "DB SQL CONTRACT SENTINEL" in client.prompts[0]
    assert "DB SQL SUMMARY SENTINEL" in client.prompts[1]
    assert "DB SQL DISCOVERY SENTINEL" in client.prompts[2]


def test_remaining_side_prompts_use_db_template_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    sentinels = {
        "parameters.draft": "DB PARAMETER DRAFT SENTINEL",
        "parameters.database_profile_adjudication": "DB PARAMETER DB ADJUDICATION SENTINEL",
        "monitors.plan": "DB MONITOR PLAN SENTINEL ${max_duration_seconds} ${judge_min_interval_seconds}",
        "monitors.judge": "DB MONITOR JUDGE SENTINEL",
        "reliability.evidence_auditor": "DB EVIDENCE AUDITOR SENTINEL",
        "reliability.coverage_verifier": "DB COVERAGE VERIFIER SENTINEL",
        "operator.lrex_shape_match": "DB LREX SHAPE SENTINEL",
    }
    for key, body in sentinels.items():
        store.upsert(PromptTemplateRecord(prompt_key=key, version=99, body=body))
    configure_prompt_fetcher(db_path)

    parameter_prompt = _parameter_planner_prompt(
        AgentParameterDraftRequest(prompt="store this parameter")
    )
    monitor_prompt = _monitor_planner_prompt(
        AgentMonitorDraftRequest(prompt="monitor free RAM"),
        max_duration_seconds=900,
        judge_min_interval_seconds=30,
    )
    monitor_judge_prompt = AgentMonitorManager(
        monitor_store=None,  # type: ignore[arg-type]
        event_store=None,
    )._judge_prompt(
        AgentMonitorRecord(
            monitor_id="mon_1",
            title="RAM",
            prompt="monitor free RAM",
            command="free -m",
            natural_language_condition="memory pressure is high",
            trigger_mode="llm_judged",
        ),
        recent_output="Mem: 95% used",
        exit_code=0,
    )
    reliability = ReliabilityController(store=None)
    evidence_prompt = reliability._build_evidence_auditor_prompt(
        request_id="req_1",
        user_prompt="show status",
        plan=None,
        records=[],
        context={},
        source_preview_chars=500,
    )
    coverage_prompt = reliability._build_coverage_verifier_prompt(
        user_prompt="show status",
        final_response="Status is ok.",
        obligation_set=None,
        plan=None,
        records=[],
        source_preview_chars=500,
    )
    lrex_prompt = build_lrex_shape_match_prompt(
        current_shape={"task": "commit"},
        candidate_shapes=[{"candidate_id": "cache_1"}],
        prior_outputs=[],
    )

    class _CaptureClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(prompt)
            return {"decision": "clarify", "confidence": 1.0, "reason": "ambiguous"}

    record_a = AgentParameterRecord(
        key="db_a",
        normalized_key="db_a",
        value_json={"host": "db-a.local", "database": "app", "user": "readonly"},
        tags=["database"],
        created_at="",
        updated_at="",
    )
    record_b = AgentParameterRecord(
        key="db_b",
        normalized_key="db_b",
        value_json={"host": "db-b.local", "database": "app", "user": "readonly"},
        tags=["database"],
        created_at="",
        updated_at="",
    )
    matches = [
        AgentParameterMatch(
            record=record_a,
            summary=AgentParameterSummary(key="db_a", normalized_key="db_a"),
            score=10,
            exact=False,
        ),
        AgentParameterMatch(
            record=record_b,
            summary=AgentParameterSummary(key="db_b", normalized_key="db_b"),
            score=9,
            exact=False,
        ),
    ]
    capture_client = _CaptureClient()
    runtime = type("Runtime", (_MemoryParameterMixin,), {"llm_client": capture_client})()
    runtime._adjudicate_database_parameter_matches(
        UserRequest(raw_prompt="use the app database"),
        matches,
    )

    assert "DB PARAMETER DRAFT SENTINEL" in parameter_prompt
    assert "DB MONITOR PLAN SENTINEL 900 30" in monitor_prompt
    assert "DB MONITOR JUDGE SENTINEL" in monitor_judge_prompt
    assert "DB EVIDENCE AUDITOR SENTINEL" in evidence_prompt
    assert "DB COVERAGE VERIFIER SENTINEL" in coverage_prompt
    assert "DB LREX SHAPE SENTINEL" in lrex_prompt
    assert "DB PARAMETER DB ADJUDICATION SENTINEL" in capture_client.prompts[0]


def test_clarification_resolution_prompt_uses_db_template_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="clarification.resolution",
            version=99,
            body=(
                "DB CLARIFICATION RESOLUTION SENTINEL\n"
                "Mode: ${mode}\n"
                "Prompt: ${user_prompt}\n"
                "Candidates: ${candidates_json}"
            ),
        )
    )
    configure_prompt_fetcher(db_path)

    prompt = build_agent_clarification_resolution_prompt(
        mode="balanced",
        user_prompt="summarize the report as json",
        proposed_question="Which output format should I use?",
        missing_information="Output format",
        reason="The output format was not selected explicitly.",
        candidates=[{"entity_type": "option", "value": "json"}],
    )

    assert "DB CLARIFICATION RESOLUTION SENTINEL" in prompt
    assert "Mode: balanced" in prompt
    assert "summarize the report as json" in prompt
    assert '"value": "json"' in prompt


def test_operator_clarification_mode_policy_uses_db_template_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="operator.clarification",
            version=99,
            body=(
                "DB OPERATOR CLARIFICATION SENTINEL\n"
                "${mode_label}\n"
                "${mode_policy}\n"
                "${schema_json}"
            ),
        )
    )
    store.upsert(
        PromptTemplateRecord(
            prompt_key="clarification.mode_policy.balanced",
            version=99,
            body="DB BALANCED CLARIFICATION POLICY",
        )
    )
    configure_prompt_fetcher(db_path)

    prompt = build_operator_clarification_prompt(
        UserRequest(
            raw_prompt="summarize the report",
            session_context={"agent_clarification_mode": "balanced"},
        )
    )

    assert "DB OPERATOR CLARIFICATION SENTINEL" in prompt
    assert "DB BALANCED CLARIFICATION POLICY" in prompt
    assert "OperatorClarificationDecision" in prompt


class _RepairClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return {"answer": "ok"}
        return {"answer": "ok", "needs_clarification": False}


def test_schema_repair_uses_db_template_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import BaseModel, ConfigDict

    class TinyProposal(BaseModel):
        model_config = ConfigDict(extra="forbid")

        answer: str
        needs_clarification: bool

    db_path = tmp_path / "prompts.db"
    monkeypatch.setenv("AOR_AGENT_PROMPTS_DB_PATH", str(db_path))
    store = PromptTemplateStore(db_path)
    store.seed_defaults()
    store.upsert(
        PromptTemplateRecord(
            prompt_key="llm.schema_repair",
            version=99,
            body=(
                "DB SCHEMA REPAIR SENTINEL\n"
                "Repair a malformed JSON object from a structured LLM call.\n"
                "Expected schema name:\n${schema_name}\n"
                "Expected JSON schema:\n${schema_json}\n"
                "Invalid payload:\n${invalid_payload_json}\n"
                "Pydantic validation errors:\n${validation_error}"
            ),
        )
    )
    configure_prompt_fetcher(db_path)
    client = _RepairClient()

    structured_call(client, "classify", TinyProposal)

    assert "DB SCHEMA REPAIR SENTINEL" in client.prompts[1]
