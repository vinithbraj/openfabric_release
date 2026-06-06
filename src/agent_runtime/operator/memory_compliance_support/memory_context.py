"""Memory context attachment helpers for operator memory compliance."""

from __future__ import annotations

from agent_runtime.operator.memory_compliance_support.common import *
from agent_runtime.operator.memory_compliance_support.common import _MemoryComplianceCommonMixin


class _MemoryComplianceContextMixin(_MemoryComplianceCommonMixin):
    @staticmethod
    def _step_validation_repair_retry_active(user_request: UserRequest) -> bool:
        """Return true while replanning one step from validation repair feedback."""

        context = dict(user_request.session_context or {})
        if not bool(context.get("operator_bypass_lr_for_step_validation_retry")):
            return False
        feedback = context.get("operator_step_validation_feedback")
        if not isinstance(feedback, dict):
            return False
        decision = str(feedback.get("decision") or "").strip().lower()
        return decision in {"repair", "continue_with_more_evidence"}

    def _active_memory_model_name(self, user_request: UserRequest) -> str:
        """Return the best model identifier available for model-scoped memory."""

        context = dict(user_request.session_context or {})
        for key in ("active_llm_model", "llm_model"):
            value = str(context.get(key) or "").strip()
            if value and value.lower() != "auto":
                return value
        model_name, _ = llm_client_metadata(self.llm_client)
        model_name = str(model_name or "").strip()
        return "" if model_name.lower() == "auto" else model_name

    @staticmethod
    def _memory_tags_from_self_brief(user_request: UserRequest, brief: OperatorSelfBrief) -> list[str]:
        """Derive targeted retrieval tags after the task has been self-briefed."""

        contract = brief.verification_contract
        pieces: list[str] = [
            user_request.raw_prompt,
            brief.task_understanding,
            brief.success_postcondition,
            brief.recommended_strategy,
            brief.verification_strategy,
            contract.goal,
            contract.observable_state,
            " ".join(contract.acceptable_evidence),
            " ".join(brief.key_domain_facts[:5]),
            " ".join(brief.likely_pitfalls[:5]),
        ]
        context = dict(user_request.session_context or {})
        intent_block = context.get("operator_intent_block")
        if isinstance(intent_block, dict):
            pieces.append(_stable_json(intent_block.get("classification", {})))
            for task in list(intent_block.get("tasks") or [])[:8]:
                if isinstance(task, dict):
                    pieces.extend(
                        [
                            task.get("description", ""),
                            task.get("semantic_verb", ""),
                            task.get("object_type", ""),
                        ]
                    )
        for key in ("agent_mode", "mode", "operator_mode_label"):
            value = str(context.get(key) or "").strip()
            if value:
                pieces.append(value)
        base_tags = _memory_tag_tokens(" ".join(str(piece or "") for piece in pieces), limit=40)
        detection = detect_domain_hints(user_request.raw_prompt, existing_tags=base_tags)
        return _memory_tag_tokens(" ".join(detection.tags), limit=40)

    def _emit_memory_check(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
        *,
        title: str,
        summary: str,
        details: dict[str, Any],
    ) -> None:
        """Emit memory retrieval status and mirror compact metadata onto the trace."""

        trace = user_request.safety_context.get("planning_trace")
        metadata = getattr(trace, "metadata", None)
        if isinstance(metadata, dict):
            update_memory_scope_trace(metadata, details, user_request.session_context)
        if observability is None:
            return
        observability.stage_started(
            OPERATOR_MEMORY_CHECK,
            "Memory check started",
            "The runtime is checking persistent memory after task self-briefing.",
        )
        observability.stage_completed(
            OPERATOR_MEMORY_CHECK,
            title,
            summary,
            details=details,
        )

    def _attach_memory_for_stage(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
        *,
        stage: str,
        source: str,
        brief: OperatorSelfBrief | None = None,
    ) -> None:
        """Retrieve and activate memory for the current stage/subtask scope."""

        context = user_request.session_context
        if self.memory_store is None and context.get("agent_memory_directives") and not isinstance(
            context.get(SCOPED_CACHE_KEY),
            dict,
        ):
            return
        if self.memory_store is None or not bool(self.config.agent_memory_enabled):
            scope = scoped_empty_memory_scope(
                user_request,
                stage=stage,
                source=source,
                disabled=True,
            )
            set_active_memory_scope(user_request.session_context, scope=scope)
            user_request.session_context["agent_memory_disabled"] = True
            self._emit_memory_check(
                user_request,
                observability,
                title="Memory check disabled",
                summary="Persistent memory is disabled for this scoped request stage.",
                details=scope_observability_details(scope, memory_count=0),
            )
            return

        retrieval_prompt = scoped_retrieval_prompt(
            user_request,
            stage=stage,
            brief=brief,
        )
        base_tags = _memory_tag_tokens(
            " ".join(
                scoped_memory_tags(
                    user_request,
                    pre_clarification_tags=self._pre_clarification_memory_tags(user_request),
                    self_brief_tags=(
                        self._memory_tags_from_self_brief(user_request, brief)
                        if brief is not None
                        else None
                    ),
                )
            ),
            limit=40,
        )
        current_task = scoped_current_task(user_request.session_context)
        task_type = str(current_task.get("object_type") or "").strip()
        if brief is not None and not task_type:
            goal = str(brief.verification_contract.goal or "").strip()
            task_type = "" if goal in {"other", "listing_or_summary", "computed_answer"} else goal
        hints = enrich_memory_retrieval_hints(
            retrieval_prompt or user_request.raw_prompt,
            task_type=task_type,
            tags=base_tags,
        )
        model_name = self._active_memory_model_name(user_request)
        retrieval = MemoryRetrievalContext(
            prompt=retrieval_prompt or user_request.raw_prompt,
            model_name=model_name,
            model_family=normalize_model_family(model_name),
            task_type=hints.task_type,
            tool_type="",
            intent_type="",
            tags=hints.tags,
            max_chars=int(self.config.agent_memory_prompt_max_chars),
        )
        scope_id = scoped_memory_scope_id(
            user_request,
            stage=stage,
            source=source,
            retrieval=retrieval,
        )
        scope = cached_memory_scope(user_request.session_context, scope_id)
        if scope is not None:
            set_active_memory_scope(user_request.session_context, scope=scope)
            self._emit_memory_check(
                user_request,
                observability,
                title="Memory check reused",
                summary="Reused scoped persistent memory for this stage and subtask.",
                details=scope_observability_details(scope),
            )
            return

        try:
            matches = self.memory_store.retrieve_matches(retrieval)
        except Exception as exc:
            scope = scoped_empty_memory_scope(
                user_request,
                stage=stage,
                source=source,
                retrieval=retrieval,
                error=str(exc),
            )
            set_active_memory_scope(user_request.session_context, scope=scope)
            user_request.session_context["agent_memory_error"] = str(exc)
            self._emit_memory_check(
                user_request,
                observability,
                title="Memory check failed",
                summary="Persistent memory retrieval failed for this scoped stage.",
                details=scope_observability_details(scope, memory_count=0, error=str(exc)),
            )
            return

        entries = [match.entry for match in matches]
        entry_payloads = [entry.model_dump(mode="json") for entry in entries]
        match_payloads = [match.model_dump(mode="json") for match in matches]
        directives = [
            directive.model_dump(mode="json")
            for directive in memory_directives_from_entries(
                entry_payloads,
                match_details=match_payloads,
            )
        ]
        memory_use_counts = {entry.memory_id: int(entry.use_count or 0) for entry in entries}
        scope = memory_scope_from_matches(
            user_request,
            stage=stage,
            source=source,
            retrieval=retrieval,
            entry_payloads=entry_payloads,
            match_payloads=match_payloads,
            directives=directives,
            memory_use_counts=memory_use_counts,
        )
        set_active_memory_scope(user_request.session_context, scope=scope)
        self._emit_memory_check(
            user_request,
            observability,
            title="Memory check completed",
            summary=f"Retrieved {len(entries)} relevant memory item(s) for this scoped stage.",
            details=scope_observability_details(scope),
        )

    @staticmethod
    def _pre_clarification_memory_tags(user_request: UserRequest) -> list[str]:
        """Return coarse tags from current operator context for memory retrieval."""

        context = dict(user_request.session_context or {})
        raw_items: list[Any] = []
        intent_block = context.get("operator_intent_block")
        if isinstance(intent_block, dict):
            raw_items.extend(list(intent_block.get("tasks") or []))
            constraints = intent_block.get("global_constraints")
            if constraints:
                raw_items.append(constraints)
        current_task = context.get("operator_streaming_current_task")
        if isinstance(current_task, dict):
            raw_items.append(current_task)
        tasks = context.get("operator_streaming_tasks")
        if isinstance(tasks, list):
            raw_items.extend(item for item in tasks if isinstance(item, dict))
        tags: list[str] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            for key in (
                "semantic_verb",
                "object_type",
                "description",
                "goal",
                "task_id",
            ):
                value = str(item.get(key) or "").strip().lower()
                if not value:
                    continue
                tags.extend(
                    token
                    for token in re.findall(r"[a-z0-9]+", value)
                    if len(token) > 1
                )
        return list(dict.fromkeys(tags))[:24]

    def _attach_memory_before_clarification(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
    ) -> None:
        """Retrieve stage-relevant memory before the clarification gate runs."""

        self._attach_memory_for_stage(
            user_request,
            observability,
            stage="clarification",
            source="pre_clarification",
        )
        user_request.session_context["agent_memory_retrieved_before_clarification"] = True
        if user_request.session_context.get("agent_memory"):
            user_request.session_context["agent_memory_retrieved_after_self_brief"] = True

    def _attach_memory_after_self_brief(
        self,
        user_request: UserRequest,
        brief: OperatorSelfBrief,
        observability: ObservabilityContext | None,
    ) -> None:
        """Retrieve relevant memory only after the task has enough classified shape."""

        self._attach_memory_for_stage(
            user_request,
            observability,
            stage="operator_plan",
            source="after_self_brief",
            brief=brief,
        )
        user_request.session_context["agent_memory_retrieved_after_self_brief"] = True



__all__ = ["_MemoryComplianceContextMixin"]
