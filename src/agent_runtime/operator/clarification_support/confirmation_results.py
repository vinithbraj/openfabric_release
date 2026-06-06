"""Confirmation and clarification result rendering helpers."""

from __future__ import annotations

from .common import *
from .confirmation_payloads import *
from .policy_gates import *
from .memory_questions import *
from .resolution_prompt import *


class _ClarificationResultsMixin:
    """Confirmation and clarification result rendering helpers."""

    @staticmethod
    def _completed_action_ids(records: list[OperatorExecutionRecord] | None) -> set[str]:
        """Return successful action ids that can be treated as already done."""

        return {
            record.action_id
            for record in list(records or [])
            if record.status == "success"
        }

    def confirmation_actions(
        self,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord] | None = None,
        *,
        user_request: UserRequest | None = None,
    ) -> list[dict[str, Any]]:
        """Return user-visible operator action summaries for approval UI."""

        completed_action_ids = self._completed_action_ids(records)
        sudo_authorization = (
            request_sudo_authorization(user_request)
            if user_request is not None
            else None
        )
        sudo_overrides = {}
        if user_request is not None:
            raw_overrides = dict(user_request.session_context or {}).get(
                REQUEST_SCOPED_SUDO_OVERRIDES_CONTEXT_KEY
            )
            if isinstance(raw_overrides, list):
                sudo_overrides = {
                    str(item.get("action_id") or ""): dict(item)
                    for item in raw_overrides
                    if isinstance(item, dict)
                }
        actions: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.action_id in completed_action_ids:
                continue
            if action.kind == "shell_command":
                preview_key = "command"
                preview_value = action.command
                semantic_verb = "execute"
            elif action.kind == "llm_text":
                preview_key = "llm_prompt"
                preview_value = action.llm_prompt
                semantic_verb = "generate"
            else:
                preview_key = "code"
                preview_value = action.code
                semantic_verb = "execute" if action.kind == "python_action" else "transform"
            binding_previews = _confirmation_input_binding_previews(action, records)
            sudo_arguments: dict[str, Any] = {}
            if (
                action.kind == "shell_command"
                and sudo_authorization is not None
                and shell_command_uses_sudo(action.command)
            ):
                override = sudo_overrides.get(action.action_id, {})
                sudo_arguments = {
                    "request_scoped_sudo_authorized": True,
                    "command_hash": operator_command_hash(str(action.command or "")),
                    "requires_terminal_context": action.interaction_mode == "may_prompt",
                    "sudo_authorization_source": sudo_authorization["source"],
                    **(
                        {"sudo_override_block_reason": override.get("block_reason")}
                        if override.get("block_reason")
                        else {}
                    ),
                }
            actions.append(
                {
                    "node_id": action.action_id,
                    "task_id": action.task_id,
                    "capability_id": f"llm_operator.{action.kind}",
                    "operation_id": action.kind,
                    "semantic_verb": semantic_verb,
                    "description": action.reason,
                    "requires_terminal_context": (
                        action.kind == "shell_command"
                        and action.interaction_mode == "may_prompt"
                    ),
                    "arguments": {
                        "kind": action.kind,
                        "cwd": action.cwd,
                        "risk": action.risk,
                        "interaction_mode": action.interaction_mode,
                        "requires_terminal_context": (
                            action.kind == "shell_command"
                            and action.interaction_mode == "may_prompt"
                        ),
                        "defer_code_generation": action.defer_code_generation,
                        **(
                            {"stdin": _confirmation_stdin_preview(action, binding_previews)}
                            if action.kind == "shell_command"
                            and getattr(action, "stdin_mode", "none") != "none"
                            else {}
                        ),
                        **(
                            {"inputs": _action_shell_inputs_preview(action)}
                            if action.kind == "shell_command" and action.inputs
                            else {}
                        ),
                        **({"bindings": binding_previews} if binding_previews else {}),
                        **sudo_arguments,
                        preview_key: _truncate(preview_value, 1200),
                    },
                }
            )
        return actions

    def plan_requires_confirmation(self, plan: OperatorPlan) -> bool:
        """Return whether this concrete operator plan needs user approval."""

        return operator_plan_requires_confirmation(
            plan,
            policy_mode=operator_policy_mode(self.config, "effect"),
        )

    def plan_requires_confirmation_for_pending(
        self,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord] | None = None,
    ) -> bool:
        """Return whether any action not already completed needs approval."""

        completed_action_ids = self._completed_action_ids(records)
        return any(
            self._action_requires_confirmation(plan, action)
            for action in plan.actions
            if action.action_id not in completed_action_ids
        )

    def _display_document(
        self,
        *,
        user_request: UserRequest,
        status: str,
        markdown: str,
        plan: OperatorPlan | None = None,
        records: list[OperatorExecutionRecord] | None = None,
    ) -> dict[str, Any]:
        display_label = str(user_request.session_context.get("operator_display_label") or "Conversational")
        sections: list[dict[str, Any]] = [
            {
                "section_id": new_id("display-section"),
                "title": display_label,
                "primitive_id": "markdown",
                "display_type": "markdown",
                "shape_type": "markdown",
                "source_node_id": None,
                "content": markdown,
                "rows": [],
                "columns": [],
                "metadata": {"status": status},
                "language": None,
                "truncated": False,
                "preview_count": None,
                "total_count": None,
                "data_ref": None,
                "raw_available": False,
            }
        ]
        action_labels = self._operator_action_labels(plan)
        target_ui = user_request.session_context.get("target_ui")
        for action in list(plan.actions if plan is not None else []):
            if target_ui == "agent_ui":
                continue
            content = (
                action.command
                if action.kind == "shell_command"
                else action.llm_prompt
                if action.kind == "llm_text"
                else action.code
            )
            if action.kind != "shell_command" and action.defer_code_generation:
                content = "Python code will be generated after upstream input shape is known."
            sections.append(
                {
                    "section_id": new_id("display-section"),
                    "title": f"{action_labels.get(action.action_id, action.action_id)} · {action.kind}",
                    "primitive_id": "code_block",
                    "display_type": "code_block",
                    "shape_type": "code",
                    "source_node_id": action.action_id,
                    "content": content,
                    "rows": [],
                    "columns": [],
                    "metadata": {"cwd": action.cwd, "risk": action.risk},
                    "language": (
                        "bash"
                        if action.kind == "shell_command"
                        else "text"
                        if action.kind == "llm_text"
                        else "python"
                    ),
                    "truncated": False,
                    "preview_count": None,
                    "total_count": None,
                    "data_ref": None,
                    "raw_available": False,
                }
            )
        include_record_outputs = user_request.session_context.get("target_ui") != "agent_ui"
        for record in list(records or []) if include_record_outputs else []:
            content = record.stdout or record.stderr or _stable_json(record.output)
            if content:
                sections.append(
                    {
                        "section_id": new_id("display-section"),
                        "title": f"{action_labels.get(record.action_id, record.action_id)} Output",
                        "primitive_id": "code_block",
                        "display_type": "code_block",
                        "shape_type": "text",
                        "source_node_id": record.action_id,
                        "content": _truncate(content, self.config.max_output_preview_bytes),
                        "rows": [],
                        "columns": [],
                        "metadata": {"status": record.status, "exit_code": record.exit_code},
                        "language": "text",
                        "truncated": len(str(content)) > self.config.max_output_preview_bytes,
                        "preview_count": None,
                        "total_count": None,
                        "data_ref": None,
                        "raw_available": False,
                    }
                )
        return {
            "document_id": new_id("display-doc"),
            "request_id": user_request.request_id,
            "target_ui": user_request.session_context.get("target_ui", "openwebui"),
            "summary": f"{display_label} {status}.",
            "sections": sections,
            "raw_available": False,
            "trace_refs": [],
        }

    def _confirmation_markdown(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord] | None = None,
    ) -> str:
        display_label = str(user_request.session_context.get("operator_display_label") or "Conversational")
        seed_records = list(records or [])
        completed_action_ids = self._completed_action_ids(seed_records)
        lines = [
            "## Confirmation Required",
            "",
            f"{display_label} mode authored one or more actions and is waiting for approval before execution.",
            "",
        ]
        action_labels = self._operator_action_labels(plan)
        if completed_action_ids:
            lines.append("### Already Completed")
            for record in seed_records:
                if record.action_id not in completed_action_ids:
                    continue
                action_label = action_labels.get(record.action_id, record.action_id)
                exit_code = f" · exit `{record.exit_code}`" if record.exit_code is not None else ""
                lines.append(
                    f"- **{action_label}** · `{record.kind}` · status `{record.status}`{exit_code}"
                )
            lines.append("")
        lines.append("### Proposed Actions")
        pending_count = 0
        for action in plan.actions:
            if action.action_id in completed_action_ids:
                continue
            pending_count += 1
            action_label = action_labels.get(action.action_id, action.action_id)
            lines.append(f"- **{action_label}** · `{action.kind}` · risk `{action.risk}`")
            lines.append(f"  - Cwd: `{action.cwd}`")
            lines.append(f"  - Reason: {action.reason}")
            binding_previews = _confirmation_input_binding_previews(action, seed_records)
            if action.kind == "shell_command":
                lines.append("  - Command:")
                lines.append(f"    ```bash\n{_truncate(action.command, 1200)}\n    ```")
                shell_inputs = _action_shell_inputs_preview(action)
                if shell_inputs:
                    lines.append("  - Inputs:")
                    for item in shell_inputs:
                        lines.append(
                            f"    - `{item.get('input_name')}` -> `{item.get('env_name')}` "
                            f"({item.get('value_length') or 0} chars)"
                        )
                        if item.get("value_preview"):
                            lines.append("      Preview:")
                            lines.append(f"      ```text\n{item.get('value_preview')}\n      ```")
                if action.interaction_mode == "may_prompt":
                    lines.append(
                        "  - Terminal: may prompt; it will run in the Agent UI terminal after approval."
                    )
                stdin_preview = _confirmation_stdin_preview(action, binding_previews)
                if stdin_preview.get("stdin_mode") != "none":
                    lines.append("  - Stdin:")
                    lines.append(f"    - Mode: `{stdin_preview.get('stdin_mode')}`")
                    if stdin_preview.get("stdin_input_name"):
                        lines.append(f"    - Input binding: `{stdin_preview.get('stdin_input_name')}`")
                    lines.append(f"    - Length: `{stdin_preview.get('stdin_length') or 0}` chars")
                    if stdin_preview.get("stdin_preview"):
                        lines.append("    - Preview:")
                        lines.append(f"      ```text\n{stdin_preview.get('stdin_preview')}\n      ```")
                if binding_previews:
                    lines.append("  - Input bindings:")
                    for item in binding_previews:
                        lines.append(
                            f"    - `{item.get('input_name')}` from `{item.get('source_action_id')}` "
                            f"`{item.get('source_field')}`"
                        )
                        if item.get("value_preview"):
                            lines.append("      Preview:")
                            lines.append(f"      ```text\n{item.get('value_preview')}\n      ```")
            elif action.kind == "llm_text":
                lines.append("  - Text prompt:")
                lines.append(f"    ```text\n{_truncate(action.llm_prompt, 1200)}\n    ```")
                if binding_previews:
                    lines.append("  - Input bindings:")
                    for item in binding_previews:
                        lines.append(
                            f"    - `{item.get('input_name')}` from `{item.get('source_action_id')}` "
                            f"`{item.get('source_field')}`"
                        )
                        if item.get("value_preview"):
                            lines.append("      Preview:")
                            lines.append(f"      ```text\n{item.get('value_preview')}\n      ```")
            elif action.defer_code_generation:
                lines.append("  - Code: generated after upstream action output is available.")
            else:
                lines.append("  - Code:")
                lines.append(f"    ```python\n{_truncate(action.code, 1200)}\n    ```")
        if pending_count == 0:
            lines.append("- No new actions remain pending.")
        flow_lines = self._operator_output_flow_lines(plan)
        if flow_lines:
            lines.extend(["", "### Output Flow"])
            lines.extend(flow_lines)
        lines.extend(["", "Execution requires confirmation before proceeding."])
        return "\n".join(lines)

    def _clarification_markdown(
        self,
        user_request: UserRequest,
        request: OperatorClarificationRequest,
    ) -> str:
        display_label = str(user_request.session_context.get("operator_display_label") or "Conversational")
        reason = str(request.reason or "")
        lines = [
            "## Clarification Required",
            "",
            f"{display_label} needs one answer before it can plan safely.",
            "",
            f"**Question:** {request.question}",
            "",
            f"**Why it matters:** {reason}",
        ]
        lines.extend(
            [
                "",
                f"**Missing information:** {request.missing_information}",
                "",
                "Provide the answer in the input field.",
            ]
        )
        return "\n".join(lines)

    def _clarification_round_count(self, user_request: UserRequest) -> int:
        raw = dict(user_request.session_context or {}).get("clarifications")
        return len(raw) if isinstance(raw, list) else 0

    def _clarification_limit_reached(self, user_request: UserRequest) -> bool:
        max_rounds = int(self.config.llm_operator_max_clarification_rounds)
        return max_rounds <= 0 or self._clarification_round_count(user_request) >= max_rounds

    def _clarification_limit_result(
        self,
        user_request: UserRequest,
        *,
        plan: OperatorPlan | None = None,
        records: list[OperatorExecutionRecord] | None = None,
    ) -> OperatorPipelineResult:
        max_rounds = int(self.config.llm_operator_max_clarification_rounds)
        markdown = (
            "## Clarification Limit Reached\n\n"
            f"The request reached the configured clarification limit ({max_rounds}). "
            "I stopped instead of guessing."
        )
        return OperatorPipelineResult(
            status="error",
            final_response=markdown,
            plan=plan,
            execution_records=list(records or []),
            display_document=self._display_document(
                user_request=user_request,
                status="error",
                markdown=markdown,
                plan=plan,
                records=records,
            ),
            metadata={"clarification_limit_reached": True},
        )

    @staticmethod
    def _operator_action_labels(plan: OperatorPlan | None) -> dict[str, str]:
        """Return stable human-readable labels for operator actions."""

        if plan is None:
            return {}
        task_goals = {
            task.task_id: _truncate(str(task.goal).strip(), 120)
            for task in plan.tasks
            if str(task.goal or "").strip()
        }
        labels: dict[str, str] = {}
        for action in plan.actions:
            label = task_goals.get(action.task_id) or str(action.reason or "").strip() or action.action_id
            labels[action.action_id] = " ".join(label.split())
        return labels

    @staticmethod
    def _operator_output_flow_lines(plan: OperatorPlan) -> list[str]:
        """Return markdown lines describing typed input bindings between actions."""

        action_labels = _ClarificationResultsMixin._operator_action_labels(plan)
        labels = {
            action.action_id: f"`{action_labels.get(action.action_id, action.action_id)}`"
            for action in plan.actions
        }
        flow_lines: list[str] = []
        for action in plan.actions:
            target = labels.get(action.action_id, f"`{action.action_id}`")
            for binding in action.input_bindings:
                source = labels.get(binding.source_action_id, f"`{binding.source_action_id}`")
                required = "" if binding.required else " (optional)"
                flow_lines.append(
                    f"- {source} `{binding.source_field}` -> "
                    f"{target} input `{binding.input_name}`{required}"
                )
        return flow_lines

    def require_confirmation(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
        *,
        records: list[OperatorExecutionRecord] | None = None,
    ) -> OperatorPipelineResult:
        seed_records = list(records or [])
        actions = self.confirmation_actions(plan, seed_records, user_request=user_request)
        envelope = self.reliability.create_approval_envelope(
            request_id=user_request.request_id,
            goal=user_request.raw_prompt,
            plan=plan,
            context=user_request.session_context,
        )
        if envelope is not None:
            for action in actions:
                action["approval_envelope"] = {
                    "envelope_id": envelope["envelope_id"],
                    "max_risk": envelope["max_risk"],
                    "mutation_budget": envelope["mutation_budget"],
                    "used_mutations": envelope["used_mutations"],
                }
        markdown = self._confirmation_markdown(user_request, plan, seed_records)
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_APPROVAL_REQUIRED,
            title="Operator approval required",
            summary="LLM-authored operator actions require explicit approval.",
            details={
                "action_count": len(actions),
                "confirmation_actions": actions,
                "seed_record_count": len(seed_records),
                "approval_envelope": envelope,
            },
        )
        return OperatorPipelineResult(
            status="confirmation_required",
            final_response=markdown,
            plan=plan,
            confirmation_required=True,
            confirmation_actions=actions,
            execution_records=seed_records,
            display_document=self._display_document(
                user_request=user_request,
                status="confirmation_required",
                markdown=markdown,
                plan=plan,
                records=seed_records,
            ),
            metadata={"approval_envelope": envelope} if envelope is not None else {},
        )

    def require_clarification(
        self,
        user_request: UserRequest,
        request: OperatorClarificationRequest,
        observability: ObservabilityContext | None = None,
        *,
        plan: OperatorPlan | None = None,
        records: list[OperatorExecutionRecord] | None = None,
        phase: str | None = None,
    ) -> OperatorPipelineResult:
        """Return a typed pause asking the user for one clarification answer."""

        if self._clarification_limit_reached(user_request):
            return self._clarification_limit_result(user_request, plan=plan, records=records)

        markdown = self._clarification_markdown(user_request, request)
        metadata = {
            "clarification_required": True,
            "clarification_phase": str(
                phase
                or ("operator_loop" if plan is not None or records else "pre_planning")
            ),
            "seed_record_count": len(records or []),
        }
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_CLARIFICATION_REQUIRED,
            title="Operator clarification required",
            summary="The request is waiting for a user clarification before planning or continuing.",
            details=request.model_dump(mode="json"),
        )
        return OperatorPipelineResult(
            status="clarification_required",
            final_response=markdown,
            plan=plan,
            execution_records=list(records or []),
            clarification_required=True,
            clarification_request=request,
            display_document=self._display_document(
                user_request=user_request,
                status="clarification_required",
                markdown=markdown,
                plan=plan,
                records=records,
            ),
            metadata=metadata,
        )
