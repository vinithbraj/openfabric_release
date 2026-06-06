"""Plan repair, critique, compiler, and validation-review helpers."""

from __future__ import annotations

from .common import *


class _PlanRepairMixin:
    """Plan repair, critique, compiler, and validation-review helpers."""

    def _complete_plan(self, user_request: UserRequest, feedback: list[dict[str, Any]] | None = None) -> OperatorPlan:
        prompt = build_operator_plan_prompt(user_request, feedback)
        return structured_call(self.llm_client, prompt, OperatorPlan)

    def _complete_repair(
        self,
        user_request: UserRequest,
        feedback: list[dict[str, Any]],
        rejected_plan: OperatorPlan | None = None,
    ) -> OperatorPlan:
        self._attach_memory_for_stage(
            user_request,
            None,
            stage="repair",
            source="repair",
        )
        lines = [
            *prompt_lines(
                "operator.rejected_plan_repair",
                {
                    "mode_label": str(
                        user_request.session_context.get("operator_mode_label") or "Conversational"
                    ).strip()
                },
            ),
            *_operator_discoverability_lines(),
            *memory_prompt_lines_from_context(user_request, stage="repair"),
            *parameter_prompt_lines_from_context(user_request, stage="repair"),
            *_operator_clarification_lines(user_request),
            *_operator_policy_note_lines(user_request),
            *_operator_self_brief_lines(user_request),
            *_operator_gateway_context_lines(user_request),
            *_operator_user_macro_lines(user_request),
            *_operator_online_lookup_lines(user_request),
            *_operator_literal_payload_lines(user_request),
            *_guided_deliberation_lines(user_request),
            *_operator_execution_shape_lines(user_request),
            "Follow repair_hint fields exactly. Keep legal plan structure, and prefer python_action for command execution plus data transformation when that is simpler than shell plus Python.",
            *_operator_contract_lines(
                _terminal_cwd_from_request(user_request),
                terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
                verification_enforced=_operator_verification_enforced_from_request(user_request),
                shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
            ),
            "OperatorRepair schema:",
            _stable_json(OperatorRepair.model_json_schema()),
            "Validation feedback:",
            _stable_json(feedback),
        ]
        if rejected_plan is not None:
            lines.extend(
                [
                    "Rejected plan:",
                    rejected_plan.model_dump_json(indent=2),
                ]
            )
        lines.extend(
            [
                "User prompt:",
                user_request.raw_prompt,
            ]
        )
        prompt = "\n".join(lines)
        repair = structured_call(self.llm_client, prompt, OperatorRepair)
        if repair.corrected_plan is None:
            raise ValueError("Operator repair did not include a corrected_plan.")
        return repair.corrected_plan

    @staticmethod
    def _single_report_compiler_needed(
        user_request: UserRequest,
        errors: list[dict[str, Any]],
    ) -> bool:
        del user_request, errors
        return False

    @staticmethod
    def _single_report_compiler_failed_errors(
        errors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "error": _SINGLE_REPORT_COMPILER_FAILURE,
                "message": (
                    "The retired execution-shape compiler path is disabled; normal "
                    "granular planning and repair must continue."
                ),
                "compiler_errors": list(errors),
            }
        ]

    def _normalize_single_report_compiler_python(
        self,
        plan: OperatorPlan,
        observability: ObservabilityContext | None,
    ) -> OperatorPlan:
        """Move import-only preambles under def main(inputs): for compiler Python actions."""

        changed: list[str] = []
        actions: list[OperatorAction] = []
        marker = "def main(inputs):"
        for action in plan.actions:
            if action.kind != "python_action":
                actions.append(action)
                continue
            raw_code = str(action.code or "")
            stripped = raw_code.lstrip()
            if stripped.startswith(marker):
                actions.append(action)
                continue
            marker_index = stripped.find(marker)
            if marker_index <= 0:
                actions.append(action)
                continue
            preamble = stripped[:marker_index].strip()
            preamble_lines = [line.strip() for line in preamble.splitlines() if line.strip()]
            if not preamble_lines or not all(
                line.startswith(("import ", "from ")) or line.startswith("#")
                for line in preamble_lines
            ):
                actions.append(action)
                continue
            rest = stripped[marker_index + len(marker) :]
            moved_preamble = "\n".join(f"    {line}" for line in preamble_lines)
            code = f"{marker}\n{moved_preamble}{rest}"
            actions.append(action.model_copy(update={"code": code}))
            changed.append(action.action_id)
        if not changed:
            return plan
        self._emit(
            observability,
            level="info",
            event_type="operator.execution_shape.single_report_python_normalized",
            title="Retired compiler Python normalized",
            summary="The compiler moved import-only Python preambles inside def main(inputs): before validation.",
            details={"action_ids": changed},
        )
        return plan.model_copy(update={"actions": actions})

    def _compile_single_report_plan(
        self,
        user_request: UserRequest,
        validation_errors: list[dict[str, Any]],
        rejected_plan: OperatorPlan | None,
        observability: ObservabilityContext | None,
        *,
        source: str,
        conversation_context: dict[str, Any] | None = None,
    ) -> tuple[OperatorPlan | None, list[dict[str, Any]]]:
        """Retired compatibility hook; report-shaped work now follows normal repair."""

        del user_request, rejected_plan, observability, source, conversation_context
        return None, self._single_report_compiler_failed_errors(validation_errors)

    def _review_guided_plan_critique(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
        *,
        phase: str = "plan_review",
        feedback: list[dict[str, Any]] | None = None,
    ) -> tuple[OperatorPlan, list[dict[str, Any]]]:
        """Run one bounded Deep Reasoning plan critique when enabled."""

        trigger_reason = self._guided_deliberation_trigger_reason(
            user_request,
            phase=phase,
            plan=plan,
            feedback=feedback,
        )
        if not trigger_reason:
            self._emit_guided_deliberation_skipped(
                user_request,
                observability,
                phase=phase,
                reason="Deep Reasoning is off or auto mode skipped this plan.",
            )
            return plan, []
        brief = self._ensure_self_brief(user_request, observability)
        self._ensure_deliberation_frame(
            user_request,
            brief,
            observability,
            phase=phase,
            reason=trigger_reason,
        )
        try:
            critique = structured_call(
                self.llm_client,
                build_plan_critique_prompt(user_request, plan, phase=phase),
                PlanCritique,
            )
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_DELIBERATION_SKIPPED,
                title="Deep Reasoning plan critique rejected",
                summary="The plan critique did not produce a valid typed response; the runtime will continue with the last validated plan.",
                details={
                    "phase": phase,
                    "mode": self._guided_deliberation_mode(),
                    "reason": trigger_reason,
                    "error": str(exc),
                },
            )
            return plan, []
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_DELIBERATION_REVIEWED,
            title="Deep Reasoning audited plan",
            summary="The LLM audited the candidate plan for concrete goal, evidence, safety, and failure-mode risks.",
            details={
                "phase": phase,
                "mode": self._guided_deliberation_mode(),
                "trigger_reason": trigger_reason,
                "decision": critique.decision,
                "satisfies_user_goal": critique.satisfies_user_goal,
                "missing_evidence": list(critique.missing_evidence),
                "unnecessary_actions": list(critique.unnecessary_actions),
                "safer_alternative": critique.safer_alternative,
                "repair_guidance": critique.repair_guidance,
                "violated_memory_ids": list(critique.violated_memory_ids),
                "reason": critique.reason,
                "confidence": critique.confidence,
            },
        )
        if critique.decision == "accept":
            return plan, []
        if critique.decision == "ask_user" and critique.clarification_request is not None:
            return plan, [
                {
                    "error": "guided_deliberation_ask_user",
                    "message": critique.reason or "Deep Reasoning requires a user clarification.",
                    "clarification_request": critique.clarification_request.model_dump(mode="json"),
                }
            ]
        if critique.decision == "block":
            return plan, [
                {
                    "error": "guided_deliberation_blocked",
                    "message": critique.reason or "Deep Reasoning blocked the candidate plan.",
                    "repair_hint": critique.repair_guidance,
                    "confidence": critique.confidence,
                }
            ]
        if critique.decision != "repair_required":
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_DELIBERATION_SKIPPED,
                title="Deep Reasoning audit ignored",
                summary="Deep Reasoning returned no actionable audit decision; the runtime will continue with the validated plan.",
                details={
                    "phase": phase,
                    "mode": self._guided_deliberation_mode(),
                    "trigger_reason": trigger_reason,
                    "repair_guidance": critique.repair_guidance,
                    "confidence": critique.confidence,
                },
            )
            return plan, []
        repair_confidence_threshold = self._repair_confidence_threshold()
        if critique.confidence < repair_confidence_threshold:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_DELIBERATION_SKIPPED,
                title="Deep Reasoning low-confidence repair skipped",
                summary="Deep Reasoning requested plan repair, but confidence was too low to override the validated plan.",
                details={
                    "phase": phase,
                    "mode": self._guided_deliberation_mode(),
                    "trigger_reason": trigger_reason,
                    "repair_guidance": critique.repair_guidance,
                    "confidence": critique.confidence,
                    "repair_confidence_threshold": repair_confidence_threshold,
                },
            )
            return plan, []
        if critique.revised_plan is not None:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_DELIBERATION_SKIPPED,
                title="Deep Reasoning rewrite ignored",
                summary=(
                    "Deep Reasoning returned a replacement plan, but direct rewrites are disabled; "
                    "only audit feedback will be sent to the normal repair path."
                ),
                details={
                    "phase": phase,
                    "mode": self._guided_deliberation_mode(),
                    "trigger_reason": trigger_reason,
                    "confidence": critique.confidence,
                },
            )
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_DELIBERATION_AUDIT_FEEDBACK,
            title="Deep Reasoning audit sent to repair",
            summary="Deep Reasoning found a concrete issue; the normal operator repair path will decide any plan changes.",
            details={
                "phase": phase,
                "mode": self._guided_deliberation_mode(),
                "trigger_reason": trigger_reason,
                "repair_guidance": critique.repair_guidance,
                "missing_evidence": list(critique.missing_evidence),
                "unnecessary_actions": list(critique.unnecessary_actions),
                "safer_alternative": critique.safer_alternative,
                "violated_memory_ids": list(critique.violated_memory_ids),
                "reason": critique.reason,
                "confidence": critique.confidence,
            },
        )
        return plan, [
            {
                "error": "guided_deliberation_audit_repair_required",
                "message": critique.reason or "Deep Reasoning audit found a concrete issue.",
                "repair_hint": critique.repair_guidance,
                "missing_evidence": list(critique.missing_evidence),
                "unnecessary_actions": list(critique.unnecessary_actions),
                "safer_alternative": critique.safer_alternative,
                "violated_memory_ids": list(critique.violated_memory_ids),
                "confidence": critique.confidence,
            }
        ]

    def _review_validated_plan(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
    ) -> OperatorPlan:
        """Ask the LLM to review a valid plan for answer quality before execution."""

        if not self._operator_plan_review_enabled():
            return self._normalize_plan_interactions(plan, observability)
        current_plan = self._normalize_plan_interactions(plan, observability)
        total_reviews = max(
            1,
            self._attempt_count(self._max_validation_repair_attempts()) + 1,
        )
        last_review: OperatorPlanReview | None = None
        for review_index in range(total_reviews):
            prompt = build_operator_plan_review_prompt(
                user_request,
                current_plan,
                previous_review=last_review,
            )
            try:
                review = structured_call(self.llm_client, prompt, OperatorPlanReview)
            except Exception as exc:
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_PLAN_REVIEW_REJECTED,
                    title="Operator plan review payload ignored",
                    summary=(
                        "The plan review response did not satisfy the review schema; "
                        "the runtime will continue with the last validated plan."
                    ),
                    details={
                        "review_index": review_index + 1,
                        "error": "plan_review_payload_validation_failed",
                        "message": str(exc),
                    },
                )
                return current_plan
            last_review = review
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_PLAN_REVIEW_PROPOSED,
                title="Operator plan reviewed",
                summary="The LLM reviewed the validated operator plan for command quality and answer completeness.",
                details={
                    "review_index": review_index + 1,
                    "decision": review.decision,
                    "strategy_alignment": review.strategy_alignment,
                    "postcondition_alignment": review.postcondition_alignment,
                    "verification_contract_alignment": review.verification_contract_alignment,
                    "verification_gap": review.verification_gap,
                    "required_revision": review.required_revision,
                    "issues": list(review.issues),
                    "reason": review.reason,
                    "confidence": review.confidence,
                },
            )
            if review.decision == "accept":
                self._emit(
                    observability,
                    level="info",
                    event_type=OPERATOR_PLAN_REVIEW_ACCEPTED,
                    title="Operator plan review accepted",
                    summary="The LLM quality review accepted the validated operator plan.",
                    details={"review_index": review_index + 1, "confidence": review.confidence},
                )
                return current_plan
            if review.revised_plan is None:
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_PLAN_REVIEW_REJECTED,
                    title="Operator plan review revision ignored",
                    summary=(
                        "The plan review asked for a revision without providing one; "
                        "the runtime will continue with the last validated plan."
                    ),
                    details={
                        "review_index": review_index + 1,
                        "error": "plan_review_missing_revision",
                        "issues": list(review.issues),
                    },
                )
                return current_plan
            revised_plan = self._normalize_plan_interactions(
                self._defer_dependent_python_actions(review.revised_plan, observability),
                observability,
            )
            if self._should_ignore_read_only_verification_expansion(
                user_request,
                current_plan,
                revised_plan,
            ):
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_PLAN_REVIEW_REJECTED,
                    title="Operator plan review revision ignored",
                    summary=(
                        "The plan review tried to add a redundant verification action "
                        "to a read-only computed-answer plan."
                    ),
                    details={
                        "review_index": review_index + 1,
                        "error": "read_only_verification_expansion_ignored",
                        "required_revision": review.required_revision,
                        "issues": list(review.issues),
                    },
                )
                return current_plan
            errors = self._validate_plan(user_request, revised_plan, observability)
            if errors:
                enriched_errors = [
                    {
                        "error": "plan_review_revision_validation_failed",
                        "message": "The LLM plan review revision failed validation.",
                        "strategy_alignment": review.strategy_alignment,
                        "postcondition_alignment": review.postcondition_alignment,
                        "verification_contract_alignment": review.verification_contract_alignment,
                        "verification_gap": review.verification_gap,
                        "required_revision": review.required_revision,
                        "issues": list(review.issues),
                        "validation_errors": errors,
                    }
                ]
                if self._max_validation_repair_attempts() > 0:
                    try:
                        repaired_plan = self._complete_repair(
                            user_request,
                            enriched_errors,
                            revised_plan,
                        )
                        repaired_plan = self._defer_dependent_python_actions(
                            repaired_plan,
                            observability,
                        )
                        repaired_plan = self._normalize_plan_interactions(
                            repaired_plan,
                            observability,
                        )
                        repair_errors = self._validate_plan(user_request, repaired_plan, observability)
                    except Exception as exc:
                        repair_errors = [{"error": "plan_review_revision_repair_failed", "message": str(exc)}]
                    if not repair_errors:
                        revised_plan = repaired_plan
                    else:
                        enriched_errors[0]["repair_errors"] = repair_errors
                        self._emit(
                            observability,
                            level="warning",
                            event_type=OPERATOR_PLAN_REVIEW_REJECTED,
                            title="Operator plan review revision ignored",
                            summary=(
                                "The LLM plan review produced an invalid revision; "
                                "the runtime will continue with the last validated plan."
                            ),
                            details={"errors": enriched_errors, "issues": list(review.issues)},
                        )
                        return current_plan
                else:
                    self._emit(
                        observability,
                        level="warning",
                        event_type=OPERATOR_PLAN_REVIEW_REJECTED,
                        title="Operator plan review revision ignored",
                        summary=(
                            "The LLM plan review produced an invalid revision; "
                            "the runtime will continue with the last validated plan."
                        ),
                        details={"errors": enriched_errors, "issues": list(review.issues)},
                    )
                    return current_plan
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_PLAN_REVIEW_ACCEPTED,
                title="Operator plan review revised",
                summary="The LLM quality review revised the operator plan before execution; the revision will be reviewed again.",
                details={
                    "review_index": review_index + 1,
                    "issues": list(review.issues),
                    "strategy_alignment": review.strategy_alignment,
                    "postcondition_alignment": review.postcondition_alignment,
                    "verification_contract_alignment": review.verification_contract_alignment,
                    "verification_gap": review.verification_gap,
                    "required_revision": review.required_revision,
                    "reason": review.reason,
                    "confidence": review.confidence,
                    "action_count": len(revised_plan.actions),
                },
            )
            current_plan = revised_plan
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_PLAN_REVIEW_REJECTED,
            title="Operator plan review budget exhausted",
            summary=(
                "The plan review did not accept a revision within budget; "
                "the runtime will continue with the last validated plan."
            ),
            details={
                "issues": list(last_review.issues) if last_review is not None else [],
                "verification_contract_alignment": (
                    last_review.verification_contract_alignment if last_review is not None else ""
                ),
                "verification_gap": last_review.verification_gap if last_review is not None else "",
                "required_revision": last_review.required_revision if last_review is not None else "",
            },
        )
        return current_plan

    def _defer_dependent_python_actions(
        self,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
    ) -> OperatorPlan:
        """Move upstream-dependent Python code generation to execution time."""

        changed_actions: list[dict[str, Any]] = []
        actions: list[OperatorAction] = []
        for action in plan.actions:
            should_defer = action.kind in {"python_action", "python_transform"} and bool(
                action.input_bindings
            )
            if not should_defer:
                actions.append(action)
                continue
            if action.defer_code_generation and not str(action.code or "").strip():
                actions.append(action)
                continue
            actions.append(
                action.model_copy(
                    update={
                        "code": None,
                        "defer_code_generation": True,
                    }
                )
            )
            changed_actions.append(
                {
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "kind": action.kind,
                    "input_names": [binding.input_name for binding in action.input_bindings],
                }
            )
        if not changed_actions:
            return plan
        deferred_plan = plan.model_copy(update={"actions": actions})
        self._emit(
            observability,
            level="info",
            event_type="operator.python_code.deferred",
            title="Python code generation deferred",
            summary=(
                "The runtime moved upstream-dependent Python code generation "
                "until after producer outputs are available."
            ),
            details={"actions": changed_actions},
        )
        return deferred_plan
