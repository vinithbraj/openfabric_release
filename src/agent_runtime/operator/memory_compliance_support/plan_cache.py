"""Plan cache helpers for operator memory compliance."""

from __future__ import annotations

from agent_runtime.operator.memory_compliance_support.common import *
from agent_runtime.operator.memory_compliance_support.common import _MemoryComplianceCommonMixin


class _MemoryCompliancePlanCacheMixin(_MemoryComplianceCommonMixin):
    def _plan_cache_enabled(self, user_request: UserRequest) -> bool:
        """Return whether the private operator cache may be used for this request."""

        if self.plan_cache_store is None or not bool(self.config.agent_plan_cache_enabled):
            return False
        if self._step_validation_repair_retry_active(user_request):
            return False
        context = dict(user_request.session_context or {})
        if (
            self._lrdirect_requested(user_request)
            and workflow_uses_streaming(
                context.get(
                    "workflow_execution_mode",
                    getattr(self.config, "workflow_execution_mode", "streaming"),
                )
            )
        ):
            return False
        if "agent_plan_cache_enabled" in context:
            return bool(context.get("agent_plan_cache_enabled"))
        return True

    def _plan_cache_context(
        self,
        user_request: UserRequest,
        *,
        brief: OperatorSelfBrief | None = None,
    ) -> PlanCacheLookupContext:
        """Build the strongly typed cache lookup shape for the current request."""

        context = dict(user_request.session_context or {})
        model_name = self._active_memory_model_name(user_request)
        mode = str(context.get("agent_mode") or context.get("operator_mode_label") or "").strip()
        tags: list[str] = _memory_tag_tokens(user_request.raw_prompt, limit=32)
        task_type = ""
        tool_type = ""
        intent_type = ""
        if brief is not None:
            contract = brief.verification_contract
            task_type = str(contract.goal or "").strip()
            intent_type = str(contract.freshness or "").strip()
            tags = self._memory_tags_from_self_brief(user_request, brief)
        intent_block = context.get("operator_intent_block")
        if isinstance(intent_block, dict):
            classification = intent_block.get("classification")
            if isinstance(classification, dict):
                task_type = task_type or str(classification.get("prompt_type") or "").strip()
                intent_type = intent_type or str(classification.get("risk_level") or "").strip()
                domains = classification.get("likely_domains")
                if isinstance(domains, list):
                    tags.extend(str(item) for item in domains if str(item or "").strip())
            for task in list(intent_block.get("tasks") or [])[:4]:
                if isinstance(task, dict):
                    tool_type = tool_type or str(task.get("object_type") or "").strip()
                    tags.extend(
                        str(task.get(key) or "")
                        for key in ("description", "semantic_verb", "object_type")
                    )
        return PlanCacheLookupContext(
            prompt=user_request.raw_prompt,
            mode=mode,
            model_name=model_name,
            model_family=normalize_model_family(model_name),
            cwd=str(context.get("terminal_cwd") or ""),
            task_type=task_type,
            tool_type=tool_type,
            intent_type=intent_type,
            tags=_memory_tag_tokens(" ".join(tags), limit=40),
            max_chars=int(self.config.agent_plan_cache_prompt_max_chars),
            similarity_threshold=float(self.config.agent_plan_cache_similarity_threshold),
            excluded_cache_ids=[
                str(item)
                for item in list(context.get("learning_ledger_suspect_plan_cache_ids") or [])
                if str(item or "").strip()
            ],
        )

    def _emit_plan_cache_lookup(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
        *,
        title: str,
        summary: str,
        details: dict[str, Any],
    ) -> None:
        """Emit private cache lookup/write status into the developer trace."""

        trace = user_request.safety_context.get("planning_trace")
        metadata = getattr(trace, "metadata", None)
        if isinstance(metadata, dict):
            metadata["operator_plan_cache"] = dict(details)
        if observability is None:
            return
        observability.info(
            OPERATOR_STAGE,
            OPERATOR_PLAN_CACHE_LOOKUP,
            title,
            summary,
            details=details,
            debug_only=True,
        )

    def _try_plan_cache_adaptation(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
    ) -> tuple[OperatorPlan, PlanCacheCandidate] | None:
        """Try adapting a strong private cache hit before full planning."""

        if not self._plan_cache_enabled(user_request):
            self._emit_plan_cache_lookup(
                user_request,
                observability,
                title="Operator cache skipped",
                summary="The private operator cache is disabled or unavailable for this request.",
                details={"enabled": False, "candidate_count": 0},
            )
            return None
        assert self.plan_cache_store is not None
        lookup = self._plan_cache_context(
            user_request,
            brief=_operator_self_brief_from_request(user_request),
        )
        try:
            candidates = self.plan_cache_store.retrieve(lookup, record_use=False)
        except Exception as exc:
            self._emit_plan_cache_lookup(
                user_request,
                observability,
                title="Operator cache lookup failed",
                summary="The private operator cache lookup failed; normal planning will continue.",
                details={"enabled": True, "candidate_count": 0, "error": str(exc)},
            )
            return None
        self._emit_plan_cache_lookup(
            user_request,
            observability,
            title="Operator cache lookup completed",
            summary="The private operator cache checked for reusable plan structure.",
            details={
                "enabled": True,
                "candidate_count": len(candidates),
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
        cached_brief = candidate.entry.self_brief
        if isinstance(cached_brief, dict) and cached_brief:
            try:
                brief = OperatorSelfBrief.model_validate(cached_brief)
            except Exception:
                brief = None
            if brief is not None:
                user_request.session_context["operator_self_brief"] = brief.model_dump(mode="json")
                if not bool(user_request.session_context.get("agent_memory_retrieved_after_self_brief")):
                    self._attach_memory_after_self_brief(user_request, brief, observability)
        prompt = build_operator_plan_cache_prompt(user_request, candidate)
        try:
            decision = structured_call(self.llm_client, prompt, OperatorPlanCacheDecision)
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_VALIDATION_REJECTED,
                title="Operator cache adaptation rejected",
                summary="The cached plan adaptation did not produce a valid typed decision.",
                details={
                    "cache_id": candidate.entry.cache_id,
                    "error": str(exc),
                },
            )
            return None
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_PLAN_CACHE_HIT,
            title="Operator cache adaptation decision",
            summary="The LLM reviewed a private cached plan skeleton for reuse.",
            details={
                **decision.model_dump(mode="json"),
                "cache_id": candidate.entry.cache_id,
                "score": candidate.score,
            },
        )
        if decision.decision == "use_adapted_plan" and decision.adapted_plan is not None:
            return decision.adapted_plan, candidate
        return None



__all__ = ["_MemoryCompliancePlanCacheMixin"]
