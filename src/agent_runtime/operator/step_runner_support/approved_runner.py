"""Approved operator execution orchestration for the step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class _StepRunnerApprovedRunnerMixin:
    def _recover_missing_generated_stdin_payload(
        self,
        user_request: UserRequest,
        current_plan: OperatorPlan,
        action: OperatorAction,
        records: list[OperatorExecutionRecord],
        records_by_action: dict[str, OperatorExecutionRecord],
        execution_context: dict[str, Any],
        observability: ObservabilityContext | None,
    ) -> tuple[OperatorPlan, OperatorAction] | OperatorPipelineResult | None:
        """Regenerate one missing generated-text stdin payload before side effects."""

        if action.kind != "shell_command" or action.stdin_mode != "input_binding":
            return None
        input_name = str(action.stdin_input_name or "").strip()
        if not input_name or is_user_macro_input_name(input_name, execution_context):
            return None
        binding = next(
            (
                item
                for item in list(action.input_bindings or [])
                if str(item.input_name or "").strip() == input_name
            ),
            None,
        )
        if binding is None or not binding.required:
            return None
        source = records_by_action.get(str(binding.source_action_id or ""))
        source_metadata = dict(source.metadata or {}) if source is not None else {}
        source_generated = (
            source is not None
            and (
                source.kind == "llm_text"
                or bool(source_metadata.get("llm_text"))
                or bool(source_metadata.get("generated_text"))
            )
        )
        if not source_generated and not self._input_name_looks_generated_text(input_name):
            return None
        value = self._record_field(source, binding.source_field) if source is not None else None
        if value is not None and str(value).strip():
            return None
        recovery_key = f"operator_generated_payload_recovery_attempted:{action.action_id}:{input_name}"
        if bool(execution_context.get(recovery_key)):
            return None
        execution_context[recovery_key] = True
        recovery_action = OperatorAction(
            action_id=new_id("action"),
            task_id=action.task_id,
            kind="llm_text",
            llm_prompt=(
                f"Generate the missing required payload `{input_name}` for the next action.\n"
                f"User request: {user_request.raw_prompt}\n"
                f"Next action reason: {action.reason}\n"
                f"Next command: {action.command}\n"
                "Return only the payload text. Do not include Markdown fences."
            ),
            cwd=action.cwd,
            inputs={},
            input_bindings=[],
            declared_output_shape="text",
            risk="low",
            effect_intent="read_only",
            effect_confidence=1.0,
            effect_summary="Regenerates a missing required generated-text payload.",
            timeout_seconds=action.timeout_seconds,
            reason=f"Regenerate missing required payload {input_name}.",
            depends_on=[],
        )
        self._emit(
            observability,
            level="warning",
            event_type="operator.generated_payload.recovery_started",
            title="Generated payload recovery started",
            summary="A required generated-text payload was empty; the runtime will retry text generation once.",
            details={
                "action_id": action.action_id,
                "task_id": action.task_id,
                "input_name": input_name,
                "source_action_id": binding.source_action_id,
                "source_field": binding.source_field,
                "recovery_action_id": recovery_action.action_id,
            },
        )
        recovery_record = self._execute_action(
            user_request,
            recovery_action,
            records_by_action,
            execution_context,
            next(
                (
                    task
                    for task in list(current_plan.tasks or [])
                    if task.task_id == action.task_id
                ),
                None,
            ),
            observability,
        )
        recovery_record = self._record_with_execution_scope(
            recovery_record,
            execution_context,
        )
        recovery_metadata = dict(recovery_record.metadata or {})
        recovery_metadata.update(
            {
                "generated_payload_recovery": True,
                "recovered_input_name": input_name,
                "recovered_for_action_id": action.action_id,
                "previous_source_action_id": binding.source_action_id,
                "previous_source_field": binding.source_field,
            }
        )
        recovery_record = recovery_record.model_copy(update={"metadata": recovery_metadata})
        records.append(recovery_record)
        records_by_action[recovery_record.action_id] = recovery_record
        if recovery_record.status == "success" and str(recovery_record.stdout or "").strip():
            rewritten_bindings = [
                item.model_copy(
                    update={
                        "source_action_id": recovery_record.action_id,
                        "source_field": "stdout",
                    }
                )
                if str(item.input_name or "").strip() == input_name
                else item
                for item in list(action.input_bindings or [])
            ]
            rewritten_action = action.model_copy(
                update={
                    "input_bindings": rewritten_bindings,
                    "depends_on": list(
                        dict.fromkeys([*list(action.depends_on or []), recovery_record.action_id])
                    ),
                }
            )
            self._emit(
                observability,
                level="info",
                event_type="operator.generated_payload.recovered",
                title="Generated payload recovered",
                summary="The runtime regenerated a missing required generated-text payload.",
                details={
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "input_name": input_name,
                    "recovery_action_id": recovery_record.action_id,
                    "value_preview": _truncate(recovery_record.stdout, 240),
                },
            )
            return self._plan_with_replaced_action(current_plan, rewritten_action), rewritten_action

        clarification = OperatorClarificationRequest(
            question=(
                f"I could not regenerate the required `{input_name}` payload. "
                "Please provide the text to use before I run the command."
            ),
            reason=(
                "The required generated-text payload was empty, and one automatic "
                "regeneration attempt did not produce usable text."
            ),
            missing_information=input_name,
            input_kind="unknown",
            allow_freeform=True,
            confidence=0.9,
            metadata={
                "kind": "generated_payload_retry_failed",
                "action_id": action.action_id,
                "task_id": action.task_id,
                "input_name": input_name,
                "source_action_id": binding.source_action_id,
                "recovery_action_id": recovery_action.action_id,
            },
        )
        return self.require_clarification(
            user_request,
            clarification,
            observability,
            plan=current_plan,
            records=records,
            phase="generated_payload_recovery",
        )

    @staticmethod
    def _sudo_retry_failed_record(
        records: list[OperatorExecutionRecord],
    ) -> OperatorExecutionRecord | None:
        for record in records:
            metadata = dict(record.metadata or {})
            if record.status == "error" and bool(metadata.get("permission_denied_sudo_candidate")):
                return record
        return None

    @staticmethod
    def _sudo_retry_record_from_forbidden_error(
        errors: list[dict[str, Any]],
        records: list[OperatorExecutionRecord],
    ) -> OperatorExecutionRecord | None:
        sudo_error = next(
            (
                error
                for error in errors
                if str(error.get("error") or "") == "forbidden_command"
                and bool(error.get("overrideable"))
                and shell_command_uses_sudo(str(error.get("normalized_command") or ""))
            ),
            None,
        )
        if sudo_error is None:
            return None
        action_id = str(sudo_error.get("action_id") or "").strip()
        source_record = next(
            (
                record
                for record in records
                if record.status == "error" and (not action_id or record.action_id == action_id)
            ),
            None,
        )
        if source_record is None:
            return None
        source_metadata = dict(source_record.metadata or {})
        original_command = str(
            source_metadata.get("shell_command")
            or source_metadata.get("sudo_retry_original_command")
            or ""
        ).strip()
        proposed_command = str(sudo_error.get("normalized_command") or "").strip()
        return source_record.model_copy(
            update={
                "metadata": {
                    **source_metadata,
                    "permission_denied_sudo_candidate": True,
                    "sudo_retry_reason": (
                        "The repaired plan requested sudo after a permission-related execution failure."
                    ),
                    "sudo_retry_original_command": original_command or proposed_command,
                    "sudo_retry_proposed_command": proposed_command,
                }
            }
        )

    @staticmethod
    def _sudo_retry_clarification_request(
        record: OperatorExecutionRecord,
    ) -> OperatorClarificationRequest:
        metadata = dict(record.metadata or {})
        original_command = str(
            metadata.get("sudo_retry_original_command")
            or metadata.get("shell_command")
            or ""
        ).strip()
        proposed_command = str(
            metadata.get("sudo_retry_proposed_command")
            or sudo_command_for(original_command)
        ).strip()
        reason = str(metadata.get("sudo_retry_reason") or "").strip()
        return OperatorClarificationRequest(
            question=(
                "This step previously failed and needs sudo to continue. "
                "Enter a sudo password or choose a saved credential to retry this exact command with sudo."
            ),
            reason=(
                reason
                or "The command failed because it appears to need elevated privileges."
            ),
            missing_information="Sudo password or saved credential to retry this command",
            options=[
                OperatorClarificationOption(
                    option_id="retry_with_sudo",
                    label="Retry with sudo in terminal",
                    description="Run the same command with sudo using terminal/typein input for the password prompt.",
                ),
                OperatorClarificationOption(
                    option_id="do_not_use_sudo",
                    label="Do not use sudo",
                    description="Continue without elevating this command.",
                ),
            ],
            input_kind="password",
            secret_input=True,
            allow_freeform=True,
            metadata={
                "kind": "sudo_retry",
                "action_id": record.action_id,
                "task_id": record.task_id,
                "original_command": original_command,
                "proposed_sudo_command": proposed_command,
                "sudo_retry_reason": reason,
            },
        )

    @staticmethod
    def _sudo_retry_plan(
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
    ) -> OperatorPlan | None:
        failed_by_id = {
            record.action_id: record
            for record in records
            if record.status == "error"
            and bool(dict(record.metadata or {}).get("permission_denied_sudo_candidate"))
        }
        if not failed_by_id:
            return None
        updated_actions: list[OperatorAction] = []
        changed = False
        for action in plan.actions:
            record = failed_by_id.get(action.action_id)
            if record is None or action.kind != "shell_command":
                updated_actions.append(action)
                continue
            metadata = dict(record.metadata or {})
            proposed_command = str(
                metadata.get("sudo_retry_proposed_command")
                or sudo_command_for(action.command)
            ).strip()
            if not proposed_command:
                updated_actions.append(action)
                continue
            updated_actions.append(
                action.model_copy(
                    update={
                        "command": proposed_command,
                        "interaction_mode": "may_prompt",
                        "stdin_mode": "none",
                        "stdin_text": None,
                        "stdin_input_name": None,
                        "reason": (
                            f"{action.reason} Retry with sudo after the prior command "
                            "failed with Permission denied."
                        ).strip(),
                    }
                )
            )
            changed = True
        if not changed:
            return None
        return plan.model_copy(
            update={
                "summary": f"{plan.summary} Retry permission-denied command with sudo.".strip(),
                "actions": updated_actions,
            }
        )

    def _sudo_retry_result_if_applicable(
        self,
        *,
        user_request: UserRequest,
        current_plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        execution_context: dict[str, Any],
        observability: ObservabilityContext | None,
    ) -> OperatorPipelineResult | None:
        failed_record = self._sudo_retry_failed_record(records)
        if failed_record is None or request_denies_sudo(user_request):
            return None
        if not request_authorizes_sudo(user_request):
            return self.require_clarification(
                user_request,
                self._sudo_retry_clarification_request(failed_record),
                observability,
                plan=current_plan,
                records=records,
                phase="execution_repair",
            )
        repaired_plan = self._sudo_retry_plan(current_plan, records)
        if repaired_plan is None:
            return None
        repair_errors = self._validate_plan(user_request, repaired_plan, observability)
        if repair_errors:
            self._emit(
                observability,
                level="error",
                event_type=OPERATOR_REPAIR_REJECTED,
                title="Sudo retry repair rejected",
                summary="The deterministic sudo retry plan failed validation.",
                details={"errors": repair_errors},
            )
            return None
        seed_records = self._repair_seed_records(current_plan, repaired_plan, records)
        repaired_requires_confirmation = self.plan_requires_confirmation(repaired_plan)
        if self.operator_tryout_enabled():
            repaired_requires_confirmation = False
        self._emit(
            observability,
            level="warning",
            event_type="operator.sudo.retry_authorized",
            title="Sudo retry authorized",
            summary="A permission-denied command is being retried with request-scoped sudo authorization.",
            details={
                "failed_action_id": failed_record.action_id,
                "original_command": dict(failed_record.metadata or {}).get(
                    "sudo_retry_original_command"
                ),
                "proposed_sudo_command": dict(failed_record.metadata or {}).get(
                    "sudo_retry_proposed_command"
                ),
                "requires_confirmation": repaired_requires_confirmation,
            },
        )
        if repaired_requires_confirmation:
            repaired_result = self.require_confirmation(
                user_request,
                repaired_plan,
                observability,
                records=seed_records,
            )
            metadata = dict(repaired_result.metadata)
            metadata.update(
                {
                    "operator_execution_repair_pending": True,
                    "operator_execution_repair_reason": "Retry permission-denied command with sudo.",
                    "operator_execution_repair_failed_action_id": failed_record.action_id,
                    "operator_execution_repair_source_records": [
                        record.model_dump(mode="json") for record in records
                    ],
                    "operator_sudo_retry_pending": True,
                    "seed_record_count": len(seed_records),
                }
            )
            return repaired_result.model_copy(
                update={"execution_records": seed_records, "metadata": metadata}
            )
        repair_context = dict(execution_context)
        repair_context.pop("operator_resume_after_clarification", None)
        repair_context["operator_execution_repair_attempted"] = True
        repair_context["operator_seed_records"] = [
            record.model_dump(mode="json") for record in seed_records
        ]
        repaired_result = self.execute_approved(
            user_request,
            repaired_plan,
            repair_context,
            observability,
        )
        seed_action_ids = {
            record["action_id"]
            for record in repair_context["operator_seed_records"]
            if isinstance(record, dict)
        }
        repaired_new_records = [
            record
            for record in repaired_result.execution_records
            if record.action_id not in seed_action_ids
        ]
        combined_result = repaired_result.model_copy(
            update={"execution_records": [*records, *repaired_new_records]}
        )
        return self._attach_execution_learning_digest(
            user_request,
            combined_result,
            observability,
        )

    def execute_approved(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        execution_context: dict[str, Any] | None = None,
        observability: ObservabilityContext | None = None,
    ) -> OperatorPipelineResult:
        """Execute an already-approved operator plan."""

        execution_context = dict(execution_context or {})
        plan = self._normalize_plan_interactions(plan, observability)
        plan = self._apply_llm_effect_policy(user_request, plan, observability)
        if (
            self.config.llm_operator_requires_approval
            and self.plan_requires_confirmation(plan)
            and not self.operator_tryout_enabled()
            and not bool(execution_context.get("confirmation", False))
        ):
            raise OperatorValidationError(
                [
                    {
                        "error": "operator_approval_required",
                        "message": "Conversational actions require approval before execution.",
                    }
                ]
            )
        errors = self._validate_plan(user_request, plan, observability)
        if errors:
            raise OperatorValidationError(errors)
        include_error_seed_records = bool(execution_context.get("operator_resume_after_clarification"))
        seed_records = [
            record
            for record in (
                OperatorExecutionRecord.model_validate(item)
                for item in self._operator_seed_record_payloads(user_request, execution_context)
            )
            if record.status == "success" or include_error_seed_records
        ]
        plan_task_ids = {str(task.task_id or "").strip() for task in plan.tasks}
        plan_action_ids = {str(action.action_id or "").strip() for action in plan.actions}
        current_streaming_step_id = str(
            execution_context.get("operator_streaming_step_id") or ""
        ).strip()

        def _external_streaming_seed(record: OperatorExecutionRecord) -> bool:
            metadata = dict(record.metadata or {})
            streaming_step_id = str(metadata.get("streaming_step_id") or "").strip()
            if current_streaming_step_id and streaming_step_id:
                return streaming_step_id != current_streaming_step_id
            streaming_task_id = str(metadata.get("streaming_task_id") or "").strip()
            return bool(streaming_task_id and streaming_task_id not in plan_task_ids)

        current_seed_records = [
            record for record in seed_records if not _external_streaming_seed(record)
        ]
        external_seed_records = [
            record for record in seed_records if _external_streaming_seed(record)
        ]
        records: list[OperatorExecutionRecord] = list(seed_records)
        records_by_action: dict[str, OperatorExecutionRecord] = {
            record.action_id: record for record in current_seed_records
        }
        for record in external_seed_records:
            if record.action_id not in plan_action_ids:
                records_by_action.setdefault(record.action_id, record)
        completed_seed_action_ids = {record.action_id for record in current_seed_records}
        seed_has_error = any(record.status == "error" for record in current_seed_records)
        current_plan = plan
        for action in self._execution_order(current_plan):
            if _cancel_requested(execution_context):
                break
            if seed_has_error:
                break
            if action.action_id in completed_seed_action_ids:
                continue
            generated_from_deferred = False
            deferred_action_inputs: dict[str, Any] | None = None
            if action.kind in {"python_action", "python_transform"} and action.defer_code_generation:
                action_inputs = self._resolve_action_inputs(action, records_by_action, observability)
                deferred_action_inputs = dict(action_inputs)
                try:
                    generated_action = self._complete_deferred_python_code(
                        user_request,
                        action,
                        action_inputs,
                        observability,
                    )
                except DeferredPythonCodeGenerationError as exc:
                    rejected_generated_code = (
                        str(exc.rejected_proposal.get("code") or "")
                        if isinstance(exc.rejected_proposal, dict)
                        else ""
                    )
                    self.reliability.record_failure(
                        request_id=user_request.request_id,
                        stage="operator.deferred_python",
                        payload=list(exc.errors),
                        title="Deferred Python generation failure",
                        summary="Generated deferred Python code failed validation.",
                        failure_kind="deferred_python_failed",
                    )
                    record = OperatorExecutionRecord(
                        action_id=action.action_id,
                        task_id=action.task_id,
                        kind=action.kind,
                        status="error",
                        stdout="",
                        stderr=str(exc),
                        exit_code=None,
                        output=None,
                        error=str(exc),
                        metadata={
                            "deferred_code_generation_failed": True,
                            "deferred_code_generation_errors": list(exc.errors),
                            "rejected_python_code_proposal": exc.rejected_proposal,
                            "required_function_name": exc.function_name,
                            "bound_inputs_preview": self._inputs_preview(action_inputs),
                            "generated_code": rejected_generated_code or None,
                            "deferred_code_generated": bool(rejected_generated_code),
                        },
                    )
                    records.append(record)
                    records_by_action[record.action_id] = record
                    self._emit(
                        observability,
                        level="error",
                        event_type=OPERATOR_EXECUTION_FAILED,
                        title="Deferred Python code generation failed",
                        summary=(
                            "Generated deferred Python code failed structural validation; "
                            "execution repair will receive the validation packet."
                        ),
                        details=record.model_dump(mode="json"),
                    )
                    break
                current_plan = self._plan_with_replaced_action(current_plan, generated_action)
                if (
                    self.config.llm_operator_requires_approval
                    and self._action_requires_confirmation(current_plan, generated_action)
                    and not self.operator_tryout_enabled()
                ):
                    self._store_records(records)
                    return self._deferred_python_code_confirmation_result(
                        user_request,
                        current_plan,
                        generated_action,
                        records,
                        observability,
                    )
                action = generated_action
                generated_from_deferred = True
            shell_binding_mode = _shell_input_bindings_mode_from_value(
                getattr(self.config, "shell_input_bindings_mode", "allow")
            )
            if (
                isinstance(user_request.session_context, dict)
                and "shell_input_bindings_mode" in user_request.session_context
            ) or (
                isinstance(user_request.safety_context, dict)
                and "shell_input_bindings_mode" in user_request.safety_context
            ):
                shell_binding_mode = _shell_input_bindings_mode_from_request(user_request)
            generated_payload_recovery = self._recover_missing_generated_stdin_payload(
                user_request,
                current_plan,
                action,
                records,
                records_by_action,
                execution_context,
                observability,
            )
            if isinstance(generated_payload_recovery, OperatorPipelineResult):
                self._store_records(records)
                return generated_payload_recovery
            if generated_payload_recovery is not None:
                current_plan, action = generated_payload_recovery
            confirmed_shell_action_ids = {
                str(item)
                for item in list(execution_context.get("shell_input_bindings_confirmed_action_ids") or [])
            }
            if (
                action.kind == "shell_command"
                and action.input_bindings
                and shell_binding_mode == "confirm_bound"
                and action.action_id not in confirmed_shell_action_ids
            ):
                stdin_input_name = _action_stdin_binding_name(action)
                shell_stdin, shell_stdin_metadata = self._resolve_shell_stdin(
                    action,
                    records_by_action,
                    observability,
                )
                shell_bound_env = self._resolve_shell_binding_env(
                    action,
                    records_by_action,
                    observability,
                    exclude_input_names={stdin_input_name} if stdin_input_name else set(),
                )
                self._store_records(records)
                return self._shell_bound_confirmation_result(
                    user_request,
                    current_plan,
                    action,
                    shell_bound_env,
                    shell_stdin_metadata,
                    records,
                    observability,
                )
            if action.kind == "shell_command" and action.input_bindings and shell_binding_mode == "allow":
                self._emit(
                    observability,
                    level="info",
                    event_type="operator.shell_bindings.allowed",
                    title="Shell input bindings allowed",
                    summary="Shell input bindings are allowed by the request setting.",
                    details={"action_id": action.action_id, "mode": shell_binding_mode},
                )
            if self.operator_tryout_enabled():
                self._emit(
                    observability,
                    level="info",
                    event_type=OPERATOR_TRYOUT_EXECUTING,
                    title="Tryout action executing",
                    summary="Retired tryout started executing a validated LLM-authored action capsule.",
                    details={"action": action.model_dump(mode="json")},
                )
            record = self._execute_action(
                user_request,
                action,
                records_by_action,
                execution_context,
                next((task for task in current_plan.tasks if task.task_id == action.task_id), None),
                observability,
            )
            record = self._record_with_execution_scope(record, execution_context)
            if action.kind in {"python_action", "python_transform"}:
                metadata = dict(record.metadata)
                if generated_from_deferred:
                    metadata.update(
                        {
                            "deferred_code_generated": True,
                            "generated_code": str(action.code or ""),
                        }
                    )
                    record = record.model_copy(update={"metadata": metadata})
                python_repair_inputs = deferred_action_inputs
                if record.status == "error" and python_repair_inputs is None:
                    try:
                        python_repair_inputs = self._resolve_action_inputs(
                            action,
                            records_by_action,
                            observability,
                        )
                    except Exception as exc:
                        metadata = dict(record.metadata)
                        metadata.update(
                            {
                                "python_code_execution_repair_skipped": True,
                                "python_code_execution_repair_skip_reason": (
                                    f"failed to resolve action inputs: {exc}"
                                ),
                            }
                        )
                        record = record.model_copy(update={"metadata": metadata})
                if (
                    record.status == "error"
                    and python_repair_inputs is not None
                    and self._max_deferred_code_repair_attempts() > 0
                    and not self._action_requires_confirmation(current_plan, action)
                ):
                    previous_errors: list[dict[str, Any]] = []
                    repair_attempts = max(
                        1,
                        int(self._max_deferred_code_repair_attempts()),
                    )
                    previous_code = str(action.code or "")
                    for repair_index in range(repair_attempts):
                        failed_record = record.model_dump(mode="json")
                        previous_errors.append(failed_record)
                        self._emit(
                            observability,
                            level="warning",
                            event_type="operator.python_code.execution_repair_started",
                            title="Python execution repair started",
                            summary=(
                                "Python failed at runtime; the runtime will ask for repaired code for "
                                "the same action before escalating to full plan repair."
                            ),
                            details={
                                "action_id": action.action_id,
                                "task_id": action.task_id,
                                "attempt": repair_index + 1,
                                "max_attempts": repair_attempts,
                                "failed_record": failed_record,
                            },
                        )
                        try:
                            repair_template = action.model_copy(
                                update={"code": None, "defer_code_generation": True}
                            )
                            repaired_action = self._complete_deferred_python_code(
                                user_request,
                                repair_template,
                                python_repair_inputs,
                                observability,
                                execution_feedback={
                                    "latest_failure": failed_record,
                                    "previous_failures": previous_errors,
                                    "attempt": repair_index + 1,
                                    "max_attempts": repair_attempts,
                                },
                                previous_code=previous_code,
                            )
                        except DeferredPythonCodeGenerationError as exc:
                            metadata = dict(record.metadata)
                            metadata.update(
                                {
                                    "deferred_code_execution_repair_failed": True,
                                    "deferred_code_execution_repair_errors": list(exc.errors),
                                    "rejected_python_code_proposal": exc.rejected_proposal,
                                    "deferred_code_execution_repair_attempts": repair_index + 1,
                                }
                            )
                            record = record.model_copy(update={"metadata": metadata})
                            break
                        except Exception as exc:
                            metadata = dict(record.metadata)
                            metadata.update(
                                {
                                    "deferred_code_execution_repair_failed": True,
                                    "deferred_code_execution_repair_errors": [
                                        {
                                            "error": "deferred_code_repair_exception",
                                            "message": str(exc),
                                        }
                                    ],
                                    "deferred_code_execution_repair_attempts": repair_index + 1,
                                }
                            )
                            record = record.model_copy(update={"metadata": metadata})
                            self._emit(
                                observability,
                                level="error",
                            event_type="operator.python_code.execution_repair_failed",
                            title="Python execution repair failed",
                            summary=(
                                "The LLM repair request failed before producing usable Python; "
                                "the runtime will preserve the execution failure for normal repair handling."
                                ),
                                details=record.model_dump(mode="json"),
                                error=str(exc),
                            )
                            break
                        current_plan = self._plan_with_replaced_action(current_plan, repaired_action)
                        repaired_record = self._execute_action(
                            user_request,
                            repaired_action,
                            records_by_action,
                            execution_context,
                            next(
                                (
                                    task
                                    for task in current_plan.tasks
                                    if task.task_id == repaired_action.task_id
                                ),
                                None,
                            ),
                            observability,
                        )
                        repaired_record = self._record_with_execution_scope(
                            repaired_record,
                            execution_context,
                        )
                        repaired_metadata = dict(repaired_record.metadata)
                        repaired_metadata.update(
                            {
                                "deferred_code_generated": True,
                                "deferred_code_execution_repaired": repaired_record.status == "success",
                                "deferred_code_execution_repair_attempts": repair_index + 1,
                                "python_code_execution_repaired": repaired_record.status == "success",
                                "python_code_execution_repair_attempts": repair_index + 1,
                                "generated_code": str(repaired_action.code or ""),
                                "previous_deferred_execution_errors": list(previous_errors),
                            }
                        )
                        record = repaired_record.model_copy(update={"metadata": repaired_metadata})
                        action = repaired_action
                        previous_code = str(repaired_action.code or "")
                        self._emit(
                            observability,
                            level="info" if record.status == "success" else "error",
                            event_type="operator.python_code.execution_repaired",
                            title=(
                                "Python execution repaired"
                                if record.status == "success"
                                else "Python execution repair failed"
                            ),
                            summary="The repaired Python code was executed for the same action.",
                            details=record.model_dump(mode="json"),
                        )
                        if record.status == "success":
                            break
                if generated_from_deferred or bool(record.metadata.get("deferred_code_generated")):
                    self._write_computation_cache_result(
                        user_request,
                        current_plan,
                        action,
                        dict(deferred_action_inputs or {}),
                        record,
                        observability,
                    )
            applied_lrdirect = dict(user_request.session_context or {}).get("operator_lrdirect_applied")
            if isinstance(applied_lrdirect, dict) and record.status != "success":
                cache_type = str(applied_lrdirect.get("cache_type") or "").strip()
                cache_id = str(applied_lrdirect.get("cache_id") or "").strip()
                if (
                    cache_type in {"command_template", "payload_command_template"}
                    and cache_id
                    and self.command_template_cache_store is not None
                ):
                    self.command_template_cache_store.mark_failed(
                        cache_id,
                        failure_category=str(record.error or record.stderr or "lrdirect_command_failed")[:300],
                        repair_notes=str(user_request.session_context.get("operator_repair_notes") or ""),
                    )
                elif cache_type == "computation" and cache_id and self.computation_cache_store is not None:
                    self.computation_cache_store.mark_failed(
                        cache_id,
                        failure_category=str(record.error or record.stderr or "lrdirect_python_failed")[:300],
                        repair_notes=str(user_request.session_context.get("operator_repair_notes") or ""),
                    )
            if action.kind == "shell_command":
                applied_template = dict(user_request.session_context or {}).get(
                    "operator_command_template_cache_applied"
                )
                if (
                    isinstance(applied_template, dict)
                    and record.status == "error"
                    and self.command_template_cache_store is not None
                ):
                    template_id = str(applied_template.get("template_id") or "").strip()
                    if template_id:
                        self.command_template_cache_store.mark_failed(
                            template_id,
                            failure_category=str(record.error or record.stderr or "command_template_failed")[:300],
                            repair_notes=str(user_request.session_context.get("operator_repair_notes") or ""),
                        )
                self._write_command_template_cache_result(
                    user_request,
                    current_plan,
                    action,
                    record,
                    execution_context,
                    observability,
                )
            records.append(record)
            records_by_action[record.action_id] = record
            event_type = OPERATOR_EXECUTION_COMPLETED if record.status == "success" else OPERATOR_EXECUTION_FAILED
            self._emit(
                observability,
                level="info" if record.status == "success" else "error",
                event_type=event_type,
                title="Operator action completed" if record.status == "success" else "Operator action failed",
                summary="The approved operator action finished.",
                details=record.model_dump(mode="json"),
            )
            if self.operator_tryout_enabled():
                self._emit(
                    observability,
                    level="info" if record.status == "success" else "error",
                    event_type=(
                        OPERATOR_TRYOUT_COMPLETED
                        if record.status == "success"
                        else OPERATOR_TRYOUT_FAILED
                    ),
                    title=(
                        "Tryout action completed"
                        if record.status == "success"
                        else "Tryout action failed"
                    ),
                    summary="A retired tryout action finished execution.",
                    details=record.model_dump(mode="json"),
                )
            if record.status == "error":
                if bool(record.metadata.get("partial_stdout_available")) and not bool(
                    execution_context.get("allow_partial_shell_results")
                ):
                    self._store_records(records)
                    return self._partial_results_confirmation_result(
                        user_request,
                        current_plan,
                        records,
                        record,
                        observability,
                    )
                break
        self._store_records(records)
        streaming_step_scope = isinstance(
            dict(user_request.session_context or {}).get("operator_streaming_current_task"),
            dict,
        )
        for failed_record in [record for record in records if record.status == "error"]:
            failure_kind = self.reliability.record_failure(
                request_id=user_request.request_id,
                stage="operator.execution",
                payload=failed_record.model_dump(mode="json"),
                title="Operator execution failure",
                summary=failed_record.error or "An operator action failed during execution.",
            )
            decision = self.reliability.decide(
                failure_kind,
                budget=self.reliability.budget_from_context(user_request.session_context),
            )
            self.reliability.record_decision(
                user_request.request_id,
                decision,
                stage="operator.execution",
            )
        sudo_retry_result = self._sudo_retry_result_if_applicable(
            user_request=user_request,
            current_plan=current_plan,
            records=records,
            execution_context=execution_context,
            observability=observability,
        )
        if sudo_retry_result is not None:
            return sudo_retry_result
        repair_attempt_count = int(
            execution_context.get("operator_execution_repair_attempt_count")
            or (1 if execution_context.get("operator_execution_repair_attempted") else 0)
        )
        if (
            any(record.status == "error" for record in records)
            and not (
                self.operator_fast_trout_enabled()
                and bool(execution_context.get("operator_fast_trout_disable_execution_repair"))
            )
            and repair_attempt_count < self._tryout_repair_attempt_budget()
        ):
            repair_feedback: list[dict[str, Any]] = []
            policy_rejection_count = 0
            repair_attempt_budget = self._tryout_repair_attempt_budget()
            while repair_attempt_count < repair_attempt_budget:
                repair_attempt_count += 1
                if self.operator_tryout_enabled():
                    self._emit(
                        observability,
                        level="warning",
                        event_type=OPERATOR_TRYOUT_REPAIRING,
                        title="Tryout repair started",
                        summary="Retired tryout is feeding execution failure evidence back to the LLM for a bounded repair.",
                        details={
                            "attempt": repair_attempt_count,
                            "max_attempts": repair_attempt_budget,
                            "record_count": len(records),
                            "failed_records": [
                                record.model_dump(mode="json")
                                for record in records
                                if record.status == "error"
                            ],
                        },
                    )
                try:
                    repair = self._complete_execution_repair(
                        user_request,
                        current_plan,
                        records,
                        repair_feedback=repair_feedback,
                    )
                    repair_errors = []
                    repaired_plan = None
                    seed_records = []
                    if repair is None:
                        pass
                    else:
                        try:
                            adjudication = self._complete_execution_repair_adjudication(
                                user_request,
                                current_plan,
                                records,
                                repair,
                                repair_feedback=repair_feedback,
                            )
                        except Exception as adjudication_exc:
                            self._emit(
                                observability,
                                level="warning",
                                event_type=OPERATOR_REPAIR_ADJUDICATION_SKIPPED,
                                title="Operator execution repair adjudication skipped",
                                summary=(
                                    "The repair adjudicator failed, so the runtime kept the "
                                    "existing deterministic repair path."
                                ),
                                details={
                                    "attempt": repair_attempt_count,
                                    "max_attempts": repair_attempt_budget,
                                    "error": str(adjudication_exc),
                                    "repair_decision": repair.decision,
                                },
                            )
                        else:
                            adjudication_details = {
                                "attempt": repair_attempt_count,
                                "max_attempts": repair_attempt_budget,
                                "decision": adjudication.decision,
                                "issues": list(adjudication.issues),
                                "required_change": adjudication.required_change,
                                "reason": adjudication.reason,
                                "confidence": adjudication.confidence,
                                "repair_decision": repair.decision,
                            }
                            self._emit(
                                observability,
                                level=(
                                    "info"
                                    if adjudication.decision == "accept"
                                    else "warning"
                                ),
                                event_type=OPERATOR_REPAIR_ADJUDICATED,
                                title="Operator execution repair adjudicated",
                                summary=(
                                    "The repair adjudicator accepted the proposed execution repair."
                                    if adjudication.decision == "accept"
                                    else (
                                        "The repair adjudicator requested a revised execution "
                                        "repair before validation or execution."
                                    )
                                ),
                                details=adjudication_details,
                            )
                            if adjudication.decision == "revise_repair":
                                repair_errors = [
                                    {
                                        "error": "execution_repair_adjudication_rejected",
                                        "message": (
                                            adjudication.required_change
                                            or adjudication.reason
                                            or "Revise the execution repair."
                                        ),
                                        "issues": list(adjudication.issues),
                                        "reason": adjudication.reason,
                                        "confidence": adjudication.confidence,
                                        "repair_decision": repair.decision,
                                    }
                                ]
                    if repair is None:
                        pass
                    elif repair_errors:
                        pass
                    elif repair.decision == "ask_user":
                        if repair.clarification_request is None:
                            raise ValueError("Execution repair requested clarification without a question.")
                        internal_repair_error = (
                            self._execution_repair_internal_python_clarification_error(
                                repair.clarification_request,
                                records,
                            )
                        )
                        if internal_repair_error is not None:
                            policy_rejection_count += 1
                            repair_attempt_count -= 1
                            notes = user_request.session_context.setdefault(
                                "operator_policy_notes",
                                [],
                            )
                            if isinstance(notes, list):
                                notes.append(
                                    {
                                        "phase": "execution_repair",
                                        "reason": "internal_python_failure",
                                        "instruction": internal_repair_error["message"],
                                        "question": repair.clarification_request.question,
                                        "missing_information": (
                                            repair.clarification_request.missing_information
                                        ),
                                    }
                                )
                            repair_feedback.append(
                                {
                                    "attempt": repair_attempt_count + 1,
                                    "errors": [internal_repair_error],
                                    "repair_decision": repair.decision,
                                    "retry_rationale": repair.retry_rationale,
                                    "required_change": repair.required_change,
                                    "corrected_plan_summary": "",
                                }
                            )
                            self._emit(
                                observability,
                                level="warning",
                                event_type=OPERATOR_CLARIFICATION_REJECTED,
                                title="Operator execution repair clarification rejected",
                                summary=(
                                    "The repair asked for a user choice about an internal "
                                    "generated Python failure."
                                ),
                                details=internal_repair_error,
                            )
                            continue
                        policy_review = self._reject_clarification_by_policy(
                            user_request,
                            repair.clarification_request,
                            observability,
                            phase="execution_repair",
                        )
                        if policy_review is None:
                            resolution = self._resolve_clarification_before_ask(
                                user_request,
                                repair.clarification_request,
                                observability,
                                phase="execution_repair",
                                reason=repair.retry_rationale or repair.root_cause,
                            )
                            if resolution.decision == "continue_with_assumption":
                                repair_attempt_count -= 1
                                repair_feedback.append(
                                    {
                                        "attempt": repair_attempt_count + 1,
                                        "errors": [
                                            {
                                                "error": "execution_repair_clarification_auto_resolved",
                                                "message": resolution.reason,
                                                "assumptions": list(resolution.assumptions),
                                                "selected_entities": [
                                                    entity.model_dump(mode="json")
                                                    for entity in resolution.selected_entities
                                                ],
                                                "execution_directives": list(
                                                    resolution.execution_directives
                                                ),
                                            }
                                        ],
                                        "repair_decision": repair.decision,
                                        "retry_rationale": repair.retry_rationale,
                                        "required_change": repair.required_change,
                                        "corrected_plan_summary": "",
                                    }
                                )
                                continue
                            if resolution.user_question.strip():
                                repair.clarification_request = (
                                    repair.clarification_request.model_copy(
                                        update={
                                            "question": resolution.user_question.strip(),
                                            "reason": resolution.reason
                                            or repair.clarification_request.reason,
                                            "confidence": max(
                                                float(
                                                    repair.clarification_request.confidence
                                                    or 0.0
                                                ),
                                                float(resolution.confidence or 0.0),
                                            ),
                                        }
                                    )
                                )
                            return self.require_clarification(
                                user_request,
                                repair.clarification_request,
                                observability,
                                plan=current_plan,
                                records=records,
                                phase="execution_repair",
                            )
                        if policy_rejection_count < 1:
                            policy_rejection_count += 1
                            repair_attempt_count -= 1
                            repair_feedback.append(
                                {
                                    "attempt": repair_attempt_count + 1,
                                    "errors": [
                                        {
                                            "error": "execution_repair_clarification_rejected",
                                            "message": policy_review.reason,
                                            "missing_information": (
                                                repair.clarification_request.missing_information
                                            ),
                                            "question": repair.clarification_request.question,
                                        }
                                    ],
                                    "repair_decision": repair.decision,
                                    "retry_rationale": repair.retry_rationale,
                                    "required_change": repair.required_change,
                                    "corrected_plan_summary": "",
                                }
                            )
                            continue
                        repair_errors = [
                            {
                                "error": "execution_repair_clarification_rejected",
                                "message": policy_review.reason,
                                "missing_information": (
                                    repair.clarification_request.missing_information
                                ),
                                "question": repair.clarification_request.question,
                            }
                        ]
                        repaired_plan = None
                        seed_records = []
                    elif repair.decision == "repair_plan":
                        if repair.corrected_plan is None:
                            raise ValueError("Execution repair did not include a corrected plan.")
                        repaired_plan = self._normalize_plan_interactions(
                            repair.corrected_plan,
                            observability,
                        )
                        # Execution repair receives live stdout/stderr, output previews, and the
                        # generated Python code that failed. Keep repaired Python concrete here
                        # instead of re-defering it back into the planning-time codegen path.
                        repair_errors = self._validate_plan(user_request, repaired_plan, observability)
                        if not repair_errors:
                            reviewed_plan, memory_errors = self._review_memory_compliance(
                                user_request,
                                repaired_plan,
                                observability,
                                source="execution_repair",
                            )
                            if memory_errors:
                                repair_errors = memory_errors
                            else:
                                repaired_plan = reviewed_plan
                        if not repair_errors:
                            memory_directives = [
                                directive
                                for directive in _memory_directives_for_stage(
                                    user_request,
                                    "plan_review",
                                )
                                if str(directive.get("strength") or "")
                                in {"must_consider", "required_unless_conflict"}
                            ]
                            repair_errors = _memory_constraint_errors(
                                user_request,
                                repaired_plan,
                                memory_directives,
                            )
                        seed_records = self._repair_seed_records(current_plan, repaired_plan, records)
                        if not repair_errors:
                            repair_errors = self._execution_repair_repeated_failed_command_errors(
                                current_plan,
                                repaired_plan,
                                records,
                            )
                        if not repair_errors:
                            repair_errors = self._execution_repair_completed_mutation_errors(
                                current_plan,
                                repaired_plan,
                                records,
                                seed_records,
                            )
                        if not repair_errors:
                            repair_errors = self._execution_repair_ignored_clarification_errors(
                                user_request,
                                current_plan,
                                repaired_plan,
                                records,
                            )
                except Exception as exc:
                    repair_errors = [
                        {
                            "error": "execution_repair_exception",
                            "message": str(exc),
                        }
                    ]
                    repaired_plan = None
                    seed_records = []
                    repair = None

                if repair_errors:
                    sudo_forbidden = any(
                        str(error.get("error") or "") == "forbidden_command"
                        and bool(error.get("overrideable"))
                        and shell_command_uses_sudo(str(error.get("normalized_command") or ""))
                        for error in repair_errors
                    )
                    failed_record = (
                        self._sudo_retry_failed_record(records)
                        or self._sudo_retry_record_from_forbidden_error(repair_errors, records)
                    )
                    if (
                        sudo_forbidden
                        and failed_record is not None
                        and not request_authorizes_sudo(user_request)
                        and not request_denies_sudo(user_request)
                    ):
                        return self.require_clarification(
                            user_request,
                            self._sudo_retry_clarification_request(failed_record),
                            observability,
                            plan=current_plan,
                            records=records,
                            phase="execution_repair",
                        )
                    adjudication_rejected = any(
                        str(error.get("error") or "")
                        == "execution_repair_adjudication_rejected"
                        for error in repair_errors
                    )
                    self._emit(
                        observability,
                        level="error",
                        event_type=OPERATOR_REPAIR_REJECTED,
                        title="Operator execution repair rejected",
                        summary=(
                            "The repair adjudicator requested a revised execution repair."
                            if adjudication_rejected
                            else (
                            "The repaired operator plan failed deterministic validation."
                            if repaired_plan is not None
                            else "The LLM could not produce a valid repair plan for the execution failure."
                            )
                        ),
                        details={
                            "attempt": repair_attempt_count,
                            "max_attempts": repair_attempt_budget,
                            "errors": repair_errors,
                        },
                    )
                    repair_feedback.append(
                        {
                            "attempt": repair_attempt_count,
                            "errors": repair_errors,
                            "repair_decision": getattr(repair, "decision", ""),
                            "retry_rationale": getattr(repair, "retry_rationale", ""),
                            "required_change": getattr(repair, "required_change", ""),
                            "corrected_plan_summary": (
                                repaired_plan.summary if repaired_plan is not None else ""
                            ),
                        }
                    )
                    continue
                if repaired_plan is None or repair is None:
                    continue
                repaired_requires_confirmation = self.plan_requires_confirmation(repaired_plan)
                mutating_repair_action_ids = [
                    action.action_id
                    for action in repaired_plan.actions
                    if self._action_requires_confirmation(repaired_plan, action)
                ]
                envelope_decision: dict[str, Any] | None = None
                if repaired_requires_confirmation and self.reliability.enabled:
                    envelope_decision = self.reliability.check_and_consume_approval_envelope(
                        request_id=user_request.request_id,
                        plan=repaired_plan,
                        mutating_action_ids=mutating_repair_action_ids,
                    )
                    if envelope_decision.get("allowed") is True:
                        repaired_requires_confirmation = False
                if self.operator_tryout_enabled():
                    repaired_requires_confirmation = False
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_REPAIR_ACCEPTED,
                    title="Operator execution repair accepted",
                    summary=(
                        "The LLM proposed a repaired operator plan that requires approval."
                        if repaired_requires_confirmation
                        else (
                            "The repaired mutating plan stayed inside the approval envelope."
                            if envelope_decision and envelope_decision.get("allowed") is True
                            else "The LLM proposed a read-only repaired operator plan."
                        )
                    ),
                    details={
                        **repaired_plan.model_dump(mode="json"),
                        "attempt": repair_attempt_count,
                        "max_attempts": repair_attempt_budget,
                        "requires_confirmation": repaired_requires_confirmation,
                        "approval_envelope": envelope_decision,
                    },
                )
                if self.reliability.enabled:
                    self.reliability.store.record_event(
                        request_id=user_request.request_id,
                        event_kind="recovery_accepted",
                        recovery_action="repair_plan",
                        stage="operator.execution_repair",
                        title="Execution repair accepted",
                        summary=repair.retry_rationale
                        or repair.required_change
                        or "The repaired operator plan passed validation.",
                        model_id=self.reliability.model_id,
                        status="info",
                        evidence={
                            "attempt": repair_attempt_count,
                            "requires_confirmation": repaired_requires_confirmation,
                            "approval_envelope": envelope_decision,
                            "action_count": len(repaired_plan.actions),
                        },
                    )
                if repaired_requires_confirmation:
                    repaired_result = self.require_confirmation(
                        user_request,
                        repaired_plan,
                        observability,
                        records=seed_records,
                    )
                    metadata = dict(repaired_result.metadata)
                    failed_record = next(
                        (record for record in records if record.status == "error"),
                        None,
                    )
                    metadata.update(
                        {
                            "operator_execution_repair_pending": True,
                            "operator_execution_repair_reason": repair.retry_rationale
                            or repair.required_change
                            or repair.root_cause,
                            "operator_execution_repair_attempt_count": repair_attempt_count,
                            "operator_execution_repair_failed_action_id": (
                                failed_record.action_id if failed_record is not None else ""
                            ),
                            "operator_execution_repair_source_records": [
                                record.model_dump(mode="json") for record in records
                            ],
                            "seed_record_count": len(seed_records),
                        }
                    )
                    return repaired_result.model_copy(
                        update={"execution_records": seed_records, "metadata": metadata}
                    )
                repair_context = dict(execution_context)
                repair_context.pop("operator_resume_after_clarification", None)
                repair_context["operator_execution_repair_attempted"] = True
                repair_context["operator_execution_repair_attempt_count"] = repair_attempt_count
                repair_context["operator_seed_records"] = [
                    record.model_dump(mode="json") for record in seed_records
                ]
                repaired_result = self.execute_approved(
                    user_request,
                    repaired_plan,
                    repair_context,
                    observability,
                )
                seed_action_ids = {
                    record["action_id"]
                    for record in repair_context["operator_seed_records"]
                    if isinstance(record, dict)
                }
                repaired_new_records = [
                    record
                    for record in repaired_result.execution_records
                    if record.action_id not in seed_action_ids
                ]
                combined_result = repaired_result.model_copy(
                    update={"execution_records": [*records, *repaired_new_records]}
                )
                return self._attach_execution_learning_digest(
                    user_request,
                    combined_result,
                    observability,
                )
        status = "success" if all(record.status == "success" for record in records) else "error"
        completion_review_count = int(execution_context.get("operator_completion_review_attempt_count") or 0)
        streaming_step_scope = isinstance(
            dict(user_request.session_context or {}).get("operator_streaming_current_task"),
            dict,
        )
        if self.operator_fast_trout_enabled() and bool(
            execution_context.get("operator_fast_trout_probe_only")
        ):
            markdown = (
                "retired tryout capsule pass completed."
                if status == "success"
                else "retired tryout capsule pass failed."
            )
            result = OperatorPipelineResult(
                status=status,
                final_response=markdown,
                plan=current_plan,
                execution_records=records,
                display_document=self._display_document(
                    user_request=user_request,
                    status=status,
                    markdown=markdown,
                    plan=current_plan,
                    records=records,
                ),
                metadata={"operator_fast_trout_probe_only": True},
            )
            return self._attach_execution_learning_digest(user_request, result, observability)
        if status == "success":
            step_validation_result = self._validate_operator_plan_steps(
                user_request,
                current_plan,
                records,
                observability,
            )
            if step_validation_result is not None:
                return self._attach_execution_learning_digest(
                    user_request,
                    step_validation_result,
                    observability,
                )
            if streaming_step_scope:
                trace_metadata = _operator_planning_trace_metadata(user_request)
                if trace_metadata is not None:
                    trace_metadata["operator_step_validation_checked"] = True
        if (
            status == "success"
            and not streaming_step_scope
            and self.plan_requires_confirmation(current_plan)
            and completion_review_count < self._max_completion_repair_attempts()
        ):
            try:
                policy_retry_count = 0
                while True:
                    completion_review = self._complete_completion_review(
                        user_request,
                        current_plan,
                        records,
                        observability,
                    )
                    if (
                        completion_review.decision == "ask_user"
                        and completion_review.clarification_request is not None
                    ):
                        policy_review = self._reject_clarification_by_policy(
                            user_request,
                            completion_review.clarification_request,
                            observability,
                            phase="completion_review",
                        )
                        if policy_review is not None:
                            policy_retry_count += 1
                            if policy_retry_count > 1:
                                raise ValueError(
                                    "Completion review repeated a clarification that runtime policy rejected: "
                                    f"{policy_review.reason}"
                                )
                            continue
                        resolution = self._resolve_clarification_before_ask(
                            user_request,
                            completion_review.clarification_request,
                            observability,
                            phase="completion_review",
                            reason=completion_review.reason,
                        )
                        if resolution.decision == "continue_with_assumption":
                            policy_retry_count += 1
                            if policy_retry_count > 1:
                                raise ValueError(
                                    "Completion review repeated a clarification after typed resolution: "
                                    f"{resolution.reason}"
                                )
                            continue
                        if resolution.user_question.strip():
                            completion_review = completion_review.model_copy(
                                update={
                                    "clarification_request": (
                                        completion_review.clarification_request.model_copy(
                                            update={
                                                "question": resolution.user_question.strip(),
                                                "reason": resolution.reason
                                                or completion_review.clarification_request.reason,
                                                "confidence": max(
                                                    float(
                                                        completion_review.clarification_request.confidence
                                                        or 0.0
                                                    ),
                                                    float(resolution.confidence or 0.0),
                                                ),
                                            }
                                        )
                                    )
                                }
                            )
                    break
            except Exception as exc:
                self._emit(
                    observability,
                    level="error",
                    event_type=OPERATOR_REPAIR_REJECTED,
                    title="Operator completion review failed",
                    summary="The LLM could not review whether the requested end state was complete.",
                    details={"error": str(exc)},
                )
            else:
                if completion_review.decision == "complete":
                    self._emit(
                        observability,
                        level="info",
                        event_type=OPERATOR_COMPLETION_REVIEW_ACCEPTED,
                        title="Operator completion accepted",
                        summary="The LLM completion review accepted the executed end state.",
                        details={
                            "reason": completion_review.reason,
                            "confidence": completion_review.confidence,
                        },
                    )
                elif completion_review.decision == "ask_user":
                    if completion_review.clarification_request is None:
                        self._emit(
                            observability,
                            level="error",
                            event_type=OPERATOR_REPAIR_REJECTED,
                            title="Operator completion clarification rejected",
                            summary="The completion review requested clarification without a question.",
                            details={
                                "issues": list(completion_review.issues),
                                "reason": completion_review.reason,
                            },
                        )
                    else:
                        return self.require_clarification(
                            user_request,
                            completion_review.clarification_request,
                            observability,
                            plan=current_plan,
                            records=records,
                            phase="completion_review",
                        )
                elif completion_review.continuation_plan is None:
                    self._emit(
                        observability,
                        level="error",
                        event_type=OPERATOR_REPAIR_REJECTED,
                        title="Operator completion review rejected",
                        summary="The completion review requested more actions but did not provide a continuation plan.",
                        details={
                            "issues": list(completion_review.issues),
                            "reason": completion_review.reason,
                        },
                    )
                else:
                    continuation_plan = self._defer_dependent_python_actions(
                        completion_review.continuation_plan,
                        observability,
                    )
                    continuation_plan = self._normalize_plan_interactions(
                        continuation_plan,
                        observability,
                    )
                    continuation_errors = self._validate_plan(user_request, continuation_plan, observability)
                    if continuation_errors:
                        self._emit(
                            observability,
                            level="error",
                            event_type=OPERATOR_REPAIR_REJECTED,
                            title="Operator completion continuation rejected",
                            summary="The completion continuation plan failed validation.",
                            details={"errors": continuation_errors},
                        )
                    else:
                        self._emit(
                            observability,
                            level="warning",
                            event_type=OPERATOR_COMPLETION_REVIEW_CONTINUATION,
                            title="Operator completion continuation proposed",
                            summary=(
                                "The LLM determined the requested end state needs additional actions."
                            ),
                            details={
                                **continuation_plan.model_dump(mode="json"),
                                "issues": list(completion_review.issues),
                                "reason": completion_review.reason,
                                "requires_confirmation": self.plan_requires_confirmation(continuation_plan),
                            },
                        )
                        if self.plan_requires_confirmation(continuation_plan):
                            return self._completion_continuation_confirmation_result(
                                user_request,
                                continuation_plan,
                                records,
                                completion_review,
                                completion_review_count + 1,
                                observability,
                            )
                        continuation_context = dict(execution_context)
                        continuation_context["operator_completion_review_attempt_count"] = (
                            completion_review_count + 1
                        )
                        continuation_context["operator_seed_records"] = [
                            record.model_dump(mode="json") for record in records
                        ]
                        continuation_result = self.execute_approved(
                            user_request,
                            continuation_plan,
                            continuation_context,
                            observability,
                        )
                        seed_action_ids = {record.action_id for record in records}
                        continuation_records = [
                            record
                            for record in continuation_result.execution_records
                            if record.action_id not in seed_action_ids
                        ]
                        combined_result = continuation_result.model_copy(
                            update={"execution_records": [*records, *continuation_records]}
                        )
                        return self._attach_execution_learning_digest(
                            user_request,
                            combined_result,
                            observability,
                        )
        if (
            status == "success"
            and not streaming_step_scope
            and completion_review_count < self._max_completion_repair_attempts()
        ):
            evidence_review = self._complete_evidence_satisfaction_review(
                user_request,
                current_plan,
                records,
                observability,
            )
            if evidence_review is not None:
                if evidence_review.decision == "ask_user" and evidence_review.clarification_request is not None:
                    resolution = self._resolve_clarification_before_ask(
                        user_request,
                        evidence_review.clarification_request,
                        observability,
                        phase="guided_deliberation_evidence",
                        reason=evidence_review.reason,
                    )
                    if resolution.decision == "continue_with_assumption":
                        evidence_review = None
                    if resolution.user_question.strip():
                        evidence_review.clarification_request = (
                            evidence_review.clarification_request.model_copy(
                                update={
                                    "question": resolution.user_question.strip(),
                                    "reason": resolution.reason
                                    or evidence_review.clarification_request.reason,
                                    "confidence": max(
                                        float(
                                            evidence_review.clarification_request.confidence
                                            or 0.0
                                        ),
                                        float(resolution.confidence or 0.0),
                                    ),
                                }
                            )
                        )
                    if evidence_review is not None:
                        return self.require_clarification(
                            user_request,
                            evidence_review.clarification_request,
                            observability,
                            plan=current_plan,
                            records=records,
                            phase="guided_deliberation_evidence",
                        )
                if evidence_review is not None and evidence_review.decision == "fail":
                    markdown = "\n\n".join(
                        [
                            "## Deep Reasoning Review Failed",
                            evidence_review.evidence_summary,
                            evidence_review.reason,
                        ]
                    ).strip()
                    return OperatorPipelineResult(
                        status="error",
                        final_response=markdown,
                        plan=current_plan,
                        execution_records=records,
                        display_document=self._display_document(
                            user_request=user_request,
                            status="error",
                            markdown=markdown,
                            plan=current_plan,
                            records=records,
                        ),
                        metadata={
                            "guided_deliberation_evidence_failed": True,
                            "unsatisfied_criteria": list(evidence_review.unsatisfied_criteria),
                        },
                    )
                if evidence_review is not None and evidence_review.decision == "continue_required":
                    continuation_plan = self._normalize_plan_interactions(
                        self._defer_dependent_python_actions(
                            evidence_review.continuation_plan,
                            observability,
                        ),
                        observability,
                    )
                    continuation_errors = self._validate_plan(
                        user_request,
                        continuation_plan,
                        observability,
                    )
                    mutating_evidence_actions = [
                        action.action_id
                        for action in continuation_plan.actions
                        if self._action_requires_confirmation(continuation_plan, action)
                    ]
                    if mutating_evidence_actions:
                        continuation_errors.append(
                            {
                                "error": "guided_deliberation_evidence_mutating_continuation",
                                "message": (
                                    "Deep Reasoning evidence continuation may only gather "
                                    "read-only evidence or verification. It must not introduce "
                                    "new mutating work after a successful run."
                                ),
                                "action_ids": mutating_evidence_actions,
                                "repair_hint": (
                                    "Return fail or ask_user if the user goal needs another mutation; "
                                    "otherwise use read-only probes only."
                                ),
                            }
                        )
                    if continuation_errors:
                        self._emit(
                            observability,
                            level="error",
                            event_type=OPERATOR_DELIBERATION_SKIPPED,
                            title="Deep Reasoning continuation rejected",
                            summary="The evidence continuation plan failed validation.",
                            details={"errors": continuation_errors},
                        )
                    else:
                        self._emit(
                            observability,
                            level="warning",
                            event_type=OPERATOR_DELIBERATION_REPAIRED,
                            title="Deep Reasoning continuation proposed",
                            summary="The evidence review determined that more actions are needed before answering.",
                            details={
                                **continuation_plan.model_dump(mode="json"),
                                "evidence_summary": evidence_review.evidence_summary,
                                "next_step_guidance": evidence_review.next_step_guidance,
                                "requires_confirmation": self.plan_requires_confirmation(continuation_plan),
                            },
                        )
                        if self.plan_requires_confirmation(continuation_plan):
                            result = self.require_confirmation(
                                user_request,
                                continuation_plan,
                                observability,
                                records=records,
                            )
                            metadata = dict(result.metadata)
                            metadata.update(
                                {
                                    "guided_deliberation_pending": True,
                                    "guided_deliberation_reason": evidence_review.reason,
                                    "seed_record_count": len(records),
                                }
                            )
                            return result.model_copy(
                                update={"execution_records": list(records), "metadata": metadata}
                            )
                        continuation_context = dict(execution_context)
                        continuation_context["operator_completion_review_attempt_count"] = (
                            completion_review_count + 1
                        )
                        continuation_context["operator_seed_records"] = [
                            record.model_dump(mode="json") for record in records
                        ]
                        continuation_result = self.execute_approved(
                            user_request,
                            continuation_plan,
                            continuation_context,
                            observability,
                        )
                        seed_action_ids = {record.action_id for record in records}
                        continuation_records = [
                            record
                            for record in continuation_result.execution_records
                            if record.action_id not in seed_action_ids
                        ]
                        combined_result = continuation_result.model_copy(
                            update={"execution_records": [*records, *continuation_records]}
                        )
                        return self._attach_execution_learning_digest(
                            user_request,
                            combined_result,
                            observability,
                        )
        obligation_set: AnswerObligationSet | None = None
        coverage_review: AnswerCoverageReview | None = None
        compact_response = not self._operator_profile_policy().run_answer_coverage
        if status == "success" and self.reliability.enabled and not compact_response:
            obligation_set = self.reliability.audit_evidence(
                request_id=user_request.request_id,
                llm_client=self.llm_client,
                user_prompt=user_request.raw_prompt,
                plan=current_plan,
                records=records,
                context=user_request.session_context,
                source_preview_chars=self.config.llm_operator_formatter_source_preview_chars,
            )

        display_label = str(user_request.session_context.get("operator_display_label") or "Conversational")
        if status == "success" and (
            compact_response or self._final_response_mode() == "simple"
        ):
            final_response_mode = self._final_response_mode()
            markdown = (
                self._compact_execution_markdown(records)
                if compact_response and final_response_mode != "simple"
                else self._simple_execution_markdown()
            )
            if observability is not None:
                observability.info(
                    OPERATOR_STAGE,
                    "operator.final_formatter.skipped",
                    "Operator final formatter skipped",
                    (
                        "Compact operator profile returned deterministic execution output."
                        if compact_response
                        else "Simple final response mode returned a one-line completion response."
                    ),
                    details={
                        "final_response_mode": final_response_mode,
                        "compact_profile": compact_response,
                    },
                    debug_only=True,
                )
            if obligation_set is not None and obligation_set.audit_status == "complete":
                coverage_review = self.reliability.review_answer_coverage(
                    request_id=user_request.request_id,
                    llm_client=self.llm_client,
                    user_prompt=user_request.raw_prompt,
                    final_response=markdown,
                    obligation_set=obligation_set,
                    plan=current_plan,
                    records=records,
                    source_preview_chars=self.config.llm_operator_formatter_source_preview_chars,
                )
        else:
            if status == "success":
                self._attach_memory_for_stage(
                    user_request,
                    observability,
                    stage="final_answer",
                    source="final_answer",
                )
            formatter_result = (
                self._formatted_execution_markdown(
                    user_request,
                    records,
                    current_plan,
                    observability,
                    obligation_set=obligation_set,
                )
                if status == "success"
                else None
            )
            if formatter_result is not None:
                markdown = formatter_result.content
                coverage_review = formatter_result.coverage_review
            else:
                markdown = self._execution_markdown(
                    records,
                    current_plan,
                    display_label=display_label,
                )
                if obligation_set is not None and obligation_set.audit_status == "complete":
                    coverage_review = self.reliability.review_answer_coverage(
                        request_id=user_request.request_id,
                        llm_client=self.llm_client,
                        user_prompt=user_request.raw_prompt,
                        final_response=markdown,
                        obligation_set=obligation_set,
                        plan=current_plan,
                        records=records,
                        source_preview_chars=self.config.llm_operator_formatter_source_preview_chars,
                    )
        self._write_plan_cache_result(
            user_request,
            current_plan,
            records,
            markdown,
            status,
            observability,
        )
        result_metadata: dict[str, Any] = {}
        if streaming_step_scope:
            trace_metadata = _operator_planning_trace_metadata(user_request)
            if isinstance(trace_metadata, dict) and trace_metadata.get("operator_step_validation_checked"):
                result_metadata["operator_step_validation_checked"] = True
                result_metadata["operator_step_validations"] = list(
                    trace_metadata.get("operator_step_validations") or []
                )
        result = OperatorPipelineResult(
            status=status,
            final_response=markdown,
            plan=current_plan,
            execution_records=records,
            display_document=self._display_document(
                user_request=user_request,
                status=status,
                markdown=markdown,
                plan=current_plan,
                records=records,
            ),
            metadata=result_metadata,
        )
        verification = self.reliability.verify_outcome(
            request_id=user_request.request_id,
            status=status,
            records=records,
            final_response=markdown,
            obligation_set=obligation_set,
            coverage_review=coverage_review,
        )
        result = result.model_copy(
            update={
                "metadata": {
                    **dict(result.metadata or {}),
                    "reliability_verification": verification.model_dump(mode="json"),
                }
            }
        )
        return self._attach_execution_learning_digest(user_request, result, observability)



__all__ = ["_StepRunnerApprovedRunnerMixin"]
