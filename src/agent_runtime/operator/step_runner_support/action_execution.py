"""Action execution helpers for the operator step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class _StepRunnerActionExecutionMixin:
    def _classify_shell_stdout_after_nonzero_exit(
        self,
        *,
        user_request: UserRequest,
        action_kind: str,
        command: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        command_cancelled: bool,
        macro_delivery_failed: bool,
    ) -> ShellStdoutClassification:
        """Return whether non-zero shell stdout is useful output or a diagnostic."""

        if not _shell_stdout_candidate(
            action_kind=action_kind,
            exit_code=exit_code,
            stdout=stdout,
            command_cancelled=command_cancelled,
            macro_delivery_failed=macro_delivery_failed,
        ):
            return ShellStdoutClassification(
                stdout_usable=bool(str(stdout or "").strip()),
                reason="not_nonzero_shell_stdout_candidate",
                confidence=1.0,
                classifier_source="not_applicable",
            )
        deterministic = classify_shell_stdout_deterministic(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
        if (
            not deterministic.stdout_usable
            or not _stdout_needs_llm_error_judge(stdout)
            or operator_policy_mode(self.config, "stdout") != "llm"
        ):
            return deterministic

        cache_key = _shell_stdout_error_judge_cache_key(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
        cache = user_request.safety_context.setdefault("shell_stdout_error_judge_cache", {})
        if isinstance(cache, dict) and isinstance(cache.get(cache_key), dict):
            cached = cache[cache_key]
            return ShellStdoutClassification(
                stdout_usable=bool(cached.get("stdout_usable")),
                reason=str(cached.get("reason") or "llm_cached"),
                confidence=float(cached.get("confidence") or 0.0),
                classifier_source="llm_cached",
            )

        try:
            payload = self.llm_client.complete_json(
                _shell_stdout_error_judge_prompt(
                    command=command,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                ),
                ShellStdoutErrorJudge.model_json_schema(),
            )
            judge = ShellStdoutErrorJudge.model_validate(payload)
        except Exception:
            classification = ShellStdoutClassification(
                stdout_usable=True,
                reason="llm_judge_unavailable_preserve_stdout",
                confidence=0.0,
                classifier_source="llm_unavailable",
            )
        else:
            if judge.confidence < 0.75:
                classification = ShellStdoutClassification(
                    stdout_usable=True,
                    reason="llm_judge_low_confidence_preserve_stdout",
                    confidence=float(judge.confidence),
                    classifier_source="llm_low_confidence",
                )
            else:
                classification = ShellStdoutClassification(
                    stdout_usable=not bool(judge.is_error),
                    reason="llm_judge_error" if judge.is_error else "llm_judge_usable_stdout",
                    confidence=float(judge.confidence),
                    classifier_source="llm",
                )
        if isinstance(cache, dict):
            cache[cache_key] = {
                "stdout_usable": classification.stdout_usable,
                "reason": classification.reason,
                "confidence": classification.confidence,
                "classifier_source": classification.classifier_source,
            }
        return classification

    def _judge_sudo_retry_after_nonzero_exit(
        self,
        *,
        user_request: UserRequest,
        action_kind: str,
        command: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        error: str = "",
        command_cancelled: bool,
        macro_delivery_failed: bool,
    ) -> SudoRetryJudge | None:
        """Return a confident LLM sudo retry judgment for a failed non-sudo shell command."""

        if (
            action_kind != "shell_command"
            or exit_code is None
            or exit_code == 0
            or command_cancelled
            or macro_delivery_failed
            or shell_command_uses_sudo(command)
        ):
            return None

        cache_key = _sudo_retry_judge_cache_key(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            error=error,
        )
        cache = user_request.safety_context.setdefault("sudo_retry_judge_cache", {})
        if isinstance(cache, dict) and isinstance(cache.get(cache_key), dict):
            try:
                judge = SudoRetryJudge.model_validate(cache[cache_key])
            except Exception:
                judge = None
        else:
            try:
                payload = self.llm_client.complete_json(
                    _sudo_retry_judge_prompt(
                        command=command,
                        exit_code=exit_code,
                        stdout=stdout,
                        stderr=stderr,
                        error=error,
                    ),
                    SudoRetryJudge.model_json_schema(),
                )
                judge = SudoRetryJudge.model_validate(payload)
            except Exception:
                judge = None
            if isinstance(cache, dict) and judge is not None:
                cache[cache_key] = judge.model_dump(mode="json")

        if (
            judge is None
            or judge.confidence < 0.75
            or not judge.is_permission_denied
            or not judge.sudo_required_or_likely_to_fix
        ):
            return None
        return judge

    def _execute_action(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        records_by_action: dict[str, OperatorExecutionRecord],
        execution_context: dict[str, Any],
        task_context: Any | None,
        observability: ObservabilityContext | None,
    ) -> OperatorExecutionRecord:
        action_inputs_for_record: dict[str, Any] = {}
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_EXECUTION_STARTED,
            title="Operator action started",
            summary="The runtime started one approved operator action.",
            details={"action": action.model_dump(mode="json")},
        )
        try:
            if action.kind == "llm_text":
                cwd = self.validator.resolved_cwd(action)
                action_inputs = self._resolve_action_inputs(action, records_by_action, observability)
                action_inputs_for_record = dict(action_inputs)
                operation_metadata = self._execution_operation_description(action, cwd=str(cwd))
                self._emit(
                    observability,
                    level="info",
                    event_type="execution.command.started",
                    title="Text generation started",
                    summary="The runtime started one generated-text action.",
                    details={
                        "action_id": action.action_id,
                        "task_id": action.task_id,
                        "label": action.reason or action.action_id,
                        "kind": action.kind,
                        "cwd": str(cwd),
                        "execution_mode": action.execution_mode,
                        "interaction_mode": action.interaction_mode,
                        "llm_prompt": _truncate(action.llm_prompt, 1200),
                        **operation_metadata,
                    },
                )
                draft = structured_call(
                    self.llm_client,
                    build_operator_llm_text_prompt(user_request, action, action_inputs),
                    OperatorTextGenerationDraft,
                )
                text = str(draft.text or "").strip()
                if not text:
                    raise RuntimeError("llm_text generated empty text.")
                if len(text) > _LLM_TEXT_OUTPUT_MAX_CHARS:
                    raise RuntimeError(
                        f"llm_text generated {len(text)} characters, exceeding "
                        f"the {_LLM_TEXT_OUTPUT_MAX_CHARS} character runtime cap."
                    )
                self._emit(
                    observability,
                    level="info",
                    event_type="execution.command.stdout",
                    title="Generated text",
                    summary="A generated-text action produced output.",
                    details={
                        "action_id": action.action_id,
                        "task_id": action.task_id,
                        "label": action.reason or action.action_id,
                        "channel": "stdout",
                        "text": text,
                        "cwd": str(cwd),
                        "kind": action.kind,
                        **operation_metadata,
                    },
                )
                self._emit(
                    observability,
                    level="info",
                    event_type="execution.command.completed",
                    title="Text generation completed",
                    summary="A generated-text action completed successfully.",
                    details={
                        "action_id": action.action_id,
                        "task_id": action.task_id,
                        "label": action.reason or action.action_id,
                        "channel": "completed",
                        "exit_code": 0,
                        "cwd": str(cwd),
                        "kind": action.kind,
                        **operation_metadata,
                    },
                )
                self._emit_tryout_output(
                    observability,
                    action=action,
                    channel="stdout",
                    text=text,
                    cwd=str(cwd),
                )
                self._emit_tryout_output(
                    observability,
                    action=action,
                    channel="completed",
                    text="",
                    exit_code=0,
                    cwd=str(cwd),
                )
                return OperatorExecutionRecord(
                    action_id=action.action_id,
                    task_id=action.task_id,
                    kind=action.kind,
                    status="success",
                    stdout=text,
                    stderr="",
                    exit_code=0,
                    output=text,
                    metadata={
                        "cwd": str(cwd),
                        "declared_output_shape": action.declared_output_shape,
                        "bound_inputs_preview": self._inputs_preview(action_inputs_for_record),
                        "llm_text": True,
                        "llm_text_confidence": draft.confidence,
                        "llm_prompt": _truncate(action.llm_prompt, 1200),
                        **operation_metadata,
                    },
                )
            if action.kind in {"shell_command", "python_action"}:
                cwd = self.validator.resolved_cwd(action)
                stdout_parts: list[str] = []
                stderr_parts: list[str] = []
                exit_code = 1
                command_cancelled = False
                macro_delivery_failed = False
                macro_delivery_error: str | None = None
                repeated_terminal_prompt_after_macro = False
                terminal_macro_delivery_keys: set[str] = set()
                gateway_node = None
                gateway_url = None
                gateway_metadata = merge_gateway_metadata(execution_context)
                action_inputs: dict[str, Any] = {}
                shell_literal_env: dict[str, str] = {}
                shell_bound_env: dict[str, str] = {}
                shell_context_env: dict[str, str] = {}
                shell_env: dict[str, str] = {}
                shell_stdin: str | None = None
                shell_stdin_metadata: dict[str, Any] = {"stdin_mode": "none"}
                deferred_code_generated = False
                tryout_output_buffers: dict[str, dict[str, Any]] = {}

                def _tryout_output_preview(text_value: str) -> tuple[str, bool]:
                    max_chars = 4000
                    text_string = str(text_value or "")
                    if len(text_string) <= max_chars:
                        return text_string, False
                    head = text_string[:2500].rstrip()
                    tail = text_string[-1000:].lstrip()
                    return f"{head}\n...[truncated tryout output]...\n{tail}", True

                def flush_tryout_output(channel: str | None = None) -> None:
                    channels = [channel] if channel else list(tryout_output_buffers)
                    for item_channel in channels:
                        buffer = tryout_output_buffers.pop(str(item_channel), None)
                        if not buffer:
                            continue
                        text_preview, truncated = _tryout_output_preview(str(buffer.get("text") or ""))
                        self._emit_tryout_output(
                            observability,
                            action=action,
                            channel=str(item_channel),
                            text=text_preview,
                            cwd=str(cwd),
                            extra={
                                **dict(buffer.get("extra") or {}),
                                "chunk_count": int(buffer.get("chunk_count") or 0),
                                "line_count": int(buffer.get("line_count") or 0),
                                "truncated": truncated,
                            },
                            level=str(buffer.get("level") or "info"),
                        )

                def emit_tryout_output(
                    *,
                    channel: str,
                    text: str = "",
                    exit_code: Any = None,
                    extra: dict[str, Any] | None = None,
                    level: str = "info",
                ) -> None:
                    if channel in {"stdout", "stderr"} and self.operator_tryout_enabled():
                        buffer = tryout_output_buffers.setdefault(
                            channel,
                            {
                                "text": "",
                                "chunk_count": 0,
                                "line_count": 0,
                                "extra": {},
                                "level": level,
                            },
                        )
                        buffer["text"] = f"{buffer.get('text') or ''}{text}"
                        buffer["chunk_count"] = int(buffer.get("chunk_count") or 0) + 1
                        buffer["line_count"] = int(buffer.get("line_count") or 0) + str(text or "").count("\n")
                        buffer["extra"] = {**dict(buffer.get("extra") or {}), **dict(extra or {})}
                        buffer["level"] = level
                        if int(buffer["chunk_count"]) >= 20 or len(str(buffer["text"])) >= 4000:
                            flush_tryout_output(channel)
                        return
                    flush_tryout_output()
                    self._emit_tryout_output(
                        observability,
                        action=action,
                        channel=channel,
                        text=text,
                        exit_code=exit_code,
                        cwd=str(cwd),
                        extra=extra,
                        level=level,
                    )
                if action.kind == "shell_command":
                    shell_literal_env = self._resolve_shell_literal_input_env(
                        action,
                        observability,
                    )
                    shell_stdin, shell_stdin_metadata = self._resolve_shell_stdin(
                        action,
                        records_by_action,
                        observability,
                        execution_context,
                    )
                if action.kind == "shell_command" and action.input_bindings:
                    stdin_input_name = _action_stdin_binding_name(action)
                    shell_bound_env = self._resolve_shell_binding_env(
                        action,
                        records_by_action,
                        observability,
                        exclude_input_names={stdin_input_name} if stdin_input_name else set(),
                    )
                if action.kind == "shell_command":
                    raw_context_env = execution_context.get("shell_env")
                    if isinstance(raw_context_env, dict):
                        shell_context_env = {
                            str(name): str(value)
                            for name, value in raw_context_env.items()
                            if str(name or "").strip()
                        }
                    shell_env = {**shell_context_env, **shell_literal_env, **shell_bound_env}
                    action_inputs_for_record = {**shell_literal_env, **shell_bound_env}
                if action.kind == "python_action":
                    action_inputs = self._resolve_action_inputs(action, records_by_action, observability)
                    action_inputs_for_record = dict(action_inputs)
                    if action.defer_code_generation:
                        action = self._complete_deferred_python_code(
                            user_request,
                            action,
                            action_inputs,
                            observability,
                        )
                        deferred_code_generated = True
                    self._proof_concrete_python_before_execution(
                        user_request,
                        action,
                        action_inputs,
                        observability,
                    )
                command = (
                    str(action.command or "")
                    if action.kind == "shell_command"
                    else python_action_command(str(action.code or ""), action_inputs)
                )
                if action.kind == "shell_command" and (shell_env or shell_stdin is not None):
                    execution_context = {
                        **execution_context,
                        **({"shell_env": dict(shell_env)} if shell_env else {}),
                        **({"shell_stdin": shell_stdin} if shell_stdin is not None else {}),
                    }
                display_command = str(action.command or "") if action.kind == "shell_command" else ""
                detached = action.execution_mode == "terminal_detached"
                terminal_session_id = str(execution_context.get("terminal_session_id") or "").strip()
                terminal_required = action.interaction_mode == "may_prompt"
                terminal_dispatch_requested = bool(
                    execution_context.get("execute_in_terminal")
                    and terminal_session_id
                )
                terminal_dispatch = bool(terminal_required and terminal_session_id) or detached
                execution_snippet = self._execution_snippet_metadata(action, max_chars=20000)
                execution_snippet_command = str(execution_snippet.get("execution_snippet") or "")
                operation_metadata = self._execution_operation_description(action, cwd=str(cwd))
                self._emit(
                    observability,
                    level="info",
                    event_type="execution.command.started",
                    title="Command started" if action.kind == "shell_command" else "Python action started",
                    summary="The runtime started streaming one gateway action.",
                    details={
                        **gateway_metadata,
                        "action_id": action.action_id,
                        "task_id": action.task_id,
                        "label": action.reason or action.action_id,
                        "command": display_command or execution_snippet_command,
                        "code": _truncate(action.code, 1200) if action.kind == "python_action" else None,
                        **execution_snippet,
                        **operation_metadata,
                        "kind": action.kind,
                        "cwd": str(cwd),
                        "terminal_dispatch": terminal_dispatch,
                        "terminal_dispatch_requested": terminal_dispatch_requested,
                        "terminal_session_id": terminal_session_id
                        if terminal_dispatch
                        else None,
                        "execution_mode": action.execution_mode,
                        "interaction_mode": action.interaction_mode,
                        "stdin": shell_stdin_metadata,
                    },
                )
                if detached:
                    start_terminal_detached_command = getattr(
                        self.gateway_client,
                        "start_terminal_detached_command",
                        None,
                    )
                    if not callable(start_terminal_detached_command):
                        raise RuntimeError("Gateway client does not support detached terminal execution.")
                    response = start_terminal_detached_command(
                        command=command,
                        cwd=str(cwd),
                        execution_context=execution_context,
                    )
                    gateway_node = response.get("gateway_node")
                    gateway_url = response.get("gateway_url")
                    gateway_metadata = merge_gateway_metadata(
                        gateway_metadata,
                        {"gateway_node": gateway_node, "gateway_url": gateway_url},
                        response,
                    )
                    message = str(response.get("message") or "Command started in terminal.")
                    self._emit(
                        observability,
                        level="info",
                        event_type="execution.command.completed",
                        title="Command started in terminal",
                        summary="A terminal-owned command was started and detached from runtime capture.",
                        details={
                            **gateway_metadata,
                            "action_id": action.action_id,
                            "task_id": action.task_id,
                            "label": action.reason or action.action_id,
                            "channel": "completed",
                            "text": message,
                            "exit_code": 0,
                            **operation_metadata,
                            "cwd": str(cwd),
                            "terminal_dispatch": True,
                            "terminal_detached": True,
                            "terminal_session_id": response.get("terminal_session_id")
                            or execution_context.get("terminal_session_id"),
                        },
                    )
                    self._emit_tryout_output(
                        observability,
                        action=action,
                        channel="completed",
                        text=message,
                        exit_code=0,
                        cwd=str(cwd),
                        extra=gateway_metadata,
                    )
                    return OperatorExecutionRecord(
                        action_id=action.action_id,
                        task_id=action.task_id,
                        kind=action.kind,
                        status="success",
                        output=message,
                        exit_code=0,
                        metadata={
                            "cwd": str(cwd),
                            **self._execution_snippet_metadata(action),
                            **operation_metadata,
                            **gateway_metadata,
                            "gateway_node": gateway_node,
                            "gateway_url": gateway_url,
                            "terminal_dispatch": True,
                            "terminal_detached": True,
                            "declared_output_shape": action.declared_output_shape,
                        },
                    )
                stream_terminal_command = getattr(self.gateway_client, "stream_terminal_command", None)
                stream_raw_command = getattr(self.gateway_client, "stream_raw_command", None)
                if terminal_dispatch and callable(stream_terminal_command):
                    stream_events = stream_terminal_command(
                        command=command,
                        cwd=str(cwd),
                        execution_context=execution_context,
                        display_command=(
                            display_command
                            if action.kind == "shell_command"
                            else f"[python_action] {action.reason or action.action_id}"
                        ),
                    )
                elif terminal_required:
                    background_terminal_error = self._background_terminal_error_context(execution_context)
                    if background_terminal_error:
                        message = str(
                            background_terminal_error.get("background_terminal_error") or ""
                        ).strip()
                        return OperatorExecutionRecord(
                            action_id=action.action_id,
                            task_id=action.task_id,
                            kind=action.kind,
                            status="error",
                            stderr=(
                                "This background action may require interactive terminal input, "
                                "but no background terminal was available."
                            ),
                            exit_code=None,
                            metadata={
                                "cwd": str(cwd),
                                **self._execution_snippet_metadata(action),
                                **operation_metadata,
                                "terminal_dispatch": False,
                                "terminal_required": True,
                                "terminal_context_required": True,
                                "resumable": True,
                                "interaction_mode": action.interaction_mode,
                                "declared_output_shape": action.declared_output_shape,
                                **background_terminal_error,
                            },
                            error=(
                                "background_terminal_unavailable: "
                                f"{message or 'No background terminal session was attached to this background task.'}"
                            ),
                        )
                    return OperatorExecutionRecord(
                        action_id=action.action_id,
                        task_id=action.task_id,
                        kind=action.kind,
                        status="error",
                        stderr=(
                            "This action may require interactive terminal input, but no trusted "
                            "Agent UI terminal context was attached to this execution."
                        ),
                        exit_code=None,
                        metadata={
                            "cwd": str(cwd),
                            **self._execution_snippet_metadata(action),
                            **operation_metadata,
                            "terminal_dispatch": False,
                            "terminal_required": True,
                            "terminal_context_required": True,
                            "resumable": True,
                            "interaction_mode": action.interaction_mode,
                            "declared_output_shape": action.declared_output_shape,
                        },
                        error=(
                            "terminal_context_required: Open the Agent UI terminal and continue "
                            "this run so the command can receive any required user input."
                        ),
                    )
                elif callable(stream_raw_command):
                    stream_events = stream_raw_command(
                        command=command,
                        cwd=str(cwd),
                        execution_context=execution_context,
                    )
                else:
                    response = self.gateway_client.execute_raw_command(
                        command=command,
                        cwd=str(cwd),
                        execution_context=execution_context,
                    )
                    gateway_node = response.get("gateway_node")
                    gateway_url = response.get("gateway_url")
                    gateway_metadata = merge_gateway_metadata(
                        gateway_metadata,
                        {"gateway_node": gateway_node, "gateway_url": gateway_url},
                        response,
                    )
                    stream_events = (
                        {"type": "stdout", "text": str(response.get("stdout", ""))},
                        {"type": "stderr", "text": str(response.get("stderr", ""))},
                        {"type": "completed", "exit_code": int(response.get("exit_code", 1))},
                    )
                for chunk in stream_events:
                    if _cancel_requested(execution_context):
                        flush_tryout_output()
                        return OperatorExecutionRecord(
                            action_id=action.action_id,
                            task_id=action.task_id,
                            kind=action.kind,
                            status="error",
                            stderr="Execution cancelled by user.",
                            error="Execution cancelled by user.",
                            metadata={
                                "cwd": str(cwd),
                                **self._execution_snippet_metadata(action),
                                **gateway_metadata,
                                "gateway_node": gateway_node,
                                "gateway_url": gateway_url,
                                "cancelled": True,
                            },
                        )
                    chunk_type = str(chunk.get("type") or "")
                    text = str(chunk.get("text") or "")
                    gateway_node = chunk.get("gateway_node", gateway_node)
                    gateway_url = chunk.get("gateway_url", gateway_url)
                    gateway_metadata = merge_gateway_metadata(
                        gateway_metadata,
                        {"gateway_node": gateway_node, "gateway_url": gateway_url},
                        chunk,
                    )
                    terminal_dispatch = bool(chunk.get("terminal_dispatch") or terminal_dispatch)
                    if chunk_type == "stdout":
                        stdout_parts.append(text)
                    elif chunk_type in {"stderr", "error"}:
                        stderr_parts.append(text)
                    elif chunk_type == "cancelled":
                        command_cancelled = True
                        stderr_parts.append(text or "Command cancelled by user.\n")
                        exit_code = int(chunk.get("exit_code") if chunk.get("exit_code") is not None else 130)
                    elif chunk_type == "terminal_input_required":
                        terminal_input_satisfied_by_macro = False
                        terminal_input_macro_delivery: dict[str, Any] | None = None
                        macro = consume_typein_macro(
                            execution_context,
                            delivery="terminal_prompt",
                            action_id=action.action_id,
                            reusable=True,
                        )
                        if macro is not None:
                            delivery = user_macro_public_delivery(
                                macro,
                                delivery="terminal_prompt",
                                action_id=action.action_id,
                            )
                            macro_key = str(delivery.get("macro_id") or "")
                            if macro_key and macro_key in terminal_macro_delivery_keys:
                                repeated_terminal_prompt_after_macro = True
                                self._emit(
                                    observability,
                                    level="warning",
                                    event_type="operator.user_macro.repeated_prompt",
                                    title="Terminal prompt repeated",
                                    summary=(
                                        "The terminal asked for input again after the runtime "
                                        "typed the provided user macro. The runtime will not "
                                        "re-send the same secret automatically."
                                    ),
                                    details=delivery,
                                )
                                continue
                            if macro_key:
                                terminal_macro_delivery_keys.add(macro_key)
                            try:
                                write_terminal_input = getattr(
                                    self.gateway_client,
                                    "write_terminal_input",
                                    None,
                                )
                                if not callable(write_terminal_input):
                                    raise RuntimeError(
                                        "Gateway client does not support terminal input writes."
                                )
                                write_terminal_input(
                                    text=macro_value_with_enter(macro.get("value")),
                                    execution_context=execution_context,
                                )
                                terminal_input_satisfied_by_macro = True
                                terminal_input_macro_delivery = dict(delivery)
                                self._emit(
                                    observability,
                                    level="info",
                                    event_type="operator.user_macro.consumed",
                                    title="User macro consumed",
                                    summary=(
                                        "The runtime typed a deterministic user macro into "
                                        "the interactive terminal."
                                    ),
                                    details=delivery,
                                )
                            except Exception as exc:
                                macro_delivery_failed = True
                                macro_delivery_error = (
                                    f"Failed to deliver user macro to terminal: {exc}"
                                )
                                stderr_parts.append(
                                    f"{macro_delivery_error}\n"
                                )
                                self._emit(
                                    observability,
                                    level="error",
                                    event_type="operator.user_macro.delivery_failed",
                                    title="User macro delivery failed",
                                    summary=(
                                        "The runtime could not type the deterministic user "
                                        "macro into the terminal."
                                    ),
                                    details={
                                        **delivery,
                                        "error": str(exc),
                                    },
                                )
                    elif chunk_type == "completed":
                        exit_code = int(chunk.get("exit_code") if chunk.get("exit_code") is not None else 1)
                    if chunk_type in {
                        "stdout",
                        "stderr",
                        "completed",
                        "error",
                        "cancelled",
                        "terminal_input_required",
                    }:
                        self._emit(
                            observability,
                            level=(
                                "warning"
                                if chunk_type == "terminal_input_required"
                                else "error"
                                if chunk_type in {"error", "cancelled"}
                                else "info"
                            ),
                            event_type=f"execution.command.{chunk_type}",
                            title=(
                                "Command output"
                                if chunk_type in {"stdout", "stderr"}
                                else "Terminal input required"
                                if chunk_type == "terminal_input_required"
                                else "Command cancelled"
                                if chunk_type == "cancelled"
                                else "Command completed"
                            ),
                            summary=(
                                "The command is waiting for user input in the terminal."
                                if chunk_type == "terminal_input_required"
                                else "A command stream event was received."
                            ),
                            details={
                                **gateway_metadata,
                                "action_id": action.action_id,
                                "task_id": action.task_id,
                                "label": action.reason or action.action_id,
                                "channel": chunk_type,
                                "text": text,
                                "exit_code": chunk.get("exit_code"),
                                "cwd": str(cwd),
                                **execution_snippet,
                                **operation_metadata,
                                **({"command": execution_snippet_command} if execution_snippet_command else {}),
                                "terminal_dispatch": terminal_dispatch,
                                "terminal_dispatch_requested": terminal_dispatch_requested,
                                "terminal_session_id": chunk.get("terminal_session_id")
                                or (
                                    execution_context.get("terminal_session_id")
                                    if terminal_dispatch
                                    else None
                                ),
                                **(
                                    {
                                        "terminal_input_satisfied_by_macro": True,
                                        "user_macro_delivery": terminal_input_macro_delivery,
                                    }
                                    if (
                                        chunk_type == "terminal_input_required"
                                        and terminal_input_satisfied_by_macro
                                    )
                                    else {}
                                ),
                            },
                        )
                        emit_tryout_output(
                            channel=chunk_type,
                            text=text,
                            exit_code=chunk.get("exit_code"),
                            extra=gateway_metadata,
                            level=(
                                "warning"
                                if chunk_type == "terminal_input_required"
                                else "error"
                                if chunk_type in {"error", "cancelled"}
                                else "info"
                            ),
                        )
                flush_tryout_output()
                stdout = "".join(stdout_parts)
                stderr = "".join(stderr_parts)
                sudo_retry_judge = (
                    self._judge_sudo_retry_after_nonzero_exit(
                        user_request=user_request,
                        action_kind=action.kind,
                        command=display_command or command,
                        exit_code=exit_code,
                        stdout=stdout,
                        stderr=stderr,
                        error="",
                        command_cancelled=command_cancelled,
                        macro_delivery_failed=macro_delivery_failed,
                    )
                    if action.kind == "shell_command"
                    else None
                )
                sudo_retry_reason = (
                    str(sudo_retry_judge.reason or "").strip()
                    or "The failed command appears to need sudo."
                    if sudo_retry_judge is not None
                    else None
                )
                action_effect = classify_action_effect(
                    action,
                    task=task_context,
                    policy_mode=operator_policy_mode(self.config, "effect"),
                )
                mutating_shell_command = (
                    action.kind == "shell_command" and action_effect.mutates_state
                )
                if mutating_shell_command and exit_code != 0:
                    stdout_classification = ShellStdoutClassification(
                        stdout_usable=False,
                        reason="mutating_command_nonzero_exit",
                        confidence=1.0,
                        classifier_source="effect_classifier",
                    )
                else:
                    stdout_classification = self._classify_shell_stdout_after_nonzero_exit(
                        user_request=user_request,
                        action_kind=action.kind,
                        command=display_command or command,
                        exit_code=exit_code,
                        stdout=stdout,
                        stderr=stderr,
                        command_cancelled=command_cancelled,
                        macro_delivery_failed=macro_delivery_failed,
                    )
                shell_stdout_candidate = _shell_stdout_candidate(
                    action_kind=action.kind,
                    exit_code=exit_code,
                    stdout=stdout,
                    command_cancelled=command_cancelled,
                    macro_delivery_failed=macro_delivery_failed,
                )
                stdout_diagnostic_error = shell_stdout_candidate and not stdout_classification.stdout_usable
                partial_stdout_available = shell_stdout_candidate and stdout_classification.stdout_usable
                partial_stdout_approved = bool(execution_context.get("allow_partial_shell_results"))
                if sudo_retry_reason:
                    stdout_diagnostic_error = True
                    partial_stdout_available = False
                if mutating_shell_command and exit_code != 0:
                    partial_stdout_available = False
                shell_stdout_success = (
                    action.kind == "shell_command"
                    and bool(stdout.strip())
                    and stdout_classification.stdout_usable
                    and not command_cancelled
                    and not macro_delivery_failed
                    and not mutating_shell_command
                    and not sudo_retry_reason
                    and exit_code not in {124, 130}
                )
                if action.kind == "shell_command":
                    status = (
                        "success"
                        if not macro_delivery_failed and (shell_stdout_success or exit_code == 0)
                        else "error"
                    )
                else:
                    status = "success" if not macro_delivery_failed and exit_code == 0 else "error"
                if macro_delivery_failed and exit_code == 0:
                    exit_code = 1
                if action.kind == "python_action" and status == "success":
                    self._validate_python_output(action, action_inputs, stdout)
                metadata = {
                    "cwd": str(cwd),
                    **operation_metadata,
                    **gateway_metadata,
                    "gateway_node": gateway_node,
                    "gateway_url": gateway_url,
                    "terminal_dispatch": terminal_dispatch,
                    "terminal_dispatch_requested": terminal_dispatch_requested,
                    "terminal_required": terminal_required,
                    "interaction_mode": action.interaction_mode,
                }
                if action.kind == "shell_command":
                    metadata["shell_command"] = display_command or command
                    if sudo_retry_judge is not None:
                        proposed_sudo_command = sudo_command_for(display_command or command)
                        metadata.update(
                            {
                                "permission_denied_sudo_candidate": True,
                                "sudo_retry_reason": sudo_retry_reason,
                                "sudo_retry_original_command": display_command or command,
                                "sudo_retry_proposed_command": proposed_sudo_command,
                                "sudo_retry_judge": sudo_retry_judge.model_dump(mode="json"),
                            }
                        )
                if action.kind == "python_action":
                    metadata.update(
                        {
                            **self._execution_snippet_metadata(action),
                            "command": str(action.code or ""),
                            "deferred_code_generated": deferred_code_generated,
                            "generated_code": str(action.code or "")
                            if deferred_code_generated
                            else None,
                            "bound_inputs_preview": self._inputs_preview(action_inputs_for_record),
                        }
                    )
                if action.kind == "shell_command" and shell_env:
                    metadata.update(
                        {
                            "shell_input_env_used": True,
                            "shell_input_env_names": sorted(shell_env),
                            "shell_literal_input_env_names": sorted(shell_literal_env),
                            "shell_input_binding_env_names": sorted(shell_bound_env),
                            "shell_context_env_names": sorted(shell_context_env),
                            "bound_inputs_preview": self._inputs_preview(action_inputs_for_record),
                        }
                    )
                if action.kind == "shell_command" and shell_bound_env:
                    metadata["shell_input_bindings_used"] = True
                if action.kind == "shell_command" and shell_stdin_metadata.get("stdin_mode") != "none":
                    metadata.update(
                        {
                            "shell_stdin_used": True,
                            "shell_stdin": dict(shell_stdin_metadata),
                        }
                    )
                if partial_stdout_available:
                    metadata.update(
                        {
                            "partial_stdout_available": True,
                            "partial_stdout_approved": partial_stdout_approved,
                            "partial_exit_code": exit_code,
                            "stdout_primary_success": shell_stdout_success,
                            "partial_reason": (
                                "The shell command produced stdout but exited non-zero. "
                                "Stdout is treated as the primary useful result for shell actions."
                            ),
                        }
                    )
                if shell_stdout_candidate:
                    metadata["stdout_error_classifier"] = {
                        "stdout_usable": stdout_classification.stdout_usable,
                        "reason": stdout_classification.reason,
                        "confidence": stdout_classification.confidence,
                        "classifier_source": stdout_classification.classifier_source,
                    }
                if stdout_diagnostic_error:
                    metadata.update(
                        {
                            "stdout_diagnostic_error": True,
                            "stdout_primary_success": False,
                        }
                    )
                if macro_delivery_failed:
                    metadata.update(
                        {
                            "user_macro_delivery_failed": True,
                            "user_macro_delivery_error": macro_delivery_error,
                        }
                    )
                if repeated_terminal_prompt_after_macro:
                    metadata["terminal_prompt_repeated_after_user_macro"] = True
                return OperatorExecutionRecord(
                    action_id=action.action_id,
                    task_id=action.task_id,
                    kind=action.kind,
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    output=stdout if action.kind == "python_action" else None,
                    metadata=metadata,
                    error=None if status == "success" else (
                        macro_delivery_error
                        if macro_delivery_error
                        else
                        (stderr or stdout or "Command failed with a permission error.")
                        if sudo_retry_reason
                        else
                        stdout.strip()
                        if stdout_diagnostic_error
                        else
                        (
                            "Command produced partial stdout but exited with "
                            f"code {exit_code}. Approval is required before "
                            "downstream actions consume incomplete output."
                        )
                        if partial_stdout_available
                        else (stderr or stdout or "Command failed.")
                    ),
                )
            action_inputs = self._resolve_action_inputs(action, records_by_action, observability)
            action_inputs_for_record = dict(action_inputs)
            python_terminal_dispatch_requested = bool(
                execution_context.get("execute_in_terminal")
                and execution_context.get("terminal_session_id")
            )
            if action.defer_code_generation:
                action = self._complete_deferred_python_code(
                    user_request,
                    action,
                    action_inputs,
                    observability,
                )
                deferred_code_generated = True
            else:
                deferred_code_generated = False
            self._proof_concrete_python_before_execution(
                user_request,
                action,
                action_inputs,
                observability,
            )
            execution_snippet = self._execution_snippet_metadata(action, max_chars=20000)
            execution_snippet_command = str(execution_snippet.get("execution_snippet") or "")
            operation_metadata = self._execution_operation_description(
                action,
                cwd=str(self.validator.resolved_cwd(action)),
            )
            self._emit(
                observability,
                level="info",
                event_type="execution.command.started",
                title="Python transform started",
                summary="The runtime started one python transform.",
                details={
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "label": action.reason or action.action_id,
                    "cwd": str(self.validator.resolved_cwd(action)),
                    "terminal_dispatch": False,
                    "terminal_dispatch_requested": python_terminal_dispatch_requested,
                    "terminal_session_id": None,
                    "execution_mode": action.execution_mode,
                    "kind": action.kind,
                    "command": execution_snippet_command,
                    **execution_snippet,
                    **operation_metadata,
                },
            )
            output = self.python_executor.execute(action, action_inputs)
            self._validate_python_output(action, action_inputs, output)
            rendered = output if isinstance(output, str) else _stable_json(output)
            terminal_write_delivered = False
            terminal_write_error: str | None = None
            if rendered and not terminal_write_delivered:
                self._emit(
                    observability,
                    level="info",
                    event_type="execution.command.stdout",
                    title="Python transform output",
                    summary="A python transform output event was received.",
                    details={
                        "action_id": action.action_id,
                        "task_id": action.task_id,
                        "label": action.reason or action.action_id,
                        "channel": "stdout",
                        "text": f"{rendered}\n",
                        "cwd": str(self.validator.resolved_cwd(action)),
                        "terminal_dispatch": False,
                        "terminal_dispatch_requested": python_terminal_dispatch_requested,
                        "terminal_write_error": terminal_write_error,
                        "kind": action.kind,
                        "command": execution_snippet_command,
                        **execution_snippet,
                        **operation_metadata,
                    },
                )
                self._emit_tryout_output(
                    observability,
                    action=action,
                    channel="stdout",
                    text=f"{rendered}\n",
                    cwd=str(self.validator.resolved_cwd(action)),
                )
            self._emit(
                observability,
                level="info",
                event_type="execution.command.completed",
                title="Python transform completed",
                summary="A python transform completed successfully.",
                details={
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "label": action.reason or action.action_id,
                    "channel": "completed",
                    "text": "",
                    "exit_code": 0,
                    "cwd": str(self.validator.resolved_cwd(action)),
                    "terminal_dispatch": terminal_write_delivered,
                    "terminal_dispatch_requested": python_terminal_dispatch_requested,
                    "terminal_write_error": terminal_write_error,
                    "terminal_session_id": execution_context.get("terminal_session_id")
                    if terminal_write_delivered
                    else None,
                    "kind": action.kind,
                    "command": execution_snippet_command,
                    **execution_snippet,
                    **operation_metadata,
                },
            )
            self._emit_tryout_output(
                observability,
                action=action,
                channel="completed",
                text="",
                exit_code=0,
                cwd=str(self.validator.resolved_cwd(action)),
            )
            return OperatorExecutionRecord(
                action_id=action.action_id,
                task_id=action.task_id,
                kind=action.kind,
                status="success",
                output=output,
                metadata={
                    "declared_output_shape": action.declared_output_shape,
                    "terminal_dispatch": terminal_write_delivered,
                    "terminal_dispatch_requested": python_terminal_dispatch_requested,
                    "terminal_write_error": terminal_write_error,
                    **self._execution_snippet_metadata(action),
                    **operation_metadata,
                    "command": str(action.code or ""),
                    "deferred_code_generated": deferred_code_generated,
                    "generated_code": str(action.code or "") if deferred_code_generated else None,
                    "bound_inputs_preview": self._inputs_preview(action_inputs_for_record),
                },
            )
        except Exception as exc:
            generated_code = (
                str(action.code or "")
                if getattr(action, "kind", None) in {"python_action", "python_transform"}
                and not getattr(action, "defer_code_generation", False)
                else ""
            )
            proof_metadata: dict[str, Any] = {}
            if isinstance(exc, GeneratedPythonRuntimeProofError):
                proof_metadata = {
                    "python_code_pre_execution_proof_failed": True,
                    "python_code_proof": exc.proof.model_dump(mode="json"),
                }
            if action.kind == "python_transform":
                python_terminal_dispatch_requested = bool(
                    execution_context.get("execute_in_terminal")
                    and execution_context.get("terminal_session_id")
                )
                operation_metadata = self._execution_operation_description(
                    action,
                    cwd=str(action.cwd or "."),
                )
                text = f"{type(exc).__name__}: {exc}\n"
                terminal_write_delivered = False
                terminal_write_error: str | None = None
                if not terminal_write_delivered:
                    self._emit(
                        observability,
                        level="error",
                        event_type="execution.command.error",
                        title="Python transform failed",
                        summary="A python transform failed during execution.",
                        details={
                            "action_id": action.action_id,
                            "task_id": action.task_id,
                            "label": action.reason or action.action_id,
                            "channel": "error",
                            "text": text,
                            "exit_code": 1,
                            "cwd": str(action.cwd or "."),
                            "terminal_dispatch": False,
                            "terminal_dispatch_requested": python_terminal_dispatch_requested,
                            "terminal_write_error": terminal_write_error,
                            "kind": action.kind,
                            "command": execution_snippet_command,
                            **execution_snippet,
                            **operation_metadata,
                        },
                    )
                    self._emit_tryout_output(
                        observability,
                        action=action,
                        channel="error",
                        text=text,
                        exit_code=1,
                        cwd=str(action.cwd or "."),
                        level="error",
                    )
            return OperatorExecutionRecord(
                action_id=action.action_id,
                task_id=action.task_id,
                kind=action.kind,
                status="error",
                error=str(exc),
                stderr=traceback.format_exc(limit=3),
                metadata={
                    "generated_code": generated_code or None,
                    "deferred_code_generated": bool(generated_code),
                    **self._execution_snippet_metadata(action),
                    **self._execution_operation_description(action, cwd=str(action.cwd or ".")),
                    "command": str(action.code or "") if getattr(action, "kind", None) in {"python_action", "python_transform"} else "",
                    "bound_inputs_preview": self._inputs_preview(action_inputs_for_record),
                    **proof_metadata,
                },
            )

    def _store_records(self, records: list[OperatorExecutionRecord]) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        for record in records:
            payload = record.model_dump(mode="json")
            data_ref = self.result_store.put(
                record.action_id,
                payload,
                record.kind,
                {"operator_mode": True, "status": record.status},
            )
            result = ExecutionResult(
                node_id=record.action_id,
                status="success" if record.status == "success" else "error",
                data_ref=data_ref,
                data_preview=data_ref.preview,
                error=record.error,
                metadata={"operator_mode": True, **record.metadata},
            )
            self.result_store.add(result)
            results.append(result)
        return results



__all__ = ["_StepRunnerActionExecutionMixin"]
