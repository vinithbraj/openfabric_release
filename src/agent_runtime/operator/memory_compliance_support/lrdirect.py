"""LR-direct helpers for operator memory compliance."""

from __future__ import annotations

from agent_runtime.operator.memory_compliance_support.common import *
from agent_runtime.operator.memory_compliance_support.common import (
    _MemoryComplianceCommonMixin,
)
from agent_runtime.operator.memory_compliance_support.lrex_shape_match import (
    LrExShapeMatchDecision,
    judge_lrex_shape_match,
)


_LREX_SHAPE_MATCH_MIN_CONFIDENCE = 0.85
_LREX_SHAPE_MATCH_CANDIDATE_LIMIT = 3
_LREX_SHAPE_MATCH_POOL_LIMIT = 25


class _MemoryComplianceLrDirectMixin(_MemoryComplianceCommonMixin):
    @staticmethod
    def _lrdirect_streaming_task(user_request: UserRequest) -> dict[str, Any]:
        context = dict(user_request.session_context or {})
        current_task = context.get("operator_streaming_current_task")
        return current_task if isinstance(current_task, dict) else {}

    def _lrdirect_requested(self, user_request: UserRequest) -> bool:
        """Return whether LR Direct is requested, even before a streaming step exists."""

        context = dict(user_request.session_context or {})
        if "lrdirect_enabled" in context:
            return bool(context.get("lrdirect_enabled"))
        return bool(getattr(self.config, "lrdirect_enabled", True))

    def _lrdirect_enabled(self, user_request: UserRequest) -> bool:
        """Return whether exact streaming-step replay may be attempted."""

        if self._step_validation_repair_retry_active(user_request):
            return False
        if not self._lrdirect_streaming_task(user_request):
            return False
        return self._lrdirect_requested(user_request)

    @staticmethod
    def _lrdirect_step_locator(user_request: UserRequest) -> str:
        """Return the stable position/id component for an LR Direct step key."""

        context = dict(user_request.session_context or {})
        if "operator_streaming_current_index" in context:
            try:
                index = int(context.get("operator_streaming_current_index") or 0) + 1
            except (TypeError, ValueError):
                index = 1
            return f"index={index}"
        current_task = _MemoryComplianceLrDirectMixin._lrdirect_streaming_task(
            user_request
        )
        step_id = str(
            current_task.get("streaming_step_id")
            or context.get("operator_streaming_step_id")
            or current_task.get("task_id")
            or current_task.get("id")
            or ""
        ).strip()
        return f"id={step_id}" if step_id else ""

    def _lrdirect_step_key_parts(self, user_request: UserRequest) -> dict[str, str]:
        """Return canonical LR Direct identity fields for the current step."""

        current_task = self._lrdirect_streaming_task(user_request)
        if not current_task:
            return {}
        return {
            "step_description": str(
                current_task.get("description") or user_request.raw_prompt or ""
            ),
            "semantic_verb": str(current_task.get("semantic_verb") or ""),
            "object_type": str(current_task.get("object_type") or ""),
            "step_locator": self._lrdirect_step_locator(user_request),
        }

    def _lrdirect_step_metadata(self, user_request: UserRequest) -> tuple[str, str]:
        """Return canonical exact-step key and readable excerpt for cache metadata."""

        parts = self._lrdirect_step_key_parts(user_request)
        if not parts:
            return "", ""
        key = lrdirect_canonical_step_key(**parts)
        excerpt = lrdirect_canonical_step_excerpt(**parts)[:1000]
        return key, excerpt

    @staticmethod
    def _lrdirect_context_flag_enabled(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}

    @classmethod
    def _lrdirect_replay_environment(cls, user_request: UserRequest) -> dict[str, str]:
        context = dict(user_request.session_context or {})
        cwd_guard_enabled = cls._lrdirect_context_flag_enabled(
            context.get("operator_workspace_cwd_guard_enabled")
        )
        return {
            "cwd": str(context.get("terminal_cwd") or "").strip() if cwd_guard_enabled else "",
            "gateway_platform": str(context.get("gateway_platform") or "").strip(),
        }

    def _legacy_lrdirect_command_candidates(
        self,
        user_request: UserRequest,
        *,
        lr_mode: str,
    ) -> list[CommandTemplateCandidate]:
        """Return legacy direct command rows keyed by old rendered prompt hashes."""

        if self.command_template_cache_store is None:
            return []
        current_task = self._lrdirect_streaming_task(user_request)
        if not current_task:
            return []
        return self.command_template_cache_store.retrieve_legacy_exact_step(
            step_description=str(
                current_task.get("description") or user_request.raw_prompt or ""
            ),
            task_type=str(current_task.get("object_type") or ""),
            intent_type=str(current_task.get("semantic_verb") or ""),
            lr_mode=lr_mode,
            **self._lrdirect_replay_environment(user_request),
        )

    @staticmethod
    def _lrdirect_shell_action_is_raw_command(payload: dict[str, Any]) -> bool:
        """Return whether a shell direct action has no runtime-provided arguments."""

        if str(payload.get("kind") or "") != "shell_command":
            return True
        if dict(payload.get("inputs") or {}):
            return False
        if list(payload.get("input_bindings") or []):
            return False
        stdin_mode = str(payload.get("stdin_mode") or "none").strip().lower() or "none"
        if stdin_mode != "none":
            return False
        if str(payload.get("stdin_text") or ""):
            return False
        if str(payload.get("stdin_input_name") or ""):
            return False
        command = str(payload.get("command") or "")
        return not re.search(
            r"\$(?:\{OF_INPUT_[A-Za-z0-9_]+\}|OF_INPUT_[A-Za-z0-9_]+)", command
        )

    @classmethod
    def _lrdirect_whole_step_action_payload(
        cls, plan: OperatorPlan, action: OperatorAction
    ) -> dict[str, Any]:
        if len(list(plan.actions or [])) != 1:
            return {}
        payload = action.model_dump(mode="json")
        for run_key in ("action_id", "task_id", "depends_on"):
            payload.pop(run_key, None)
        if (
            action.kind == "shell_command"
            and not cls._lrdirect_shell_action_is_raw_command(payload)
        ):
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
        payload["lrdirect_schema_version"] = 2
        return payload

    def _emit_lrdirect(
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
        """Emit exact-step LR Direct status into the developer trace."""

        trace = user_request.safety_context.get("planning_trace")
        metadata = getattr(trace, "metadata", None)
        if isinstance(metadata, dict):
            metadata["operator_lrdirect"] = dict(details)
        self._emit(
            observability,
            level=level,
            event_type=event_type,
            title=title,
            summary=summary,
            details=details,
        )

    def _lrex_shape_current_step(
        self,
        user_request: UserRequest,
        *,
        exact_key: str,
        prompt_excerpt: str,
    ) -> dict[str, Any]:
        context = dict(user_request.session_context or {})
        current_task = self._lrdirect_streaming_task(user_request)
        return {
            "raw_prompt": user_request.raw_prompt,
            "step_description": str(
                current_task.get("description") or user_request.raw_prompt or ""
            ),
            "semantic_verb": str(current_task.get("semantic_verb") or ""),
            "object_type": str(current_task.get("object_type") or ""),
            "step_locator": self._lrdirect_step_locator(user_request),
            "exact_step_key": exact_key,
            "prompt_excerpt": prompt_excerpt,
            "mode": str(
                context.get("agent_mode") or context.get("operator_mode_label") or ""
            ),
            "gateway_platform": str(context.get("gateway_platform") or ""),
            "cwd": str(context.get("terminal_cwd") or ""),
        }

    def _lrex_shape_prior_outputs(
        self, user_request: UserRequest
    ) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for candidate in self._streaming_prior_output_candidates(user_request)[:8]:
            value = str(candidate.get("value") or "")
            metadata = candidate.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            outputs.append(
                {
                    "source_action_id": str(candidate.get("action_id") or ""),
                    "task_id": str(candidate.get("task_id") or ""),
                    "kind": str(candidate.get("kind") or ""),
                    "source_field": str(candidate.get("source_field") or "stdout"),
                    "task_description": str(
                        candidate.get("task_description") or ""
                    )[:500],
                    "final_response": str(candidate.get("final_response") or "")[:500],
                    "metadata": {
                        "llm_text": bool(metadata.get("llm_text")),
                        "generated_text": bool(metadata.get("generated_text")),
                        "source": str(metadata.get("source") or "")[:80],
                    },
                    "value_preview": value[:700],
                }
            )
        return outputs

    @staticmethod
    def _lrex_shape_binding_map(
        decision: LrExShapeMatchDecision | None,
    ) -> dict[str, str]:
        if decision is None:
            return {}
        result: dict[str, str] = {}
        for raw_key, raw_value in dict(decision.prior_binding_map or {}).items():
            key = str(raw_key or "").strip()
            value = str(raw_value or "").strip()
            if not key or not value:
                continue
            result[key] = value
            normalized = normalize_template_variable_name(key)
            if normalized:
                result[normalized] = value
        return result

    def _lrex_prior_candidate_from_shape_match(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        candidates: list[dict[str, Any]],
        *,
        input_name: str,
        source_field: str,
        prior_binding_map: dict[str, str] | None,
    ) -> dict[str, Any] | None:
        binding_map = dict(prior_binding_map or {})
        if not binding_map:
            return None
        normalized_input = normalize_template_variable_name(input_name)
        source = (
            binding_map.get(str(input_name or "").strip())
            or binding_map.get(normalized_input)
            or binding_map.get(str(source_field or "").strip())
        )
        source = str(source or "").strip()
        if not source:
            return None
        for candidate in candidates:
            if str(candidate.get("source_field") or "stdout") != source_field:
                continue
            aliases = self._streaming_prior_source_aliases(candidate)
            if source in aliases or source == str(candidate.get("action_id") or ""):
                return candidate
        return None

    def _lrex_shape_candidate_risk_compatible(
        self,
        user_request: UserRequest,
        *,
        risk: Any,
        effect_intent: Any,
        semantic_verb: Any,
        object_type: Any,
    ) -> bool:
        risk_text = str(risk or "").strip().lower()
        effect_text = str(effect_intent or "").strip().lower()
        if risk_text in {"high", "critical"}:
            return False
        if risk_text in {"medium"} or effect_text == "mutates_state":
            current = self._lrdirect_streaming_task(user_request)
            current_verb = normalize_lrdirect_step_text(
                current.get("semantic_verb") or ""
            )
            current_object = normalize_lrdirect_step_text(
                current.get("object_type") or ""
            )
            candidate_verb = normalize_lrdirect_step_text(semantic_verb)
            candidate_object = normalize_lrdirect_step_text(object_type)
            return bool(
                current_verb
                and current_object
                and candidate_verb
                and candidate_object
                and current_verb == candidate_verb
                and current_object == candidate_object
            )
        return effect_text in {"", "read_only", "unknown"} and risk_text in {
            "",
            "low",
        }

    def _lrex_shape_command_candidate_payload(
        self, candidate: CommandTemplateCandidate
    ) -> dict[str, Any]:
        entry = candidate.entry
        payload_bindings = normalize_payload_bindings(entry.payload_bindings)
        return {
            "candidate_id": entry.template_id,
            "cache_type": "payload_command_template",
            "action_family": "shell_command",
            "score": round(float(candidate.score), 4),
            "reason": candidate.reason,
            "step_excerpt": entry.step_excerpt[:700],
            "exact_step_prompt_excerpt": entry.exact_step_prompt_excerpt[:700],
            "semantic_verb": entry.intent_type,
            "object_type": entry.task_type or entry.tool_type,
            "risk": entry.risk,
            "effect_intent": entry.effect_intent,
            "effect_summary": entry.effect_summary[:500],
            "payload_input_names": [
                str(item.get("input_name") or "") for item in payload_bindings
            ],
            "payload_binding_kinds": self._lrex_payload_binding_kinds(
                entry.payload_bindings
            ),
            "command_excerpt": str(entry.command_template or "")[:700],
        }

    def _lrex_shape_computation_candidate_payload(
        self, candidate: ComputationCacheCandidate
    ) -> dict[str, Any]:
        entry = candidate.entry
        direct_action = dict(entry.direct_action or {})
        input_bindings = [
            item
            for item in list(direct_action.get("input_bindings") or [])
            if isinstance(item, dict)
        ]
        return {
            "candidate_id": entry.cache_id,
            "cache_type": "computation",
            "action_family": str(direct_action.get("kind") or entry.action_kind),
            "score": round(float(candidate.score), 4),
            "reason": candidate.reason,
            "step_excerpt": entry.exact_step_prompt_excerpt[:700]
            or entry.prompt_excerpt[:700],
            "semantic_verb": entry.intent_type,
            "object_type": entry.task_type or entry.tool_type,
            "declared_output_shape": entry.declared_output_shape,
            "action_reason": entry.action_reason[:500],
            "template_reason": entry.template_reason[:500],
            "risk": str(direct_action.get("risk") or ""),
            "effect_intent": str(direct_action.get("effect_intent") or ""),
            "effect_summary": str(direct_action.get("effect_summary") or "")[:500],
            "input_names": [
                str(item.get("input_name") or "") for item in input_bindings
            ],
            "input_source_fields": [
                str(item.get("source_field") or "stdout") for item in input_bindings
            ],
            "code_excerpt": str(
                direct_action.get("code") or entry.code_template or ""
            )[:900],
        }

    def _lrex_shape_match_decision(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
        *,
        cache_type: str,
        exact_key: str,
        prompt_excerpt: str,
        candidate_shapes: list[dict[str, Any]],
    ) -> LrExShapeMatchDecision | None:
        if not candidate_shapes or getattr(self, "llm_client", None) is None:
            summary = (
                "No LR-EX semantic shape candidates were available."
                if not candidate_shapes
                else "No LLM client was available for LR-EX semantic shape judging."
            )
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_LOOKUP,
                title="LR-EX semantic shape lookup completed",
                summary=summary,
                details={
                    "enabled": True,
                    "cache_type": cache_type,
                    "match_mode": "llm_shape_equivalence",
                    "exact_step_key": exact_key,
                    "prompt_excerpt": prompt_excerpt,
                    "candidate_count": len(candidate_shapes),
                    "llm_available": getattr(self, "llm_client", None) is not None,
                    "accepted": False,
                    "bypassed_llm": False,
                },
            )
            return None
        current_shape = self._lrex_shape_current_step(
            user_request,
            exact_key=exact_key,
            prompt_excerpt=prompt_excerpt,
        )
        prior_outputs = self._lrex_shape_prior_outputs(user_request)
        try:
            decision = judge_lrex_shape_match(
                self.llm_client,
                current_shape=current_shape,
                candidate_shapes=candidate_shapes,
                prior_outputs=prior_outputs,
            )
        except Exception as exc:
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_REJECTED,
                title="LR-EX semantic shape lookup rejected",
                summary="The LR-EX semantic shape judge failed; normal planning will continue.",
                details={
                    "enabled": True,
                    "cache_type": cache_type,
                    "match_mode": "llm_shape_equivalence",
                    "exact_step_key": exact_key,
                    "candidate_count": len(candidate_shapes),
                    "error": str(exc),
                    "accepted": False,
                    "bypassed_llm": False,
                },
                level="warning",
            )
            return None
        candidate_ids = {
            str(item.get("candidate_id") or "") for item in candidate_shapes
        }
        accepted = (
            bool(decision.equivalent)
            and not bool(decision.unsafe_to_reuse)
            and float(decision.confidence) >= _LREX_SHAPE_MATCH_MIN_CONFIDENCE
            and str(decision.selected_candidate_id or "") in candidate_ids
        )
        self._emit_lrdirect(
            user_request,
            observability,
            event_type=OPERATOR_LRDIRECT_LOOKUP,
            title="LR-EX semantic shape lookup completed",
            summary="The runtime asked a cheap LLM judge whether cached direct replay shapes are equivalent.",
            details={
                "enabled": True,
                "cache_type": cache_type,
                "match_mode": "llm_shape_equivalence",
                "exact_step_key": exact_key,
                "prompt_excerpt": prompt_excerpt,
                "candidate_count": len(candidate_shapes),
                "selected_candidate_id": decision.selected_candidate_id,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "unsafe_to_reuse": decision.unsafe_to_reuse,
                "accepted": accepted,
                "bypassed_llm": False,
            },
        )
        return decision if accepted else None

    def _try_lrex_shape_match_command_cache(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
        *,
        exact_key: str,
        prompt_excerpt: str,
        seed_candidates: list[CommandTemplateCandidate] | None = None,
    ) -> tuple[OperatorPlan, CommandTemplateCandidate, str] | None:
        if (
            self.command_template_cache_store is None
            or getattr(self, "llm_client", None) is None
        ):
            return None
        candidates: list[CommandTemplateCandidate] = list(seed_candidates or [])
        seen_ids = {candidate.entry.template_id for candidate in candidates}
        try:
            lookup = self._command_template_cache_context(user_request).model_copy(
                update={
                    "lr_mode": LR_MODE_PAYLOAD,
                    "limit": _LREX_SHAPE_MATCH_CANDIDATE_LIMIT,
                }
            )
            extra_candidates = (
                self.command_template_cache_store.retrieve_lrex_shape_candidates(
                    lookup,
                    exact_key=exact_key,
                    excluded_template_ids=sorted(seen_ids),
                    limit=_LREX_SHAPE_MATCH_CANDIDATE_LIMIT,
                    pool_limit=_LREX_SHAPE_MATCH_POOL_LIMIT,
                )
            )
        except Exception as exc:
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_REJECTED,
                title="LR-EX semantic shape command lookup failed",
                summary="The LR-EX semantic shape command shortlist failed; normal planning will continue.",
                details={
                    "enabled": True,
                    "cache_type": "payload_command_template",
                    "match_mode": "llm_shape_equivalence",
                    "exact_step_key": exact_key,
                    "error": str(exc),
                    "bypassed_llm": False,
                },
                level="warning",
            )
            return None
        for candidate in extra_candidates:
            if candidate.entry.template_id not in seen_ids:
                candidates.append(candidate)
                seen_ids.add(candidate.entry.template_id)
        safe_candidates: list[CommandTemplateCandidate] = []
        for candidate in candidates:
            entry = candidate.entry
            if normalize_lr_mode(entry.lr_mode) != LR_MODE_PAYLOAD:
                continue
            if not normalize_payload_bindings(entry.payload_bindings):
                continue
            if not self._lrex_shape_candidate_risk_compatible(
                user_request,
                risk=entry.risk,
                effect_intent=entry.effect_intent,
                semantic_verb=entry.intent_type,
                object_type=entry.task_type or entry.tool_type,
            ):
                continue
            safe_candidates.append(candidate)
        safe_candidates = safe_candidates[:_LREX_SHAPE_MATCH_CANDIDATE_LIMIT]
        candidate_shapes = [
            self._lrex_shape_command_candidate_payload(candidate)
            for candidate in safe_candidates
        ]
        decision = self._lrex_shape_match_decision(
            user_request,
            observability,
            cache_type="payload_command_template",
            exact_key=exact_key,
            prompt_excerpt=prompt_excerpt,
            candidate_shapes=candidate_shapes,
        )
        if decision is None:
            return None
        selected = next(
            (
                candidate
                for candidate in safe_candidates
                if candidate.entry.template_id == decision.selected_candidate_id
            ),
            None,
        )
        if selected is None:
            return None
        binding_map = self._lrex_shape_binding_map(decision)
        plan = self._lrex_plan_from_entry(
            user_request,
            selected.entry,
            cache_id=selected.entry.template_id,
            prior_binding_map=binding_map,
        )
        if plan is None:
            return None
        self._emit_lrdirect(
            user_request,
            observability,
            event_type=OPERATOR_LRDIRECT_HIT,
            title="LR-EX semantic shape command hit",
            summary="A payload-aware streaming-step command matched through LR-EX semantic shape judging.",
            details={
                "cache_type": "payload_command_template",
                "template_id": selected.entry.template_id,
                "selected_candidate_id": selected.entry.template_id,
                "selected_score": selected.score,
                "lr_mode": LR_MODE_PAYLOAD,
                "exact_step_key": exact_key,
                "match_mode": "llm_shape_equivalence",
                "confidence": decision.confidence,
                "reason": decision.reason,
                "action_kind": "shell_command",
                "payload_binding_count": len(selected.entry.payload_bindings or []),
                "payload_binding_kinds": self._lrex_payload_binding_kinds(
                    selected.entry.payload_bindings
                ),
                "shape_llm_used": True,
                "bypassed_planning_llm": True,
                "bypassed_llm": False,
            },
        )
        return plan, selected, "payload_command_template"

    def _lrdirect_computation_shape_context(
        self, user_request: UserRequest
    ) -> ComputationCacheLookupContext:
        context = dict(user_request.session_context or {})
        current_task = self._lrdirect_streaming_task(user_request)
        model_name = self._active_memory_model_name(user_request)
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
                    "python_action python_transform direct replay",
                ]
            ),
            limit=40,
        )
        return ComputationCacheLookupContext(
            prompt=user_request.raw_prompt,
            mode=str(
                context.get("agent_mode") or context.get("operator_mode_label") or ""
            ).strip(),
            model_name=model_name,
            model_family=normalize_model_family(model_name),
            task_type=task_type,
            tool_type=task_type,
            intent_type=intent_type,
            tags=tags,
            action_kind="",
            action_reason=step_description,
            input_profile={},
            input_signature="",
            max_chars=int(self.config.agent_computation_cache_prompt_max_chars),
            similarity_threshold=float(
                self.config.agent_computation_cache_similarity_threshold
            ),
            excluded_cache_ids=[
                str(item)
                for item in list(
                    context.get("learning_ledger_suspect_computation_cache_ids")
                    or []
                )
                if str(item or "").strip()
            ],
        )

    def _try_lrdirect_python_shape_match_cache(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
        *,
        exact_key: str,
        prompt_excerpt: str,
        seed_candidates: list[ComputationCacheCandidate] | None = None,
    ) -> tuple[OperatorPlan, ComputationCacheCandidate, str] | None:
        if (
            self.computation_cache_store is None
            or getattr(self, "llm_client", None) is None
        ):
            return None
        candidates: list[ComputationCacheCandidate] = list(seed_candidates or [])
        seen_ids = {candidate.entry.cache_id for candidate in candidates}
        try:
            lookup = self._lrdirect_computation_shape_context(user_request)
            extra_candidates = (
                self.computation_cache_store.retrieve_direct_shape_candidates(
                    lookup,
                    exact_key=exact_key,
                    excluded_cache_ids=sorted(seen_ids),
                    limit=_LREX_SHAPE_MATCH_CANDIDATE_LIMIT,
                    pool_limit=_LREX_SHAPE_MATCH_POOL_LIMIT,
                )
            )
        except Exception as exc:
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_REJECTED,
                title="LR Direct Python semantic shape lookup failed",
                summary="The LR Direct Python semantic shape shortlist failed; normal planning will continue.",
                details={
                    "enabled": True,
                    "cache_type": "computation",
                    "match_mode": "llm_shape_equivalence",
                    "exact_step_key": exact_key,
                    "error": str(exc),
                    "bypassed_llm": False,
                },
                level="warning",
            )
            return None
        for candidate in extra_candidates:
            if candidate.entry.cache_id not in seen_ids:
                candidates.append(candidate)
                seen_ids.add(candidate.entry.cache_id)
        safe_candidates: list[ComputationCacheCandidate] = []
        for candidate in candidates:
            payload = dict(candidate.entry.direct_action or {})
            if payload.get("kind") not in {"python_action", "python_transform"}:
                continue
            if not self._lrex_shape_candidate_risk_compatible(
                user_request,
                risk=payload.get("risk"),
                effect_intent=payload.get("effect_intent"),
                semantic_verb=candidate.entry.intent_type,
                object_type=candidate.entry.task_type or candidate.entry.tool_type,
            ):
                continue
            safe_candidates.append(candidate)
        safe_candidates = safe_candidates[:_LREX_SHAPE_MATCH_CANDIDATE_LIMIT]
        candidate_shapes = [
            self._lrex_shape_computation_candidate_payload(candidate)
            for candidate in safe_candidates
        ]
        decision = self._lrex_shape_match_decision(
            user_request,
            observability,
            cache_type="computation",
            exact_key=exact_key,
            prompt_excerpt=prompt_excerpt,
            candidate_shapes=candidate_shapes,
        )
        if decision is None:
            return None
        selected = next(
            (
                candidate
                for candidate in safe_candidates
                if candidate.entry.cache_id == decision.selected_candidate_id
            ),
            None,
        )
        if selected is None:
            return None
        payload = dict(selected.entry.direct_action or {})
        try:
            plan = self._lrdirect_plan_from_action_payload(
                user_request,
                payload,
                cache_type="computation",
                cache_id=selected.entry.cache_id,
                prior_binding_map=self._lrex_shape_binding_map(decision),
            )
        except (PydanticValidationError, ValueError):
            return None
        if plan is None:
            return None
        self._emit_lrdirect(
            user_request,
            observability,
            event_type=OPERATOR_LRDIRECT_HIT,
            title="LR Direct Python semantic shape hit",
            summary="A direct Python streaming step matched through LR-EX semantic shape judging.",
            details={
                "cache_type": "computation",
                "cache_id": selected.entry.cache_id,
                "selected_candidate_id": selected.entry.cache_id,
                "selected_score": selected.score,
                "exact_step_key": exact_key,
                "match_mode": "llm_shape_equivalence",
                "confidence": decision.confidence,
                "reason": decision.reason,
                "action_kind": payload.get("kind"),
                "shape_llm_used": True,
                "bypassed_planning_llm": True,
                "bypassed_llm": False,
            },
        )
        return plan, selected, "computation"

    def _lrdirect_plan_from_action_payload(
        self,
        user_request: UserRequest,
        payload: dict[str, Any],
        *,
        cache_type: str,
        cache_id: str,
        prior_binding_map: dict[str, str] | None = None,
    ) -> OperatorPlan | None:
        """Rebuild a one-action streaming plan from a cached direct action."""

        task_payload = self._lrdirect_streaming_task(user_request)
        if not task_payload:
            return None
        task_id = str(
            task_payload.get("task_id") or task_payload.get("id") or "task_lrdirect"
        )
        action_payload = dict(payload)
        schema_version = action_payload.pop("lrdirect_schema_version", None)
        if schema_version != 2:
            raise ValueError("Unsupported LR Direct action payload schema.")
        if str(
            action_payload.get("kind") or ""
        ) == "shell_command" and not self._lrdirect_shell_action_is_raw_command(
            action_payload
        ):
            raise ValueError(
                "LR Direct shell command entries may not contain runtime inputs."
            )
        action_payload["action_id"] = new_id("action")
        action_payload["task_id"] = task_id
        action_payload["depends_on"] = []
        action_payload["reason"] = (
            f"LR Direct exact-step replay from {cache_type} cache {cache_id}."
        )
        scoring_action = OperatorAction.model_validate(
            {**action_payload, "input_bindings": []}
        )
        rebuilt_bindings: list[dict[str, Any]] = []
        for binding in list(action_payload.get("input_bindings") or []):
            if not isinstance(binding, dict):
                continue
            if binding.get("source") != "prior_streaming_result":
                raise ValueError(
                    "LR Direct input bindings must use prior_streaming_result."
                )
            source_field = (
                str(binding.get("source_field") or "stdout").strip() or "stdout"
            )
            candidates = self._streaming_prior_output_candidates(user_request)
            candidate = self._lrex_prior_candidate_from_shape_match(
                user_request,
                scoring_action,
                candidates,
                input_name=str(binding.get("input_name") or source_field),
                source_field=source_field,
                prior_binding_map=prior_binding_map,
            )
            if candidate is None:
                candidate = self._best_streaming_prior_candidate(
                    user_request,
                    scoring_action,
                    candidates,
                    preferred_field=source_field,
                )
            if candidate is None:
                raise ValueError(
                    "No current prior streaming result is available for LR Direct replay."
                )
            source_action_id = self._streaming_prior_binding_source_action_id(
                user_request,
                action=scoring_action,
                candidate=candidate,
            )
            rebuilt_bindings.append(
                {
                    "input_name": str(binding.get("input_name") or source_field),
                    "source_action_id": source_action_id,
                    "source_field": candidate["source_field"],
                    "required": bool(binding.get("required", True)),
                    "fallback_value": binding.get("fallback_value"),
                }
            )
        action_payload["input_bindings"] = rebuilt_bindings
        action = OperatorAction.model_validate(action_payload)
        action = action.model_copy(
            update={
                "task_id": task_id,
                "reason": f"LR Direct exact-step replay from {cache_type} cache {cache_id}.",
            }
        )
        semantic_verb = str(task_payload.get("semantic_verb") or "execute")
        object_type = str(task_payload.get("object_type") or action.kind)
        goal = str(
            task_payload.get("description")
            or user_request.raw_prompt
            or "Execute cached streaming step."
        )
        task = OperatorTask(
            task_id=task_id,
            goal=goal,
            semantic_verb=semantic_verb,
            object_type=object_type,
            dependencies=[],
            reason="Exact rendered streaming-step prompt matched a successful LR Direct cache entry.",
        )
        return OperatorPlan(
            summary="Replay an exact successful streaming step from LR Direct.",
            tasks=[task],
            actions=[action],
            dependencies=[],
            expected_outputs=["Output from the cached exact streaming step action."],
            assumptions=[],
            confidence=1.0,
        )

    def _try_lrex_direct_step_cache(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
        *,
        exact_key: str,
        prompt_excerpt: str,
    ) -> tuple[OperatorPlan, CommandTemplateCandidate, str] | None:
        """Try exact-step LR-EX command replay with fresh payload bindings."""

        if (
            self.command_template_cache_store is None
            or not self._command_template_cache_enabled(user_request)
        ):
            return None
        if not (
            operator_request_requires_generated_text(user_request)
            or bool(self._streaming_prior_output_candidates(user_request))
        ):
            return None
        try:
            command_candidates = self.command_template_cache_store.retrieve_exact_step(
                exact_key,
                lr_mode=LR_MODE_PAYLOAD,
                **self._lrdirect_replay_environment(user_request),
            )
            legacy_command_lookup = False
            if not command_candidates:
                command_candidates = self._legacy_lrdirect_command_candidates(
                    user_request,
                    lr_mode=LR_MODE_PAYLOAD,
                )
                legacy_command_lookup = bool(command_candidates)
        except Exception as exc:
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_REJECTED,
                title="LR-EX direct command lookup failed",
                summary="The exact payload-aware command cache lookup failed; normal planning will continue.",
                details={
                    "cache_type": "payload_command_template",
                    "lr_mode": LR_MODE_PAYLOAD,
                    "exact_step_key": exact_key,
                    "error": str(exc),
                },
                level="warning",
            )
            return None
        self._emit_lrdirect(
            user_request,
            observability,
            event_type=OPERATOR_LRDIRECT_LOOKUP,
            title="LR-EX direct lookup completed",
            summary="The runtime checked for exact payload-aware command replay entries.",
            details={
                "enabled": True,
                "cache_type": "payload_command_template",
                "lr_mode": LR_MODE_PAYLOAD,
                "exact_step_key": exact_key,
                "prompt_excerpt": prompt_excerpt,
                "command_candidate_count": len(command_candidates),
                "bypassed_llm": False,
                "legacy_fallback": legacy_command_lookup,
            },
        )
        for candidate in command_candidates:
            plan = self._lrex_plan_from_entry(
                user_request,
                candidate.entry,
                cache_id=candidate.entry.template_id,
            )
            if plan is None:
                continue
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_HIT,
                title="LR-EX direct command hit",
                summary="An exact payload-aware streaming-step command will be replayed with current payloads.",
                details={
                    "cache_type": "payload_command_template",
                    "template_id": candidate.entry.template_id,
                    "lr_mode": LR_MODE_PAYLOAD,
                    "exact_step_key": exact_key,
                    "action_kind": "shell_command",
                    "payload_binding_count": len(
                        candidate.entry.payload_bindings or []
                    ),
                    "payload_binding_kinds": self._lrex_payload_binding_kinds(
                        candidate.entry.payload_bindings
                    ),
                    "bypassed_llm": True,
                    "legacy_fallback": legacy_command_lookup,
                },
            )
            return plan, candidate, "payload_command_template"
        return self._try_lrex_shape_match_command_cache(
            user_request,
            observability,
            exact_key=exact_key,
            prompt_excerpt=prompt_excerpt,
            seed_candidates=command_candidates,
        )

    def _try_lrdirect_step_cache(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
    ) -> (
        tuple[OperatorPlan, CommandTemplateCandidate | ComputationCacheCandidate, str]
        | None
    ):
        """Try exact-step direct action replay before any LLM planning."""

        if not self._lrdirect_enabled(user_request):
            return None
        exact_key, prompt_excerpt = self._lrdirect_step_metadata(user_request)
        if not exact_key:
            return None
        lrex_cached = self._try_lrex_direct_step_cache(
            user_request,
            observability,
            exact_key=exact_key,
            prompt_excerpt=prompt_excerpt,
        )
        if lrex_cached is not None:
            return lrex_cached
        if operator_request_requires_generated_text(user_request):
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_LOOKUP,
                title="LR Direct lookup skipped",
                summary="Generated-text steps require fresh llm_text planning instead of direct shell replay.",
                details={"enabled": True, "candidate_count": 0, "bypassed_llm": False},
            )
            return None
        self._emit_lrdirect(
            user_request,
            observability,
            event_type=OPERATOR_LRDIRECT_LOOKUP,
            title="LR Direct lookup started",
            summary="The runtime checked for an exact successful streaming-step action.",
            details={
                "enabled": True,
                "exact_step_key": exact_key,
                "bypassed_llm": False,
            },
        )
        command_candidates: list[CommandTemplateCandidate] = []
        computation_candidates: list[ComputationCacheCandidate] = []
        legacy_command_lookup = False
        if (
            self.command_template_cache_store is not None
            and self._command_template_cache_enabled(user_request)
        ):
            try:
                command_candidates = (
                    self.command_template_cache_store.retrieve_exact_step(
                        exact_key,
                        **self._lrdirect_replay_environment(user_request),
                    )
                )
                if not command_candidates:
                    command_candidates = self._legacy_lrdirect_command_candidates(
                        user_request,
                        lr_mode=LR_MODE_DEFAULT,
                    )
                    legacy_command_lookup = bool(command_candidates)
            except Exception as exc:
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_REJECTED,
                    title="LR Direct command lookup failed",
                    summary="The exact command cache lookup failed; normal planning will continue.",
                    details={
                        "cache_type": "command_template",
                        "exact_step_key": exact_key,
                        "error": str(exc),
                    },
                    level="warning",
                )
        if (
            self.computation_cache_store is not None
            and self._computation_cache_enabled(user_request)
        ):
            try:
                computation_candidates = (
                    self.computation_cache_store.retrieve_exact_step(
                        exact_key,
                        require_direct_action=True,
                    )
                )
            except Exception as exc:
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_REJECTED,
                    title="LR Direct Python lookup failed",
                    summary="The exact Python cache lookup failed; normal planning will continue.",
                    details={
                        "cache_type": "computation",
                        "exact_step_key": exact_key,
                        "error": str(exc),
                    },
                    level="warning",
                )
        self._emit_lrdirect(
            user_request,
            observability,
            event_type=OPERATOR_LRDIRECT_LOOKUP,
            title="LR Direct lookup completed",
            summary="The runtime finished checking exact-step direct cache entries.",
            details={
                "enabled": True,
                "exact_step_key": exact_key,
                "prompt_excerpt": prompt_excerpt,
                "command_candidate_count": len(command_candidates),
                "python_candidate_count": len(computation_candidates),
                "bypassed_llm": False,
                "legacy_fallback": legacy_command_lookup,
            },
        )
        for candidate in command_candidates:
            payload = dict(candidate.entry.direct_action or {})
            if payload.get("kind") != "shell_command":
                continue
            try:
                plan = self._lrdirect_plan_from_action_payload(
                    user_request,
                    payload,
                    cache_type="command_template",
                    cache_id=candidate.entry.template_id,
                )
            except (PydanticValidationError, ValueError) as exc:
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_REJECTED,
                    title="LR Direct command entry rejected",
                    summary="The cached command action was invalid; normal planning will continue.",
                    details={
                        "cache_type": "command_template",
                        "template_id": candidate.entry.template_id,
                        "exact_step_key": exact_key,
                        "error": str(exc),
                    },
                    level="warning",
                )
                continue
            if plan is not None:
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_HIT,
                    title="LR Direct command hit",
                    summary="An exact successful streaming-step command will be replayed without LLM planning.",
                    details={
                        "cache_type": "command_template",
                        "template_id": candidate.entry.template_id,
                        "exact_step_key": exact_key,
                        "action_kind": "shell_command",
                        "bypassed_llm": True,
                        "legacy_fallback": legacy_command_lookup,
                    },
                )
                return plan, candidate, "command_template"
        for candidate in computation_candidates:
            payload = dict(candidate.entry.direct_action or {})
            if payload.get("kind") not in {"python_action", "python_transform"}:
                continue
            try:
                plan = self._lrdirect_plan_from_action_payload(
                    user_request,
                    payload,
                    cache_type="computation",
                    cache_id=candidate.entry.cache_id,
                )
            except (PydanticValidationError, ValueError) as exc:
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_REJECTED,
                    title="LR Direct Python entry rejected",
                    summary="The cached Python action was invalid; normal planning will continue.",
                    details={
                        "cache_type": "computation",
                        "cache_id": candidate.entry.cache_id,
                        "exact_step_key": exact_key,
                        "error": str(exc),
                    },
                    level="warning",
                )
                continue
            if plan is not None:
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_HIT,
                    title="LR Direct Python hit",
                    summary="An exact successful streaming-step Python action will be replayed without LLM planning.",
                    details={
                        "cache_type": "computation",
                        "cache_id": candidate.entry.cache_id,
                        "exact_step_key": exact_key,
                        "action_kind": payload.get("kind"),
                        "bypassed_llm": True,
                    },
                )
                return plan, candidate, "computation"
        python_shape_cached = self._try_lrdirect_python_shape_match_cache(
            user_request,
            observability,
            exact_key=exact_key,
            prompt_excerpt=prompt_excerpt,
            seed_candidates=computation_candidates,
        )
        if python_shape_cached is not None:
            return python_shape_cached
        return None


__all__ = ["_MemoryComplianceLrDirectMixin"]
