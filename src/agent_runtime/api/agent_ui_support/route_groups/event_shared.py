"""Install scheduled event, run reconciliation, and notification helpers."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared_imports import *


def install_event_helpers(ctx: Any) -> None:
    """Install scheduled event, run reconciliation, and notification helpers."""


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

    def _event_macro_context_from(context: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict(context or {})
        macro_context: dict[str, Any] = {}
        private_macros = raw.get(USER_MACRO_PRIVATE_CONTEXT_KEY)
        if isinstance(private_macros, list) and private_macros:
            macro_context[USER_MACRO_PRIVATE_CONTEXT_KEY] = [
                dict(item) for item in private_macros if isinstance(item, dict)
            ]
        summaries = raw.get(USER_MACRO_SUMMARY_CONTEXT_KEY)
        if isinstance(summaries, list) and summaries:
            macro_context[USER_MACRO_SUMMARY_CONTEXT_KEY] = [
                dict(item) for item in summaries if isinstance(item, dict)
            ]
        return macro_context

    def _apply_event_extraction_hint_context(context: dict[str, Any]) -> dict[str, Any]:
        updated = dict(context or {})
        summaries = updated.get(USER_MACRO_SUMMARY_CONTEXT_KEY)
        if not isinstance(summaries, list):
            return updated
        filtered: list[dict[str, Any]] = []
        hint = ""
        for item in summaries:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip().lower()
            action_type = str(item.get("event_action_type") or "").strip().lower()
            if kind == RUNLATER_MACRO_KIND or action_type == "agent_prompt":
                hint = "agent_prompt"
                continue
            if kind in {REMIND_MACRO_KIND, TODO_MACRO_KIND} or action_type == "notification":
                hint = "notification"
                continue
            filtered.append(dict(item))
        if filtered:
            updated[USER_MACRO_SUMMARY_CONTEXT_KEY] = filtered
        else:
            updated.pop(USER_MACRO_SUMMARY_CONTEXT_KEY, None)
        if hint:
            updated[SCHEDULED_EVENT_EXTRACTION_HINT_CONTEXT_KEY] = hint
        return updated

    def _strip_event_macro_context(context: dict[str, Any] | None) -> dict[str, Any]:
        clean = dict(context or {})
        clean.pop(USER_MACRO_PRIVATE_CONTEXT_KEY, None)
        clean.pop(USER_MACRO_SUMMARY_CONTEXT_KEY, None)
        clean.pop(SCHEDULED_EVENT_MACRO_REF_CONTEXT_KEY, None)
        return clean

    def _event_context_for_api(
        context: dict[str, Any] | None,
        *,
        keep_pending_ref: bool = False,
    ) -> dict[str, Any]:
        public = dict(context or {})
        public.pop(USER_MACRO_PRIVATE_CONTEXT_KEY, None)
        if not keep_pending_ref:
            public.pop(SCHEDULED_EVENT_MACRO_REF_CONTEXT_KEY, None)
        return public

    def _stash_event_macro_context_for_draft(context: dict[str, Any] | None) -> dict[str, Any]:
        public = _event_context_for_api(context, keep_pending_ref=True)
        macro_context = _event_macro_context_from(context)
        if macro_context.get(USER_MACRO_PRIVATE_CONTEXT_KEY):
            ref = new_id("macro")
            with pending_event_macro_contexts_lock:
                pending_event_macro_contexts[ref] = macro_context
            public[SCHEDULED_EVENT_MACRO_REF_CONTEXT_KEY] = ref
        return public

    def _resolve_pending_event_macro_context(context: dict[str, Any] | None) -> dict[str, Any]:
        resolved = dict(context or {})
        ref = str(resolved.pop(SCHEDULED_EVENT_MACRO_REF_CONTEXT_KEY, "") or "").strip()
        if ref:
            with pending_event_macro_contexts_lock:
                macro_context = pending_event_macro_contexts.pop(ref, None)
            if macro_context:
                resolved.update(macro_context)
        return resolved

    def _prompt_has_sanitized_typein_marker(prompt: str) -> bool:
        return bool(
            re.search(
                r'\btypein\s*"\[(?:provided|redacted)\]"',
                str(prompt or ""),
                flags=re.IGNORECASE,
            )
        )

    def _prepare_event_prompt_and_context(
        prompt: str,
        context: dict[str, Any] | None,
        *,
        preserve_existing_context_macros: bool,
    ) -> tuple[str, dict[str, Any]]:
        resolved_context = _resolve_pending_event_macro_context(context)
        existing_macro_context = _event_macro_context_from(resolved_context)
        base_context = _strip_event_macro_context(resolved_context)
        sanitized_prompt, parsed_macro_context, _macro_detail = _prepare_user_macro_prompt(
            prompt,
            preserve_checkonline_hint=True,
            parameter_store=_parameter_store_for_macros(settings, agent_runtime),
        )
        if parsed_macro_context:
            base_context.update(parsed_macro_context)
        elif existing_macro_context and (
            preserve_existing_context_macros or _prompt_has_sanitized_typein_marker(sanitized_prompt)
        ):
            base_context.update(existing_macro_context)
        base_context = _apply_event_extraction_hint_context(base_context)
        return sanitized_prompt, base_context

    def _event_record_api_payload(event: AgentEventRecord) -> dict[str, Any]:
        payload = event.model_dump(mode="json")
        payload["context"] = _event_context_for_api(payload.get("context"))
        return payload

    def _event_draft_response_api_payload(draft: AgentEventDraftResponse) -> dict[str, Any]:
        payload = draft.model_dump(mode="json")
        drafts = payload.get("drafts")
        if isinstance(drafts, list):
            for item in drafts:
                if isinstance(item, dict):
                    item["context"] = _stash_event_macro_context_for_draft(item.get("context"))
        return payload

    def _event_create_payload_from_draft(draft: AgentEventDraft) -> AgentEventCreate:
        return AgentEventCreate(
            title=draft.title or _event_title_from_prompt(draft.notification_message or draft.prompt),
            prompt=draft.prompt or draft.notification_message,
            status="active",
            schedule_type=draft.schedule_type,
            event_kind=draft.event_kind,
            interval_seconds=draft.interval_seconds,
            timezone=draft.timezone or "UTC",
            next_run_at=draft.next_run_at or "",
            context=dict(draft.context or {}),
            auto_approve_confirmations=draft.auto_approve_confirmations,
            action_type=draft.action_type,
            notification_message=draft.notification_message,
            notify_on=list(draft.notify_on or DEFAULT_EVENT_NOTIFY_ON),
        )

    def _event_schedule_phrase(event: AgentEventRecord) -> str:
        if event.event_kind == "todo":
            return "Todo (no schedule)"
        phrase = ""
        if event.schedule_type == "once":
            phrase = str(
                dict(event.context or {}).get(SCHEDULED_EVENT_SCHEDULE_SUMMARY_CONTEXT_KEY) or ""
            ).strip()
        if not phrase:
            phrase = _event_interval_phrase(event.interval_seconds, schedule_type=event.schedule_type)
        return phrase[:1].lower() + phrase[1:]

    def _saved_events_message(events: list[AgentEventRecord]) -> str:
        if not events:
            return "No scheduled events were saved."
        if len(events) == 1:
            event = events[0]
            if event.event_kind == "todo":
                subject = event.notification_message or event.prompt or event.title
                return f"Saved todo: {subject}."
            label = "reminder" if event.action_type == "notification" else "scheduled event"
            subject = event.notification_message or event.prompt or event.title
            return f"Saved {label}: {subject} {_event_schedule_phrase(event)}."
        labels = [
            (
                f"{event.title or event.notification_message or event.prompt} (Todo)"
                if event.event_kind == "todo"
                else f"{event.title or event.notification_message or event.prompt} ({_event_schedule_phrase(event)})"
            )
            for event in events
        ]
        return f"Saved {len(events)} scheduled events: " + "; ".join(labels) + "."

    def _event_needs_detail_message(draft: AgentEventDraftResponse) -> str:
        missing = [str(item) for item in draft.missing_details if str(item).strip()]
        if missing:
            return "I need more detail before saving this scheduled event: " + ", ".join(missing) + "."
        return (
            "I need a supported schedule before saving this event. Try a phrase like "
            "`after 10 seconds`, `every hour`, or `on 6/01/2026`."
        )

    def _event_has_typein_macro(context: dict[str, Any] | None) -> bool:
        raw = dict(context or {}).get(USER_MACRO_PRIVATE_CONTEXT_KEY)
        if not isinstance(raw, list):
            return False
        return any(
            isinstance(item, dict)
            and str(item.get("kind") or "").strip().lower() == "typein"
            and str(item.get("value") or "")
            for item in raw
        )

    def _event_run_status_from_trace(trace: AgentRequestTrace | None) -> str:
        if trace is None:
            return "failed"
        if trace.status == "completed" and trace.confirmation_required:
            return "awaiting_confirmation"
        if trace.status == "completed" and trace.clarification_required:
            return "awaiting_clarification"
        if trace.status in {"completed", "failed", "cancelled"}:
            return trace.status
        return "running"

    def _event_context(event: AgentEventRecord) -> dict[str, Any]:
        raw_context = dict(event.context or {})
        _drop_removed_operator_context_keys(raw_context)
        raw_context.update(_resolve_agent_gateway_selection(raw_context, gateway_store))
        gateway_id = str(raw_context.get("gateway_id") or "").strip()
        gateway_node = str(raw_context.get("gateway_node") or raw_context.get("node") or "").strip()
        gateway_record = gateway_store.get(gateway_id) if gateway_id else None
        if gateway_record is None and gateway_node:
            gateway_record = gateway_store.find_by_node(gateway_node)
        context = dict(raw_context)
        if not str(context.get("terminal_cwd") or "").strip():
            terminal_cwd = _gateway_terminal_cwd(settings, gateway_record)
            if terminal_cwd:
                context["terminal_cwd"] = terminal_cwd
        backend_runtime_snapshot = _apply_backend_runtime_snapshot(context)
        if not str(context.get("agent_clarification_mode") or "").strip():
            context["agent_clarification_mode"] = backend_runtime_snapshot["agent_clarification_mode"]
        if "operator_workspace_cwd_guard_enabled" not in context:
            context["operator_workspace_cwd_guard_enabled"] = backend_runtime_snapshot[
                "operator_workspace_cwd_guard_enabled"
            ]
        if "llm_operator_verbose_enabled" not in context:
            context["llm_operator_verbose_enabled"] = backend_runtime_snapshot[
                "llm_operator_verbose_enabled"
            ]
        _apply_runtime_repair_attempt_context(context)
        context.pop("execute_in_terminal", None)
        context.pop("terminal_session_id", None)
        context["llm_operator_final_response_mode"] = "detailed"
        context["scheduled_event_id"] = event.event_id
        context["scheduled_event_title"] = event.title
        context["agent_mode"] = str(raw_context.get("agent_mode") or "llm_operator")
        llm_model = _resolve_agent_request_model(settings, raw_context.get("llm_model"))
        if llm_model:
            context["llm_model"] = llm_model
        llm_context_window_tokens, _source = _agent_context_window_tokens(settings, context)
        context["llm_context_window_tokens"] = llm_context_window_tokens
        if prompt_requests_parameter_typein_terminal(
            event.prompt,
            _parameter_store_for_macros(settings, agent_runtime),
        ):
            context["parameter_typein_terminal_required"] = True
            context["scheduled_event_parameter_typein_terminal"] = True
        context = _attach_background_terminal_for_typein(
            context=context,
            gateway_record=gateway_record,
            source="scheduled_event",
        )
        return _apply_command_allowlist_context(context)

    def _finalize_event_run(event_run_id: str, trace: AgentRequestTrace | None) -> str:
        status = _event_run_status_from_trace(trace)
        result_preview = _event_result_preview_from_trace(trace)
        execution_error_detail = _trace_execution_error_detail(trace)
        execution_error_preview = _execution_error_preview(execution_error_detail)
        run = event_store.get_run(event_run_id)
        event = event_store.get_event(run.event_id) if run is not None else None
        auto_approval_detail = _trace_auto_approval_detail(trace)
        parent_request_id = str(getattr(run, "parent_request_id", "") or "").strip()
        if not parent_request_id:
            parent_request_id = _trace_parent_request_id(trace)
        if event is not None:
            notification_metadata = {
                **auto_approval_detail,
                **execution_error_detail,
            }
            force_completed_auto_approval = (
                status == "completed"
                and bool(auto_approval_detail.get("auto_approved_confirmation"))
                and "awaiting_confirmation" in set(event.notify_on or [])
            )
            _maybe_create_event_notification(
                event=event,
                status=status,
                preview=result_preview or getattr(trace, "error", "") or "",
                event_run_id=event_run_id,
                request_id=getattr(trace, "request_id", "") or (run.request_id if run else ""),
                source_id=f"{event_run_id}:{status}" if force_completed_auto_approval else "",
                force=force_completed_auto_approval,
                metadata=notification_metadata,
            )
        event_store.update_run(
            event_run_id,
            status=status,  # type: ignore[arg-type]
            final_response_preview=result_preview or getattr(trace, "final_response", "") or "",
            error_preview=getattr(trace, "error", "") or execution_error_preview or "",
            completed_at=utc_now_iso() if status not in {"running", "awaiting_confirmation", "awaiting_clarification"} else "",
            parent_request_id=parent_request_id if parent_request_id else None,
        )
        return status

    def _append_scheduled_event_completed_trace_event(
        *,
        trace: AgentRequestTrace | None,
        event_id: str,
        event_run_id: str,
        status: str,
        parent_request_id: str = "",
    ) -> None:
        if trace is None:
            return
        detail = {"event_id": event_id, "event_run_id": event_run_id, "status": status}
        if parent_request_id:
            detail["parent_request_id"] = parent_request_id
        for trace_event in getattr(trace, "events", []) or []:
            if getattr(trace_event, "event_type", "") != "scheduled_event.completed":
                continue
            event_detail = getattr(trace_event, "detail", None)
            if (
                isinstance(event_detail, dict)
                and str(event_detail.get("event_run_id") or "") == event_run_id
            ):
                return
        try:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="completed",
                    level="info" if status in {"completed", "awaiting_clarification"} else "warning",
                    event_type="scheduled_event.completed",
                    title="Scheduled event updated",
                    summary=f"Scheduled event run is {status}.",
                    detail=detail,
                )
            )
        except KeyError:
            # The trace store is bounded and in-memory; the durable event run and
            # chat fallback still preserve the user-visible result.
            return

    def _ensure_scheduled_event_trace_enriched(
        trace: AgentRequestTrace | None,
    ) -> AgentRequestTrace | None:
        if trace is None:
            return None
        enriched = _event_enriched_final_response(trace)
        if not enriched or enriched == str(trace.final_response or "").strip():
            return trace
        trace_store.update_final_response(trace.request_id, enriched)
        updated_trace = trace_store.get_trace(trace.request_id) or trace
        state = state_store.get(trace.request_id)
        if state is not None and state.conversation_id:
            conversation_store.append_turn(state.conversation_id, updated_trace)
        return updated_trace

    def _trace_parent_request_id(trace: AgentRequestTrace | None) -> str:
        if trace is None:
            return ""
        for event in list(trace.events or []):
            detail = event.detail if isinstance(event.detail, dict) else {}
            parent_request_id = str(detail.get("parent_request_id") or "").strip()
            if parent_request_id:
                return parent_request_id
        return ""

    def _relink_handled_event_run(
        run: AgentEventRunRecord,
        *,
        state: AgentUiRequestState | None,
    ) -> AgentEventRunRecord | None:
        child_request_id = str(getattr(state, "continuation_request_id", "") or "").strip()
        if not child_request_id:
            child_request_id = state_store.find_child_request_id(run.request_id)
        if not child_request_id:
            child_request_id = trace_store.find_child_request_id(run.request_id)
        if not child_request_id:
            return None
        child_trace = trace_store.get_trace(child_request_id)
        if child_trace is not None:
            event_store.update_run(
                run.event_run_id,
                request_id=child_request_id,
                parent_request_id=run.request_id,
            )
            _finalize_event_run(run.event_run_id, child_trace)
            return event_store.update_run(
                run.event_run_id,
                request_id=child_request_id,
                parent_request_id=run.request_id,
            ) or event_store.get_run(run.event_run_id)
        child_state = state_store.get(child_request_id)
        if child_state is None:
            return None
        return event_store.update_run(
            run.event_run_id,
            request_id=child_request_id,
            parent_request_id=run.request_id,
            status="running",
            completed_at="",
        ) or event_store.get_run(run.event_run_id)

    def _reconcile_event_run(run: AgentEventRunRecord) -> AgentEventRunRecord:
        if run.status not in {"queued", "running", "awaiting_confirmation", "awaiting_clarification"}:
            if not run.parent_request_id and run.request_id:
                state = state_store.get(run.request_id)
                parent_request_id = str(getattr(state, "parent_request_id", "") or "").strip()
                if not parent_request_id:
                    parent_request_id = _trace_parent_request_id(trace_store.get_trace(run.request_id))
                if parent_request_id:
                    return event_store.update_run(
                        run.event_run_id,
                        parent_request_id=parent_request_id,
                    ) or run
            return run
        if run.status == "queued":
            task = task_store.get_task(run.task_id) if run.task_id else None
            if task is None:
                return run
            if task.status == "queued":
                return run
            if task.current_request_id:
                return event_store.update_run(
                    run.event_run_id,
                    request_id=task.current_request_id,
                    task_id=task.task_id,
                    status=task.status,  # type: ignore[arg-type]
                ) or run
            if task.status in {"interrupted", "failed", "cancelled"}:
                return event_store.update_run(
                    run.event_run_id,
                    task_id=task.task_id,
                    status="cancelled" if task.status == "interrupted" else task.status,  # type: ignore[arg-type]
                    final_response_preview=task.blocker_reason or task.error_preview,
                    completed_at=utc_now_iso(),
                ) or run
            return run
        if run.status == "awaiting_confirmation":
            state = state_store.get(run.request_id)
            handled = state is not None and state.confirmation_handled
            child_request_id = state_store.find_child_request_id(
                run.request_id
            ) or trace_store.find_child_request_id(run.request_id)
            if handled or child_request_id:
                relinked = _relink_handled_event_run(run, state=state)
                if relinked is not None:
                    return relinked
                if handled and not child_request_id:
                    return run
                return event_store.update_run(
                    run.event_run_id,
                    status="cancelled",
                    final_response_preview=(
                        "The confirmation for this scheduled run was already handled. "
                        "The stale run record was closed to unblock future event triggers."
                    ),
                    completed_at=utc_now_iso(),
                ) or run
        if run.status == "awaiting_clarification":
            state = state_store.get(run.request_id)
            handled = state is not None and state.clarification_handled
            child_request_id = state_store.find_child_request_id(
                run.request_id
            ) or trace_store.find_child_request_id(run.request_id)
            if handled or child_request_id:
                relinked = _relink_handled_event_run(run, state=state)
                if relinked is not None:
                    return relinked
                if handled and not child_request_id:
                    return run
                return event_store.update_run(
                    run.event_run_id,
                    status="cancelled",
                    final_response_preview=(
                        "The clarification for this scheduled run was already handled. "
                        "The stale run record was closed to unblock future event triggers."
                    ),
                    completed_at=utc_now_iso(),
                ) or run
        trace = trace_store.get_trace(run.request_id) if run.request_id else None
        if trace is not None:
            status = _event_run_status_from_trace(trace)
            if status not in {"running", "awaiting_confirmation", "awaiting_clarification"}:
                if run.status in {"awaiting_confirmation", "awaiting_clarification"} and status == "completed":
                    relinked = _relink_handled_event_run(run, state=state_store.get(run.request_id))
                    if relinked is not None:
                        return relinked
                    return run
                trace = _ensure_scheduled_event_trace_enriched(trace)
                _append_scheduled_event_completed_trace_event(
                    trace=trace,
                    event_id=run.event_id,
                    event_run_id=run.event_run_id,
                    status=status,
                    parent_request_id=run.parent_request_id,
                )
                _finalize_event_run(run.event_run_id, trace)
                return event_store.get_run(run.event_run_id) or run
        if run.status == "running" and run.request_id and trace is None:
            state = state_store.get(run.request_id)
            if state is None:
                return event_store.update_run(
                    run.event_run_id,
                    status="cancelled",
                    final_response_preview=(
                        "The request trace and runtime state for this scheduled run are no longer "
                        "available. The stale run record was closed to unblock future event triggers."
                    ),
                    completed_at=utc_now_iso(),
                ) or run
        if run.status == "awaiting_confirmation":
            state = state_store.get(run.request_id)
            if state is not None and state.confirmation_handled:
                relinked = _relink_handled_event_run(run, state=state)
                if relinked is not None:
                    return relinked
                return event_store.update_run(
                    run.event_run_id,
                    status="cancelled",
                    final_response_preview=(
                        "The confirmation for this scheduled run was already handled. "
                        "The stale run record was closed to unblock future event triggers."
                    ),
                    completed_at=utc_now_iso(),
                ) or run
        if run.status == "awaiting_clarification":
            state = state_store.get(run.request_id)
            if state is None:
                return event_store.update_run(
                    run.event_run_id,
                    status="cancelled",
                    final_response_preview=(
                        "The clarification state for this scheduled run is no longer "
                        "available. The stale run record was closed to unblock future "
                        "event triggers."
                    ),
                    completed_at=utc_now_iso(),
                ) or run
            if state.clarification_handled:
                relinked = _relink_handled_event_run(run, state=state)
                if relinked is not None:
                    return relinked
                return event_store.update_run(
                    run.event_run_id,
                    status="cancelled",
                    final_response_preview=(
                        "The clarification for this scheduled run was already handled. "
                        "The stale run record was closed to unblock future event triggers."
                    ),
                    completed_at=utc_now_iso(),
                ) or run
        return run

    def _reconcile_event_runs(event_id: str, *, limit: int = 100) -> list[AgentEventRunRecord]:
        runs = event_store.list_runs(event_id, limit=limit)
        return [_reconcile_event_run(run) for run in runs]

    def _reconcile_all_event_runs() -> None:
        for event in event_store.list_events():
            _reconcile_event_runs(event.event_id, limit=event_store.max_run_history)

    def _event_run_api_payload(
        run: AgentEventRunRecord,
        *,
        blocking_run: AgentEventRunRecord | None = None,
    ) -> dict[str, Any]:
        payload = run.model_dump(mode="json")
        if run.status == "skipped":
            blocker = (
                event_store.get_run_by_request_id(run.event_id, run.parent_request_id)
                if run.parent_request_id
                else None
            )
            if blocker is None:
                blocker = blocking_run
            if blocker is not None and blocker.event_run_id != run.event_run_id:
                payload["blocked_by_event_run_id"] = blocker.event_run_id
                payload["blocked_by_request_id"] = blocker.request_id
                payload["blocked_by_status"] = blocker.status
            elif run.parent_request_id:
                payload["blocked_by_request_id"] = run.parent_request_id
        return payload

    def _notification_api_payload(notification: Any) -> dict[str, Any]:
        return notification.model_dump(mode="json")

    def _execution_error_code(source: dict[str, Any]) -> str:
        metadata = source.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        error_text = str(source.get("error") or source.get("stderr") or "").strip()
        if (
            metadata.get("background_terminal_unavailable") is True
            or error_text.startswith("background_terminal_unavailable:")
        ):
            return "background_terminal_unavailable"
        if (
            metadata.get("terminal_context_required") is True
            or error_text.startswith("terminal_context_required:")
        ):
            return "terminal_context_required"
        return "operator_execution_error"

    def _execution_error_message(source: dict[str, Any], code: str) -> str:
        if code == "background_terminal_unavailable":
            metadata = source.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            configured_error = str(
                metadata.get("background_terminal_error")
                or source.get("error")
                or source.get("stderr")
                or ""
            ).strip()
            if configured_error.startswith("background_terminal_unavailable:"):
                configured_error = configured_error.split(":", 1)[1].strip()
            return configured_error or "A background terminal was unavailable for an interactive action."
        return str(source.get("error") or source.get("stderr") or source.get("stdout") or "").strip()

    def _execution_error_detail_from_source(
        source: Any,
        *,
        fallback_action_id: str = "",
    ) -> dict[str, Any]:
        if not isinstance(source, dict):
            return {}
        metadata = source.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        status = str(source.get("status") or metadata.get("status") or "").strip().lower()
        error_text = str(source.get("error") or source.get("stderr") or "").strip()
        if status != "error" and not error_text.startswith(
            ("background_terminal_unavailable:", "terminal_context_required:")
        ):
            return {}
        code = _execution_error_code(source)
        severity = "error" if code == "background_terminal_unavailable" else "warning"
        action_id = str(
            source.get("action_id")
            or source.get("node_id")
            or source.get("task_id")
            or fallback_action_id
            or ""
        ).strip()
        message = _execution_error_message(source, code)
        return {
            "execution_error": True,
            "execution_error_code": code,
            "execution_error_severity": severity,
            "execution_error_action_id": action_id,
            "execution_error_message": message,
        }

    def _trace_execution_error_detail(trace: AgentRequestTrace | None) -> dict[str, Any]:
        if trace is None:
            return {}
        fallback_detail: dict[str, Any] = {}
        for payload in dict(trace.raw_payloads or {}).values():
            if not isinstance(payload, dict):
                continue
            fallback_action_id = str(payload.get("source_node_id") or "").strip()
            for source in (payload.get("preview"), payload.get("diagnostics")):
                detail = _execution_error_detail_from_source(
                    source,
                    fallback_action_id=fallback_action_id,
                )
                if not detail:
                    continue
                if detail.get("execution_error_code") == "background_terminal_unavailable":
                    return detail
                if not fallback_detail:
                    fallback_detail = detail
        for event in list(getattr(trace, "events", []) or []):
            event_type = str(getattr(event, "event_type", "") or "")
            event_detail = getattr(event, "detail", None)
            if not isinstance(event_detail, dict):
                continue
            if event_type in {"operator.execution.failed", "execution.node.failed"}:
                source = dict(event_detail)
                source.setdefault("status", "error")
                detail = _execution_error_detail_from_source(source)
                if not detail:
                    continue
                if detail.get("execution_error_code") == "background_terminal_unavailable":
                    return detail
                if not fallback_detail:
                    fallback_detail = detail
        for event in reversed(list(getattr(trace, "events", []) or [])):
            event_detail = getattr(event, "detail", None)
            if not isinstance(event_detail, dict):
                continue
            final_status = str(event_detail.get("final_status") or "").strip().lower()
            result_status = str(event_detail.get("status") or "").strip().lower()
            if final_status in {"error", "failed"} or (
                str(getattr(event, "event_type", "") or "")
                in {"operator.execution.completed", "operator.continuation.completed"}
                and result_status in {"error", "failed"}
            ):
                detail = dict(fallback_detail)
                detail.setdefault("execution_error", True)
                detail["execution_error_code"] = str(
                    detail.get("execution_error_code") or "operator_final_status_error"
                )
                detail["execution_error_severity"] = "error"
                detail.setdefault("execution_error_action_id", "")
                detail.setdefault(
                    "execution_error_message",
                    "Operator execution finished with one or more action errors.",
                )
                return detail
        return fallback_detail

    def _execution_error_preview(detail: dict[str, Any] | None) -> str:
        if not isinstance(detail, dict) or not detail.get("execution_error"):
            return ""
        message = str(detail.get("execution_error_message") or "").strip()
        code = str(detail.get("execution_error_code") or "operator_execution_error").strip()
        if message:
            return f"{code}: {message}"[:4000]
        if code == "background_terminal_unavailable":
            return "background_terminal_unavailable: A background terminal was unavailable."
        return "operator_execution_error: Execution completed with one or more action errors."

    def _notification_level_with_execution_error(
        status: str,
        default_level: str,
        execution_error_detail: dict[str, Any] | None = None,
    ) -> str:
        if not isinstance(execution_error_detail, dict) or not execution_error_detail.get("execution_error"):
            return default_level
        if default_level == "error":
            return default_level
        severity = str(execution_error_detail.get("execution_error_severity") or "").strip().lower()
        if severity in {"warning", "error"}:
            return severity
        if str(execution_error_detail.get("execution_error_code") or "") == "background_terminal_unavailable":
            return "error"
        return "warning" if status == "completed" else default_level

    def _event_notification_level(
        status: str,
        execution_error_detail: dict[str, Any] | None = None,
    ) -> str:
        default_level = "info"
        if status == "completed":
            default_level = "success"
        elif status in {"awaiting_confirmation", "awaiting_clarification", "skipped"}:
            default_level = "warning"
        elif status in {"failed", "cancelled"}:
            default_level = "error"
        return _notification_level_with_execution_error(
            status,
            default_level,
            execution_error_detail,
        )

    def _plural_count(count: int, singular: str, plural: str | None = None) -> str:
        value = max(0, int(count or 0))
        return f"{value} {singular if value == 1 else (plural or singular + 's')}"

    def _trace_auto_approval_detail(trace: AgentRequestTrace | None) -> dict[str, Any]:
        if trace is None:
            return {}
        for event in list(trace.events or []):
            if event.event_type != "scheduled_event.auto_approved":
                continue
            detail = event.detail if isinstance(event.detail, dict) else {}
            return {
                "auto_approved_confirmation": True,
                "confirmation_action_count": max(1, int(detail.get("confirmation_action_count") or 0)),
                "parent_request_id": str(detail.get("parent_request_id") or "").strip(),
            }
        for event in list(trace.events or []):
            if event.event_type != "confirmation.auto_approved":
                continue
            detail = event.detail if isinstance(event.detail, dict) else {}
            return {
                "auto_approved_confirmation": True,
                "confirmation_action_count": max(1, int(detail.get("confirmation_action_count") or 0)),
                "parent_request_id": str(detail.get("parent_request_id") or "").strip(),
            }
        return {}

    def _event_run_notification_message(
        *,
        event: AgentEventRecord,
        status: str,
        preview: str,
        auto_approved: bool = False,
        confirmation_action_count: int = 0,
        execution_error_detail: dict[str, Any] | None = None,
    ) -> str:
        configured = str(event.notification_message or "").strip()
        auto_approval_line = ""
        if auto_approved:
            count_text = _plural_count(confirmation_action_count or 1, "confirmation")
            auto_approval_line = f"Auto-approved {count_text} before continuing the scheduled run."
        if status == "completed":
            if isinstance(execution_error_detail, dict) and execution_error_detail.get("execution_error"):
                error_preview = _execution_error_preview(execution_error_detail)
                parts = [f"`{event.title}` completed with one or more action errors."]
                if auto_approval_line:
                    parts.append(auto_approval_line)
                for body in (error_preview, configured or preview):
                    if body and body not in parts:
                        parts.append(body)
                return "\n\n".join(parts)
            success_line = f"`{event.title}` completed successfully."
            body = configured or preview
            parts = [success_line]
            if auto_approval_line:
                parts.append(auto_approval_line)
            if body and body != success_line:
                parts.append(body)
            return "\n\n".join(parts)
        if status == "awaiting_confirmation":
            return f"`{event.title}` needs approval before it can continue."
        if status == "awaiting_clarification":
            return f"`{event.title}` needs input before it can continue."
        if status == "skipped":
            return preview or f"`{event.title}` was skipped because another run is still open."
        if status == "failed":
            return preview or f"`{event.title}` failed."
        if status == "cancelled":
            return preview or f"`{event.title}` was cancelled."
        return preview or configured or f"`{event.title}` is {status}."

    def _maybe_create_event_notification(
        *,
        event: AgentEventRecord,
        status: str,
        preview: str = "",
        event_run_id: str = "",
        request_id: str = "",
        source_type: str = "scheduled_event_run",
        source_id: str = "",
        force: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if event.event_kind == "todo":
            return
        if not force and status not in set(event.notify_on or []):
            return
        source_id = str(source_id or event_run_id or request_id or event.event_id).strip()
        if not source_id or event_store.notification_exists(source_type=source_type, source_id=source_id):
            return
        detail = dict(metadata or {})
        auto_approved = bool(detail.get("auto_approved_confirmation"))
        confirmation_action_count = int(detail.get("confirmation_action_count") or 0)
        execution_error_detail = detail if detail.get("execution_error") else None
        if status == "completed":
            detail["completed_successfully"] = not bool(execution_error_detail)
        title_status = status.replace("_", " ")
        event_store.create_notification(
            AgentNotificationCreate(
                level=_event_notification_level(status, execution_error_detail),
                title=f"Event {title_status}: {event.title}",
                message=_event_run_notification_message(
                    event=event,
                    status=status,
                    preview=preview,
                    auto_approved=auto_approved,
                    confirmation_action_count=confirmation_action_count,
                    execution_error_detail=execution_error_detail,
                ),
                source_type=source_type,
                source_id=source_id,
                event_id=event.event_id,
                event_run_id=event_run_id,
                request_id=request_id,
                metadata={"event_status": status, **detail},
            )
        )

    def _create_scheduled_event_auto_approved_notification(
        *,
        event: AgentEventRecord,
        event_run_id: str,
        request_id: str,
        parent_request_id: str,
        confirmation_action_count: int,
    ) -> None:
        if event.event_kind == "todo":
            return
        notify_on = set(event.notify_on or [])
        if "awaiting_confirmation" not in notify_on and "completed" not in notify_on:
            return
        source_id = f"{event_run_id}:auto_approved"
        if not event_run_id or event_store.notification_exists(
            source_type="scheduled_event_auto_approved",
            source_id=source_id,
        ):
            return
        count_text = _plural_count(confirmation_action_count or 1, "confirmation")
        event_store.create_notification(
            AgentNotificationCreate(
                level="info",
                title=f"Event auto-approved: {event.title}",
                message=f"`{event.title}` auto-approved {count_text} and continued in the background.",
                source_type="scheduled_event_auto_approved",
                source_id=source_id,
                event_id=event.event_id,
                event_run_id=event_run_id,
                request_id=request_id,
                metadata={
                    "event_status": "auto_approved",
                    "auto_approved_confirmation": True,
                    "confirmation_action_count": max(1, int(confirmation_action_count or 0)),
                    "parent_request_id": parent_request_id,
                },
            )
        )

    def _create_direct_event_notification(
        *,
        event: AgentEventRecord,
        event_run_id: str,
    ) -> None:
        if event.event_kind == "todo":
            return
        source_id = event_run_id or event.event_id
        if event_store.notification_exists(source_type="scheduled_event_notification", source_id=source_id):
            return
        message = str(event.notification_message or event.prompt or event.title).strip()
        event_store.create_notification(
            AgentNotificationCreate(
                level="info",
                title=event.title or "Reminder",
                message=message or "Reminder",
                source_type="scheduled_event_notification",
                source_id=source_id,
                event_id=event.event_id,
                event_run_id=event_run_id,
                metadata={"event_status": "completed", "action_type": "notification"},
            )
        )

    for _name in (
        '_event_macro_context_from',
        '_apply_event_extraction_hint_context',
        '_strip_event_macro_context',
        '_event_context_for_api',
        '_stash_event_macro_context_for_draft',
        '_resolve_pending_event_macro_context',
        '_prompt_has_sanitized_typein_marker',
        '_prepare_event_prompt_and_context',
        '_event_record_api_payload',
        '_event_draft_response_api_payload',
        '_event_create_payload_from_draft',
        '_event_schedule_phrase',
        '_saved_events_message',
        '_event_needs_detail_message',
        '_event_has_typein_macro',
        '_event_run_status_from_trace',
        '_event_context',
        '_finalize_event_run',
        '_append_scheduled_event_completed_trace_event',
        '_ensure_scheduled_event_trace_enriched',
        '_trace_parent_request_id',
        '_relink_handled_event_run',
        '_reconcile_event_run',
        '_reconcile_event_runs',
        '_reconcile_all_event_runs',
        '_event_run_api_payload',
        '_notification_api_payload',
        '_execution_error_code',
        '_execution_error_message',
        '_execution_error_detail_from_source',
        '_trace_execution_error_detail',
        '_execution_error_preview',
        '_notification_level_with_execution_error',
        '_event_notification_level',
        '_plural_count',
        '_trace_auto_approval_detail',
        '_event_run_notification_message',
        '_maybe_create_event_notification',
        '_create_scheduled_event_auto_approved_notification',
        '_create_direct_event_notification'
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
