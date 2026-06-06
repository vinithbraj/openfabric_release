"""Clarification decision and policy review helpers."""

from __future__ import annotations

from .common import *
from .confirmation_payloads import *
from .policy_gates import *
from .memory_questions import *
from .resolution_prompt import *


class _ClarificationDecisionMixin:
    """Clarification decision and policy review helpers."""

    @staticmethod
    def _memory_question_policy_context(user_request: UserRequest) -> dict[str, Any]:
        """Return bounded request context for LLM-owned memory question review."""

        context = dict(user_request.session_context or {})

        def _compact_value(value: Any, *, depth: int = 0) -> Any:
            if value is None or isinstance(value, (bool, int, float)):
                return value
            if isinstance(value, str):
                return value[:4000] if depth <= 1 else value[:1200]
            if depth >= 5:
                return str(value)[:800]
            if isinstance(value, dict):
                return {
                    str(key)[:120]: _compact_value(item, depth=depth + 1)
                    for key, item in list(value.items())[:40]
                }
            if isinstance(value, list):
                limit = 12 if depth <= 1 else 8
                return [_compact_value(item, depth=depth + 1) for item in value[:limit]]
            return str(value)[:800]

        def _copy_dict(value: Any) -> dict[str, Any]:
            compact = _compact_value(value) if isinstance(value, dict) else {}
            return compact if isinstance(compact, dict) else {}

        def _copy_dict_list(value: Any, *, limit: int = 8) -> list[dict[str, Any]]:
            if not isinstance(value, list):
                return []
            items: list[dict[str, Any]] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                compact = _compact_value(item)
                if isinstance(compact, dict):
                    items.append(compact)
                if len(items) >= limit:
                    break
            return items

        def _task_step_summary() -> dict[str, Any]:
            current_task = _copy_dict(context.get("operator_streaming_current_task"))
            streaming_tasks = _copy_dict_list(context.get("operator_streaming_tasks"), limit=16)
            intent_block = _copy_dict(context.get("operator_intent_block"))
            intent_tasks = intent_block.get("tasks") if isinstance(intent_block, dict) else []
            intent_task_list = intent_tasks if isinstance(intent_tasks, list) else []
            current_id = str(
                current_task.get("task_id")
                or current_task.get("id")
                or context.get("operator_streaming_step_id")
                or ""
            ).strip()

            def _step_payload(task: Any, *, source: str, index: int) -> dict[str, Any]:
                if not isinstance(task, dict):
                    return {}
                task_id = str(task.get("task_id") or task.get("id") or "").strip()
                role = "current" if current_id and task_id == current_id else "workflow"
                return {
                    "source": source,
                    "index": index,
                    "role": role,
                    "task_id": task_id,
                    "description": str(task.get("description") or task.get("goal") or "")[:1200],
                    "semantic_verb": str(task.get("semantic_verb") or "")[:200],
                    "object_type": str(task.get("object_type") or "")[:200],
                    "dependencies": list(task.get("dependencies") or [])[:12]
                    if isinstance(task.get("dependencies"), list)
                    else [],
                    "constraints": _compact_value(task.get("constraints") or {}),
                }

            steps: list[dict[str, Any]] = []
            for index, task in enumerate(streaming_tasks):
                payload = _step_payload(task, source="operator_streaming_tasks", index=index)
                if payload:
                    steps.append(payload)
            if not steps:
                for index, task in enumerate(intent_task_list[:16]):
                    payload = _step_payload(task, source="operator_intent_block.tasks", index=index)
                    if payload:
                        steps.append(payload)
            if current_task and not any(step.get("role") == "current" for step in steps):
                payload = _step_payload(current_task, source="operator_streaming_current_task", index=0)
                if payload:
                    payload["role"] = "current"
                    steps.insert(0, payload)
            return {
                "current_task_id": current_id,
                "steps": steps[:16],
            }

        literal_payload_summaries: list[dict[str, Any]] = []
        for payload in literal_payloads_from_context(context)[:8]:
            value = str(payload.get("value") or "")
            literal_payload_summaries.append(
                {
                    "payload_id": str(payload.get("payload_id") or ""),
                    "kind": str(payload.get("kind") or ""),
                    "label": str(payload.get("label") or ""),
                    "input_name": str(payload.get("input_name") or ""),
                    "placeholder": str(payload.get("placeholder") or ""),
                    "value_present": bool(value.strip()),
                    "value_length": len(value),
                }
            )

        return {
            "raw_prompt": _compact_value(str(user_request.raw_prompt or "")),
            "streaming_original_prompt": _compact_value(
                str(
                    context.get("streaming_original_prompt")
                    or context.get("operator_streaming_original_prompt")
                    or ""
                )
            ),
            "operator_original_prompt": _compact_value(
                str(context.get("operator_original_prompt") or "")
            ),
            "original_prompt": _compact_value(str(context.get("original_prompt") or "")),
            "workflow_step_summary": _task_step_summary(),
            "operator_streaming_current_task": _copy_dict(
                context.get("operator_streaming_current_task")
            ),
            "operator_streaming_tasks": _copy_dict_list(
                context.get("operator_streaming_tasks"),
                limit=12,
            ),
            "operator_intent_block": _copy_dict(context.get("operator_intent_block")),
            "previous_clarifications": _copy_dict_list(context.get("clarifications"), limit=5),
            "literal_payload_summaries": literal_payload_summaries,
            "operator_seed_records": _copy_dict_list(context.get("operator_seed_records"), limit=8),
            "operator_streaming_prior_results": _copy_dict(
                context.get("operator_streaming_prior_results")
            ),
            "operator_streaming_completed_task_ids": list(
                context.get("operator_streaming_completed_task_ids") or []
            )[:24]
            if isinstance(context.get("operator_streaming_completed_task_ids"), list)
            else [],
        }

    def _resolve_clarification_before_ask(
        self,
        user_request: UserRequest,
        request: OperatorClarificationRequest,
        observability: ObservabilityContext | None,
        *,
        phase: str,
        reason: str = "",
    ) -> AgentClarificationResolution:
        mode = _agent_clarification_mode_from_request(user_request)
        risk_flags = _clarification_request_risk_flags(request)
        start_details = {
            "phase": phase,
            "mode": mode,
            "proposed_question": request.model_dump(mode="json"),
            "risk_flags": risk_flags,
        }
        self._emit(
            observability,
            level="info",
            event_type="clarification.resolution.started",
            title="Clarification resolution started",
            summary="The runtime is asking the typed clarification resolver whether it can proceed.",
            details=start_details,
        )
        resolution = resolve_agent_clarification(
            llm_client=self.llm_client,
            mode=mode,
            user_prompt=str(user_request.raw_prompt or ""),
            proposed_question=request.question,
            missing_information=request.missing_information,
            reason=reason or request.reason,
            options=_clarification_options_payload(request),
            candidates=_clarification_options_payload(request),
            context=_clarification_resolution_context(user_request),
            risk_flags=risk_flags,
        )
        if resolution.decision == "continue_with_assumption":
            _apply_clarification_resolution_to_request(user_request, resolution)
            self._emit(
                observability,
                level="info",
                event_type="clarification.auto_resolved",
                title="Clarification auto-resolved",
                summary="The typed clarification resolver supplied assumptions for the next agent step.",
                details=_operator_clarification_resolution_details(resolution, phase=phase),
            )
            return resolution
        self._emit(
            observability,
            level="info",
            event_type="clarification.resolution.ask_user",
            title="Clarification resolution asks user",
            summary="The typed clarification resolver kept the user-facing clarification.",
            details=_operator_clarification_resolution_details(resolution, phase=phase),
        )
        return resolution

    def _reject_clarification_by_policy(
        self,
        user_request: UserRequest,
        request: OperatorClarificationRequest,
        observability: ObservabilityContext | None,
        *,
        phase: str,
    ) -> _ClarificationPolicyReview | None:
        """Reject inadmissible clarification pauses and add a planning note."""

        review = _review_operator_clarification_policy(user_request, request)
        if review.allowed:
            return None
        _append_operator_policy_note(
            user_request,
            phase=phase,
            request=request,
            review=review,
        )
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_CLARIFICATION_REJECTED,
            title="Operator clarification rejected by runtime policy",
            summary=(
                "The LLM asked for information that should be handled without "
                "another user pause."
            ),
            details={
                "phase": phase,
                "reason": review.reason,
                "instruction": review.instruction,
                "clarification_request": request.model_dump(mode="json"),
            },
        )
        return review

    def _review_memory_question_policy(
        self,
        user_request: UserRequest,
        clarification_request: OperatorClarificationRequest,
        memory_details: dict[str, Any],
        observability: ObservabilityContext | None = None,
    ) -> OperatorPolicyDecision:
        """Review whether persistent memory should force this user question."""

        subject_id = (
            str(memory_details.get("memory_id") or "").strip()
            or str(memory_details.get("missing_information") or "").strip()
            or "memory_question"
        )
        subject = OperatorPolicySubject(
            module="memory_question",
            subject_id=subject_id,
            context={
                "memory_question_decision_contract": {
                    "policy_owner": "llm",
                    "memory_directive_role": (
                        "candidate_clarification_requirement_not_binding_conclusion"
                    ),
                    "core_question": (
                        "Will the missing information be supplied by the prompt, prior context, "
                        "or any requested/current/later workflow step before it is consumed?"
                    ),
                    "ask_user_only_if": (
                        "The memory is relevant and the workflow forecast does not show any "
                        "prompt/context value or step expected to produce the missing information."
                    ),
                    "allow_when_already_answered": (
                        "Return allow with repair_hint already_answered when prompt, prior "
                        "clarifications, literal payloads, seed records, or prior outputs supply it."
                    ),
                    "allow_when_future_step_answers": (
                        "Return allow with repair_hint future_step_answers when an explicit "
                        "requested/current/later step is expected to generate, transform, render, "
                        "draft, discover, inspect, collect, read, fetch, calculate, or otherwise "
                        "produce the missing information before a later step consumes it."
                    ),
                    "do_not_do": (
                        "Do not ask merely because the memory text says the user must provide "
                        "the value; first forecast whether the workflow itself provides it."
                    ),
                },
                "proposed_question": clarification_request.model_dump(mode="json"),
                "memory_directive": {
                    "memory_id": str(memory_details.get("memory_id") or ""),
                    "instruction": str(memory_details.get("instruction") or "")[:1000],
                    "missing_information": str(memory_details.get("missing_information") or ""),
                },
                "workflow_forecast_context": self._memory_question_policy_context(
                    user_request
                ),
            },
        )
        cache = user_request.safety_context.setdefault("operator_policy_cache", {})
        if not isinstance(cache, dict):
            cache = {}
            user_request.safety_context["operator_policy_cache"] = cache
        result = review_operator_policy_subjects(
            self.llm_client,
            [subject],
            request_id=str(getattr(observability, "request_id", "") or ""),
            mode="llm",
            cache=cache,
        )
        if result.decisions:
            decision = result.decisions[0]
        else:
            decision = OperatorPolicyDecision(
                module="memory_question",
                subject_id=subject_id,
                decision="unknown",
                effect_intent="unknown",
                risk_level="medium",
                requires_confirmation=False,
                confidence=0.0,
                reason="Memory-question policy returned no decision.",
                evidence_refs=[],
            )
        self._emit(
            observability,
            level="info",
            event_type="operator.policy.memory_question.review",
            title="Memory question policy reviewed",
            summary="LLM-owned memory-question policy judged whether persistent memory should ask the user.",
            details={
                **decision.model_dump(mode="json"),
                "memory_id": str(memory_details.get("memory_id") or ""),
                "missing_information": str(memory_details.get("missing_information") or ""),
            },
        )
        return decision

    def complete_clarification_decision(
        self,
        user_request: UserRequest,
        conversation_context: dict[str, Any] | None = None,
        failure_context: dict[str, Any] | None = None,
        observability: ObservabilityContext | None = None,
    ) -> OperatorClarificationDecision:
        """Ask the LLM whether this request should pause for one clarification."""

        for clarification_request, memory_details in _memory_forced_clarification_candidates(
            user_request
        ):
            try:
                policy_decision = self._review_memory_question_policy(
                    user_request,
                    clarification_request,
                    memory_details,
                    observability,
                )
            except Exception as exc:
                _record_skipped_memory_forced_clarification(
                    user_request,
                    directive={"memory_id": memory_details.get("memory_id", "")},
                    missing_information=str(memory_details.get("missing_information") or ""),
                    reason="memory_question_policy_unavailable",
                    instruction=(
                        "LLM-owned memory-question policy failed, so the runtime skipped "
                        "this memory question instead of falling back to deterministic asking: "
                        f"{str(exc)[:300]}"
                    ),
                    scope="llm_policy_unavailable",
                )
                self._emit(
                    observability,
                    level="warning",
                    event_type="operator.policy.memory_question.unavailable",
                    title="Memory question policy unavailable",
                    summary=(
                        "LLM-owned memory-question policy failed; skipping the memory-required "
                        "clarification."
                    ),
                    details={"error": str(exc)[:500], **memory_details},
                )
                continue
            policy_payload = policy_decision.model_dump(mode="json")
            memory_details = {
                **memory_details,
                "policy_decision": policy_payload,
            }
            decision_confidence = float(policy_decision.confidence or 0.0)
            if policy_decision.decision in {"ask_user", "applies"}:
                decision = OperatorClarificationDecision(
                    decision="ask_user",
                    clarification_request=clarification_request,
                    reason="LLM-owned memory-question policy accepted a memory-required user question.",
                    confidence=decision_confidence,
                )
                self._emit(
                    observability,
                    level="info",
                    event_type=OPERATOR_CLARIFICATION_DECISION,
                    title="Operator clarification decision received",
                    summary="Persistent memory required one user clarification after LLM policy review.",
                    details={**decision.model_dump(mode="json"), **memory_details},
                )
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_CLARIFICATION_REQUIRED,
                    title="Operator clarification required",
                    summary="The request is paused until the user answers one clarification question.",
                    details={
                        **clarification_request.model_dump(mode="json"),
                        "memory_id": memory_details.get("memory_id", ""),
                        "source": "memory_directive",
                    },
                )
                return decision

            if policy_decision.decision == "allow":
                repair_hint = _normalized_policy_text(policy_decision.repair_hint).replace(
                    " ",
                    "_",
                )
                if repair_hint == "future_step_answers":
                    skip_reason = "memory_question_policy_future_step_answers"
                    skip_scope = "llm_policy_future_step_answers"
                elif repair_hint == "already_answered":
                    skip_reason = "memory_question_policy_already_answered"
                    skip_scope = "llm_policy_already_answered"
                else:
                    skip_reason = "memory_question_policy_allow"
                    skip_scope = "llm_policy_allow"
            else:
                skip_reason = f"memory_question_policy_{policy_decision.decision}"
                skip_scope = "llm_policy_not_applicable"
            _record_skipped_memory_forced_clarification(
                user_request,
                directive={"memory_id": memory_details.get("memory_id", "")},
                missing_information=str(memory_details.get("missing_information") or ""),
                reason=skip_reason,
                instruction=(
                    "LLM-owned memory-question policy decided not to pause for "
                    f"this memory question: {policy_decision.reason}"
                ),
                scope=skip_scope,
            )
            self._emit(
                observability,
                level="info",
                event_type="operator.policy.memory_question.skipped",
                title="Memory question policy skipped clarification",
                summary="LLM-owned memory-question policy did not allow this memory question to pause.",
                details=memory_details,
            )

        prompt = build_operator_clarification_prompt(
            user_request,
            conversation_context=conversation_context,
            failure_context=failure_context,
        )
        decision = structured_call(self.llm_client, prompt, OperatorClarificationDecision)
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_CLARIFICATION_DECISION,
            title="Operator clarification decision received",
            summary="The LLM decided whether the request needs one user clarification before continuing.",
            details=decision.model_dump(mode="json"),
        )
        if decision.decision == "ask_user" and decision.clarification_request is not None:
            policy_review = self._reject_clarification_by_policy(
                user_request,
                decision.clarification_request,
                observability,
                phase="pre_planning",
            )
            if policy_review is not None:
                return OperatorClarificationDecision(
                    decision="continue",
                    clarification_request=None,
                    reason=policy_review.instruction,
                    confidence=max(float(decision.confidence), 0.01),
                )
            resolution = self._resolve_clarification_before_ask(
                user_request,
                decision.clarification_request,
                observability,
                phase="pre_planning",
                reason=decision.reason,
            )
            if resolution.decision == "continue_with_assumption":
                return OperatorClarificationDecision(
                    decision="continue",
                    clarification_request=None,
                    reason=resolution.reason
                    or "Typed clarification resolver supplied a safe assumption.",
                    confidence=max(float(decision.confidence), float(resolution.confidence)),
                )
            if resolution.user_question.strip():
                decision.clarification_request = decision.clarification_request.model_copy(
                    update={
                        "question": resolution.user_question.strip(),
                        "reason": resolution.reason or decision.clarification_request.reason,
                        "confidence": max(
                            float(decision.clarification_request.confidence or 0.0),
                            float(resolution.confidence or 0.0),
                        ),
                    }
                )
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_CLARIFICATION_REQUIRED,
                title="Operator clarification required",
                summary="The request is paused until the user answers one clarification question.",
                details=decision.clarification_request.model_dump(mode="json")
                if decision.clarification_request is not None
                else {},
            )
        return decision
