"""Self-brief and guided-deliberation clarification helpers."""

from __future__ import annotations

from .common import *
from .confirmation_payloads import *
from .policy_gates import *
from .memory_questions import *
from .resolution_prompt import *


class _SelfBriefDeliberationMixin:
    """Self-brief and guided-deliberation clarification helpers."""

    def _complete_self_brief(
        self,
        user_request: UserRequest,
        conversation_context: dict[str, Any] | None = None,
    ) -> OperatorSelfBrief:
        prompt = build_operator_self_brief_prompt(user_request, conversation_context)
        return self._call_with_max_tokens(
            self.llm_client,
            prompt,
            OperatorSelfBrief,
            max_tokens=int(
                operator_legacy_override(
                    self.config,
                    "llm_operator_self_brief_max_tokens",
                    reasoning_settings_from_profile(
                        getattr(self.config, "reasoning_profile", None)
                    ).self_brief_max_tokens,
                )
            ),
        )

    def _self_brief_mode(self) -> str:
        override = operator_legacy_override(self.config, "llm_operator_self_brief_mode")
        if override is not None:
            mode = str(override or "").strip().lower()
            return mode if mode in {"off", "auto", "always"} else "off"
        profile_policy = operator_profile_policy(
            getattr(self.config, "reasoning_profile", None),
            llm_operator_verbose_enabled=getattr(
                self.config,
                "llm_operator_verbose_enabled",
                True,
            ),
        )
        if not profile_policy.run_self_brief:
            return "off"
        return reasoning_settings_from_profile(
            getattr(self.config, "reasoning_profile", None)
        ).self_brief_mode

    def _self_brief_auto_trigger_reason(
        self,
        user_request: UserRequest,
        conversation_context: dict[str, Any] | None = None,
    ) -> str:
        context = dict(user_request.session_context or {})
        current_task = context.get("operator_streaming_current_task")
        if isinstance(current_task, dict):
            trigger_reason = _normalized_task_trigger_reason(current_task, scope="streaming")
            if trigger_reason:
                return trigger_reason
        if any(
            key in context
            for key in (
                "operator_seed_records",
                "operator_failure_continuation",
                "operator_completion_review_pending",
                "operator_execution_repair_pending",
                "operator_streaming_prior_results",
            )
        ):
            return "resume_or_prior_evidence"
        for task in _operator_intent_tasks_from_context(context):
            trigger_reason = _normalized_task_trigger_reason(task, scope="decomposed")
            if trigger_reason:
                return trigger_reason
        if isinstance(conversation_context, dict) and conversation_context:
            return "conversation_context"
        prompt = _mask_prompt_literal_regions(str(user_request.raw_prompt or "")).lower()
        mutating_patterns = (
            r"\b(add|append|apply|build|checkout|cherry[- ]pick|chmod|chown|commit|copy|cp|create|delete|docker\s+compose\s+(up|down|restart|stop|start)|install|kill|merge|mkdir|move|mv|npm\s+publish|pull|push|rebase|remove|rename|restart|rm|rmdir|stage|start|stop|touch|update|upgrade|write)\b",
        )
        if any(re.search(pattern, prompt) for pattern in mutating_patterns):
            return "mutating_prompt"
        if re.search(r"\b(git|docker|kubectl|ssh|aws|npm|pip|conda|systemctl)\b", prompt):
            return "stateful_tool_prompt"
        if re.search(r"\b(script|python|code|program|function|parse|transform)\b", prompt):
            return "code_or_data_shape_prompt"
        return ""

    @staticmethod
    def _skipped_self_brief(user_request: UserRequest, *, reason: str) -> OperatorSelfBrief:
        context = dict(user_request.session_context or {})
        current_task = context.get("operator_streaming_current_task")
        task_label = ""
        if isinstance(current_task, dict):
            task_label = str(current_task.get("description") or "").strip()
        prompt_label = task_label or str(user_request.raw_prompt or "").strip() or "the request"
        return OperatorSelfBrief.model_validate(
            {
                "task_understanding": (
                    f"Self-brief skipped ({reason}); plan directly for: {prompt_label}"
                ),
                "self_questions": [
                    {
                        "question": "What should planning optimize for when self-brief is skipped?",
                        "answer": "Use the user prompt, prior runtime context, deterministic validation, and real command outputs directly.",
                        "confidence": 0.7,
                    }
                ],
                "key_domain_facts": [],
                "likely_pitfalls": [
                    "Do not claim success without real command output or a completed mutation."
                ],
                "success_postcondition": (
                    "The current streaming step or user request is satisfied by captured runtime evidence."
                ),
                "recommended_strategy": (
                    "Plan the smallest concrete operator action set needed for the current request context."
                ),
                "verification_strategy": (
                    "Use action return codes, stdout/stderr, and any required fresh state checks to avoid false success."
                ),
                "verification_contract": {
                    "goal": "other",
                    "observable_state": "The requested answer or external state is available from execution evidence.",
                    "success_when": "The planned action output or state change satisfies the request.",
                    "failure_when": "The required output or state change is missing or contradicted by execution evidence.",
                    "freshness": "not_applicable",
                    "must_exit_nonzero_when_unmet": True,
                    "acceptable_evidence": ["Captured action output or fresh post-action state."],
                },
                "confidence": 0.0,
            }
        )

    def _ensure_self_brief(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None = None,
        conversation_context: dict[str, Any] | None = None,
    ) -> OperatorSelfBrief:
        existing = _operator_self_brief_from_request(user_request)
        if existing is not None:
            if not bool(
                dict(user_request.session_context or {}).get(
                    "agent_memory_retrieved_after_self_brief"
                )
            ):
                self._attach_memory_after_self_brief(user_request, existing, observability)
            return existing
        mode = self._self_brief_mode()
        trigger_reason = (
            "always_on"
            if mode == "always"
            else self._self_brief_auto_trigger_reason(user_request, conversation_context)
            if mode == "auto"
            else ""
        )
        if not trigger_reason:
            brief = self._skipped_self_brief(user_request, reason=f"mode={mode}")
            user_request.session_context["operator_self_brief"] = brief.model_dump(mode="json")
            user_request.session_context["operator_self_brief_skipped"] = True
            user_request.session_context["operator_self_brief_mode"] = mode
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_SELF_BRIEF_COMPLETED,
                title="Operator self-brief skipped",
                summary="The operator used deterministic context and validation without a pre-planning self-brief LLM call.",
                details={
                    "mode": mode,
                    "reason": f"mode={mode}",
                    "brief": brief.model_dump(mode="json"),
                },
            )
            self._attach_memory_after_self_brief(user_request, brief, observability)
            return brief
        brief = self._complete_self_brief(user_request, conversation_context)
        user_request.session_context["operator_self_brief"] = brief.model_dump(mode="json")
        user_request.session_context["operator_self_brief_skipped"] = False
        user_request.session_context["operator_self_brief_mode"] = mode
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_SELF_BRIEF_COMPLETED,
            title="Operator self-brief completed",
            summary="The LLM prepared domain facts, pitfalls, and the success postcondition before planning.",
            details={
                "mode": mode,
                "reason": trigger_reason,
                "brief": brief.model_dump(mode="json"),
            },
        )
        self._attach_memory_after_self_brief(user_request, brief, observability)
        return brief

    def _guided_deliberation_mode(self) -> str:
        override = operator_legacy_override(self.config, "guided_deliberation_mode")
        if override is not None:
            mode = str(override or "").strip().lower()
            return mode if mode in {"off", "auto", "always"} else "off"
        profile_policy = operator_profile_policy(
            getattr(self.config, "reasoning_profile", None),
            llm_operator_verbose_enabled=getattr(
                self.config,
                "llm_operator_verbose_enabled",
                True,
            ),
        )
        if not profile_policy.include_rationales:
            return "off"
        return reasoning_settings_from_profile(
            getattr(self.config, "reasoning_profile", None)
        ).deliberation_mode

    def _repair_confidence_threshold(self) -> float:
        override = operator_legacy_override(
            self.config,
            "llm_operator_repair_confidence_threshold",
        )
        if override is not None:
            return float(override)
        return repair_settings_from_profile(
            getattr(self.config, "repair_profile", None)
        ).confidence_threshold

    def _guided_deliberation_trigger_reason(
        self,
        user_request: UserRequest,
        *,
        phase: str,
        plan: OperatorPlan | None = None,
        feedback: list[dict[str, Any]] | None = None,
    ) -> str:
        mode = self._guided_deliberation_mode()
        if mode == "off":
            return ""
        if mode == "always":
            return "always_on"
        prompt = str(user_request.raw_prompt or "")
        if feedback:
            return "repair_or_validation_feedback"
        if phase in {"cache_plan", "followup_cache_plan"}:
            return "plan_cache_candidate"
        if dict(user_request.session_context or {}).get("operator_plan_cache_applied"):
            return "plan_cache_applied"
        context = dict(user_request.session_context or {})
        if context.get("operator_failure_continuation") or context.get("operator_seed_records"):
            return "continuation_or_replay_context"
        current_task = context.get("operator_streaming_current_task")
        if isinstance(current_task, dict):
            trigger_reason = _normalized_task_trigger_reason(current_task, scope="streaming")
            if trigger_reason:
                return trigger_reason
        for task in _operator_intent_tasks_from_context(context):
            trigger_reason = _normalized_task_trigger_reason(task, scope="decomposed")
            if trigger_reason:
                return trigger_reason
        if plan is not None:
            for task in plan.tasks:
                # OperatorPlan tasks often use "execute" for both read-only shell
                # probes and real mutations. At this stage the concrete actions
                # are a better signal, so don't let a generic execute verb alone
                # force Deep Reasoning on.
                semantic_verb = str(task.semantic_verb or "").strip().lower()
                normalized_semantic_verb = "" if semantic_verb == "execute" else task.semantic_verb
                trigger_reason = _normalized_task_trigger_reason(
                    {
                        "semantic_verb": normalized_semantic_verb,
                        "object_type": task.object_type,
                    },
                    scope="plan",
                )
                if trigger_reason:
                    return trigger_reason
        if plan is not None:
            for action in plan.actions:
                if action.kind in {"python_action", "python_transform"}:
                    return "python_codegen_plan"
                if action.defer_code_generation:
                    return "deferred_codegen_plan"
                if str(action.risk or "").lower() in _RISKY_LEVELS:
                    return "non_low_risk_action"
                if _GUIDED_DELIBERATION_MUTATING_COMMAND_RE.search(str(action.command or "")):
                    return "mutating_command_pattern"
        prompt = _mask_prompt_literal_regions(prompt)
        if _GUIDED_DELIBERATION_PROMPT_RE.search(prompt):
            return "complex_or_risky_prompt"
        return ""

    def _emit_guided_deliberation_skipped(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
        *,
        phase: str,
        reason: str,
    ) -> None:
        key = f"guided_deliberation_skip_emitted_{phase}"
        if user_request.session_context.get(key):
            return
        user_request.session_context[key] = True
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_DELIBERATION_SKIPPED,
            title="Deep Reasoning skipped",
            summary=reason,
            details={
                "phase": phase,
                "mode": self._guided_deliberation_mode(),
                "reason": reason,
            },
        )

    def _ensure_deliberation_frame(
        self,
        user_request: UserRequest,
        brief: OperatorSelfBrief,
        observability: ObservabilityContext | None,
        *,
        phase: str = "pre_plan",
        reason: str | None = None,
    ) -> DeliberationFrame | None:
        existing = _deliberation_frame_from_request(user_request)
        if existing is not None:
            return existing
        trigger_reason = reason or self._guided_deliberation_trigger_reason(
            user_request,
            phase=phase,
        )
        if not trigger_reason:
            self._emit_guided_deliberation_skipped(
                user_request,
                observability,
                phase=phase,
                reason="Deep Reasoning is off or auto mode did not find a complex/risky trigger.",
            )
            return None
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_DELIBERATION_STARTED,
            title="Deep Reasoning started",
            summary="The runtime is asking for a concise goal, evidence, risk, and strategy frame.",
            details={
                "phase": phase,
                "mode": self._guided_deliberation_mode(),
                "reason": trigger_reason,
            },
        )
        try:
            frame = structured_call(
                self.llm_client,
                build_deliberation_frame_prompt(user_request, brief, reason=trigger_reason),
                DeliberationFrame,
            )
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_DELIBERATION_SKIPPED,
                title="Deep Reasoning frame rejected",
                summary="The deliberation frame did not produce a valid typed response.",
                details={
                    "phase": phase,
                    "mode": self._guided_deliberation_mode(),
                    "reason": trigger_reason,
                    "error": str(exc),
                },
            )
            return None
        user_request.session_context["guided_deliberation_frame"] = frame.model_dump(mode="json")
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_DELIBERATION_REVIEWED,
            title="Deep Reasoning framed request",
            summary="The LLM produced a concise goal, evidence, risk, and strategy frame.",
            details={
                "phase": phase,
                "mode": self._guided_deliberation_mode(),
                "reason": trigger_reason,
                **frame.model_dump(mode="json"),
            },
        )
        return frame
