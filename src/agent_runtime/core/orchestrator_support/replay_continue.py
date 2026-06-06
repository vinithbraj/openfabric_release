"""Trace replay and continuation helpers."""

from __future__ import annotations

from .common import *
from .formatting import *


class _ReplayContinueMixin:
    """Trace replay and continuation helpers."""

    def _replay_operator_trace(self, trace: PlanningTrace, context: dict[str, Any] | None = None) -> str:
        """Execute a saved Conversational plan after explicit approval."""

        execution_context = dict(context or {})
        agent_mode = str(trace.metadata.get("agent_mode") or "llm_operator")
        mode_label = _agent_display_name_from_context(execution_context)
        if trace.metadata.get("operator_partial_results_pending"):
            execution_context["allow_partial_shell_results"] = True
            execution_context["partial_results_approved"] = True
            trace.metadata["operator_partial_results_approved"] = True
        if trace.metadata.get("operator_deferred_python_code_confirmation_pending"):
            execution_context["operator_seed_records"] = list(
                trace.metadata.get("operator_execution_records") or []
            )
        if trace.metadata.get("operator_completion_review_pending"):
            execution_context["operator_seed_records"] = list(
                trace.metadata.get("operator_execution_records") or []
            )
            completion_metadata = dict(trace.metadata.get("operator_completion_review") or {})
            execution_context["operator_completion_review_attempt_count"] = int(
                completion_metadata.get("completion_review_attempt_count") or 1
            )
        if trace.metadata.get("operator_shell_input_bindings_confirmation_pending"):
            execution_context["operator_seed_records"] = list(
                trace.metadata.get("operator_execution_records") or []
            )
            pending_action_id = str(
                trace.metadata.get("operator_shell_input_bindings_action_id") or ""
            ).strip()
            if pending_action_id:
                execution_context["shell_input_bindings_confirmed_action_ids"] = [
                    pending_action_id
                ]
        if trace.metadata.get("operator_execution_repair_pending"):
            execution_context["operator_seed_records"] = list(
                trace.metadata.get("operator_execution_records") or []
            )
            execution_context["operator_execution_repair_attempted"] = True
            repair_metadata = dict(trace.metadata.get("operator_execution_repair") or {})
            execution_context["operator_execution_repair_attempt_count"] = int(
                repair_metadata.get("operator_execution_repair_attempt_count") or 1
            )
        if trace.metadata.get("operator_failure_continuation_pending"):
            execution_context["operator_seed_records"] = list(
                trace.metadata.get("operator_execution_records") or []
            )
        if trace.metadata.get("operator_clarification_pending"):
            execution_context["operator_resume_after_clarification"] = True
            execution_context["operator_seed_records"] = list(
                trace.metadata.get("operator_execution_records") or []
            )
        streaming_state = trace.metadata.get("operator_streaming_state")
        if isinstance(streaming_state, dict):
            streaming_state = self._ensure_streaming_step_ids(streaming_state)
            trace.metadata["operator_streaming_state"] = dict(streaming_state)
            streaming_seed_records = self._streaming_seed_record_payloads(streaming_state)
            if streaming_seed_records and not execution_context.get("operator_seed_records"):
                execution_context["operator_seed_records"] = streaming_seed_records
            execution_context["workflow_execution_mode"] = "streaming"
        observability = build_observability_context(trace.request_id, execution_context)
        execution_context["observability"] = observability
        user_request = self._build_user_request(trace.raw_prompt, execution_context).model_copy(
            update={"request_id": trace.request_id}
        )
        user_request.safety_context["planning_trace"] = trace
        user_request.safety_context["observability"] = observability
        if isinstance(execution_context.get("runtime_profiler"), RuntimeProfiler):
            user_request.safety_context["runtime_profiler"] = execution_context["runtime_profiler"]
        user_request.session_context["observability"] = observability
        if trace.metadata.get("agent_mode") == "standard_operator":
            user_request.session_context["operator_mode_label"] = mode_label
            user_request.session_context["operator_display_label"] = mode_label
        else:
            user_request.session_context.setdefault("operator_mode_label", mode_label)
            user_request.session_context.setdefault("operator_display_label", mode_label)
        if isinstance(streaming_state, dict):
            user_request.session_context["workflow_execution_mode"] = "streaming"
            user_request.session_context["operator_streaming_prior_results"] = {
                "completed_tasks": list(streaming_state.get("prior_results") or [])
            }
            user_request.session_context["operator_streaming_tasks"] = list(
                streaming_state.get("tasks") or []
            )
            user_request.session_context["operator_streaming_current_index"] = int(
                streaming_state.get("current_index") or 0
            )
            user_request.session_context["operator_streaming_completed_task_ids"] = list(
                streaming_state.get("completed_task_ids") or []
            )
            if execution_context.get("operator_seed_records"):
                user_request.session_context["operator_seed_records"] = list(
                    execution_context.get("operator_seed_records") or []
                )
            tasks = list(streaming_state.get("tasks") or [])
            current_index = int(streaming_state.get("current_index") or 0)
            if 0 <= current_index < len(tasks):
                current_task = dict(tasks[current_index])
                current_step_id = str(current_task.get("streaming_step_id") or "").strip()
                user_request.session_context["operator_streaming_current_task"] = dict(current_task)
                execution_context["operator_streaming_current_task"] = dict(current_task)
                execution_context["operator_streaming_current_index"] = current_index
                if current_step_id:
                    user_request.session_context["operator_streaming_step_id"] = current_step_id
                    user_request.session_context["operator_execution_step_id"] = current_step_id
                    execution_context["operator_streaming_step_id"] = current_step_id
                    execution_context["operator_execution_step_id"] = current_step_id
                current_task_id = str(current_task.get("task_id") or "")
                for item in list(streaming_state.get("online_lookup_results") or []):
                    if not isinstance(item, dict):
                        continue
                    if current_task_id and str(item.get("task_id") or "") != current_task_id:
                        continue
                    lookup_payload = {
                        key: item.get(key)
                        for key in (
                            "provider",
                            "query",
                            "available",
                            "answer_text",
                            "source_title",
                            "source_url",
                            "fetched_at",
                            "error",
                            "provider_results",
                        )
                    }
                    execution_context[ONLINE_LOOKUP_CONTEXT_KEY] = lookup_payload
                    execution_context[ONLINE_LOOKUP_CONTEXTS_KEY] = [lookup_payload]
                    execution_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] = True
                    user_request.session_context[ONLINE_LOOKUP_CONTEXT_KEY] = lookup_payload
                    user_request.session_context[ONLINE_LOOKUP_CONTEXTS_KEY] = [lookup_payload]
                    user_request.session_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] = True
                    break
        operator_self_brief = trace.metadata.get("operator_self_brief")
        if isinstance(operator_self_brief, dict):
            user_request.session_context["operator_self_brief"] = dict(operator_self_brief)
        self._restore_agent_memory_from_trace(user_request, trace, execution_context)
        self.last_planning_trace = trace
        clarification_phase = str(trace.metadata.get("operator_clarification_phase") or "")
        if (
            isinstance(streaming_state, dict)
            and trace.metadata.get("operator_clarification_pending")
            and clarification_phase == "pre_planning"
        ):
            trace.metadata["operator_clarification_pending"] = False
            trace.metadata.pop("operator_clarification_phase", None)
            execution_context["workflow_execution_mode"] = "streaming"
            user_request.session_context["workflow_execution_mode"] = "streaming"
            observability.stage_started(
                OPERATOR_STAGE,
                "Streaming clarification resume started",
                "The runtime is resuming the current streaming step after user clarification.",
                details={
                    "current_index": int(streaming_state.get("current_index") or 0),
                    "completed_task_count": len(streaming_state.get("completed_task_ids") or []),
                },
            )
            return self._run_streaming_operator_loop(
                user_request,
                execution_context,
                self.llm_client,
                observability,
                trace,
                dict(streaming_state),
            )
        plan_payload = trace.metadata.get("operator_plan")
        plan = OperatorPlan.model_validate(plan_payload)
        pipeline = self._operator_pipeline(self.llm_client, execution_context)
        observability.stage_started(
            OPERATOR_STAGE,
            f"{mode_label} execution started",
            "The runtime is executing the approved operator plan.",
            details={"action_count": len(plan.actions)},
        )
        try:
            result = pipeline.execute_approved(
                user_request,
                plan,
                execution_context=execution_context,
                observability=observability,
            )
        except OperatorValidationError as exc:
            trace.metadata["operator_validation_errors"] = list(exc.errors)
            details_for_user = _validation_errors_for_user(list(exc.errors))
            self._finalize_profile(user_request, final_status="error", failed_stage=OPERATOR_STAGE)
            return (
                f"I couldn't complete that request safely at the {OPERATOR_STAGE} stage. "
                "Operator replay validation failed."
                + (f"\n\n{details_for_user}" if details_for_user else "")
            )
        trace.metadata["operator_execution_records"] = [
            record.model_dump(mode="json") for record in result.execution_records
        ]
        self.last_display_document = result.display_document
        if getattr(result, "clarification_required", False) and result.clarification_request is not None:
            result.clarification_request = self._enrich_parameter_clarification_request(
                user_request,
                result.clarification_request,
            )
            trace.metadata["operator_clarification_request"] = (
                result.clarification_request.model_dump(mode="json")
            )
            trace.metadata["operator_clarification_pending"] = True
            trace.metadata["operator_clarification_phase"] = str(
                result.metadata.get("clarification_phase") or "operator_loop"
            )
            if result.plan is not None:
                trace.metadata["operator_plan"] = result.plan.model_dump(mode="json")
            trace.metadata["operator_display_document"] = result.display_document
            self._record_last_failure(
                request_id=trace.request_id,
                prompt=trace.raw_prompt,
                category="clarification_required",
                stage=OPERATOR_STAGE,
                reason=f"{mode_label} execution needs one clarification before continuing.",
                metadata={"clarification_request": trace.metadata["operator_clarification_request"]},
            )
        elif result.confirmation_required and result.plan is not None:
            trace.metadata["operator_plan"] = result.plan.model_dump(mode="json")
            trace.metadata["operator_confirmation_actions"] = list(result.confirmation_actions)
            trace.metadata["operator_display_document"] = result.display_document
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
            if partial_results_pending:
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
                request_id=trace.request_id,
                prompt=trace.raw_prompt,
                category="confirmation_required",
                stage=OPERATOR_STAGE,
                reason=(
                    "Generated Python code requires approval before execution."
                    if deferred_python_pending
                    else "A shell action with bound upstream outputs requires approval before execution."
                    if shell_bindings_pending
                    else "Execution repair actions are waiting for approval before retrying unfinished work."
                    if execution_repair_pending
                    else "Continuation actions are waiting for approval before retrying the failed run."
                    if failure_continuation_pending
                    else "Additional actions are required to finish the requested end state."
                    if completion_review_pending
                    else f"{mode_label} repair actions require approval before execution."
                ),
                metadata={
                    "confirmation_actions": list(result.confirmation_actions),
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
        else:
            if trace.metadata.get("operator_partial_results_approved"):
                trace.metadata["operator_partial_results_pending"] = False
                trace.metadata.pop("operator_confirmation_actions", None)
            if trace.metadata.get("operator_deferred_python_code_confirmation_pending"):
                trace.metadata["operator_deferred_python_code_confirmation_pending"] = False
                trace.metadata.pop("operator_deferred_python_code_confirmation", None)
                trace.metadata.pop("operator_confirmation_actions", None)
            if trace.metadata.get("operator_completion_review_pending"):
                trace.metadata["operator_completion_review_pending"] = False
                trace.metadata.pop("operator_completion_review", None)
                trace.metadata.pop("operator_confirmation_actions", None)
            if trace.metadata.get("operator_shell_input_bindings_confirmation_pending"):
                trace.metadata["operator_shell_input_bindings_confirmation_pending"] = False
                trace.metadata.pop("operator_shell_input_bindings_confirmation", None)
                trace.metadata.pop("operator_shell_input_bindings_action_id", None)
                trace.metadata.pop("operator_confirmation_actions", None)
            if trace.metadata.get("operator_execution_repair_pending"):
                trace.metadata["operator_execution_repair_pending"] = False
                trace.metadata.pop("operator_execution_repair", None)
                trace.metadata.pop("operator_confirmation_actions", None)
            if trace.metadata.get("operator_failure_continuation_pending"):
                trace.metadata["operator_failure_continuation_pending"] = False
                trace.metadata.pop("operator_confirmation_actions", None)
            trace.metadata["operator_clarification_pending"] = False
            trace.metadata.pop("operator_clarification_phase", None)
            continuation = self._operator_failure_continuation_metadata(
                agent_mode=agent_mode,
                plan=result.plan,
                records=list(result.execution_records),
                conversation_id=str(user_request.session_context.get("conversation_id") or ""),
                parent_request_id=str(trace.metadata.get("parent_request_id") or ""),
            )
            if continuation.get("resumable"):
                trace.metadata["operator_failure_continuation"] = continuation
                self.last_failure_summary = {
                    "request_id": trace.request_id,
                    "prompt": trace.raw_prompt,
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
                    "request_id": trace.request_id,
                    "prompt": trace.raw_prompt,
                    "category": "runtime_error",
                    "stage": OPERATOR_STAGE,
                    "reason": f"{mode_label} execution failed.",
                    "metadata": {"operator_failure_continuation": continuation},
                }
        streaming_state = trace.metadata.get("operator_streaming_state")
        if (
            isinstance(streaming_state, dict)
            and result.status == "success"
            and not result.confirmation_required
            and not getattr(result, "clarification_required", False)
        ):
            tasks = list(streaming_state.get("tasks") or [])
            current_index = int(streaming_state.get("current_index") or 0)
            if 0 <= current_index < len(tasks):
                streaming_state = self._streaming_state_after_step(
                    streaming_state,
                    task=dict(tasks[current_index]),
                    result=result,
                )
                trace.metadata["operator_streaming_state"] = dict(streaming_state)
                trace.metadata["operator_execution_records"] = self._streaming_flat_record_payloads(
                    streaming_state
                )
                if int(streaming_state.get("current_index") or 0) < len(tasks):
                    replay_context = dict(user_request.session_context or {})
                    return self._run_streaming_operator_loop(
                        user_request,
                        replay_context,
                        self.llm_client,
                        observability,
                        trace,
                        streaming_state,
                    )
        observability.stage_completed(
            OPERATOR_STAGE,
            f"{mode_label} execution completed",
            "The approved operator plan finished execution.",
            details={"status": result.status, "record_count": len(result.execution_records)},
        )
        self._finalize_profile(
            user_request,
            final_status=(
                "clarification_required"
                if getattr(result, "clarification_required", False)
                else "confirmation_required"
                if result.confirmation_required
                else "success"
                if result.status == "success"
                else "error"
            ),
            failed_stage=None
            if result.status == "success"
            or result.confirmation_required
            or getattr(result, "clarification_required", False)
            else OPERATOR_STAGE,
        )
        return result.final_response

    def _continue_streaming_failure_from_trace(
        self,
        trace: PlanningTrace,
        execution_context: dict[str, Any],
        *,
        agent_mode: str,
        mode_label: str,
    ) -> str | None:
        streaming_state = trace.metadata.get("operator_streaming_state")
        if not isinstance(streaming_state, dict):
            return None
        streaming_state = self._ensure_streaming_step_ids(dict(streaming_state))
        tasks = [dict(task) for task in list(streaming_state.get("tasks") or []) if isinstance(task, dict)]
        try:
            current_index = int(streaming_state.get("current_index") or 0)
        except (TypeError, ValueError):
            current_index = 0
        if current_index < 0 or current_index >= len(tasks):
            return None
        current_task = dict(tasks[current_index])
        current_task_id = str(current_task.get("task_id") or "").strip()
        failed_attempts = [
            item
            for item in list(streaming_state.get("prior_results") or [])
            if isinstance(item, dict)
            and str(item.get("status") or "").strip().lower() != "success"
            and (
                not current_task_id
                or str(item.get("task_id") or "").strip() == current_task_id
            )
        ]
        if not failed_attempts:
            return None

        trace.metadata["agent_mode"] = agent_mode
        trace.metadata["workflow_execution_mode"] = "streaming"
        trace.metadata["operator_streaming_state"] = dict(streaming_state)
        execution_context["workflow_execution_mode"] = "streaming"
        execution_context["operator_failure_continuation"] = True
        observability = build_observability_context(trace.request_id, execution_context)
        execution_context["observability"] = observability
        user_request = self._build_user_request(trace.raw_prompt, execution_context).model_copy(
            update={"request_id": trace.request_id}
        )
        user_request.safety_context["planning_trace"] = trace
        user_request.safety_context["observability"] = observability
        if isinstance(execution_context.get("runtime_profiler"), RuntimeProfiler):
            user_request.safety_context["runtime_profiler"] = execution_context["runtime_profiler"]
        user_request.session_context["observability"] = observability
        user_request.session_context["workflow_execution_mode"] = "streaming"
        user_request.session_context.setdefault("operator_mode_label", mode_label)
        user_request.session_context.setdefault("operator_display_label", mode_label)
        self._restore_agent_memory_from_trace(user_request, trace, execution_context)
        self.last_planning_trace = trace
        observability.stage_started(
            OPERATOR_STAGE,
            f"{mode_label} streaming continuation started",
            "The runtime is resuming the failed decomposed streaming step with prior failure evidence.",
            details={
                "current_index": current_index,
                "task": current_task,
                "failed_attempt_count": len(failed_attempts),
                "completed_task_count": len(streaming_state.get("completed_task_ids") or []),
            },
        )
        observability.info(
            OPERATOR_STAGE,
            "operator.streaming_continuation.seeded",
            "Streaming continuation seeded",
            "The runtime preserved completed streaming outputs and exposed the failed attempt as planning evidence.",
            details={
                "current_index": current_index,
                "task_id": current_task_id,
                "seed_record_count": len(self._streaming_seed_record_payloads(streaming_state)),
                "failed_attempt_count": len(failed_attempts),
            },
        )
        return self._run_streaming_operator_loop(
            user_request,
            execution_context,
            self.llm_client,
            observability,
            trace,
            streaming_state,
        )

    def continue_from_trace(self, trace: PlanningTrace, context: dict[str, Any] | None = None) -> str:
        """Continue a failed operator trace from its completed action records."""

        execution_context = dict(context or {})
        agent_mode = str(trace.metadata.get("agent_mode") or execution_context.get("agent_mode") or "llm_operator")
        mode_label = _agent_display_name_from_context(execution_context)
        streaming_continuation = self._continue_streaming_failure_from_trace(
            trace,
            execution_context,
            agent_mode=agent_mode,
            mode_label=mode_label,
        )
        if streaming_continuation is not None:
            return streaming_continuation
        plan_payload = trace.metadata.get("operator_plan")
        records_payload = list(trace.metadata.get("operator_execution_records") or [])
        if not isinstance(plan_payload, dict) or not records_payload:
            raise ValidationError("This request does not have an operator plan and execution records to continue.")

        plan = OperatorPlan.model_validate(plan_payload)
        records = [
            OperatorExecutionRecord.model_validate(item)
            for item in records_payload
            if isinstance(item, dict)
        ]
        if not any(record.status == "error" for record in records):
            raise ValidationError("This request has no failed operator action to continue.")
        if not any(record.status == "success" for record in records):
            raise ValidationError("This request has no completed operator actions to reuse.")

        observability = build_observability_context(trace.request_id, execution_context)
        execution_context["observability"] = observability
        user_request = self._build_user_request(trace.raw_prompt, execution_context).model_copy(
            update={"request_id": trace.request_id}
        )
        user_request.safety_context["planning_trace"] = trace
        user_request.safety_context["observability"] = observability
        if isinstance(execution_context.get("runtime_profiler"), RuntimeProfiler):
            user_request.safety_context["runtime_profiler"] = execution_context["runtime_profiler"]
        user_request.session_context["observability"] = observability
        user_request.session_context.setdefault("operator_mode_label", mode_label)
        user_request.session_context.setdefault("operator_display_label", mode_label)
        if agent_mode == "standard_operator":
            user_request.session_context["operator_mode_label"] = mode_label
            user_request.session_context["operator_display_label"] = mode_label
        self._restore_agent_memory_from_trace(user_request, trace, execution_context)

        pipeline = self._operator_pipeline(self.llm_client, execution_context)
        notes = str(execution_context.get("operator_continuation_notes") or "").strip()
        self.last_planning_trace = trace
        source_records = list(records)
        first_failed = next(record for record in source_records if record.status == "error")
        seed_records = pipeline._repair_seed_records(plan, plan, source_records)
        if not seed_records:
            raise ValidationError("This request has no reusable completed operator actions to continue from.")
        continuation = self._operator_failure_continuation_metadata(
            agent_mode=agent_mode,
            plan=plan,
            records=source_records,
            conversation_id=str(user_request.session_context.get("conversation_id") or ""),
            parent_request_id=str(execution_context.get("parent_request_id") or ""),
        )
        seed_action_ids = [record.action_id for record in seed_records]
        source_record_payloads = [record.model_dump(mode="json") for record in source_records]
        trace.metadata["operator_failure_continuation"] = {
            **continuation,
            "seed_action_ids": list(seed_action_ids),
            "failed_action_id": first_failed.action_id,
            "deterministic_replay": True,
        }
        trace.metadata["operator_failure_continuation_source_records"] = source_record_payloads
        if notes:
            trace.metadata["operator_failure_continuation_notes"] = notes
        observability.stage_started(
            OPERATOR_STAGE,
            f"{mode_label} continuation started",
            "The runtime is continuing a failed operator run from completed action records.",
            details={"record_count": len(records), "agent_mode": agent_mode},
        )
        observability.info(
            OPERATOR_STAGE,
            "operator.continuation.started",
            "Failure continuation started",
            "The runtime started deterministic continuation from the failed action ledger.",
            details={
                "parent_request_id": execution_context.get("parent_request_id")
                or trace.metadata.get("parent_request_id"),
                "record_count": len(records),
                "failed_action_ids": [record.action_id for record in records if record.status == "error"],
            },
        )
        observability.info(
            OPERATOR_STAGE,
            "operator.continuation.seeded",
            "Failure continuation seeded",
            "The runtime marked successful operator actions as already completed.",
            details={
                "seed_action_ids": list(seed_action_ids),
                "failed_action_id": first_failed.action_id,
                "source_record_count": len(source_records),
            },
        )
        continuation_context = dict(execution_context)
        continuation_context["operator_seed_records"] = [
            record.model_dump(mode="json") for record in seed_records
        ]
        continuation_context["operator_failure_continuation"] = True
        continuation_context["operator_failure_continuation_source_records"] = source_record_payloads
        continuation_context["operator_failure_continuation_failed_action_id"] = first_failed.action_id
        requires_confirmation = pipeline.plan_requires_confirmation_for_pending(plan, seed_records)
        if requires_confirmation:
            confirmation_result = pipeline.require_confirmation(
                user_request,
                plan,
                observability,
                records=seed_records,
            )
            metadata = dict(confirmation_result.metadata)
            metadata.update(
                {
                    "operator_failure_continuation_pending": True,
                    "operator_failure_continuation_reason": (
                        "Continue from the first failed operator action."
                    ),
                    "operator_failure_continuation_failed_action_id": first_failed.action_id,
                    "operator_failure_continuation_source_records": source_record_payloads,
                    "seed_record_count": len(seed_records),
                }
            )
            result = confirmation_result.model_copy(
                update={"execution_records": seed_records, "metadata": metadata}
            )
            observability.warning(
                OPERATOR_STAGE,
                "operator.continuation.paused",
                "Failure continuation paused",
                "Remaining continuation actions require user approval before execution.",
                details={
                    "seed_action_ids": list(seed_action_ids),
                    "failed_action_id": first_failed.action_id,
                    "pending_action_count": len(result.confirmation_actions),
                },
            )
        else:
            continuation_context["confirmation"] = True
            result = pipeline.execute_approved(
                user_request,
                plan,
                continuation_context,
                observability,
            )
            observability.info(
                OPERATOR_STAGE,
                "operator.continuation.completed"
                if result.status == "success"
                else "operator.continuation.failed",
                "Failure continuation completed"
                if result.status == "success"
                else "Failure continuation failed",
                "The runtime replayed the original plan from the failed action.",
                details={
                    "status": result.status,
                    "seed_action_ids": list(seed_action_ids),
                    "record_count": len(result.execution_records),
                },
            )
        trace.metadata["operator_failure_continuation_pending"] = bool(
            result.metadata.get("operator_failure_continuation_pending")
        )
        if trace.metadata.get("operator_failure_continuation_pending"):
            trace.metadata["operator_execution_records"] = [
                record.model_dump(mode="json") for record in result.execution_records
            ]
        final_response = self._finish_operator_execution_result(
            user_request,
            trace,
            observability,
            result,
        )
        observability.stage_completed(
            OPERATOR_STAGE,
            f"{mode_label} continuation completed",
            "The failure-aware continuation finished or paused safely.",
            details={"status": result.status, "record_count": len(result.execution_records)},
        )
        return final_response

    def replay_from_trace(self, trace: PlanningTrace, context: dict[str, Any] | None = None) -> str:
        """Replay a validated DAG from trace without calling the LLM again."""

        from agent_runtime.core.types import ActionDAG

        metadata = trace.metadata if isinstance(trace.metadata, dict) else {}
        direct_sql_pending = metadata.get("sql_pending_confirmation")
        if (
            isinstance(direct_sql_pending, dict)
            and not (trace.dag_validated or trace.validated_dag)
            and bool((context or {}).get("confirmation", False))
        ):
            execution_context = dict(context or {})
            observability = build_observability_context(trace.request_id, execution_context)
            sql_context = {
                **execution_context,
                "raw_prompt": trace.raw_prompt,
                "observability": observability,
                "gateway_client": self.execution_engine.gateway_client,
                "confirmation": True,
                "sql_confirmed_action": dict(direct_sql_pending),
            }
            payload = self._sql_agent_service(context=sql_context).run(
                prompt=str(direct_sql_pending.get("prompt") or trace.raw_prompt),
                parameter_key=str(direct_sql_pending.get("parameter_key") or ""),
                operation=str(direct_sql_pending.get("operation") or "query"),
            )
            return render_sql_agent_response(payload)

        if isinstance(trace.metadata, dict) and (
            trace.metadata.get("agent_mode") in {"llm_operator", "standard_operator"}
            or trace.metadata.get("operator_plan") is not None
        ):
            return self._replay_operator_trace(trace, context)

        execution_context = dict(context or {})
        pending_confirmation = metadata.get("sql_pending_confirmation")
        if isinstance(pending_confirmation, dict) and bool(
            execution_context.get("confirmation", False)
        ):
            execution_context["sql_confirmed_action"] = dict(pending_confirmation)
        observability = build_observability_context(trace.request_id, execution_context)
        execution_context["observability"] = observability
        result_bundle = replay_from_validated_dag(
            trace,
            self.execution_engine,
            {**execution_context, "trusted_replay": True},
        )
        if not bool(result_bundle.metadata.get("confirmation_required", False)) and result_bundle.status == "success":
            self.last_failure_summary = None
        user_request = self._build_user_request(trace.raw_prompt, execution_context).model_copy(
            update={"request_id": trace.request_id}
        )
        user_request.safety_context["planning_trace"] = trace
        user_request.safety_context["observability"] = observability
        if isinstance(execution_context.get("runtime_profiler"), RuntimeProfiler):
            user_request.safety_context["runtime_profiler"] = execution_context["runtime_profiler"]
        user_request.session_context["observability"] = observability
        dag_payload = trace.dag_validated or trace.validated_dag
        dag = ActionDAG.model_validate(dag_payload or {})
        rendered = self.output_orchestrator.render(
            result_bundle,
            user_request=user_request,
            dag=dag,
            llm_client=self.llm_client,
        )
        display_document = rendered.metadata.get("display_document")
        self.last_display_document = (
            dict(display_document)
            if isinstance(display_document, dict)
            else None
        )
        final_status = self._final_request_status(result_bundle, 0)
        self._finalize_profile(
            user_request,
            final_status=final_status,
            failed_stage=None if final_status in {"success", "confirmation_required"} else "runtime",
        )
        return rendered.content
