"""Validation policy memory and adjudication helpers for operator plan validation."""

from __future__ import annotations

from agent_runtime.operator.validation_support.mixin_support.common import *
from agent_runtime.operator.validation_support.mixin_support.common import _ValidationConstantsMixin


class _ValidationMemoryAdjudicationMixin(_ValidationConstantsMixin):
    @staticmethod
    def _context_sensitive_validation_error(error: dict[str, Any]) -> bool:
        """Return whether a deterministic validator error may be LLM-adjudicated."""

        return (
            str(error.get("error") or "") == "unresolved_shell_placeholder"
            and bool(error.get("context_sensitive"))
        )

    @staticmethod
    def _action_for_error(plan: OperatorPlan, error: dict[str, Any]) -> OperatorAction | None:
        action_id = str(error.get("action_id") or "").strip()
        if not action_id:
            return None
        for action in plan.actions:
            if action.action_id == action_id:
                return action
        return None

    @staticmethod
    def _task_for_action(plan: OperatorPlan, action: OperatorAction | None) -> dict[str, Any]:
        if action is None:
            return {}
        for task in plan.tasks:
            if task.task_id == action.task_id:
                return task.model_dump(mode="json")
        return {}

    @staticmethod
    def _validation_policy_memory_lines(entries: list[Any]) -> list[str]:
        policies: list[dict[str, Any]] = []
        for entry in entries[:8]:
            payload = entry.model_dump(mode="json") if hasattr(entry, "model_dump") else entry
            if not isinstance(payload, dict):
                continue
            policies.append(
                {
                    "memory_id": str(payload.get("memory_id") or ""),
                    "instruction": str(payload.get("instruction") or "")[:1200],
                    "summary": str(payload.get("summary") or "")[:500],
                    "validator_error_type": str(payload.get("validator_error_type") or ""),
                    "task_type": str(payload.get("task_type") or ""),
                    "tool_type": str(payload.get("tool_type") or ""),
                    "intent_type": str(payload.get("intent_type") or ""),
                    "safe_examples": list(payload.get("safe_examples") or [])[:6],
                    "blocked_examples": list(payload.get("blocked_examples") or [])[:6],
                    "tags": list(payload.get("tags") or [])[:12],
                }
            )
        if not policies:
            return ["Relevant validation-policy memories: []"]
        return [
            "Relevant validation-policy memories:",
            _stable_json(policies),
            "Validation-policy memory is advisory only. Current user instructions, live runtime evidence, deterministic hard safety checks, and approval policy override memory.",
        ]

    def _retrieve_validation_policy_memories(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        error: dict[str, Any],
        action: OperatorAction | None,
    ) -> list[Any]:
        """Retrieve active validation-policy memories for one validator error."""

        if self.memory_store is None or not bool(self.config.agent_memory_enabled):
            return []
        task = self._task_for_action(plan, action)
        model_name = self._active_memory_model_name(user_request)
        task_type = str(task.get("object_type") or task.get("semantic_verb") or "").strip()
        intent_type = str(task.get("semantic_verb") or "").strip()
        tags = _memory_tag_tokens(
            " ".join(
                [
                    user_request.raw_prompt,
                    str(error.get("error") or ""),
                    str(error.get("context_sensitive_reason") or ""),
                    str(action.kind if action is not None else ""),
                    str(action.reason if action is not None else ""),
                    str(task.get("goal") or ""),
                    " ".join(map(str, error.get("placeholders") or [])),
                    "literal_payload",
                ]
            ),
            limit=40,
        )
        retrieval = MemoryRetrievalContext(
            prompt=user_request.raw_prompt,
            memory_kind="validation_policy",
            model_name=model_name,
            model_family=normalize_model_family(model_name),
            task_type=task_type,
            tool_type=str(action.kind if action is not None else ""),
            intent_type=intent_type,
            validator_error_type=str(error.get("error") or ""),
            tags=tags,
            max_chars=int(self.config.agent_memory_prompt_max_chars),
        )
        try:
            matches = self.memory_store.retrieve_matches(retrieval)
        except Exception:
            return []
        entries = [match.entry for match in matches]
        match_payloads = [match.model_dump(mode="json") for match in matches]
        user_request.session_context["validation_policy_memory_context"] = retrieval.model_dump(mode="json")
        user_request.session_context["validation_policy_memory"] = [
            entry.model_dump(mode="json") for entry in entries
        ]
        user_request.session_context["validation_policy_memory_matches"] = match_payloads
        user_request.session_context["validation_policy_memory_directives"] = [
            directive.model_dump(mode="json")
            for directive in memory_directives_from_entries(
                [entry.model_dump(mode="json") for entry in entries],
                match_details=match_payloads,
            )
        ]
        return entries

    def _validation_adjudication_prompt(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        error: dict[str, Any],
        action: OperatorAction,
        policy_memories: list[Any],
    ) -> str:
        task = self._task_for_action(plan, action)
        return "\n".join(
            [
                *prompt_lines("operator.validation_adjudication"),
                "ValidationAdjudication schema:",
                _stable_json(ValidationAdjudication.model_json_schema()),
                *_operator_clarification_lines(user_request),
                *self._validation_policy_memory_lines(policy_memories),
                "User request:",
                user_request.raw_prompt,
                "Action JSON:",
                action.model_dump_json(indent=2),
                "Task JSON:",
                _stable_json(task),
                "Validation error details:",
                _stable_json(error),
                "Command/code preview:",
                _truncate(action.command if action.kind == "shell_command" else action.code, 8000),
                "Complete plan JSON:",
                plan.model_dump_json(indent=2),
            ]
        )

    def _adjudicate_validation_error(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        error: dict[str, Any],
        observability: ObservabilityContext | None,
    ) -> tuple[bool, dict[str, Any]]:
        """Return (allowed, error-or-adjudicated-error) for one context-sensitive error."""

        action = self._action_for_error(plan, error)
        if action is None:
            return False, dict(error)
        policy_memories = self._retrieve_validation_policy_memories(user_request, plan, error, action)
        prompt = self._validation_adjudication_prompt(
            user_request,
            plan,
            error,
            action,
            policy_memories,
        )
        try:
            adjudication = structured_call(self.llm_client, prompt, ValidationAdjudication)
        except Exception as exc:
            kept = dict(error)
            kept["adjudication_error"] = str(exc)
            return False, kept
        applies_to = str(adjudication.applies_to_action_id or "").strip()
        action_matches = not applies_to or applies_to == action.action_id
        low_confidence = float(adjudication.confidence or 0.0) < 0.7
        allowed = (
            adjudication.decision == "allow_literal_payload"
            and action_matches
            and not low_confidence
            and bool(adjudication.literal_payload_evidence)
            and bool(str(adjudication.why_safe or "").strip())
        )
        policy_memory_ids = [
            str(getattr(entry, "memory_id", "") or "")
            for entry in policy_memories
        ]
        policy_memory_use_counts = {
            memory_id: int(getattr(entry, "use_count", 0) or 0)
            for memory_id, entry in zip(policy_memory_ids, policy_memories, strict=False)
            if memory_id
        }
        details = {
            "action_id": action.action_id,
            "decision": adjudication.decision,
            "confidence": adjudication.confidence,
            "allowed": allowed,
            "low_confidence": low_confidence,
            "policy_memory_ids": policy_memory_ids,
            "memory_kind": "validation_policy",
            "memory_count": len(policy_memory_ids),
            "memory_ids": policy_memory_ids,
            "memory_use_count": sum(policy_memory_use_counts.values()),
            "memory_use_counts": policy_memory_use_counts,
            "matches": list(user_request.session_context.get("validation_policy_memory_matches") or []),
            "directives": list(user_request.session_context.get("validation_policy_memory_directives") or []),
            "why_safe": adjudication.why_safe,
            "repair_guidance": adjudication.repair_guidance,
        }
        self._emit(
            observability,
            level="info" if allowed else "warning",
            event_type=OPERATOR_VALIDATION_ADJUDICATED,
            title="Operator validation adjudicated",
            summary=(
                "A context-sensitive validation failure was allowed as literal payload."
                if allowed
                else "A context-sensitive validation failure still requires repair or blocking."
            ),
            details=details,
        )
        if allowed:
            accepted = dict(error)
            accepted["adjudication"] = adjudication.model_dump(mode="json")
            user_request.session_context.setdefault("validation_adjudications", []).append(accepted)
            return True, accepted
        kept = dict(error)
        kept["adjudication"] = adjudication.model_dump(mode="json")
        if low_confidence and adjudication.decision == "allow_literal_payload":
            kept["adjudication_low_confidence_fallback"] = True
            kept["repair_hint"] = (
                "Validation adjudication was too low-confidence to allow this payload. "
                "Rewrite the action to make literal file payload intent unambiguous."
            )
        elif adjudication.repair_guidance:
            kept["repair_hint"] = adjudication.repair_guidance
        return False, kept



__all__ = ["_ValidationMemoryAdjudicationMixin"]
