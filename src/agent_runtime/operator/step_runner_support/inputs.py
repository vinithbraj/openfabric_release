"""Input, shell binding, and runtime contract helpers for the operator step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class _StepRunnerInputsMixin:
    @staticmethod
    def _record_field(record: OperatorExecutionRecord, source_field: str) -> Any:
        """Return one declared field from a completed operator execution record."""

        field = str(source_field or "stdout").strip()
        if field == "stdout":
            return record.stdout
        if field == "stderr":
            return record.stderr
        if field == "exit_code":
            return record.exit_code
        if field == "output":
            return record.output
        if field == "record":
            return record.model_dump(mode="json")
        raise RuntimeError(f"Unsupported operator input binding source_field: {field}")

    def _resolve_action_inputs(
        self,
        action: OperatorAction,
        records_by_action: dict[str, OperatorExecutionRecord],
        observability: ObservabilityContext | None,
    ) -> dict[str, Any]:
        """Resolve literal and bound inputs for one operator action."""

        resolved = dict(action.inputs or {})
        binding_details: list[dict[str, Any]] = []
        for binding in action.input_bindings:
            source = records_by_action.get(binding.source_action_id)
            if source is None:
                if binding.required:
                    raise RuntimeError(
                        f"Required operator input {binding.input_name!r} depends on "
                        f"unavailable action {binding.source_action_id!r}."
                    )
                resolved[binding.input_name] = binding.fallback_value
                continue
            if source.status != "success" and binding.required:
                raise RuntimeError(
                    f"Required operator input {binding.input_name!r} depends on failed "
                    f"action {binding.source_action_id!r}."
                )
            value = self._record_field(source, binding.source_field)
            if value is None and binding.required:
                raise RuntimeError(
                    "required_binding_resolved_null: "
                    f"Required operator input {binding.input_name!r} resolved to null "
                    f"from action {binding.source_action_id!r} field {binding.source_field!r}."
                )
            if (value is None or value == "") and not binding.required:
                value = binding.fallback_value
            resolved[binding.input_name] = value
            binding_details.append(
                {
                    "input_name": binding.input_name,
                    "source_action_id": binding.source_action_id,
                    "source_field": binding.source_field,
                    "value_preview": _truncate(value, 240),
                }
            )
        if action.input_bindings:
            self._emit(
                observability,
                level="info",
                event_type="operator.input_bindings.resolved",
                title="Operator input bindings resolved",
                summary="The runtime injected declared upstream action outputs into this action.",
                details={
                    "action_id": action.action_id,
                    "bindings": binding_details,
                    "input_names": sorted(resolved.keys()),
                },
            )
        return resolved

    def _resolve_shell_stdin(
        self,
        action: OperatorAction,
        records_by_action: dict[str, OperatorExecutionRecord],
        observability: ObservabilityContext | None,
        execution_context: dict[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, Any]]:
        """Resolve an explicit shell stdin payload for one action."""

        mode = str(getattr(action, "stdin_mode", "none") or "none")
        if action.kind != "shell_command" or mode == "none":
            return None, {"stdin_mode": "none"}
        if mode == "literal":
            text = str(action.stdin_text or "")
            metadata = {
                "stdin_mode": "literal",
                "stdin_length": len(text),
                "stdin_preview": _truncate(text, 500),
            }
        elif mode == "input_binding":
            input_name = str(action.stdin_input_name or "").strip()
            if is_user_macro_input_name(input_name, execution_context):
                macro = consume_typein_macro(
                    execution_context,
                    input_name=input_name,
                    delivery="shell_stdin",
                    action_id=action.action_id,
                )
                if macro is None:
                    raise RuntimeError(
                        f"Required user macro stdin input {input_name!r} was already consumed or unavailable."
                    )
                text = macro_value_with_enter(macro.get("value"))
                delivery = user_macro_public_delivery(
                    macro,
                    delivery="shell_stdin",
                    action_id=action.action_id,
                )
                metadata = {
                    "stdin_mode": "input_binding",
                    "stdin_input_name": input_name,
                    "stdin_length": len(text),
                    "stdin_preview": "" if delivery.get("redacted") else _truncate(text, 500),
                    "user_macro": delivery,
                }
                self._emit(
                    observability,
                    level="info",
                    event_type="operator.user_macro.consumed",
                    title="User macro consumed",
                    summary="The runtime supplied a deterministic user macro as shell stdin.",
                    details=delivery,
                )
            else:
                resolved = self._resolve_action_inputs(action, records_by_action, observability)
                value = resolved.get(input_name)
                if value is None:
                    raise RuntimeError(
                        "required_binding_resolved_null: "
                        f"Required shell stdin input binding {input_name!r} resolved to null."
                    )
                text = str(value)
                if not text.strip():
                    raise RuntimeError(
                        "required_binding_resolved_null: "
                        f"Required shell stdin input binding {input_name!r} resolved to an empty value."
                    )
                metadata = {
                    "stdin_mode": "input_binding",
                    "stdin_input_name": input_name,
                    "stdin_length": len(text),
                    "stdin_preview": _truncate(text, 500),
                }
        else:
            raise RuntimeError(f"Unsupported shell stdin mode: {mode}")
        if len(text) > _SHELL_STDIN_MAX_CHARS:
            raise RuntimeError(
                f"Shell stdin payload exceeds {_SHELL_STDIN_MAX_CHARS} characters."
            )
        self._emit(
            observability,
            level="info",
            event_type="operator.shell_stdin.resolved",
            title="Shell stdin resolved",
            summary="The runtime prepared explicit stdin for a captured shell command.",
            details={"action_id": action.action_id, **metadata},
        )
        return text, metadata

    def _resolve_shell_literal_input_env(
        self,
        action: OperatorAction,
        observability: ObservabilityContext | None,
    ) -> dict[str, str]:
        """Resolve shell literal inputs into OF_INPUT_* environment values."""

        if action.kind != "shell_command" or not action.inputs:
            return {}
        env: dict[str, str] = {}
        input_details: list[dict[str, Any]] = []
        for input_name, raw_value in sorted(dict(action.inputs or {}).items()):
            env_name = _shell_binding_env_name(str(input_name))
            text = _shell_input_value_to_text(raw_value)
            if len(text) > _SHELL_LITERAL_INPUT_MAX_CHARS:
                raise RuntimeError(
                    f"Shell input {input_name!r} exceeds {_SHELL_LITERAL_INPUT_MAX_CHARS} characters."
                )
            env[env_name] = text
            input_details.append(
                {
                    "input_name": str(input_name),
                    "env_name": env_name,
                    "value_preview": _truncate(text, 500),
                    "value_length": len(text),
                }
            )
        self._emit(
            observability,
            level="info",
            event_type="operator.shell_inputs.resolved",
            title="Shell inputs resolved",
            summary="The runtime exposed shell action inputs as OF_INPUT_* environment variables.",
            details={"action_id": action.action_id, "inputs": input_details},
        )
        return env

    def _resolve_shell_binding_env(
        self,
        action: OperatorAction,
        records_by_action: dict[str, OperatorExecutionRecord],
        observability: ObservabilityContext | None,
        *,
        exclude_input_names: set[str] | None = None,
    ) -> dict[str, str]:
        """Resolve shell input bindings into short, safe environment values."""

        if not action.input_bindings:
            return {}
        excluded = {str(item or "").strip() for item in set(exclude_input_names or set())}
        env_bindings = [
            binding
            for binding in action.input_bindings
            if str(binding.input_name or "").strip() not in excluded
        ]
        if not env_bindings:
            return {}
        resolved = self._resolve_action_inputs(action, records_by_action, observability)
        env: dict[str, str] = {}
        binding_details: list[dict[str, Any]] = []
        for binding in env_bindings:
            if binding.source_field == "stderr":
                raise RuntimeError(
                    "shell_command input_bindings cannot bind stderr; use stdout/output/exit_code "
                    "or a Python action for diagnostic parsing."
                )
            env_name = _shell_binding_env_name(binding.input_name)
            value = resolved.get(binding.input_name)
            if value is None:
                if binding.required:
                    raise RuntimeError(
                        f"Required shell input binding {binding.input_name!r} resolved to null."
                    )
                value = binding.fallback_value
            text = str(value if value is not None else "").strip()
            if binding.required and not text:
                raise RuntimeError(
                    f"Required shell input binding {binding.input_name!r} resolved to an empty value."
                )
            if "\n" in text or "\r" in text:
                raise RuntimeError(
                    f"Shell input binding {binding.input_name!r} resolved to multiline content; "
                    "use python_action/python_transform for multiline data."
                )
            if len(text) > _SHELL_INPUT_BINDING_MAX_CHARS:
                raise RuntimeError(
                    f"Shell input binding {binding.input_name!r} exceeds "
                    f"{_SHELL_INPUT_BINDING_MAX_CHARS} characters."
                )
            env[env_name] = text
            binding_details.append(
                {
                    "input_name": binding.input_name,
                    "source_action_id": binding.source_action_id,
                    "source_field": binding.source_field,
                    "env_name": env_name,
                    "value_preview": _truncate(text, 240),
                    "value_length": len(text),
                }
            )
        self._emit(
            observability,
            level="info",
            event_type="operator.shell_bindings.resolved",
            title="Shell input bindings resolved",
            summary="The runtime resolved upstream outputs into OF_INPUT_* shell environment variables.",
            details={"action_id": action.action_id, "bindings": binding_details},
        )
        return env

    @staticmethod
    def _shell_bound_command(command: str, env: dict[str, str]) -> str:
        """Return a gateway-compatible command with safe env assignments prefixed."""

        if not env:
            return str(command or "")
        prefix = "; ".join(f"export {name}={shlex.quote(value)}" for name, value in sorted(env.items()))
        return f"{prefix}; {command}"

    def _shell_bound_confirmation_result(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        action: OperatorAction,
        env: dict[str, str],
        stdin_metadata: dict[str, Any],
        records: list[OperatorExecutionRecord],
        observability: ObservabilityContext | None,
    ) -> OperatorPipelineResult:
        """Pause before executing a shell action with newly bound runtime values."""

        action_labels = self._operator_action_labels(plan)
        label = action_labels.get(action.action_id, action.action_id)
        bindings = [
            {
                "env_name": name,
                "value_preview": _truncate(value, 500),
                "value_length": len(value),
            }
            for name, value in sorted(env.items())
        ]
        stdin_details = dict(stdin_metadata or {"stdin_mode": "none"})
        lines = [
            "## Bound Shell Values Require Confirmation",
            "",
            "A shell action is about to consume values produced by earlier actions.",
            "",
            f"### {label}",
            "",
            f"- Action: `{action.action_id}`",
            f"- Cwd: `{action.cwd}`",
            "- Command:",
            f"  ```bash\n{_truncate(action.command, 1200)}\n  ```",
            "",
            "### Bound Environment",
        ]
        if bindings:
            for binding in bindings:
                lines.append(
                    f"- `{binding['env_name']}` = `{binding['value_preview']}` "
                    f"({binding['value_length']} chars)"
                )
        else:
            lines.append("- None")
        if stdin_details.get("stdin_mode") != "none":
            lines.extend(
                [
                    "",
                    "### Bound Stdin",
                    f"- Mode: `{stdin_details.get('stdin_mode')}`",
                    f"- Input: `{stdin_details.get('stdin_input_name') or 'literal'}`",
                    f"- Preview: `{stdin_details.get('stdin_preview') or ''}`",
                    f"- Length: `{stdin_details.get('stdin_length') or 0}` chars",
                ]
            )
        lines.extend(
            [
                "",
                "Approve to run this command with the bound environment values. Deny to stop here.",
            ]
        )
        markdown = "\n".join(lines)
        confirmation_action = {
            "capability_id": "operator.shell_input_bindings",
            "operation_id": "continue_with_bound_shell_inputs",
            "details": label,
            "risk": action.risk,
            "reason": "A shell command will consume upstream outputs through OF_INPUT_* environment variables.",
            "action_id": action.action_id,
            "bindings": bindings,
            "stdin": stdin_details,
            "command": _truncate(action.command, 1200),
            "cwd": action.cwd,
        }
        self._emit(
            observability,
            level="warning",
            event_type="operator.shell_bindings.awaiting_bound_confirmation",
            title="Bound shell inputs require approval",
            summary="The runtime paused before executing a shell command with upstream-bound values.",
            details={
                "confirmation_actions": [confirmation_action],
                "bindings": bindings,
                "stdin": stdin_details,
            },
        )
        return OperatorPipelineResult(
            status="confirmation_required",
            final_response=markdown,
            plan=plan,
            execution_records=list(records),
            confirmation_required=True,
            confirmation_actions=[confirmation_action],
            display_document=self._display_document(
                user_request=user_request,
                status="confirmation_required",
                markdown=markdown,
                plan=plan,
                records=records,
            ),
            metadata={
                "shell_input_bindings_confirmation_pending": True,
                "shell_input_bindings_action_id": action.action_id,
                "shell_input_bindings": bindings,
                "shell_stdin": stdin_details,
                "seed_record_count": len(records),
            },
        )

    @staticmethod
    def _input_preview(value: Any, *, max_chars: int = 6000) -> dict[str, Any]:
        """Return a compact shape and snippet for one runtime input value."""

        if isinstance(value, str):
            lines = value.splitlines()
            return {
                "type": "string",
                "chars": len(value),
                "line_count": len(lines),
                "preview": _truncate(value, max_chars),
            }
        if isinstance(value, dict):
            return {
                "type": "object",
                "key_count": len(value),
                "keys": list(value.keys())[:50],
                "preview": _truncate(_stable_json(value), max_chars),
            }
        if isinstance(value, (list, tuple)):
            return {
                "type": "array",
                "item_count": len(value),
                "sample": value[:5] if isinstance(value, list) else list(value[:5]),
                "preview": _truncate(_stable_json(value), max_chars),
            }
        return {
            "type": type(value).__name__,
            "preview": _truncate(repr(value), max_chars),
        }

    @classmethod
    def _inputs_preview(cls, inputs: dict[str, Any], *, max_chars: int = 4000) -> dict[str, Any]:
        """Return compact previews for action inputs bound at execution time."""

        return {
            name: cls._input_preview(value, max_chars=max_chars)
            for name, value in sorted(dict(inputs or {}).items())
        }

    @staticmethod
    def _runtime_input_contract(value: Any, *, max_chars: int = 6000) -> dict[str, Any]:
        """Return the exact runtime access shape plus a bounded sample value."""

        if isinstance(value, str):
            return {
                "runtime_type": "string",
                "sample_value": _truncate(value, max_chars),
            }
        if isinstance(value, dict):
            sample = {
                str(key): _StepRunnerInputsMixin._runtime_input_contract(item, max_chars=max_chars)
                for key, item in list(value.items())[:20]
            }
            return {
                "runtime_type": "object",
                "sample_value": sample,
                "sample_truncated": len(value) > 20,
            }
        if isinstance(value, (list, tuple)):
            sample_items = [
                _StepRunnerInputsMixin._runtime_input_contract(item, max_chars=max_chars)
                for item in list(value)[:5]
            ]
            return {
                "runtime_type": "array",
                "sample_value": sample_items,
                "sample_truncated": len(value) > 5,
            }
        return {
            "runtime_type": type(value).__name__,
            "sample_value": value if value is None or isinstance(value, (bool, int, float)) else repr(value),
        }



__all__ = ["_StepRunnerInputsMixin"]
