"""Fast tryout and capsule repair helpers."""

from __future__ import annotations

from .common import *


class _RepairTryoutMixin:
    """Fast tryout and capsule repair helpers."""

    def _tryout_plan_prefix(
        self,
        plan: OperatorPlan,
        included_action_ids: set[str],
    ) -> OperatorPlan:
        included_task_ids = {
            action.task_id
            for action in plan.actions
            if action.action_id in included_action_ids
        }
        return plan.model_copy(
            update={
                "summary": f"{plan.summary} (retired tryout prefix)",
                "tasks": [
                    task
                    for task in plan.tasks
                    if task.task_id in included_task_ids
                ],
                "actions": [
                    action
                    for action in plan.actions
                    if action.action_id in included_action_ids
                ],
                "dependencies": [
                    dependency
                    for dependency in plan.dependencies
                    if dependency.consumer_action_id in included_action_ids
                ],
                "expected_outputs": list(plan.expected_outputs),
                "assumptions": [
                    *list(plan.assumptions),
                    "Retired tryout accepted a valid executable prefix before full-plan repair.",
                ],
            }
        )

    def _tryout_candidate_order(self, plan: OperatorPlan) -> list[OperatorAction]:
        try:
            ordered = self._execution_order(plan)
        except Exception:
            ordered = []
        if len(ordered) != len(plan.actions):
            ordered = list(plan.actions)
        return ordered

    def _tryout_valid_runnable_plan(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        validation_errors: list[dict[str, Any]],
        observability: ObservabilityContext | None,
    ) -> OperatorPlan | None:
        if not self.operator_tryout_enabled() or not plan.actions:
            return None
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_TRYOUT_CANDIDATE,
            title="Tryout candidate received",
            summary="Retired tryout is inspecting the LLM-authored plan for runnable action capsules.",
            details={
                "action_count": len(plan.actions),
                "task_count": len(plan.tasks),
                "full_plan_errors": validation_errors,
            },
        )
        included: set[str] = set()
        best_plan: OperatorPlan | None = None
        best_action_count = 0
        blocked_errors: list[dict[str, Any]] = []
        for action in self._tryout_candidate_order(plan):
            included.add(action.action_id)
            candidate = self._tryout_plan_prefix(plan, included)
            errors = self._validate_plan(user_request, candidate, observability)
            if errors:
                blocked_errors = errors
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_TRYOUT_BLOCKED,
                    title="Tryout capsule blocked",
                    summary="A candidate action prefix failed runtime validation and will not execute.",
                    details={
                        "action_id": action.action_id,
                        "candidate_action_ids": [item.action_id for item in candidate.actions],
                        "errors": errors,
                    },
                )
                break
            best_plan = candidate
            best_action_count = len(candidate.actions)
        if best_plan is None:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_TRYOUT_BLOCKED,
                title="Tryout blocked",
                summary="Retired tryout found no structurally valid action capsule to execute.",
                details={"errors": blocked_errors or validation_errors},
            )
            return None
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_TRYOUT_ACCEPTED,
            title="Tryout prefix accepted",
            summary="Retired tryout accepted the valid LLM-authored action prefix for immediate execution.",
            details={
                **best_plan.model_dump(mode="json"),
                "accepted_action_count": best_action_count,
                "original_action_count": len(plan.actions),
                "remaining_action_count": max(0, len(plan.actions) - best_action_count),
                "blocked_errors": blocked_errors,
            },
        )
        return best_plan

    def _emit_tryout_validated_plan_accepted(
        self,
        plan: OperatorPlan,
        observability: ObservabilityContext | None,
        *,
        source: str,
    ) -> None:
        if not self.operator_tryout_enabled():
            return
        details = {
            **plan.model_dump(mode="json"),
            "source": source,
            "accepted_action_count": len(plan.actions),
            "original_action_count": len(plan.actions),
            "remaining_action_count": 0,
        }
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_TRYOUT_CANDIDATE,
            title="Tryout candidate received",
            summary="Retired tryout received a fully valid LLM-authored plan.",
            details=details,
        )
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_TRYOUT_ACCEPTED,
            title="Tryout plan accepted",
            summary="Retired tryout accepted the validated LLM-authored plan for immediate execution.",
            details=details,
        )

    def _complete_tryout_capsule_batch(
        self,
        user_request: UserRequest,
        feedback: list[dict[str, Any]] | None = None,
    ) -> OperatorTryoutCapsuleBatch:
        prompt = build_operator_tryout_capsule_prompt(user_request, feedback)
        return structured_call(
            self.llm_client,
            prompt,
            OperatorTryoutCapsuleBatch,
            repair_attempts=0,
        )

    def _complete_tryout_result_review(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        observability: ObservabilityContext | None = None,
    ) -> OperatorTryoutResultReview:
        """Ask the LLM whether retired tryout execution evidence satisfies the request."""

        review = structured_call(
            self.llm_client,
            build_operator_tryout_result_review_prompt(user_request, plan, records),
            OperatorTryoutResultReview,
            repair_attempts=0,
        )
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_TRYOUT_COMPLETED,
            title="retired tryout result reviewed",
            summary="The LLM reviewed whether the fast capsule evidence satisfies the request.",
            details={
                "decision": review.decision,
                "issues": list(review.issues),
                "reason": review.reason,
                "confidence": review.confidence,
            },
        )
        return review

    @staticmethod
    def _tryout_result_review_error(
        review: OperatorTryoutResultReview | None,
        *,
        exception: Exception | None = None,
        attempt: int,
    ) -> list[dict[str, Any]]:
        if exception is not None:
            return [
                {
                    "error": "fast_trout_result_review_failed",
                    "message": str(exception),
                    "attempt": attempt,
                }
            ]
        if review is None:
            return [
                {
                    "error": "fast_trout_result_review_missing",
                    "message": "retired tryout result review was unavailable.",
                    "attempt": attempt,
                }
            ]
        message = (
            review.repair_feedback
            or review.next_step_hint
            or review.reason
            or "The retired tryout result review found unsatisfied request criteria."
        )
        return [
            {
                "error": "fast_trout_result_review_rejected",
                "message": message,
                "decision": review.decision,
                "issues": list(review.issues),
                "evidence_summary": review.evidence_summary,
                "next_step_hint": review.next_step_hint,
                "reason": review.reason,
                "confidence": review.confidence,
                "attempt": attempt,
            }
        ]

    def _fast_trout_review_rejected_result(
        self,
        result: OperatorPipelineResult,
        *,
        review_errors: list[dict[str, Any]],
        attempt: int,
        llm_call_count: int,
        mutating_plan: bool,
    ) -> OperatorPipelineResult:
        metadata = dict(result.metadata)
        next_step_hint = " ".join(
            str(error.get("message") or error.get("next_step_hint") or "").strip()
            for error in review_errors
            if str(error.get("message") or error.get("next_step_hint") or "").strip()
        ).strip()
        metadata.update(
            {
                "operator_fast_trout_goal_complete": False,
                "operator_fast_trout_result_review_decision": "rejected",
                "operator_fast_trout_result_review_errors": review_errors,
                "operator_fast_trout_fallback_reason": "result_review_rejected",
                "operator_fast_trout_next_step_hint": next_step_hint,
                "operator_fast_trout_attempt": attempt,
                "operator_fast_trout_llm_call_count": llm_call_count,
            }
        )
        if not mutating_plan:
            return result.model_copy(update={"metadata": metadata})
        final_response = "\n\n".join(
            [
                "## retired tryout Result Review Failed",
                "The fast capsule pass executed mutating work, but the result review did not accept the evidence as complete.",
                next_step_hint or "Review the execution records before retrying or continuing.",
            ]
        ).strip()
        return result.model_copy(
            update={
                "status": "error",
                "final_response": final_response,
                "metadata": {
                    **metadata,
                    "operator_fast_trout_mutating_review_rejected": True,
                },
            }
        )

    @staticmethod
    def _tryout_schema_error(exc: Exception, *, attempt: int) -> list[dict[str, Any]]:
        diagnostics = getattr(exc, "diagnostics", None)
        if diagnostics is None:
            return [
                {
                    "error": "fast_trout_schema_error",
                    "message": str(exc),
                    "attempt": attempt,
                }
            ]
        return [
            {
                "error": getattr(diagnostics, "error_kind", "fast_trout_schema_error"),
                "message": getattr(diagnostics, "error_message", str(exc)),
                "schema_name": getattr(diagnostics, "schema_name", "OperatorTryoutCapsuleBatch"),
                "validation_errors": list(getattr(diagnostics, "validation_errors", []) or []),
                "raw_response_preview": getattr(diagnostics, "raw_response_preview", None),
                "raw_payload_preview": getattr(diagnostics, "raw_payload_preview", None),
                "attempt": attempt,
            }
        ]

    @staticmethod
    def _tryout_capsule_batch_to_plan(batch: OperatorTryoutCapsuleBatch) -> OperatorPlan:
        capsules = list(batch.capsules or [])[:4]
        return OperatorPlan(
            summary=batch.summary or "retired tryout capsule batch.",
            tasks=[capsule.task for capsule in capsules],
            actions=[capsule.action for capsule in capsules],
            dependencies=[
                dependency
                for capsule in capsules
                for dependency in list(capsule.dependencies or [])
            ],
            expected_outputs=[
                str(capsule.expected_output or "").strip()
                for capsule in capsules
                if str(capsule.expected_output or "").strip()
            ],
            assumptions=[
                "retired tryout compact capsule batch drafted before the full operator planner.",
                *[
                    str(capsule.reason or "").strip()
                    for capsule in capsules
                    if str(capsule.reason or "").strip()
                ],
            ],
            confidence=batch.confidence,
        )

    def try_fast_trout_capsules(
        self,
        user_request: UserRequest,
        *,
        execution_context: dict[str, Any] | None = None,
        observability: ObservabilityContext | None = None,
    ) -> OperatorPipelineResult | None:
        if not self.operator_fast_trout_enabled():
            return None

        feedback: list[dict[str, Any]] = []
        max_attempts = max(0, int(self._max_execution_repair_attempts() or 0))
        llm_call_count = 0
        for attempt_index in range(max_attempts + 1):
            attempt = attempt_index + 1
            llm_call_count += 1
            user_request.session_context["operator_fast_trout_llm_call_count"] = llm_call_count
            try:
                batch = self._complete_tryout_capsule_batch(
                    user_request,
                    feedback if feedback else None,
                )
            except (PydanticValidationError, StructuredCallError) as exc:
                errors = self._tryout_schema_error(exc, attempt=attempt)
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_TRYOUT_BLOCKED,
                    title="retired tryout capsule draft rejected",
                    summary="The compact tryout capsule batch did not match the typed schema.",
                    details={"attempt": attempt, "max_attempts": max_attempts, "errors": errors},
                )
                if attempt_index < max_attempts:
                    self._emit(
                        observability,
                        level="warning",
                        event_type=OPERATOR_TRYOUT_REPAIRING,
                        title="retired tryout capsule repair started",
                        summary="retired tryout is feeding schema evidence back to the LLM for a bounded capsule repair.",
                        details={"attempt": attempt, "max_attempts": max_attempts, "errors": errors},
                    )
                    feedback.append({"attempt": attempt, "errors": errors})
                    continue
                return None

            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_TRYOUT_CANDIDATE,
                title="retired tryout capsule candidate received",
                summary="The LLM drafted a compact operator-native capsule batch before full planning.",
                details={
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "capsule_count": len(batch.capsules),
                    "goal_complete_if_successful": batch.goal_complete_if_successful,
                    "batch": batch.model_dump(mode="json"),
                },
            )
            if not batch.capsules:
                errors = [
                    {
                        "error": "fast_trout_empty_capsule_batch",
                        "message": "retired tryout capsule batch contained no runnable capsules.",
                        "attempt": attempt,
                    }
                ]
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_TRYOUT_BLOCKED,
                    title="retired tryout capsule batch empty",
                    summary="The compact tryout draft did not include any action capsules.",
                    details={"attempt": attempt, "errors": errors},
                )
                if attempt_index < max_attempts:
                    self._emit(
                        observability,
                        level="warning",
                        event_type=OPERATOR_TRYOUT_REPAIRING,
                        title="retired tryout capsule repair started",
                        summary="retired tryout is asking the LLM for runnable capsules.",
                        details={"attempt": attempt, "max_attempts": max_attempts, "errors": errors},
                    )
                    feedback.append({"attempt": attempt, "errors": errors})
                    continue
                return None

            plan = self._tryout_capsule_batch_to_plan(batch)
            plan = self._normalize_plan_interactions(plan, observability)
            plan = self._normalize_plan_interactions(
                self._defer_dependent_python_actions(plan, observability),
                observability,
            )
            errors = self._validate_plan(user_request, plan, observability)
            if errors:
                accepted_plan = self._tryout_valid_runnable_plan(
                    user_request,
                    plan,
                    errors,
                    observability,
                )
            else:
                self._emit_tryout_validated_plan_accepted(
                    plan,
                    observability,
                    source="fast_trout_capsule",
                )
                accepted_plan = plan
            if accepted_plan is not None:
                context = dict(execution_context or {})
                context["operator_fast_trout_disable_execution_repair"] = True
                accepted_all_capsules = len(accepted_plan.actions) == len(plan.actions)
                goal_complete = bool(batch.goal_complete_if_successful and accepted_all_capsules)
                if not goal_complete:
                    context["operator_fast_trout_probe_only"] = True
                result = self.execute_approved(
                    user_request,
                    accepted_plan,
                    execution_context=context,
                    observability=observability,
                )
                metadata = dict(result.metadata)
                metadata.update(
                    {
                        "operator_fast_trout": True,
                        "operator_fast_trout_attempt": attempt,
                        "operator_fast_trout_goal_complete": goal_complete,
                        "operator_fast_trout_accepted_all_capsules": accepted_all_capsules,
                        "operator_fast_trout_next_step_hint": batch.next_step_hint,
                        "operator_fast_trout_capsule_count": len(accepted_plan.actions),
                        "operator_fast_trout_llm_call_count": llm_call_count,
                    }
                )
                result = result.model_copy(update={"metadata": metadata})
                if result.status == "error" and attempt_index < max_attempts:
                    execution_errors = [
                        {
                            "error": "fast_trout_execution_failed",
                            "message": record.error or record.stderr or "retired tryout action failed.",
                            "action_id": record.action_id,
                            "task_id": record.task_id,
                            "stdout": record.stdout,
                            "stderr": record.stderr,
                            "exit_code": record.exit_code,
                            "record": record.model_dump(mode="json"),
                        }
                        for record in result.execution_records
                        if record.status == "error"
                    ]
                    self._emit(
                        observability,
                        level="warning",
                        event_type=OPERATOR_TRYOUT_REPAIRING,
                        title="retired tryout capsule repair started",
                        summary="retired tryout is feeding execution failure evidence back to the LLM for a bounded capsule repair.",
                        details={
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "errors": execution_errors,
                        },
                    )
                    feedback.append({"attempt": attempt, "errors": execution_errors})
                    continue
                if result.status == "success" and goal_complete:
                    mutating_plan = self.plan_requires_confirmation(accepted_plan)
                    review: OperatorTryoutResultReview | None = None
                    review_errors: list[dict[str, Any]] = []
                    try:
                        llm_call_count += 1
                        user_request.session_context["operator_fast_trout_llm_call_count"] = llm_call_count
                        review = self._complete_tryout_result_review(
                            user_request,
                            accepted_plan,
                            list(result.execution_records),
                            observability,
                        )
                    except Exception as exc:
                        review_errors = self._tryout_result_review_error(
                            None,
                            exception=exc,
                            attempt=attempt,
                        )
                    else:
                        if review.decision != "complete":
                            review_errors = self._tryout_result_review_error(
                                review,
                                attempt=attempt,
                            )
                    if not review_errors:
                        accepted_metadata = dict(result.metadata)
                        accepted_metadata.update(
                            {
                                "operator_fast_trout_result_review_decision": "complete",
                                "operator_fast_trout_result_review": (
                                    review.model_dump(mode="json") if review is not None else {}
                                ),
                                "operator_fast_trout_llm_call_count": llm_call_count,
                            }
                        )
                        return result.model_copy(update={"metadata": accepted_metadata})
                    if not mutating_plan and attempt_index < max_attempts:
                        self._emit(
                            observability,
                            level="warning",
                            event_type=OPERATOR_TRYOUT_REPAIRING,
                            title="retired tryout capsule repair started",
                            summary=(
                                "retired tryout is feeding result-review evidence back to the LLM "
                                "for a bounded capsule repair."
                            ),
                            details={
                                "attempt": attempt,
                                "max_attempts": max_attempts,
                                "errors": review_errors,
                                "records": [
                                    record.model_dump(mode="json")
                                    for record in result.execution_records
                                ],
                            },
                        )
                        feedback.append(
                            {
                                "attempt": attempt,
                                "errors": review_errors,
                                "records": [
                                    record.model_dump(mode="json")
                                    for record in result.execution_records
                                ],
                            }
                        )
                        continue
                    return self._fast_trout_review_rejected_result(
                        result,
                        review_errors=review_errors,
                        attempt=attempt,
                        llm_call_count=llm_call_count,
                        mutating_plan=mutating_plan,
                    )
                return result

            if attempt_index < max_attempts:
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_TRYOUT_REPAIRING,
                    title="retired tryout capsule repair started",
                    summary="retired tryout is feeding validation evidence back to the LLM for a bounded capsule repair.",
                    details={"attempt": attempt, "max_attempts": max_attempts, "errors": errors},
                )
                feedback.append({"attempt": attempt, "errors": errors})
                continue
            return None
        return None
