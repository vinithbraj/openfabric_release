"""Memory compliance review helpers for the operator pipeline."""

from __future__ import annotations

from agent_runtime.operator.memory_compliance_support.common import *
from agent_runtime.operator.memory_compliance_support.common import _MemoryComplianceCommonMixin


class _MemoryComplianceReviewMixin(_MemoryComplianceCommonMixin):
    def _review_memory_compliance(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
        *,
        source: str = "fresh_plan",
    ) -> tuple[OperatorPlan, list[dict[str, Any]]]:
        """Review and, when possible, repair a plan against retrieved memory."""

        self._attach_memory_for_stage(
            user_request,
            observability,
            stage="plan_review",
            source=source,
        )
        directives = [
            directive
            for directive in _memory_directives_for_stage(user_request, "plan_review")
            if str(directive.get("strength") or "") in {"must_consider", "required_unless_conflict"}
        ]
        if not directives:
            return plan, []
        directives, ignored_directives, memory_intent_scope = _memory_directives_for_current_intent(
            user_request,
            plan,
            directives,
        )
        if not directives:
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_MEMORY_COMPLIANCE_REVIEWED,
                title="Memory compliance ignored by intent",
                summary="Retrieved memory directives were read-only/status scoped and did not apply to the current requested mutation.",
                details={
                    "source": source,
                    "decision": "ignore_memory",
                    "ignored_directives": ignored_directives,
                    "memory_intent_scope": memory_intent_scope,
                },
            )
            return plan, []
        deterministic_errors = _memory_constraint_errors(user_request, plan, directives)
        if not _plan_has_mutating_action(plan) and not any(
            str(directive.get("strength") or "") == "required_unless_conflict"
            for directive in directives
        ):
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_MEMORY_COMPLIANCE_REVIEWED,
                title="Memory compliance skipped for read-only plan",
                summary=(
                    "Scoped memory contained only soft directives and the validated plan "
                    "does not mutate state."
                ),
                details={
                    "source": source,
                    "decision": "skip_soft_memory",
                    "memory_ids": [
                        str(directive.get("memory_id") or "") for directive in directives
                    ],
                    "memory_intent_scope": memory_intent_scope,
                },
            )
            return plan, []
        review: MemoryComplianceReview | None = None
        try:
            review = structured_call(
                self.llm_client,
                build_memory_compliance_review_prompt(
                    user_request,
                    plan,
                    directives,
                    deterministic_errors,
                    source=source,
                ),
                MemoryComplianceReview,
            )
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                title="Memory compliance review failed",
                summary="The memory compliance review did not produce a valid typed response.",
                details={
                    "source": source,
                    "error": str(exc),
                    "deterministic_errors": deterministic_errors,
                },
            )
        if review is not None:
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_MEMORY_COMPLIANCE_REVIEWED,
                title="Memory compliance reviewed",
                summary="The LLM checked the operator plan against retrieved memory directives.",
                details={
                    "source": source,
                    "decision": review.decision,
                    "violated_memory_ids": list(review.violated_memory_ids),
                    "repair_guidance": review.repair_guidance,
                    "reason": review.reason,
                    "confidence": review.confidence,
                    "deterministic_errors": deterministic_errors,
                    "ignored_directives": ignored_directives,
                    "memory_intent_scope": memory_intent_scope,
                },
            )
            if review.decision in {"accept", "ignore_memory"} and not deterministic_errors:
                return plan, []

        candidate: OperatorPlan | None = None
        memory_feedback: list[dict[str, Any]] = list(deterministic_errors)
        if (
            review is not None
            and review.decision == "repair_required"
            and review.revised_plan is not None
            and review.confidence >= self._repair_confidence_threshold()
        ):
            candidate = review.revised_plan
            memory_feedback.append(
                {
                    "error": "memory_compliance_repair_required",
                    "message": "Memory compliance review required a revised plan.",
                    "memory_ids": list(review.violated_memory_ids),
                    "repair_hint": review.repair_guidance,
                    "reason": review.reason,
                    "confidence": review.confidence,
                }
            )
        elif review is not None and review.decision == "repair_required":
            low_confidence_feedback = {
                "error": "memory_compliance_low_confidence",
                "message": "Memory compliance review requested repair without enough confidence.",
                "memory_ids": list(review.violated_memory_ids),
                "repair_hint": review.repair_guidance,
                "confidence": review.confidence,
            }
            if deterministic_errors:
                memory_feedback.append(low_confidence_feedback)
            else:
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                    title="Low-confidence memory repair ignored",
                    summary="Memory requested repair without deterministic errors or enough confidence; the validated plan was kept.",
                    details={
                        "source": source,
                        "memory_feedback": [low_confidence_feedback],
                        "ignored_directives": ignored_directives,
                        "memory_intent_scope": memory_intent_scope,
                    },
                )
                return plan, []

        if candidate is None and memory_feedback:
            try:
                candidate = self._complete_repair(user_request, memory_feedback, plan)
            except Exception as exc:
                memory_feedback.append(
                    {
                        "error": "memory_compliance_repair_failed",
                        "message": str(exc),
                    }
                )
                return plan, memory_feedback
        if candidate is None:
            return plan, []

        candidate = self._normalize_plan_interactions(
            self._defer_dependent_python_actions(candidate, observability),
            observability,
        )
        preservation_errors = _memory_repair_preservation_errors(user_request, plan, candidate)
        if preservation_errors:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                title="Memory repair ignored by intent",
                summary="The memory repair erased the current user-requested action, so the validated plan was kept.",
                details={
                    "source": source,
                    "errors": preservation_errors,
                    "memory_feedback": memory_feedback,
                    "ignored_directives": ignored_directives,
                    "memory_intent_scope": memory_intent_scope,
                },
            )
            return plan, []
        validation_errors = self._validate_plan(user_request, candidate, observability)
        if validation_errors:
            revision_error = {
                "error": "memory_compliance_revision_validation_failed",
                "message": "The memory-compliance revision failed deterministic validation.",
                "validation_errors": validation_errors,
                "repair_hint": (
                    "Repair the memory-compliance revision through the normal operator "
                    "repair path while preserving the memory-required behavior."
                ),
            }
            repair_feedback = [*memory_feedback, revision_error]
            candidate_repaired = False
            if self._max_validation_repair_attempts() > 0:
                try:
                    repaired_candidate = self._complete_repair(
                        user_request,
                        repair_feedback,
                        candidate,
                    )
                    repaired_candidate = self._normalize_plan_interactions(
                        self._defer_dependent_python_actions(repaired_candidate, observability),
                        observability,
                    )
                    repair_preservation_errors = _memory_repair_preservation_errors(
                        user_request,
                        plan,
                        repaired_candidate,
                    )
                    if repair_preservation_errors:
                        self._emit(
                            observability,
                            level="warning",
                            event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                            title="Memory repair ignored by intent",
                            summary="The repaired memory plan erased the current user-requested action, so the validated plan was kept.",
                            details={
                                "source": source,
                                "errors": repair_preservation_errors,
                                "memory_feedback": repair_feedback,
                                "ignored_directives": ignored_directives,
                                "memory_intent_scope": memory_intent_scope,
                            },
                        )
                        return plan, []
                    repair_validation_errors = self._validate_plan(
                        user_request,
                        repaired_candidate,
                        observability,
                    )
                except Exception as exc:
                    return plan, [
                        *repair_feedback,
                        {
                            "error": "memory_compliance_revision_repair_failed",
                            "message": str(exc),
                        },
                    ]
                if not repair_validation_errors:
                    candidate = repaired_candidate
                    memory_feedback = repair_feedback
                    candidate_repaired = True
                else:
                    revision_error["repair_validation_errors"] = repair_validation_errors
            if not candidate_repaired:
                return plan, [
                    revision_error,
                ]
        remaining_errors = _memory_constraint_errors(user_request, candidate, directives)
        if remaining_errors:
            repair_feedback = [
                *memory_feedback,
                {
                    "error": "memory_compliance_remaining_constraint_violation",
                    "message": "The memory-compliance revision still violates retrieved memory.",
                    "validation_errors": remaining_errors,
                    "repair_hint": "Repair the plan so it satisfies the retrieved memory guard rules.",
                }
            ]
            if self._max_validation_repair_attempts() > 0:
                try:
                    repaired_candidate = self._complete_repair(
                        user_request,
                        repair_feedback,
                        candidate,
                    )
                    repaired_candidate = self._normalize_plan_interactions(
                        self._defer_dependent_python_actions(repaired_candidate, observability),
                        observability,
                    )
                    repair_preservation_errors = _memory_repair_preservation_errors(
                        user_request,
                        plan,
                        repaired_candidate,
                    )
                    if repair_preservation_errors:
                        self._emit(
                            observability,
                            level="warning",
                            event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                            title="Memory repair ignored by intent",
                            summary="The repaired memory plan erased the current user-requested action, so the validated plan was kept.",
                            details={
                                "source": source,
                                "errors": repair_preservation_errors,
                                "memory_feedback": repair_feedback,
                                "ignored_directives": ignored_directives,
                                "memory_intent_scope": memory_intent_scope,
                            },
                        )
                        return plan, []
                    repair_validation_errors = self._validate_plan(
                        user_request,
                        repaired_candidate,
                        observability,
                    )
                    repair_remaining_errors = _memory_constraint_errors(
                        user_request,
                        repaired_candidate,
                        directives,
                    )
                except Exception as exc:
                    return plan, [
                        *repair_feedback,
                        {
                            "error": "memory_compliance_repair_failed",
                            "message": str(exc),
                        },
                    ]
                if not repair_validation_errors and not repair_remaining_errors:
                    candidate = repaired_candidate
                    memory_feedback = repair_feedback
                else:
                    repair_feedback.append(
                        {
                            "error": "memory_compliance_repair_rejected",
                            "message": "The repaired memory-compliance revision still failed checks.",
                            "validation_errors": repair_validation_errors,
                            "memory_errors": repair_remaining_errors,
                        }
                    )
                    return plan, repair_feedback
            else:
                return plan, repair_feedback
        remaining_errors = _memory_constraint_errors(user_request, candidate, directives)
        if remaining_errors:
            return plan, remaining_errors
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_MEMORY_COMPLIANCE_REPAIRED,
            title="Memory compliance repaired plan",
            summary="The operator plan was revised to satisfy retrieved memory directives.",
            details={
                "source": source,
                "action_count": len(candidate.actions),
                "task_count": len(candidate.tasks),
                "memory_feedback": memory_feedback,
            },
        )
        return candidate, []



__all__ = ["_MemoryComplianceReviewMixin"]
