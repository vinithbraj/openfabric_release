"""Core operator plan validator and shell mutation classifier."""

from __future__ import annotations

from agent_runtime.operator.validation_support.common import *
from agent_runtime.operator.validation_support.context import *
from agent_runtime.operator.validation_support.generated_python import *

class OperatorPlanValidator:
    """Generic operator plan validator driven by schemas and safety policy."""

    _never_override_fragments = (
        "rm -rf /",
        "mkfs",
        "shutdown",
        "reboot",
        ":(){",
        "dd if=",
        "dd of=",
    )
    _never_override_tokens = {"su"}
    _overrideable_hard_block_tokens = {"sudo"}

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.workspace_root = Path(config.workspace_root or ".").expanduser().resolve()
        self.terminal_cwd = self._trusted_terminal_cwd(config.terminal_session_id, config.terminal_cwd)

    @staticmethod
    def _trusted_terminal_cwd(session_id: str | None, cwd: str | None) -> Path | None:
        if not str(session_id or "").strip():
            return None
        raw_cwd = str(cwd or "").strip()
        if not raw_cwd:
            return None
        try:
            return Path(raw_cwd).expanduser().resolve(strict=False)
        except Exception:
            return None

    def _workspace_path(self, cwd: str) -> tuple[Path | None, str | None]:
        raw = str(cwd or ".").strip() or "."
        if raw == "." and self.terminal_cwd is not None:
            return self.terminal_cwd, None
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            if self.terminal_cwd is not None:
                terminal_relative = self.terminal_cwd / candidate
                try:
                    terminal_resolved = terminal_relative.resolve(strict=False)
                    terminal_resolved.relative_to(self.terminal_cwd)
                    return terminal_resolved, None
                except Exception:
                    pass
            candidate = self.workspace_root / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            return None, f"cwd {raw!r} could not be resolved."
        if not bool(getattr(self.config, "operator_workspace_cwd_guard_enabled", False)):
            return resolved, None
        try:
            resolved.relative_to(self.workspace_root)
        except Exception:
            if self.terminal_cwd is not None and resolved == self.terminal_cwd:
                return resolved, None
            return None, f"cwd {raw!r} resolves outside the workspace root."
        return resolved, None

    @staticmethod
    def _has_cycle(action_ids: set[str], edges: list[tuple[str, str]]) -> bool:
        adjacency: dict[str, set[str]] = {action_id: set() for action_id in action_ids}
        indegree: dict[str, int] = {action_id: 0 for action_id in action_ids}
        for source, target in edges:
            if target not in adjacency[source]:
                adjacency[source].add(target)
                indegree[target] += 1
        queue = deque([action_id for action_id, degree in indegree.items() if degree == 0])
        seen = 0
        while queue:
            current = queue.popleft()
            seen += 1
            for child in adjacency[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        return seen != len(action_ids)

    def _validate_shell_action(self, action: OperatorAction) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        if not self.config.allow_shell_execution:
            errors.append(
                {
                    "error": "shell_execution_disabled",
                    "message": "Shell execution is disabled by runtime policy.",
                    "action_id": action.action_id,
                }
            )
        command = str(action.command or "").strip()
        lowered = command.lower()
        try:
            tokens = set(shlex.split(command, comments=False, posix=True)) if command else set()
        except ValueError as exc:
            errors.append(
                {
                    "error": "command_parse_error",
                    "message": str(exc),
                    "action_id": action.action_id,
                }
            )
            tokens = set()
        never_override = any(fragment in lowered for fragment in self._never_override_fragments) or bool(
            tokens & self._never_override_tokens
        )
        overrideable_tokens = tokens & self._overrideable_hard_block_tokens
        overrideable = bool(overrideable_tokens)
        command_hash = operator_command_hash(command)
        allowed_hashes = {
            str(item or "").strip().lower()
            for item in getattr(self.config, "operator_command_allowlist_hashes", []) or []
            if str(item or "").strip()
        }
        if never_override or (overrideable and command_hash not in allowed_hashes):
            block_reason = (
                "The command contains a non-overridable hard-blocked shell operation."
                if never_override
                else "The command uses an overrideable hard-blocked shell operation."
            )
            errors.append(
                {
                    "error": "forbidden_command",
                    "message": "The operator command contains a hard-blocked shell operation.",
                    "action_id": action.action_id,
                    "overrideable": bool(overrideable and not never_override),
                    "block_reason": block_reason,
                    "normalized_command": normalize_operator_command(command),
                    "command_hash": command_hash,
                }
            )
        if len(command) > 8000:
            errors.append(
                {
                    "error": "command_too_large",
                    "message": "Operator shell commands must be 8000 characters or fewer.",
                    "action_id": action.action_id,
                }
            )
        foreground_service_reason = _captured_foreground_service_reason(command)
        if (
            foreground_service_reason
            and action.execution_mode == "captured"
            and action.interaction_mode == "non_interactive"
        ):
            errors.append(
                {
                    "error": "captured_foreground_service_command",
                    "message": (
                        f"{foreground_service_reason} Use a detach/background flag for "
                        "captured execution, or use interaction_mode long_running with "
                        "execution_mode terminal_detached when the user wants to observe "
                        "a foreground service process."
                    ),
                    "action_id": action.action_id,
                    "repair_hint": (
                        "For Docker Compose service startup in captured mode, use "
                        "`docker compose up -d` or `docker compose -f <file> up -d`."
                    ),
                }
            )
        compose_file_order_reason = _docker_compose_file_option_order_reason(command)
        if compose_file_order_reason:
            errors.append(
                {
                    "error": "docker_compose_file_option_order",
                    "message": (
                        f"{compose_file_order_reason} Use `docker compose -f <file> "
                        "<subcommand> ...`, for example `docker compose -f \"$OF_INPUT_COMPOSE_FILE\" up -d`."
                    ),
                    "action_id": action.action_id,
                    "repair_hint": (
                        "Move -f/--file before the Docker Compose subcommand. Example: "
                        "`docker compose -f \"$OF_INPUT_COMPOSE_FILE\" up -d`."
                    ),
                }
            )
        path_output_reason = _shell_path_output_absolute_reason(command)
        if path_output_reason:
            errors.append(
                {
                    "error": "path_output_not_absolute",
                    "message": (
                        f"{path_output_reason} Path discovery commands must emit absolute "
                        "paths when their output may be reported or consumed downstream."
                    ),
                    "action_id": action.action_id,
                    "repair_hint": (
                        "Use an absolute search root such as `find \"$PWD\" ...`, "
                        "`find /absolute/root ...`, or pipe relative path output through "
                        "`realpath`/`readlink -f` before reporting or consuming it."
                    ),
                }
            )
        allowed_env_names: set[str] = set()
        literal_input_names = {str(name or "").strip() for name in dict(action.inputs or {})}
        bound_input_names = {str(binding.input_name or "").strip() for binding in action.input_bindings}
        for input_name in sorted(literal_input_names):
            try:
                env_name = _shell_binding_env_name(input_name)
            except ValueError as exc:
                errors.append(
                    {
                        "error": "shell_input_name_invalid",
                        "message": str(exc),
                        "action_id": action.action_id,
                        "input_name": input_name,
                    }
                )
                continue
            allowed_env_names.add(env_name)
            text = _shell_input_value_to_text(dict(action.inputs or {}).get(input_name))
            if len(text) > _SHELL_LITERAL_INPUT_MAX_CHARS:
                errors.append(
                    {
                        "error": "shell_input_too_large",
                        "message": (
                            f"shell_command input {input_name!r} must be "
                            f"{_SHELL_LITERAL_INPUT_MAX_CHARS} characters or fewer."
                        ),
                        "action_id": action.action_id,
                        "input_name": input_name,
                        "value_length": len(text),
                    }
                )
            if input_name in bound_input_names:
                errors.append(
                    {
                        "error": "shell_input_name_conflict",
                        "message": (
                            "shell_command inputs and input_bindings must not use the same "
                            f"input name {input_name!r}."
                        ),
                        "action_id": action.action_id,
                        "input_name": input_name,
                    }
                )
            if not _shell_command_references_env(command, env_name):
                errors.append(
                    {
                        "error": "shell_input_env_unreferenced",
                        "message": (
                            "shell_command literal inputs must be consumed through their "
                            f"runtime environment variable {env_name!r}."
                        ),
                        "action_id": action.action_id,
                        "input_name": input_name,
                        "env_name": env_name,
                    }
                )
        stdin_mode = str(getattr(action, "stdin_mode", "none") or "none")
        stdin_binding_name = _action_stdin_binding_name(action)
        if stdin_mode != "none" and (
            action.execution_mode != "captured" or action.interaction_mode != "non_interactive"
        ):
            errors.append(
                {
                    "error": "shell_stdin_interactive_mode_invalid",
                    "message": (
                        "Explicit shell stdin is only supported for captured non_interactive "
                        "shell_command actions. Use terminal interaction for real prompts."
                    ),
                    "action_id": action.action_id,
                    "stdin_mode": stdin_mode,
                }
            )
        if stdin_mode == "none":
            stdin_reason = _shell_command_external_stdin_reason(command)
            if stdin_reason:
                errors.append(
                    {
                        "error": "implicit_shell_stdin_required",
                        "message": (
                            f"{stdin_reason} Put the payload in shell inputs and reference "
                            "the matching OF_INPUT_* variable inside an explicit pipe or "
                            "temporary file command, or add stdin_mode only for captured "
                            "non-interactive execution, or use "
                            "may_prompt terminal execution for genuine interactive input."
                        ),
                        "action_id": action.action_id,
                        "repair_hint": (
                            "Do not rely on captured shell commands to wait for invisible input. "
                            "Declare the payload as a shell input and consume $OF_INPUT_<NAME> "
                            "inside the command."
                        ),
                    }
                )
        elif stdin_mode == "literal":
            stdin_text = str(action.stdin_text or "")
            if len(stdin_text) > _SHELL_STDIN_MAX_CHARS:
                errors.append(
                    {
                        "error": "shell_stdin_too_large",
                        "message": f"shell_command stdin_text must be {_SHELL_STDIN_MAX_CHARS} characters or fewer.",
                        "action_id": action.action_id,
                        "stdin_length": len(stdin_text),
                    }
                )
        elif stdin_mode == "input_binding":
            binding_names = {str(binding.input_name or "").strip() for binding in action.input_bindings}
            if not stdin_binding_name or stdin_binding_name not in binding_names:
                errors.append(
                    {
                        "error": "shell_stdin_binding_missing",
                        "message": (
                            "stdin_mode input_binding requires stdin_input_name to match one "
                            "declared input_binding.input_name."
                        ),
                        "action_id": action.action_id,
                        "stdin_input_name": stdin_binding_name,
                    }
                )
        binding_mode = _shell_input_bindings_mode_from_value(
            getattr(self.config, "shell_input_bindings_mode", "allow")
        )
        if action.input_bindings and binding_mode != "off":
            for binding in action.input_bindings:
                if stdin_mode == "input_binding" and str(binding.input_name or "").strip() == stdin_binding_name:
                    continue
                try:
                    env_name = _shell_binding_env_name(binding.input_name)
                except ValueError as exc:
                    errors.append(
                        {
                            "error": "shell_input_binding_name_invalid",
                            "message": str(exc),
                            "action_id": action.action_id,
                            "input_name": binding.input_name,
                        }
                    )
                    continue
                allowed_env_names.add(env_name)
                if binding.source_field == "stderr":
                    errors.append(
                        {
                            "error": "shell_input_binding_stderr_unsupported",
                            "message": (
                                "shell_command input_bindings cannot bind stderr. "
                                "Use stdout/output/exit_code or parse stderr inside Python."
                            ),
                            "action_id": action.action_id,
                            "input_name": binding.input_name,
                            "source_field": binding.source_field,
                        }
                    )
                if not _shell_command_references_env(command, env_name):
                    errors.append(
                        {
                            "error": "shell_input_binding_env_unreferenced",
                            "message": (
                                "shell_command input_bindings must be consumed through their "
                                f"runtime environment variable {env_name!r}."
                            ),
                            "action_id": action.action_id,
                            "input_name": binding.input_name,
                            "env_name": env_name,
                        }
                    )
        placeholders = [
            placeholder
            for placeholder in unresolved_shell_placeholders(command)
            if _shell_variable_name(placeholder) not in allowed_env_names
        ]
        if placeholders:
            literal_payload_evidence = _literal_payload_placeholder_evidence(command, placeholders)
            errors.append(
                {
                    "error": "unresolved_shell_placeholder",
                    "message": (
                        "Shell command contains unresolved placeholder(s): "
                        f"{', '.join(placeholders)}. Shell commands must be concrete at approval time. "
                        "Use one complete shell command with any runtime lookup assigned and consumed inside "
                        "that command, or use python_action/python_transform actions with input_bindings."
                    ),
                    "action_id": action.action_id,
                    "placeholders": placeholders,
                    "context_sensitive": bool(literal_payload_evidence),
                    "context_sensitive_reason": (
                        "possible_literal_file_payload"
                        if literal_payload_evidence
                        else ""
                    ),
                    "literal_payload_evidence": literal_payload_evidence,
                }
            )
        return errors

    def _validate_python_action(self, action: OperatorAction) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        code = str(action.code or "")
        if not code.lstrip().startswith("def transform(inputs):"):
            errors.append(
                {
                    "error": "python_transform_contract_shape",
                    "message": (
                        "python_transform code must start with def transform(inputs): "
                        "and put all executable statements, including imports, "
                        "inside that function body."
                    ),
                    "action_id": action.action_id,
                    "repair_hint": (
                        "Move any top-level imports or statements under def transform(inputs):."
                    ),
                }
            )
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return [
                {
                    "error": "python_syntax_error",
                    "message": str(exc),
                    "action_id": action.action_id,
                }
            ]
        function_names = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        if "transform" not in function_names:
            errors.append(
                {
                    "error": "missing_transform_function",
                    "message": "python_transform code must define transform(inputs).",
                    "action_id": action.action_id,
                }
            )
        allowed_input_names = {
            str(name)
            for name in dict(action.inputs or {}).keys()
            if str(name).strip()
        } | {
            str(binding.input_name)
            for binding in action.input_bindings
            if str(binding.input_name or "").strip()
        }
        referenced_input_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                if node.value.id == "inputs":
                    slice_node = node.slice
                    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                        referenced_input_names.add(slice_node.value)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "inputs"
                    and node.func.attr == "get"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    input_name = node.args[0].value
                    referenced_input_names.add(input_name)
        for input_name in sorted(referenced_input_names - allowed_input_names):
            errors.append(
                {
                    "error": "python_input_unbound",
                    "message": (
                        f"python_transform code references inputs[{input_name!r}], "
                        "but no literal input or input_binding supplies that key."
                    ),
                    "action_id": action.action_id,
                    "input_name": input_name,
                    "available_inputs": sorted(allowed_input_names),
                }
            )
        return errors

    def _validate_python_program_action(self, action: OperatorAction) -> list[dict[str, Any]]:
        """Validate gateway-executed Python program actions."""

        errors: list[dict[str, Any]] = []
        code = str(action.code or "")
        if not code.lstrip().startswith("def main(inputs):"):
            errors.append(
                {
                    "error": "python_action_contract_shape",
                    "message": (
                        "python_action code must start with def main(inputs): "
                        "and put all executable statements, including imports, "
                        "inside that function body."
                    ),
                    "action_id": action.action_id,
                    "repair_hint": (
                        "Move any top-level imports or statements under def main(inputs): "
                        "while preserving the action's intended behavior."
                    ),
                }
            )
        if len(code) > 20000:
            errors.append(
                {
                    "error": "python_action_too_large",
                    "message": "python_action code must be 20000 characters or fewer.",
                    "action_id": action.action_id,
                }
            )
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return [
                {
                    "error": "python_syntax_error",
                    "message": str(exc),
                    "action_id": action.action_id,
                }
            ]
        function_names = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        all_function_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if "main" not in function_names:
            errors.append(
                {
                    "error": "missing_python_action_main",
                    "message": "python_action code must define main(inputs).",
                    "action_id": action.action_id,
                }
            )
        if _SIZE_SUFFIX_BARE_B_FIRST_RE.search(code):
            errors.append(
                {
                    "error": "python_action_size_suffix_order",
                    "message": (
                        "python_action appears to parse compact size strings by checking "
                        "bare 'B' before longer units such as KB, MB, or GB."
                    ),
                    "action_id": action.action_id,
                    "repair_hint": (
                        "Parse the numeric prefix and alphabetic suffix separately, or check "
                        "longer size suffixes like TB, GB, MB, and KB before bare B. Do not "
                        "strip the final B before deciding the full unit."
                    ),
                }
            )
        if "parse_size" in called_names and "parse_size" not in all_function_names:
            errors.append(
                {
                    "error": "python_action_undefined_size_parser",
                    "message": "python_action calls parse_size(...) but does not define that helper.",
                    "action_id": action.action_id,
                    "repair_hint": (
                        "Define parse_size in the same concrete Python action before use, "
                        "or inline the size parsing with explicit unit handling."
                    ),
                }
            )
        for error in generated_python_regex_escape_errors(code):
            errors.append(
                {
                    **error,
                    "action_id": action.action_id,
                }
            )
        return errors

    def _validate_llm_text_action(self, action: OperatorAction) -> list[dict[str, Any]]:
        """Validate the domain-neutral generated-text action contract."""

        errors: list[dict[str, Any]] = []
        prompt = str(action.llm_prompt or "").strip()
        if not prompt:
            errors.append(
                {
                    "error": "llm_text_prompt_missing",
                    "message": "llm_text actions require a non-empty llm_prompt.",
                    "action_id": action.action_id,
                }
            )
        if len(prompt) > 8000:
            errors.append(
                {
                    "error": "llm_text_prompt_too_large",
                    "message": "llm_text llm_prompt must be 8000 characters or fewer.",
                    "action_id": action.action_id,
                    "prompt_length": len(prompt),
                }
            )
        if str(action.effect_intent or "").strip() != "read_only":
            errors.append(
                {
                    "error": "llm_text_effect_must_be_read_only",
                    "message": "llm_text actions must declare effect_intent read_only.",
                    "action_id": action.action_id,
                    "effect_intent": action.effect_intent,
                }
            )
        if action.command is not None or action.code is not None:
            errors.append(
                {
                    "error": "llm_text_executable_payload_forbidden",
                    "message": "llm_text actions must not include command or code.",
                    "action_id": action.action_id,
                }
            )
        if action.stdin_mode != "none" or action.stdin_text is not None or action.stdin_input_name is not None:
            errors.append(
                {
                    "error": "llm_text_stdin_forbidden",
                    "message": "llm_text actions must not include shell stdin fields.",
                    "action_id": action.action_id,
                }
            )
        if action.execution_mode != "captured" or action.interaction_mode != "non_interactive":
            errors.append(
                {
                    "error": "llm_text_execution_mode_invalid",
                    "message": "llm_text actions support only captured non_interactive execution.",
                    "action_id": action.action_id,
                    "execution_mode": action.execution_mode,
                    "interaction_mode": action.interaction_mode,
                }
            )
        return errors

    def validate(self, plan: OperatorPlan) -> list[dict[str, Any]]:
        """Return validation errors for one operator plan."""

        plan, _ = normalize_operator_plan_interactions(
            plan,
            policy_mode=operator_policy_mode(self.config, "interaction"),
        )
        errors: list[dict[str, Any]] = []
        shell_binding_mode = _shell_input_bindings_mode_from_value(
            getattr(self.config, "shell_input_bindings_mode", "allow")
        )
        if not self.config.llm_operator_enabled:
            errors.append(
                {
                    "error": "llm_operator_disabled",
                    "message": "Conversational mode is disabled by runtime configuration.",
                }
            )
        if not plan.actions:
            errors.append({"error": "missing_actions", "message": "Operator plans require at least one action."})
        if len(plan.actions) > self.config.llm_operator_max_actions:
            errors.append(
                {
                    "error": "too_many_actions",
                    "message": f"Operator plans may contain at most {self.config.llm_operator_max_actions} actions.",
                    "actual": len(plan.actions),
                }
            )

        task_ids = [task.task_id for task in plan.tasks]
        action_ids = [action.action_id for action in plan.actions]
        if len(set(task_ids)) != len(task_ids):
            errors.append({"error": "duplicate_task_id", "message": "Operator task ids must be unique."})
        if len(set(action_ids)) != len(action_ids):
            errors.append({"error": "duplicate_action_id", "message": "Operator action ids must be unique."})

        task_id_set = set(task_ids)
        action_id_set = set(action_ids)
        detached_action_ids = {
            action.action_id
            for action in plan.actions
            if getattr(action, "execution_mode", "captured") == "terminal_detached"
        }
        edges: list[tuple[str, str]] = []
        for action in plan.actions:
            if action.task_id not in task_id_set:
                errors.append(
                    {
                        "error": "unknown_task_reference",
                        "message": "Operator action references an unknown task_id.",
                        "action_id": action.action_id,
                        "task_id": action.task_id,
                    }
                )
            _, cwd_error = self._workspace_path(action.cwd)
            if cwd_error:
                errors.append(
                    {
                        "error": "cwd_outside_workspace",
                        "message": cwd_error,
                        "action_id": action.action_id,
                    }
                )
            timeout = action.timeout_seconds
            if timeout is not None and timeout <= 0:
                errors.append(
                    {
                        "error": "timeout_out_of_bounds",
                        "message": "timeout_seconds must be greater than zero when provided.",
                        "action_id": action.action_id,
                    }
                )
            if action.execution_mode == "terminal_detached":
                if action.kind != "shell_command":
                    errors.append(
                        {
                            "error": "terminal_detached_kind_invalid",
                            "message": "terminal_detached execution is valid only for shell_command actions.",
                            "action_id": action.action_id,
                        }
                    )
                if self.terminal_cwd is None or not self.config.terminal_execution_enabled:
                    errors.append(
                        {
                            "error": "terminal_detached_requires_terminal",
                            "message": (
                                "terminal_detached execution requires Run in Terminal with an active "
                                "trusted Agent UI terminal session."
                            ),
                        "action_id": action.action_id,
                    }
                )
            if action.interaction_mode in {"may_prompt", "long_running"} and action.kind != "shell_command":
                errors.append(
                    {
                        "error": "interactive_action_kind_invalid",
                        "message": (
                            "may_prompt and long_running interaction modes are valid only for "
                            "shell_command actions. Interactive terminal work must not be hidden "
                            "inside Python subprocess capture."
                        ),
                        "action_id": action.action_id,
                        "interaction_mode": action.interaction_mode,
                    }
                )
            if action.interaction_mode == "long_running" and action.execution_mode != "terminal_detached":
                errors.append(
                    {
                        "error": "long_running_requires_terminal_detached",
                        "message": (
                            "long_running actions must use execution_mode terminal_detached so "
                            "the terminal owns the running process."
                        ),
                        "action_id": action.action_id,
                    }
                )
            if (
                action.kind == "shell_command"
                and action.input_bindings
                and shell_binding_mode == "off"
            ):
                errors.append(
                    {
                        "error": "shell_input_bindings_unsupported",
                        "message": (
                            "shell_command actions cannot consume input_bindings. "
                            "Compute discovered values inside one concrete shell command, "
                            "or use a python_action/python_transform action to consume upstream outputs."
                        ),
                        "action_id": action.action_id,
                    }
                )
            for dependency in action.depends_on:
                if dependency not in action_id_set:
                    errors.append(
                        {
                            "error": "unknown_action_dependency",
                            "message": "Operator action depends on an unknown action id.",
                            "action_id": action.action_id,
                            "dependency": dependency,
                        }
                    )
                elif dependency in detached_action_ids:
                    errors.append(
                        {
                            "error": "terminal_detached_dependency_output",
                            "message": (
                                "terminal_detached actions do not produce dataflow output for downstream actions."
                            ),
                            "action_id": action.action_id,
                            "dependency": dependency,
                        }
                    )
                else:
                    edges.append((dependency, action.action_id))
            seen_input_names: set[str] = set()
            for binding in action.input_bindings:
                input_name = str(binding.input_name or "").strip()
                if not input_name:
                    errors.append(
                        {
                            "error": "empty_input_binding_name",
                            "message": "Operator input binding input_name must be non-empty.",
                            "action_id": action.action_id,
                        }
                    )
                if input_name in seen_input_names:
                    errors.append(
                        {
                            "error": "duplicate_input_binding_name",
                            "message": "Operator input binding names must be unique within an action.",
                            "action_id": action.action_id,
                            "input_name": input_name,
                        }
                    )
                seen_input_names.add(input_name)
                if input_name in action.inputs:
                    errors.append(
                        {
                            "error": "input_binding_overwrites_literal",
                            "message": "Input bindings must not overwrite literal action inputs.",
                            "action_id": action.action_id,
                            "input_name": input_name,
                        }
                    )
                if binding.source_action_id not in action_id_set:
                    errors.append(
                        {
                            "error": "unknown_input_binding_source",
                            "message": "Operator input binding references an unknown source_action_id.",
                            "action_id": action.action_id,
                            "source_action_id": binding.source_action_id,
                        }
                    )
                elif binding.source_action_id == action.action_id:
                    errors.append(
                        {
                            "error": "self_input_binding",
                            "message": "Operator actions cannot bind inputs from themselves.",
                            "action_id": action.action_id,
                            "source_action_id": binding.source_action_id,
                        }
                    )
                elif binding.source_action_id in detached_action_ids:
                    errors.append(
                        {
                            "error": "terminal_detached_binding_source",
                            "message": (
                                "terminal_detached actions stream to the terminal and cannot be used as input_binding sources."
                            ),
                            "action_id": action.action_id,
                            "source_action_id": binding.source_action_id,
                        }
                    )
                else:
                    edges.append((binding.source_action_id, action.action_id))
            if action.defer_code_generation:
                if action.kind not in {"python_action", "python_transform"}:
                    errors.append(
                        {
                            "error": "deferred_code_kind_invalid",
                            "message": "defer_code_generation is valid only for Python actions.",
                            "action_id": action.action_id,
                        }
                    )
                if str(action.code or "").strip():
                    errors.append(
                        {
                            "error": "deferred_code_already_present",
                            "message": "Deferred Python actions must set code to null or omit it.",
                            "action_id": action.action_id,
                        }
                    )
                if not action.input_bindings:
                    errors.append(
                        {
                            "error": "deferred_code_missing_inputs",
                            "message": "Deferred Python actions must declare input_bindings.",
                            "action_id": action.action_id,
                        }
                    )
            elif action.kind == "shell_command":
                errors.extend(self._validate_shell_action(action))
            elif action.kind == "python_transform":
                errors.extend(self._validate_python_action(action))
            elif action.kind == "python_action":
                errors.extend(self._validate_python_program_action(action))
            elif action.kind == "llm_text":
                errors.extend(self._validate_llm_text_action(action))

        for dependency in plan.dependencies:
            if dependency.producer_action_id not in action_id_set:
                errors.append(
                    {
                        "error": "unknown_dependency_producer",
                        "message": "Operator dependency producer_action_id is unknown.",
                        "producer_action_id": dependency.producer_action_id,
                    }
                )
                continue
            if dependency.consumer_action_id not in action_id_set:
                errors.append(
                    {
                        "error": "unknown_dependency_consumer",
                        "message": "Operator dependency consumer_action_id is unknown.",
                        "consumer_action_id": dependency.consumer_action_id,
                    }
                )
                continue
            if dependency.producer_action_id in detached_action_ids:
                errors.append(
                    {
                        "error": "terminal_detached_dependency_output",
                        "message": (
                            "terminal_detached actions do not produce dataflow output for downstream actions."
                        ),
                        "producer_action_id": dependency.producer_action_id,
                        "consumer_action_id": dependency.consumer_action_id,
                    }
                )
                continue
            edges.append((dependency.producer_action_id, dependency.consumer_action_id))
        if action_id_set and self._has_cycle(action_id_set, edges):
            errors.append({"error": "cycle_detected", "message": "Operator action graph must be acyclic."})
        return errors

    def resolved_cwd(self, action: OperatorAction) -> Path:
        """Return a validated workspace-bounded cwd for one action."""

        resolved, error = self._workspace_path(action.cwd)
        if error or resolved is None:
            raise OperatorValidationError(
                [{"error": "cwd_outside_workspace", "message": error or "Invalid cwd."}]
            )
        return resolved


def operator_shell_command_looks_mutating(command: str, *, policy_mode: str | None = None) -> bool:
    """Return whether shell text appears to perform a state-changing operation."""

    return classify_shell_effect(str(command or ""), policy_mode=policy_mode).mutates_state

__all__ = [name for name in globals() if not name.startswith("__")]
