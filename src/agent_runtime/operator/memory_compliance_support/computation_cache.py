"""Computation cache helpers for operator memory compliance."""

from __future__ import annotations

from agent_runtime.operator.memory_compliance_support.common import *
from agent_runtime.operator.memory_compliance_support.common import _MemoryComplianceCommonMixin


class _MemoryComplianceComputationCacheMixin(_MemoryComplianceCommonMixin):
    def _computation_cache_enabled(self, user_request: UserRequest) -> bool:
        """Return whether computation templates may be used for this request."""

        if self.computation_cache_store is None or not bool(self.config.agent_computation_cache_enabled):
            return False
        if self._step_validation_repair_retry_active(user_request):
            return False
        context = dict(user_request.session_context or {})
        if "agent_computation_cache_enabled" in context:
            return bool(context.get("agent_computation_cache_enabled"))
        if "agent_plan_cache_enabled" in context and not bool(context.get("agent_plan_cache_enabled")):
            return False
        return True

    @staticmethod
    def _stable_computation_tool_type(action: OperatorAction) -> str:
        """Return a cache-stable operation bucket for generated computation code."""

        task_id = str(action.task_id or "").strip()
        if task_id and not re.fullmatch(r"(?i)(task|step|action)[_-]?\d+", task_id):
            return task_id
        shape = str(action.declared_output_shape or "text").strip() or "text"
        return f"{action.kind}:{shape}"

    def _computation_cache_context(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        action_inputs: dict[str, Any],
    ) -> ComputationCacheLookupContext:
        """Build the typed cache lookup shape for one deferred computation action."""

        context = dict(user_request.session_context or {})
        model_name = self._active_memory_model_name(user_request)
        mode = str(context.get("agent_mode") or context.get("operator_mode_label") or "").strip()
        tags = _memory_tag_tokens(
            " ".join(
                [
                    user_request.raw_prompt,
                    action.kind,
                    action.reason,
                    action.declared_output_shape,
                ]
            ),
            limit=40,
        )
        brief = _operator_self_brief_from_request(user_request)
        task_type = ""
        intent_type = ""
        if brief is not None:
            task_type = str(brief.verification_contract.goal or "").strip()
            intent_type = str(brief.verification_contract.freshness or "").strip()
            tags.extend(self._memory_tags_from_self_brief(user_request, brief))
        profile = computation_input_profile(action_inputs)
        return ComputationCacheLookupContext(
            prompt=user_request.raw_prompt,
            mode=mode,
            model_name=model_name,
            model_family=normalize_model_family(model_name),
            task_type=task_type,
            tool_type=self._stable_computation_tool_type(action),
            intent_type=intent_type,
            tags=_memory_tag_tokens(" ".join(tags), limit=40),
            action_kind=action.kind,
            action_reason=action.reason,
            input_profile=profile,
            input_signature=computation_input_signature(profile),
            max_chars=int(self.config.agent_computation_cache_prompt_max_chars),
            similarity_threshold=float(self.config.agent_computation_cache_similarity_threshold),
            excluded_cache_ids=[
                str(item)
                for item in list(context.get("learning_ledger_suspect_computation_cache_ids") or [])
                if str(item or "").strip()
            ],
        )

    def _emit_computation_cache(
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
        """Emit private computation-cache status into the developer trace."""

        trace = user_request.safety_context.get("planning_trace")
        metadata = getattr(trace, "metadata", None)
        if isinstance(metadata, dict):
            metadata["operator_computation_cache"] = dict(details)
        self._emit(
            observability,
            level=level,
            event_type=event_type,
            title=title,
            summary=summary,
            details=details,
        )

    def _try_computation_cache_adaptation(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        action_inputs: dict[str, Any],
        *,
        runtime_contract: dict[str, Any],
        input_packet: dict[str, Any],
        observability: ObservabilityContext | None,
    ) -> tuple[OperatorPythonCodeProposal, ComputationCacheCandidate] | None:
        """Try adapting a cached computation template before fresh code generation."""

        if not self._computation_cache_enabled(user_request):
            self._emit_computation_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMPUTATION_CACHE_LOOKUP,
                title="Computation cache skipped",
                summary="The private computation template cache is disabled or unavailable.",
                details={"enabled": False, "candidate_count": 0, "action_id": action.action_id},
                level="info",
            )
            return None
        assert self.computation_cache_store is not None
        lookup = self._computation_cache_context(user_request, action, action_inputs)
        exact_key, prompt_excerpt = self._lrdirect_step_metadata(user_request)
        if self._lrdirect_enabled(user_request) and exact_key:
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_LOOKUP,
                title="LR Direct Python code lookup started",
                summary="The runtime checked for exact cached deferred Python code.",
                details={
                    "cache_type": "computation",
                    "exact_step_key": exact_key,
                    "action_id": action.action_id,
                    "action_kind": action.kind,
                    "input_signature": lookup.input_signature,
                    "bypassed_llm": False,
                },
            )
            try:
                exact_candidates = self.computation_cache_store.retrieve_exact_step(
                    exact_key,
                    action_kind=action.kind,
                    input_signature=lookup.input_signature,
                    require_direct_action=False,
                    record_use=False,
                    limit=1,
                )
            except Exception as exc:
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_REJECTED,
                    title="LR Direct Python code lookup failed",
                    summary="The exact deferred Python cache lookup failed; normal code generation will continue.",
                    details={
                        "cache_type": "computation",
                        "exact_step_key": exact_key,
                        "action_id": action.action_id,
                        "error": str(exc),
                    },
                    level="warning",
                )
                exact_candidates = []
            self._emit_lrdirect(
                user_request,
                observability,
                event_type=OPERATOR_LRDIRECT_LOOKUP,
                title="LR Direct Python code lookup completed",
                summary="The runtime finished checking exact cached deferred Python code.",
                details={
                    "cache_type": "computation",
                    "exact_step_key": exact_key,
                    "prompt_excerpt": prompt_excerpt,
                    "action_id": action.action_id,
                    "action_kind": action.kind,
                    "candidate_count": len(exact_candidates),
                    "bypassed_llm": False,
                },
            )
            if exact_candidates:
                candidate = exact_candidates[0]
                proposal = OperatorPythonCodeProposal(
                    code=str(candidate.entry.code_template or ""),
                    declared_output_shape=candidate.entry.declared_output_shape,
                    allow_zero_result=candidate.entry.allow_zero_result,
                    reason=f"LR Direct exact deferred Python replay from computation cache {candidate.entry.cache_id}.",
                    confidence=1.0,
                )
                user_request.session_context["operator_lrdirect_computation_codegen"] = {
                    "cache_type": "computation",
                    "cache_id": candidate.entry.cache_id,
                    "exact_step_key": exact_key,
                    "action_id": action.action_id,
                    "action_kind": action.kind,
                    "bypassed_llm": True,
                }
                self._emit_lrdirect(
                    user_request,
                    observability,
                    event_type=OPERATOR_LRDIRECT_HIT,
                    title="LR Direct Python code hit",
                    summary="Exact cached deferred Python code will be proof-checked without LLM codegen or review.",
                    details={
                        "cache_type": "computation",
                        "cache_id": candidate.entry.cache_id,
                        "exact_step_key": exact_key,
                        "action_id": action.action_id,
                        "action_kind": action.kind,
                        "bypassed_llm": True,
                    },
                )
                return proposal, candidate
        try:
            candidates = self.computation_cache_store.retrieve(lookup, record_use=False)
        except Exception as exc:
            self._emit_computation_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMPUTATION_CACHE_LOOKUP,
                title="Computation cache lookup failed",
                summary="The private computation template cache lookup failed; fresh code generation will continue.",
                details={"enabled": True, "candidate_count": 0, "action_id": action.action_id, "error": str(exc)},
                level="warning",
            )
            return None
        self._emit_computation_cache(
            user_request,
            observability,
            event_type=OPERATOR_COMPUTATION_CACHE_LOOKUP,
            title="Computation cache lookup completed",
            summary="The private computation template cache checked for reusable code structure.",
            details={
                "enabled": True,
                "candidate_count": len(candidates),
                "action_id": action.action_id,
                "lookup": lookup.model_dump(mode="json"),
                "candidates": [
                    {
                        "cache_id": candidate.entry.cache_id,
                        "score": candidate.score,
                        "reason": candidate.reason,
                    }
                    for candidate in candidates
                ],
            },
        )
        if not candidates:
            return None
        candidate = candidates[0]
        prompt = build_operator_computation_cache_prompt(
            user_request,
            action,
            candidate,
            runtime_contract=runtime_contract,
            input_packet=input_packet,
        )
        try:
            decision = structured_call(self.llm_client, prompt, OperatorComputationCacheDecision)
        except Exception as exc:
            self._emit_computation_cache(
                user_request,
                observability,
                event_type=OPERATOR_COMPUTATION_CACHE_HIT,
                title="Computation cache adaptation rejected",
                summary="The cached computation adaptation did not produce a valid typed decision.",
                details={"cache_id": candidate.entry.cache_id, "error": str(exc)},
                level="warning",
            )
            return None
        self._emit_computation_cache(
            user_request,
            observability,
            event_type=OPERATOR_COMPUTATION_CACHE_HIT,
            title="Computation cache adaptation decision",
            summary="The LLM reviewed a private cached computation template for reuse.",
            details={
                **decision.model_dump(mode="json"),
                "cache_id": candidate.entry.cache_id,
                "score": candidate.score,
            },
        )
        if decision.decision in {"use_template", "adapt_template"} and decision.proposal is not None:
            return decision.proposal, candidate
        return None



__all__ = ["_MemoryComplianceComputationCacheMixin"]
