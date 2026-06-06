"""Install terminal and confirmation helper functions."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared_imports import *


def install_terminal_helpers(ctx: Any) -> None:
    """Install terminal and confirmation helper functions."""


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

    def _ctx_event_has_typein_macro(context: dict[str, Any] | None) -> bool:
        helper = getattr(ctx, "_event_has_typein_macro", None)
        return bool(helper(context)) if callable(helper) else False

    def _ctx_needs_parameter_typein_terminal(context: dict[str, Any] | None) -> bool:
        payload = dict(context or {})
        return bool(
            payload.get("parameter_typein_terminal_required")
            or payload.get("request_parameter_typein_terminal")
            or payload.get("scheduled_event_parameter_typein_terminal")
            or payload.get("durable_task_parameter_typein_terminal")
        )

    def _confirmation_actions_require_terminal(actions: list[dict[str, Any]] | None) -> bool:
        for action in list(actions or []):
            if not isinstance(action, dict):
                continue
            arguments = action.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            operation_id = str(action.get("operation_id") or arguments.get("operation_id") or "").strip().lower()
            capability_id = str(action.get("capability_id") or arguments.get("capability_id") or "").strip().lower()
            kind = str(action.get("kind") or arguments.get("kind") or "").strip().lower()
            interaction_mode = str(
                action.get("interaction_mode") or arguments.get("interaction_mode") or ""
            ).strip().lower()
            execution_mode = str(
                action.get("execution_mode") or arguments.get("execution_mode") or ""
            ).strip().lower()
            shell_action = (
                operation_id == "shell_command"
                or kind == "shell_command"
                or capability_id.endswith(".shell_command")
            )
            if (
                action.get("requires_terminal_context") is True
                or arguments.get("requires_terminal_context") is True
                or (
                    shell_action
                    and (
                        interaction_mode in {"may_prompt", "long_running"}
                        or execution_mode == "terminal_detached"
                    )
                )
            ):
                return True
        return False

    def _attach_background_terminal(
        *,
        context: dict[str, Any],
        gateway_record: Any | None,
        source: str = "request",
        required: bool = False,
    ) -> dict[str, Any]:
        if (
            not required
            and not _ctx_event_has_typein_macro(context)
            and not _ctx_needs_parameter_typein_terminal(context)
        ):
            return context
        existing_session_id = str(context.get("terminal_session_id") or "").strip()
        if existing_session_id and (context.get("background_terminal") or not required):
            return context
        if existing_session_id and required:
            context.pop("terminal_session_id", None)
            context.pop("execute_in_terminal", None)
        source_prefix = str(source or "request").strip() or "request"
        error_key = f"{source_prefix}_background_terminal_error"
        attached_key = f"{source_prefix}_background_terminal"
        local_gateway_record = gateway_record
        node = str(context.get("gateway_node") or context.get("node") or "").strip()
        gateway_url = str(context.get("gateway_url") or "").strip()
        if not node or not gateway_url:
            try:
                resolved_gateway_id, node, gateway_url = _resolve_terminal_gateway_selection(
                    settings,
                    gateway_store,
                    gateway_id=str(context.get("gateway_id") or "").strip() or None,
                    gateway_node=node or None,
                )
            except HTTPException as exc:
                context[error_key] = str(exc.detail)
                context["background_terminal_error"] = context[error_key]
                return context
            if resolved_gateway_id:
                context["gateway_id"] = resolved_gateway_id
                local_gateway_record = gateway_store.get(resolved_gateway_id)
            context["gateway_node"] = node
            context["gateway_url"] = gateway_url
            context["gateway_endpoints"] = {node: gateway_url}
        terminal_cwd = str(context.get("terminal_cwd") or "").strip()
        if not terminal_cwd:
            terminal_cwd = _gateway_terminal_cwd(settings, local_gateway_record)
            if terminal_cwd:
                context["terminal_cwd"] = terminal_cwd
        if not terminal_cwd:
            context[error_key] = "Terminal cwd is not configured."
            context["background_terminal_error"] = context[error_key]
            return context
        terminal_session = terminal_store.create(
            terminal_cwd,
            gateway_id=str(context.get("gateway_id") or ""),
            gateway_node=node,
        )
        try:
            response = _request_gateway_json_at(
                gateway_url=gateway_url,
                node=node,
                path="/terminal/session",
                payload={
                    "session_id": terminal_session.session_id,
                    "initial_cwd": terminal_cwd,
                    "rows": 24,
                    "cols": 100,
                },
                timeout_seconds=min(float(settings.gateway_timeout_seconds), 10.0),
            )
        except HTTPException as exc:
            context[error_key] = str(exc.detail)
            context["background_terminal_error"] = context[error_key]
            return context
        session_id = str(response.get("session_id") or terminal_session.session_id).strip()
        cwd = str(response.get("cwd") or terminal_cwd).strip()
        if session_id and cwd:
            terminal_store.update_cwd(session_id, cwd)
            context["terminal_session_id"] = session_id
            context["terminal_cwd"] = cwd
            context["execute_in_terminal"] = True
            context[attached_key] = True
            context["background_terminal"] = True
        return context

    def _attach_background_terminal_for_typein(
        *,
        context: dict[str, Any],
        gateway_record: Any | None,
        source: str = "request",
    ) -> dict[str, Any]:
        return _attach_background_terminal(
            context=context,
            gateway_record=gateway_record,
            source=source,
        )

    def _background_terminal_context_hint(context: dict[str, Any]) -> bool:
        return any(
            str(context.get(key) or "").strip()
            for key in ("terminal_cwd", "gateway_id", "gateway_node", "gateway_url", "node")
        )

    for _name in (
        '_confirmation_actions_require_terminal',
        '_attach_background_terminal',
        '_attach_background_terminal_for_typein',
        '_background_terminal_context_hint'
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
