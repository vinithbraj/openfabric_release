"""Execution metadata and presentation helpers for the operator step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class _StepRunnerMetadataMixin:
    @staticmethod
    def _background_terminal_error_context(execution_context: dict[str, Any]) -> dict[str, Any]:
        background_task = bool(
            str(execution_context.get("durable_task_id") or "").strip()
            or str(execution_context.get("scheduled_event_run_id") or "").strip()
            or str(execution_context.get("scheduled_event_id") or "").strip()
        )
        if not background_task:
            return {}
        background_terminal_error = str(
            execution_context.get("background_terminal_error")
            or execution_context.get("scheduled_event_background_terminal_error")
            or execution_context.get("durable_task_background_terminal_error")
            or "No background terminal session was attached to this background task."
        ).strip()
        return {
            "background_task": True,
            "background_terminal_unavailable": True,
            "background_terminal_error": background_terminal_error,
            "retryable_after_terminal_config": True,
        }

    @staticmethod
    def _execution_scope_metadata(execution_context: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        execution_step_id = str(execution_context.get("operator_execution_step_id") or "").strip()
        streaming_step_id = str(execution_context.get("operator_streaming_step_id") or "").strip()
        if execution_step_id:
            metadata["operator_execution_step_id"] = execution_step_id
        if streaming_step_id:
            metadata["streaming_step_id"] = streaming_step_id
        current_task = execution_context.get("operator_streaming_current_task")
        if isinstance(current_task, dict):
            task_id = str(current_task.get("task_id") or "").strip()
            if task_id:
                metadata["streaming_task_id"] = task_id
        try:
            current_index = int(execution_context.get("operator_streaming_current_index"))
        except (TypeError, ValueError):
            current_index = None
        if current_index is not None:
            metadata["streaming_step_index"] = current_index
        return metadata

    @classmethod
    def _record_with_execution_scope(
        cls,
        record: OperatorExecutionRecord,
        execution_context: dict[str, Any],
    ) -> OperatorExecutionRecord:
        scope_metadata = cls._execution_scope_metadata(execution_context)
        if not scope_metadata:
            return record
        metadata = {**scope_metadata, **dict(record.metadata or {})}
        return record.model_copy(update={"metadata": metadata})

    @staticmethod
    def _execution_snippet_metadata(
        action: OperatorAction,
        *,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        if action.kind not in {"python_action", "python_transform"}:
            return {}
        code = str(action.code or "").strip()
        if not code:
            return {}
        snippet = _truncate(code, max_chars) if max_chars is not None else code
        return {
            "execution_snippet": snippet,
            "execution_snippet_language": "python",
            "execution_snippet_label": (
                "Python action" if action.kind == "python_action" else "Python transform"
            ),
        }

    @staticmethod
    def _execution_operation_description(
        action: OperatorAction,
        *,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Return a novice-friendly explanation for the execution capsule."""

        def clean(value: Any, *, limit: int = 420) -> str:
            text = " ".join(str(value or "").split())
            return _truncate(text, limit).strip()

        reason = clean(getattr(action, "reason", ""))
        effect_summary = clean(getattr(action, "effect_summary", ""))
        declared_shape = clean(getattr(action, "declared_output_shape", ""), limit=120)
        cwd_text = clean(cwd or getattr(action, "cwd", "") or ".", limit=180)
        risk = clean(getattr(action, "risk", ""), limit=80)
        effect_intent = clean(getattr(action, "effect_intent", ""), limit=100)
        execution_mode = clean(getattr(action, "execution_mode", ""), limit=80)
        interaction_mode = clean(getattr(action, "interaction_mode", ""), limit=80)

        if action.kind == "python_transform":
            opener = (
                "This operation runs the Python transform code shown below. The agent passes "
                "the available execution records into a transform(inputs) function, uses the code to reshape "
                "or summarize those records, and captures the returned value for the next step "
                "or final response."
            )
        elif action.kind == "python_action":
            opener = (
                "This operation runs the Python code shown below through the operator runtime. The agent passes "
                "resolved action inputs into a main(inputs) function, lets the code compute or "
                "parse the needed result, and captures the returned output."
            )
        elif action.kind == "llm_text":
            opener = (
                "This operation asks the language model to generate text from the resolved runtime inputs. "
                "It does not run shell commands or mutate runtime state, and the generated text can be "
                "bound into a later action."
            )
        else:
            command = str(getattr(action, "command", "") or "")
            sql_hint = bool(
                re.search(
                    r"\b(psql|mysql|sqlite3)\b|\bselect\b|\binsert\b|\bupdate\b|\bdelete\b",
                    command,
                    re.IGNORECASE,
                )
            )
            if sql_hint:
                opener = (
                    "This operation runs the shell or SQL command shown below. It appears to "
                    "execute or prepare SQL, so the agent uses the command-line database tool "
                    "or query text to inspect or "
                    "change database data, then captures stdout, stderr, and the exit code."
                )
            else:
                opener = (
                    "This operation runs the shell command shown below. The agent sends the command to the "
                    "configured gateway, captures its stdout and stderr, and uses that real "
                    "runtime output as evidence for later steps or the final answer."
                )

        details: list[str] = [opener]
        if effect_summary:
            details.append(f"Planned effect: {effect_summary}.")
        if reason and reason != effect_summary:
            details.append(f"Why the agent is doing it: {reason}.")
        if cwd_text:
            details.append(f"It runs from `{cwd_text}`.")
        runtime_bits = []
        if execution_mode:
            runtime_bits.append(f"execution mode `{execution_mode}`")
        if interaction_mode:
            runtime_bits.append(f"interaction mode `{interaction_mode}`")
        if runtime_bits:
            details.append(f"Runtime behavior: {', '.join(runtime_bits)}.")
        classification_bits = []
        if effect_intent:
            classification_bits.append(f"effect intent `{effect_intent}`")
        if risk:
            classification_bits.append(f"risk `{risk}`")
        if declared_shape:
            classification_bits.append(f"expected output `{declared_shape}`")
        if classification_bits:
            details.append(f"Safety and output classification: {', '.join(classification_bits)}.")

        description = " ".join(part.strip() for part in details if part.strip())
        return {"operation_description": description}

    def _emit_tryout_output(
        self,
        observability: ObservabilityContext | None,
        *,
        action: OperatorAction,
        channel: str,
        text: str = "",
        exit_code: Any = None,
        cwd: str | None = None,
        extra: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        if not self.operator_tryout_enabled():
            return
        self._emit(
            observability,
            level=level,
            event_type=OPERATOR_TRYOUT_OUTPUT,
            title="Tryout output",
            summary="A retired tryout action produced runtime output.",
            details={
                **dict(extra or {}),
                "action_id": action.action_id,
                "task_id": action.task_id,
                "kind": action.kind,
                "channel": channel,
                "text": text,
                "exit_code": exit_code,
                "cwd": str(cwd if cwd is not None else action.cwd or "."),
            },
        )

    def answer_from_context_result(
        self,
        user_request: UserRequest,
        *,
        answer: str,
        reason: str = "",
        confidence: float = 0.0,
    ) -> OperatorPipelineResult:
        """Return a follow-up answer that did not require new operator actions."""

        markdown = str(answer or "").strip() or "I could not answer that from the available context."
        return OperatorPipelineResult(
            status="success",
            final_response=markdown,
            confirmation_required=False,
            confirmation_actions=[],
            display_document=self._display_document(
                user_request=user_request,
                status="success",
                markdown=markdown,
            ),
            validation_errors=[],
        )

    def _execution_order(self, plan: OperatorPlan) -> list[OperatorAction]:
        action_by_id = {action.action_id: action for action in plan.actions}
        adjacency: dict[str, set[str]] = {action.action_id: set() for action in plan.actions}
        indegree: dict[str, int] = {action.action_id: 0 for action in plan.actions}
        for action in plan.actions:
            for dependency in action.depends_on:
                if dependency not in adjacency:
                    continue
                if action.action_id not in adjacency[dependency]:
                    adjacency[dependency].add(action.action_id)
                    indegree[action.action_id] += 1
            for binding in action.input_bindings:
                if binding.source_action_id not in adjacency:
                    continue
                if action.action_id not in adjacency[binding.source_action_id]:
                    adjacency[binding.source_action_id].add(action.action_id)
                    indegree[action.action_id] += 1
        for dependency in plan.dependencies:
            if (
                dependency.producer_action_id not in adjacency
                or dependency.consumer_action_id not in adjacency
            ):
                continue
            if dependency.consumer_action_id not in adjacency[dependency.producer_action_id]:
                adjacency[dependency.producer_action_id].add(dependency.consumer_action_id)
                indegree[dependency.consumer_action_id] += 1
        stable_index = {action.action_id: index for index, action in enumerate(plan.actions)}
        queue = deque(sorted((key for key, value in indegree.items() if value == 0), key=stable_index.get))
        ordered: list[OperatorAction] = []
        while queue:
            action_id = queue.popleft()
            ordered.append(action_by_id[action_id])
            for child_id in sorted(adjacency[action_id], key=stable_index.get):
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    queue.append(child_id)
        return ordered

    @staticmethod
    def _action_execution_fingerprint(action: OperatorAction) -> str:
        """Return a stable signature for fields that affect action execution."""

        payload = action.model_dump(mode="json")
        payload.pop("reason", None)
        payload.pop("risk", None)
        return _stable_json(payload)



__all__ = ["_StepRunnerMetadataMixin"]
