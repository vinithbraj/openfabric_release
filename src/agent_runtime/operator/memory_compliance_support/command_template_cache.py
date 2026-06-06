"""Command template cache helpers for operator memory compliance."""

from __future__ import annotations

from agent_runtime.operator.memory_compliance_support.common import *
from agent_runtime.operator.memory_compliance_support.common import (
    _MemoryComplianceCommonMixin,
)


class _MemoryComplianceCommandTemplateCacheMixin(_MemoryComplianceCommonMixin):
    @staticmethod
    def _cached_shell_interaction_mode(entry: CommandTemplateEntry) -> str:
        mode = str(getattr(entry, "interaction_mode", "") or "").strip()
        return mode if mode in {"non_interactive", "may_prompt"} else "non_interactive"

    def _command_template_cache_enabled(self, user_request: UserRequest) -> bool:
        """Return whether learned command templates may be used for this request."""

        if self.command_template_cache_store is None or not bool(
            self.config.agent_command_template_cache_enabled
        ):
            return False
        if self._step_validation_repair_retry_active(user_request):
            return False
        context = dict(user_request.session_context or {})
        if "agent_command_template_cache_enabled" in context:
            return bool(context.get("agent_command_template_cache_enabled"))
        return True

    def _command_template_cache_context(
        self,
        user_request: UserRequest,
    ) -> CommandTemplateLookupContext:
        """Build the typed cache lookup shape for learned command templates."""

        context = dict(user_request.session_context or {})
        model_name = self._active_memory_model_name(user_request)
        mode = str(
            context.get("agent_mode") or context.get("operator_mode_label") or ""
        ).strip()
        current_task = context.get("operator_streaming_current_task")
        current_task = current_task if isinstance(current_task, dict) else {}
        step_description = str(
            current_task.get("description") or user_request.raw_prompt or ""
        ).strip()
        task_type = str(current_task.get("object_type") or "").strip()
        intent_type = str(current_task.get("semantic_verb") or "").strip()
        tags = _memory_tag_tokens(
            " ".join(
                [
                    user_request.raw_prompt,
                    step_description,
                    task_type,
                    intent_type,
                    str(context.get("gateway_platform") or ""),
                    str(context.get("gateway_shell") or ""),
                ]
            ),
            limit=40,
        )
        brief = _operator_self_brief_from_request(user_request)
        if brief is not None:
            tags.extend(self._memory_tags_from_self_brief(user_request, brief))
        return CommandTemplateLookupContext(
            prompt=user_request.raw_prompt,
            step_description=step_description,
            mode=mode,
            model_name=model_name,
            model_family=normalize_model_family(model_name),
            cwd=str(context.get("terminal_cwd") or ""),
            gateway_platform=str(context.get("gateway_platform") or ""),
            task_type=task_type,
            tool_type=task_type,
            intent_type=intent_type,
            tags=_memory_tag_tokens(" ".join(tags), limit=40),
            max_chars=int(self.config.agent_command_template_cache_prompt_max_chars),
            similarity_threshold=float(
                self.config.agent_command_template_cache_similarity_threshold
            ),
            secondary_similarity_threshold=float(
                self.config.agent_command_template_cache_secondary_similarity_threshold
            ),
            excluded_template_ids=[
                str(item)
                for item in list(
                    context.get("learning_ledger_suspect_command_template_cache_ids")
                    or []
                )
                if str(item or "").strip()
            ],
        )

    def _emit_command_template_cache(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
        *,
        event_type: str,
        title: str,
        summary: str,
        details: dict[str, Any],
        level: str = "info",
    ) -> None:
        """Emit learned command-template cache status into the developer trace."""

        trace = user_request.safety_context.get("planning_trace")
        metadata = getattr(trace, "metadata", None)
        if isinstance(metadata, dict):
            metadata["operator_command_template_cache"] = dict(details)
        self._emit(
            observability,
            level=level,
            event_type=event_type,
            title=title,
            summary=summary,
            details=details,
        )

    def _command_template_plan_from_decision(
        self,
        user_request: UserRequest,
        candidate: CommandTemplateCandidate,
        decision: OperatorCommandTemplateDecision,
    ) -> OperatorPlan | None:
        """Return a one-action plan rendered from a learned command template."""

        entry = candidate.entry
        referenced_names = extract_template_input_names(entry.command_template)
        variables = {
            normalize_template_variable_name(name): str(value)
            for name, value in dict(decision.variables or {}).items()
            if normalize_template_variable_name(name)
        }
        if any(
            _execution_learning_has_sensitive_text(name, value)
            for name, value in variables.items()
        ):
            return None
        for name in referenced_names:
            if name not in variables or variables[name] == "":
                return None
        context = dict(user_request.session_context or {})
        current_task = context.get("operator_streaming_current_task")
        current_task = current_task if isinstance(current_task, dict) else {}
        task_id = str(
            current_task.get("task_id")
            or current_task.get("id")
            or "task_command_template"
        )
        semantic_verb = str(
            current_task.get("semantic_verb") or entry.intent_type or "execute"
        )
        object_type = str(
            current_task.get("object_type")
            or entry.task_type
            or entry.tool_type
            or "shell"
        )
        task_goal = str(
            current_task.get("description")
            or entry.step_excerpt
            or user_request.raw_prompt
        )
        action_id = new_id("action")
        action = OperatorAction(
            action_id=action_id,
            task_id=task_id,
            kind="shell_command",
            command=entry.command_template,
            execution_mode="captured",
            interaction_mode=self._cached_shell_interaction_mode(entry),
            cwd=str(context.get("terminal_cwd") or entry.cwd or "."),
            inputs={name: variables[name] for name in referenced_names},
            input_bindings=[],
            stdin_mode="none",
            declared_output_shape="text",
            risk=(
                entry.risk
                if entry.risk in {"low", "medium", "high", "critical"}
                else "medium"
            ),
            effect_intent=(
                entry.effect_intent
                if entry.effect_intent in {"read_only", "mutates_state", "unknown"}
                else "unknown"
            ),
            effect_confidence=0.8 if entry.effect_intent else 0.0,
            effect_summary=entry.effect_summary
            or "Reused learned shell command template.",
            timeout_seconds=None,
            reason=f"Reused learned command template {entry.template_id}.",
            depends_on=[],
        )
        task = OperatorTask(
            task_id=task_id,
            goal=task_goal,
            semantic_verb=semantic_verb,
            object_type=object_type,
            dependencies=[],
            reason="Matched a learned command template for the current request.",
        )
        return OperatorPlan(
            summary="Use a learned command template for this request.",
            tasks=[task],
            actions=[action],
            dependencies=[],
            expected_outputs=["Command output from the learned template."],
            assumptions=[decision.reason] if decision.reason else [],
            confidence=min(candidate.score, decision.confidence),
        )

    @staticmethod
    def _lrex_payload_binding_kinds(
        payload_bindings: list[dict[str, Any]],
    ) -> list[str]:
        kinds: list[str] = []
        for binding in normalize_payload_bindings(payload_bindings):
            role = str(binding.get("source_role") or "runtime_output")
            mode = str(binding.get("stdin_mode") or "none")
            kinds.append(f"{role}:{mode}")
        return sorted(set(kinds))

    @staticmethod
    def _lrex_candidate_is_generated_text(candidate: dict[str, Any]) -> bool:
        metadata = candidate.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        return (
            str(candidate.get("kind") or "").strip() == "llm_text"
            or bool(metadata.get("llm_text"))
            or bool(metadata.get("generated_text"))
            or str(metadata.get("source") or "").strip().lower() == "generated"
        )

    def _lrex_runtime_payload_sources_available(self, user_request: UserRequest) -> bool:
        if self._streaming_prior_output_candidates(user_request):
            return True
        return any(
            bool(private_user_macros_from_context(context))
            for context in (user_request.session_context, user_request.safety_context)
            if isinstance(context, dict)
        )

    def _lrex_plan_from_entry(
        self,
        user_request: UserRequest,
        entry: CommandTemplateEntry,
        *,
        cache_id: str,
        prior_binding_map: dict[str, str] | None = None,
    ) -> OperatorPlan | None:
        """Rebuild a payload-aware command template with current prior payloads."""

        if normalize_lr_mode(entry.lr_mode) != LR_MODE_PAYLOAD:
            return None
        payload_bindings = normalize_payload_bindings(entry.payload_bindings)
        if not payload_bindings:
            return None
        command = str(entry.command_template or "").strip()
        if not command:
            return None
        if _execution_learning_has_sensitive_text(command):
            return None
        context = dict(user_request.session_context or {})
        current_task = context.get("operator_streaming_current_task")
        current_task = current_task if isinstance(current_task, dict) else {}
        task_id = str(
            current_task.get("task_id")
            or current_task.get("id")
            or "task_command_template"
        )
        semantic_verb = str(
            current_task.get("semantic_verb") or entry.intent_type or "execute"
        )
        object_type = str(
            current_task.get("object_type")
            or entry.task_type
            or entry.tool_type
            or "shell"
        )
        task_goal = str(
            current_task.get("description")
            or entry.step_excerpt
            or user_request.raw_prompt
        )
        action_id = new_id("action")
        scoring_action = OperatorAction(
            action_id=action_id,
            task_id=task_id,
            kind="shell_command",
            command=command,
            execution_mode="captured",
            interaction_mode=self._cached_shell_interaction_mode(entry),
            cwd=str(context.get("terminal_cwd") or entry.cwd or "."),
            inputs={},
            input_bindings=[],
            stdin_mode="none",
            declared_output_shape="text",
            risk=(
                entry.risk
                if entry.risk in {"low", "medium", "high", "critical"}
                else "medium"
            ),
            effect_intent=(
                entry.effect_intent
                if entry.effect_intent in {"read_only", "mutates_state", "unknown"}
                else "unknown"
            ),
            effect_confidence=0.8 if entry.effect_intent else 0.0,
            effect_summary=entry.effect_summary
            or "Reused payload-aware learned shell command template.",
            timeout_seconds=None,
            reason=f"Reused LR-EX payload-aware command template {entry.template_id}.",
            depends_on=[],
        )
        candidates: list[dict[str, Any]] | None = None
        rebuilt_bindings: list[dict[str, Any]] = []
        depends_on: list[str] = []
        seen_inputs: set[str] = set()
        stdin_input_name = ""
        for binding in payload_bindings:
            input_name = str(binding.get("input_name") or "").strip()
            if not input_name or input_name in seen_inputs:
                return None
            seen_inputs.add(input_name)
            source_field = (
                str(binding.get("source_field") or "stdout").strip() or "stdout"
            )
            source_role = str(binding.get("source_role") or "runtime_output").strip()
            stdin_mode = str(binding.get("stdin_mode") or "none").strip()
            if source_role == "user_macro":
                if stdin_mode != "input_binding":
                    return None
                if not any(
                    is_user_macro_input_name(input_name, context)
                    for context in (
                        user_request.session_context,
                        user_request.safety_context,
                    )
                    if isinstance(context, dict)
                ):
                    return None
                if stdin_input_name:
                    return None
                stdin_input_name = input_name
                continue
            if candidates is None:
                candidates = self._streaming_prior_output_candidates(user_request)
            if not candidates:
                return None
            matching_candidates = [
                candidate
                for candidate in candidates
                if str(candidate.get("source_field") or "stdout") == source_field
            ]
            if source_role == "generated_text" or bool(binding.get("generated_text")):
                matching_candidates = [
                    candidate
                    for candidate in matching_candidates
                    if self._lrex_candidate_is_generated_text(candidate)
                ]
            if not matching_candidates:
                return None
            candidate = self._lrex_prior_candidate_from_shape_match(
                user_request,
                scoring_action,
                matching_candidates,
                input_name=input_name,
                source_field=source_field,
                prior_binding_map=prior_binding_map,
            )
            if candidate is None:
                candidate = self._best_streaming_prior_candidate(
                    user_request,
                    scoring_action,
                    matching_candidates,
                    preferred_field=source_field,
                )
            if candidate is None:
                return None
            if _execution_learning_has_sensitive_text(candidate.get("value")):
                return None
            if stdin_mode == "input_binding":
                if stdin_input_name:
                    return None
                stdin_input_name = input_name
            else:
                env_name = template_env_name(input_name)
                if (
                    env_name
                    and f"${env_name}" not in command
                    and f"${{{env_name}}}" not in command
                ):
                    return None
            source_action_id = self._streaming_prior_binding_source_action_id(
                user_request,
                action=scoring_action,
                candidate=candidate,
            )
            if not source_action_id:
                return None
            self._ensure_streaming_prior_alias_seed(
                user_request,
                candidate=candidate,
                alias_action_id=source_action_id,
            )
            if source_action_id and source_action_id not in depends_on:
                depends_on.append(source_action_id)
            rebuilt_bindings.append(
                {
                    "input_name": input_name,
                    "source_action_id": source_action_id,
                    "source_field": source_field,
                    "required": True,
                    "fallback_value": None,
                }
            )
        action = OperatorAction.model_validate(
            {
                **scoring_action.model_dump(mode="json"),
                "input_bindings": rebuilt_bindings,
                "stdin_mode": "input_binding" if stdin_input_name else "none",
                "stdin_input_name": stdin_input_name or None,
                "depends_on": depends_on,
            }
        )
        task = OperatorTask(
            task_id=task_id,
            goal=task_goal,
            semantic_verb=semantic_verb,
            object_type=object_type,
            dependencies=[],
            reason="Matched a payload-aware learned command template for the current request.",
        )
        return OperatorPlan(
            summary="Use an LR-EX payload-aware learned command template for this request.",
            tasks=[task],
            actions=[action],
            dependencies=[],
            expected_outputs=[
                "Command output from the payload-aware learned template."
            ],
            assumptions=[],
            confidence=1.0,
        )

    def _try_lrex_command_template_cache_adaptation(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
    ) -> tuple[OperatorPlan, CommandTemplateCandidate] | None:
        """Try payload-aware learned command templates without LLM redrafting."""

        if self.command_template_cache_store is None:
            return None
        lookup = self._command_template_cache_context(user_request).model_copy(
            update={"lr_mode": LR_MODE_PAYLOAD}
        )
        try:
            candidates = self.command_template_cache_store.retrieve(
                lookup, record_use=False
            )
        except Exception as exc:
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_LOOKUP,
                title="LR-EX command template cache lookup failed",
                summary="The payload-aware command template cache lookup failed; normal planning will continue.",
                details={
                    "enabled": True,
                    "lr_mode": LR_MODE_PAYLOAD,
                    "cache_type": "payload_command_template",
                    "candidate_count": 0,
                    "error": str(exc),
                },
                level="warning",
            )
            return None
        self._emit_command_template_cache(
            user_request,
            observability,
            event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_LOOKUP,
            title="LR-EX command template cache lookup completed",
            summary="The runtime checked for payload-aware learned command templates.",
            details={
                "enabled": True,
                "lr_mode": LR_MODE_PAYLOAD,
                "cache_type": "payload_command_template",
                "candidate_count": len(candidates),
                "lookup": lookup.model_dump(mode="json"),
                "candidates": [
                    {
                        "template_id": candidate.entry.template_id,
                        "score": candidate.score,
                        "payload_binding_count": len(
                            candidate.entry.payload_bindings or []
                        ),
                        "payload_binding_kinds": self._lrex_payload_binding_kinds(
                            candidate.entry.payload_bindings
                        ),
                        "reason": candidate.reason,
                    }
                    for candidate in candidates
                ],
            },
        )
        for candidate in candidates:
            plan = self._lrex_plan_from_entry(
                user_request,
                candidate.entry,
                cache_id=candidate.entry.template_id,
            )
            if plan is None:
                continue
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_HIT,
                title="LR-EX command template cache hit",
                summary="A payload-aware learned command template matched current runtime payloads.",
                details={
                    "template_id": candidate.entry.template_id,
                    "selected_template_id": candidate.entry.template_id,
                    "selected_score": candidate.score,
                    "lr_mode": LR_MODE_PAYLOAD,
                    "cache_type": "payload_command_template",
                    "payload_binding_count": len(
                        candidate.entry.payload_bindings or []
                    ),
                    "payload_binding_kinds": self._lrex_payload_binding_kinds(
                        candidate.entry.payload_bindings
                    ),
                },
            )
            return plan, candidate
        return None

    def _try_command_template_cache_adaptation(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
    ) -> tuple[OperatorPlan, CommandTemplateCandidate] | None:
        """Try using a learned shell command template before full command planning."""

        if not self._command_template_cache_enabled(user_request):
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_LOOKUP,
                title="Command template cache skipped",
                summary="The learned command template cache is disabled or unavailable.",
                details={"enabled": False, "candidate_count": 0},
            )
            return None
        generated_text_step = operator_request_requires_generated_text(user_request)
        if generated_text_step:
            lrex_cached = self._try_lrex_command_template_cache_adaptation(
                user_request, observability
            )
            if lrex_cached is not None:
                return lrex_cached
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_LOOKUP,
                title="Command template cache skipped",
                summary="Generated-text steps should not replay or adapt shell-only command templates.",
                details={
                    "enabled": True,
                    "candidate_count": 0,
                    "generated_text_step": True,
                },
            )
            return None
        assert self.command_template_cache_store is not None
        lookup = self._command_template_cache_context(user_request)
        try:
            candidates = self.command_template_cache_store.retrieve(
                lookup, record_use=False
            )
        except Exception as exc:
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_LOOKUP,
                title="Command template cache lookup failed",
                summary="The learned command template cache lookup failed; normal planning will continue.",
                details={"enabled": True, "candidate_count": 0, "error": str(exc)},
                level="warning",
            )
            return None
        self._emit_command_template_cache(
            user_request,
            observability,
            event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_LOOKUP,
            title="Command template cache lookup completed",
            summary="The learned command template cache checked for reusable command patterns.",
            details={
                "enabled": True,
                "candidate_count": len(candidates),
                "lookup": lookup.model_dump(mode="json"),
                "candidates": [
                    {
                        "template_id": candidate.entry.template_id,
                        "score": candidate.score,
                        "reason": candidate.reason,
                    }
                    for candidate in candidates
                ],
            },
        )
        if not candidates:
            if self._lrex_runtime_payload_sources_available(user_request):
                lrex_cached = self._try_lrex_command_template_cache_adaptation(
                    user_request, observability
                )
                if lrex_cached is not None:
                    return lrex_cached
            return None
        prompt = build_operator_command_template_cache_prompt(user_request, candidates)
        try:
            decision = structured_call(
                self.llm_client, prompt, OperatorCommandTemplateDecision
            )
        except Exception as exc:
            self._emit_command_template_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_HIT,
                title="Command template cache judgement rejected",
                summary="The learned command template judgement did not produce a valid typed decision.",
                details={"error": str(exc)},
                level="warning",
            )
            return None
        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.entry.template_id == decision.selected_template_id
            ),
            None,
        )
        self._emit_command_template_cache(
            user_request,
            observability,
            event_type=OPERATOR_COMMAND_TEMPLATE_CACHE_HIT,
            title="Command template cache judgement",
            summary="The LLM judged whether a learned command template fits this request.",
            details={
                **decision.model_dump(mode="json"),
                "candidate_count": len(candidates),
                "selected_score": selected.score if selected is not None else None,
            },
        )
        if (
            decision.decision != "use_template"
            or selected is None
            or decision.confidence < 0.6
        ):
            if self._lrex_runtime_payload_sources_available(user_request):
                lrex_cached = self._try_lrex_command_template_cache_adaptation(
                    user_request, observability
                )
                if lrex_cached is not None:
                    return lrex_cached
            return None
        plan = self._command_template_plan_from_decision(
            user_request, selected, decision
        )
        if plan is None:
            if self._lrex_runtime_payload_sources_available(user_request):
                lrex_cached = self._try_lrex_command_template_cache_adaptation(
                    user_request, observability
                )
                if lrex_cached is not None:
                    return lrex_cached
            return None
        return plan, selected


__all__ = ["_MemoryComplianceCommandTemplateCacheMixin"]
