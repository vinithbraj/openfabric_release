"""Install scheduled event launch and auto-approval helpers."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared_imports import *


def install_scheduled_event_helpers(ctx: Any) -> None:
    """Install scheduled event launch and auto-approval helpers."""


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

    def _run_scheduled_event_request(
        *,
        event: AgentEventRecord,
        event_run_id: str,
        request_id: str,
        request_context: dict[str, Any],
        cancel_event: threading.Event,
        confirmation: bool = False,
        planning_trace: Any = None,
    ) -> AgentRequestTrace | None:
        state_context = dict(request_context)
        state_context.pop("operator_conversation_context", None)
        state_store.create(
            request_id=request_id,
            prompt=event.prompt,
            context=state_context,
            conversation_id=str(request_context.get("conversation_id") or "") or None,
        )
        _run_agent_request(
            request_id=request_id,
            prompt=event.prompt,
            request_context=request_context,
            settings=settings,
            base_runtime=agent_runtime,
            store=trace_store,
            state_store=state_store,
            conversation_store=conversation_store,
            confirmation=confirmation,
            planning_trace=planning_trace,
            cancel_event=cancel_event,
            learning_ledger_store=learning_ledger_store,
        )
        trace = trace_store.get_trace(request_id)
        return _ensure_scheduled_event_trace_enriched(trace)

    def _approve_confirmation_request(
        *,
        request_id: str,
        approval_context_payload: dict[str, Any] | None = None,
        run_inline: bool = False,
    ) -> dict[str, Any]:
        """Continue one confirmation-gated request through the shared approval path."""

        _trace_or_404(trace_store, request_id)
        state = state_store.get(request_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Confirmation state not found")
        if not state.confirmation_required:
            raise HTTPException(status_code=409, detail="Request is not waiting for confirmation")
        if state.confirmation_handled:
            raise HTTPException(status_code=409, detail="Confirmation was already handled")

        state_store.mark_handled(request_id)
        replay_context = dict(state.context or {})
        _drop_removed_operator_context_keys(replay_context)
        raw_approval_context = dict(approval_context_payload or {})
        followup_context = _request_followup_context(
            state_context=replay_context,
            followup_context=raw_approval_context,
        )
        auto_approved_confirmation = bool(
            followup_context.get("auto_approved_confirmation")
            or followup_context.get("auto_approve_commands")
        )
        followup_context.update(_resolve_agent_gateway_selection(followup_context, gateway_store))
        approval_context = _apply_trusted_terminal_context(followup_context, terminal_store)
        replay_context.update(
            {
                key: approval_context[key]
                for key in (
                    "terminal_session_id",
                    "terminal_cwd",
                    "execute_in_terminal",
                    "gateway_id",
                    "gateway_node",
                    "gateway_url",
                    "gateway_endpoints",
                )
                if key in approval_context
            }
        )
        backend_runtime_snapshot = _apply_backend_runtime_snapshot(replay_context)
        if not str(replay_context.get("agent_clarification_mode") or "").strip():
            replay_context["agent_clarification_mode"] = backend_runtime_snapshot["agent_clarification_mode"]
        if "operator_workspace_cwd_guard_enabled" not in replay_context:
            replay_context["operator_workspace_cwd_guard_enabled"] = backend_runtime_snapshot[
                "operator_workspace_cwd_guard_enabled"
            ]
        _apply_runtime_repair_attempt_context(replay_context)
        if online_ai_check_requested_from_context(replay_context):
            replay_context[ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY] = True
        if auto_approved_confirmation:
            replay_context["auto_approved_confirmation"] = True
        for scheduled_key in (
            "scheduled_event_id",
            "scheduled_event_run_id",
            "scheduled_event_auto_approve_count",
        ):
            if scheduled_key in followup_context:
                replay_context[scheduled_key] = followup_context[scheduled_key]
        for durable_task_key in (
            "durable_task_id",
            "durable_task_attempt_id",
            "durable_task_auto_approve_count",
        ):
            if durable_task_key in followup_context:
                replay_context[durable_task_key] = followup_context[durable_task_key]
        gateway_record = gateway_store.get(str(replay_context.get("gateway_id") or "").strip())
        background_terminal_required = (
            auto_approved_confirmation
            and _confirmation_actions_require_terminal(state.confirmation_actions)
        )
        background_terminal_source = (
            "scheduled_event"
            if str(replay_context.get("scheduled_event_run_id") or "").strip()
            else "durable_task"
            if str(replay_context.get("durable_task_id") or "").strip()
            else "request"
        )
        replay_context = _attach_background_terminal(
            context=replay_context,
            gateway_record=gateway_record,
            source=background_terminal_source,
            required=background_terminal_required,
        )
        llm_context_window_tokens, _llm_context_window_source = _agent_context_window_tokens(
            settings,
            replay_context,
        )
        replay_context["llm_context_window_tokens"] = llm_context_window_tokens
        _apply_command_allowlist_context(replay_context)

        approved = trace_store.create_request(state.prompt)
        state_store.link_continuation(request_id, approved.request_id)
        _link_task_followup_request(
            parent_request_id=request_id,
            child_request_id=approved.request_id,
            context=replay_context,
            trigger="confirmation",
        )
        durable_task_id = str(replay_context.get("durable_task_id") or "").strip()
        scheduled_event_run_id = str(replay_context.get("scheduled_event_run_id") or "").strip()
        scheduled_event_id = str(replay_context.get("scheduled_event_id") or "").strip()
        if scheduled_event_run_id:
            event_store.update_run(
                scheduled_event_run_id,
                request_id=approved.request_id,
                parent_request_id=request_id,
                status="running",
            )
        cancel_event = threading.Event()
        with cancel_events_lock:
            cancel_events[approved.request_id] = cancel_event
        state_store.create(
            request_id=approved.request_id,
            prompt=state.prompt,
            context=replay_context,
            parent_request_id=request_id,
            conversation_id=state.conversation_id,
        )
        trace_store.append_event(
            AgentTraceEvent(
                request_id=approved.request_id,
                stage="request_received",
                level="info",
                event_type=(
                    "confirmation.auto_approved"
                    if auto_approved_confirmation
                    else "confirmation.approved"
                ),
                title=(
                    "Confirmation auto-approved"
                    if auto_approved_confirmation
                    else "Confirmation approved"
                ),
                summary=(
                    "Auto-Approve Commands submitted the visible approval prompt."
                    if auto_approved_confirmation
                    else "The approved request is continuing from the saved validated plan."
                ),
                detail={
                    "parent_request_id": request_id,
                    "confirmation_action_count": len(state.confirmation_actions),
                    "auto_approved": auto_approved_confirmation,
                },
            )
        )
        if durable_task_id and auto_approved_confirmation:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=approved.request_id,
                    stage="request_received",
                    level="info",
                    event_type="durable_task.auto_approved",
                    title="Task auto-approved",
                    summary="The background task auto-approved the visible confirmation prompt.",
                    detail={
                        "task_id": durable_task_id,
                        "parent_request_id": request_id,
                        "confirmation_action_count": len(state.confirmation_actions),
                    },
                )
            )
        if scheduled_event_run_id and auto_approved_confirmation:
            scheduled_event = event_store.get_event(scheduled_event_id)
            if scheduled_event is not None:
                _create_scheduled_event_auto_approved_notification(
                    event=scheduled_event,
                    event_run_id=scheduled_event_run_id,
                    request_id=approved.request_id,
                    parent_request_id=request_id,
                    confirmation_action_count=len(state.confirmation_actions),
                )
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=approved.request_id,
                    stage="request_received",
                    level="info",
                    event_type="scheduled_event.auto_approved",
                    title="Scheduled event auto-approved",
                    summary="The scheduled event auto-approved the visible confirmation prompt.",
                    detail={
                        "event_id": scheduled_event_id,
                        "event_run_id": scheduled_event_run_id,
                        "parent_request_id": request_id,
                        "confirmation_action_count": len(state.confirmation_actions),
                    },
                )
            )

        def _run_confirmed_request_worker() -> None:
            _run_agent_request(
                request_id=approved.request_id,
                prompt=state.prompt,
                request_context=replay_context,
                settings=settings,
                base_runtime=agent_runtime,
                store=trace_store,
                state_store=state_store,
                conversation_store=conversation_store,
                confirmation=True,
                planning_trace=state.planning_trace,
                cancel_event=cancel_event,
                learning_ledger_store=learning_ledger_store,
            )
            if not scheduled_event_run_id:
                return
            trace = trace_store.get_trace(approved.request_id)
            scheduled_event = event_store.get_event(scheduled_event_id) if scheduled_event_id else None
            if (
                scheduled_event is not None
                and scheduled_event.auto_approve_confirmations
                and _event_run_status_from_trace(trace) == "awaiting_confirmation"
                and trace is not None
            ):
                _auto_approve_scheduled_event(
                    event=scheduled_event,
                    event_run_id=scheduled_event_run_id,
                    parent_request_id=approved.request_id,
                    parent_trace=trace,
                )
                return
            trace = _ensure_scheduled_event_trace_enriched(trace)
            status = _event_run_status_from_trace(trace)
            _append_scheduled_event_completed_trace_event(
                trace=trace,
                event_id=scheduled_event_id,
                event_run_id=scheduled_event_run_id,
                status=status,
                parent_request_id=request_id,
            )
            _finalize_event_run(scheduled_event_run_id, trace)

        if run_inline:
            _run_confirmed_request_worker()
        else:
            worker = threading.Thread(
                target=_run_confirmed_request_worker,
                daemon=True,
            )
            _track_active_non_task_request(approved.request_id, replay_context)
            worker.start()
        response = {
            "status": "approved",
            "request_id": approved.request_id,
            "stream_url": f"/api/agent/stream/{approved.request_id}",
            "trace_url": f"/api/agent/trace/{approved.request_id}",
            "llm_context_window_tokens": llm_context_window_tokens,
            "llm_context_window_source": _llm_context_window_source,
        }
        if state.conversation_id:
            response["conversation_id"] = state.conversation_id
        return response

    def _auto_approve_scheduled_event(
        *,
        event: AgentEventRecord,
        event_run_id: str,
        parent_request_id: str,
        parent_trace: AgentRequestTrace,
    ) -> AgentRequestTrace:
        state = state_store.get(parent_request_id)
        if state is None or not state.confirmation_required or state.confirmation_handled:
            return parent_trace
        try:
            auto_approve_count = int(
                dict(state.context or {}).get("scheduled_event_auto_approve_count") or 0
            )
        except (TypeError, ValueError):
            auto_approve_count = 0
        if auto_approve_count >= 20:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=parent_request_id,
                    stage="request_received",
                    level="warning",
                    event_type="scheduled_event.auto_approve_limit_reached",
                    title="Scheduled event auto-approval stopped",
                    summary="The scheduled event reached the auto-approval limit for this run.",
                    detail={
                        "event_id": event.event_id,
                        "event_run_id": event_run_id,
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
                    "scheduled_event_id": event.event_id,
                    "scheduled_event_run_id": event_run_id,
                    "scheduled_event_auto_approve_count": auto_approve_count + 1,
                },
                run_inline=True,
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

    def _run_scheduled_event_worker(event: AgentEventRecord, event_run_id: str, request_id: str) -> None:
        cancel_event = threading.Event()
        with cancel_events_lock:
            cancel_events[request_id] = cancel_event
        request_context = _event_context(event)
        request_context["scheduled_event_run_id"] = event_run_id
        trace_store.append_event(
            AgentTraceEvent(
                request_id=request_id,
                stage="request_received",
                level="info",
                event_type="scheduled_event.started",
                title="Scheduled event started",
                summary=f"Event `{event.title}` invoked this request.",
                detail={"event_id": event.event_id, "event_run_id": event_run_id},
            )
        )
        if request_context.get("scheduled_event_background_terminal"):
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=request_id,
                    stage="request_received",
                    level="info",
                    event_type="scheduled_event.background_terminal.created",
                    title="Background terminal attached",
                    summary="A PTY-backed terminal was created for scheduled event terminal actions.",
                    detail={
                        "event_id": event.event_id,
                        "event_run_id": event_run_id,
                        "terminal_session_id": str(request_context.get("terminal_session_id") or ""),
                        "terminal_cwd": str(request_context.get("terminal_cwd") or ""),
                    },
                )
            )
        elif request_context.get("scheduled_event_background_terminal_error"):
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=request_id,
                    stage="request_received",
                    level="warning",
                event_type="scheduled_event.background_terminal.failed",
                title="Background terminal unavailable",
                summary="The scheduled event could not create a background terminal for terminal actions.",
                    detail={
                        "event_id": event.event_id,
                        "event_run_id": event_run_id,
                        "error": str(request_context.get("scheduled_event_background_terminal_error") or ""),
                    },
                )
            )
        if not str(request_context.get("conversation_id") or "").strip():
            request_context["conversation_id"] = conversation_store.create(event.prompt)
        trace = _run_scheduled_event_request(
            event=event,
            event_run_id=event_run_id,
            request_id=request_id,
            request_context=request_context,
            cancel_event=cancel_event,
        )
        auto_approved_run = False
        if (
            _event_run_status_from_trace(trace) == "awaiting_confirmation"
            and event.auto_approve_confirmations
            and trace is not None
        ):
            trace = _auto_approve_scheduled_event(
                event=event,
                event_run_id=event_run_id,
                parent_request_id=request_id,
                parent_trace=trace,
            )
            auto_approved_run = True
        if auto_approved_run:
            return
        status = _event_run_status_from_trace(trace)
        _append_scheduled_event_completed_trace_event(
            trace=trace,
            event_id=event.event_id,
            event_run_id=event_run_id,
            status=status,
        )
        _finalize_event_run(event_run_id, trace)

    def _launch_scheduled_event(event: AgentEventRecord, scheduled_for: str) -> str:
        if _event_should_run_as_notification(event):
            run = event_store.create_run(
                event_id=event.event_id,
                scheduled_for=scheduled_for,
                status="completed",
            )
            message = str(event.notification_message or event.prompt or event.title).strip() or "Reminder"
            event_store.update_run(
                run.event_run_id,
                status="completed",
                final_response_preview=message,
                completed_at=utc_now_iso(),
            )
            if event.event_kind != "todo":
                _create_direct_event_notification(event=event, event_run_id=run.event_run_id)
            return ""
        run = event_store.create_run(
            event_id=event.event_id,
            scheduled_for=scheduled_for,
            status="queued",
        )
        request_context = _event_context(event)
        request_context["scheduled_event_run_id"] = run.event_run_id
        task = task_store.create_task(
            AgentTaskCreate(
                prompt=event.prompt,
                title=event.title,
                source="scheduled_event",
                status="queued",
                agent_mode=str(request_context.get("agent_mode") or "llm_operator"),
                conversation_id=str(request_context.get("conversation_id") or ""),
                gateway_id=str(request_context.get("gateway_id") or ""),
                event_id=event.event_id,
                event_run_id=run.event_run_id,
                context=request_context,
            )
        )
        event_store.update_run(run.event_run_id, task_id=task.task_id, status="queued")
        response = _queue_or_start_task(task.task_id, trigger="schedule")
        updated_run = event_store.get_run(run.event_run_id)
        return str(response.get("request_id") or getattr(updated_run, "request_id", "") or "")

    if settings.agent_events_enabled:
        app.state.agent_event_scheduler = AgentEventScheduler(
            event_store,
            _launch_scheduled_event,
            poll_seconds=settings.agent_events_poll_seconds,
            before_tick_callback=_reconcile_all_event_runs,
            enabled_callback=_events_enabled,
        )

    for _name in (
        '_run_scheduled_event_request',
        '_approve_confirmation_request',
        '_run_confirmed_request_worker',
        '_auto_approve_scheduled_event',
        '_run_scheduled_event_worker',
        '_launch_scheduled_event'
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
