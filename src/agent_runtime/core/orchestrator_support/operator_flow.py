"""Operator execution flow helpers."""

from __future__ import annotations

from .common import *
from .formatting import *


class _OperatorFlowMixin:
    """Operator execution flow helpers."""

    def _run_streaming_operator_loop(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        llm_client,
        observability: ObservabilityContext,
        trace: PlanningTrace,
        state: dict[str, Any],
    ) -> str:
        """Execute one decomposed operator task at a time."""

        raw_agent_mode = str(state.get("agent_mode") or request_context.get("agent_mode") or "standard_operator")
        agent_mode = "llm_operator" if raw_agent_mode == "llm_operator" else "standard_operator"
        state = self._ensure_streaming_step_ids(state)
        state["agent_mode"] = agent_mode
        trace.metadata["agent_mode"] = agent_mode
        trace.metadata["workflow_execution_mode"] = "streaming"
        tasks = list(state.get("tasks") or [])
        while int(state.get("current_index") or 0) < len(tasks):
            current_index = int(state.get("current_index") or 0)
            task = dict(tasks[current_index])
            step_id = str(task.get("streaming_step_id") or "").strip()
            flat_records = self._streaming_flat_record_payloads(state)
            seed_records = self._streaming_seed_record_payloads(state)
            step_context = dict(request_context)
            step_context["operator_streaming_current_task"] = dict(task)
            step_context["operator_streaming_current_index"] = current_index
            if step_id:
                step_context["operator_streaming_step_id"] = step_id
                step_context["operator_execution_step_id"] = step_id
            if seed_records:
                step_context["operator_seed_records"] = seed_records
            trace.metadata["operator_streaming_state"] = dict(state)
            trace.metadata["operator_execution_records"] = flat_records
            step_request = self._streaming_step_request(user_request, state, task)
            validation_feedback = self._streaming_step_validation_feedback_for_task(state, task)
            self._attach_step_validation_feedback_context(
                step_context=step_context,
                step_request=step_request,
                feedback=validation_feedback,
            )
            if self._streaming_task_should_lookup_online_ai(state, task):
                self._attach_streaming_online_ai_check(
                    state=state,
                    task=task,
                    step_request=step_request,
                    step_context=step_context,
                    observability=observability,
                )
            else:
                step_context.pop(ONLINE_AI_CHECK_CONTEXT_KEY, None)
                step_context.pop(ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY, None)
                step_request.session_context.pop(ONLINE_AI_CHECK_CONTEXT_KEY, None)
                step_request.session_context.pop(ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY, None)
                for scoped_context in (step_context, step_request.session_context):
                    summaries = scoped_context.get("operator_user_macro_summaries")
                    if isinstance(summaries, list):
                        scoped_context["operator_user_macro_summaries"] = [
                            item
                            for item in summaries
                            if not (
                                isinstance(item, dict)
                                and str(item.get("kind") or "").strip().lower()
                                == "checkonlineai"
                            )
                        ]
            if self._streaming_task_should_lookup_online(state, task):
                self._attach_streaming_online_lookup(
                    state=state,
                    task=task,
                    step_request=step_request,
                    step_context=step_context,
                    observability=observability,
                )
            else:
                step_context.pop(ONLINE_LOOKUP_CONTEXT_KEY, None)
                step_context.pop(ONLINE_LOOKUP_CONTEXTS_KEY, None)
                step_context.pop(ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY, None)
                step_request.session_context.pop(ONLINE_LOOKUP_CONTEXT_KEY, None)
                step_request.session_context.pop(ONLINE_LOOKUP_CONTEXTS_KEY, None)
                step_request.session_context.pop(ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY, None)
                for scoped_context in (step_context, step_request.session_context):
                    summaries = scoped_context.get("operator_user_macro_summaries")
                    if isinstance(summaries, list):
                        scoped_context["operator_user_macro_summaries"] = [
                            item
                            for item in summaries
                            if not (
                                isinstance(item, dict)
                                and str(item.get("kind") or "").strip().lower()
                                == "checkonline"
                            )
                        ]
            trace.metadata["operator_streaming_state"] = dict(state)
            pipeline = self._operator_pipeline(llm_client, step_context)
            start_details = {
                "workflow_execution_mode": "streaming",
                "agent_mode": agent_mode,
                "current_index": current_index,
                "step_count": len(tasks),
                "streaming_step_id": step_id,
                "task": task,
            }
            durable_task_id = str(request_context.get("durable_task_id") or "").strip()
            durable_task_attempt_id = str(
                request_context.get("durable_task_attempt_id") or ""
            ).strip()
            if durable_task_id:
                start_details["durable_task_id"] = durable_task_id
            if durable_task_attempt_id:
                start_details["durable_task_attempt_id"] = durable_task_attempt_id
            observability.stage_started(
                OPERATOR_STAGE,
                "Streaming operator step started",
                "The runtime is planning one decomposed task as a scoped operator request.",
                details=start_details,
            )
            clarification_response = self._maybe_pause_for_operator_clarification(
                user_request=step_request,
                request_context=request_context,
                pipeline=pipeline,
                observability=observability,
                trace=trace,
                agent_mode=agent_mode,
            )
            if clarification_response is not None:
                trace.metadata["operator_streaming_state"] = dict(state)
                return clarification_response
            try:
                plan, _ = pipeline.propose_and_validate(step_request, observability)
            except OperatorValidationError as exc:
                trace.metadata["operator_validation_errors"] = list(exc.errors)
                message = "Streaming operator step failed validation after configured repair attempts."
                state = self._streaming_state_with_validation_failure(
                    state,
                    task=task,
                    errors=list(exc.errors),
                    message=message,
                )
                trace.metadata["operator_streaming_state"] = dict(state)
                trace.metadata["operator_execution_records"] = self._streaming_flat_record_payloads(
                    state
                )
                details_for_user = _validation_errors_for_user(list(exc.errors))
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="runtime_error",
                    stage=OPERATOR_STAGE,
                    reason=message,
                    metadata={"validation_errors": list(exc.errors), "streaming_task": task},
                )
                observability.stage_failed(
                    OPERATOR_STAGE,
                    "Streaming operator validation failed",
                    message,
                    details={"validation_errors": list(exc.errors), "streaming_task": task},
                )
                self._finalize_profile(user_request, final_status="error", failed_stage=OPERATOR_STAGE)
                return (
                    f"I couldn't complete that request safely at the {OPERATOR_STAGE} stage. {message}"
                    + (f"\n\n{details_for_user}" if details_for_user else "")
                )

            trace.metadata["operator_plan"] = plan.model_dump(mode="json")
            trace.metadata["operator_streaming_state"] = dict(state)
            lrdirect_bypass_confirmation = bool(
                step_request.session_context.get("operator_lrdirect_bypass_confirmation")
                or dict(step_request.session_context.get("operator_lrdirect_applied") or {}).get("bypassed_approval")
            )
            if (
                pipeline.plan_requires_confirmation(plan)
                and not pipeline.operator_tryout_enabled()
                and not lrdirect_bypass_confirmation
            ):
                result = pipeline.require_confirmation(step_request, plan, observability)
                trace.metadata["operator_confirmation_actions"] = list(result.confirmation_actions)
                trace.metadata["operator_display_document"] = result.display_document
                trace.metadata["operator_streaming_state"] = dict(state)
                self.last_display_document = result.display_document
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="confirmation_required",
                    stage=OPERATOR_STAGE,
                    reason="Streaming operator step requires approval before execution.",
                    metadata={
                        "confirmation_actions": list(result.confirmation_actions),
                        "streaming_task": task,
                    },
                )
                observability.stage_completed(
                    OPERATOR_STAGE,
                    "Streaming operator step paused",
                    "The current decomposed task is waiting for approval.",
                    details={"action_count": len(result.confirmation_actions), "streaming_task": task},
                )
                observability.stage_completed(
                    STAGE_COMPLETED,
                    "Request paused for approval",
                    "The streaming operator step is waiting for user approval.",
                    details={"final_status": "confirmation_required", "agent_mode": agent_mode},
                )
                self._finalize_profile(user_request, final_status="confirmation_required")
                return result.final_response

            try:
                result = pipeline.execute_approved(
                    step_request,
                    plan,
                    execution_context=step_context,
                    observability=observability,
                )
            except OperatorValidationError as exc:
                trace.metadata["operator_validation_errors"] = list(exc.errors)
                message = "Streaming operator execution validation failed."
                state = self._streaming_state_with_validation_failure(
                    state,
                    task=task,
                    errors=list(exc.errors),
                    message=message,
                )
                trace.metadata["operator_streaming_state"] = dict(state)
                trace.metadata["operator_execution_records"] = self._streaming_flat_record_payloads(
                    state
                )
                details_for_user = _validation_errors_for_user(list(exc.errors))
                self._finalize_profile(user_request, final_status="error", failed_stage=OPERATOR_STAGE)
                return (
                    f"I couldn't complete that request safely at the {OPERATOR_STAGE} stage. "
                    f"{message}"
                    + (f"\n\n{details_for_user}" if details_for_user else "")
                )

            if result.confirmation_required or getattr(result, "clarification_required", False):
                trace.metadata["operator_streaming_state"] = dict(state)
                return self._finish_operator_execution_result(
                    step_request,
                    trace,
                    observability,
                    result,
                )
            if result.status != "success" and result.metadata.get("operator_step_validation_checked"):
                validation_decision = str(
                    result.metadata.get("operator_step_validation_decision") or ""
                )
                task_id = str(task.get("task_id") or "").strip()
                attempts_by_task = dict(state.get("step_validation_attempts_by_task") or {})
                validation_attempts = int(attempts_by_task.get(task_id) or 0)
                if (
                    validation_decision in {"repair", "continue_with_more_evidence"}
                    and validation_attempts < 1
                ):
                    state = self._streaming_state_with_step_validation_feedback(
                        state,
                        task=task,
                        result=result,
                    )
                    trace.metadata["operator_streaming_state"] = dict(state)
                    observability.warning(
                        OPERATOR_STAGE,
                        OPERATOR_STEP_VALIDATION_REJECTED,
                        "Streaming step validation retry requested",
                        (
                            "The LLM step judge rejected the evidence, so the runtime will "
                            "re-plan the same decomposed step once with the validation feedback."
                        ),
                        details={
                            "task_id": task_id,
                            "decision": validation_decision,
                            "attempt": validation_attempts + 1,
                            "feedback": result.metadata.get("operator_step_validation_feedback"),
                        },
                    )
                    continue
                trace.metadata["operator_streaming_state"] = dict(state)
                return self._finish_operator_execution_result(
                    step_request,
                    trace,
                    observability,
                    result,
                )
            if result.status != "success":
                state = self._streaming_state_after_step(state, task=task, result=result)
                trace.metadata["operator_streaming_state"] = dict(state)
                trace.metadata["operator_execution_records"] = self._streaming_flat_record_payloads(
                    state
                )
                failed_action_ids = [
                    record.action_id
                    for record in result.execution_records
                    if record.status == "error"
                ]
                observability.stage_failed(
                    OPERATOR_STAGE,
                    "Streaming operator step failed",
                    "The current decomposed task finished with one or more execution errors.",
                    details={
                        "current_index": current_index,
                        "task": task,
                        "status": result.status,
                        "record_count": len(result.execution_records),
                        "failed_action_ids": failed_action_ids,
                    },
                )
                return self._finish_operator_execution_result(
                    step_request,
                    trace,
                    observability,
                    result,
                )
            if not result.metadata.get("operator_step_validation_checked"):
                result = self._run_streaming_step_validation(
                    user_request=user_request,
                    step_request=step_request,
                    state=state,
                    task=task,
                    plan=plan,
                    result=result,
                    context=step_context,
                    observability=observability,
                    trace=trace,
                )
            if result.status != "success":
                validation_decision = str(
                    result.metadata.get("operator_step_validation_decision") or ""
                )
                task_id = str(task.get("task_id") or "").strip()
                attempts_by_task = dict(state.get("step_validation_attempts_by_task") or {})
                validation_attempts = int(attempts_by_task.get(task_id) or 0)
                if (
                    validation_decision in {"repair", "continue_with_more_evidence"}
                    and validation_attempts < 1
                ):
                    state = self._streaming_state_with_step_validation_feedback(
                        state,
                        task=task,
                        result=result,
                    )
                    trace.metadata["operator_streaming_state"] = dict(state)
                    observability.warning(
                        OPERATOR_STAGE,
                        OPERATOR_STEP_VALIDATION_REJECTED,
                        "Streaming step validation retry requested",
                        (
                            "The LLM step judge rejected the evidence, so the runtime will "
                            "re-plan the same decomposed step once with the validation feedback."
                        ),
                        details={
                            "task_id": task_id,
                            "decision": validation_decision,
                            "attempt": validation_attempts + 1,
                            "feedback": result.metadata.get("operator_step_validation_feedback"),
                        },
                    )
                    continue
                trace.metadata["operator_streaming_state"] = dict(state)
                return self._finish_operator_execution_result(
                    step_request,
                    trace,
                    observability,
                    result,
                )
            state = self._streaming_state_after_step(state, task=task, result=result)
            trace.metadata["operator_streaming_state"] = dict(state)
            trace.metadata["operator_execution_records"] = self._streaming_flat_record_payloads(state)
            observability.stage_completed(
                OPERATOR_STAGE,
                "Streaming operator step completed",
                "The current decomposed task completed; the next task will be planned with its output.",
                details={
                    "current_index": current_index,
                    "next_index": int(state.get("current_index") or 0),
                    "record_count": len(result.execution_records),
                },
            )

        markdown = self._streaming_completion_markdown(state)
        trace.metadata["operator_streaming_state"] = dict(state)
        trace.metadata["operator_execution_records"] = self._streaming_flat_record_payloads(state)
        self.last_failure_summary = None
        observability.stage_completed(
            STAGE_COMPLETED,
            "Streaming request completed",
            "All decomposed streaming operator steps completed.",
            details={"final_status": "success", "step_count": len(tasks)},
        )
        self._finalize_profile(user_request, final_status="success")
        return markdown

    def _handle_streaming_operator_request(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        llm_client,
        observability: ObservabilityContext,
        trace: PlanningTrace,
        *,
        tasks: list[TaskFrame],
        global_constraints: dict[str, Any],
        classification_context: dict[str, Any],
        execution_shape: dict[str, Any] | None = None,
    ) -> str:
        raw_agent_mode = str(request_context.get("agent_mode") or "standard_operator")
        agent_mode = "llm_operator" if raw_agent_mode == "llm_operator" else "standard_operator"
        online_ai_requested = bool(
            online_ai_check_requested_from_context(request_context)
            or online_ai_check_requested_from_context(user_request.session_context)
        )
        if online_ai_requested:
            tasks = self._strip_pure_online_ai_lookup_tasks(list(tasks))
        state = self._new_streaming_state(
            original_prompt=user_request.raw_prompt,
            tasks=tasks,
            global_constraints=global_constraints,
            classification_context=classification_context,
            execution_shape=execution_shape,
            agent_mode=agent_mode,
        )
        online_mode_enabled = False
        if online_mode_enabled:
            state["online_lookup_requested"] = True
            state["online_lookup_mode_enabled"] = True
            state["online_lookup_task_ids"] = []
        elif online_lookup_requested_from_context(request_context) or online_lookup_requested_from_context(
            user_request.session_context
        ):
            state["online_lookup_requested"] = True
            state["online_lookup_task_ids"] = []
        if online_ai_requested:
            state["online_ai_check_requested"] = True
            state["online_ai_check_task_ids"] = []
            explicit_ai_query = self._explicit_online_ai_query_from_context(
                request_context,
                user_request.session_context,
            )
            if explicit_ai_query:
                state["online_ai_check_query"] = explicit_ai_query
        trace.metadata["operator_streaming_state"] = dict(state)
        observability.stage_started(
            OPERATOR_STAGE,
            "Streaming operator started",
            "The runtime is executing decomposed tasks one operator step at a time.",
            details={"task_count": len(tasks), "agent_mode": agent_mode},
        )
        return self._run_streaming_operator_loop(
            user_request,
            request_context,
            llm_client,
            observability,
            trace,
            state,
        )

    @staticmethod
    def _operator_failure_continuation_metadata(
        *,
        agent_mode: str,
        plan: OperatorPlan | None,
        records: list[OperatorExecutionRecord],
        conversation_id: str | None = None,
        parent_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Return compact metadata for a failed operator run that can be continued."""

        if plan is None:
            return {"resumable": False}
        failed = [record for record in records if record.status == "error"]
        successful = [record for record in records if record.status == "success"]
        if not failed or not successful:
            return {"resumable": False}
        first_failed = failed[0]
        return {
            "resumable": True,
            "agent_mode": agent_mode,
            "conversation_id": conversation_id or "",
            "parent_request_id": parent_request_id or "",
            "successful_action_ids": [record.action_id for record in successful],
            "failed_action_id": first_failed.action_id,
            "failed_action_kind": first_failed.kind,
            "failed_exit_code": first_failed.exit_code,
            "failed_error": first_failed.error or first_failed.stderr or first_failed.stdout,
            "record_count": len(records),
        }

    def _finish_operator_execution_result(
        self,
        user_request: UserRequest,
        trace: PlanningTrace,
        observability: ObservabilityContext,
        result: Any,
    ) -> str:
        """Record an already-executed Conversational result and return the final response."""

        agent_mode = str(trace.metadata.get("agent_mode") or "llm_operator")
        mode_label = "Agent operator" if agent_mode == "standard_operator" else "Conversational"
        operator_self_brief = dict(user_request.session_context or {}).get("operator_self_brief")
        if isinstance(operator_self_brief, dict):
            trace.metadata["operator_self_brief"] = dict(operator_self_brief)
        if result.plan is not None:
            trace.metadata["operator_plan"] = result.plan.model_dump(mode="json")
        trace.metadata["operator_execution_records"] = [
            record.model_dump(mode="json") for record in result.execution_records
        ]
        trace.metadata["operator_display_document"] = result.display_document
        self.last_display_document = result.display_document

        if getattr(result, "clarification_required", False) and result.clarification_request is not None:
            result.clarification_request = self._enrich_parameter_clarification_request(
                user_request,
                result.clarification_request,
            )
            clarification_payload = result.clarification_request.model_dump(mode="json")
            trace.metadata["operator_clarification_request"] = clarification_payload
            trace.metadata["operator_clarification_pending"] = True
            trace.metadata["operator_clarification_phase"] = str(
                result.metadata.get("clarification_phase") or "operator_loop"
            )
            if result.plan is not None:
                trace.metadata["operator_plan"] = result.plan.model_dump(mode="json")
            self._record_last_failure(
                request_id=user_request.request_id,
                prompt=user_request.raw_prompt,
                category="clarification_required",
                stage=OPERATOR_STAGE,
                reason="The operator pipeline needs one user clarification before continuing.",
                metadata={"clarification_request": clarification_payload},
            )
            observability.stage_completed(
                OPERATOR_STAGE,
                f"{mode_label} execution paused",
                "The operator pipeline is waiting for a user clarification.",
                details={"status": "clarification_required", "agent_mode": agent_mode},
            )
            observability.stage_completed(
                STAGE_COMPLETED,
                "Request paused for clarification",
                "The request is waiting for user input before planning or continuing.",
                details={"final_status": "clarification_required", "agent_mode": agent_mode},
            )
            self._finalize_profile(user_request, final_status="clarification_required")
            return result.final_response

        if result.confirmation_required and result.plan is not None:
            trace.metadata["operator_plan"] = result.plan.model_dump(mode="json")
            trace.metadata["operator_confirmation_actions"] = list(result.confirmation_actions)
            partial_results_pending = bool(result.metadata.get("partial_results_pending"))
            deferred_python_pending = bool(
                result.metadata.get("deferred_python_code_confirmation_pending")
            )
            completion_review_pending = bool(result.metadata.get("completion_review_pending"))
            shell_bindings_pending = bool(
                result.metadata.get("shell_input_bindings_confirmation_pending")
            )
            execution_repair_pending = bool(result.metadata.get("operator_execution_repair_pending"))
            failure_continuation_pending = bool(
                result.metadata.get("operator_failure_continuation_pending")
            )
            if result.metadata.get("partial_results_pending"):
                trace.metadata["operator_partial_results_pending"] = True
                trace.metadata["operator_partial_results"] = dict(result.metadata)
            if deferred_python_pending:
                trace.metadata["operator_deferred_python_code_confirmation_pending"] = True
                trace.metadata["operator_deferred_python_code_confirmation"] = dict(result.metadata)
            if completion_review_pending:
                trace.metadata["operator_completion_review_pending"] = True
                trace.metadata["operator_completion_review"] = dict(result.metadata)
            if shell_bindings_pending:
                trace.metadata["operator_shell_input_bindings_confirmation_pending"] = True
                trace.metadata["operator_shell_input_bindings_confirmation"] = dict(result.metadata)
                trace.metadata["operator_shell_input_bindings_action_id"] = str(
                    result.metadata.get("shell_input_bindings_action_id") or ""
                )
            if execution_repair_pending:
                trace.metadata["operator_execution_repair_pending"] = True
                trace.metadata["operator_execution_repair"] = dict(result.metadata)
            if failure_continuation_pending:
                trace.metadata["operator_failure_continuation_pending"] = True
            trace.metadata["operator_clarification_pending"] = False
            trace.metadata.pop("operator_clarification_phase", None)
            self._record_last_failure(
                request_id=user_request.request_id,
                prompt=user_request.raw_prompt,
                category="confirmation_required",
                stage=OPERATOR_STAGE,
                reason=(
                    "Partial command output requires approval before downstream actions consume it."
                    if partial_results_pending
                    else "Generated Python code requires approval before execution."
                    if deferred_python_pending
                    else "A shell action with bound upstream outputs requires approval before execution."
                    if shell_bindings_pending
                    else "Execution repair actions are waiting for approval before retrying unfinished work."
                    if execution_repair_pending
                    else "Continuation actions are waiting for approval before retrying the failed run."
                    if failure_continuation_pending
                    else "Additional actions are required to finish the requested end state."
                    if completion_review_pending
                    else "Conversational repair actions require approval before execution."
                ),
                metadata={
                    "confirmation_actions": list(result.confirmation_actions),
                    **({"partial_results": dict(result.metadata)} if partial_results_pending else {}),
                    **(
                        {"deferred_python_code_confirmation": dict(result.metadata)}
                        if deferred_python_pending
                        else {}
                    ),
                    **(
                        {"completion_review": dict(result.metadata)}
                        if completion_review_pending
                        else {}
                    ),
                    **(
                        {"shell_input_bindings_confirmation": dict(result.metadata)}
                        if shell_bindings_pending
                        else {}
                    ),
                    **(
                        {"operator_execution_repair": dict(result.metadata)}
                        if execution_repair_pending
                        else {}
                    ),
                    **(
                        {"operator_failure_continuation": dict(result.metadata)}
                        if failure_continuation_pending
                        else {}
                    ),
                },
            )
            pause_summary = (
                f"The {mode_label} execution has partial command output waiting for user approval."
                if partial_results_pending
                else f"The {mode_label} execution generated Python code that is waiting for approval."
                if deferred_python_pending
                else f"The {mode_label} execution has bound shell values waiting for approval."
                if shell_bindings_pending
                else f"The {mode_label} execution repair needs approval before retrying unfinished work."
                if execution_repair_pending
                else f"The {mode_label} continuation needs approval before retrying failed work."
                if failure_continuation_pending
                else f"The {mode_label} execution needs additional approved actions to complete the requested end state."
                if completion_review_pending
                else f"The {mode_label} execution produced a repair plan that is waiting for approval."
            )
            observability.stage_completed(
                OPERATOR_STAGE,
                f"{mode_label} execution paused",
                pause_summary,
                details={"status": result.status, "action_count": len(result.confirmation_actions)},
            )
            observability.stage_completed(
                STAGE_COMPLETED,
                "Request paused for approval",
                (
                    "Partial command output is waiting for user approval."
                    if partial_results_pending
                    else "Generated Python code is waiting for user approval."
                    if deferred_python_pending
                else "Bound shell values are waiting for user approval."
                if shell_bindings_pending
                else "Execution repair actions are waiting for user approval."
                if execution_repair_pending
                else "Continuation actions are waiting for user approval."
                if failure_continuation_pending
                    else "Completion continuation actions are waiting for user approval."
                    if completion_review_pending
                    else f"The {mode_label} repair plan is waiting for user approval."
                ),
                details={"final_status": "confirmation_required", "agent_mode": agent_mode},
            )
            self._finalize_profile(user_request, final_status="confirmation_required")
            return result.final_response

        continuation = self._operator_failure_continuation_metadata(
            agent_mode=agent_mode,
            plan=result.plan,
            records=list(result.execution_records),
            conversation_id=str(user_request.session_context.get("conversation_id") or ""),
            parent_request_id=str(user_request.session_context.get("parent_request_id") or ""),
        )
        if continuation.get("resumable"):
            trace.metadata["operator_failure_continuation"] = continuation
            self.last_failure_summary = {
                "request_id": user_request.request_id,
                "prompt": user_request.raw_prompt,
                "category": "runtime_error",
                "stage": OPERATOR_STAGE,
                "reason": f"{mode_label} execution partially failed.",
                "metadata": {"operator_failure_continuation": continuation},
            }
            observability.warning(
                OPERATOR_STAGE,
                "operator.continuation.available",
                "Failure continuation available",
                "The operator run failed after completing earlier actions and can be continued.",
                details=continuation,
            )
        elif result.status == "success":
            self.last_failure_summary = None
        else:
            trace.metadata["operator_failure_continuation"] = continuation
            self.last_failure_summary = {
                "request_id": user_request.request_id,
                "prompt": user_request.raw_prompt,
                "category": "runtime_error",
                "stage": OPERATOR_STAGE,
                "reason": f"{mode_label} execution failed.",
                "metadata": {"operator_failure_continuation": continuation},
            }
        trace.metadata["operator_clarification_pending"] = False
        trace.metadata.pop("operator_clarification_phase", None)
        observability.stage_completed(
            OPERATOR_STAGE,
            f"{mode_label} execution completed",
            f"The {mode_label} plan finished execution.",
            details={"status": result.status, "record_count": len(result.execution_records)},
        )
        observability.stage_completed(
            STAGE_COMPLETED,
            "Request completed",
            f"The {mode_label} request finished execution.",
            details={"final_status": result.status, "agent_mode": agent_mode},
        )
        self._finalize_profile(
            user_request,
            final_status="success" if result.status == "success" else "error",
            failed_stage=None if result.status == "success" else OPERATOR_STAGE,
        )
        return result.final_response

    @staticmethod
    def _clarification_round_count(context: dict[str, Any] | None) -> int:
        clarifications = dict(context or {}).get("clarifications")
        return len(clarifications) if isinstance(clarifications, list) else 0

    def _maybe_pause_for_operator_clarification(
        self,
        *,
        user_request: UserRequest,
        request_context: dict[str, Any],
        pipeline: LLMOperatorPipeline,
        observability: ObservabilityContext,
        trace: PlanningTrace,
        agent_mode: str,
        conversation_context: dict[str, Any] | None = None,
        failure_context: dict[str, Any] | None = None,
    ) -> str | None:
        """Run the request-scoped LLM clarification gate and return final text when paused."""

        max_rounds = int(self._runtime_config_for_context(request_context).llm_operator_max_clarification_rounds)
        if max_rounds <= 0:
            return None
        if self._clarification_round_count(user_request.session_context) >= max_rounds:
            return self._safe_failure(
                user_request,
                OPERATOR_STAGE,
                f"Maximum clarification rounds reached ({max_rounds}).",
            )
        try:
            pipeline._attach_memory_before_clarification(user_request, observability)
        except Exception as exc:
            trace.metadata["operator_pre_clarification_memory_error"] = str(exc)
            observability.warning(
                OPERATOR_STAGE,
                "operator.pre_clarification_memory.failed",
                "Pre-clarification memory check skipped",
                "The runtime could not refresh memory before the clarification gate, so it will continue with existing context.",
                details={"error": str(exc)},
            )
        try:
            decision = pipeline.complete_clarification_decision(
                user_request,
                conversation_context=conversation_context,
                failure_context=failure_context,
                observability=observability,
            )
        except Exception as exc:
            trace.metadata["operator_clarification_error"] = str(exc)
            observability.warning(
                OPERATOR_STAGE,
                "operator.clarification.failed",
                "Operator clarification gate skipped",
                "The LLM clarification gate failed, so planning will continue normally.",
                details={"error": str(exc)},
            )
            return None
        trace.metadata["operator_clarification_decision"] = decision.model_dump(mode="json")
        if decision.decision != "ask_user" or decision.clarification_request is None:
            return None
        decision.clarification_request = self._enrich_parameter_clarification_request(
            user_request,
            decision.clarification_request,
        )
        result = pipeline.require_clarification(
            user_request,
            decision.clarification_request,
            observability,
        )
        trace.metadata["agent_mode"] = agent_mode
        trace.metadata["operator_clarification_request"] = (
            decision.clarification_request.model_dump(mode="json")
        )
        trace.metadata["operator_clarification_pending"] = True
        trace.metadata["operator_clarification_phase"] = "pre_planning"
        trace.metadata["operator_display_document"] = result.display_document
        self.last_display_document = result.display_document
        self._record_last_failure(
            request_id=user_request.request_id,
            prompt=user_request.raw_prompt,
            category="clarification_required",
            stage=OPERATOR_STAGE,
            reason="The operator pipeline needs one user clarification before planning.",
            metadata={"clarification_request": trace.metadata["operator_clarification_request"]},
        )
        observability.stage_completed(
            OPERATOR_STAGE,
            "Operator clarification required",
            "The request is waiting for one user answer before planning.",
            details={"agent_mode": agent_mode},
        )
        observability.stage_completed(
            STAGE_COMPLETED,
            "Request paused for clarification",
            "The request is waiting for user input before planning.",
            details={"final_status": "clarification_required", "agent_mode": agent_mode},
        )
        self._finalize_profile(user_request, final_status="clarification_required")
        return result.final_response

    def _seed_fast_trout_evidence(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        trace: PlanningTrace,
        observability: ObservabilityContext,
        result: OperatorPipelineResult,
    ) -> None:
        fast_trout_records = [
            record.model_dump(mode="json")
            for record in result.execution_records
        ]
        next_step_hint = str(result.metadata.get("operator_fast_trout_next_step_hint") or "")
        user_request.session_context["operator_fast_trout_records"] = fast_trout_records
        user_request.session_context["operator_fast_trout_next_step_hint"] = next_step_hint
        request_context["operator_fast_trout_records"] = fast_trout_records
        request_context["operator_fast_trout_next_step_hint"] = next_step_hint
        trace.metadata["operator_fast_trout_seed_records"] = fast_trout_records
        if result.metadata.get("operator_fast_trout_fallback_reason"):
            trace.metadata["operator_fast_trout_fallback_reason"] = str(
                result.metadata.get("operator_fast_trout_fallback_reason") or ""
            )
        if result.metadata.get("operator_fast_trout_result_review_errors"):
            review_errors = list(result.metadata.get("operator_fast_trout_result_review_errors") or [])
            user_request.session_context["operator_fast_trout_result_review_errors"] = review_errors
            request_context["operator_fast_trout_result_review_errors"] = review_errors
            trace.metadata["operator_fast_trout_result_review_errors"] = review_errors
        observability.info(
            OPERATOR_STAGE,
            OPERATOR_TRYOUT_COMPLETED,
            "retired tryout capsule pass seeded planner",
            "The compact capsule pass produced evidence; the normal operator planner will continue.",
            details={
                "record_count": len(fast_trout_records),
                "next_step_hint": next_step_hint,
            },
        )

    def _try_fast_trout_preflight(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        llm_client,
        observability: ObservabilityContext,
        trace: PlanningTrace,
        *,
        agent_mode: str,
        intent_block: dict[str, Any] | None = None,
    ) -> str | None:
        """retired tryout preflight is retired; decomposition remains in control."""

        del user_request, request_context, llm_client, observability, trace, agent_mode, intent_block
        return None

    def _handle_standard_operator_request(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        llm_client,
        observability: ObservabilityContext,
        trace: PlanningTrace,
        *,
        intent_block: dict[str, Any],
    ) -> str:
        """Run standard Agent ordinary work through the shared operator-native planner."""

        operator_request = user_request.model_copy(
            update={
                "session_context": dict(user_request.session_context),
                "safety_context": dict(user_request.safety_context),
            }
        )
        operator_request.session_context["operator_intent_block"] = dict(intent_block)
        agent_display_name = _agent_display_name_from_context(operator_request.session_context)
        operator_request.session_context["operator_mode_label"] = agent_display_name
        operator_request.session_context["operator_display_label"] = agent_display_name
        operator_request.safety_context["operator_intent_block"] = dict(intent_block)
        trace.metadata["agent_mode"] = "standard_operator"
        trace.metadata["operator_intent_block"] = dict(intent_block)
        observability.stage_started(
            OPERATOR_STAGE,
            "Agent operator planning started",
            "The standard Agent pipeline is lowering ordinary local work through the operator-native planner.",
            details={"agent_mode": "standard_operator", "task_count": len(intent_block.get("tasks") or [])},
        )
        pipeline = self._operator_pipeline(llm_client, request_context)
        clarification_response = self._maybe_pause_for_operator_clarification(
            user_request=operator_request,
            request_context=request_context,
            pipeline=pipeline,
            observability=observability,
            trace=trace,
            agent_mode="standard_operator",
        )
        if clarification_response is not None:
            return clarification_response
        try:
            plan, _ = pipeline.propose_and_validate(operator_request, observability)
        except OperatorValidationError as exc:
            trace.metadata["operator_validation_errors"] = list(exc.errors)
            message = "Agent operator plan failed validation after configured repair attempts."
            details_for_user = _validation_errors_for_user(list(exc.errors))
            self._record_last_failure(
                request_id=user_request.request_id,
                prompt=user_request.raw_prompt,
                category="runtime_error",
                stage=OPERATOR_STAGE,
                reason=message,
                metadata={"validation_errors": list(exc.errors)},
            )
            observability.stage_failed(
                OPERATOR_STAGE,
                "Agent operator validation failed",
                message,
                details={"validation_errors": list(exc.errors)},
            )
            self._finalize_profile(user_request, final_status="error", failed_stage=OPERATOR_STAGE)
            return (
                f"I couldn't complete that request safely at the {OPERATOR_STAGE} stage. {message}"
                + (f"\n\n{details_for_user}" if details_for_user else "")
            )

        trace.metadata["operator_plan"] = plan.model_dump(mode="json")
        operator_self_brief = dict(operator_request.session_context or {}).get("operator_self_brief")
        if isinstance(operator_self_brief, dict):
            trace.metadata["operator_self_brief"] = dict(operator_self_brief)
        if not pipeline.plan_requires_confirmation(plan) or pipeline.operator_tryout_enabled():
            try:
                result = pipeline.execute_approved(
                    operator_request,
                    plan,
                    execution_context=self._execution_context_with_agent_parameters(user_request, request_context),
                    observability=observability,
                )
            except OperatorValidationError as exc:
                trace.metadata["operator_validation_errors"] = list(exc.errors)
                details_for_user = _validation_errors_for_user(list(exc.errors))
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="runtime_error",
                    stage=OPERATOR_STAGE,
                    reason="Agent operator execution validation failed.",
                    metadata={"validation_errors": list(exc.errors)},
                )
                observability.stage_failed(
                    OPERATOR_STAGE,
                    "Agent operator execution validation failed",
                    "The read-only Agent operator plan failed validation before execution.",
                    details={"validation_errors": list(exc.errors)},
                )
                self._finalize_profile(user_request, final_status="error", failed_stage=OPERATOR_STAGE)
                return (
                    f"I couldn't complete that request safely at the {OPERATOR_STAGE} stage. "
                    "Agent operator execution validation failed."
                    + (f"\n\n{details_for_user}" if details_for_user else "")
                )
            return self._finish_operator_execution_result(
                operator_request,
                trace,
                observability,
                result,
            )

        result = pipeline.require_confirmation(operator_request, plan, observability)
        trace.metadata["operator_confirmation_actions"] = list(result.confirmation_actions)
        trace.metadata["operator_display_document"] = result.display_document
        self.last_display_document = result.display_document
        self._record_last_failure(
            request_id=user_request.request_id,
            prompt=user_request.raw_prompt,
            category="confirmation_required",
            stage=OPERATOR_STAGE,
            reason="Agent operator actions require approval before execution.",
            metadata={"confirmation_actions": list(result.confirmation_actions)},
        )
        observability.stage_completed(
            OPERATOR_STAGE,
            "Agent operator planning completed",
            "The Agent operator plan passed validation and is waiting for approval.",
            details={"action_count": len(result.confirmation_actions)},
        )
        observability.stage_completed(
            STAGE_COMPLETED,
            "Request paused for approval",
            "The Agent operator plan is waiting for user approval.",
            details={"final_status": "confirmation_required", "agent_mode": "standard_operator"},
        )
        self._finalize_profile(user_request, final_status="confirmation_required")
        return result.final_response

    def _handle_operator_request(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        llm_client,
        observability: ObservabilityContext,
        trace: PlanningTrace,
    ) -> str:
        """Run the explicit Conversational planning path and pause for approval."""

        agent_display_name = _agent_display_name_from_context(user_request.session_context)
        user_request.session_context.setdefault("operator_mode_label", agent_display_name)
        user_request.session_context.setdefault("operator_display_label", agent_display_name)
        self._attach_execution_shape_hint(
            user_request,
            request_context,
            trace,
            observability,
            classification_context={
                "original_prompt": user_request.raw_prompt,
                "prompt_type": "operator_request",
                "requires_tools": True,
                "likely_domains": ["operator"],
                "risk_level": "low",
            },
        )
        observability.stage_started(
            OPERATOR_STAGE,
            "Conversational planning started",
            "The runtime is asking the LLM to author an operator plan.",
            details={"agent_mode": "llm_operator"},
        )
        self._attach_online_ai_direct_lookup(user_request, request_context, observability)
        self._attach_online_mode_direct_lookup(user_request, request_context, observability)
        pipeline = self._operator_pipeline(llm_client, request_context)
        conversation_context = request_context.get("operator_conversation_context")
        if isinstance(conversation_context, dict):
            summary = {
                "conversation_id": conversation_context.get("conversation_id"),
                "turn_count": len(conversation_context.get("turns") or []),
                "truncated": bool(conversation_context.get("truncated", False)),
                "full_payloads_included": bool(conversation_context.get("full_payloads_included", False)),
                "dropped_turn_count": conversation_context.get("dropped_turn_count", 0),
            }
            trace.metadata["operator_conversation_context"] = summary
            observability.info(
                OPERATOR_STAGE,
                OPERATOR_CONVERSATION_CONTEXT_BUILT,
                "Operator conversation context built",
                "The runtime received prior operator turns for this follow-up.",
                details=summary,
            )
            if summary["truncated"]:
                observability.warning(
                    OPERATOR_STAGE,
                    OPERATOR_CONVERSATION_CONTEXT_TRUNCATED,
                    "Operator conversation context truncated",
                    "The follow-up context was reduced to fit the configured context budget.",
                    details=summary,
                )
            clarification_response = self._maybe_pause_for_operator_clarification(
                user_request=user_request,
                request_context=request_context,
                pipeline=pipeline,
                observability=observability,
                trace=trace,
                agent_mode="llm_operator",
                conversation_context=conversation_context,
            )
            if clarification_response is not None:
                return clarification_response
            try:
                decision = pipeline.complete_followup_decision(
                    user_request,
                    conversation_context,
                    observability,
                )
            except OperatorValidationError as exc:
                trace.metadata["operator_validation_errors"] = list(exc.errors)
                message = "Conversational follow-up failed validation after configured repair attempts."
                details_for_user = _validation_errors_for_user(list(exc.errors))
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="runtime_error",
                    stage=OPERATOR_STAGE,
                    reason=message,
                    metadata={"validation_errors": list(exc.errors)},
                )
                observability.stage_failed(
                    OPERATOR_STAGE,
                    "Conversational follow-up validation failed",
                    message,
                    details={"validation_errors": list(exc.errors)},
                )
                self._finalize_profile(user_request, final_status="error", failed_stage=OPERATOR_STAGE)
                return (
                    f"I couldn't complete that request safely at the {OPERATOR_STAGE} stage. {message}"
                    + (f"\n\n{details_for_user}" if details_for_user else "")
                )

            trace.metadata["agent_mode"] = "llm_operator"
            trace.metadata["operator_conversation_followup"] = True
            trace.metadata["operator_conversation_followup_decision"] = decision.model_dump(mode="json")
            if decision.mode == "answer_from_context":
                operator_self_brief = dict(user_request.session_context or {}).get("operator_self_brief")
                if isinstance(operator_self_brief, dict):
                    trace.metadata["operator_self_brief"] = dict(operator_self_brief)
                result = pipeline.answer_from_context_result(
                    user_request,
                    answer=decision.answer,
                    reason=decision.reason,
                    confidence=decision.confidence,
                )
                self.last_display_document = result.display_document
                self.last_failure_summary = None
                observability.stage_completed(
                    OPERATOR_STAGE,
                    "Conversational follow-up completed",
                    "The follow-up was answered from conversation context without new actions.",
                    details={"confidence": decision.confidence, "mode": decision.mode},
                )
                observability.stage_completed(
                    STAGE_COMPLETED,
                    "Request completed",
                    "The conversational follow-up was answered from prior operator output.",
                    details={"final_status": "success", "agent_mode": "llm_operator"},
                )
                self._finalize_profile(user_request, final_status="success")
                return result.final_response

            if decision.plan is None:
                return self._safe_failure(
                    user_request,
                    OPERATOR_STAGE,
                    "Operator follow-up requested new actions but did not provide a plan.",
                )
            plan = decision.plan
            trace.metadata["operator_plan"] = plan.model_dump(mode="json")
            operator_self_brief = dict(user_request.session_context or {}).get("operator_self_brief")
            if isinstance(operator_self_brief, dict):
                trace.metadata["operator_self_brief"] = dict(operator_self_brief)
            if not pipeline.plan_requires_confirmation(plan) or pipeline.operator_tryout_enabled():
                try:
                    result = pipeline.execute_approved(
                        user_request,
                        plan,
                        execution_context=self._execution_context_with_agent_parameters(user_request, request_context),
                        observability=observability,
                    )
                except OperatorValidationError as exc:
                    trace.metadata["operator_validation_errors"] = list(exc.errors)
                    details_for_user = _validation_errors_for_user(list(exc.errors))
                    self._finalize_profile(user_request, final_status="error", failed_stage=OPERATOR_STAGE)
                    return (
                        f"I couldn't complete that request safely at the {OPERATOR_STAGE} stage. "
                        "Conversational execution validation failed."
                        + (f"\n\n{details_for_user}" if details_for_user else "")
                    )
                return self._finish_operator_execution_result(
                    user_request,
                    trace,
                    observability,
                    result,
                )
            result = pipeline.require_confirmation(user_request, plan, observability)
            trace.metadata["operator_confirmation_actions"] = list(result.confirmation_actions)
            trace.metadata["operator_display_document"] = result.display_document
            self.last_display_document = result.display_document
            self._record_last_failure(
                request_id=user_request.request_id,
                prompt=user_request.raw_prompt,
                category="confirmation_required",
                stage=OPERATOR_STAGE,
                reason="Conversational follow-up actions require approval before execution.",
                metadata={"confirmation_actions": list(result.confirmation_actions)},
            )
            observability.stage_completed(
                OPERATOR_STAGE,
                "Conversational follow-up planning completed",
                "The follow-up plan passed validation and is waiting for approval.",
                details={"action_count": len(result.confirmation_actions), "mode": decision.mode},
            )
            observability.stage_completed(
                STAGE_COMPLETED,
                "Request paused for approval",
                "The Conversational follow-up plan is waiting for user approval.",
                details={"final_status": "confirmation_required", "agent_mode": "llm_operator"},
            )
            self._finalize_profile(user_request, final_status="confirmation_required")
            return result.final_response

        clarification_response = self._maybe_pause_for_operator_clarification(
            user_request=user_request,
            request_context=request_context,
            pipeline=pipeline,
            observability=observability,
            trace=trace,
            agent_mode="llm_operator",
        )
        if clarification_response is not None:
            return clarification_response

        try:
            plan, _ = pipeline.propose_and_validate(user_request, observability)
        except OperatorValidationError as exc:
            trace.metadata["operator_validation_errors"] = list(exc.errors)
            message = "Conversational plan failed validation after configured repair attempts."
            details_for_user = _validation_errors_for_user(list(exc.errors))
            self._record_last_failure(
                request_id=user_request.request_id,
                prompt=user_request.raw_prompt,
                category="runtime_error",
                stage=OPERATOR_STAGE,
                reason=message,
                metadata={"validation_errors": list(exc.errors)},
            )
            observability.stage_failed(
                OPERATOR_STAGE,
                "Conversational validation failed",
                message,
                details={"validation_errors": list(exc.errors)},
            )
            self._finalize_profile(user_request, final_status="error", failed_stage=OPERATOR_STAGE)
            return (
                f"I couldn't complete that request safely at the {OPERATOR_STAGE} stage. {message}"
                + (f"\n\n{details_for_user}" if details_for_user else "")
            )

        trace.metadata["agent_mode"] = "llm_operator"
        trace.metadata["operator_plan"] = plan.model_dump(mode="json")
        operator_self_brief = dict(user_request.session_context or {}).get("operator_self_brief")
        if isinstance(operator_self_brief, dict):
            trace.metadata["operator_self_brief"] = dict(operator_self_brief)
        if not pipeline.plan_requires_confirmation(plan) or pipeline.operator_tryout_enabled():
            try:
                result = pipeline.execute_approved(
                    user_request,
                    plan,
                    execution_context=self._execution_context_with_agent_parameters(user_request, request_context),
                    observability=observability,
                )
            except OperatorValidationError as exc:
                trace.metadata["operator_validation_errors"] = list(exc.errors)
                details_for_user = _validation_errors_for_user(list(exc.errors))
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="runtime_error",
                    stage=OPERATOR_STAGE,
                    reason="Conversational execution validation failed.",
                    metadata={"validation_errors": list(exc.errors)},
                )
                observability.stage_failed(
                    OPERATOR_STAGE,
                    "Conversational execution validation failed",
                    "The read-only Conversational plan failed validation before execution.",
                    details={"validation_errors": list(exc.errors)},
                )
                self._finalize_profile(user_request, final_status="error", failed_stage=OPERATOR_STAGE)
                return (
                    f"I couldn't complete that request safely at the {OPERATOR_STAGE} stage. "
                    "Conversational execution validation failed."
                    + (f"\n\n{details_for_user}" if details_for_user else "")
                )
            return self._finish_operator_execution_result(
                user_request,
                trace,
                observability,
                result,
            )

        result = pipeline.require_confirmation(user_request, plan, observability)
        trace.metadata["operator_confirmation_actions"] = list(result.confirmation_actions)
        trace.metadata["operator_display_document"] = result.display_document
        self.last_display_document = result.display_document
        self._record_last_failure(
            request_id=user_request.request_id,
            prompt=user_request.raw_prompt,
            category="confirmation_required",
            stage=OPERATOR_STAGE,
            reason="Conversational actions require approval before execution.",
            metadata={"confirmation_actions": list(result.confirmation_actions)},
        )
        observability.stage_completed(
            OPERATOR_STAGE,
            "Conversational planning completed",
            "The operator plan passed validation and is waiting for approval.",
            details={"action_count": len(result.confirmation_actions)},
        )
        observability.stage_completed(
            STAGE_COMPLETED,
            "Request paused for approval",
            "The Conversational plan is waiting for user approval.",
            details={"final_status": "confirmation_required", "agent_mode": "llm_operator"},
        )
        self._finalize_profile(user_request, final_status="confirmation_required")
        return result.final_response
