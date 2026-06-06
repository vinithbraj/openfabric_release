"""Initial operator proposal and validation flow."""

from __future__ import annotations

from .common import *


class _RepairProposeMixin:
    """Initial operator proposal and validation flow."""

    def propose_and_validate(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None = None,
    ) -> tuple[OperatorPlan, list[dict[str, Any]]]:
        """Ask the LLM for one plan, then one repair if schema/validation fails."""

        feedback: list[dict[str, Any]] | None = None
        last_errors: list[dict[str, Any]] = []
        rejected_plan: OperatorPlan | None = None
        total_attempts = self._attempt_count(
            self._max_validation_repair_attempts()
        )
        lrdirect_cached = self._try_lrdirect_step_cache(user_request, observability)
        if lrdirect_cached is not None:
            cached_plan, candidate, cache_type = lrdirect_cached
            plan = self._normalize_plan_interactions(cached_plan, observability)
            self._normalize_streaming_prior_python_inputs(user_request, plan, observability)
            cache_id = (
                candidate.entry.template_id
                if cache_type in {"command_template", "payload_command_template"}
                else candidate.entry.cache_id
            )
            lr_mode = LR_MODE_PAYLOAD if cache_type == "payload_command_template" else LR_MODE_DEFAULT
            action_kind = plan.actions[0].kind if plan.actions else ""
            exact_key, _ = self._lrdirect_step_metadata(user_request)
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_PLAN_PROPOSED,
                title="LR Direct plan proposed",
                summary="The runtime rebuilt a one-action plan from an exact successful streaming-step cache entry.",
                details={
                    **plan.model_dump(mode="json"),
                    "cache_type": cache_type,
                    "cache_id": cache_id,
                    "lr_mode": lr_mode,
                    "exact_step_key": exact_key,
                    "action_kind": action_kind,
                    "bypassed_llm": True,
                },
            )
            errors = self._validate_plan(user_request, plan, observability)
            if not errors:
                bypasses_approval = not self.plan_requires_confirmation(plan)
                if (
                    cache_type in {"command_template", "payload_command_template"}
                    and self.command_template_cache_store is not None
                ):
                    self.command_template_cache_store.mark_used(cache_id)
                elif cache_type == "computation" and self.computation_cache_store is not None:
                    self.computation_cache_store.mark_used(cache_id)
                user_request.session_context["operator_lrdirect_applied"] = {
                    "cache_type": cache_type,
                    "cache_id": cache_id,
                    "lr_mode": lr_mode,
                    "exact_step_key": exact_key,
                    "action_kind": action_kind,
                    "bypassed_llm": True,
                    "bypassed_approval": bypasses_approval,
                }
                if bypasses_approval:
                    user_request.session_context["operator_lrdirect_bypass_confirmation"] = True
                else:
                    user_request.session_context.pop("operator_lrdirect_bypass_confirmation", None)
                self._emit(
                    observability,
                    level="info",
                    event_type=OPERATOR_VALIDATION_ACCEPTED,
                    title="LR Direct plan accepted",
                    summary=(
                        "The exact cached one-action streaming step passed validation and will "
                        "execute directly."
                        if bypasses_approval
                        else (
                            "The exact cached one-action streaming step passed validation "
                            "and will follow the normal approval path."
                        )
                    ),
                    details={
                        "cache_type": cache_type,
                        "cache_id": cache_id,
                        "lr_mode": lr_mode,
                        "exact_step_key": exact_key,
                        "action_count": len(plan.actions),
                        "task_count": len(plan.tasks),
                        "bypassed_llm": True,
                        "bypassed_approval": bypasses_approval,
                    },
                )
                return plan, []
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_REJECTED,
                title="LR Direct plan rejected",
                summary="The exact cached action failed validation; normal planning will continue.",
                details={
                    "cache_type": cache_type,
                    "cache_id": cache_id,
                    "lr_mode": lr_mode,
                    "exact_step_key": exact_key,
                    "errors": errors,
                    "bypassed_llm": False,
                },
                level="warning",
            )
        step_tree_cached = self._try_streaming_step_action_tree_cache(
            user_request,
            observability,
        )
        if step_tree_cached is not None:
            cached_plan, candidate = step_tree_cached
            plan = self._normalize_plan_interactions(cached_plan, observability)
            self._normalize_streaming_prior_python_inputs(user_request, plan, observability)
            exact_key, _ = self._lrdirect_step_metadata(user_request)
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_PLAN_PROPOSED,
                title="Streaming step tree cache plan proposed",
                summary="The runtime rebuilt a multi-action streaming-step plan from regular LR.",
                details={
                    **plan.model_dump(mode="json"),
                    "cache_type": "streaming_step_tree",
                    "cache_id": candidate.entry.cache_id,
                    "exact_step_key": exact_key,
                    "intent_signature": candidate.entry.intent_signature,
                    "bypassed_llm": True,
                    "bypassed_approval": False,
                },
            )
            errors = self._validate_plan(user_request, plan, observability)
            if not errors:
                plan, memory_errors = self._review_memory_compliance(
                    user_request,
                    plan,
                    observability,
                    source="streaming_step_tree_cache",
                )
                errors = memory_errors or []
            if not errors:
                if self.plan_cache_store is not None:
                    self.plan_cache_store.mark_used(candidate.entry.cache_id)
                user_request.session_context["operator_plan_cache_applied"] = {
                    "cache_id": candidate.entry.cache_id,
                    "score": candidate.score,
                    "cache_type": "streaming_step_tree",
                    "exact_step_key": exact_key,
                    "intent_signature": candidate.entry.intent_signature,
                }
                self._emit(
                    observability,
                    level="info",
                    event_type=OPERATOR_VALIDATION_ACCEPTED,
                    title="Streaming step tree cache plan accepted",
                    summary=(
                        "The exact cached streaming-step command tree passed validation "
                        "and will follow the normal approval path."
                    ),
                    details={
                        "cache_type": "streaming_step_tree",
                        "cache_id": candidate.entry.cache_id,
                        "exact_step_key": exact_key,
                        "intent_signature": candidate.entry.intent_signature,
                        "action_count": len(plan.actions),
                        "task_count": len(plan.tasks),
                        "bypassed_llm": True,
                        "bypassed_approval": False,
                    },
                )
                return plan, []
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_PLAN_CACHE_REJECTED,
                title="Streaming step tree cache plan rejected",
                summary="The exact cached streaming-step command tree failed validation; normal planning will continue.",
                details={
                    "cache_type": "streaming_step_tree",
                    "cache_id": candidate.entry.cache_id,
                    "exact_step_key": exact_key,
                    "errors": errors,
                },
            )
        command_template_cached = self._try_command_template_cache_adaptation(
            user_request,
            observability,
        )
        if command_template_cached is not None:
            cached_plan, candidate = command_template_cached
            entry_lr_mode = normalize_lr_mode(candidate.entry.lr_mode)
            entry_cache_type = (
                "payload_command_template"
                if entry_lr_mode == LR_MODE_PAYLOAD
                else "command_template"
            )
            plan = self._normalize_plan_interactions(cached_plan, observability)
            self._normalize_streaming_prior_python_inputs(user_request, plan, observability)
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_PLAN_PROPOSED,
                title="Operator command template plan proposed",
                summary="The runtime built an operator plan from a learned command template.",
                details={
                    **plan.model_dump(mode="json"),
                    "command_template_id": candidate.entry.template_id,
                    "cache_type": entry_cache_type,
                    "lr_mode": entry_lr_mode,
                    "cache_score": candidate.score,
                },
            )
            errors = self._validate_plan(user_request, plan, observability)
            if not errors:
                plan = self._review_validated_plan(user_request, plan, observability)
                plan, deliberation_errors = self._review_guided_plan_critique(
                    user_request,
                    plan,
                    observability,
                    phase="command_template_cache_plan",
                )
                errors = deliberation_errors or []
            if not errors:
                plan, memory_errors = self._review_memory_compliance(
                    user_request,
                    plan,
                    observability,
                    source="command_template_cache",
                )
                if memory_errors:
                    errors = memory_errors
                    self._emit(
                        observability,
                        level="warning",
                        event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                        title="Operator command template plan rejected by memory",
                        summary="The learned command template plan conflicted with retrieved memory; normal planning will continue.",
                        details={
                            "command_template_id": candidate.entry.template_id,
                            "errors": memory_errors,
                        },
                    )
                else:
                    if self.command_template_cache_store is not None:
                        self.command_template_cache_store.mark_used(candidate.entry.template_id)
                    user_request.session_context["operator_command_template_cache_applied"] = {
                        "template_id": candidate.entry.template_id,
                        "score": candidate.score,
                        "lr_mode": entry_lr_mode,
                        "cache_type": entry_cache_type,
                    }
                    self._emit(
                        observability,
                        level="info",
                        event_type=OPERATOR_VALIDATION_ACCEPTED,
                        title="Operator command template plan accepted",
                        summary="The learned command template plan passed deterministic validation and memory review.",
                        details={
                            "command_template_id": candidate.entry.template_id,
                            "cache_type": entry_cache_type,
                            "lr_mode": entry_lr_mode,
                            "payload_binding_count": len(candidate.entry.payload_bindings or []),
                            "payload_binding_kinds": self._lrex_payload_binding_kinds(
                                candidate.entry.payload_bindings
                            ),
                            "action_count": len(plan.actions),
                            "task_count": len(plan.tasks),
                        },
                    )
                    return plan, []
            if errors:
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_VALIDATION_REJECTED,
                    title="Operator command template plan rejected",
                    summary="The learned command template plan failed validation; normal planning will continue.",
                    details={
                        "command_template_id": candidate.entry.template_id,
                        "errors": errors,
                    },
                )
        cached = self._try_plan_cache_adaptation(user_request, observability)
        if cached is not None:
            cached_plan, candidate = cached
            plan = self._normalize_plan_interactions(cached_plan, observability)
            self._normalize_streaming_prior_python_inputs(user_request, plan, observability)
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_PLAN_PROPOSED,
                title="Operator cached plan proposed",
                summary="The LLM adapted a private cached operator plan skeleton.",
                details={
                    **plan.model_dump(mode="json"),
                    "cache_id": candidate.entry.cache_id,
                    "cache_score": candidate.score,
                },
            )
            plan = self._normalize_plan_interactions(
                self._defer_dependent_python_actions(plan, observability),
                observability,
            )
            errors = self._validate_plan(user_request, plan, observability)
            if errors and self._single_report_compiler_needed(user_request, errors):
                compiled_plan, compiler_errors = self._compile_single_report_plan(
                    user_request,
                    errors,
                    plan,
                    observability,
                    source="cache",
                )
                if compiled_plan is not None:
                    return compiled_plan, []
                errors = compiler_errors
            if errors:
                tryout_plan = self._tryout_valid_runnable_plan(
                    user_request,
                    plan,
                    errors,
                    observability,
                )
                if tryout_plan is not None:
                    return tryout_plan, []
            if not errors:
                plan = self._review_validated_plan(user_request, plan, observability)
                plan, deliberation_errors = self._review_guided_plan_critique(
                    user_request,
                    plan,
                    observability,
                    phase="cache_plan",
                )
                if deliberation_errors:
                    errors = deliberation_errors
                else:
                    errors = []
            if not errors:
                plan, memory_errors = self._review_memory_compliance(
                    user_request,
                    plan,
                    observability,
                    source="cache",
                )
                if memory_errors:
                    self._emit(
                        observability,
                        level="warning",
                        event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                        title="Operator cached plan rejected by memory",
                        summary="The adapted cached plan conflicted with retrieved memory; normal planning will continue.",
                        details={
                            "cache_id": candidate.entry.cache_id,
                            "errors": memory_errors,
                        },
                    )
                else:
                    if self.plan_cache_store is not None:
                        self.plan_cache_store.mark_used(candidate.entry.cache_id)
                    user_request.session_context["operator_plan_cache_applied"] = {
                        "cache_id": candidate.entry.cache_id,
                        "score": candidate.score,
                    }
                    self._emit(
                        observability,
                        level="info",
                        event_type=OPERATOR_VALIDATION_ACCEPTED,
                        title="Operator cached plan accepted",
                        summary="The adapted cached plan passed deterministic validation and memory review.",
                        details={
                            "cache_id": candidate.entry.cache_id,
                            "action_count": len(plan.actions),
                            "task_count": len(plan.tasks),
                        },
                    )
                    self._emit_tryout_validated_plan_accepted(
                        plan,
                        observability,
                        source="cache",
                    )
                    return plan, []
            if not errors:
                errors = memory_errors
            if errors:
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_VALIDATION_REJECTED,
                    title="Operator cached plan rejected",
                    summary="The adapted cached plan failed validation; normal planning will continue.",
                    details={
                        "cache_id": candidate.entry.cache_id,
                        "errors": errors,
                    },
                )
        brief = self._ensure_self_brief(user_request, observability)
        self._ensure_deliberation_frame(user_request, brief, observability, phase="pre_plan")
        for attempt in range(total_attempts):
            try:
                plan = (
                    self._complete_plan(user_request, feedback)
                    if attempt == 0
                    else self._complete_repair(user_request, feedback or last_errors, rejected_plan)
                )
            except (PydanticValidationError, StructuredCallError) as exc:
                failure_kind = self.reliability.record_failure(
                    request_id=user_request.request_id,
                    stage="operator.plan_schema",
                    payload=str(exc),
                    title="Operator plan schema failure",
                    summary="The LLM operator payload did not match the typed schema.",
                )
                decision = self.reliability.decide(
                    failure_kind,
                    budget=self.reliability.budget_from_context(user_request.session_context),
                )
                self.reliability.record_decision(
                    user_request.request_id,
                    decision,
                    stage="operator.plan_schema",
                )
                last_errors = [
                    {
                        "error": "schema_validation_error",
                        "message": str(exc),
                        "failure_kind": failure_kind,
                        "recovery_action": decision.action,
                    }
                ]
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_VALIDATION_REJECTED,
                    title="Operator plan schema rejected",
                    summary="The LLM operator payload did not match the typed schema.",
                    details={"attempt": attempt + 1, "errors": last_errors},
                )
                feedback = last_errors
                rejected_plan = None
                continue

            plan = self._normalize_plan_interactions(plan, observability)
            self._normalize_streaming_prior_python_inputs(user_request, plan, observability)
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_PLAN_PROPOSED if attempt == 0 else OPERATOR_REPAIR_PROPOSED,
                title="Operator plan proposed" if attempt == 0 else "Operator repair proposed",
                summary="The LLM proposed an operator plan.",
                details=plan.model_dump(mode="json"),
            )
            plan = self._normalize_plan_interactions(
                self._defer_dependent_python_actions(plan, observability),
                observability,
            )
            compiler_diagnostics = self.reliability.compile_plan(
                request_id=user_request.request_id,
                plan=plan,
                workspace_root=str(self.config.workspace_root),
            )
            if compiler_diagnostics:
                self._emit(
                    observability,
                    level="warning",
                    event_type="operator.reliability.plan_compiler_diagnostics",
                    title="Reliability compiler diagnostics",
                    summary="The Reliability Kernel found plan risks before validation.",
                    details={"diagnostics": compiler_diagnostics},
                )
            errors = self._validate_plan(user_request, plan, observability)
            if errors and self._single_report_compiler_needed(user_request, errors):
                compiled_plan, compiler_errors = self._compile_single_report_plan(
                    user_request,
                    errors,
                    plan,
                    observability,
                    source="operator_plan" if attempt == 0 else "operator_repair",
                )
                if compiled_plan is not None:
                    return compiled_plan, []
                last_errors = compiler_errors
                rejected_plan = plan
                feedback = compiler_errors
                break
            if errors:
                tryout_plan = self._tryout_valid_runnable_plan(
                    user_request,
                    plan,
                    errors,
                    observability,
                )
                if tryout_plan is not None:
                    return tryout_plan, []
            if not errors:
                plan = self._review_validated_plan(user_request, plan, observability)
                plan, deliberation_errors = self._review_guided_plan_critique(
                    user_request,
                    plan,
                    observability,
                    phase="fresh_plan" if attempt == 0 else "repair_plan",
                    feedback=feedback,
                )
                if deliberation_errors:
                    errors = deliberation_errors
                else:
                    errors = []
            if not errors:
                plan, memory_errors = self._review_memory_compliance(
                    user_request,
                    plan,
                    observability,
                    source="fresh_plan" if attempt == 0 else "repair_plan",
                )
                if not memory_errors:
                    self._emit(
                        observability,
                        level="info",
                        event_type=(
                            OPERATOR_VALIDATION_ACCEPTED
                            if attempt == 0
                            else OPERATOR_REPAIR_ACCEPTED
                        ),
                        title="Operator plan accepted",
                        summary="The operator plan passed deterministic validation.",
                        details={"action_count": len(plan.actions), "task_count": len(plan.tasks)},
                    )
                    self._emit_tryout_validated_plan_accepted(
                        plan,
                        observability,
                        source="fresh_plan" if attempt == 0 else "repair_plan",
                    )
                    return plan, []
                errors = memory_errors
            last_errors = errors
            rejected_plan = plan
            failure_kind = self.reliability.record_failure(
                request_id=user_request.request_id,
                stage="operator.validation",
                payload=errors,
                title="Operator plan validation failure",
                summary="The candidate operator plan failed validation or memory compliance.",
            )
            decision = self.reliability.decide(
                failure_kind,
                budget=self.reliability.budget_from_context(user_request.session_context),
            )
            self.reliability.record_decision(
                user_request.request_id,
                decision,
                stage="operator.validation",
            )
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_VALIDATION_REJECTED,
                title="Operator plan rejected",
                summary="The operator plan failed deterministic validation or memory compliance.",
                details={
                    "attempt": attempt + 1,
                    "errors": errors,
                    "failure_kind": failure_kind,
                    "recovery_action": decision.action,
                },
            )
            feedback = errors
        retry_request = self._auto_rephrase_retry_request(
            user_request,
            last_errors,
            observability,
            source="operator_plan",
        )
        if retry_request is not None:
            try:
                plan, feedback = self.propose_and_validate(retry_request, observability)
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
                    summary="The rephrased prompt still failed planning validation.",
                    details={"source": "operator_plan", "errors": list(exc.errors)},
                )
                raise
            self._mark_auto_rephrase_retry_trace(retry_request, status="succeeded")
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_AUTO_REPHRASE_RETRY,
                title="Operator auto rephrase retry succeeded",
                summary="The rephrased prompt produced a valid operator plan.",
                details={
                    "source": "operator_plan",
                    "action_count": len(plan.actions),
                    "task_count": len(plan.tasks),
                },
            )
            return plan, feedback
        self._emit(
            observability,
            level="error",
            event_type=OPERATOR_REPAIR_REJECTED,
            title="Operator plan repair rejected",
            summary="The operator plan still failed after configured repair attempts.",
            details={"errors": last_errors},
        )
        raise OperatorValidationError(last_errors)
