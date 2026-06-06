"""Operator follow-up decision repair flow."""

from __future__ import annotations

from .common import *


class _RepairFollowupMixin:
    """Operator follow-up decision repair flow."""

    def complete_followup_decision(
        self,
        user_request: UserRequest,
        conversation_context: dict[str, Any],
        observability: ObservabilityContext | None = None,
    ) -> OperatorFollowupDecision:
        """Ask the LLM whether a follow-up can be answered from context or needs actions."""

        feedback: list[dict[str, Any]] | None = None
        last_errors: list[dict[str, Any]] = []
        rejected_plan: OperatorPlan | None = None
        total_attempts = self._attempt_count(
            self._max_validation_repair_attempts()
        )
        brief = self._ensure_self_brief(user_request, observability, conversation_context)
        self._ensure_deliberation_frame(user_request, brief, observability, phase="followup_pre_plan")
        cached = self._try_plan_cache_adaptation(user_request, observability)
        if cached is not None:
            cached_plan, candidate = cached
            plan = self._normalize_plan_interactions(
                self._defer_dependent_python_actions(cached_plan, observability),
                observability,
            )
            errors = self._validate_plan(user_request, plan, observability)
            if not errors:
                plan, deliberation_errors = self._review_guided_plan_critique(
                    user_request,
                    plan,
                    observability,
                    phase="followup_cache_plan",
                )
                errors = deliberation_errors
            if not errors:
                plan, memory_errors = self._review_memory_compliance(
                    user_request,
                    plan,
                    observability,
                    source="followup_cache",
                )
                errors = memory_errors
            if not errors:
                if self.plan_cache_store is not None:
                    self.plan_cache_store.mark_used(candidate.entry.cache_id)
                user_request.session_context["operator_plan_cache_applied"] = {
                    "cache_id": candidate.entry.cache_id,
                    "score": candidate.score,
                }
                self._emit(
                    observability,
                    level="info",
                    event_type=OPERATOR_CONVERSATION_PLAN_PROPOSED,
                    title="Follow-up cached operator plan accepted",
                    summary="The follow-up reused an adapted private cached plan skeleton.",
                    details={
                        "cache_id": candidate.entry.cache_id,
                        "action_count": len(plan.actions),
                        "task_count": len(plan.tasks),
                    },
                )
                return OperatorFollowupDecision(
                    mode="plan_actions",
                    plan=plan,
                    reason="Adapted from a private operator plan cache hit.",
                    confidence=max(0.01, candidate.score),
                )
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_VALIDATION_REJECTED,
                title="Follow-up cached plan rejected",
                summary="The adapted cached follow-up plan failed validation; normal follow-up planning will continue.",
                details={"cache_id": candidate.entry.cache_id, "errors": errors},
            )
        for attempt in range(total_attempts):
            prompt = build_operator_followup_prompt(
                user_request,
                conversation_context,
                feedback,
                rejected_plan,
            )
            try:
                decision = structured_call(self.llm_client, prompt, OperatorFollowupDecision)
            except (PydanticValidationError, StructuredCallError) as exc:
                last_errors = [{"error": "schema_validation_error", "message": str(exc)}]
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_VALIDATION_REJECTED,
                    title="Operator follow-up schema rejected",
                    summary="The LLM follow-up payload did not match the typed schema.",
                    details={"attempt": attempt + 1, "errors": last_errors},
                )
                feedback = last_errors
                rejected_plan = None
                continue

            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_CONVERSATION_FOLLOWUP_DECISION,
                title="Operator follow-up decision received",
                summary="The LLM decided how to handle the conversational follow-up.",
                details=decision.model_dump(mode="json"),
            )

            if decision.mode == "answer_from_context":
                if str(decision.answer or "").strip():
                    self._emit(
                        observability,
                        level="info",
                        event_type=OPERATOR_CONVERSATION_ANSWER_FROM_CONTEXT,
                        title="Follow-up answered from context",
                        summary="The LLM answered the follow-up without proposing new actions.",
                        details={
                            "confidence": decision.confidence,
                            "reason": decision.reason,
                        },
                    )
                    return decision
                last_errors = [
                    {
                        "error": "missing_context_answer",
                        "message": "answer_from_context requires a non-empty answer.",
                    }
                ]
                feedback = last_errors
                rejected_plan = None
                continue

            if decision.plan is None:
                last_errors = [
                    {
                        "error": "missing_operator_plan",
                        "message": "plan_actions requires a complete operator plan.",
                    }
                ]
                feedback = last_errors
                rejected_plan = None
                continue

            decision = decision.model_copy(
                update={
                    "plan": self._normalize_plan_interactions(
                        self._defer_dependent_python_actions(
                            decision.plan,
                            observability,
                        ),
                        observability,
                    )
                }
            )
            errors = self._validate_plan(user_request, decision.plan, observability)
            if errors and self._single_report_compiler_needed(user_request, errors):
                compiled_plan, compiler_errors = self._compile_single_report_plan(
                    user_request,
                    errors,
                    decision.plan,
                    observability,
                    source="followup",
                    conversation_context=conversation_context,
                )
                if compiled_plan is not None:
                    self._emit(
                        observability,
                        level="info",
                        event_type=OPERATOR_CONVERSATION_PLAN_PROPOSED,
                        title="Follow-up retired compiler plan accepted",
                        summary="A compatibility compiler unexpectedly returned a plan.",
                        details={
                            "action_count": len(compiled_plan.actions),
                            "task_count": len(compiled_plan.tasks),
                            "confidence": decision.confidence,
                        },
                    )
                    return decision.model_copy(update={"plan": compiled_plan})
                last_errors = compiler_errors
                rejected_plan = decision.plan
                feedback = compiler_errors
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_VALIDATION_REJECTED,
                    title="Follow-up retired compiler rejected",
                    summary="The retired compiler path is disabled; normal validation feedback will continue.",
                    details={"attempt": attempt + 1, "errors": compiler_errors},
                )
                break
            if not errors:
                reviewed_plan, deliberation_errors = self._review_guided_plan_critique(
                    user_request,
                    decision.plan,
                    observability,
                    phase="followup_plan",
                    feedback=feedback,
                )
                if deliberation_errors:
                    errors = deliberation_errors
                else:
                    decision.plan = reviewed_plan
            if not errors:
                reviewed_plan, memory_errors = self._review_memory_compliance(
                    user_request,
                    decision.plan,
                    observability,
                    source="followup_plan",
                )
                if memory_errors:
                    errors = memory_errors
                else:
                    decision.plan = reviewed_plan
            if not errors:
                self._emit(
                    observability,
                    level="info",
                    event_type=OPERATOR_CONVERSATION_PLAN_PROPOSED,
                    title="Follow-up operator plan accepted",
                    summary="The follow-up plan passed deterministic validation.",
                    details={
                        "action_count": len(decision.plan.actions),
                        "task_count": len(decision.plan.tasks),
                        "confidence": decision.confidence,
                    },
                )
                return decision
            last_errors = errors
            rejected_plan = decision.plan
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_VALIDATION_REJECTED,
                title="Follow-up operator plan rejected",
                summary="The follow-up plan failed deterministic validation.",
                details={"attempt": attempt + 1, "errors": errors},
            )
            feedback = errors

        retry_request = self._auto_rephrase_retry_request(
            user_request,
            last_errors,
            observability,
            source="followup",
        )
        if retry_request is not None:
            try:
                decision = self.complete_followup_decision(
                    retry_request,
                    conversation_context,
                    observability,
                )
            except OperatorValidationError as exc:
                self._mark_auto_rephrase_retry_trace(
                    retry_request,
                    status="failed",
                    validation_errors=list(exc.errors),
                )
                self._emit(
                    observability,
                    level="error",
                    event_type=OPERATOR_AUTO_REPHRASE_RETRY,
                    title="Operator auto rephrase retry failed",
                    summary="The rephrased follow-up still failed planning validation.",
                    details={"source": "followup", "errors": list(exc.errors)},
                )
                raise
            self._mark_auto_rephrase_retry_trace(retry_request, status="succeeded")
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_AUTO_REPHRASE_RETRY,
                title="Operator auto rephrase retry succeeded",
                summary="The rephrased follow-up produced a valid operator decision.",
                details={"source": "followup", "mode": decision.mode},
            )
            return decision
        self._emit(
            observability,
            level="error",
            event_type=OPERATOR_REPAIR_REJECTED,
            title="Operator follow-up repair rejected",
            summary="The follow-up decision still failed after configured repair attempts.",
            details={"errors": last_errors},
        )
        raise OperatorValidationError(last_errors)
