"""Streaming task scope helpers for operator plan validation."""

from __future__ import annotations

from agent_runtime.operator.validation_support.mixin_support.common import *
from agent_runtime.operator.validation_support.mixin_support.common import _ValidationConstantsMixin


class _ValidationStreamingScopeMixin(_ValidationConstantsMixin):
    @classmethod
    def _streaming_task_looks_read_only_report(cls, task: dict[str, Any]) -> bool:
        if not isinstance(task, dict):
            return False
        text = cls._streaming_task_scope_text(task)
        if not cls._STREAMING_READ_ONLY_REPORT_RE.search(text):
            return False
        if cls._STREAMING_STRONG_MUTATION_RE.search(text):
            return False
        side_effect_type = str(task.get("side_effect_type") or "").strip().lower()
        if side_effect_type and side_effect_type not in {
            "none",
            "null",
            "read_only",
            "read-only",
            "report",
            "inspect",
            "observe",
        }:
            return False
        return True

    @classmethod
    def _streaming_task_is_read_only(cls, task: dict[str, Any]) -> bool:
        verb = str(task.get("semantic_verb") or "").strip().lower()
        risk = str(task.get("risk_level") or "").strip().lower()
        if bool(task.get("requires_confirmation")):
            return False
        if cls._streaming_task_looks_read_only_report(task):
            return True
        if risk in {"medium", "high", "critical"}:
            return False
        return verb in {
            "analyze",
            "calculate",
            "compare",
            "count",
            "extract",
            "filter",
            "get",
            "inspect",
            "list",
            "read",
            "render",
            "search",
            "show",
            "sort",
            "summarize",
            "verify",
        }

    @staticmethod
    def _streaming_task_requires_mutation(
        task: dict[str, Any],
        *,
        policy_mode: str | None = None,
    ) -> bool:
        if not isinstance(task, dict) or _ValidationStreamingScopeMixin._streaming_task_is_read_only(task):
            return False
        generated_text = operator_text_generation_requested_in_text(
            " ".join(
                str(task.get(key) or "")
                for key in (
                    "description",
                    "goal",
                    "semantic_verb",
                    "object_type",
                    "operation_intent",
                    "side_effect_type",
                )
            )
        )
        if generated_text:
            return False
        has_explicit_effect_context = any(
            task.get(key)
            for key in (
                "semantic_verb",
                "operation_intent",
                "side_effect_type",
                "requires_confirmation",
                "risk_level",
                "risk",
            )
        )
        if not has_explicit_effect_context:
            return False
        return classify_task_effect(task, policy_mode=policy_mode).mutates_state

    @staticmethod
    def _python_action_looks_mutating(
        action: OperatorAction,
        *,
        policy_mode: str | None = None,
    ) -> bool:
        return classify_python_effect(
            str(action.code or ""),
            reason=str(action.reason or ""),
            policy_mode=policy_mode,
        ).mutates_state

    @staticmethod
    def _action_has_mutating_effect(
        action: OperatorAction,
        task: Any | None = None,
        current_task: dict[str, Any] | None = None,
        policy_mode: str | None = None,
    ) -> bool:
        return classify_action_effect(
            action,
            task=task,
            current_task=current_task,
            policy_mode=policy_mode,
        ).mutates_state

    @staticmethod
    def _streaming_task_scope_text(task: dict[str, Any]) -> str:
        if not isinstance(task, dict):
            return ""
        constraints = task.get("constraints")
        return " ".join(
            [
                str(task.get("task_id") or ""),
                str(task.get("description") or ""),
                str(task.get("goal") or ""),
                str(task.get("semantic_verb") or ""),
                str(task.get("object_type") or ""),
                str(task.get("operation_intent") or ""),
                (
                    _stable_json(constraints)
                    if isinstance(constraints, dict)
                    else str(constraints or "")
                ),
            ]
        )

    @staticmethod
    def _streaming_plan_task_scope_text(task: Any | None) -> str:
        if task is None:
            return ""
        return " ".join(
            [
                str(getattr(task, "task_id", "") or ""),
                str(getattr(task, "goal", "") or ""),
                str(getattr(task, "semantic_verb", "") or ""),
                str(getattr(task, "object_type", "") or ""),
            ]
        )

    @staticmethod
    def _streaming_action_scope_text(
        action: OperatorAction,
        task: Any | None,
    ) -> str:
        return " ".join(
            [
                str(action.command or ""),
                str(action.code or ""),
                str(action.llm_prompt or ""),
                _ValidationStreamingScopeMixin._streaming_plan_task_scope_text(task),
            ]
        )

    @staticmethod
    def _streaming_operation_tokens(text: str) -> set[str]:
        return {
            operation
            for operation, pattern in _STREAMING_OPERATION_PATTERNS
            if pattern.search(str(text or ""))
        }

    @staticmethod
    def _streaming_future_tasks(context: dict[str, Any]) -> list[dict[str, Any]]:
        tasks = context.get("operator_streaming_tasks")
        if not isinstance(tasks, list):
            return []
        try:
            index = int(context.get("operator_streaming_current_index") or 0)
        except (TypeError, ValueError):
            index = 0
        return [dict(task) for task in tasks[index + 1 :] if isinstance(task, dict)]

    def _streaming_step_scope_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        context = dict(user_request.session_context or {})
        current_task = context.get("operator_streaming_current_task")
        if not isinstance(current_task, dict):
            return []
        errors: list[dict[str, Any]] = []
        task_by_id = {task.task_id: task for task in plan.tasks}
        effect_policy_mode = operator_policy_mode(self.config, "effect")
        if self._streaming_task_requires_mutation(
            current_task,
            policy_mode=effect_policy_mode,
        ) and not any(
            self._action_has_mutating_effect(
                action,
                task_by_id.get(action.task_id),
                current_task,
                policy_mode=effect_policy_mode,
            )
            for action in plan.actions
        ):
            errors.append(
                {
                    "error": "streaming_mutating_step_without_mutation",
                    "message": (
                        "The current streaming step is mutating, but the proposed plan "
                        "only inspects or reports state and does not perform the requested "
                        "state change."
                    ),
                    "current_task_id": str(current_task.get("task_id") or ""),
                    "repair_hint": (
                        "Include the concrete mutating action for this current step, plus "
                        "any necessary read-only precondition or verification actions. Do "
                        "not replace the requested mutation with a status or verification check."
                    ),
                }
            )
        if self._streaming_task_is_read_only(current_task):
            for action in plan.actions:
                task = task_by_id.get(action.task_id)
                action_effect = classify_action_effect(
                    action,
                    task=task,
                    current_task=current_task,
                    policy_mode=effect_policy_mode,
                )
                approval_gated = operator_action_requires_confirmation(
                    action,
                    semantic_verb=task.semantic_verb if task is not None else None,
                    policy_mode=effect_policy_mode,
                )
                if action_effect.mutates_state or (approval_gated and not action_effect.read_only):
                    errors.append(
                        {
                            "error": "streaming_read_only_step_mutation",
                            "message": (
                                "Streaming mode executes one decomposed task at a time. "
                                "The current task is read-only, so it cannot include "
                                "mutating or approval-gated actions from a later task."
                            ),
                            "action_id": action.action_id,
                            "task_id": action.task_id,
                            "current_task_id": str(current_task.get("task_id") or ""),
                            "repair_hint": (
                                "Keep only the read-only action needed for the current "
                                "decomposed task. The runtime will plan later mutations "
                                "after this step produces output."
                            ),
                        }
                    )
        if operator_policy_mode(self.config, "streaming_scope") == "llm":
            return errors
        current_ops = self._streaming_operation_tokens(self._streaming_task_scope_text(current_task))
        future_tasks = self._streaming_future_tasks(context)
        future_ops_by_task: dict[str, set[str]] = {}
        for future_task in future_tasks:
            task_ops = self._streaming_operation_tokens(self._streaming_task_scope_text(future_task))
            if task_ops:
                future_ops_by_task[str(future_task.get("task_id") or "")] = task_ops
        future_ops = set().union(*future_ops_by_task.values()) if future_ops_by_task else set()
        if not future_ops:
            return errors
        for action in plan.actions:
            task = task_by_id.get(action.task_id)
            action_ops = self._streaming_operation_tokens(
                self._streaming_action_scope_text(action, task)
            )
            future_only_ops = sorted((action_ops & future_ops) - current_ops)
            if not future_only_ops:
                continue
            matching_future_task_ids = sorted(
                task_id
                for task_id, task_ops in future_ops_by_task.items()
                if set(future_only_ops) & task_ops
            )
            errors.append(
                {
                    "error": "streaming_future_step_action",
                    "message": (
                        "Streaming mode executes one decomposed task at a time. "
                        "This action appears to perform work reserved for a later "
                        "decomposed task."
                    ),
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "current_task_id": str(current_task.get("task_id") or ""),
                    "future_task_ids": matching_future_task_ids,
                    "future_operations": future_only_ops,
                    "repair_hint": (
                        "Remove actions for later decomposed tasks and keep only "
                        "the current step's work. The runtime will plan the later "
                        "task after this step succeeds."
                    ),
                }
            )
        return errors

    def _per_entity_scope_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject per-entity streaming steps that collapse prior entities to one scalar."""

        if not self._streaming_task_requires_per_entity_scope(user_request):
            return []
        candidates = self._streaming_prior_output_candidates(user_request)
        if not candidates:
            return []
        if any(
            self._action_uses_streaming_prior_output(action, candidates)
            or self._action_enumerates_per_entity_scope(action)
            for action in plan.actions
        ):
            return []

        scalar_actions = [
            action for action in plan.actions if self._action_uses_global_scalar_selector(action)
        ]
        if not scalar_actions:
            return []
        current_task_text = self._streaming_current_task_text(user_request)
        return [
            {
                "error": "per_entity_scope_not_preserved",
                "message": (
                    "This streaming step asks for per-entity output, but the plan "
                    "collapses prior discovered entities to a single global scalar."
                ),
                "action_ids": [action.action_id for action in scalar_actions],
                "task_preview": current_task_text[:240],
                "repair_hint": (
                    "Bind the prior entity list through input_bindings and iterate over "
                    "it, or rewrite the action to emit one row per entity. Do not satisfy "
                    "a for-each/per/every/all-entity task with head -n 1, tail -n 1, "
                    "or another first/last-only selector."
                ),
            }
        ]



__all__ = ["_ValidationStreamingScopeMixin"]
