"""Deterministic Reliability Kernel eval harness."""

from __future__ import annotations

from agent_runtime.core.ids import new_id
from agent_runtime.reliability.controller import ReliabilityController
from agent_runtime.reliability.models import (
    ReliabilityEvalCase,
    ReliabilityEvalResult,
    RecoveryBudget,
)
from agent_runtime.reliability.store import AgentReliabilityStore, utc_now_iso


DEFAULT_RELIABILITY_EVAL_CASES: tuple[ReliabilityEvalCase, ...] = (
    ReliabilityEvalCase(
        case_id="malformed-json",
        title="Malformed structured output",
        failure_kind="schema_failure",
        expected_recovery="retry_same",
        prompt="Return valid operator JSON for a read-only status request.",
    ),
    ReliabilityEvalCase(
        case_id="shell-placeholder",
        title="Unresolved shell placeholder",
        failure_kind="placeholder_command",
        expected_recovery="repair_plan",
        prompt="Inspect a docker container discovered at runtime.",
    ),
    ReliabilityEvalCase(
        case_id="wrong-cwd",
        title="Wrong working directory",
        failure_kind="cwd_not_found",
        expected_recovery="run_probe",
        prompt="Run tests from the repository root.",
    ),
    ReliabilityEvalCase(
        case_id="bad-shell-syntax",
        title="Bad shell syntax",
        failure_kind="command_failed",
        expected_recovery="repair_plan",
        prompt="Parse command diagnostics and retry with corrected syntax.",
    ),
    ReliabilityEvalCase(
        case_id="command-not-found",
        title="Command not found",
        failure_kind="command_not_found",
        expected_recovery="run_probe",
        prompt="Use available local tooling for inspection.",
    ),
    ReliabilityEvalCase(
        case_id="fake-python-success",
        title="Fake Python success",
        failure_kind="python_failed",
        expected_recovery="repair_plan",
        prompt="Reject Python that reports mutation success without doing it.",
    ),
    ReliabilityEvalCase(
        case_id="stale-verifier",
        title="Stale verifier",
        failure_kind="verification_failed",
        expected_recovery="repair_plan",
        prompt="Verify fresh post-mutation state.",
    ),
    ReliabilityEvalCase(
        case_id="dropped-final-result",
        title="Dropped authoritative result",
        failure_kind="formatting_failed",
        expected_recovery="finalize",
        prompt="Final answer must preserve authoritative execution value.",
    ),
    ReliabilityEvalCase(
        case_id="dropped-version-value",
        title="Dropped required version value",
        failure_kind="authoritative_value_dropped",
        expected_recovery="finalize",
        prompt="Final answer must preserve a required execution value such as 9.7.13.",
    ),
    ReliabilityEvalCase(
        case_id="paraphrased-required-fact",
        title="Paraphrased but correct required fact",
        failure_kind="authoritative_value_dropped",
        expected_recovery="finalize",
        prompt="Semantic coverage should accept faithful paraphrase of required evidence.",
    ),
    ReliabilityEvalCase(
        case_id="unsupported-success-claim",
        title="Unsupported success claim",
        failure_kind="authoritative_value_dropped",
        expected_recovery="finalize",
        prompt="Final answer must not claim success beyond execution evidence.",
    ),
    ReliabilityEvalCase(
        case_id="redacted-sensitive-value",
        title="Sensitive value redaction",
        failure_kind="authoritative_value_dropped",
        expected_recovery="finalize",
        prompt="Redacted obligations should not require repeating secret text verbatim.",
    ),
    ReliabilityEvalCase(
        case_id="repair-loop",
        title="Repeated failed repair",
        failure_kind="budget_exhausted",
        expected_recovery="block",
        prompt="Stop repeated failed recovery instead of looping.",
    ),
    ReliabilityEvalCase(
        case_id="ambiguous-stdout",
        title="Ambiguous stdout",
        failure_kind="command_failed",
        expected_recovery="repair_plan",
        prompt="Treat diagnostic stdout as failure evidence.",
    ),
)


def run_reliability_eval(
    store: AgentReliabilityStore,
    *,
    controller: ReliabilityController | None = None,
) -> ReliabilityEvalResult:
    """Run deterministic reliability decisions without a live LLM."""

    kernel = controller or ReliabilityController(store=store, model_id="deterministic-eval")
    cases: list[dict[str, object]] = []
    recovered = 0
    blocked = 0
    failed = 0
    for case in DEFAULT_RELIABILITY_EVAL_CASES:
        budget = RecoveryBudget()
        if case.failure_kind == "budget_exhausted":
            budget.validation_repairs = budget.max_validation_repairs
            budget.execution_repairs = budget.max_execution_repairs
            budget.schema_repairs = budget.max_schema_repairs
        decision = kernel.decide(case.failure_kind, budget=budget)
        passed = decision.action == case.expected_recovery
        recovered += 1 if passed and decision.action != "block" else 0
        blocked += 1 if passed and decision.action == "block" else 0
        failed += 0 if passed else 1
        cases.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "failure_kind": case.failure_kind,
                "expected_recovery": case.expected_recovery,
                "actual_recovery": decision.action,
                "passed": passed,
                "reason": decision.reason,
            }
        )
    total = len(cases)
    result = ReliabilityEvalResult(
        eval_id=new_id("rel_eval"),
        created_at=utc_now_iso(),
        total_cases=total,
        recovered_cases=recovered,
        blocked_cases=blocked,
        failed_cases=failed,
        score=round((total - failed) / max(1, total), 4),
        cases=cases,
    )
    return store.record_eval(result)
