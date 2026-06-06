"""Cache write helpers for the operator step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class _StepRunnerCacheWritesMixin:
    @staticmethod
    def _user_macro_contexts(user_request: UserRequest) -> list[dict[str, Any]]:
        return [
            context
            for context in (user_request.session_context, user_request.safety_context)
            if isinstance(context, dict)
        ]

    def _request_has_user_macro(self, user_request: UserRequest) -> bool:
        for context in self._user_macro_contexts(user_request):
            if private_user_macros_from_context(context) or user_macro_summaries_from_context(
                context
            ):
                return True
        return False

    def _command_template_learning_prompt(self, user_request: UserRequest) -> str:
        """Return prompt text safe to store with learned command-template metadata."""

        text = str(user_request.raw_prompt or "")
        if not self._request_has_user_macro(user_request):
            return text
        text = re.sub(
            r"(?i)(?:/?typein|type\s+in|tpein|tpe\s+in)\s*(?:\"(?:\\.|[^\"])*\"|\[[^\]]+\])?",
            "provided terminal input",
            text,
        )
        text = re.sub(
            r"(?i)\b(?:password|passwd|passphrase|token|secret|credential|credentials|auth|api[_-]?key|ssh[-_\s]?key|typein)\b",
            "terminal input",
            text,
        )
        return re.sub(r"\s+", " ", text).strip()

    def _command_template_learning_tags(
        self,
        user_request: UserRequest,
        tags: list[str],
    ) -> list[str]:
        if not self._request_has_user_macro(user_request):
            return list(tags)
        return [
            tag
            for tag in list(tags)
            if not _execution_learning_has_sensitive_text(tag)
        ]

    @staticmethod
    def _action_reuses_command_template_cache(action: OperatorAction) -> bool:
        """Return whether a serialized action was rebuilt from a command cache hit."""

        reason = str(action.reason or "").strip()
        return (
            reason.startswith("Reused learned command template cmdtpl-")
            or reason.startswith("Reused LR-EX payload-aware command template cmdtpl-")
            or reason.startswith(
                "LR Direct exact-step replay from command_template cache "
            )
            or reason.startswith(
                "LR Direct exact-step replay from payload_command_template cache "
            )
        )

    @staticmethod
    def _plan_cache_skeleton(
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
    ) -> OperatorPlan:
        """Strip generated deferred Python bodies before storing reusable plan shape."""

        generated_deferred_action_ids = {
            record.action_id
            for record in records
            if isinstance(record.metadata, dict)
            and bool(record.metadata.get("deferred_code_generated"))
        }
        if not generated_deferred_action_ids:
            return plan
        actions: list[OperatorAction] = []
        for action in plan.actions:
            if action.action_id in generated_deferred_action_ids and action.kind in {
                "python_action",
                "python_transform",
            }:
                actions.append(
                    action.model_copy(
                        update={
                            "code": None,
                            "defer_code_generation": True,
                        }
                    )
                )
            else:
                actions.append(action)
        return plan.model_copy(update={"actions": actions})

    def _write_plan_cache_result(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        final_response: str,
        status: str,
        observability: ObservabilityContext | None,
    ) -> None:
        """Persist bounded reusable plan structure after a completed operator run."""

        if not self._plan_cache_enabled(user_request):
            return
        assert self.plan_cache_store is not None
        brief = _operator_self_brief_from_request(user_request)
        lookup = self._plan_cache_context(user_request, brief=brief)
        failure_category = ""
        if status != "success":
            first_error = next(
                (record for record in records if record.status == "error"), None
            )
            failure_category = (
                str(first_error.error or first_error.stderr or "execution_error")[:160]
                if first_error is not None
                else "operator_error"
            )
        trace_metadata = _operator_planning_trace_metadata(user_request)
        exact_key = ""
        exact_excerpt = ""
        cache_prompt = user_request.raw_prompt
        intent_snapshot: dict[str, Any] = {}
        intent_signature = ""
        direct_plan: dict[str, Any] = {}
        if (
            status == "success"
            and isinstance(
                dict(user_request.session_context or {}).get(
                    "operator_streaming_current_task"
                ),
                dict,
            )
            and isinstance(trace_metadata, dict)
            and bool(trace_metadata.get("operator_step_validation_checked"))
            and all(record.status == "success" for record in records)
        ):
            exact_key, exact_excerpt = self._lrdirect_step_metadata(user_request)
            if exact_key:
                cache_prompt = self._streaming_step_prompt_for_cache(user_request)
                direct_plan = self._streaming_step_tree_payload(user_request, plan)
                if direct_plan:
                    intent_snapshot = self._streaming_step_intent_snapshot(
                        user_request, lookup=lookup
                    )
                    intent_signature = self._streaming_step_intent_signature(
                        intent_snapshot
                    )
        payload = PlanCacheWrite(
            prompt=cache_prompt,
            mode=lookup.mode,
            model_name=lookup.model_name,
            model_family=lookup.model_family,
            cwd=lookup.cwd,
            task_type=lookup.task_type,
            tool_type=lookup.tool_type,
            intent_type=lookup.intent_type,
            tags=lookup.tags,
            self_brief=brief.model_dump(mode="json") if brief is not None else {},
            plan=self._plan_cache_skeleton(plan, records).model_dump(mode="json"),
            records=[record.model_dump(mode="json") for record in records],
            final_response=final_response,
            exact_step_key=exact_key,
            exact_step_prompt_excerpt=exact_excerpt,
            intent_snapshot=intent_snapshot,
            intent_signature=intent_signature,
            direct_plan=direct_plan,
            status="success" if status == "success" else "failure",
            failure_category=failure_category,
            repair_notes=str(
                user_request.session_context.get("operator_repair_notes") or ""
            ),
        )
        try:
            entry = self.plan_cache_store.upsert_entry(payload)
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_PLAN_CACHE_WRITE,
                title="Operator cache write failed",
                summary="The private operator cache could not persist this run.",
                details={"error": str(exc)},
            )
            return
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_PLAN_CACHE_WRITE,
            title="Operator cache updated",
            summary="The private operator cache stored bounded reusable plan structure.",
            details={
                "cache_id": entry.cache_id,
                "status": entry.status,
                "success_count": entry.success_count,
                "failure_count": entry.failure_count,
                **(
                    {
                        "cache_type": "streaming_step_tree",
                        "exact_step_key": exact_key,
                        "intent_signature": intent_signature,
                        "action_count": len(list(direct_plan.get("actions") or [])),
                        "task_count": len(list(direct_plan.get("tasks") or [])),
                    }
                    if direct_plan
                    else {}
                ),
            },
        )

    def _write_streaming_step_action_tree_cache_result(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        result: OperatorPipelineResult,
        observability: ObservabilityContext | None,
    ) -> None:
        """Persist a successful validated streaming-step shell action tree for exact LR reuse."""

        if not self._streaming_step_tree_cache_enabled(user_request):
            return
        if result.status != "success":
            return
        if operator_request_requires_generated_text(user_request) and not any(
            action.kind == "llm_text" for action in plan.actions
        ):
            return
        metadata = dict(result.metadata or {})
        if not metadata.get("operator_step_validation_checked"):
            return
        if (
            str(metadata.get("operator_step_validation_decision") or "accept")
            != "accept"
        ):
            return
        records = list(result.execution_records or [])
        if not records or any(record.status != "success" for record in records):
            return
        direct_plan = self._streaming_step_tree_payload(user_request, plan)
        if not direct_plan:
            return
        exact_key, exact_excerpt = self._lrdirect_step_metadata(user_request)
        if not exact_key:
            return
        assert self.plan_cache_store is not None
        lookup = self._plan_cache_context(user_request)
        intent_snapshot = self._streaming_step_intent_snapshot(
            user_request, lookup=lookup
        )
        intent_signature = self._streaming_step_intent_signature(intent_snapshot)
        cache_prompt = self._streaming_step_prompt_for_cache(user_request)
        payload = PlanCacheWrite(
            prompt=cache_prompt,
            mode=lookup.mode,
            model_name=lookup.model_name,
            model_family=lookup.model_family,
            cwd=lookup.cwd,
            task_type=lookup.task_type,
            tool_type=lookup.tool_type,
            intent_type=lookup.intent_type,
            tags=lookup.tags,
            plan={},
            records=[],
            final_response="",
            exact_step_key=exact_key,
            exact_step_prompt_excerpt=exact_excerpt,
            intent_snapshot=intent_snapshot,
            intent_signature=intent_signature,
            direct_plan=direct_plan,
            status="success",
            repair_notes="Exact streaming-step shell action tree.",
        )
        try:
            entry = self.plan_cache_store.upsert_entry(payload)
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_PLAN_CACHE_WRITE,
                title="Streaming step tree cache write failed",
                summary="The exact streaming-step action-tree LR cache could not persist this run.",
                details={
                    "cache_type": "streaming_step_tree",
                    "exact_step_key": exact_key,
                    "intent_signature": intent_signature,
                    "error": str(exc),
                },
            )
            return
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_PLAN_CACHE_WRITE,
            title="Streaming step tree cache updated",
            summary="Regular LR stored a validated streaming-step command tree for exact reuse.",
            details={
                "cache_type": "streaming_step_tree",
                "cache_id": entry.cache_id,
                "exact_step_key": exact_key,
                "intent_signature": intent_signature,
                "action_count": len(list(direct_plan.get("actions") or [])),
                "task_count": len(list(direct_plan.get("tasks") or [])),
                "status": entry.status,
                "success_count": entry.success_count,
                "failure_count": entry.failure_count,
            },
        )

    def _write_computation_cache_result(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        action: OperatorAction,
        action_inputs: dict[str, Any],
        record: OperatorExecutionRecord,
        observability: ObservabilityContext | None,
    ) -> None:
        """Persist a reusable computation template after generated Python runs."""

        if not self._computation_cache_enabled(user_request):
            return
        if (
            action.kind not in {"python_action", "python_transform"}
            or not str(action.code or "").strip()
        ):
            return
        if operator_action_requires_confirmation(
            action,
            policy_mode=operator_policy_mode(self.config, "effect"),
        ):
            return
        if not operator_python_has_nonempty_inputs(action, action_inputs):
            return
        assert self.computation_cache_store is not None
        lookup = self._computation_cache_context(user_request, action, action_inputs)
        status = "success" if record.status == "success" else "failure"
        if status != "success":
            applied_direct = dict(user_request.session_context or {}).get(
                "operator_lrdirect_computation_codegen"
            )
            if isinstance(applied_direct, dict):
                cache_id = str(applied_direct.get("cache_id") or "").strip()
                if cache_id:
                    self.computation_cache_store.mark_failed(
                        cache_id,
                        failure_category=str(
                            record.error or record.stderr or "python_execution_error"
                        )[:300],
                        repair_notes=str(
                            user_request.session_context.get("operator_repair_notes")
                            or ""
                        ),
                    )
            return
        failure_category = ""
        exact_key, exact_excerpt = self._lrdirect_step_metadata(user_request)
        direct_action = (
            self._lrdirect_whole_step_action_payload(plan, action) if exact_key else {}
        )
        payload = ComputationCacheWrite(
            prompt=user_request.raw_prompt,
            mode=lookup.mode,
            model_name=lookup.model_name,
            model_family=lookup.model_family,
            task_type=lookup.task_type,
            tool_type=lookup.tool_type,
            intent_type=lookup.intent_type,
            tags=lookup.tags,
            action_kind=action.kind,
            action_reason=action.reason,
            input_profile=lookup.input_profile,
            input_signature=lookup.input_signature,
            exact_step_key=exact_key,
            exact_step_prompt_excerpt=exact_excerpt,
            code_template=str(action.code or ""),
            direct_action=direct_action,
            declared_output_shape=action.declared_output_shape,
            allow_zero_result=action.allow_zero_result,
            template_reason=action.reason,
            output=record.output if record.output is not None else record.stdout,
            status=status,
            failure_category=failure_category,
            repair_notes=str(
                user_request.session_context.get("operator_repair_notes") or ""
            ),
        )
        try:
            entry = self.computation_cache_store.upsert_entry(payload)
        except Exception as exc:
            self._emit_computation_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMPUTATION_CACHE_WRITE,
                title="Computation cache write failed",
                summary="The private computation template cache could not persist this generated code.",
                details={"action_id": action.action_id, "error": str(exc)},
                level="warning",
            )
            return
        self._emit_computation_cache(
            user_request,
            observability,
            event_type=OPERATOR_COMPUTATION_CACHE_WRITE,
            title="Computation cache updated",
            summary="The private computation template cache stored bounded reusable generated code.",
            details={
                "cache_id": entry.cache_id,
                "action_id": action.action_id,
                "status": entry.status,
                "success_count": entry.success_count,
                "failure_count": entry.failure_count,
            },
        )
        if exact_key and direct_action:
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_WRITE,
                title="LR Direct Python cached",
                summary="A successful one-action streaming Python action was cached for exact replay.",
                details={
                    "cache_type": "computation",
                    "cache_id": entry.cache_id,
                    "exact_step_key": exact_key,
                    "action_kind": action.kind,
                    "bypassed_llm": False,
                },
            )

    def _write_command_template_cache_result(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        action: OperatorAction,
        record: OperatorExecutionRecord,
        execution_context: dict[str, Any],
        observability: ObservabilityContext | None,
    ) -> None:
        """Persist a learned reusable shell command template after success."""

        if not self._command_template_cache_enabled(user_request):
            return
        applied_lrdirect = dict(user_request.session_context or {}).get(
            "operator_lrdirect_applied"
        )
        if (
            isinstance(applied_lrdirect, dict)
            and applied_lrdirect.get("bypassed_llm") is True
        ):
            return
        applied_command_template = isinstance(
            dict(user_request.session_context or {}).get(
                "operator_command_template_cache_applied"
            ),
            dict,
        )
        reused_command_template = self._action_reuses_command_template_cache(action)
        if action.kind != "shell_command" or record.status != "success":
            return
        interaction_mode = str(action.interaction_mode or "non_interactive")
        if interaction_mode not in {"non_interactive", "may_prompt"}:
            return
        if action.execution_mode != "captured":
            return
        command = str(action.command or "").strip()
        if not command:
            return
        requires_confirmation = self._action_requires_confirmation(plan, action)
        approval_observed = bool(execution_context.get("confirmation"))
        if requires_confirmation and not approval_observed:
            return
        learning_prompt = self._command_template_learning_prompt(user_request)
        if _execution_learning_has_sensitive_text(
            learning_prompt,
            command,
            _stable_json(action.inputs),
            record.stdout,
            record.stderr,
        ):
            return
        assert self.command_template_cache_store is not None
        if action.input_bindings or action.stdin_mode != "none":
            if applied_command_template or reused_command_template:
                return
            if self._write_lrex_command_template_cache_result(
                user_request,
                plan,
                action,
                record,
                requires_confirmation=requires_confirmation,
                approval_observed=approval_observed,
                observability=observability,
            ):
                return
            return
        if operator_request_requires_generated_text(user_request):
            return
        exact_key = ""
        exact_excerpt = ""
        direct_action: dict[str, Any] = {}
        if self._lrdirect_enabled(user_request):
            exact_key, exact_excerpt = self._lrdirect_step_metadata(user_request)
            direct_action = self._lrdirect_whole_step_action_payload(plan, action)
        if exact_key and direct_action:
            lookup = self._command_template_cache_context(user_request)
            task = next(
                (item for item in plan.tasks if item.task_id == action.task_id), None
            )
            try:
                direct_entry = self.command_template_cache_store.upsert_entry(
                    CommandTemplateWrite(
                        prompt=learning_prompt,
                        step_description=lookup.step_description,
                        mode=lookup.mode,
                        model_name=lookup.model_name,
                        model_family=lookup.model_family,
                        cwd=str(
                            record.metadata.get("cwd") or action.cwd or lookup.cwd or ""
                        ),
                        gateway_platform=lookup.gateway_platform,
                        task_type=str(
                            task.object_type if task is not None else lookup.task_type
                        ),
                        tool_type=str(
                            task.object_type if task is not None else lookup.tool_type
                        ),
                        intent_type=str(
                            task.semantic_verb
                            if task is not None
                            else lookup.intent_type
                        ),
                        interaction_mode=interaction_mode,
                        tags=self._command_template_learning_tags(
                            user_request, lookup.tags
                        ),
                        exact_step_key=exact_key,
                        exact_step_prompt_excerpt=exact_excerpt,
                        command_template=command,
                        variables=[],
                        direct_action=direct_action,
                        observed_command=command,
                        risk=action.risk,
                        effect_intent=action.effect_intent,
                        effect_summary=action.effect_summary,
                        requires_confirmation=requires_confirmation,
                        approval_observed=approval_observed,
                        status="success",
                        repair_notes="LR Direct exact streaming-step command action.",
                    )
                )
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_WRITE,
                    title="LR Direct command cached",
                    summary="A successful one-action streaming command was cached for exact replay.",
                    details={
                        "cache_type": "command_template",
                        "template_id": direct_entry.template_id,
                        "exact_step_key": exact_key,
                        "action_kind": action.kind,
                        "bypassed_llm": False,
                    },
                )
            except Exception as exc:
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_REJECTED,
                    title="LR Direct command cache write failed",
                    summary="The exact command replay cache could not persist this successful action.",
                    details={
                        "cache_type": "command_template",
                        "exact_step_key": exact_key,
                        "action_id": action.action_id,
                        "error": str(exc),
                    },
                    level="warning",
                )
        if applied_command_template or reused_command_template:
            return
        try:
            draft = structured_call(
                self.llm_client,
                build_operator_command_template_draft_prompt(
                    user_request, plan, action, record
                ),
                OperatorCommandTemplateDraft,
            )
        except Exception as exc:
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_WRITE,
                title="Command template learning skipped",
                summary="The LLM could not draft a valid command template from the successful command.",
                details={"action_id": action.action_id, "error": str(exc)},
                level="warning",
            )
            return
        if draft.decision != "store_template" or draft.confidence < 0.6:
            return
        command_template = str(draft.command_template or "").strip()
        referenced_names = extract_template_input_names(command_template)
        variables = [item.model_dump(mode="json") for item in draft.variables]
        variables = normalize_template_variables(variables)
        values = {
            str(item.get("name") or ""): str(item.get("observed_value") or "")
            for item in variables
            if str(item.get("name") or "")
        }
        action_input_values = {
            normalize_template_variable_name(name): str(value)
            for name, value in dict(action.inputs or {}).items()
            if normalize_template_variable_name(name)
        }
        for name in referenced_names:
            if not values.get(name) and name in action_input_values:
                values[name] = action_input_values[name]
        if any(
            _execution_learning_has_sensitive_text(name, value)
            for name, value in values.items()
        ):
            return
        if any(not values.get(name) for name in referenced_names):
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_WRITE,
                title="Command template learning skipped",
                summary="The drafted command template omitted observed values for one or more variables.",
                details={
                    "action_id": action.action_id,
                    "referenced_names": referenced_names,
                },
                level="warning",
            )
            return
        if referenced_names:
            variables_by_name = {
                str(item.get("name") or ""): dict(item) for item in variables
            }
            variables = [
                {
                    **variables_by_name.get(name, {"name": name, "description": ""}),
                    "observed_value": values[name],
                }
                for name in referenced_names
            ]
        expected = normalize_operator_command(
            render_template_with_values(command, action_input_values)
        )
        actual = normalize_operator_command(
            render_template_with_values(command_template, values)
        )
        if expected != actual:
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_WRITE,
                title="Command template learning rejected",
                summary="The drafted command template did not reproduce the successful command when rendered.",
                details={
                    "action_id": action.action_id,
                    "expected_command": expected,
                    "rendered_template": actual,
                    "reason": draft.reason,
                },
                level="warning",
            )
            return
        lookup = self._command_template_cache_context(user_request)
        task = next(
            (item for item in plan.tasks if item.task_id == action.task_id), None
        )
        tags = self._command_template_learning_tags(user_request, lookup.tags)
        tags.extend(
            _memory_tag_tokens(
                " ".join(
                    [
                        learning_prompt,
                        str(task.goal if task is not None else ""),
                        str(task.semantic_verb if task is not None else ""),
                        str(task.object_type if task is not None else ""),
                        _shell_tool_name(command),
                        command_template,
                    ]
                ),
                limit=40,
            )
        )
        payload = CommandTemplateWrite(
            prompt=learning_prompt,
            step_description=lookup.step_description,
            mode=lookup.mode,
            model_name=lookup.model_name,
            model_family=lookup.model_family,
            cwd=str(record.metadata.get("cwd") or action.cwd or lookup.cwd or ""),
            gateway_platform=lookup.gateway_platform,
            task_type=str(task.object_type if task is not None else lookup.task_type),
            tool_type=str(task.object_type if task is not None else lookup.tool_type),
            intent_type=str(
                task.semantic_verb if task is not None else lookup.intent_type
            ),
            interaction_mode=interaction_mode,
            tags=_memory_tag_tokens(" ".join(tags), limit=40),
            command_template=command_template,
            variables=variables,
            observed_command=command,
            risk=action.risk,
            effect_intent=action.effect_intent,
            effect_summary=action.effect_summary,
            requires_confirmation=requires_confirmation,
            approval_observed=approval_observed,
            status="success",
            repair_notes=draft.reason,
        )
        try:
            entry = self.command_template_cache_store.upsert_entry(payload)
        except Exception as exc:
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_WRITE,
                title="Command template cache write failed",
                summary="The learned command template cache could not persist this command.",
                details={"action_id": action.action_id, "error": str(exc)},
                level="warning",
            )
            return
        self._emit_command_template_cache(
            user_request,
            observability,
            event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_WRITE,
            title="Command template learned",
            summary="The runtime learned a reusable shell command template from a successful command.",
            details={
                "template_id": entry.template_id,
                "action_id": action.action_id,
                "status": entry.status,
                "success_count": entry.success_count,
                "failure_count": entry.failure_count,
                "requires_confirmation": entry.requires_confirmation,
                "approval_observed": entry.approval_observed,
            },
        )

    def _lrex_payload_bindings_for_action(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        action: OperatorAction,
    ) -> list[dict[str, Any]]:
        """Return safe LR-EX payload binding metadata for one shell action."""

        if action.kind != "shell_command":
            return []
        stdin_mode = str(action.stdin_mode or "none").strip() or "none"
        if stdin_mode not in {"none", "input_binding"}:
            return []
        if (
            stdin_mode == "input_binding"
            and not str(action.stdin_input_name or "").strip()
        ):
            return []
        stdin_input_name = normalize_template_variable_name(action.stdin_input_name)
        if stdin_mode == "input_binding" and not action.input_bindings:
            if dict(action.inputs or {}):
                return []
            if not stdin_input_name:
                return []
            if not any(
                is_user_macro_input_name(stdin_input_name, context)
                for context in self._user_macro_contexts(user_request)
            ):
                return []
            return normalize_payload_bindings(
                [
                    {
                        "input_name": stdin_input_name,
                        "source_role": "user_macro",
                        "source_field": "value",
                        "stdin_mode": "input_binding",
                        "generated_text": False,
                    }
                ]
            )
        if not action.input_bindings:
            return []
        if dict(action.inputs or {}):
            return []
        source_action_by_id = {
            str(item.action_id): item for item in list(plan.actions or [])
        }
        bindings: list[dict[str, Any]] = []
        seen_inputs: set[str] = set()
        for binding in list(action.input_bindings or []):
            input_name = normalize_template_variable_name(binding.input_name)
            if not input_name or input_name in seen_inputs:
                return []
            seen_inputs.add(input_name)
            source_action = source_action_by_id.get(str(binding.source_action_id or ""))
            generated_text = bool(
                source_action is not None and source_action.kind == "llm_text"
            )
            if not generated_text:
                prior = self._streaming_prior_record_for_source(
                    user_request,
                    str(binding.source_action_id or ""),
                )
                if prior is not None:
                    prior_record, _task = prior
                    prior_kind = str(
                        self._execution_record_value(prior_record, "kind") or ""
                    )
                    metadata = self._execution_record_value(prior_record, "metadata")
                    metadata = metadata if isinstance(metadata, dict) else {}
                    generated_text = (
                        prior_kind == "llm_text"
                        or bool(metadata.get("llm_text"))
                        or bool(metadata.get("generated_text"))
                        or str(metadata.get("source") or "").strip().lower()
                        == "generated"
                    )
            if not generated_text and operator_request_requires_generated_text(
                user_request
            ):
                generated_text = self._input_name_looks_generated_text(input_name)
            binding_stdin_mode = (
                "input_binding"
                if stdin_mode == "input_binding"
                and input_name
                == normalize_template_variable_name(action.stdin_input_name)
                else "none"
            )
            bindings.append(
                {
                    "input_name": input_name,
                    "source_role": (
                        "generated_text" if generated_text else "runtime_output"
                    ),
                    "source_field": str(binding.source_field or "stdout"),
                    "stdin_mode": binding_stdin_mode,
                    "generated_text": generated_text,
                }
            )
        if stdin_mode == "input_binding" and not any(
            str(item.get("stdin_mode") or "") == "input_binding" for item in bindings
        ):
            return []
        return normalize_payload_bindings(bindings)

    @staticmethod
    def _lrex_direct_action_payload(
        plan: OperatorPlan, action: OperatorAction
    ) -> dict[str, Any]:
        """Return a sanitized exact-step payload for LR-EX direct replay."""

        if len(list(plan.actions or [])) != 1:
            return {}
        payload = action.model_dump(mode="json")
        for run_key in ("action_id", "task_id", "depends_on"):
            payload.pop(run_key, None)
        if dict(payload.get("inputs") or {}):
            return {}
        bindings: list[dict[str, Any]] = []
        for binding in list(action.input_bindings or []):
            bindings.append(
                {
                    "input_name": binding.input_name,
                    "source": "prior_streaming_result",
                    "source_field": binding.source_field,
                    "required": binding.required,
                    "fallback_value": binding.fallback_value,
                }
            )
        payload["input_bindings"] = bindings
        payload["lrex_schema_version"] = 1
        return payload

    def _write_lrex_command_template_cache_result(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        action: OperatorAction,
        record: OperatorExecutionRecord,
        *,
        requires_confirmation: bool,
        approval_observed: bool,
        observability: ObservabilityContext | None,
    ) -> bool:
        """Persist an LR-EX payload-aware command template after success."""

        command = str(action.command or "").strip()
        if not command:
            return False
        if requires_confirmation and not approval_observed:
            return False
        payload_bindings = self._lrex_payload_bindings_for_action(
            user_request, plan, action
        )
        if not payload_bindings:
            return False
        if _execution_learning_has_sensitive_text(
            command, action.effect_summary, action.reason
        ):
            return False
        learning_prompt = self._command_template_learning_prompt(user_request)
        lookup = self._command_template_cache_context(user_request)
        task = next(
            (item for item in plan.tasks if item.task_id == action.task_id), None
        )
        tags = self._command_template_learning_tags(user_request, lookup.tags)
        tags.extend(
            _memory_tag_tokens(
                " ".join(
                    [
                        learning_prompt,
                        str(task.goal if task is not None else ""),
                        str(task.semantic_verb if task is not None else ""),
                        str(task.object_type if task is not None else ""),
                        _shell_tool_name(command),
                        command,
                        "lr_ex payload command template",
                    ]
                ),
                limit=40,
            )
        )
        exact_key, exact_excerpt = self._lrdirect_step_metadata(user_request)
        direct_action = (
            self._lrex_direct_action_payload(plan, action) if exact_key else {}
        )
        payload = CommandTemplateWrite(
            prompt=learning_prompt,
            step_description=lookup.step_description,
            mode=lookup.mode,
            model_name=lookup.model_name,
            model_family=lookup.model_family,
            cwd=str(record.metadata.get("cwd") or action.cwd or lookup.cwd or ""),
            gateway_platform=lookup.gateway_platform,
            task_type=str(task.object_type if task is not None else lookup.task_type),
            tool_type=str(task.object_type if task is not None else lookup.tool_type),
            intent_type=str(
                task.semantic_verb if task is not None else lookup.intent_type
            ),
            interaction_mode=str(action.interaction_mode or "non_interactive"),
            tags=_memory_tag_tokens(" ".join(tags), limit=40),
            lr_mode=LR_MODE_PAYLOAD,
            payload_bindings=payload_bindings,
            exact_step_key=exact_key if direct_action else "",
            exact_step_prompt_excerpt=exact_excerpt if direct_action else "",
            command_template=command,
            variables=[],
            direct_action=direct_action,
            observed_command=command,
            risk=action.risk,
            effect_intent=action.effect_intent,
            effect_summary=action.effect_summary,
            requires_confirmation=requires_confirmation,
            approval_observed=approval_observed,
            status="success",
            repair_notes="LR-EX payload-aware command template.",
        )
        try:
            entry = self.command_template_cache_store.upsert_entry(payload)
        except Exception as exc:
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_WRITE,
                title="LR-EX command template cache write failed",
                summary="The payload-aware command template cache could not persist this command.",
                details={
                    "action_id": action.action_id,
                    "lr_mode": LR_MODE_PAYLOAD,
                    "cache_type": "payload_command_template",
                    "error": str(exc),
                },
                level="warning",
            )
            return False
        details = {
            "template_id": entry.template_id,
            "action_id": action.action_id,
            "status": entry.status,
            "success_count": entry.success_count,
            "failure_count": entry.failure_count,
            "requires_confirmation": entry.requires_confirmation,
            "approval_observed": entry.approval_observed,
            "lr_mode": LR_MODE_PAYLOAD,
            "cache_type": "payload_command_template",
            "payload_binding_count": len(entry.payload_bindings or []),
            "payload_binding_kinds": self._lrex_payload_binding_kinds(
                entry.payload_bindings
            ),
        }
        self._emit_command_template_cache(
            user_request,
            observability,
            event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_WRITE,
            title="LR-EX command template learned",
            summary="The runtime learned a payload-aware shell command template.",
            details=details,
        )
        if exact_key and direct_action:
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_WRITE,
                title="LR-EX direct command cached",
                summary="A successful payload-aware streaming command was cached for exact replay.",
                details={
                    **details,
                    "exact_step_key": exact_key,
                    "action_kind": action.kind,
                    "bypassed_llm": False,
                },
            )
        return True


__all__ = ["_StepRunnerCacheWritesMixin"]
