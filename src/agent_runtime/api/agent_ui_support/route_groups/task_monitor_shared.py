"""Install task lifecycle and monitor helper functions."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared_imports import *


def install_task_monitor_helpers(ctx: Any) -> None:
    """Install task lifecycle and monitor helper functions."""


    app = ctx.app
    settings = ctx.settings
    agent_runtime = ctx.agent_runtime
    trace_store = ctx.trace_store
    prompt_fetcher = ctx.prompt_fetcher
    prompt_template_store = ctx.prompt_template_store
    state_store = ctx.state_store
    conversation_store = ctx.conversation_store
    terminal_store = ctx.terminal_store
    gateway_store = ctx.gateway_store
    task_store = ctx.task_store
    monitor_store = ctx.monitor_store
    event_store = ctx.event_store
    command_allowlist_store = ctx.command_allowlist_store
    ui_settings_store = ctx.ui_settings_store
    learning_ledger_store = ctx.learning_ledger_store
    reliability_store = ctx.reliability_store
    cancel_events = ctx.cancel_events
    cancel_events_lock = ctx.cancel_events_lock
    task_queue_lock = ctx.task_queue_lock
    active_non_task_requests_lock = ctx.active_non_task_requests_lock
    monitored_task_requests = ctx.monitored_task_requests
    active_non_task_request_ids = ctx.active_non_task_request_ids
    llm_endpoint_defaults = ctx.llm_endpoint_defaults
    runtime_controls = ctx.runtime_controls
    runtime_controls_lock = ctx.runtime_controls_lock
    pending_event_macro_contexts = ctx.pending_event_macro_contexts
    pending_event_macro_contexts_lock = ctx.pending_event_macro_contexts_lock

    default_gateway_node = getattr(ctx, 'default_gateway_node', None)
    monitor_manager = getattr(ctx, 'monitor_manager', None)
    _submit_agent_request = getattr(ctx, '_submit_agent_request', None)
    _normalize_prompt_history_items = getattr(ctx, '_normalize_prompt_history_items', None)
    _prompt_history_items = getattr(ctx, '_prompt_history_items', None)
    _record_prompt_history_item = getattr(ctx, '_record_prompt_history_item', None)
    _event_macro_context_from = getattr(ctx, '_event_macro_context_from', None)
    _apply_event_extraction_hint_context = getattr(ctx, '_apply_event_extraction_hint_context', None)
    _strip_event_macro_context = getattr(ctx, '_strip_event_macro_context', None)
    _event_context_for_api = getattr(ctx, '_event_context_for_api', None)
    _stash_event_macro_context_for_draft = getattr(ctx, '_stash_event_macro_context_for_draft', None)
    _resolve_pending_event_macro_context = getattr(ctx, '_resolve_pending_event_macro_context', None)
    _prompt_has_sanitized_typein_marker = getattr(ctx, '_prompt_has_sanitized_typein_marker', None)
    _prepare_event_prompt_and_context = getattr(ctx, '_prepare_event_prompt_and_context', None)
    _event_record_api_payload = getattr(ctx, '_event_record_api_payload', None)
    _event_draft_response_api_payload = getattr(ctx, '_event_draft_response_api_payload', None)
    _event_create_payload_from_draft = getattr(ctx, '_event_create_payload_from_draft', None)
    _event_schedule_phrase = getattr(ctx, '_event_schedule_phrase', None)
    _saved_events_message = getattr(ctx, '_saved_events_message', None)
    _event_needs_detail_message = getattr(ctx, '_event_needs_detail_message', None)
    _event_has_typein_macro = getattr(ctx, '_event_has_typein_macro', None)
    _confirmation_actions_require_terminal = getattr(ctx, '_confirmation_actions_require_terminal', None)
    _attach_background_terminal = getattr(ctx, '_attach_background_terminal', None)
    _attach_background_terminal_for_typein = getattr(ctx, '_attach_background_terminal_for_typein', None)
    _background_terminal_context_hint = getattr(ctx, '_background_terminal_context_hint', None)
    _command_allowlist_context = getattr(ctx, '_command_allowlist_context', None)
    _apply_command_allowlist_context = getattr(ctx, '_apply_command_allowlist_context', None)
    _runtime_controls_snapshot = getattr(ctx, '_runtime_controls_snapshot', None)
    _public_runtime_controls_snapshot = getattr(ctx, '_public_runtime_controls_snapshot', None)
    _apply_backend_runtime_snapshot = getattr(ctx, '_apply_backend_runtime_snapshot', None)
    _apply_runtime_control_values = getattr(ctx, '_apply_runtime_control_values', None)
    _events_enabled = getattr(ctx, '_events_enabled', None)
    _apply_runtime_repair_attempt_context = getattr(ctx, '_apply_runtime_repair_attempt_context', None)
    _apply_sql_agent_route_context = getattr(ctx, '_apply_sql_agent_route_context', None)
    _reliability_run_payload = getattr(ctx, '_reliability_run_payload', None)
    _reliability_trace_summary = getattr(ctx, '_reliability_trace_summary', None)
    _reliability_markdown_report = getattr(ctx, '_reliability_markdown_report', None)
    _event_run_status_from_trace = getattr(ctx, '_event_run_status_from_trace', None)
    _event_context = getattr(ctx, '_event_context', None)
    _finalize_event_run = getattr(ctx, '_finalize_event_run', None)
    _append_scheduled_event_completed_trace_event = getattr(ctx, '_append_scheduled_event_completed_trace_event', None)
    _ensure_scheduled_event_trace_enriched = getattr(ctx, '_ensure_scheduled_event_trace_enriched', None)
    _trace_parent_request_id = getattr(ctx, '_trace_parent_request_id', None)
    _relink_handled_event_run = getattr(ctx, '_relink_handled_event_run', None)
    _reconcile_event_run = getattr(ctx, '_reconcile_event_run', None)
    _reconcile_event_runs = getattr(ctx, '_reconcile_event_runs', None)
    _reconcile_all_event_runs = getattr(ctx, '_reconcile_all_event_runs', None)
    _event_run_api_payload = getattr(ctx, '_event_run_api_payload', None)
    _notification_api_payload = getattr(ctx, '_notification_api_payload', None)
    _execution_error_code = getattr(ctx, '_execution_error_code', None)
    _execution_error_message = getattr(ctx, '_execution_error_message', None)
    _execution_error_detail_from_source = getattr(ctx, '_execution_error_detail_from_source', None)
    _trace_execution_error_detail = getattr(ctx, '_trace_execution_error_detail', None)
    _execution_error_preview = getattr(ctx, '_execution_error_preview', None)
    _notification_level_with_execution_error = getattr(ctx, '_notification_level_with_execution_error', None)
    _event_notification_level = getattr(ctx, '_event_notification_level', None)
    _plural_count = getattr(ctx, '_plural_count', None)
    _trace_auto_approval_detail = getattr(ctx, '_trace_auto_approval_detail', None)
    _event_run_notification_message = getattr(ctx, '_event_run_notification_message', None)
    _maybe_create_event_notification = getattr(ctx, '_maybe_create_event_notification', None)
    _create_scheduled_event_auto_approved_notification = getattr(ctx, '_create_scheduled_event_auto_approved_notification', None)
    _create_direct_event_notification = getattr(ctx, '_create_direct_event_notification', None)
    _cancel_agent_request = getattr(ctx, '_cancel_agent_request', None)
    _task_status_from_trace = getattr(ctx, '_task_status_from_trace', None)
    _task_preview_from_trace = getattr(ctx, '_task_preview_from_trace', None)
    _task_blocker_from_trace = getattr(ctx, '_task_blocker_from_trace', None)
    _task_checkpoint_detail = getattr(ctx, '_task_checkpoint_detail', None)
    _persist_task_checkpoints = getattr(ctx, '_persist_task_checkpoints', None)
    _context_is_durable_task = getattr(ctx, '_context_is_durable_task', None)
    _has_active_non_task_request = getattr(ctx, '_has_active_non_task_request', None)
    _track_active_non_task_request = getattr(ctx, '_track_active_non_task_request', None)
    _task_notification_level = getattr(ctx, '_task_notification_level', None)
    _task_notification_body = getattr(ctx, '_task_notification_body', None)
    _maybe_create_task_notification = getattr(ctx, '_maybe_create_task_notification', None)
    _finalize_task_attempt_from_trace = getattr(ctx, '_finalize_task_attempt_from_trace', None)
    _task_record_payload = getattr(ctx, '_task_record_payload', None)
    _task_attempt_payload = getattr(ctx, '_task_attempt_payload', None)
    _task_checkpoint_payload = getattr(ctx, '_task_checkpoint_payload', None)
    _mark_task_attempt_waiting_for_auto_approval = getattr(ctx, '_mark_task_attempt_waiting_for_auto_approval', None)
    _task_auto_approve_enabled = getattr(ctx, '_task_auto_approve_enabled', None)
    _auto_approve_durable_task_confirmation = getattr(ctx, '_auto_approve_durable_task_confirmation', None)
    _monitor_task_attempt = getattr(ctx, '_monitor_task_attempt', None)
    _worker = getattr(ctx, '_worker', None)
    _start_next_queued_task = getattr(ctx, '_start_next_queued_task', None)
    _monitor_active_non_task_request = getattr(ctx, '_monitor_active_non_task_request', None)
    _worker = getattr(ctx, '_worker', None)
    _task_context = getattr(ctx, '_task_context', None)
    _start_task_now = getattr(ctx, '_start_task_now', None)
    _queue_or_start_task = getattr(ctx, '_queue_or_start_task', None)
    _link_task_followup_request = getattr(ctx, '_link_task_followup_request', None)
    _reconcile_open_tasks = getattr(ctx, '_reconcile_open_tasks', None)
    _monitor_record_payload = getattr(ctx, '_monitor_record_payload', None)
    _monitor_observation_payload = getattr(ctx, '_monitor_observation_payload', None)
    _monitor_draft_payload = getattr(ctx, '_monitor_draft_payload', None)
    _start_monitor = getattr(ctx, '_start_monitor', None)
    _cancel_monitor = getattr(ctx, '_cancel_monitor', None)
    _run_scheduled_event_request = getattr(ctx, '_run_scheduled_event_request', None)
    _approve_confirmation_request = getattr(ctx, '_approve_confirmation_request', None)
    _run_confirmed_request_worker = getattr(ctx, '_run_confirmed_request_worker', None)
    _auto_approve_scheduled_event = getattr(ctx, '_auto_approve_scheduled_event', None)
    _run_scheduled_event_worker = getattr(ctx, '_run_scheduled_event_worker', None)
    _launch_scheduled_event = getattr(ctx, '_launch_scheduled_event', None)

    def _approve_confirmation_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        helper = getattr(ctx, "_approve_confirmation_request", None)
        if not callable(helper):
            raise RuntimeError("Agent confirmation route helper is not registered.")
        return helper(*args, **kwargs)

    def _auto_approve_scheduled_event(*args: Any, **kwargs: Any) -> None:
        helper = getattr(ctx, "_auto_approve_scheduled_event", None)
        if not callable(helper):
            raise RuntimeError("Scheduled event auto-approval helper is not registered.")
        helper(*args, **kwargs)

    def _cancel_agent_request(
        request_id: str,
        *,
        scheduled_event_id: str = "",
        scheduled_event_run_id: str = "",
    ) -> AgentRequestTrace | None:
        trace = trace_store.get_trace(request_id)
        if trace is not None and trace.status in {"completed", "failed", "cancelled"}:
            return trace
        with cancel_events_lock:
            cancel_event = cancel_events.get(request_id)
            if cancel_event is not None:
                cancel_event.set()
        state = state_store.get(request_id)
        try:
            gateway_client = getattr(getattr(agent_runtime, "execution_engine", None), "gateway_client", None)
            cancel_raw_command = getattr(gateway_client, "cancel_raw_command", None)
            if callable(cancel_raw_command):
                cancel_context = dict(getattr(state, "context", None) or {})
                cancel_context["request_id"] = request_id
                cancel_context["gateway_execution_id"] = request_id
                cancel_raw_command(execution_id=request_id, execution_context=cancel_context)
        except Exception:
            # Cancellation remains best-effort: the local request/run state should
            # still close even if a gateway is older, disconnected, or already done.
            pass
        if trace is not None:
            if scheduled_event_run_id:
                trace_store.append_event(
                    AgentTraceEvent(
                        request_id=request_id,
                        stage="cancelled",
                        level="warning",
                        event_type="scheduled_event.run_cancelled",
                        title="Scheduled event run cancelled",
                        summary="The scheduled event run was cancelled from Event History.",
                        detail={
                            "event_id": scheduled_event_id,
                            "event_run_id": scheduled_event_run_id,
                        },
                    )
                )
            trace_store.cancel_request(request_id)
            trace = trace_store.get_trace(request_id) or trace
            if state is not None and state.conversation_id:
                conversation_store.append_turn(state.conversation_id, trace)
        return trace

    def _task_status_from_trace(trace: AgentRequestTrace | None) -> str:
        if trace is None:
            return "running"
        if trace.status == "completed" and trace.confirmation_required:
            return "awaiting_confirmation"
        if trace.status == "completed" and trace.clarification_required:
            return "awaiting_clarification"
        if trace.status in {"completed", "failed", "cancelled"}:
            return trace.status
        return "running"

    def _task_preview_from_trace(trace: AgentRequestTrace | None) -> str:
        if trace is None:
            return ""
        if trace.error:
            return str(trace.error or "")[:4000]
        if trace.final_response:
            return str(trace.final_response or "")[:12000]
        display_document = trace.display_document if isinstance(trace.display_document, dict) else {}
        summary = str(display_document.get("summary") or "").strip()
        if summary:
            return summary[:12000]
        return ""

    def _task_blocker_from_trace(trace: AgentRequestTrace | None) -> str:
        if trace is None:
            return ""
        if trace.confirmation_required:
            return "The task is waiting for approval."
        if trace.clarification_required:
            return "The task is waiting for clarification."
        return str(trace.error or "")[:4000]

    def _task_checkpoint_detail(detail: Any) -> dict[str, Any]:
        if not isinstance(detail, dict):
            return {}
        try:
            redacted = redact_debug_value(detail)
        except Exception:
            redacted = detail
        if not isinstance(redacted, dict):
            return {}
        serialized = json.dumps(redacted, default=str)
        if len(serialized) > 4000:
            return {"preview": serialized[:4000]}
        payload = json.loads(serialized)
        return payload if isinstance(payload, dict) else {}

    def _persist_task_checkpoints(
        *,
        task_id: str,
        attempt_id: str,
        request_id: str,
        trace: AgentRequestTrace | None,
        after_id: int = 0,
    ) -> int:
        if trace is None:
            return int(after_id or 0)
        cursor = int(after_id or 0)
        for event in list(trace.events or []):
            event_id = int(getattr(event, "id", 0) or 0)
            if event_id and event_id <= cursor:
                continue
            task_store.add_checkpoint(
                task_id=task_id,
                attempt_id=attempt_id,
                request_id=request_id,
                trace_event_id=event_id,
                stage=str(getattr(event, "stage", "") or ""),
                event_type=str(getattr(event, "event_type", "") or ""),
                level=str(getattr(event, "level", "") or "info"),
                title=str(getattr(event, "title", "") or ""),
                summary=str(getattr(event, "summary", "") or ""),
                detail=_task_checkpoint_detail(getattr(event, "detail", None)),
                created_at=str(getattr(event, "created_at", "") or "") or task_utc_now_iso(),
            )
            cursor = max(cursor, event_id)
        return cursor

    def _context_is_durable_task(context: dict[str, Any] | None) -> bool:
        return bool(str((context or {}).get("durable_task_id") or "").strip())

    def _has_active_non_task_request() -> bool:
        with active_non_task_requests_lock:
            return bool(active_non_task_request_ids)

    def _active_non_task_child_request_id(
        request_id: str,
        state: Any | None = None,
    ) -> str:
        child_request_id = str(getattr(state, "continuation_request_id", "") or "").strip()
        if child_request_id:
            return child_request_id
        child_request_id = state_store.find_child_request_id(request_id)
        if child_request_id:
            return child_request_id
        return trace_store.find_child_request_id(request_id)

    def _active_non_task_request_pending(request_id: str, seen: set[str] | None = None) -> bool:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return False
        seen = set(seen or set())
        if normalized_request_id in seen:
            return False
        seen.add(normalized_request_id)
        trace = trace_store.get_trace(normalized_request_id)
        if trace is None:
            return True
        if trace.status not in {"completed", "failed", "cancelled"}:
            return True
        state = state_store.get(normalized_request_id)
        child_request_id = _active_non_task_child_request_id(normalized_request_id, state)
        if child_request_id:
            return _active_non_task_request_pending(child_request_id, seen)
        if trace.status == "completed" and (
            trace.confirmation_required or trace.clarification_required
        ):
            return True
        return False

    def _release_active_non_task_request(request_id: str) -> bool:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return False
        removed = False
        with active_non_task_requests_lock:
            if normalized_request_id in active_non_task_request_ids:
                active_non_task_request_ids.discard(normalized_request_id)
                removed = True
        if removed:
            _start_next_queued_task()
        return removed

    def _track_active_non_task_request(request_id: str, context: dict[str, Any] | None) -> None:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id or _context_is_durable_task(context):
            return
        with active_non_task_requests_lock:
            active_non_task_request_ids.add(normalized_request_id)
        _monitor_active_non_task_request(normalized_request_id)

    def _task_notification_level(
        status: str,
        execution_error_detail: dict[str, Any] | None = None,
    ) -> str:
        default_level = "info"
        if status == "completed":
            default_level = "success"
        elif status in {"awaiting_confirmation", "awaiting_clarification", "interrupted"}:
            default_level = "warning"
        elif status in {"failed", "cancelled"}:
            default_level = "error"
        return _notification_level_with_execution_error(
            status,
            default_level,
            execution_error_detail,
        )

    def _task_notification_body(
        *,
        task: Any,
        status_text: str,
        body: str,
        execution_error_detail: dict[str, Any] | None = None,
    ) -> str:
        if not isinstance(execution_error_detail, dict) or not execution_error_detail.get("execution_error"):
            return body
        error_preview = _execution_error_preview(execution_error_detail)
        title = str(getattr(task, "title", "") or "Untitled task")
        if str(getattr(task, "status", "") or "") == "completed":
            parts = [f"`{title}` completed with one or more action errors."]
        else:
            parts = [f"`{title}` is {status_text} with one or more action errors."]
        for item in (error_preview, body):
            if item and item not in parts:
                parts.append(item)
        return "\n\n".join(parts)

    def _maybe_create_task_notification(
        *,
        task_id: str,
        status: str,
        request_id: str = "",
        attempt_id: str = "",
        message: str = "",
        execution_error_detail: dict[str, Any] | None = None,
    ) -> None:
        if status == "running":
            return
        task = task_store.get_task(task_id)
        if task is None:
            return
        source_id = f"{task_id}:{attempt_id or request_id or status}:{status}"
        if event_store.notification_exists(source_type="durable_task", source_id=source_id):
            return
        status_text = status.replace("_", " ")
        title = f"Task {status_text}: {task.title or 'Untitled task'}"
        body = message or task.blocker_reason or task.error_preview or task.final_response_preview
        if not body:
            body = f"`{task.title or 'Untitled task'}` is {status_text}."
        body = _task_notification_body(
            task=task,
            status_text=status_text,
            body=body,
            execution_error_detail=execution_error_detail,
        )
        metadata = {
            "task_id": task.task_id,
            "task_status": status,
            "attempt_id": attempt_id,
        }
        if isinstance(execution_error_detail, dict) and execution_error_detail.get("execution_error"):
            metadata.update(execution_error_detail)
        event_store.create_notification(
            AgentNotificationCreate(
                level=_task_notification_level(status, execution_error_detail),
                title=title,
                message=body,
                source_type="durable_task",
                source_id=source_id,
                event_id=task.event_id,
                event_run_id=task.event_run_id,
                request_id=request_id,
                metadata=metadata,
            )
        )

    def _finalize_task_attempt_from_trace(
        *,
        task_id: str,
        attempt_id: str,
        request_id: str,
        trace: AgentRequestTrace | None,
    ) -> str:
        status = _task_status_from_trace(trace)
        final_preview = _task_preview_from_trace(trace)
        execution_error_detail = _trace_execution_error_detail(trace)
        execution_error_preview = _execution_error_preview(execution_error_detail)
        error_preview = (
            str(getattr(trace, "error", "") or "")[:4000] if trace is not None else ""
        ) or execution_error_preview
        blocker = _task_blocker_from_trace(trace)
        task_store.update_attempt(
            attempt_id,
            status=status,  # type: ignore[arg-type]
            final_response_preview=final_preview,
            error_preview=error_preview or (blocker if status != "completed" else ""),
        )
        task_store.update_task(
            task_id,
            AgentTaskUpdate(
                status=status,  # type: ignore[arg-type]
                current_request_id=request_id if status in OPEN_TASK_STATUSES else "",
                latest_request_id=request_id,
                current_attempt_id=attempt_id if status in OPEN_TASK_STATUSES else "",
                final_response_preview=final_preview,
                error_preview=error_preview,
                blocker_reason=blocker,
            ),
        )
        task = task_store.get_task(task_id)
        if task is not None and task.event_run_id:
            _maybe_create_task_notification(
                task_id=task_id,
                status=status,
                request_id=request_id,
                attempt_id=attempt_id,
                message=blocker or error_preview or final_preview,
                execution_error_detail=execution_error_detail,
            )
        if task is not None and task.event_run_id:
            event_trace = trace
            if trace is not None:
                event_trace = _ensure_scheduled_event_trace_enriched(trace)
                _append_scheduled_event_completed_trace_event(
                    trace=event_trace,
                    event_id=task.event_id,
                    event_run_id=task.event_run_id,
                    status=status,
                )
            _finalize_event_run(task.event_run_id, event_trace)
            event_store.update_run(
                task.event_run_id,
                request_id=request_id,
                task_id=task.task_id,
            )
        else:
            _maybe_create_task_notification(
                task_id=task_id,
                status=status,
                request_id=request_id,
                attempt_id=attempt_id,
                message=blocker or error_preview or final_preview,
                execution_error_detail=execution_error_detail,
            )
        return status

    def _task_record_payload(task: Any, *, include_latest_checkpoint: bool = True) -> dict[str, Any]:
        payload = task.model_dump(mode="json") if hasattr(task, "model_dump") else dict(task or {})
        if include_latest_checkpoint:
            checkpoint = task_store.latest_checkpoint(payload.get("task_id", ""))
            payload["latest_checkpoint"] = checkpoint.model_dump(mode="json") if checkpoint else None
        if str(payload.get("source") or "") == "scheduled_event":
            event_id = str(payload.get("event_id") or "").strip()
            source_event = event_store.get_event(event_id) if event_id else None
            payload["source_event"] = (
                {
                    "exists": True,
                    "event_id": source_event.event_id,
                    "title": source_event.title,
                    "status": source_event.status,
                    "event_kind": source_event.event_kind,
                }
                if source_event is not None
                else {
                    "exists": False,
                    "event_id": event_id,
                    "title": "",
                    "status": "missing",
                    "event_kind": "scheduled",
                }
            )
        return payload

    def _task_attempt_payload(attempt: Any) -> dict[str, Any]:
        return attempt.model_dump(mode="json") if hasattr(attempt, "model_dump") else dict(attempt or {})

    def _task_checkpoint_payload(checkpoint: Any) -> dict[str, Any]:
        return checkpoint.model_dump(mode="json") if hasattr(checkpoint, "model_dump") else dict(checkpoint or {})

    def _mark_task_attempt_waiting_for_auto_approval(
        *,
        attempt_id: str,
        trace: AgentRequestTrace | None,
    ) -> None:
        status = _task_status_from_trace(trace)
        task_store.update_attempt(
            attempt_id,
            status=status,  # type: ignore[arg-type]
            final_response_preview=_task_preview_from_trace(trace),
            error_preview=_task_blocker_from_trace(trace) if status != "completed" else "",
        )

    def _task_auto_approve_enabled(task: Any | None, event: AgentEventRecord | None) -> bool:
        if task is None:
            return False
        if str(getattr(task, "source", "") or "") == "scheduled_event" or str(
            getattr(task, "event_run_id", "") or ""
        ).strip():
            return bool(event is not None and event.auto_approve_confirmations)
        context = dict(getattr(task, "context", {}) or {})
        if context.get("auto_approve_commands") is True:
            return True
        if context.get("auto_approve_commands") is False:
            return False
        return bool(_runtime_controls_snapshot().get("auto_approve_commands"))

    def _auto_approve_durable_task_confirmation(
        *,
        task: Any,
        parent_request_id: str,
        parent_trace: AgentRequestTrace,
    ) -> AgentRequestTrace:
        state = state_store.get(parent_request_id)
        if state is None or not state.confirmation_required or state.confirmation_handled:
            return parent_trace
        try:
            auto_approve_count = int(
                dict(state.context or {}).get("durable_task_auto_approve_count") or 0
            )
        except (TypeError, ValueError):
            auto_approve_count = 0
        if auto_approve_count >= 20:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=parent_request_id,
                    stage="request_received",
                    level="warning",
                    event_type="durable_task.auto_approve_limit_reached",
                    title="Task auto-approval stopped",
                    summary="The durable task reached the auto-approval limit for this run.",
                    detail={
                        "task_id": str(getattr(task, "task_id", "") or ""),
                        "auto_approve_count": auto_approve_count,
                    },
                )
            )
            return parent_trace
        try:
            response = _approve_confirmation_request(
                request_id=parent_request_id,
                approval_context_payload={
                    "auto_approved_confirmation": True,
                    "auto_approve_commands": True,
                    "durable_task_id": str(getattr(task, "task_id", "") or ""),
                    "durable_task_auto_approve_count": auto_approve_count + 1,
                },
                run_inline=False,
            )
        except HTTPException as exc:
            if exc.status_code != 409 or "already handled" not in str(exc.detail).lower():
                raise
            child_request_id = (
                state_store.find_child_request_id(parent_request_id)
                or trace_store.find_child_request_id(parent_request_id)
            )
            if child_request_id:
                return trace_store.get_trace(child_request_id) or parent_trace
            return trace_store.get_trace(parent_request_id) or parent_trace
        approved_request_id = str(response.get("request_id") or "").strip()
        return trace_store.get_trace(approved_request_id) or parent_trace

    def _monitor_task_attempt(task_id: str, attempt_id: str, request_id: str) -> None:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return
        with task_queue_lock:
            if normalized_request_id in monitored_task_requests:
                return
            monitored_task_requests.add(normalized_request_id)

        def _worker() -> None:
            cursor = 0
            try:
                while True:
                    trace = trace_store.get_trace(normalized_request_id)
                    cursor = _persist_task_checkpoints(
                        task_id=task_id,
                        attempt_id=attempt_id,
                        request_id=normalized_request_id,
                        trace=trace,
                        after_id=cursor,
                    )
                    status = _task_status_from_trace(trace)
                    if status != "running":
                        task = task_store.get_task(task_id)
                        event = (
                            event_store.get_event(task.event_id)
                            if task is not None and task.event_id
                            else None
                        )
                        if (
                            status == "awaiting_confirmation"
                            and task is not None
                            and trace is not None
                            and _task_auto_approve_enabled(task, event)
                        ):
                            _mark_task_attempt_waiting_for_auto_approval(
                                attempt_id=attempt_id,
                                trace=trace,
                            )
                            if event is not None and task.event_run_id:
                                _auto_approve_scheduled_event(
                                    event=event,
                                    event_run_id=task.event_run_id,
                                    parent_request_id=normalized_request_id,
                                    parent_trace=trace,
                                )
                            else:
                                _auto_approve_durable_task_confirmation(
                                    task=task,
                                    parent_request_id=normalized_request_id,
                                    parent_trace=trace,
                                )
                            return
                        _finalize_task_attempt_from_trace(
                            task_id=task_id,
                            attempt_id=attempt_id,
                            request_id=normalized_request_id,
                            trace=trace,
                        )
                        if status not in {"awaiting_confirmation", "awaiting_clarification"}:
                            _start_next_queued_task()
                        return
                    if trace is None:
                        time.sleep(0.5)
                        continue
                    try:
                        trace_store.wait_for_events(
                            normalized_request_id,
                            after_id=cursor,
                            timeout_seconds=1.5,
                        )
                    except KeyError:
                        return
            finally:
                with task_queue_lock:
                    monitored_task_requests.discard(normalized_request_id)

        threading.Thread(target=_worker, daemon=True, name=f"agent-task-monitor-{task_id}").start()

    def _start_next_queued_task() -> dict[str, Any] | None:
        with task_queue_lock:
            if task_store.get_open_task() is not None:
                return None
            if _has_active_non_task_request():
                return None
            task = task_store.next_queued_task()
            if task is not None:
                return _start_task_now(task.task_id, trigger="start")
            return None

    def _monitor_active_non_task_request(request_id: str) -> None:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return

        def _worker() -> None:
            cursor = 0
            try:
                while True:
                    with active_non_task_requests_lock:
                        if normalized_request_id not in active_non_task_request_ids:
                            return
                    trace = trace_store.get_trace(normalized_request_id)
                    if not _active_non_task_request_pending(normalized_request_id):
                        break
                    if trace is None:
                        time.sleep(0.5)
                        continue
                    if trace.status in {"completed", "failed", "cancelled"}:
                        time.sleep(0.5)
                        continue
                    for event in list(trace.events or []):
                        cursor = max(cursor, int(getattr(event, "id", 0) or 0))
                    try:
                        trace_store.wait_for_events(
                            normalized_request_id,
                            after_id=cursor,
                            timeout_seconds=1.5,
                        )
                    except KeyError:
                        break
            finally:
                _release_active_non_task_request(normalized_request_id)

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"agent-chat-monitor-{normalized_request_id}",
        ).start()

    def _task_context(task: Any, attempt_id: str) -> dict[str, Any]:
        context = dict(getattr(task, "context", {}) or {})
        context["durable_task_id"] = task.task_id
        context["durable_task_attempt_id"] = attempt_id
        context["durable_task_source"] = task.source
        if task.gateway_id and not str(context.get("gateway_id") or "").strip():
            context["gateway_id"] = task.gateway_id
        if task.event_id:
            context["scheduled_event_id"] = task.event_id
        if task.event_run_id:
            context["scheduled_event_run_id"] = task.event_run_id
        if _background_terminal_context_hint(context):
            gateway_id = str(context.get("gateway_id") or "").strip()
            gateway_record = gateway_store.get(gateway_id) if gateway_id else None
            source = "scheduled_event" if task.source == "scheduled_event" else "durable_task"
            context = _attach_background_terminal(
                context=context,
                gateway_record=gateway_record,
                source=source,
                required=True,
            )
        return context

    def _start_task_now(task_id: str, *, trigger: str = "start") -> dict[str, Any]:
        task = task_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        if task.archived_at:
            raise HTTPException(status_code=409, detail="Archived tasks cannot be started.")
        attempt = task_store.create_attempt(
            task_id=task.task_id,
            trigger=trigger,  # type: ignore[arg-type]
            status="running",
        )
        task_context = _task_context(task, attempt.attempt_id)
        task_llm_model = str(task_context.get("llm_model") or "").strip() or None
        task_store.update_task(
            task.task_id,
            AgentTaskUpdate(
                status="running",
                current_attempt_id=attempt.attempt_id,
                current_request_id="",
                final_response_preview="",
                error_preview="",
                blocker_reason="",
            ),
        )
        if task.event_run_id:
            event_store.update_run(task.event_run_id, task_id=task.task_id, status="running")
        try:
            response = _submit_agent_request(
                AgentRequestPayload(
                    prompt=task.prompt,
                    context=task_context,
                    agent_mode=task.agent_mode,
                    conversation_id=task.conversation_id or None,
                    llm_model=task_llm_model,
                )
            )
        except Exception as exc:
            task_store.update_attempt(
                attempt.attempt_id,
                status="failed",
                error_preview=str(exc),
            )
            task_store.update_task(
                task.task_id,
                AgentTaskUpdate(
                    status="failed",
                    current_attempt_id="",
                    current_request_id="",
                    error_preview=str(exc),
                    blocker_reason=str(exc),
                ),
            )
            _maybe_create_task_notification(
                task_id=task.task_id,
                status="failed",
                attempt_id=attempt.attempt_id,
                message=str(exc),
            )
            _start_next_queued_task()
            raise
        request_id = str(response.get("request_id") or "").strip()
        conversation_id = str(response.get("conversation_id") or task.conversation_id or "").strip()
        task_store.update_attempt(
            attempt.attempt_id,
            request_id=request_id,
            status="running",
        )
        task_store.update_task(
            task.task_id,
            AgentTaskUpdate(
                status="running",
                current_request_id=request_id,
                latest_request_id=request_id,
                current_attempt_id=attempt.attempt_id,
                conversation_id=conversation_id,
            ),
        )
        if task.event_run_id:
            event_store.update_run(
                task.event_run_id,
                request_id=request_id,
                task_id=task.task_id,
                status="running",
            )
        if request_id:
            try:
                if task.source == "scheduled_event" and task.event_id and task.event_run_id:
                    event = event_store.get_event(task.event_id)
                    event_title = str(getattr(event, "title", "") or task.title or "Scheduled event")
                    trace_store.append_event(
                        AgentTraceEvent(
                            request_id=request_id,
                            stage="request_received",
                            level="info",
                            event_type="scheduled_event.started",
                            title="Scheduled event started",
                            summary=f"Event `{event_title}` invoked this request.",
                            detail={
                                "event_id": task.event_id,
                                "event_run_id": task.event_run_id,
                                "task_id": task.task_id,
                            },
                        )
                    )
                    if task_context.get("scheduled_event_background_terminal"):
                        trace_store.append_event(
                            AgentTraceEvent(
                                request_id=request_id,
                                stage="request_received",
                                level="info",
                                event_type="scheduled_event.background_terminal.created",
                                title="Background terminal attached",
                                summary="A PTY-backed terminal was created for scheduled event terminal actions.",
                                detail={
                                    "event_id": task.event_id,
                                    "event_run_id": task.event_run_id,
                                    "task_id": task.task_id,
                                    "terminal_session_id": str(
                                        task_context.get("terminal_session_id") or ""
                                    ),
                                    "terminal_cwd": str(task_context.get("terminal_cwd") or ""),
                                },
                            )
                        )
                    elif task_context.get("scheduled_event_background_terminal_error"):
                        trace_store.append_event(
                            AgentTraceEvent(
                                request_id=request_id,
                                stage="request_received",
                                level="warning",
                                event_type="scheduled_event.background_terminal.failed",
                                title="Background terminal unavailable",
                                summary=(
                                    "The scheduled event could not create a background terminal "
                                    "for terminal actions."
                                ),
                                detail={
                                    "event_id": task.event_id,
                                    "event_run_id": task.event_run_id,
                                    "task_id": task.task_id,
                                    "error": str(
                                        task_context.get(
                                            "scheduled_event_background_terminal_error"
                                        )
                                        or ""
                                    ),
                                },
                            )
                        )
                trace_store.append_event(
                    AgentTraceEvent(
                        request_id=request_id,
                        stage="request_received",
                        level="info",
                        event_type="durable_task.started",
                        title="Durable task started",
                        summary=f"Task `{task.title}` started.",
                        detail={
                            "task_id": task.task_id,
                            "attempt_id": attempt.attempt_id,
                            "trigger": trigger,
                        },
                    )
                )
                if task_context.get("durable_task_background_terminal"):
                    trace_store.append_event(
                        AgentTraceEvent(
                            request_id=request_id,
                            stage="request_received",
                            level="info",
                            event_type="durable_task.background_terminal.created",
                            title="Background terminal attached",
                            summary="A PTY-backed terminal was created for durable task terminal actions.",
                            detail={
                                "task_id": task.task_id,
                                "attempt_id": attempt.attempt_id,
                                "terminal_session_id": str(
                                    task_context.get("terminal_session_id") or ""
                                ),
                                "terminal_cwd": str(task_context.get("terminal_cwd") or ""),
                            },
                        )
                    )
                elif task_context.get("durable_task_background_terminal_error"):
                    trace_store.append_event(
                        AgentTraceEvent(
                            request_id=request_id,
                            stage="request_received",
                            level="warning",
                            event_type="durable_task.background_terminal.failed",
                            title="Background terminal unavailable",
                            summary=(
                                "The durable task could not create a background terminal "
                                "for terminal actions."
                            ),
                            detail={
                                "task_id": task.task_id,
                                "attempt_id": attempt.attempt_id,
                                "error": str(
                                    task_context.get("durable_task_background_terminal_error")
                                    or ""
                                ),
                            },
                        )
                    )
            except KeyError:
                pass
            _monitor_task_attempt(task.task_id, attempt.attempt_id, request_id)
        return {
            "status": "running",
            "task": _task_record_payload(task_store.get_task(task.task_id)),
            "attempt": _task_attempt_payload(task_store.get_attempt(attempt.attempt_id)),
            **response,
        }

    def _queue_or_start_task(task_id: str, *, trigger: str = "start") -> dict[str, Any]:
        task = task_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        if task.status in OPEN_TASK_STATUSES:
            return {"status": task.status, "task": _task_record_payload(task)}
        with task_queue_lock:
            open_task = task_store.get_open_task()
            waiting_on_chat = _has_active_non_task_request()
            if (open_task is not None and open_task.task_id != task.task_id) or waiting_on_chat:
                queued = task_store.update_task(
                    task.task_id,
                    AgentTaskUpdate(
                        status="queued",
                        current_request_id="",
                        current_attempt_id="",
                        final_response_preview="" if trigger in {"retry", "resume"} else None,
                        error_preview="" if trigger in {"retry", "resume"} else None,
                        blocker_reason=(
                            "Waiting for the active chat request to finish."
                            if waiting_on_chat
                            else ("" if trigger in {"retry", "resume"} else None)
                        ),
                    ),
                )
                return {"status": "queued", "task": _task_record_payload(queued)}
            next_task = task_store.next_queued_task()
            if next_task is not None and next_task.task_id != task.task_id:
                queued = task_store.update_task(
                    task.task_id,
                    AgentTaskUpdate(
                        status="queued",
                        current_request_id="",
                        current_attempt_id="",
                        final_response_preview="" if trigger in {"retry", "resume"} else None,
                        error_preview="" if trigger in {"retry", "resume"} else None,
                        blocker_reason="" if trigger in {"retry", "resume"} else None,
                    ),
                )
                started = _start_task_now(next_task.task_id, trigger="start")
                return {
                    "status": "queued",
                    "task": _task_record_payload(queued),
                    "started_task": started.get("task"),
                    "started_request_id": started.get("request_id", ""),
                    "started_stream_url": started.get("stream_url", ""),
                }
            return _start_task_now(task.task_id, trigger=trigger)

    monitor_manager = AgentMonitorManager(
        monitor_store=monitor_store,
        event_store=event_store,
        task_store=task_store,
        gateway_store=gateway_store,
        gateway_client_provider=lambda: getattr(
            getattr(agent_runtime, "execution_engine", None),
            "gateway_client",
            None,
        ),
        gateway_context_resolver=lambda context: _resolve_agent_gateway_selection(
            context,
            gateway_store,
        ),
        terminal_cwd_resolver=lambda gateway_record: _gateway_terminal_cwd(settings, gateway_record),
        task_starter=lambda task_id: _queue_or_start_task(task_id, trigger="start"),
        llm_client_provider=lambda: getattr(agent_runtime, "llm_client", None),
        llm_monitor_planner_enabled=bool(settings.llm_monitor_planner_enabled),
        llm_trigger_judge_enabled=bool(settings.llm_trigger_judge_enabled),
        judge_min_interval_seconds=max(1, int(settings.monitor_judge_min_interval_seconds or 5)),
        judge_output_preview_cap=max(500, int(settings.monitor_judge_output_preview_cap or 4000)),
        max_inferred_monitor_duration_seconds=max(1, int(settings.max_inferred_monitor_duration_seconds or 300)),
    )
    ctx.monitor_manager = monitor_manager
    app.state.agent_monitor_manager = monitor_manager
    try:
        setattr(agent_runtime, "monitor_manager", monitor_manager)
    except Exception:
        pass

    def _link_task_followup_request(
        *,
        parent_request_id: str,
        child_request_id: str,
        context: dict[str, Any],
        trigger: str,
    ) -> None:
        task_id = str(context.get("durable_task_id") or "").strip()
        if not task_id:
            return
        task = task_store.get_task(task_id)
        if task is None:
            return
        attempt = task_store.create_attempt(
            task_id=task_id,
            trigger=trigger,  # type: ignore[arg-type]
            request_id=child_request_id,
            parent_request_id=parent_request_id,
            status="running",
        )
        context["durable_task_attempt_id"] = attempt.attempt_id
        task_store.update_task(
            task_id,
            AgentTaskUpdate(
                status="running",
                current_request_id=child_request_id,
                latest_request_id=child_request_id,
                current_attempt_id=attempt.attempt_id,
                blocker_reason="",
            ),
        )
        if task.event_run_id:
            event_store.update_run(
                task.event_run_id,
                request_id=child_request_id,
                parent_request_id=parent_request_id,
                task_id=task_id,
                status="running",
                completed_at="",
            )
        _monitor_task_attempt(task_id, attempt.attempt_id, child_request_id)

    def _reconcile_open_tasks() -> None:
        for task in task_store.list_tasks(limit=500, include_archived=False):
            if task.status not in OPEN_TASK_STATUSES:
                continue
            request_id = task.current_request_id or task.latest_request_id
            attempt_id = task.current_attempt_id
            if not request_id or not attempt_id:
                continue
            trace = trace_store.get_trace(request_id)
            if trace is None:
                continue
            _persist_task_checkpoints(
                task_id=task.task_id,
                attempt_id=attempt_id,
                request_id=request_id,
                trace=trace,
            )
            status = _task_status_from_trace(trace)
            if status != task.status:
                event = event_store.get_event(task.event_id) if task.event_id else None
                if status == "awaiting_confirmation" and _task_auto_approve_enabled(task, event):
                    _mark_task_attempt_waiting_for_auto_approval(
                        attempt_id=attempt_id,
                        trace=trace,
                    )
                    if event is not None and task.event_run_id:
                        _auto_approve_scheduled_event(
                            event=event,
                            event_run_id=task.event_run_id,
                            parent_request_id=request_id,
                            parent_trace=trace,
                        )
                    else:
                        _auto_approve_durable_task_confirmation(
                            task=task,
                            parent_request_id=request_id,
                            parent_trace=trace,
                        )
                    continue
                _finalize_task_attempt_from_trace(
                    task_id=task.task_id,
                    attempt_id=attempt_id,
                    request_id=request_id,
                    trace=trace,
                )

    def _monitor_record_payload(monitor: Any, *, include_latest_observation: bool = True) -> dict[str, Any]:
        return monitor_manager.record_payload(
            monitor,
            include_latest_observation=include_latest_observation,
        )

    def _monitor_observation_payload(observation: Any) -> dict[str, Any]:
        return monitor_manager.observation_payload(observation)

    def _monitor_draft_payload(draft: AgentMonitorDraftResponse) -> dict[str, Any]:
        return draft.model_dump(mode="json")

    def _start_monitor(monitor_id: str) -> dict[str, Any]:
        try:
            return monitor_manager.start_monitor(monitor_id)
        except AgentMonitorNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AgentMonitorConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _cancel_monitor(monitor_id: str, *, status: str = "cancelled") -> dict[str, Any]:
        try:
            return monitor_manager.cancel_monitor(monitor_id, status=status)
        except AgentMonitorNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    for _name in (
        '_cancel_agent_request',
        '_task_status_from_trace',
        '_task_preview_from_trace',
        '_task_blocker_from_trace',
        '_task_checkpoint_detail',
        '_persist_task_checkpoints',
        '_context_is_durable_task',
        '_has_active_non_task_request',
        '_active_non_task_child_request_id',
        '_active_non_task_request_pending',
        '_release_active_non_task_request',
        '_track_active_non_task_request',
        '_task_notification_level',
        '_task_notification_body',
        '_maybe_create_task_notification',
        '_finalize_task_attempt_from_trace',
        '_task_record_payload',
        '_task_attempt_payload',
        '_task_checkpoint_payload',
        '_mark_task_attempt_waiting_for_auto_approval',
        '_task_auto_approve_enabled',
        '_auto_approve_durable_task_confirmation',
        '_monitor_task_attempt',
        '_worker',
        '_start_next_queued_task',
        '_monitor_active_non_task_request',
        '_worker',
        '_task_context',
        '_start_task_now',
        '_queue_or_start_task',
        '_link_task_followup_request',
        '_reconcile_open_tasks',
        '_monitor_record_payload',
        '_monitor_observation_payload',
        '_monitor_draft_payload',
        '_start_monitor',
        '_cancel_monitor'
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
