"""Execution, completion, evidence, and generated-code review helpers."""

from __future__ import annotations

from .common import *


class _ExecutionReviewsMixin:
    """Execution, completion, evidence, and generated-code review helpers."""

    def _complete_execution_repair(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        repair_feedback: list[dict[str, Any]] | None = None,
    ) -> OperatorRepair:
        self._attach_memory_for_stage(
            user_request,
            None,
            stage="repair",
            source="execution_repair",
        )
        prompt = build_operator_repair_prompt(
            user_request,
            plan,
            records,
            repair_feedback=repair_feedback,
        )
        return structured_call(self.llm_client, prompt, OperatorRepair)

    def _complete_execution_repair_adjudication(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        repair: OperatorRepair,
        repair_feedback: list[dict[str, Any]] | None = None,
    ) -> OperatorRepairAdjudication:
        prompt = build_operator_repair_adjudication_prompt(
            user_request,
            plan,
            records,
            repair,
            repair_feedback=repair_feedback,
        )
        return structured_call(self.llm_client, prompt, OperatorRepairAdjudication)

    def complete_failure_continuation_review(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        *,
        continuation_notes: str = "",
    ) -> OperatorFailureContinuationReview:
        prompt = build_operator_failure_continuation_prompt(
            user_request,
            plan,
            records,
            continuation_notes=continuation_notes,
        )
        return structured_call(self.llm_client, prompt, OperatorFailureContinuationReview)

    def _should_ignore_read_only_verification_expansion(
        self,
        user_request: UserRequest,
        current_plan: OperatorPlan,
        revised_plan: OperatorPlan,
    ) -> bool:
        """Return whether plan review added redundant verification to a read-only plan."""

        if _EXPLICIT_VERIFICATION_REQUEST_RE.search(user_request.raw_prompt):
            return False
        if len(revised_plan.actions) <= len(current_plan.actions):
            return False
        if any(
            self._action_requires_confirmation(current_plan, action)
            for action in current_plan.actions
        ):
            return False
        current_ids = {action.action_id for action in current_plan.actions}
        added_actions = [
            action for action in revised_plan.actions if action.action_id not in current_ids
        ]
        if not added_actions:
            return False
        return all(
            _VERIFICATION_ACTION_TEXT_RE.search(
                " ".join(
                    [
                        str(action.reason or ""),
                        str(action.declared_output_shape or ""),
                        str(action.command or ""),
                    ]
                )
            )
            for action in added_actions
        )

    def _complete_completion_review(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        observability: ObservabilityContext | None = None,
    ) -> OperatorCompletionReview:
        """Ask the LLM whether the successful execution fully satisfied the goal."""

        prompt = build_operator_completion_review_prompt(user_request, plan, records)
        review = structured_call(self.llm_client, prompt, OperatorCompletionReview)
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_COMPLETION_REVIEW_PROPOSED,
            title="Operator completion reviewed",
            summary="The LLM reviewed whether the successful execution satisfied the requested end state.",
            details={
                "decision": review.decision,
                "issues": list(review.issues),
                "reason": review.reason,
                "confidence": review.confidence,
            },
        )
        return review

    def _complete_evidence_satisfaction_review(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        observability: ObservabilityContext | None = None,
    ) -> EvidenceSatisfactionReview | None:
        """Run an optional Deep Reasoning evidence review after execution."""

        trigger_reason = self._guided_deliberation_trigger_reason(
            user_request,
            phase="evidence_review",
            plan=plan,
        )
        if not trigger_reason:
            self._emit_guided_deliberation_skipped(
                user_request,
                observability,
                phase="evidence_review",
                reason="Deep Reasoning is off or auto mode skipped evidence review.",
            )
            return None
        try:
            review = structured_call(
                self.llm_client,
                build_evidence_satisfaction_review_prompt(user_request, plan, records),
                EvidenceSatisfactionReview,
            )
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_DELIBERATION_SKIPPED,
                title="Deep Reasoning evidence review rejected",
                summary="The evidence satisfaction review did not produce a valid typed response.",
                details={
                    "phase": "evidence_review",
                    "mode": self._guided_deliberation_mode(),
                    "reason": trigger_reason,
                    "error": str(exc),
                },
            )
            return None
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_DELIBERATION_REVIEWED,
            title="Deep Reasoning reviewed evidence",
            summary="The LLM checked whether execution evidence satisfies the user's request.",
            details={
                "phase": "evidence_review",
                "mode": self._guided_deliberation_mode(),
                "trigger_reason": trigger_reason,
                "decision": review.decision,
                "evidence_summary": review.evidence_summary,
                "unsatisfied_criteria": list(review.unsatisfied_criteria),
                "next_step_guidance": review.next_step_guidance,
                "reason": review.reason,
                "confidence": review.confidence,
            },
        )
        return review

    def _review_generated_python_code(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        proposal: OperatorPythonCodeProposal,
        *,
        input_packet: dict[str, Any],
        runtime_contract: dict[str, Any],
        observability: ObservabilityContext | None = None,
    ) -> OperatorPythonCodeProposal:
        """Ask the LLM to reject semantically fake generated Python code."""

        if not self._operator_plan_review_enabled():
            return proposal
        prompt = "\n".join(
            [
                *prompt_lines("operator.python_code_review"),
                *_operator_discoverability_lines(),
                *memory_prompt_lines_from_context(user_request, stage="code_generation"),
                *parameter_prompt_lines_from_context(user_request, stage="code_generation"),
                *_operator_clarification_lines(user_request),
                *_operator_policy_note_lines(user_request),
                *_operator_self_brief_lines(user_request),
                *_guided_deliberation_lines(user_request),
                *_operator_verification_mode_lines(
                    verification_enforced=_operator_verification_enforced_from_request(user_request)
                ),
                "For mutating actions, reject code that only parses target names or prints intent without executing the mutation.",
                (
                    "For mutating actions, reject code that reports success without checking command return codes and verifying fresh post-mutation state."
                    if _operator_verification_enforced_from_request(user_request)
                    else "For mutating actions, reject code that reports success without checking command return codes; do not require a separate fresh post-mutation verification when verification is relaxed unless false success is otherwise likely."
                ),
                "For verification actions, reject code that verifies against old pre-mutation input instead of fresh current state or a post-mutation read.",
                "For parsing or aggregation code, test the code mentally against the concrete authoring input preview before accepting or revising it.",
                "When revising parsers, preserve exact literals and delimiters from the input preview. Do not introduce placeholder tokens, ellipses, or invented regex fragments that are not present in the preview.",
                "If a revised parser still contains an invented literal word or fragment that is not present in the preview, reject it again and remove that fragment in the revised code.",
                "Prefer simple splitting, suffix checks, and straightforward numeric conversion over fragile regex when the preview has stable delimiters or compact unit strings.",
                "If revising, return OperatorPythonCodeReview with revised_proposal containing complete corrected code.",
                "OperatorPythonCodeReview schema:",
                _stable_json(OperatorPythonCodeReview.model_json_schema()),
                "User prompt:",
                user_request.raw_prompt,
                "Action:",
                action.model_dump_json(indent=2),
                "Runtime input contract:",
                _stable_json(runtime_contract),
                "Authoring input preview packet:",
                _stable_json(input_packet),
                "Generated proposal under review:",
                proposal.model_dump_json(indent=2),
            ]
        )
        review = structured_call(self.llm_client, prompt, OperatorPythonCodeReview)
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_PYTHON_CODE_REVIEW_PROPOSED,
            title="Deferred Python code reviewed",
            summary="The LLM reviewed generated deferred Python code for semantic correctness.",
            details={
                "decision": review.decision,
                "issues": list(review.issues),
                "reason": review.reason,
                "confidence": review.confidence,
            },
        )
        if review.decision == "accept":
            return proposal
        if review.revised_proposal is None:
            raise ValueError(
                "Generated Python code review requested revision but did not include revised_proposal."
            )
        return review.revised_proposal

    def _critique_generated_python_code(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        proposal: OperatorPythonCodeProposal,
        *,
        input_packet: dict[str, Any],
        runtime_contract: dict[str, Any],
        observability: ObservabilityContext | None = None,
    ) -> OperatorPythonCodeProposal:
        """Run an optional Deep Reasoning critique for generated Python code."""

        trigger_reason = self._guided_deliberation_trigger_reason(
            user_request,
            phase="python_code",
            plan=OperatorPlan(
                summary="Generated Python code critique placeholder.",
                tasks=[],
                actions=[action],
                dependencies=[],
                expected_outputs=[],
                assumptions=[],
                confidence=proposal.confidence,
            ),
        )
        if not trigger_reason:
            self._emit_guided_deliberation_skipped(
                user_request,
                observability,
                phase="python_code",
                reason="Deep Reasoning is off or auto mode skipped generated Python critique.",
            )
            return proposal
        try:
            critique = structured_call(
                self.llm_client,
                build_code_generation_critique_prompt(
                    user_request,
                    action,
                    proposal,
                    input_packet=input_packet,
                    runtime_contract=runtime_contract,
                ),
                CodeGenerationCritique,
            )
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_DELIBERATION_SKIPPED,
                title="Deep Reasoning code critique rejected",
                summary="The generated-code critique did not produce a valid typed response.",
                details={
                    "phase": "python_code",
                    "mode": self._guided_deliberation_mode(),
                    "reason": trigger_reason,
                    "action_id": action.action_id,
                    "error": str(exc),
                },
            )
            return proposal
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_DELIBERATION_REVIEWED,
            title="Deep Reasoning reviewed Python code",
            summary="The LLM checked whether generated Python code satisfies the action contract.",
            details={
                "phase": "python_code",
                "mode": self._guided_deliberation_mode(),
                "trigger_reason": trigger_reason,
                "action_id": action.action_id,
                "decision": critique.decision,
                "contract_issues": list(critique.contract_issues),
                "runtime_input_assumptions": list(critique.runtime_input_assumptions),
                "verification_gap": critique.verification_gap,
                "repair_guidance": critique.repair_guidance,
                "reason": critique.reason,
                "confidence": critique.confidence,
            },
        )
        if critique.decision == "accept":
            return proposal
        if critique.decision == "block":
            raise ValueError(critique.reason or "Deep Reasoning blocked generated Python code.")
        if (
            critique.decision == "repair_required"
            and critique.revised_proposal is not None
            and critique.confidence >= self._repair_confidence_threshold()
        ):
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_DELIBERATION_REPAIRED,
                title="Deep Reasoning repaired Python code",
                summary="Generated Python code was revised after guided critique.",
                details={
                    "phase": "python_code",
                    "action_id": action.action_id,
                    "repair_guidance": critique.repair_guidance,
                    "confidence": critique.confidence,
                },
            )
            return critique.revised_proposal
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_DELIBERATION_SKIPPED,
            title="Deep Reasoning low-confidence Python repair skipped",
            summary="Deep Reasoning requested generated Python repair, but confidence was too low to override the validated code.",
            details={
                "phase": "python_code",
                "mode": self._guided_deliberation_mode(),
                "trigger_reason": trigger_reason,
                "action_id": action.action_id,
                "repair_guidance": critique.repair_guidance,
                "confidence": critique.confidence,
                "repair_confidence_threshold": self._repair_confidence_threshold(),
            },
        )
        return proposal
