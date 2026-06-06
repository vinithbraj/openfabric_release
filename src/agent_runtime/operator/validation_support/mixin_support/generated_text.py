"""Generated-text, macro, and literal payload validation helpers."""

from __future__ import annotations

from agent_runtime.operator.validation_support.mixin_support.common import *
from agent_runtime.operator.validation_support.mixin_support.common import (
    _ValidationConstantsMixin,
)


class _ValidationGeneratedTextMixin(_ValidationConstantsMixin):
    @staticmethod
    def _streaming_prior_candidate_is_generated_text(candidate: dict[str, Any]) -> bool:
        metadata = candidate.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        return (
            str(candidate.get("kind") or "").strip() == "llm_text"
            or bool(metadata.get("llm_text"))
            or bool(metadata.get("generated_text"))
            or str(metadata.get("source") or "").strip().lower() == "generated"
        )

    def _plan_consumes_prior_generated_text(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> bool:
        """Return whether this step consumes a fresh prior generated-text payload."""

        candidates = [
            candidate
            for candidate in self._streaming_prior_output_candidates(user_request)
            if self._streaming_prior_candidate_is_generated_text(candidate)
        ]
        if not candidates:
            return False
        generated_aliases = {
            alias
            for candidate in candidates
            for alias in self._streaming_prior_source_aliases(candidate)
        }
        if not generated_aliases:
            return False
        for action in plan.actions:
            if action.kind != "shell_command" or not action.input_bindings:
                continue
            stdin_name = str(action.stdin_input_name or "").strip()
            for binding in action.input_bindings:
                source_action_id = str(binding.source_action_id or "").strip()
                if source_action_id not in generated_aliases:
                    continue
                if (
                    action.stdin_mode == "input_binding"
                    and str(binding.input_name or "").strip() == stdin_name
                ):
                    return True
                input_name = str(binding.input_name or "").strip()
                env_name = _shell_binding_env_name(input_name) if input_name else ""
                command = str(action.command or "")
                if env_name and (
                    f"${env_name}" in command or f"${{{env_name}}}" in command
                ):
                    return True
        return False

    def _normalize_user_macro_shell_inputs(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None,
    ) -> None:
        """Remove redundant LLM-authored shell inputs covered by a private typein macro."""

        macro_input_name = _typein_macro_input_name_from_request(user_request)
        if not macro_input_name:
            return
        normalized: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            stdin_mode = str(action.stdin_mode or "none")
            stdin_name = str(action.stdin_input_name or "")
            stdin_looks_like_typein = (
                _input_name_looks_like_typein_alias(stdin_name)
                or is_user_macro_input_name(stdin_name, user_request.session_context)
                or is_user_macro_input_name(stdin_name, user_request.safety_context)
            )
            if (
                action.interaction_mode == "may_prompt"
                and stdin_mode != "none"
                and stdin_looks_like_typein
            ):
                action.stdin_mode = "none"
                action.stdin_input_name = None
                action.stdin_text = None
                normalized.append(
                    {
                        "action_id": action.action_id,
                        "normalization": "removed_terminal_prompt_stdin",
                        "input_name": macro_input_name,
                    }
                )
            if (
                action.stdin_mode == "input_binding"
                and _input_name_looks_like_typein_alias(action.stdin_input_name)
                and not is_user_macro_input_name(
                    str(action.stdin_input_name or ""), user_request.session_context
                )
                and not is_user_macro_input_name(
                    str(action.stdin_input_name or ""), user_request.safety_context
                )
            ):
                action.stdin_input_name = macro_input_name
                normalized.append(
                    {
                        "action_id": action.action_id,
                        "normalization": "stdin_input_name",
                        "input_name": macro_input_name,
                    }
                )
            if action.interaction_mode != "may_prompt" or not action.inputs:
                continue
            kept_inputs: dict[str, Any] = {}
            removed_inputs: list[str] = []
            command = str(action.command or "")
            for input_name, value in dict(action.inputs or {}).items():
                name = str(input_name or "").strip()
                try:
                    env_name = _shell_binding_env_name(name)
                except ValueError:
                    kept_inputs[name] = value
                    continue
                if _input_name_looks_like_typein_alias(
                    name
                ) and not _shell_command_references_env(command, env_name):
                    removed_inputs.append(name)
                    continue
                kept_inputs[name] = value
            if removed_inputs:
                action.inputs = kept_inputs
                normalized.append(
                    {
                        "action_id": action.action_id,
                        "normalization": "removed_redundant_literal_inputs",
                        "input_names": removed_inputs,
                    }
                )
        if normalized:
            self._emit(
                observability,
                level="info",
                event_type="operator.user_macro.normalized",
                title="User macro shell inputs normalized",
                summary=(
                    "Redundant shell inputs were removed because typein macro delivery "
                    "is handled privately by the runtime."
                ),
                details={"normalizations": normalized},
            )

    def _normalize_unused_shell_input_bindings(
        self,
        plan: OperatorPlan,
        observability: ObservabilityContext | None,
    ) -> None:
        """Drop shell input bindings that cannot affect execution semantics."""

        action_ids = {str(action.action_id or "").strip() for action in plan.actions}
        normalized: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command" or not action.input_bindings:
                continue
            command = str(action.command or "")
            stdin_mode = str(action.stdin_mode or "none")
            stdin_name = str(action.stdin_input_name or "").strip()
            kept_bindings: list[OperatorInputBinding] = []
            removed_bindings: list[dict[str, Any]] = []
            for binding in action.input_bindings:
                binding_name = str(binding.input_name or "").strip()
                if stdin_mode == "input_binding" and binding_name == stdin_name:
                    kept_bindings.append(binding)
                    continue
                try:
                    env_name = _shell_binding_env_name(binding_name)
                except ValueError:
                    kept_bindings.append(binding)
                    continue
                if _shell_command_references_env(command, env_name):
                    kept_bindings.append(binding)
                    continue
                source_action_id = str(binding.source_action_id or "").strip()
                if source_action_id and source_action_id not in action_ids:
                    kept_bindings.append(binding)
                    continue
                if (
                    source_action_id
                    and source_action_id != action.action_id
                    and source_action_id not in action.depends_on
                ):
                    action.depends_on.append(source_action_id)
                removed_bindings.append(
                    {
                        "input_name": binding_name,
                        "source_action_id": source_action_id,
                        "source_field": binding.source_field,
                    }
                )
            if removed_bindings:
                action.input_bindings = kept_bindings
                normalized.append(
                    {
                        "action_id": action.action_id,
                        "normalization": "removed_unused_shell_input_bindings",
                        "input_bindings": removed_bindings,
                    }
                )
        if normalized:
            self._emit(
                observability,
                level="info",
                event_type="operator.shell_bindings.normalized",
                title="Unused shell input bindings normalized",
                summary=(
                    "Unused shell input bindings were removed before validation because "
                    "the shell commands did not consume their OF_INPUT_* variables."
                ),
                details={"normalizations": normalized},
            )

    def _protect_literal_payload_shell_inputs(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None,
    ) -> None:
        """Prefer protected literal payloads over same-name shell output bindings."""

        payloads_by_input: dict[str, dict[str, Any]] = {}
        for context in (user_request.session_context, user_request.safety_context):
            for payload in literal_payloads_from_context(context):
                input_name = str(payload.get("input_name") or "").strip()
                if not input_name:
                    continue
                payloads_by_input.setdefault(
                    input_name,
                    {
                        "payload_id": str(payload.get("payload_id") or ""),
                        "kind": str(payload.get("kind") or ""),
                        "input_name": input_name,
                        "placeholder": str(payload.get("placeholder") or ""),
                        "value": str(payload.get("value") or ""),
                        "value_length": int(
                            payload.get("value_length")
                            or len(str(payload.get("value") or ""))
                        ),
                    },
                )
        if not payloads_by_input:
            return

        protected: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            command = str(action.command or "")
            if not command:
                continue
            action_inputs = dict(action.inputs or {})
            removed_bindings: list[dict[str, Any]] = []
            restored_payloads: list[dict[str, Any]] = []
            protected_names: set[str] = set()
            for input_name, payload in sorted(payloads_by_input.items()):
                try:
                    env_name = _shell_binding_env_name(input_name)
                except ValueError:
                    continue
                if not _shell_command_references_env(command, env_name):
                    continue
                protected_names.add(input_name)
                payload_value = str(payload.get("value") or "")
                current_value = action_inputs.get(input_name)
                current_text = (
                    _shell_input_value_to_text(current_value)
                    if input_name in action_inputs
                    else None
                )
                if current_text != payload_value:
                    action_inputs[input_name] = payload_value
                    restored_payloads.append(
                        {
                            "input_name": input_name,
                            "env_name": env_name,
                            "payload_id": str(payload.get("payload_id") or ""),
                            "kind": str(payload.get("kind") or ""),
                            "value_length": int(
                                payload.get("value_length") or len(payload_value)
                            ),
                        }
                    )
            if not protected_names:
                continue
            kept_bindings = []
            for binding in action.input_bindings:
                binding_name = str(binding.input_name or "").strip()
                if binding_name in protected_names:
                    removed_bindings.append(
                        {
                            "input_name": binding_name,
                            "source_action_id": str(binding.source_action_id or ""),
                            "source_field": binding.source_field,
                        }
                    )
                    continue
                kept_bindings.append(binding)
            if restored_payloads or removed_bindings:
                action.inputs = action_inputs
                if removed_bindings:
                    action.input_bindings = kept_bindings
                protected.append(
                    {
                        "action_id": action.action_id,
                        "payload_inputs": restored_payloads,
                        "removed_input_bindings": removed_bindings,
                    }
                )
        if protected:
            self._emit(
                observability,
                level="info",
                event_type="operator.literal_payload.shell_inputs_protected",
                title="Literal payload shell inputs protected",
                summary=(
                    "Protected user literal payloads were bound directly to matching "
                    "OF_INPUT_* shell variables before validation."
                ),
                details={"actions": protected},
            )

    def _rehydrate_literal_payload_action_values(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None,
    ) -> None:
        """Replace private literal payload placeholders in action data fields."""

        replacements = _literal_payload_replacements_from_request(user_request)
        if not replacements:
            return
        rehydrated: list[dict[str, Any]] = []
        for action in plan.actions:
            action_paths: list[str] = []
            if action.inputs:
                updated, changed = _replace_literal_payload_placeholders(
                    action.inputs, replacements
                )
                if changed:
                    action.inputs = dict(updated)
                    action_paths.append("inputs")
            if action.stdin_text is not None:
                updated, changed = _replace_literal_payload_placeholders(
                    action.stdin_text, replacements
                )
                if changed:
                    action.stdin_text = str(updated)
                    action_paths.append("stdin_text")
            for index, binding in enumerate(action.input_bindings):
                if binding.fallback_value is None:
                    continue
                updated, changed = _replace_literal_payload_placeholders(
                    binding.fallback_value,
                    replacements,
                )
                if changed:
                    binding.fallback_value = updated
                    action_paths.append(f"input_bindings[{index}].fallback_value")
            if action_paths:
                rehydrated.append(
                    {
                        "action_id": action.action_id,
                        "paths": action_paths,
                        "payloads": [
                            {
                                "payload_id": str(payload.get("payload_id") or ""),
                                "kind": str(payload.get("kind") or ""),
                                "input_name": str(payload.get("input_name") or ""),
                                "placeholder": str(payload.get("placeholder") or ""),
                                "value_length": int(payload.get("value_length") or 0),
                            }
                            for payload in replacements.values()
                        ],
                    }
                )
        if rehydrated:
            self._emit(
                observability,
                level="info",
                event_type="operator.literal_payload.rehydrated",
                title="Literal payloads rehydrated",
                summary=(
                    "Runtime-owned literal payload placeholders were replaced in "
                    "operator action data fields before validation."
                ),
                details={"actions": rehydrated},
            )

    def _literal_payload_placeholder_errors(
        self,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject payload placeholders that remain in executable/action values."""

        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            executable_fields = {
                "command": action.command,
                "code": action.code,
            }
            for field_name, field_value in executable_fields.items():
                matches = _literal_payload_placeholder_matches(field_value)
                if not matches:
                    continue
                errors.append(
                    {
                        "error": "literal_payload_placeholder_in_executable",
                        "message": (
                            f"{field_name} contains literal payload placeholder(s): "
                            f"{', '.join(matches)}. Literal payload placeholders are "
                            "runtime-owned data handles, not executable text."
                        ),
                        "action_id": action.action_id,
                        "field": field_name,
                        "placeholders": matches,
                        "repair_hint": (
                            "Move the user-provided payload into action.inputs or explicit "
                            "captured stdin and reference it through the runtime-provided "
                            "OF_INPUT_* variable or stdin binding. Do not inline the "
                            "placeholder into command/code."
                        ),
                    }
                )
            data_fields = {
                "inputs": action.inputs,
                "stdin_text": action.stdin_text,
                "input_binding_fallbacks": [
                    binding.fallback_value for binding in action.input_bindings
                ],
            }
            for field_name, field_value in data_fields.items():
                matches = _literal_payload_placeholder_matches(field_value)
                if not matches:
                    continue
                errors.append(
                    {
                        "error": "literal_payload_placeholder_unresolved",
                        "message": (
                            f"{field_name} still contains unresolved literal payload "
                            f"placeholder(s): {', '.join(matches)}."
                        ),
                        "action_id": action.action_id,
                        "field": field_name,
                        "placeholders": matches,
                        "repair_hint": (
                            "Use one of the provided literal payload input names exactly, "
                            "or keep the placeholder as an action input value so the runtime "
                            "can rehydrate it before validation."
                        ),
                    }
                )
        return errors

    def _generated_text_plan_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Require a real llm_text producer for generated-text steps."""

        if not operator_request_requires_generated_text(user_request):
            return []
        if any(action.kind == "llm_text" for action in plan.actions):
            return []
        if self._plan_consumes_prior_generated_text(user_request, plan):
            return []
        return [
            {
                "error": "generated_text_action_missing",
                "message": (
                    "This step asks for generated natural-language text, but the plan "
                    "does not include an llm_text action. Shell evidence commands may "
                    "support generation, but they are not the generated text."
                ),
                "repair_hint": (
                    "Add an llm_text action with llm_prompt and input_bindings from any "
                    "runtime evidence. Bind that llm_text stdout/output into downstream consumers."
                ),
            }
        ]

    def _llm_text_prior_binding_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject generated-text actions bound to empty prior runtime output."""

        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "llm_text":
                continue
            for binding in list(action.input_bindings or []):
                prior = self._streaming_prior_record_for_source(
                    user_request,
                    str(binding.source_action_id or ""),
                )
                if prior is None:
                    continue
                record, _task_result = prior
                source_field = str(binding.source_field or "stdout").strip() or "stdout"
                raw_value = self._execution_record_value(record, source_field)
                value = (
                    raw_value if isinstance(raw_value, str) else str(raw_value or "")
                )
                if value.strip():
                    continue
                errors.append(
                    {
                        "error": "llm_text_prior_binding_empty",
                        "message": (
                            "llm_text cannot generate from an empty prior runtime output."
                        ),
                        "action_id": action.action_id,
                        "input_name": binding.input_name,
                        "source_action_id": binding.source_action_id,
                        "source_field": source_field,
                        "repair_hint": (
                            "Collect fresh read-only evidence or bind a prior action with "
                            "non-empty stdout/output before the llm_text action."
                        ),
                    }
                )
        return errors

    def _generated_text_prior_reuse_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject duplicate generation when a consumer step already has prior generated text."""

        if not self._streaming_task_can_consume_generated_text(user_request):
            return []
        prior_generated_text = [
            candidate
            for candidate in self._streaming_prior_output_candidates(user_request)
            if self._streaming_prior_candidate_is_generated_text(candidate)
        ]
        if not prior_generated_text:
            return []
        duplicate_actions = [
            action for action in plan.actions if action.kind == "llm_text"
        ]
        if not duplicate_actions:
            return []
        return [
            {
                "error": "generated_text_prior_reuse_required",
                "message": (
                    "This step consumes previously generated text, but the plan adds "
                    "another llm_text action instead of using the prior generated text."
                ),
                "action_ids": [action.action_id for action in duplicate_actions],
                "prior_source_action_id": prior_generated_text[0].get("action_id"),
                "repair_hint": (
                    "Remove the duplicate llm_text action and bind the prior llm_text "
                    "stdout/output into the downstream consumer."
                ),
            }
        ]


__all__ = ["_ValidationGeneratedTextMixin"]
