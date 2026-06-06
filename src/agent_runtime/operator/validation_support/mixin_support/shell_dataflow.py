"""Shell dataflow and literal payload helpers for operator plan validation."""

from __future__ import annotations

from agent_runtime.operator.validation_support.mixin_support.common import *
from agent_runtime.operator.validation_support.mixin_support.common import _ValidationConstantsMixin


class _ValidationShellDataflowMixin(_ValidationConstantsMixin):
    @staticmethod
    def _input_name_for_shell_env_placeholder(placeholder: str) -> str:
        variable = _shell_variable_name(placeholder) or ""
        if variable.startswith("OF_INPUT_"):
            return variable[len("OF_INPUT_") :]
        return variable

    @classmethod
    def _shell_input_name_looks_computed(cls, input_name: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(input_name or "").strip().lower()).strip("_")
        if not normalized:
            return False
        if cls._shell_input_name_is_literal_only(normalized):
            return False
        if normalized in cls._STREAMING_PRIOR_SHELL_FILE_INPUT_NAMES:
            return False
        if normalized in cls._STREAMING_PRIOR_SHELL_CONTENT_INPUT_NAMES:
            return True
        parts = set(normalized.split("_"))
        return bool(parts & cls._STREAMING_PRIOR_SHELL_CONTENT_INPUT_NAMES)

    @classmethod
    def _shell_input_name_is_literal_only(cls, input_name: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(input_name or "").strip().lower()).strip("_")
        return normalized in cls._SHELL_LITERAL_ONLY_INPUT_NAMES

    @classmethod
    def _shell_input_name_looks_dataflow(
        cls,
        input_name: str,
        *,
        allow_path_like: bool = False,
    ) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(input_name or "").strip().lower()).strip("_")
        if not normalized:
            return False
        if cls._shell_input_name_is_literal_only(normalized):
            return False
        if cls._shell_input_name_looks_computed(normalized):
            return True
        parts = set(normalized.split("_"))
        if parts & cls._STREAMING_PRIOR_SHELL_CONTENT_INPUT_NAMES:
            return True
        if allow_path_like and (
            normalized in cls._STREAMING_PRIOR_SHELL_FILE_INPUT_NAMES
            or bool(parts & cls._STREAMING_PRIOR_SHELL_FILE_INPUT_NAMES)
        ):
            return True
        return False

    @classmethod
    def _shell_prior_binding_target(
        cls,
        user_request: UserRequest,
        action: OperatorAction,
        candidates: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any], str] | None:
        if action.kind != "shell_command":
            return None
        if not candidates:
            return None
        existing_binding_names = {
            str(binding.input_name or "").strip()
            for binding in action.input_bindings
            if str(binding.input_name or "").strip()
        }
        existing_binding_env_names: set[str] = set()
        for binding_name in existing_binding_names:
            try:
                existing_binding_env_names.add(_shell_binding_env_name(binding_name))
            except ValueError:
                continue
        command = str(action.command or "")
        action_inputs = dict(action.inputs or {})
        for input_name, value in action_inputs.items():
            input_name_text = str(input_name or "").strip()
            if input_name_text in existing_binding_names:
                continue
            if cls._shell_input_name_is_literal_only(input_name_text):
                continue
            input_text = _shell_input_value_to_text(value)
            if input_text is None:
                continue
            try:
                env_name = _shell_binding_env_name(input_name_text)
            except ValueError:
                continue
            if not _shell_command_references_env(command, env_name):
                continue
            for candidate in candidates:
                if cls._looks_like_streaming_prior_literal(input_text, str(candidate.get("value") or "")):
                    if cls._value_is_multiline_payload(candidate.get("value")):
                        continue
                    return input_name_text, candidate, "literal_prior_output"

        prior_context = cls._streaming_task_can_consume_prior_output(user_request)
        if not prior_context:
            return None
        generated_text_context = cls._streaming_task_can_consume_generated_text(user_request)
        generated_text_candidates = [
            item for item in candidates if str(item.get("kind") or "") == "llm_text"
        ]
        stdin_mode = str(getattr(action, "stdin_mode", "none") or "none")
        stdin_input_name = str(getattr(action, "stdin_input_name", "") or "").strip()
        if stdin_mode == "input_binding" and stdin_input_name:
            if stdin_input_name not in existing_binding_names:
                if (
                    cls._shell_input_name_looks_dataflow(stdin_input_name, allow_path_like=prior_context)
                    or (
                        generated_text_context
                        and cls._input_name_looks_generated_text(stdin_input_name)
                    )
                ):
                    candidate_pool = (
                        generated_text_candidates
                        if (
                            generated_text_candidates
                            and cls._input_name_looks_generated_text(stdin_input_name)
                        )
                        else candidates
                    )
                    candidate = cls._best_streaming_prior_candidate(
                        user_request,
                        action,
                        candidate_pool,
                        preferred_field="stdout",
                    )
                    if candidate is not None:
                        return stdin_input_name, candidate, "missing_prior_stdin_binding"
        placeholders = [
            placeholder
            for placeholder in unresolved_shell_placeholders(command)
            if str(_shell_variable_name(placeholder) or "").startswith("OF_INPUT_")
        ]
        literal_env_names: set[str] = set()
        for input_name in action_inputs:
            try:
                literal_env_names.add(_shell_binding_env_name(str(input_name)))
            except ValueError:
                continue
        for placeholder in placeholders:
            variable_name = str(_shell_variable_name(placeholder) or "")
            if variable_name in literal_env_names:
                continue
            input_name = cls._input_name_for_shell_env_placeholder(placeholder)
            if input_name in existing_binding_names or variable_name in existing_binding_env_names:
                continue
            generated_text_input = cls._input_name_looks_generated_text(input_name)
            if cls._shell_input_name_is_literal_only(input_name) and not (
                generated_text_context and generated_text_input and generated_text_candidates
            ):
                continue
            if not (
                cls._shell_input_name_looks_dataflow(input_name, allow_path_like=prior_context)
                or (generated_text_context and generated_text_input)
            ):
                continue
            candidate_pool = (
                generated_text_candidates
                if generated_text_candidates and generated_text_input
                else candidates
            )
            candidate = cls._best_streaming_prior_candidate(
                user_request,
                action,
                candidate_pool,
                preferred_field="stdout",
            )
            if candidate is not None:
                if cls._value_is_multiline_payload(candidate.get("value")):
                    continue
                return input_name, candidate, "missing_prior_output_binding"
        return None

    @staticmethod
    def _plan_dependency_sources(plan: OperatorPlan) -> dict[str, set[str]]:
        dependency_sources: dict[str, set[str]] = {}
        for dependency in plan.dependencies:
            dependency_sources.setdefault(str(dependency.consumer_action_id), set()).add(
                str(dependency.producer_action_id)
            )
        for action in plan.actions:
            dependency_sources.setdefault(str(action.action_id), set()).update(
                str(item) for item in action.depends_on or []
            )
        return dependency_sources

    @classmethod
    def _action_text_indicates_prior_data_consumer(
        cls,
        action: OperatorAction,
        task: OperatorTask | None,
    ) -> bool:
        text = " ".join(
            str(item or "")
            for item in (
                getattr(task, "goal", ""),
                getattr(task, "semantic_verb", ""),
                getattr(task, "object_type", ""),
                getattr(task, "reason", ""),
                action.reason,
                action.effect_summary,
                action.declared_output_shape,
            )
        ).lower()
        return bool(
            re.search(
                r"\b(previous|prior|earlier|upstream|producer|stdout|output|"
                r"from\s+(?:the\s+)?(?:previous|prior|earlier|upstream)|"
                r"calculated|computed|generated|found|discovered|selected|"
                r"extracted|listed|result|results)\b",
                text,
            )
        )

    @classmethod
    def _normalize_same_plan_shell_dataflow_bindings(
        cls,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
    ) -> None:
        """Add same-plan shell bindings when a single producer is unambiguous."""

        task_by_id = {task.task_id: task for task in plan.tasks}
        dependency_sources = cls._plan_dependency_sources(plan)
        changes: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            producers = sorted(
                producer
                for producer in dependency_sources.get(str(action.action_id), set())
                if producer and producer != action.action_id
            )
            if len(producers) != 1:
                continue
            task = task_by_id.get(action.task_id)
            prior_context = cls._action_text_indicates_prior_data_consumer(action, task)
            if not prior_context:
                continue
            existing_binding_names = {
                str(binding.input_name or "").strip()
                for binding in action.input_bindings
                if str(binding.input_name or "").strip()
            }
            existing_binding_env_names: set[str] = set()
            for binding_name in existing_binding_names:
                try:
                    existing_binding_env_names.add(_shell_binding_env_name(binding_name))
                except ValueError:
                    continue
            literal_env_names: set[str] = set()
            for input_name in dict(action.inputs or {}):
                try:
                    literal_env_names.add(_shell_binding_env_name(str(input_name)))
                except ValueError:
                    continue
            command = str(action.command or "")
            for placeholder in unresolved_shell_placeholders(command):
                variable_name = str(_shell_variable_name(placeholder) or "")
                if not variable_name.startswith("OF_INPUT_"):
                    continue
                if variable_name in literal_env_names:
                    continue
                input_name = cls._input_name_for_shell_env_placeholder(placeholder)
                if input_name in existing_binding_names or variable_name in existing_binding_env_names:
                    continue
                if not cls._shell_input_name_looks_dataflow(input_name, allow_path_like=prior_context):
                    continue
                action.input_bindings = [
                    *list(action.input_bindings or []),
                    OperatorInputBinding(
                        input_name=input_name,
                        source_action_id=producers[0],
                        source_field="stdout",
                        required=True,
                        fallback_value=None,
                    ),
                ]
                existing_binding_names.add(input_name)
                existing_binding_env_names.add(variable_name)
                if producers[0] not in set(action.depends_on or []):
                    action.depends_on.append(producers[0])
                changes.append(
                    {
                        "action_id": action.action_id,
                        "task_id": action.task_id,
                        "input_name": input_name,
                        "source_action_id": producers[0],
                        "source_field": "stdout",
                        "normalization": "same_plan_missing_dataflow_binding",
                    }
                )

        if changes:
            cls._emit(
                observability,
                level="info",
                event_type="operator.same_plan_shell_input_bound",
                title="Same-plan shell dataflow bound",
                summary=(
                    "The runtime added missing shell input_bindings for unambiguous "
                    "downstream dataflow."
                ),
                details={"actions": changes},
            )

    def _normalize_streaming_prior_shell_inputs(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
    ) -> None:
        """Replace copied prior streaming values in shell inputs with real bindings."""

        candidates = self._streaming_prior_output_candidates(user_request)
        if not candidates:
            return

        changes: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind == "shell_command" and action.input_bindings:
                rewritten_bindings: list[OperatorInputBinding] = []
                bindings_changed = False
                for binding in action.input_bindings:
                    original_source_action_id = str(binding.source_action_id or "").strip()
                    candidate = self._streaming_prior_candidate_for_binding(
                        user_request,
                        action=action,
                        binding=binding,
                        candidates=candidates,
                    )
                    if candidate is None:
                        rewritten_bindings.append(binding)
                        continue
                    source_action_id = self._streaming_prior_binding_source_action_id(
                        user_request,
                        action=action,
                        candidate=candidate,
                        preferred_source_action_id=original_source_action_id,
                    )
                    source_field = str(candidate.get("source_field") or binding.source_field or "stdout")
                    if (
                        source_action_id != original_source_action_id
                        or source_field != str(binding.source_field or "stdout")
                    ):
                        rewritten_bindings.append(
                            binding.model_copy(
                                update={
                                    "source_action_id": source_action_id,
                                    "source_field": source_field,
                                }
                            )
                        )
                        self._rewrite_streaming_prior_dependency_references(
                            plan,
                            action=action,
                            original_source_action_id=original_source_action_id,
                            alias_action_id=source_action_id,
                        )
                        bindings_changed = True
                        changes.append(
                            {
                                "action_id": action.action_id,
                                "task_id": action.task_id,
                                "input_name": binding.input_name,
                                "source_action_id": source_action_id,
                                "source_field": source_field,
                                "original_source_action_id": original_source_action_id,
                                "normalization": "streaming_prior_shell_binding_alias",
                            }
                        )
                    else:
                        rewritten_bindings.append(binding)
                if bindings_changed:
                    action.input_bindings = rewritten_bindings

            target = self._shell_prior_binding_target(user_request, action, candidates)
            if target is None:
                continue
            input_name, candidate, target_kind = target
            cleaned_inputs = dict(action.inputs or {})
            literal_preview = ""
            if input_name in cleaned_inputs:
                literal_preview = str(cleaned_inputs.pop(input_name) or "")[:200]
            action.inputs = cleaned_inputs
            action.input_bindings = [
                *list(action.input_bindings or []),
                OperatorInputBinding(
                    input_name=input_name,
                    source_action_id=self._streaming_prior_binding_source_action_id(
                        user_request,
                        action=action,
                        candidate=candidate,
                    ),
                    source_field=candidate["source_field"],  # type: ignore[arg-type]
                    required=True,
                    fallback_value=None,
                ),
            ]
            source_action_id = str(action.input_bindings[-1].source_action_id)
            if source_action_id not in set(action.depends_on or []):
                action.depends_on.append(source_action_id)
            changes.append(
                {
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "input_name": input_name,
                    "source_action_id": source_action_id,
                    "source_field": candidate["source_field"],
                    "removed_literal_preview": literal_preview,
                    "normalization": target_kind,
                }
            )

        if changes:
            self._emit(
                observability,
                level="info",
                event_type="operator.streaming_prior_shell_input_bound",
                title="Streaming prior shell output bound",
                summary=(
                    "The runtime replaced copied prior streaming values in shell actions "
                    "with input_bindings."
                ),
                details={"actions": changes},
            )

    def _prior_output_literal_dataflow_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject later consumer actions that hard-code a value produced by an earlier step."""

        candidates = self._streaming_prior_output_candidates(user_request)
        if not candidates:
            return []
        binding_mode = _shell_input_bindings_mode_from_value(
            getattr(self.config, "shell_input_bindings_mode", "allow")
        )
        if binding_mode == "off" and not self._streaming_task_can_persist_prior_output(user_request):
            return []
        prompt_text = str(user_request.raw_prompt or "")
        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            command = str(action.command or "")
            code = str(action.code or "")
            for candidate in candidates:
                value = str(candidate.get("value") or "").strip()
                if not value:
                    continue
                candidate_is_bound = self._action_has_streaming_prior_candidate_binding(
                    action,
                    candidate,
                )
                value_fragments: list[tuple[str, bool]] = [(value, False)]
                if "\n" in value and not candidate_is_bound:
                    value_fragments.extend(
                        (line.strip(), True) for line in value.splitlines() if line.strip()
                    )
                for fragment, from_multiline_line in value_fragments:
                    if not fragment or len(fragment) < 4:
                        continue
                    if (
                        from_multiline_line
                        and self._streaming_prior_line_fragment_is_ambiguous(fragment)
                    ):
                        continue
                    if fragment in prompt_text:
                        continue
                    if action.kind == "shell_command" and fragment in command:
                        errors.append(
                            {
                                "error": "prior_output_literal_not_bound",
                                "message": (
                                    "This action hard-codes a value produced by an earlier step. "
                                    "Computed runtime values must flow through input_bindings and "
                                    "OF_INPUT_* variables."
                                ),
                                "action_id": action.action_id,
                                "source_action_id": candidate.get("action_id"),
                                "source_field": candidate.get("source_field"),
                                "literal_preview": fragment[:200],
                                "repair_hint": (
                                    "Bind the producer output with input_bindings and consume the "
                                    "matching OF_INPUT_* variable. Keep only user constants such as "
                                    "file names in literal inputs."
                                ),
                            }
                        )
                        break
                    if action.kind in {"python_action", "python_transform"} and fragment in code:
                        errors.append(
                            {
                                "error": "prior_output_literal_not_bound",
                                "message": (
                                    "This Python action hard-codes a value produced by an earlier step. "
                                    "Computed runtime values must flow through input_bindings."
                                ),
                                "action_id": action.action_id,
                                "source_action_id": candidate.get("action_id"),
                                "source_field": candidate.get("source_field"),
                                "literal_preview": fragment[:200],
                                "repair_hint": (
                                    "Declare an input_binding from the producer action and read that "
                                    "input inside Python."
                                ),
                            }
                        )
                        break
        return errors

    @classmethod
    def _action_has_streaming_prior_candidate_binding(
        cls,
        action: OperatorAction,
        candidate: dict[str, Any],
    ) -> bool:
        """Return whether an action already binds the candidate's prior output."""

        aliases = cls._streaming_prior_source_aliases(candidate)
        source_field = str(candidate.get("source_field") or "stdout").strip() or "stdout"
        for binding in list(action.input_bindings or []):
            if str(binding.source_action_id or "").strip() not in aliases:
                continue
            binding_field = str(binding.source_field or "stdout").strip() or "stdout"
            if binding_field == source_field:
                return True
        return False

    @staticmethod
    def _streaming_prior_line_fragment_is_ambiguous(fragment: str) -> bool:
        """Skip tiny bare-token lines that collide with normal code identifiers."""

        text = str(fragment or "").strip()
        return len(text) < 8 and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", text))

    def _streaming_shell_multiline_dataflow_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject shell env binding for known multiline upstream values."""

        candidates = self._streaming_prior_output_candidates(user_request)
        if not candidates:
            return []
        candidates_by_key = {
            (str(candidate.get("action_id") or ""), str(candidate.get("source_field") or "")): candidate
            for candidate in candidates
        }
        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            command = str(action.command or "")
            for binding in action.input_bindings:
                if binding.source_field == "stderr":
                    continue
                candidate = candidates_by_key.get(
                    (str(binding.source_action_id or ""), str(binding.source_field or "stdout"))
                )
                if candidate is None or not self._value_is_multiline_payload(candidate.get("value")):
                    continue
                try:
                    env_name = _shell_binding_env_name(binding.input_name)
                except ValueError:
                    continue
                if not _shell_command_references_env(command, env_name):
                    continue
                errors.append(
                    {
                        "error": "downstream_dataflow_requires_transform",
                        "message": (
                            "This shell action tries to consume multiline upstream output through "
                            "an OF_INPUT_* environment variable."
                        ),
                        "action_id": action.action_id,
                        "input_name": binding.input_name,
                        "source_action_id": binding.source_action_id,
                        "source_field": binding.source_field,
                        "repair_hint": (
                            "Add a Python transform to extract a short single-line value, or use "
                            "stdin_mode=input_binding when the shell command is meant to read the "
                            "whole multiline payload."
                        ),
                    }
                )
            if action.input_bindings:
                continue
            if not self._streaming_task_can_consume_prior_output(user_request):
                continue
            for placeholder in unresolved_shell_placeholders(command):
                variable_name = str(_shell_variable_name(placeholder) or "")
                if not variable_name.startswith("OF_INPUT_"):
                    continue
                input_name = self._input_name_for_shell_env_placeholder(placeholder)
                if not self._shell_input_name_looks_dataflow(input_name, allow_path_like=True):
                    continue
                candidate = self._best_streaming_prior_candidate(
                    user_request,
                    action,
                    candidates,
                    preferred_field="stdout",
                )
                if candidate is None or not self._value_is_multiline_payload(candidate.get("value")):
                    continue
                errors.append(
                    {
                        "error": "downstream_dataflow_requires_transform",
                        "message": (
                            "This shell action needs a prior multiline output, which cannot be "
                            "safely injected as an OF_INPUT_* environment variable."
                        ),
                        "action_id": action.action_id,
                        "input_name": input_name,
                        "source_action_id": candidate.get("action_id"),
                        "source_field": candidate.get("source_field"),
                        "repair_hint": (
                            "Add a Python transform to extract a short single-line value, or use "
                            "stdin_mode=input_binding when the shell command is meant to read the "
                            "whole multiline payload."
                        ),
                    }
                )
        return errors

    @classmethod
    def _shell_literal_looks_runtime_value(cls, value: str, prompt_text: str) -> bool:
        text = str(value or "").strip().strip("\"'")
        if len(text) < 4:
            return False
        if text in prompt_text:
            return False
        lowered = text.lower()
        if lowered in cls._SAME_PLAN_RUNTIME_LITERAL_SKIP_WORDS:
            return False
        if text.startswith("$") or "OF_INPUT_" in text or any(char in text for char in "{};$|`\\"):
            return False
        if re.fullmatch(r"[0-9a-fA-F]{7,64}", text):
            return True
        if re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            text,
        ):
            return True
        if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)", text, re.IGNORECASE):
            return True
        if "/" in text and not text.startswith("-") and len(text) >= 5:
            return True
        if re.fullmatch(r"https?://[^\s]+", text) or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
            return True
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*\d[A-Za-z0-9_.:-]*", text) and len(text) >= 6:
            return True
        return False

    @classmethod
    def _shell_command_runtime_literal_fragments(
        cls,
        command: str,
        prompt_text: str,
    ) -> list[str]:
        fragments: list[str] = []
        seen: set[str] = set()
        token_pattern = r"(?<![$\w])([A-Za-z0-9][A-Za-z0-9_.:/@+-]{3,})(?![\w])"
        for token in re.findall(token_pattern, str(command or "")):
            cleaned = str(token or "").strip().strip("\"'.,;()[]")
            if cleaned in seen:
                continue
            if cls._shell_literal_looks_runtime_value(cleaned, prompt_text):
                seen.add(cleaned)
                fragments.append(cleaned)
        return fragments

    def _same_plan_ambiguous_missing_dataflow_binding_errors(
        self,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        task_by_id = {task.task_id: task for task in plan.tasks}
        dependency_sources = self._plan_dependency_sources(plan)
        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            producers = sorted(
                producer
                for producer in dependency_sources.get(str(action.action_id), set())
                if producer and producer != action.action_id
            )
            if len(producers) < 2:
                continue
            task = task_by_id.get(action.task_id)
            prior_context = self._action_text_indicates_prior_data_consumer(action, task)
            if not prior_context:
                continue
            bound_names = {
                str(binding.input_name or "").strip()
                for binding in action.input_bindings
                if str(binding.input_name or "").strip()
            }
            bound_env_names: set[str] = set()
            for binding_name in bound_names:
                try:
                    bound_env_names.add(_shell_binding_env_name(binding_name))
                except ValueError:
                    continue
            literal_env_names: set[str] = set()
            for input_name in dict(action.inputs or {}):
                try:
                    literal_env_names.add(_shell_binding_env_name(str(input_name)))
                except ValueError:
                    continue
            for placeholder in unresolved_shell_placeholders(str(action.command or "")):
                variable_name = str(_shell_variable_name(placeholder) or "")
                if not variable_name.startswith("OF_INPUT_"):
                    continue
                if variable_name in literal_env_names:
                    continue
                input_name = self._input_name_for_shell_env_placeholder(placeholder)
                if input_name in bound_names or variable_name in bound_env_names:
                    continue
                if not self._shell_input_name_looks_dataflow(input_name, allow_path_like=prior_context):
                    continue
                errors.append(
                    {
                        "error": "ambiguous_dataflow_producer",
                        "message": (
                            "This downstream shell action references a computed OF_INPUT_* value "
                            "but multiple upstream producers are possible."
                        ),
                        "action_id": action.action_id,
                        "input_name": input_name,
                        "producer_action_ids": producers,
                        "repair_hint": (
                            "Declare an explicit input_binding from the correct producer action "
                            "and consume the matching OF_INPUT_* variable."
                        ),
                    }
                )
        return errors

    def _same_plan_command_literal_dataflow_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        prompt_text = str(user_request.raw_prompt or "")
        task_by_id = {task.task_id: task for task in plan.tasks}
        dependency_sources = self._plan_dependency_sources(plan)
        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command" or action.input_bindings:
                continue
            producers = sorted(
                producer
                for producer in dependency_sources.get(str(action.action_id), set())
                if producer and producer != action.action_id
            )
            if not producers:
                continue
            task = task_by_id.get(action.task_id)
            if not self._action_text_indicates_prior_data_consumer(action, task):
                continue
            for fragment in self._shell_command_runtime_literal_fragments(str(action.command or ""), prompt_text):
                errors.append(
                    {
                        "error": "computed_value_literal_not_bound",
                        "message": (
                            "This downstream action appears to hard-code a discovered or computed "
                            "runtime value. Values produced by earlier actions must flow through "
                            "input_bindings."
                        ),
                        "action_id": action.action_id,
                        "producer_action_ids": producers,
                        "literal_preview": fragment[:200],
                        "repair_hint": (
                            "Bind the producer output with input_bindings and consume the matching "
                            "OF_INPUT_* variable. Keep only user-requested constants as literals."
                        ),
                    }
                )
        return errors

    def _same_plan_computed_literal_dataflow_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject literal computed-looking inputs on same-plan downstream consumers."""

        prompt_text = str(user_request.raw_prompt or "")
        task_by_id = {task.task_id: task for task in plan.tasks}
        dependency_sources = self._plan_dependency_sources(plan)
        protected_literal_values = self._protected_literal_payload_values_by_input(user_request)

        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            producers = {
                producer
                for producer in dependency_sources.get(str(action.action_id), set())
                if producer and producer != action.action_id
            }
            if not producers:
                continue
            task = task_by_id.get(action.task_id)
            prior_context = self._action_text_indicates_prior_data_consumer(action, task)
            if not prior_context:
                continue
            bound_names = {
                str(binding.input_name or "").strip()
                for binding in action.input_bindings
                if str(binding.input_name or "").strip()
            }
            command = str(action.command or "")
            for input_name, value in dict(action.inputs or {}).items():
                input_name_text = str(input_name or "").strip()
                if input_name_text in bound_names:
                    continue
                if not self._shell_input_name_looks_dataflow(
                    input_name_text,
                    allow_path_like=prior_context,
                ):
                    continue
                value_text = _shell_input_value_to_text(value)
                if not value_text:
                    continue
                if self._matches_protected_literal_payload(
                    input_name_text,
                    value_text,
                    protected_literal_values,
                ):
                    continue
                if value_text.strip() and value_text.strip() in prompt_text:
                    continue
                try:
                    env_name = _shell_binding_env_name(input_name_text)
                except ValueError:
                    continue
                if not _shell_command_references_env(command, env_name):
                    continue
                errors.append(
                    {
                        "error": "computed_output_literal_not_bound",
                        "message": (
                            "This downstream action uses a literal for a computed-looking value. "
                            "Values produced by earlier actions must flow through input_bindings."
                        ),
                        "action_id": action.action_id,
                        "input_name": input_name_text,
                        "producer_action_ids": sorted(producers),
                        "literal_preview": value_text[:200],
                        "repair_hint": (
                            "Replace this literal input with an input_binding from the producer "
                            "action's stdout/output, and consume the same OF_INPUT_* variable. "
                            "Keep user-requested constants such as file names as literal inputs."
                        ),
                    }
                )
        return errors

    @staticmethod
    def _protected_literal_payload_values_by_input(
        user_request: UserRequest,
    ) -> dict[str, set[str]]:
        """Return trusted request payload values keyed by shell input name."""

        values_by_input: dict[str, set[str]] = {}
        for context in (user_request.session_context, user_request.safety_context):
            for payload in literal_payloads_from_context(context):
                input_name = str(payload.get("input_name") or "").strip()
                value = str(payload.get("value") or "")
                if not input_name or not value:
                    continue
                values_by_input.setdefault(input_name, set()).add(value)
        return values_by_input

    @staticmethod
    def _matches_protected_literal_payload(
        input_name: str,
        value: str,
        protected_literal_values: dict[str, set[str]],
    ) -> bool:
        """True when a shell input is a protected request payload, not prior output."""

        candidates = protected_literal_values.get(str(input_name or "").strip())
        if not candidates:
            return False
        value_text = str(value or "")
        value_stripped = value_text.strip()
        return any(
            value_text == candidate or value_stripped == str(candidate or "").strip()
            for candidate in candidates
        )

    @staticmethod
    def _action_text_indicates_exact_unit_aggregation(
        user_request: UserRequest,
        action: OperatorAction,
        task: OperatorTask | None,
    ) -> bool:
        text = " ".join(
            str(item or "")
            for item in (
                user_request.raw_prompt,
                getattr(task, "goal", ""),
                getattr(task, "semantic_verb", ""),
                getattr(task, "object_type", ""),
                getattr(task, "reason", ""),
                action.reason,
                action.effect_summary,
                action.declared_output_shape,
            )
        ).lower()
        return bool(
            re.search(r"\b(calculate|compute|sum|total|aggregate|add|measure)\b", text)
            and re.search(r"\b(size|usage|space|memory|storage|disk|bytes?|kb|mb|gb|tb|kib|mib|gib|tib)\b", text)
        )

    def _shell_human_unit_arithmetic_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject brittle shell arithmetic over human-readable unit strings."""

        task_by_id = {task.task_id: task for task in plan.tasks}
        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            command = str(action.command or "")
            if not command.strip():
                continue
            if _SHELL_CANONICAL_UNIT_OUTPUT_RE.search(command):
                continue
            if not _SHELL_ARITHMETIC_AGGREGATION_RE.search(command):
                continue
            if not _SHELL_HUMAN_UNIT_SOURCE_RE.search(command):
                continue
            task = task_by_id.get(action.task_id)
            if not self._action_text_indicates_exact_unit_aggregation(user_request, action, task):
                continue
            errors.append(
                {
                    "error": "human_unit_shell_arithmetic",
                    "message": (
                        "This shell action appears to aggregate human-readable unit strings. "
                        "Shell arithmetic commonly treats compact units like '2.43GB' and "
                        "'119MB' as unrelated bare numbers."
                    ),
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "repair_hint": (
                        "Use python_action/python_transform with explicit unit conversion, or make "
                        "the producer command emit canonical numeric units such as bytes/nounits "
                        "before doing shell arithmetic."
                    ),
                }
            )
        return errors



__all__ = ["_ValidationShellDataflowMixin"]
