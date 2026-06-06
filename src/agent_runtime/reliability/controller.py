"""Reliability controller for typed failure recovery and outcome verification."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent_runtime.observability.redaction import redact_value
from agent_runtime.reliability.models import (
    AnswerCoverageReview,
    AnswerObligationSet,
    EvidenceObligation,
    FailureKind,
    OutcomeVerification,
    RecoveryBudget,
    RecoveryDecision,
    ReliabilityMode,
)
from agent_runtime.reliability.store import AgentReliabilityStore
from agent_runtime.prompts import prompt_lines
from agent_runtime.settings_consolidation import repair_settings_from_profile


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def model_id_from_client(client: Any) -> str:
    """Return a stable model id from common LLM client wrappers."""

    current = client
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        model = str(getattr(current, "model", "") or "").strip()
        if model:
            return model
        current = getattr(current, "inner", None)
    return "unknown"


def classify_failure(payload: Any) -> FailureKind:
    """Map heterogeneous validation/execution details to a stable failure kind."""

    text = str(payload or "").lower()
    if "invalid_json" in text or ("json" in text and "schema" not in text):
        return "json_failure"
    if "schema_validation_error" in text or "pydantic" in text or "schema" in text:
        return "schema_failure"
    if "unresolved_shell_placeholder" in text or "placeholder" in text:
        return "placeholder_command"
    if "cwd" in text or "working directory" in text or "no such file or directory" in text:
        return "cwd_not_found"
    if "command_not_found" in text or ("not found" in text and "command" in text):
        return "command_not_found"
    if "deferred_code" in text or "deferred python" in text:
        return "deferred_python_failed"
    if "traceback" in text or ("python" in text and ("exception" in text or "failed" in text)):
        return "python_failed"
    if (
        "authoritative_value_dropped" in text
        or "authoritative value" in text
        or "dropped_authoritative" in text
        or "authoritative_result" in text
    ):
        return "authoritative_value_dropped"
    if "verification" in text or "stale_failure_verifier" in text:
        return "verification_failed"
    if "completion" in text or "final answer" in text:
        return "completion_failed"
    if "formatter" in text or "formatting" in text:
        return "formatting_failed"
    if "budget" in text or "attempt limit" in text:
        return "budget_exhausted"
    if "validation" in text or "operator plan rejected" in text:
        return "validation_failure"
    if "exit_code" in text or "stderr" in text or "non-zero" in text or "failed" in text:
        return "command_failed"
    return "unknown"


class ReliabilityController:
    """Central Reliability Kernel decision and recording point."""

    def __init__(
        self,
        *,
        store: AgentReliabilityStore | None,
        model_id: str = "unknown",
        mode: ReliabilityMode = "aggressive",
        max_recovery_probes: int = 3,
        max_autonomous_repair_attempts: int = 2,
        weak_model_plan_action_cap: int = 4,
        verifier_enforced: bool = True,
        approval_envelope_budget: int = 2,
    ) -> None:
        self.store = store
        self.model_id = str(model_id or "unknown")
        self.mode = mode if mode in {"off", "standard", "aggressive"} else "aggressive"
        self.max_recovery_probes = max(0, int(max_recovery_probes))
        self.max_autonomous_repair_attempts = max(0, int(max_autonomous_repair_attempts))
        self.weak_model_plan_action_cap = max(1, int(weak_model_plan_action_cap))
        self.verifier_enforced = bool(verifier_enforced)
        self.approval_envelope_budget = max(0, int(approval_envelope_budget))

    @property
    def enabled(self) -> bool:
        return self.mode != "off" and self.store is not None

    def budget_from_context(self, context: dict[str, Any] | None = None) -> RecoveryBudget:
        """Build a request budget from runtime context and reliability defaults."""

        raw = dict(context or {})
        repair = repair_settings_from_profile(raw.get("repair_profile"))

        def int_setting(key: str, default: int) -> int:
            value = raw.get(key, default)
            if value is None:
                value = default
            try:
                return int(value)
            except (TypeError, ValueError):
                return int(default)

        return RecoveryBudget(
            max_validation_repairs=repair.max_validation_repairs,
            max_schema_repairs=1,
            max_execution_repairs=repair.max_execution_repairs,
            max_probes=int_setting("reliability_max_recovery_probes", self.max_recovery_probes),
            max_completion_continuations=repair.max_completion_repairs,
            max_rephrases=1,
            max_mutation_envelope_uses=int(
                int_setting("reliability_approval_envelope_budget", self.approval_envelope_budget)
            ),
        )

    def record_failure(
        self,
        *,
        request_id: str,
        stage: str,
        payload: Any,
        title: str = "Reliability failure detected",
        summary: str = "",
        failure_kind: FailureKind | None = None,
    ) -> FailureKind:
        """Normalize and persist one failure event."""

        kind = failure_kind or classify_failure(payload)
        if self.enabled:
            self.store.record_event(
                request_id=request_id,
                event_kind="failure_detected",
                failure_kind=kind,
                stage=stage,
                title=title,
                summary=summary or _summary_from_payload(payload),
                model_id=self.model_id,
                status="warning",
                evidence={"payload": _safe_preview(payload)},
            )
        return kind

    def decide(self, failure_kind: FailureKind, *, budget: RecoveryBudget | None = None) -> RecoveryDecision:
        """Return the default recovery decision for one failure kind."""

        budget = budget or RecoveryBudget()
        if failure_kind in {"schema_failure", "json_failure"}:
            if not budget.exhausted("schema_repairs"):
                return RecoveryDecision(
                    action="retry_same",
                    failure_kind=failure_kind,
                    reason="Structured output failed; retry the same semantic stage with schema repair.",
                    confidence=0.8,
                    budget_field="schema_repairs",
                )
            return self._budget_block(failure_kind)
        if failure_kind in {"placeholder_command", "validation_failure", "verification_failed"}:
            if not budget.exhausted("validation_repairs"):
                return RecoveryDecision(
                    action="repair_plan",
                    failure_kind=failure_kind,
                    reason="Plan validation failed; route through typed plan repair.",
                    confidence=0.86,
                    budget_field="validation_repairs",
                )
            return self._budget_block(failure_kind)
        if failure_kind in {"cwd_not_found", "dependency_failure", "command_not_found"}:
            if not budget.exhausted("probes"):
                return RecoveryDecision(
                    action="run_probe",
                    failure_kind=failure_kind,
                    reason="Failure is likely discoverable through read-only environment probing.",
                    confidence=0.82,
                    budget_field="probes",
                )
            return RecoveryDecision(
                action="repair_plan",
                failure_kind=failure_kind,
                reason="Probe budget exhausted; repair using available evidence.",
                confidence=0.62,
                budget_field="execution_repairs",
            )
        if failure_kind in {"command_failed", "python_failed", "deferred_python_failed"}:
            if not budget.exhausted("execution_repairs"):
                return RecoveryDecision(
                    action="repair_plan",
                    failure_kind=failure_kind,
                    reason="Execution failed; repair action-local plan from live stdout/stderr/traceback.",
                    confidence=0.84,
                    budget_field="execution_repairs",
                )
            return self._budget_block(failure_kind)
        if failure_kind in {"completion_failed", "formatting_failed", "authoritative_value_dropped"}:
            return RecoveryDecision(
                action="finalize",
                failure_kind=failure_kind,
                reason="Completion/formatting failures should use verifier-grounded finalization.",
                confidence=0.7,
            )
        if failure_kind == "budget_exhausted":
            return self._budget_block(failure_kind)
        if failure_kind == "approval_envelope_violation":
            return RecoveryDecision(
                action="ask_user",
                failure_kind=failure_kind,
                reason="Recovery would exceed the approved mutation envelope.",
                confidence=1.0,
                requires_confirmation=True,
            )
        return RecoveryDecision(
            action="repair_plan",
            failure_kind=failure_kind,
            reason="Unknown failure; attempt one conservative typed repair.",
            confidence=0.5,
            budget_field="execution_repairs",
        )

    @staticmethod
    def _budget_block(failure_kind: FailureKind) -> RecoveryDecision:
        return RecoveryDecision(
            action="block",
            failure_kind=failure_kind,
            reason="Recovery budget exhausted for this failure class.",
            confidence=0.95,
        )

    def record_decision(self, request_id: str, decision: RecoveryDecision, *, stage: str) -> None:
        if not self.enabled:
            return
        self.store.record_event(
            request_id=request_id,
            event_kind="recovery_decided",
            failure_kind=decision.failure_kind,
            recovery_action=decision.action,
            stage=stage,
            title="Recovery decision selected",
            summary=decision.reason,
            model_id=self.model_id,
            status="info",
            evidence=decision.model_dump(mode="json"),
        )

    def compile_plan(self, *, request_id: str, plan: Any, workspace_root: str = ".") -> list[dict[str, Any]]:
        """Run lightweight plan compiler checks and record diagnostics."""

        if self.mode == "off":
            return []
        diagnostics: list[dict[str, Any]] = []
        actions = list(getattr(plan, "actions", []) or [])
        if self.mode == "aggressive" and len(actions) > self.weak_model_plan_action_cap:
            diagnostics.append(
                {
                    "kind": "weak_model_plan_action_cap",
                    "message": "Plan exceeds weak-model action cap; recovery should prefer smaller transactions.",
                    "action_count": len(actions),
                    "cap": self.weak_model_plan_action_cap,
                }
            )
        root = Path(workspace_root or ".").resolve(strict=False)
        for action in actions:
            cwd = str(getattr(action, "cwd", ".") or ".").strip() or "."
            path = Path(cwd)
            resolved = path.resolve(strict=False) if path.is_absolute() else (root / path).resolve(strict=False)
            if not resolved.exists():
                diagnostics.append(
                    {
                        "kind": "cwd_not_found",
                        "action_id": str(getattr(action, "action_id", "")),
                        "cwd": cwd,
                        "message": "Action cwd does not exist at compile time.",
                    }
                )
            command = str(getattr(action, "command", "") or "")
            if re.search(r"(\{\{[^}]+\}\}|<[a-zA-Z0-9_. -]+>)", command):
                diagnostics.append(
                    {
                        "kind": "placeholder_command",
                        "action_id": str(getattr(action, "action_id", "")),
                        "message": "Action command appears to contain an unresolved placeholder.",
                    }
                )
        if self.enabled:
            self.store.record_event(
                request_id=request_id,
                event_kind="plan_compiled",
                stage="operator",
                title="Operator plan compiled",
                summary=(
                    "Plan compiler found diagnostics."
                    if diagnostics
                    else "Plan compiler accepted the operator plan."
                ),
                model_id=self.model_id,
                status="warning" if diagnostics else "info",
                evidence={"diagnostics": diagnostics, "action_count": len(actions)},
            )
        return diagnostics

    def audit_evidence(
        self,
        *,
        request_id: str,
        llm_client: Any,
        user_prompt: str,
        plan: Any,
        records: list[Any],
        context: dict[str, Any] | None = None,
        source_preview_chars: int = 3000,
    ) -> AnswerObligationSet:
        """Ask the LLM for semantic final-answer obligations from execution evidence."""

        successful_records = [
            record for record in records if str(getattr(record, "status", "")) == "success"
        ]
        if self.mode == "off" or not successful_records:
            return AnswerObligationSet(
                request_id=request_id,
                audit_status="complete",
                reason="No successful execution records required answer obligations.",
            )
        if llm_client is None or not callable(getattr(llm_client, "complete_json", None)):
            result = AnswerObligationSet(
                request_id=request_id,
                audit_status="unknown",
                reason="No LLM client was available for evidence auditing.",
            )
            self._record_evidence_audit(request_id, result, status="warning")
            return result
        prompt = self._build_evidence_auditor_prompt(
            request_id=request_id,
            user_prompt=user_prompt,
            plan=plan,
            records=successful_records,
            context=context,
            source_preview_chars=source_preview_chars,
        )
        try:
            payload = llm_client.complete_json(prompt, AnswerObligationSet.model_json_schema())
            result = AnswerObligationSet.model_validate(payload)
        except Exception as exc:
            self.record_failure(
                request_id=request_id,
                stage="reliability.evidence_auditor",
                payload=str(exc),
                title="Evidence auditor failed",
                summary="The LLM evidence auditor did not produce valid obligations.",
                failure_kind="schema_failure",
            )
            result = AnswerObligationSet(
                request_id=request_id,
                audit_status="failed",
                reason=f"Evidence auditor failed: {exc}",
            )
            self._record_evidence_audit(request_id, result, status="warning")
            return result
        if not result.request_id:
            result = result.model_copy(update={"request_id": request_id})
        self._record_evidence_audit(request_id, result)
        return result

    def review_answer_coverage(
        self,
        *,
        request_id: str,
        llm_client: Any,
        user_prompt: str,
        final_response: str,
        obligation_set: AnswerObligationSet | None,
        plan: Any = None,
        records: list[Any] | None = None,
        source_preview_chars: int = 3000,
    ) -> AnswerCoverageReview:
        """Ask the LLM whether a final answer semantically covers obligations."""

        obligations = list((obligation_set.facts if obligation_set is not None else []) or [])
        required = [
            obligation
            for obligation in obligations
            if obligation.must_report and obligation.sensitivity == "public"
        ]
        if not required:
            review = AnswerCoverageReview(
                verdict="accept",
                covered_obligations=[obligation.obligation_id for obligation in obligations],
                reason="No public required evidence obligations were missing.",
                confidence=1.0,
            )
            self._record_coverage_review(request_id, review)
            return review
        if llm_client is None or not callable(getattr(llm_client, "complete_json", None)):
            review = AnswerCoverageReview(
                verdict="fail",
                missing_obligations=[obligation.obligation_id for obligation in required],
                repair_instruction="No LLM client was available to review semantic answer coverage.",
                reason="Coverage verifier unavailable.",
                confidence=0.2,
            )
            self._record_coverage_review(request_id, review, status="warning")
            return review
        prompt = self._build_coverage_verifier_prompt(
            user_prompt=user_prompt,
            final_response=final_response,
            obligation_set=obligation_set,
            plan=plan,
            records=records or [],
            source_preview_chars=source_preview_chars,
        )
        try:
            payload = llm_client.complete_json(prompt, AnswerCoverageReview.model_json_schema())
            review = AnswerCoverageReview.model_validate(payload)
        except Exception as exc:
            self.record_failure(
                request_id=request_id,
                stage="reliability.coverage_verifier",
                payload=str(exc),
                title="Coverage verifier failed",
                summary="The LLM coverage verifier did not produce a valid review.",
                failure_kind="schema_failure",
            )
            review = AnswerCoverageReview(
                verdict="fail",
                missing_obligations=[obligation.obligation_id for obligation in required],
                repair_instruction="Retry final formatting with all required evidence obligations.",
                reason=f"Coverage verifier failed: {exc}",
                confidence=0.2,
            )
            self._record_coverage_review(request_id, review, status="warning")
            return review
        self._record_coverage_review(
            request_id,
            review,
            status="info" if review.verdict == "accept" else "warning",
        )
        return review

    def verify_outcome(
        self,
        *,
        request_id: str,
        status: str,
        records: list[Any],
        final_response: str,
        obligation_set: AnswerObligationSet | None = None,
        coverage_review: AnswerCoverageReview | None = None,
    ) -> OutcomeVerification:
        """Compute a conservative deterministic outcome verification result."""

        failed = [record for record in records if str(getattr(record, "status", "")) == "error"]
        successful = [record for record in records if str(getattr(record, "status", "")) == "success"]
        response = str(final_response or "").strip()
        obligations = list((obligation_set.facts if obligation_set is not None else []) or [])
        public_required_obligations = [
            obligation
            for obligation in obligations
            if obligation.must_report and obligation.sensitivity == "public"
        ]
        missing_obligation_ids: list[str] = []
        if failed and successful:
            result = OutcomeVerification(
                status="partially_satisfied",
                reason="Some actions succeeded, but at least one action failed.",
                evidence=[str(getattr(record, "action_id", "")) for record in failed],
                confidence=0.78,
                obligations=obligations,
                coverage_review=coverage_review,
                obligation_status="unknown" if public_required_obligations else "not_applicable",
            )
        elif failed:
            result = OutcomeVerification(
                status="unsatisfied",
                reason="Execution contains failed actions.",
                evidence=[str(getattr(record, "action_id", "")) for record in failed],
                confidence=0.86,
                obligations=obligations,
                coverage_review=coverage_review,
                obligation_status="unknown" if public_required_obligations else "not_applicable",
            )
        elif obligation_set is not None and obligation_set.audit_status != "complete":
            result = OutcomeVerification(
                status="unknown",
                reason=(
                    "Evidence audit failed, so the final answer could not be safely verified."
                    if obligation_set.audit_status == "failed"
                    else "Evidence audit was unavailable, so the final answer could not be safely verified."
                ),
                evidence=[str(getattr(record, "action_id", "")) for record in successful[:8]],
                confidence=0.2,
                obligations=obligations,
                coverage_review=coverage_review,
                obligation_status="unknown",
            )
        elif status == "success" and response and public_required_obligations:
            if coverage_review is None:
                missing_obligation_ids = [
                    obligation.obligation_id for obligation in public_required_obligations
                ]
                result = OutcomeVerification(
                    status="unknown",
                    reason="Evidence obligations exist, but no semantic coverage review was available.",
                    evidence=[str(getattr(record, "action_id", "")) for record in successful[:8]],
                    confidence=0.25,
                    obligations=obligations,
                    missing_obligations=missing_obligation_ids,
                    coverage_review=None,
                    obligation_status="unknown",
                )
            elif coverage_review.verdict == "accept":
                result = OutcomeVerification(
                    status="satisfied",
                    reason=coverage_review.reason or "Final answer covers required evidence obligations.",
                    evidence=[str(getattr(record, "action_id", "")) for record in successful[:8]],
                    confidence=max(0.0, min(1.0, coverage_review.confidence or 0.75)),
                    obligations=obligations,
                    coverage_review=coverage_review,
                    obligation_status="covered",
                )
            else:
                required_ids = {obligation.obligation_id for obligation in public_required_obligations}
                missing_obligation_ids = [
                    obligation_id
                    for obligation_id in [
                        *coverage_review.missing_obligations,
                        *coverage_review.contradicted_obligations,
                    ]
                    if obligation_id in required_ids
                ]
                result = OutcomeVerification(
                    status="unsatisfied",
                    reason=coverage_review.reason or "Final answer did not cover required evidence obligations.",
                    evidence=[str(getattr(record, "action_id", "")) for record in successful[:8]],
                    confidence=max(0.0, min(1.0, coverage_review.confidence or 0.72)),
                    obligations=obligations,
                    missing_obligations=missing_obligation_ids,
                    coverage_review=coverage_review,
                    obligation_status="missing" if missing_obligation_ids else "covered",
                )
        elif status == "success" and response:
            result = OutcomeVerification(
                status="satisfied",
                reason="Execution completed successfully and produced a final response.",
                evidence=[str(getattr(record, "action_id", "")) for record in successful[:8]],
                confidence=0.74,
                obligations=obligations,
                coverage_review=coverage_review,
                obligation_status="not_applicable",
            )
        elif status == "success":
            result = OutcomeVerification(
                status="unknown",
                reason="Execution succeeded but no final response was available to verify.",
                confidence=0.4,
                obligations=obligations,
                coverage_review=coverage_review,
                obligation_status="unknown" if public_required_obligations else "not_applicable",
            )
        else:
            result = OutcomeVerification(
                status="unknown",
                reason="Runtime status did not provide enough evidence for deterministic verification.",
                confidence=0.35,
                obligations=obligations,
                coverage_review=coverage_review,
                obligation_status="unknown" if public_required_obligations else "not_applicable",
            )
        if self.enabled:
            prior_events = self.store.list_events(request_id=request_id, limit=100)
            had_failure = any(event.event_kind == "failure_detected" for event in prior_events)
            verification_failure_kind = (
                "authoritative_value_dropped"
                if result.status == "unsatisfied" and result.obligation_status == "missing"
                else ("verification_failed" if result.status == "unsatisfied" else None)
            )
            if verification_failure_kind == "authoritative_value_dropped":
                self.store.record_event(
                    request_id=request_id,
                    event_kind="failure_detected",
                    failure_kind="authoritative_value_dropped",
                    stage="verification",
                    title="Authoritative evidence missing from final answer",
                    summary=result.reason,
                    model_id=self.model_id,
                    status="warning",
                    evidence={
                        "missing_obligations": result.missing_obligations,
                        "coverage_review": (
                            result.coverage_review.model_dump(mode="json")
                            if result.coverage_review is not None
                            else None
                        ),
                    },
                )
            self.store.record_event(
                request_id=request_id,
                event_kind="verification_completed",
                failure_kind=verification_failure_kind,
                stage="verification",
                title="Outcome verified",
                summary=result.reason,
                model_id=self.model_id,
                status="info" if result.status in {"satisfied", "unknown"} else "warning",
                evidence=result.model_dump(mode="json"),
            )
            self.store.record_event(
                request_id=request_id,
                event_kind="outcome_recorded",
                stage="completed",
                title="Reliability outcome recorded",
                summary=f"Verification status: {result.status}.",
                model_id=self.model_id,
                status="info",
                evidence={
                    "verification": result.model_dump(mode="json"),
                    "obligation_counts": _obligation_counts(result),
                    "recovered": bool(successful and had_failure and result.status == "satisfied"),
                },
            )
        return result

    def _record_evidence_audit(
        self,
        request_id: str,
        result: AnswerObligationSet,
        *,
        status: str = "info",
    ) -> None:
        if not self.enabled:
            return
        self.store.record_event(
            request_id=request_id,
            event_kind="evidence_audited",
            stage="verification",
            title="Evidence obligations audited",
            summary=result.reason or f"{len(result.facts)} answer obligations identified.",
            model_id=self.model_id,
            status=status,
            evidence=result.model_dump(mode="json"),
        )

    def _record_coverage_review(
        self,
        request_id: str,
        review: AnswerCoverageReview,
        *,
        status: str = "info",
    ) -> None:
        if not self.enabled:
            return
        self.store.record_event(
            request_id=request_id,
            event_kind="coverage_reviewed",
            recovery_action="finalize" if review.verdict != "accept" else None,
            stage="verification",
            title="Final answer coverage reviewed",
            summary=review.reason or f"Coverage verdict: {review.verdict}.",
            model_id=self.model_id,
            status=status,
            evidence=review.model_dump(mode="json"),
        )

    def record_formatter_retry(
        self,
        *,
        request_id: str,
        review: AnswerCoverageReview,
        attempt: int,
    ) -> None:
        if not self.enabled:
            return
        self.store.record_event(
            request_id=request_id,
            event_kind="formatter_retried",
            recovery_action="finalize",
            stage="rendering",
            title="Final formatter retry requested",
            summary=review.repair_instruction or review.reason,
            model_id=self.model_id,
            status="warning",
            evidence={
                "attempt": attempt,
                "coverage_review": review.model_dump(mode="json"),
            },
        )

    def _build_evidence_auditor_prompt(
        self,
        *,
        request_id: str,
        user_prompt: str,
        plan: Any,
        records: list[Any],
        context: dict[str, Any] | None,
        source_preview_chars: int,
    ) -> str:
        return "\n".join(
            [
                *prompt_lines("reliability.evidence_auditor"),
                "AnswerObligationSet schema:",
                _stable_json(AnswerObligationSet.model_json_schema()),
                "Request id:",
                request_id,
                "User request:",
                str(user_prompt or ""),
                "Plan summary and expected outputs:",
                _stable_json(_plan_evidence_packet(plan)),
                "Verification/context hints:",
                _stable_json(_context_evidence_packet(context)),
                "Successful execution evidence packets:",
                _stable_json(
                    [
                        _record_evidence_packet(record, max_chars=source_preview_chars)
                        for record in records
                    ]
                ),
            ]
        )

    def _build_coverage_verifier_prompt(
        self,
        *,
        user_prompt: str,
        final_response: str,
        obligation_set: AnswerObligationSet | None,
        plan: Any,
        records: list[Any],
        source_preview_chars: int,
    ) -> str:
        return "\n".join(
            [
                *prompt_lines("reliability.coverage_verifier"),
                "AnswerCoverageReview schema:",
                _stable_json(AnswerCoverageReview.model_json_schema()),
                "Original user request:",
                str(user_prompt or ""),
                "Plan summary and expected outputs:",
                _stable_json(_plan_evidence_packet(plan)),
                "Evidence obligations:",
                _stable_json(
                    (obligation_set or AnswerObligationSet(audit_status="unknown")).model_dump(
                        mode="json"
                    )
                ),
                "Execution evidence packets:",
                _stable_json(
                    [
                        _record_evidence_packet(record, max_chars=source_preview_chars)
                        for record in records
                        if str(getattr(record, "status", "")) == "success"
                    ]
                ),
                "Final answer under review:",
                str(final_response or ""),
            ]
        )

    def create_approval_envelope(
        self,
        *,
        request_id: str,
        goal: str,
        plan: Any,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Create and persist a bounded approval envelope for a confirmation."""

        if not self.enabled:
            return None
        raw_context = dict(context or {})
        actions = list(getattr(plan, "actions", []) or [])
        risks = [str(getattr(action, "risk", "medium") or "medium").lower() for action in actions]
        max_risk = max(risks or ["medium"], key=lambda item: _RISK_ORDER.get(item, 1))
        cwd_values = sorted({str(getattr(action, "cwd", ".") or ".") for action in actions})
        envelope = self.store.create_approval_envelope(
            request_id=request_id,
            goal=goal,
            gateway_node=str(raw_context.get("gateway_node") or raw_context.get("node") or ""),
            gateway_url=str(raw_context.get("gateway_url") or ""),
            cwd_values=cwd_values,
            action_ids=[str(getattr(action, "action_id", "")) for action in actions],
            max_risk=max_risk,
            mutation_budget=self.approval_envelope_budget,
            model_id=self.model_id,
        )
        return envelope.model_dump(mode="json")

    def check_and_consume_approval_envelope(
        self,
        *,
        request_id: str,
        plan: Any,
        mutating_action_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Validate a repaired mutating plan against the latest approval envelope."""

        if not self.enabled:
            return {"allowed": False, "reason": "Reliability Kernel is disabled."}
        envelope = self.store.get_latest_envelope(request_id)
        if envelope is None:
            return {"allowed": False, "reason": "No approval envelope is active for this request."}
        if envelope.status not in {"active", "exhausted"}:
            return {
                "allowed": False,
                "reason": f"Approval envelope is {envelope.status}.",
                "envelope": envelope.model_dump(mode="json"),
            }
        actions = list(getattr(plan, "actions", []) or [])
        mutating_ids = [str(item) for item in (mutating_action_ids or []) if str(item).strip()]
        mutation_count = len(mutating_ids)
        cwd_values = {str(getattr(action, "cwd", ".") or ".") for action in actions}
        unknown_cwd = sorted(cwd for cwd in cwd_values if cwd not in set(envelope.cwd_values or ["."]))
        risks = [str(getattr(action, "risk", "medium") or "medium").lower() for action in actions]
        max_risk = max(risks or ["medium"], key=lambda item: _RISK_ORDER.get(item, 1))
        violations: list[str] = []
        if unknown_cwd:
            violations.append(f"new cwd outside envelope: {', '.join(unknown_cwd)}")
        if _RISK_ORDER.get(max_risk, 1) > _RISK_ORDER.get(envelope.max_risk, 1):
            violations.append(f"risk escalated from {envelope.max_risk} to {max_risk}")
        remaining = max(0, int(envelope.mutation_budget) - int(envelope.used_mutations))
        if mutation_count > remaining:
            violations.append(
                f"mutation budget exhausted: requested {mutation_count}, remaining {remaining}"
            )
        if violations:
            updated = self.store.consume_approval_envelope_budget(
                envelope_id=envelope.envelope_id,
                mutation_count=0,
                status="violated",
            )
            evidence = {
                "violations": violations,
                "mutation_count": mutation_count,
                "mutating_action_ids": mutating_ids,
                "envelope": (updated or envelope).model_dump(mode="json"),
            }
            self.store.record_event(
                request_id=request_id,
                event_kind="approval_envelope_violation",
                failure_kind="approval_envelope_violation",
                recovery_action="ask_user",
                stage="approval_envelope",
                title="Approval envelope rejected recovery",
                summary="; ".join(violations),
                model_id=self.model_id,
                status="warning",
                evidence=evidence,
            )
            return {"allowed": False, "reason": "; ".join(violations), **evidence}
        updated = self.store.consume_approval_envelope_budget(
            envelope_id=envelope.envelope_id,
            mutation_count=mutation_count,
        )
        evidence = {
            "mutation_count": mutation_count,
            "mutating_action_ids": mutating_ids,
            "envelope": (updated or envelope).model_dump(mode="json"),
        }
        self.store.record_event(
            request_id=request_id,
            event_kind="recovery_accepted",
            recovery_action="repair_plan",
            stage="approval_envelope",
            title="Approval envelope accepted recovery",
            summary="Repaired mutating actions stayed within the approved envelope.",
            model_id=self.model_id,
            status="info",
            evidence=evidence,
        )
        return {"allowed": True, "reason": "Recovery stayed inside approval envelope.", **evidence}


def _safe_preview(value: Any, limit: int = 4000) -> Any:
    text = str(value)
    if len(text) <= limit:
        return value
    return text[:limit] + "...[truncated]"


def _summary_from_payload(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:240] if text else "Reliability failure detected."


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, indent=2, default=str)
    except Exception:
        return str(value)


def _truncate(value: Any, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit)] + "\n...[truncated]"


def _record_id(record: Any) -> str:
    for key in ("action_id", "record_id", "id"):
        value = str(getattr(record, key, "") or "").strip()
        if value:
            return value
    metadata = getattr(record, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("action_id", "record_id", "id"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
    return "unknown_record"


def _record_evidence_packet(record: Any, *, max_chars: int = 3000) -> dict[str, Any]:
    """Return a bounded, redacted, tool-agnostic execution record preview."""

    output = getattr(record, "output", None)
    metadata = getattr(record, "metadata", None)
    stdout = str(getattr(record, "stdout", "") or "")
    stderr = str(getattr(record, "stderr", "") or "")
    output_preview = _stable_json(output) if output is not None else ""
    return {
        "record_id": _record_id(record),
        "action_id": str(getattr(record, "action_id", "") or ""),
        "task_id": str(getattr(record, "task_id", "") or ""),
        "kind": str(getattr(record, "kind", "") or ""),
        "status": str(getattr(record, "status", "") or ""),
        "exit_code": getattr(record, "exit_code", None),
        "error": _truncate(redact_value(getattr(record, "error", "") or ""), max_chars),
        "stdout_preview": _truncate(redact_value(stdout), max_chars),
        "stderr_preview": _truncate(redact_value(stderr), max_chars),
        "output_preview": _truncate(redact_value(output_preview), max_chars),
        "stdout_chars": len(stdout),
        "stderr_chars": len(stderr),
        "output_type": type(output).__name__ if output is not None else None,
        "metadata": redact_value(metadata if isinstance(metadata, dict) else {}),
    }


def _plan_evidence_packet(plan: Any) -> dict[str, Any]:
    """Return a compact, generic plan packet for evidence auditing."""

    if plan is None:
        return {}
    tasks = []
    for task in list(getattr(plan, "tasks", []) or [])[:20]:
        tasks.append(
            {
                "task_id": str(getattr(task, "task_id", "") or ""),
                "goal": str(getattr(task, "goal", "") or ""),
                "semantic_verb": str(getattr(task, "semantic_verb", "") or ""),
                "object_type": str(getattr(task, "object_type", "") or ""),
                "reason": str(getattr(task, "reason", "") or ""),
            }
        )
    actions = []
    for action in list(getattr(plan, "actions", []) or [])[:20]:
        actions.append(
            {
                "action_id": str(getattr(action, "action_id", "") or ""),
                "task_id": str(getattr(action, "task_id", "") or ""),
                "kind": str(getattr(action, "kind", "") or ""),
                "declared_output_shape": str(getattr(action, "declared_output_shape", "") or ""),
                "reason": str(getattr(action, "reason", "") or ""),
                "risk": str(getattr(action, "risk", "") or ""),
            }
        )
    return {
        "summary": str(getattr(plan, "summary", "") or ""),
        "expected_outputs": list(getattr(plan, "expected_outputs", []) or [])[:20],
        "assumptions": list(getattr(plan, "assumptions", []) or [])[:20],
        "tasks": tasks,
        "actions": actions,
        "confidence": getattr(plan, "confidence", None),
    }


def _context_evidence_packet(context: dict[str, Any] | None) -> dict[str, Any]:
    """Return non-sensitive context hints relevant to semantic answer obligations."""

    raw = dict(context or {})
    self_brief = raw.get("operator_self_brief")
    packet: dict[str, Any] = {}
    if isinstance(self_brief, dict):
        packet["operator_self_brief"] = redact_value(
            {
                "task_understanding": self_brief.get("task_understanding"),
                "expected_outputs": self_brief.get("expected_outputs"),
                "verification_contract": self_brief.get("verification_contract"),
                "completion_criteria": self_brief.get("completion_criteria"),
            }
        )
    clarifications = raw.get("clarifications")
    if isinstance(clarifications, list) and clarifications:
        packet["clarifications"] = redact_value(clarifications[-5:])
    return packet


def _public_required_obligations(
    obligation_set: AnswerObligationSet | None,
) -> list[EvidenceObligation]:
    return [
        obligation
        for obligation in list((obligation_set.facts if obligation_set is not None else []) or [])
        if obligation.must_report and obligation.sensitivity == "public"
    ]


def _obligation_counts(result: OutcomeVerification) -> dict[str, int]:
    obligations = list(result.obligations or [])
    return {
        "total": len(obligations),
        "must_report": sum(1 for obligation in obligations if obligation.must_report),
        "public_must_report": sum(
            1
            for obligation in obligations
            if obligation.must_report and obligation.sensitivity == "public"
        ),
        "missing": len(result.missing_obligations or []),
        "covered": len(result.coverage_review.covered_obligations)
        if result.coverage_review is not None
        else 0,
        "contradicted": len(result.coverage_review.contradicted_obligations)
        if result.coverage_review is not None
        else 0,
        "unsupported_claims": len(result.coverage_review.unsupported_claims)
        if result.coverage_review is not None
        else 0,
    }
