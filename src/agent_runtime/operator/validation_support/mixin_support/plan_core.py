"""Core plan validation orchestration helpers."""

from __future__ import annotations

from agent_runtime.operator.validation_support.mixin_support.common import *
from agent_runtime.operator.validation_support.mixin_support.common import _ValidationConstantsMixin


class _ValidationPlanCoreMixin(_ValidationConstantsMixin):
    def _validate_plan(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
    ) -> list[dict[str, Any]]:
        """Run deterministic validation, then scoped LLM adjudication for marked errors."""

        shell_binding_mode = _shell_input_bindings_mode_from_value(
            getattr(self.config, "shell_input_bindings_mode", "allow")
        )
        _normalize_single_report_plan_references(user_request, plan, observability)
        self._deconflict_streaming_current_action_ids(user_request, plan, observability)
        self._normalize_streaming_prior_python_inputs(user_request, plan, observability)
        if shell_binding_mode != "off":
            self._normalize_streaming_prior_shell_inputs(user_request, plan, observability)
            self._normalize_same_plan_shell_dataflow_bindings(plan, observability)
        self._normalize_user_macro_shell_inputs(user_request, plan, observability)
        self._protect_literal_payload_shell_inputs(user_request, plan, observability)
        self._normalize_unused_shell_input_bindings(plan, observability)
        self._rehydrate_literal_payload_action_values(user_request, plan, observability)
        plan = self._apply_llm_effect_policy(user_request, plan, observability)
        base_errors = [
            *self._literal_payload_placeholder_errors(plan),
            *self.validator.validate(plan),
            *self._failure_masking_validation_errors(user_request, plan),
            *self._path_scope_validation_errors(user_request, plan),
            *self._streaming_step_scope_errors(user_request, plan),
            *self._streaming_prior_ambiguous_binding_errors(user_request, plan),
            *self._generated_text_plan_errors(user_request, plan),
            *self._llm_text_prior_binding_errors(user_request, plan),
            *self._generated_text_prior_reuse_errors(user_request, plan),
            *self._per_entity_scope_errors(user_request, plan),
            *self._shell_human_unit_arithmetic_errors(user_request, plan),
            *self._prior_output_literal_dataflow_errors(user_request, plan),
        ]
        shape_errors = execution_shape_plan_errors(user_request, plan)
        if any(
            str(error.get("error") or "") == "execution_shape_deferred_report_action"
            for error in shape_errors
        ):
            base_errors = [
                error
                for error in base_errors
                if str(error.get("error") or "") != "deferred_code_missing_inputs"
            ]
        errors = [
            *shape_errors,
            *base_errors,
        ]
        if shell_binding_mode != "off":
            errors.extend(
                [
                    *self._streaming_shell_multiline_dataflow_errors(user_request, plan),
                    *self._same_plan_ambiguous_missing_dataflow_binding_errors(plan),
                    *self._same_plan_command_literal_dataflow_errors(user_request, plan),
                    *self._same_plan_computed_literal_dataflow_errors(user_request, plan),
                ]
            )
        errors = self._allow_agent_parameter_env_placeholders(user_request, errors)
        errors.extend(self._agent_parameter_db_profile_validation_errors(user_request, plan))
        errors = self._apply_request_scoped_sudo_policy(
            user_request,
            plan,
            errors,
            observability,
        )
        if errors:
            action_by_id = {action.action_id: action for action in plan.actions}
            macro_context = {
                USER_MACRO_PRIVATE_CONTEXT_KEY: [
                    *private_user_macros_from_context(user_request.session_context),
                    *private_user_macros_from_context(user_request.safety_context),
                ]
            }
            errors = [
                error
                for error in errors
                if not (
                    error.get("error") == "shell_stdin_binding_missing"
                    and is_user_macro_input_name(
                        str(error.get("stdin_input_name") or ""),
                        macro_context,
                    )
                    and str(
                        getattr(action_by_id.get(str(error.get("action_id") or "")), "stdin_mode", "")
                        or ""
                    )
                    == "input_binding"
                )
            ]
        external_action_ids = self._external_seed_action_ids(user_request)
        if external_action_ids:
            errors = [
                error
                for error in errors
                if not self._validation_error_resolved_by_external_seed(
                    error,
                    external_action_ids,
                )
            ]
        if not errors:
            return []
        final_errors: list[dict[str, Any]] = []
        for error in errors:
            if not self._context_sensitive_validation_error(error):
                final_errors.append(error)
                continue
            allowed, adjudicated_error = self._adjudicate_validation_error(
                user_request,
                plan,
                error,
                observability,
            )
            if not allowed:
                final_errors.append(adjudicated_error)
        return final_errors

    def _apply_request_scoped_sudo_policy(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        errors: list[dict[str, Any]],
        observability: ObservabilityContext | None = None,
    ) -> list[dict[str, Any]]:
        """Apply request-local sudo authorization without mutating global allowlists."""

        action_by_id = {action.action_id: action for action in plan.actions}
        filtered: list[dict[str, Any]] = []
        overrides: list[dict[str, Any]] = []
        for error in errors:
            action = action_by_id.get(str(error.get("action_id") or ""))
            override = (
                request_scoped_sudo_override_for_error(user_request, action, error)
                if action is not None
                else None
            )
            if override is None:
                filtered.append(error)
                continue
            record_request_scoped_sudo_override(user_request, override)
            overrides.append(override)
        if overrides:
            self._emit(
                observability,
                level="info",
                event_type="operator.sudo.request_scoped_override",
                title="Request-scoped sudo authorized",
                summary="The request explicitly authorized sudo, so overrideable sudo validation blocks were filtered for this request only.",
                details={"overrides": overrides},
            )
        return filtered

    def _apply_llm_effect_policy(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
    ) -> OperatorPlan:
        """Attach typed LLM-owned effect judgments when effect policy is LLM-owned."""

        if operator_policy_mode(self.config, "effect") != "llm":
            return plan
        subjects: list[OperatorPolicySubject] = []
        task_by_id = {task.task_id: task for task in plan.tasks}
        for action in plan.actions:
            if str(action.effect_intent or "").strip().lower() != "unknown" and float(
                action.effect_confidence or 0.0
            ) > 0.0:
                continue
            task = task_by_id.get(action.task_id)
            subjects.append(
                OperatorPolicySubject(
                    module="effect",
                    subject_id=action.action_id,
                    task=task.model_dump(mode="json") if task is not None else {},
                    action=action.model_dump(mode="json"),
                    plan={
                        "summary": plan.summary,
                        "expected_outputs": list(plan.expected_outputs),
                        "assumptions": list(plan.assumptions),
                    },
                    context={
                        "raw_prompt": str(user_request.raw_prompt or "")[:1000],
                        "operator_streaming_current_task": dict(
                            (user_request.session_context or {}).get(
                                "operator_streaming_current_task"
                            )
                            or {}
                        )
                        if isinstance(
                            (user_request.session_context or {}).get(
                                "operator_streaming_current_task"
                            ),
                            dict,
                        )
                        else {},
                    },
                )
            )
        if not subjects:
            return plan
        cache = user_request.safety_context.setdefault("operator_policy_cache", {})
        if not isinstance(cache, dict):
            cache = {}
            user_request.safety_context["operator_policy_cache"] = cache
        try:
            result = review_operator_policy_subjects(
                self.llm_client,
                subjects,
                request_id=str(getattr(observability, "request_id", "") or ""),
                mode="llm",
                cache=cache,
            )
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type="operator.policy.effect.unavailable",
                title="Effect policy unavailable",
                summary="LLM-owned effect policy failed; validation will use existing typed plan fields.",
                details={"error": str(exc)[:500]},
            )
            return plan
        decisions = {decision.subject_id: decision for decision in result.decisions}
        updated_actions: list[OperatorAction] = []
        changed: list[dict[str, Any]] = []
        for action in plan.actions:
            decision = decisions.get(action.action_id)
            if decision is None:
                updated_actions.append(action)
                continue
            update = {
                "effect_intent": decision.effect_intent,
                "effect_confidence": float(decision.confidence),
                "effect_summary": decision.reason,
            }
            updated_actions.append(action.model_copy(update=update))
            changed.append(
                {
                    "action_id": action.action_id,
                    "effect_intent": decision.effect_intent,
                    "requires_confirmation": decision.requires_confirmation,
                    "risk_level": decision.risk_level,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                }
            )
        if changed:
            self._emit(
                observability,
                level="info",
                event_type="operator.policy.effect.reviewed",
                title="Effect policy reviewed",
                summary="LLM-owned effect policy attached typed action effects.",
                details={"decisions": changed},
            )
        plan.actions = updated_actions
        return plan

    @staticmethod
    def _external_seed_action_ids(user_request: UserRequest) -> set[str]:
        """Return action IDs that are valid dataflow sources outside this step plan."""

        context = dict(user_request.session_context or {})
        action_ids: set[str] = set()
        for item in list(context.get("operator_seed_records") or []):
            if isinstance(item, OperatorExecutionRecord):
                action_id = item.action_id
            elif isinstance(item, dict):
                action_id = str(item.get("action_id") or "")
            else:
                action_id = str(getattr(item, "action_id", "") or "")
            if action_id:
                action_ids.add(action_id)
        prior_results = context.get("operator_streaming_prior_results")
        if isinstance(prior_results, dict):
            for task_result in list(prior_results.get("completed_tasks") or []):
                if not isinstance(task_result, dict):
                    continue
                for record in list(task_result.get("records") or []):
                    if isinstance(record, OperatorExecutionRecord):
                        action_id = record.action_id
                    elif isinstance(record, dict):
                        action_id = str(record.get("action_id") or "")
                    else:
                        action_id = str(getattr(record, "action_id", "") or "")
                    if action_id:
                        action_ids.add(action_id)
        return action_ids

    @staticmethod
    def _validation_error_resolved_by_external_seed(
        error: dict[str, Any],
        external_action_ids: set[str],
    ) -> bool:
        """Allow current-step plans to bind to successful records from earlier steps."""

        code = str(error.get("error") or "")
        if code == "unknown_input_binding_source":
            return str(error.get("source_action_id") or "") in external_action_ids
        if code == "unknown_action_dependency":
            return str(error.get("dependency") or "") in external_action_ids
        if code == "unknown_dependency_producer":
            return str(error.get("producer_action_id") or "") in external_action_ids
        return False

    def _normalize_plan_interactions(
        self,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
    ) -> OperatorPlan:
        """Infer terminal prompt routing before validation, approval, and execution."""

        normalized, changes = normalize_operator_plan_interactions(
            plan,
            policy_mode=operator_policy_mode(self.config, "interaction"),
        )
        if changes:
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_INTERACTION_NORMALIZED,
                title="Operator interaction mode normalized",
                summary=(
                    "The runtime marked finite credential/TTY-shaped shell commands "
                    "as may_prompt so the Agent UI terminal can receive user input."
                ),
                details={"changes": changes},
            )
        return normalized



__all__ = ["_ValidationPlanCoreMixin"]
