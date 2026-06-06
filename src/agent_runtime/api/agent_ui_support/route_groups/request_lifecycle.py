"""Request Lifecycle Agent UI routes."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared import *


def register_request_lifecycle_routes(ctx: AgentUiRouteContext) -> None:
    """Register request lifecycle routes."""

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
    _release_active_non_task_request = getattr(ctx, '_release_active_non_task_request', None)
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
    _start_next_queued_task = getattr(ctx, '_start_next_queued_task', None)
    _monitor_active_non_task_request = getattr(ctx, '_monitor_active_non_task_request', None)
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
    _auto_approve_scheduled_event = getattr(ctx, '_auto_approve_scheduled_event', None)
    _run_scheduled_event_worker = getattr(ctx, '_run_scheduled_event_worker', None)
    _launch_scheduled_event = getattr(ctx, '_launch_scheduled_event', None)
    root_directory = getattr(ctx, 'root_directory', None)
    directory = getattr(ctx, 'directory', None)
    agent_directory = getattr(ctx, 'agent_directory', None)
    agent_ui = getattr(ctx, 'agent_ui', None)
    agent_ui_client = getattr(ctx, 'agent_ui_client', None)
    agent_ui_mobile = getattr(ctx, 'agent_ui_mobile', None)
    prompt_editor = getattr(ctx, 'prompt_editor', None)
    parameter_editor = getattr(ctx, 'parameter_editor', None)
    learning_ledger = getattr(ctx, 'learning_ledger', None)
    reliability_dashboard = getattr(ctx, 'reliability_dashboard', None)
    mission_control_settings = getattr(ctx, 'mission_control_settings', None)
    agent_health = getattr(ctx, 'agent_health', None)
    agent_model_config = getattr(ctx, 'agent_model_config', None)
    agent_version = getattr(ctx, 'agent_version', None)
    agent_version_check = getattr(ctx, 'agent_version_check', None)
    agent_prompt_history = getattr(ctx, 'agent_prompt_history', None)
    record_agent_prompt_history = getattr(ctx, 'record_agent_prompt_history', None)
    _agent_ui_settings_preferences_payload = getattr(ctx, '_agent_ui_settings_preferences_payload', None)
    agent_settings_preferences = getattr(ctx, 'agent_settings_preferences', None)
    update_agent_settings_preferences = getattr(ctx, 'update_agent_settings_preferences', None)
    _agent_settings_defaults = getattr(ctx, '_agent_settings_defaults', None)
    _agent_settings_limits = getattr(ctx, '_agent_settings_limits', None)
    _registry_option = getattr(ctx, '_registry_option', None)
    _registry_setting = getattr(ctx, '_registry_setting', None)
    _agent_settings_registry_payload = getattr(ctx, '_agent_settings_registry_payload', None)
    agent_settings_registry = getattr(ctx, 'agent_settings_registry', None)
    agent_settings_config = getattr(ctx, 'agent_settings_config', None)
    agent_prompt_macros = getattr(ctx, 'agent_prompt_macros', None)
    list_prompt_editor_templates = getattr(ctx, 'list_prompt_editor_templates', None)
    get_prompt_editor_template = getattr(ctx, 'get_prompt_editor_template', None)
    update_prompt_editor_template = getattr(ctx, 'update_prompt_editor_template', None)
    reset_prompt_editor_template = getattr(ctx, 'reset_prompt_editor_template', None)
    render_prompt_editor_template = getattr(ctx, 'render_prompt_editor_template', None)
    list_prompt_editor_parameters = getattr(ctx, 'list_prompt_editor_parameters', None)
    list_prompt_editor_memories = getattr(ctx, 'list_prompt_editor_memories', None)
    create_prompt_editor_memory = getattr(ctx, 'create_prompt_editor_memory', None)
    get_prompt_editor_memory = getattr(ctx, 'get_prompt_editor_memory', None)
    update_prompt_editor_memory = getattr(ctx, 'update_prompt_editor_memory', None)
    _learning_lesson_payload = getattr(ctx, '_learning_lesson_payload', None)
    _capability_proposal_payload = getattr(ctx, '_capability_proposal_payload', None)
    get_learning_ledger_summary = getattr(ctx, 'get_learning_ledger_summary', None)
    clear_learning_ledger = getattr(ctx, 'clear_learning_ledger', None)
    get_reliability_profiles = getattr(ctx, 'get_reliability_profiles', None)
    get_reliability_profile = getattr(ctx, 'get_reliability_profile', None)
    list_reliability_runs = getattr(ctx, 'list_reliability_runs', None)
    get_reliability_run = getattr(ctx, 'get_reliability_run', None)
    get_reliability_run_report_json = getattr(ctx, 'get_reliability_run_report_json', None)
    get_reliability_run_report_markdown = getattr(ctx, 'get_reliability_run_report_markdown', None)
    list_reliability_evals = getattr(ctx, 'list_reliability_evals', None)
    run_reliability_evals = getattr(ctx, 'run_reliability_evals', None)
    list_learning_ledger_runs = getattr(ctx, 'list_learning_ledger_runs', None)
    get_learning_ledger_run = getattr(ctx, 'get_learning_ledger_run', None)
    list_capability_proposals = getattr(ctx, 'list_capability_proposals', None)
    get_capability_proposal = getattr(ctx, 'get_capability_proposal', None)
    update_capability_proposal = getattr(ctx, 'update_capability_proposal', None)
    approve_capability_proposal = getattr(ctx, 'approve_capability_proposal', None)
    apply_capability_proposal = getattr(ctx, 'apply_capability_proposal', None)
    reject_capability_proposal = getattr(ctx, 'reject_capability_proposal', None)
    retire_capability_proposal = getattr(ctx, 'retire_capability_proposal', None)
    list_learning_ledger_lessons = getattr(ctx, 'list_learning_ledger_lessons', None)
    update_learning_ledger_lesson = getattr(ctx, 'update_learning_ledger_lesson', None)
    approve_learning_ledger_lesson = getattr(ctx, 'approve_learning_ledger_lesson', None)
    reject_learning_ledger_lesson = getattr(ctx, 'reject_learning_ledger_lesson', None)
    retire_learning_ledger_lesson = getattr(ctx, 'retire_learning_ledger_lesson', None)
    restore_learning_ledger_lesson = getattr(ctx, 'restore_learning_ledger_lesson', None)
    digest_learning_ledger_note = getattr(ctx, 'digest_learning_ledger_note', None)
    list_agent_chats = getattr(ctx, 'list_agent_chats', None)
    get_agent_chat = getattr(ctx, 'get_agent_chat', None)
    delete_agent_chat = getattr(ctx, 'delete_agent_chat', None)
    agent_runtime_controls = getattr(ctx, 'agent_runtime_controls', None)
    update_agent_runtime_controls = getattr(ctx, 'update_agent_runtime_controls', None)
    list_agent_command_allowlist = getattr(ctx, 'list_agent_command_allowlist', None)
    create_agent_command_allowlist = getattr(ctx, 'create_agent_command_allowlist', None)
    delete_agent_command_allowlist = getattr(ctx, 'delete_agent_command_allowlist', None)
    draft_agent_events = getattr(ctx, 'draft_agent_events', None)
    create_agent_events_from_prompt = getattr(ctx, 'create_agent_events_from_prompt', None)
    list_agent_events = getattr(ctx, 'list_agent_events', None)
    create_agent_event = getattr(ctx, 'create_agent_event', None)
    update_agent_event = getattr(ctx, 'update_agent_event', None)
    delete_agent_event = getattr(ctx, 'delete_agent_event', None)
    trigger_agent_event = getattr(ctx, 'trigger_agent_event', None)
    list_agent_event_runs = getattr(ctx, 'list_agent_event_runs', None)
    cancel_agent_event_run = getattr(ctx, 'cancel_agent_event_run', None)
    list_agent_notifications = getattr(ctx, 'list_agent_notifications', None)
    update_agent_notification = getattr(ctx, 'update_agent_notification', None)
    mark_all_agent_notifications_read = getattr(ctx, 'mark_all_agent_notifications_read', None)
    draft_agent_monitor = getattr(ctx, 'draft_agent_monitor', None)
    list_agent_monitors = getattr(ctx, 'list_agent_monitors', None)
    create_agent_monitor = getattr(ctx, 'create_agent_monitor', None)
    get_agent_monitor = getattr(ctx, 'get_agent_monitor', None)
    list_agent_monitor_observations = getattr(ctx, 'list_agent_monitor_observations', None)
    stream_agent_monitor = getattr(ctx, 'stream_agent_monitor', None)
    start_agent_monitor = getattr(ctx, 'start_agent_monitor', None)
    pause_agent_monitor = getattr(ctx, 'pause_agent_monitor', None)
    cancel_agent_monitor = getattr(ctx, 'cancel_agent_monitor', None)
    archive_agent_monitor = getattr(ctx, 'archive_agent_monitor', None)
    list_agent_tasks = getattr(ctx, 'list_agent_tasks', None)
    clear_agent_tasks = getattr(ctx, 'clear_agent_tasks', None)
    create_agent_task = getattr(ctx, 'create_agent_task', None)
    get_agent_task = getattr(ctx, 'get_agent_task', None)
    list_agent_task_attempts = getattr(ctx, 'list_agent_task_attempts', None)
    list_agent_task_checkpoints = getattr(ctx, 'list_agent_task_checkpoints', None)
    start_agent_task = getattr(ctx, 'start_agent_task', None)
    retry_agent_task = getattr(ctx, 'retry_agent_task', None)
    cancel_agent_task = getattr(ctx, 'cancel_agent_task', None)
    archive_agent_task = getattr(ctx, 'archive_agent_task', None)
    delete_agent_task = getattr(ctx, 'delete_agent_task', None)
    agent_plan_cache_stats = getattr(ctx, 'agent_plan_cache_stats', None)
    clear_agent_plan_cache = getattr(ctx, 'clear_agent_plan_cache', None)
    clear_agent_lrdirect_cache = getattr(ctx, 'clear_agent_lrdirect_cache', None)
    suggest_agent_name = getattr(ctx, 'suggest_agent_name', None)
    draft_agent_parameter = getattr(ctx, 'draft_agent_parameter', None)
    list_agent_parameters = getattr(ctx, 'list_agent_parameters', None)
    create_agent_parameter = getattr(ctx, 'create_agent_parameter', None)
    get_agent_parameter = getattr(ctx, 'get_agent_parameter', None)
    update_agent_parameter = getattr(ctx, 'update_agent_parameter', None)
    delete_agent_parameter = getattr(ctx, 'delete_agent_parameter', None)
    reveal_agent_parameter = getattr(ctx, 'reveal_agent_parameter', None)
    list_agent_memory = getattr(ctx, 'list_agent_memory', None)
    create_agent_memory = getattr(ctx, 'create_agent_memory', None)
    draft_agent_memory_context = getattr(ctx, 'draft_agent_memory_context', None)
    update_agent_memory = getattr(ctx, 'update_agent_memory', None)
    retire_agent_memory = getattr(ctx, 'retire_agent_memory', None)
    restore_agent_memory = getattr(ctx, 'restore_agent_memory', None)
    audit_agent_memory = getattr(ctx, 'audit_agent_memory', None)
    propose_agent_memory_from_feedback = getattr(ctx, 'propose_agent_memory_from_feedback', None)
    draft_agent_memory_feedback = getattr(ctx, 'draft_agent_memory_feedback', None)
    optimize_agent_memory = getattr(ctx, 'optimize_agent_memory', None)
    apply_agent_memory_proposal = getattr(ctx, 'apply_agent_memory_proposal', None)
    summarize_agent_conversation = getattr(ctx, 'summarize_agent_conversation', None)
    list_agent_gateways = getattr(ctx, 'list_agent_gateways', None)
    create_agent_gateway = getattr(ctx, 'create_agent_gateway', None)
    update_agent_gateway = getattr(ctx, 'update_agent_gateway', None)
    delete_agent_gateway = getattr(ctx, 'delete_agent_gateway', None)
    check_agent_gateway_health = getattr(ctx, 'check_agent_gateway_health', None)
    proxy_agent_terminal_ws = getattr(ctx, 'proxy_agent_terminal_ws', None)
    get_agent_terminal_config = getattr(ctx, 'get_agent_terminal_config', None)
    update_agent_terminal_cwd = getattr(ctx, 'update_agent_terminal_cwd', None)
    agent_llm_runtime_status = getattr(ctx, 'agent_llm_runtime_status', None)
    agent_llm_runtime_log = getattr(ctx, 'agent_llm_runtime_log', None)
    agent_llm_runtime_start = getattr(ctx, 'agent_llm_runtime_start', None)
    agent_llm_runtime_stop = getattr(ctx, 'agent_llm_runtime_stop', None)
    submit_agent_request = getattr(ctx, 'submit_agent_request', None)
    submit_agent_confirmation = getattr(ctx, 'submit_agent_confirmation', None)
    continue_agent_request = getattr(ctx, 'continue_agent_request', None)
    submit_agent_clarification = getattr(ctx, 'submit_agent_clarification', None)
    stop_agent_request = getattr(ctx, 'stop_agent_request', None)
    get_agent_trace = getattr(ctx, 'get_agent_trace', None)
    get_agent_raw_payload = getattr(ctx, 'get_agent_raw_payload', None)
    get_agent_file = getattr(ctx, 'get_agent_file', None)
    restart_agent_server = getattr(ctx, 'restart_agent_server', None)
    stream_agent_trace = getattr(ctx, 'stream_agent_trace', None)

    @app.post("/api/agent/request")
    def submit_agent_request(payload: AgentRequestPayload) -> dict[str, Any]:
        """Submit one prompt for trace-enabled runtime execution."""

        raw_context = dict(payload.context or {})
        request_auto_approve = raw_context.get("auto_approve_commands") is True
        request_auto_approve_scope = str(raw_context.get("auto_approve_scope") or "request").strip() or "request"
        _drop_removed_operator_context_keys(raw_context)
        if request_auto_approve:
            raw_context["auto_approve_commands"] = True
            raw_context["auto_approve_scope"] = request_auto_approve_scope
        add_memory_text = _add_to_memory_shortcut_payload(payload.prompt)
        if add_memory_text is not None:
            if not add_memory_text:
                raise HTTPException(status_code=400, detail="Add memory text after /addtomemory.")
            memory_store = _memory_store_or_error(settings, agent_runtime)
            request_context = dict(raw_context)
            _apply_backend_runtime_snapshot(request_context)
            llm_model = _resolve_agent_request_model(settings, payload.llm_model)
            if llm_model:
                request_context["llm_model"] = llm_model
            llm_context_window_tokens, _llm_context_window_source = _agent_context_window_tokens(
                settings,
                request_context,
            )
            request_context["llm_context_window_tokens"] = llm_context_window_tokens
            requested_conversation_id = str(payload.conversation_id or "").strip() or None
            if requested_conversation_id:
                if not conversation_store.exists(requested_conversation_id):
                    raise HTTPException(
                        status_code=404,
                        detail="Conversation not found or no longer retained.",
                    )
                conversation_id = requested_conversation_id
            else:
                conversation_id = conversation_store.create(payload.prompt)
            trace = trace_store.create_request(payload.prompt)
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="memory",
                    level="info",
                    event_type="memory.addtomemory.started",
                    title="Memory shortcut started",
                    summary="The /addtomemory shortcut is digesting and saving the supplied text.",
                    detail={"input_chars": len(add_memory_text)},
                )
            )
            try:
                entries, rationale = _add_to_memory_shortcut_entries(
                    memory_store=memory_store,
                    source_text=add_memory_text,
                    settings=settings,
                    agent_runtime=agent_runtime,
                )
            except Exception as exc:
                trace_store.fail_request(trace.request_id, f"Failed to save memory: {exc}")
                failed_trace = trace_store.get_trace(trace.request_id)
                if failed_trace is not None:
                    conversation_store.append_turn(conversation_id, failed_trace)
                return {
                    "request_id": trace.request_id,
                    "stream_url": f"/api/agent/stream/{trace.request_id}",
                    "trace_url": f"/api/agent/trace/{trace.request_id}",
                    "conversation_id": conversation_id,
                    "llm_context_window_tokens": llm_context_window_tokens,
                    "llm_context_window_source": _llm_context_window_source,
                }
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="memory",
                    level="info",
                    event_type="memory.addtomemory.saved",
                    title="Memory saved",
                    summary=f"Saved {len(entries)} active memory entr{'y' if len(entries) == 1 else 'ies'}.",
                    detail={
                        "entries": [
                            {
                                "memory_id": entry.memory_id,
                                "summary": entry.summary,
                                "memory_kind": entry.memory_kind,
                                "tags": entry.tags,
                                "task_type": entry.task_type,
                                "tool_type": entry.tool_type,
                                "intent_type": entry.intent_type,
                            }
                            for entry in entries
                        ],
                        "rationale": rationale,
                    },
                )
            )
            final_response = (
                f"Saved {len(entries)} memory entr{'y' if len(entries) == 1 else 'ies'} "
                "from `/addtomemory`.\n\n"
                + "\n".join(
                    f"- `{entry.memory_id}` {entry.summary or entry.instruction[:140]}"
                    for entry in entries
                )
            )
            trace_store.complete_request(trace.request_id, final_response)
            completed_trace = trace_store.get_trace(trace.request_id)
            if completed_trace is not None:
                conversation_store.append_turn(conversation_id, completed_trace)
            response = {
                "request_id": trace.request_id,
                "stream_url": f"/api/agent/stream/{trace.request_id}",
                "trace_url": f"/api/agent/trace/{trace.request_id}",
                "conversation_id": conversation_id,
                "llm_context_window_tokens": llm_context_window_tokens,
                "llm_context_window_source": _llm_context_window_source,
                "memory_entries": [entry.model_dump(mode="json") for entry in entries],
            }
            if llm_model:
                response["llm_model"] = llm_model
            return response
        backend_runtime_snapshot = _runtime_controls_snapshot()
        requested_workflow_mode = str(
            backend_runtime_snapshot.get("workflow_execution_mode") or "streaming"
        ).strip().lower()
        prompt, literal_context, literal_event_detail = _prepare_literal_payload_prompt(payload.prompt)
        parameter_store_for_macros = _parameter_store_for_macros(settings, agent_runtime)
        prompt, macro_context, macro_event_detail = _prepare_user_macro_prompt(
            prompt,
            preserve_checkonline_hint=requested_workflow_mode == "streaming",
            parameter_store=parameter_store_for_macros,
        )
        trace = trace_store.create_request(prompt)
        if literal_event_detail is not None:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="request_received",
                    level="info",
                    event_type="operator.literal_payload.detected",
                    title="Literal payload detected",
                    summary="Quoted user-authored payload text was shielded from decomposition.",
                    detail=literal_event_detail,
                )
            )
        if macro_event_detail is not None:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="request_received",
                    level="info",
                    event_type="operator.user_macro.detected",
                    title="User macro detected",
                    summary="A deterministic user macro was parsed and attached privately to this request.",
                    detail=macro_event_detail,
                )
            )
        cancel_event = threading.Event()
        with cancel_events_lock:
            cancel_events[trace.request_id] = cancel_event
        raw_context.update(literal_context)
        raw_context.update(macro_context)
        if prompt_requests_parameter_typein_terminal(prompt, parameter_store_for_macros):
            raw_context["parameter_typein_terminal_required"] = True
            raw_context["request_parameter_typein_terminal"] = True
        raw_context.update(_resolve_agent_gateway_routing(prompt, raw_context, gateway_store))
        gateway_routing = dict(raw_context.get("gateway_routing") or {})
        if gateway_routing:
            routed_nickname = str(gateway_routing.get("gateway_nickname") or "").strip()
            routed_node = str(gateway_routing.get("gateway_node") or "").strip()
            mode = str(gateway_routing.get("mode") or "default").strip()
            if routed_nickname:
                routed_label = (
                    f"{routed_nickname} ({routed_node})"
                    if routed_node and routed_node != routed_nickname
                    else routed_nickname
                )
                summary = f"Sending request to gateway {routed_label}."
            elif mode == "default":
                summary = "Sending request to the default gateway."
            else:
                summary = "Sending request to the selected gateway."
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="request_received",
                    level="info",
                    event_type="agent.gateway.routing",
                    title="Gateway routing resolved",
                    summary=summary,
                    detail=gateway_routing,
                )
            )
        gateway_record = gateway_store.get(str(raw_context.get("gateway_id") or "").strip())
        raw_context = _attach_background_terminal_for_typein(
            context=raw_context,
            gateway_record=gateway_record,
            source="request",
        )
        if payload.agent_mode == "advisory":
            advisory_terminal_cwd = str(raw_context.get("terminal_cwd") or "").strip()
            if advisory_terminal_cwd:
                raw_context["advisory_terminal_cwd"] = advisory_terminal_cwd
        if str(raw_context.get("scheduled_event_id") or "").strip():
            request_context = dict(raw_context)
        elif str(raw_context.get("durable_task_id") or "").strip():
            request_context = _apply_trusted_terminal_context(raw_context, terminal_store)
            if str(raw_context.get("terminal_cwd") or "").strip() and not str(
                request_context.get("terminal_cwd") or ""
            ).strip():
                request_context["terminal_cwd"] = str(raw_context.get("terminal_cwd") or "").strip()
            for gateway_key in ("gateway_id", "gateway_node", "gateway_url", "gateway_endpoints"):
                if gateway_key in raw_context and gateway_key not in request_context:
                    request_context[gateway_key] = raw_context[gateway_key]
        else:
            request_context = _apply_trusted_terminal_context(raw_context, terminal_store)
        backend_runtime_snapshot = _apply_backend_runtime_snapshot(request_context)
        clarification_mode = normalize_agent_clarification_mode(
            request_context.get("agent_clarification_mode"),
        )
        has_clarification_override = bool(
            str(
                request_context.get("agent_clarification_mode")
                or ""
            ).strip()
        )
        request_context["agent_clarification_mode"] = (
            clarification_mode
            if has_clarification_override
            else request_context["agent_clarification_mode"]
        )
        if "operator_workspace_cwd_guard_enabled" not in request_context:
            request_context["operator_workspace_cwd_guard_enabled"] = _runtime_controls_snapshot()[
                "operator_workspace_cwd_guard_enabled"
            ]
        if "llm_operator_verbose_enabled" not in request_context:
            request_context["llm_operator_verbose_enabled"] = _runtime_controls_snapshot()[
                "llm_operator_verbose_enabled"
            ]
        _apply_runtime_repair_attempt_context(request_context)
        if online_ai_check_requested_from_context(request_context):
            request_context[ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY] = True
        if online_lookup_requested_from_context(request_context):
            request_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] = True
        if (
            online_lookup_requested_from_context(request_context)
            and str(request_context.get("workflow_execution_mode") or "").strip().lower() != "streaming"
        ):
            lookup_query = sanitize_online_lookup_query(prompt)
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="request_received",
                    level="info",
                    event_type="operator.online_lookup.started",
                    title="Online lookup started",
                    summary="The runtime is fetching one compact online answer for this request.",
                    detail={"provider": COMBINED_ONLINE_LOOKUP_PROVIDER, "query": lookup_query[:500]},
                )
            )
            lookup_result = lookup_online_answer(
                lookup_query,
                timeout_seconds=float(settings.online_ai_check_timeout_seconds),
                gemini_api_key=settings.online_lookup_gemini_api_key,
                gemini_model=settings.online_lookup_gemini_model,
                gemini_api_version=settings.online_lookup_gemini_api_version,
                duck_ai_reuse_browser=bool(settings.online_ai_check_reuse_browser),
                duck_ai_headless=bool(settings.online_ai_check_headless),
                duck_ai_profile_dir=str(settings.online_ai_check_profile_dir),
            )
            lookup_payload = lookup_result.to_context()
            request_context[ONLINE_LOOKUP_CONTEXT_KEY] = lookup_payload
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="request_received",
                    level="info" if lookup_result.available else "warning",
                    event_type=(
                        "operator.online_lookup.completed"
                        if lookup_result.available
                        else "operator.online_lookup.failed"
                    ),
                    title=(
                        "Online lookup completed"
                        if lookup_result.available
                        else "Online lookup unavailable"
                    ),
                    summary=(
                        "A compact online answer was attached to this request."
                        if lookup_result.available
                        else "Online lookup failed; the request will continue without claiming online context."
                    ),
                    detail=_online_lookup_trace_detail(lookup_payload),
                )
            )
        request_context.pop("llm_model", None)
        llm_model = _resolve_agent_request_model(settings, payload.llm_model)
        if llm_model:
            request_context["llm_model"] = llm_model
        llm_context_window_tokens, _llm_context_window_source = _agent_context_window_tokens(
            settings,
            request_context,
        )
        request_context["llm_context_window_tokens"] = llm_context_window_tokens
        conversation_id: str | None = None
        requested_conversation_id = str(payload.conversation_id or "").strip() or None
        if requested_conversation_id:
            if not conversation_store.exists(requested_conversation_id):
                raise HTTPException(
                    status_code=404,
                    detail="Conversation not found or no longer retained.",
                )
            conversation_id = requested_conversation_id
        else:
            conversation_id = conversation_store.create(prompt)
        if payload.agent_mode in {"llm_operator", "advisory"}:
            conversation = conversation_store.get(conversation_id)
            if conversation is not None and conversation.turns:
                conversation_context = _build_operator_conversation_context(
                    conversation=conversation,
                    trace_store=trace_store,
                    settings=settings,
                )
                if payload.agent_mode == "advisory":
                    request_context["advisory_conversation_context"] = conversation_context
                else:
                    request_context["operator_conversation_context"] = conversation_context
            request_context["agent_mode"] = payload.agent_mode
        request_context["conversation_id"] = conversation_id
        _apply_command_allowlist_context(request_context)
        state_context = dict(request_context)
        state_context.pop("operator_conversation_context", None)
        state_context.pop("advisory_conversation_context", None)
        state_store.create(
            request_id=trace.request_id,
            prompt=prompt,
            context=state_context,
            conversation_id=conversation_id,
        )
        worker_target = _run_advisory_request if payload.agent_mode == "advisory" else _run_agent_request
        worker_kwargs = {
            "request_id": trace.request_id,
            "prompt": prompt,
            "request_context": request_context,
            "settings": settings,
            "base_runtime": agent_runtime,
            "store": trace_store,
            "conversation_store": conversation_store,
            "cancel_event": cancel_event,
            "learning_ledger_store": learning_ledger_store,
        }
        if payload.agent_mode != "advisory":
            worker_kwargs["state_store"] = state_store
        worker = threading.Thread(
            target=worker_target,
            kwargs=worker_kwargs,
            daemon=True,
        )
        _track_active_non_task_request(trace.request_id, request_context)
        worker.start()
        response = {
            "request_id": trace.request_id,
            "stream_url": f"/api/agent/stream/{trace.request_id}",
            "trace_url": f"/api/agent/trace/{trace.request_id}",
            "llm_context_window_tokens": llm_context_window_tokens,
            "llm_context_window_source": _llm_context_window_source,
        }
        if llm_model:
            response["llm_model"] = llm_model
        if conversation_id:
            response["conversation_id"] = conversation_id
        gateway_routing = dict(request_context.get("gateway_routing") or {})
        if gateway_routing:
            response["gateway_routing"] = gateway_routing
        return response

    @app.post("/api/agent/confirmation/{request_id}")
    def submit_agent_confirmation(request_id: str, payload: AgentConfirmationPayload) -> dict[str, Any]:
        """Approve or deny a confirmation-gated local UI request without losing its prompt."""

        _trace_or_404(trace_store, request_id)
        state = state_store.get(request_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Confirmation state not found")
        if not state.confirmation_required:
            raise HTTPException(status_code=409, detail="Request is not waiting for confirmation")
        if state.confirmation_handled:
            raise HTTPException(status_code=409, detail="Confirmation was already handled")

        if payload.action == "deny":
            state_store.mark_handled(request_id)
            denied_task_id = str((dict(state.context or {}).get("durable_task_id") or "")).strip()
            denied_attempt_id = str((dict(state.context or {}).get("durable_task_attempt_id") or "")).strip()
            denied_event_run_id = str(
                (dict(state.context or {}).get("scheduled_event_run_id") or "")
            ).strip()
            denied_event_id = str((dict(state.context or {}).get("scheduled_event_id") or "")).strip()
            partial_results_denied = any(
                str(action.get("capability_id") or "") == "operator.partial_results"
                for action in list(state.confirmation_actions or [])
                if isinstance(action, dict)
            )
            final_response = (
                "## Partial Results Denied\n\nThe runtime stopped before downstream actions consumed incomplete command output."
                if partial_results_denied
                else "## Confirmation Denied\n\nNo confirmation-gated actions were executed."
            )
            trace_store.deny_confirmation(
                request_id,
                final_response,
                partial_results_denied=partial_results_denied,
            )
            if denied_event_run_id:
                event_store.update_run(
                    denied_event_run_id,
                    status="cancelled",
                    final_response_preview=final_response,
                    completed_at=utc_now_iso(),
                )
                trace_store.append_event(
                    AgentTraceEvent(
                        request_id=request_id,
                        stage="completed",
                        level="info",
                        event_type="scheduled_event.completed",
                        title="Scheduled event cancelled",
                        summary="The scheduled event run was cancelled after confirmation was denied.",
                        detail={
                            "event_id": denied_event_id,
                            "event_run_id": denied_event_run_id,
                            "status": "cancelled",
                        },
                        )
                    )
            if state.conversation_id:
                conversation_store.upsert_turn(
                    state.conversation_id,
                    request_id=request_id,
                    prompt=state.prompt,
                    final_response=final_response,
                    status="cancelled",
                    confirmation_required=False,
                    clarification_required=False,
                )
            released_non_task = (
                _release_active_non_task_request(request_id)
                if callable(_release_active_non_task_request)
                else False
            )
            if denied_task_id:
                if denied_attempt_id:
                    task_store.update_attempt(
                        denied_attempt_id,
                        status="cancelled",
                        final_response_preview=final_response,
                    )
                task_store.update_task(
                    denied_task_id,
                    AgentTaskUpdate(
                        status="cancelled",
                        current_request_id="",
                        current_attempt_id="",
                        final_response_preview=final_response,
                        blocker_reason="Confirmation was denied.",
                    ),
                )
                _maybe_create_task_notification(
                    task_id=denied_task_id,
                    status="cancelled",
                    request_id=request_id,
                    attempt_id=denied_attempt_id,
                    message="Confirmation was denied.",
                )
                if not released_non_task:
                    _start_next_queued_task()
            elif not released_non_task:
                _start_next_queued_task()
            return {
                "status": "denied",
                "request_id": request_id,
                "conversation_id": state.conversation_id or "",
                "final_response": final_response,
            }

        return _approve_confirmation_request(
            request_id=request_id,
            approval_context_payload=payload.context,
        )

    @app.post("/api/agent/continue/{request_id}")
    def continue_agent_request(request_id: str, payload: AgentContinuationPayload) -> dict[str, Any]:
        """Continue a failed operator request from its completed action records."""

        parent_trace = _trace_or_404(trace_store, request_id)
        state = state_store.get(request_id)
        if state is None or state.planning_trace is None:
            raise HTTPException(status_code=404, detail="Continuation state not found")
        continuation_info = _operator_failure_continuation_info(state.planning_trace)
        if not continuation_info.get("resumable"):
            raise HTTPException(status_code=409, detail="Request is not resumable")

        raw_context = dict(state.context or {})
        requested_final_response_mode = str(
            dict(payload.context or {}).get("llm_operator_final_response_mode") or ""
        ).strip().lower()
        followup_context = _request_followup_context(
            state_context=raw_context,
            followup_context=dict(payload.context or {}),
        )
        followup_context.update(_resolve_agent_gateway_selection(followup_context, gateway_store))
        request_context = _apply_trusted_terminal_context(followup_context, terminal_store)
        backend_runtime_snapshot = _apply_backend_runtime_snapshot(request_context)
        if requested_final_response_mode in {"detailed", "simple"}:
            request_context["llm_operator_final_response_mode"] = requested_final_response_mode
        clarification_mode = normalize_agent_clarification_mode(
            request_context.get("agent_clarification_mode"),
        )
        has_clarification_override = bool(
            str(
                request_context.get("agent_clarification_mode")
                or ""
            ).strip()
        )
        request_context["agent_clarification_mode"] = (
            clarification_mode
            if has_clarification_override
            else backend_runtime_snapshot["agent_clarification_mode"]
        )
        if "operator_workspace_cwd_guard_enabled" not in request_context:
            request_context["operator_workspace_cwd_guard_enabled"] = _runtime_controls_snapshot()[
                "operator_workspace_cwd_guard_enabled"
            ]
        if "llm_operator_verbose_enabled" not in request_context:
            request_context["llm_operator_verbose_enabled"] = _runtime_controls_snapshot()[
                "llm_operator_verbose_enabled"
            ]
        _apply_runtime_repair_attempt_context(request_context)
        if online_ai_check_requested_from_context(request_context):
            request_context[ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY] = True
        request_context["parent_request_id"] = request_id
        request_context["operator_continuation_notes"] = str(payload.notes or "")
        request_context["agent_mode"] = str(
            continuation_info.get("agent_mode")
            or getattr(state.planning_trace, "metadata", {}).get("agent_mode")
            or "llm_operator"
        )
        conversation_id = state.conversation_id or str(continuation_info.get("conversation_id") or "") or None
        if conversation_id:
            request_context["conversation_id"] = conversation_id
        llm_context_window_tokens, _llm_context_window_source = _agent_context_window_tokens(
            settings,
            request_context,
        )
        request_context["llm_context_window_tokens"] = llm_context_window_tokens
        _apply_command_allowlist_context(request_context)

        trace = trace_store.create_request(state.prompt)
        state_store.link_continuation(request_id, trace.request_id)
        _link_task_followup_request(
            parent_request_id=request_id,
            child_request_id=trace.request_id,
            context=request_context,
            trigger="continuation",
        )
        trace_store.append_event(
            AgentTraceEvent(
                request_id=trace.request_id,
                stage="request_received",
                level="info",
                event_type="operator.continuation.started",
                title="Continue from failure",
                summary="The user requested a failure-aware continuation of an earlier run.",
                detail={
                    "parent_request_id": request_id,
                    "parent_status": parent_trace.status,
                    "continuation": continuation_info,
                },
            )
        )
        cancel_event = threading.Event()
        with cancel_events_lock:
            cancel_events[trace.request_id] = cancel_event
        state_store.create(
            request_id=trace.request_id,
            prompt=state.prompt,
            context=request_context,
            parent_request_id=request_id,
            conversation_id=conversation_id,
        )
        worker = threading.Thread(
            target=_run_agent_request,
            kwargs={
                "request_id": trace.request_id,
                "prompt": state.prompt,
                "request_context": request_context,
                "settings": settings,
                "base_runtime": agent_runtime,
                "store": trace_store,
                "state_store": state_store,
                "conversation_store": conversation_store,
                "continuation": True,
                "planning_trace": state.planning_trace,
                "cancel_event": cancel_event,
            },
            daemon=True,
        )
        _track_active_non_task_request(trace.request_id, request_context)
        worker.start()
        response = {
            "request_id": trace.request_id,
            "parent_request_id": request_id,
            "stream_url": f"/api/agent/stream/{trace.request_id}",
            "trace_url": f"/api/agent/trace/{trace.request_id}",
            "llm_context_window_tokens": llm_context_window_tokens,
            "llm_context_window_source": _llm_context_window_source,
        }
        if conversation_id:
            response["conversation_id"] = conversation_id
        return response

    @app.post("/api/agent/clarification/{request_id}")
    def submit_agent_clarification(request_id: str, payload: AgentClarificationPayload) -> dict[str, Any]:
        """Resume one clarification-gated request with the user's answer."""

        _trace_or_404(trace_store, request_id)
        state = state_store.get(request_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Clarification state not found")
        if not state.clarification_required or not isinstance(state.clarification_request, dict):
            raise HTTPException(status_code=409, detail="Request is not waiting for clarification")
        if state.clarification_handled:
            raise HTTPException(status_code=409, detail="Clarification was already handled")

        answer = str(payload.answer or "").strip()
        if not answer:
            raise HTTPException(status_code=400, detail="Clarification answer is required")

        replay_context = dict(state.context or {})
        _drop_removed_operator_context_keys(replay_context)
        raw_clarification_context = dict(payload.context or {})
        followup_context = _request_followup_context(
            state_context=replay_context,
            followup_context=raw_clarification_context,
        )
        followup_context.update(_resolve_agent_gateway_selection(followup_context, gateway_store))
        clarification_context = _apply_trusted_terminal_context(followup_context, terminal_store)
        replay_context.update(
            {
                key: clarification_context[key]
                for key in (
                    "terminal_session_id",
                    "terminal_cwd",
                    "execute_in_terminal",
                    "gateway_id",
                    "gateway_node",
                    "gateway_url",
                    "gateway_endpoints",
                )
                if key in clarification_context
            }
        )
        _apply_backend_runtime_snapshot(replay_context)
        _apply_runtime_repair_attempt_context(replay_context)
        llm_context_window_tokens, _llm_context_window_source = _agent_context_window_tokens(
            settings,
            replay_context,
        )
        replay_context["llm_context_window_tokens"] = llm_context_window_tokens
        _apply_command_allowlist_context(replay_context)
        clarification_answer_entry = _attach_clarification_typein_macro(
            settings=settings,
            agent_runtime=agent_runtime,
            replay_context=replay_context,
            clarification_request=state.clarification_request,
            payload=payload,
            answer=answer,
        )
        state_store.mark_clarification_handled(request_id)
        previous_clarifications = list(replay_context.get("clarifications") or [])
        if not isinstance(previous_clarifications, list):
            previous_clarifications = []
        clarification_entry = {
            "question": state.clarification_request.get("question", ""),
            "answer": str(clarification_answer_entry.get("answer") or ""),
            "selected_option_id": str(clarification_answer_entry.get("selected_option_id") or ""),
            "parameter_choice_id": str(clarification_answer_entry.get("parameter_choice_id") or ""),
            "parameter_key": str(clarification_answer_entry.get("parameter_key") or ""),
            "parameter_field": str(clarification_answer_entry.get("parameter_field") or ""),
            "answer_is_secret": bool(clarification_answer_entry.get("answer_is_secret")),
            "answer_redacted": bool(clarification_answer_entry.get("answer_redacted")),
            "source": str(clarification_answer_entry.get("source") or ""),
            "reason": state.clarification_request.get("reason", ""),
            "missing_information": state.clarification_request.get("missing_information", ""),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        replay_context["clarifications"] = [*previous_clarifications, clarification_entry][-10:]
        if replay_context.get(USER_MACRO_PRIVATE_CONTEXT_KEY):
            gateway_record = gateway_store.get(str(replay_context.get("gateway_id") or "").strip())
            replay_context = _attach_background_terminal_for_typein(
                context=replay_context,
                gateway_record=gateway_record,
                source="clarification",
            )

        if state.conversation_id and conversation_store.exists(state.conversation_id):
            conversation = conversation_store.get(state.conversation_id)
            if conversation is not None and conversation.turns:
                replay_context["operator_conversation_context"] = _build_operator_conversation_context(
                    conversation=conversation,
                    trace_store=trace_store,
                    settings=settings,
                )
            replay_context["agent_mode"] = "llm_operator"
            replay_context["conversation_id"] = state.conversation_id

        planning_trace = state.planning_trace
        trace_metadata = getattr(planning_trace, "metadata", None)
        if not isinstance(trace_metadata, dict):
            trace_metadata = {}
        sql_pending_state = trace_metadata.get("sql_agent_pending_state")
        if isinstance(sql_pending_state, dict) and sql_pending_state:
            replay_context["sql_agent_pending"] = dict(sql_pending_state)
        has_saved_operator_plan = isinstance(trace_metadata.get("operator_plan"), dict)
        has_saved_streaming_state = isinstance(trace_metadata.get("operator_streaming_state"), dict)
        resume_from_saved_operator_state = bool(
            planning_trace is not None
            and trace_metadata.get("operator_clarification_pending")
            and (has_saved_operator_plan or has_saved_streaming_state)
        )
        if resume_from_saved_operator_state:
            replay_context["operator_resume_after_clarification"] = True

        resumed = trace_store.create_request(state.prompt)
        state_store.link_continuation(request_id, resumed.request_id)
        _link_task_followup_request(
            parent_request_id=request_id,
            child_request_id=resumed.request_id,
            context=replay_context,
            trigger="clarification",
        )
        scheduled_event_run_id = str(replay_context.get("scheduled_event_run_id") or "").strip()
        scheduled_event_id = str(replay_context.get("scheduled_event_id") or "").strip()
        persisted_for_event = False
        if payload.persist_for_event and scheduled_event_id and scheduled_event_run_id:
            scheduled_event = event_store.get_event(scheduled_event_id)
            if scheduled_event is not None:
                event_context = dict(scheduled_event.context or {})
                persisted_clarifications = list(event_context.get("clarifications") or [])
                if not isinstance(persisted_clarifications, list):
                    persisted_clarifications = []
                question_key = " ".join(str(clarification_entry.get("question") or "").split()).lower()
                missing_key = " ".join(
                    str(clarification_entry.get("missing_information") or "").split()
                ).lower()

                def _same_clarification(existing: Any) -> bool:
                    if not isinstance(existing, dict):
                        return False
                    existing_question = " ".join(str(existing.get("question") or "").split()).lower()
                    existing_missing = " ".join(
                        str(existing.get("missing_information") or "").split()
                    ).lower()
                    return bool(question_key and existing_question == question_key) or bool(
                        missing_key and existing_missing == missing_key
                    )

                persisted_entry = {
                    **clarification_entry,
                    "persisted_for_event": True,
                    "persisted_at": utc_now_iso(),
                    "source_request_id": request_id,
                }
                event_context["clarifications"] = [
                    *[
                        item
                        for item in persisted_clarifications
                        if not _same_clarification(item)
                    ],
                    persisted_entry,
                ][-10:]
                event_store.update_event(
                    scheduled_event_id,
                    AgentEventUpdate(context=event_context),
                )
                persisted_for_event = True
        if scheduled_event_run_id:
            event_store.update_run(
                scheduled_event_run_id,
                request_id=resumed.request_id,
                parent_request_id=request_id,
                status="running",
                completed_at="",
            )
        cancel_event = threading.Event()
        with cancel_events_lock:
            cancel_events[resumed.request_id] = cancel_event
        state_store.create(
            request_id=resumed.request_id,
            prompt=state.prompt,
            context={key: value for key, value in replay_context.items() if key != "operator_conversation_context"},
            parent_request_id=request_id,
            conversation_id=state.conversation_id,
        )
        trace_store.append_event(
            AgentTraceEvent(
                request_id=resumed.request_id,
                stage="request_received",
                level="info",
                event_type="clarification.answered",
                title="Clarification answered",
                summary="The request is continuing with the user's clarification answer.",
                detail={
                    "parent_request_id": request_id,
                    "selected_option_id": clarification_entry.get("selected_option_id"),
                    "parameter_choice_id": clarification_entry.get("parameter_choice_id"),
                    "parameter_key": clarification_entry.get("parameter_key"),
                    "answer_redacted": clarification_entry.get("answer_redacted"),
                    "question": state.clarification_request.get("question", ""),
                    "persisted_for_event": persisted_for_event,
                },
            )
        )
        if persisted_for_event:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=resumed.request_id,
                    stage="request_received",
                    level="info",
                    event_type="scheduled_event.clarification_saved",
                    title="Event clarification saved",
                    summary="This clarification answer will be reused by future scheduled runs of the event.",
                    detail={
                        "event_id": scheduled_event_id,
                        "event_run_id": scheduled_event_run_id,
                        "parent_request_id": request_id,
                        "question": state.clarification_request.get("question", ""),
                        "missing_information": state.clarification_request.get("missing_information", ""),
                    },
                )
            )

        def _run_clarified_request_worker() -> None:
            _run_agent_request(
                request_id=resumed.request_id,
                prompt=state.prompt,
                request_context=replay_context,
                settings=settings,
                base_runtime=agent_runtime,
                store=trace_store,
                state_store=state_store,
                conversation_store=conversation_store,
                confirmation=resume_from_saved_operator_state,
                planning_trace=planning_trace if resume_from_saved_operator_state else None,
                cancel_event=cancel_event,
                learning_ledger_store=learning_ledger_store,
            )
            if not scheduled_event_run_id:
                return
            trace = trace_store.get_trace(resumed.request_id)
            scheduled_event = event_store.get_event(scheduled_event_id) if scheduled_event_id else None
            if (
                scheduled_event is not None
                and _event_run_status_from_trace(trace) == "awaiting_confirmation"
                and scheduled_event.auto_approve_confirmations
                and trace is not None
            ):
                _auto_approve_scheduled_event(
                    event=scheduled_event,
                    event_run_id=scheduled_event_run_id,
                    parent_request_id=resumed.request_id,
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

        worker = threading.Thread(
            target=_run_clarified_request_worker,
            daemon=True,
        )
        _track_active_non_task_request(resumed.request_id, replay_context)
        worker.start()
        response = {
            "status": "answered",
            "request_id": resumed.request_id,
            "stream_url": f"/api/agent/stream/{resumed.request_id}",
            "trace_url": f"/api/agent/trace/{resumed.request_id}",
            "llm_context_window_tokens": llm_context_window_tokens,
            "llm_context_window_source": _llm_context_window_source,
            "persisted_for_event": persisted_for_event,
        }
        if state.conversation_id:
            response["conversation_id"] = state.conversation_id
        return response

    @app.post("/api/agent/stop/{request_id}")
    def stop_agent_request(request_id: str) -> dict[str, str]:
        """Stop one in-flight local UI request."""

        trace = _trace_or_404(trace_store, request_id)
        if trace.status in {"completed", "failed", "cancelled"}:
            return {"status": trace.status, "request_id": request_id}
        _cancel_agent_request(request_id)
        return {"status": "cancelled", "request_id": request_id}

    @app.get("/api/agent/trace/{request_id}")
    def get_agent_trace(request_id: str) -> dict[str, Any]:
        """Return the current stored trace for one request."""

        trace = trace_store.get_trace(request_id)
        if trace is not None:
            payload = trace.model_dump(mode="json")
            payload["reliability_summary"] = _reliability_trace_summary(request_id)
            return payload
        turn = conversation_store.get_turn(request_id)
        if turn is not None:
            payload = _trace_payload_from_chat_turn(turn)
            payload["reliability_summary"] = _reliability_trace_summary(request_id)
            return payload
        raise HTTPException(status_code=404, detail="Trace not found")

    @app.get("/api/agent/raw/{request_id}/{data_ref}")
    def get_agent_raw_payload(request_id: str, data_ref: str) -> dict[str, Any]:
        """Return a stored raw preview/full-payload record for local debugging."""

        _trace_or_404(trace_store, request_id)
        payload = trace_store.get_raw_payload(request_id, data_ref)
        if payload is None:
            raise HTTPException(status_code=404, detail="Raw payload not found")
        return payload

    @app.get("/api/agent/file")
    def get_agent_file(path: str) -> FileResponse:
        """Open a workspace-bounded file referenced by the final response pane."""

        resolved = _resolve_agent_ui_file(settings, path)
        media_type = mimetypes.guess_type(str(resolved))[0] or "text/plain"
        return FileResponse(
            resolved,
            media_type=media_type,
            filename=resolved.name,
            content_disposition_type="inline",
        )

    @app.post("/api/agent/restart")
    def restart_agent_server() -> dict[str, Any]:
        """Restart the local agent server process and configured gateway."""

        gateway_restart = _request_gateway_restart(settings)
        callback = getattr(app.state, "agent_ui_restart_callback", None)
        if callable(callback):
            callback()
            return {"status": "restarting", "mode": "callback", "gateway": gateway_restart}
        _schedule_process_restart()
        return {"status": "restarting", "mode": "exec", "pid": os.getpid(), "gateway": gateway_restart}

    @app.get("/api/agent/stream/{request_id}")
    def stream_agent_trace(request_id: str, after_id: int | None = None) -> StreamingResponse:
        """Stream trace events for one request using SSE."""

        _trace_or_404(trace_store, request_id)

        def event_stream():
            cursor = int(after_id or 0)
            while True:
                trace = trace_store.get_trace(request_id)
                if trace is None:
                    return
                events = trace_store.events_after(request_id, after_id=cursor)
                for event in events:
                    cursor = int(event.id)
                    yield format_sse("trace", event)
                trace = trace_store.get_trace(request_id)
                if trace is None or trace.status in {"completed", "failed", "cancelled"}:
                    for event in trace_store.events_after(request_id, after_id=cursor):
                        cursor = int(event.id)
                        yield format_sse("trace", event)
                    return
                try:
                    waited = trace_store.wait_for_events(
                        request_id,
                        after_id=cursor,
                        timeout_seconds=15,
                    )
                except KeyError:
                    return
                if not waited:
                    yield format_sse("heartbeat", {"request_id": request_id, "after_id": cursor})

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    for _name in (
        'submit_agent_request',
        'submit_agent_confirmation',
        'continue_agent_request',
        'submit_agent_clarification',
        'stop_agent_request',
        'get_agent_trace',
        'get_agent_raw_payload',
        'get_agent_file',
        'restart_agent_server',
        'stream_agent_trace',
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
