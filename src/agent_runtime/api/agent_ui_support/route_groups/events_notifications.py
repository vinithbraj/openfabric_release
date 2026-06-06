"""Events Notifications Agent UI routes."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared import *


def register_events_notifications_routes(ctx: AgentUiRouteContext) -> None:
    """Register events notifications routes."""

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

    @app.post("/api/agent/events/draft")
    def draft_agent_events(payload: AgentEventDraftRequest) -> dict[str, Any]:
        """Draft editable scheduled events from natural language."""

        if not _events_enabled():
            return {
                "draft": AgentEventDraftResponse(
                    is_schedule_request=False,
                    rationale="Agent events are disabled.",
                ).model_dump(mode="json")
            }
        prompt, context = _prepare_event_prompt_and_context(
            payload.prompt,
            payload.context,
            preserve_existing_context_macros=True,
        )
        macro_context = _event_macro_context_from(context)
        payload = payload.model_copy(
            update={"prompt": prompt, "context": _event_context_for_api(context)}
        )
        draft = _draft_agent_events(settings=settings, agent_runtime=agent_runtime, payload=payload)
        if macro_context:
            draft = draft.model_copy(
                update={
                    "drafts": [
                        item.model_copy(update={"context": {**dict(item.context or {}), **macro_context}})
                        for item in draft.drafts
                    ]
                }
            )
        return {"draft": _event_draft_response_api_payload(draft)}

    @app.post("/api/agent/events/from-prompt")
    def create_agent_events_from_prompt(payload: AgentEventDraftRequest) -> dict[str, Any]:
        """Create scheduled events directly from natural language."""

        if not _events_enabled():
            return {
                "is_schedule_request": False,
                "created_count": 0,
                "events": [],
                "missing_details": [],
                "message": "Agent events are disabled.",
            }
        prompt, context = _prepare_event_prompt_and_context(
            payload.prompt,
            payload.context,
            preserve_existing_context_macros=True,
        )
        macro_context = _event_macro_context_from(context)
        payload = payload.model_copy(
            update={"prompt": prompt, "context": _event_context_for_api(context)}
        )
        draft = _draft_agent_events(settings=settings, agent_runtime=agent_runtime, payload=payload)
        if not draft.is_schedule_request:
            return {
                "is_schedule_request": False,
                "created_count": 0,
                "events": [],
                "missing_details": [],
                "message": "",
            }
        if macro_context:
            draft = draft.model_copy(
                update={
                    "drafts": [
                        item.model_copy(update={"context": {**dict(item.context or {}), **macro_context}})
                        for item in draft.drafts
                    ]
                }
            )
        if not draft.drafts:
            return {
                "is_schedule_request": True,
                "created_count": 0,
                "events": [],
                "missing_details": list(draft.missing_details or []),
                "message": _event_needs_detail_message(draft),
            }
        created: list[AgentEventRecord] = []
        for item in draft.drafts:
            event = event_store.create_event(_event_create_payload_from_draft(item))
            created.append(event)
        return {
            "is_schedule_request": True,
            "created_count": len(created),
            "events": [_event_record_api_payload(event) for event in created],
            "missing_details": [],
            "message": _saved_events_message(created),
        }

    @app.get("/api/agent/events")
    def list_agent_events(include_deleted: bool = False, run_limit: int = 5) -> dict[str, Any]:
        """List persistent scheduled events and recent runs."""

        if not _events_enabled():
            return {"enabled": False, "events": [], "runs": {}, "stats": {"total": 0}}
        events = event_store.list_events(include_deleted=include_deleted)
        runs: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            event_runs = _reconcile_event_runs(event.event_id, limit=max(1, min(int(run_limit or 5), 50)))
            blocking_run = event_store.get_open_run(event.event_id)
            runs[event.event_id] = [
                _event_run_api_payload(run, blocking_run=blocking_run)
                for run in event_runs
            ]
        status_counts: dict[str, int] = {}
        for event in events:
            status_counts[event.status] = status_counts.get(event.status, 0) + 1
        return {
            "enabled": True,
            "events": [_event_record_api_payload(event) for event in events],
            "runs": runs,
            "stats": {"total": len(events), **status_counts},
        }

    @app.post("/api/agent/events")
    def create_agent_event(payload: AgentEventCreate) -> dict[str, Any]:
        """Create one persistent scheduled event."""

        if not _events_enabled():
            raise HTTPException(status_code=404, detail="Agent events are disabled.")
        prompt, context = _prepare_event_prompt_and_context(
            payload.prompt,
            payload.context,
            preserve_existing_context_macros=True,
        )
        updates: dict[str, Any] = {"prompt": prompt, "context": context}
        if "action_type" not in payload.model_fields_set:
            action_type = (
                "notification"
                if _looks_like_notification_event_prompt(prompt)
                else payload.action_type
            )
            updates["action_type"] = action_type
            if action_type == "notification" and not str(payload.notification_message or "").strip():
                updates["notification_message"] = _event_notification_message(prompt)
        payload = payload.model_copy(update=updates)
        event = event_store.create_event(payload)
        return {"event": _event_record_api_payload(event)}

    @app.patch("/api/agent/events/{event_id}")
    def update_agent_event(event_id: str, payload: AgentEventUpdate) -> dict[str, Any]:
        """Patch one persistent scheduled event."""

        if not _events_enabled():
            raise HTTPException(status_code=404, detail="Agent events are disabled.")
        existing = event_store.get_event(event_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Scheduled event not found.")
        raw_update = payload.model_dump(exclude_unset=True)
        if "prompt" in raw_update:
            prompt = str(raw_update["prompt"] or "")
            source_context = (
                dict(raw_update["context"])
                if isinstance(raw_update.get("context"), dict)
                else dict(existing.context or {})
            )
            if isinstance(raw_update.get("context"), dict) and not _event_macro_context_from(
                source_context
            ).get(USER_MACRO_PRIVATE_CONTEXT_KEY):
                source_context.update(_event_macro_context_from(existing.context))
            prompt, context = _prepare_event_prompt_and_context(
                prompt,
                source_context,
                preserve_existing_context_macros=False,
            )
            raw_update["prompt"] = prompt
            raw_update["context"] = context
            if "action_type" not in raw_update:
                action_type = _event_action_type_for_task(
                    prompt,
                    original_prompt=prompt,
                    context=context,
                )
                raw_update["action_type"] = action_type
                if action_type == "notification" and not str(raw_update.get("notification_message") or "").strip():
                    raw_update["notification_message"] = _event_notification_message(prompt)
            payload = AgentEventUpdate.model_validate(raw_update)
        elif isinstance(raw_update.get("context"), dict):
            context = _resolve_pending_event_macro_context(raw_update["context"])
            if not _event_macro_context_from(context).get(USER_MACRO_PRIVATE_CONTEXT_KEY):
                context.update(_event_macro_context_from(existing.context))
            raw_update["context"] = context
            payload = AgentEventUpdate.model_validate(raw_update)
        event = event_store.update_event(event_id, payload)
        if event is None:
            raise HTTPException(status_code=404, detail="Scheduled event not found.")
        return {"event": _event_record_api_payload(event)}

    @app.delete("/api/agent/events/{event_id}")
    def delete_agent_event(event_id: str) -> dict[str, Any]:
        """Soft-delete one scheduled event while preserving run history."""

        if not _events_enabled():
            raise HTTPException(status_code=404, detail="Agent events are disabled.")
        event = event_store.delete_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Scheduled event not found.")
        return {"event": _event_record_api_payload(event)}

    @app.post("/api/agent/events/{event_id}/trigger")
    def trigger_agent_event(event_id: str) -> dict[str, Any]:
        """Run one scheduled event immediately."""

        if not _events_enabled():
            raise HTTPException(status_code=404, detail="Agent events are disabled.")
        event = event_store.get_event(event_id)
        if event is None or event.status == "deleted":
            raise HTTPException(status_code=404, detail="Scheduled event not found.")
        _reconcile_event_runs(event.event_id, limit=event_store.max_run_history)
        if not event_store.can_start_run(event.event_id):
            raise HTTPException(status_code=409, detail="Scheduled event already has an open run.")
        request_id = _launch_scheduled_event(event, utc_now_iso())
        launched_run = event_store.get_open_run(event.event_id)
        response = {
            "status": "triggered",
            "event": _event_record_api_payload(event),
            "request_id": request_id,
            "trace_url": f"/api/agent/trace/{request_id}" if request_id else "",
        }
        if launched_run is not None and launched_run.task_id:
            response["task_id"] = launched_run.task_id
        if request_id:
            response["stream_url"] = f"/api/agent/stream/{request_id}"
        elif event.event_kind != "todo":
            response["notification"] = True
            response["notifications"] = [
                _notification_api_payload(notification)
                for notification in event_store.list_notifications(limit=5)
                if notification.event_id == event.event_id
            ][:1]
        return response

    @app.get("/api/agent/events/{event_id}/runs")
    def list_agent_event_runs(event_id: str, limit: int = 100) -> dict[str, Any]:
        """List run history for one scheduled event."""

        if not _events_enabled():
            raise HTTPException(status_code=404, detail="Agent events are disabled.")
        event = event_store.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Scheduled event not found.")
        runs = _reconcile_event_runs(event_id, limit=max(1, min(int(limit or 100), 500)))
        blocking_run = event_store.get_open_run(event_id)
        return {
            "event": _event_record_api_payload(event),
            "runs": [_event_run_api_payload(run, blocking_run=blocking_run) for run in runs],
        }

    @app.post("/api/agent/events/{event_id}/runs/{event_run_id}/cancel")
    def cancel_agent_event_run(event_id: str, event_run_id: str) -> dict[str, Any]:
        """Cancel one open scheduled event run and unblock future ticks."""

        if not _events_enabled():
            raise HTTPException(status_code=404, detail="Agent events are disabled.")
        event = event_store.get_event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Scheduled event not found.")
        run = event_store.get_run(event_run_id)
        if run is None or run.event_id != event.event_id:
            raise HTTPException(status_code=404, detail="Scheduled event run not found.")
        if run.status not in {"queued", "running", "awaiting_confirmation", "awaiting_clarification"}:
            blocking_run = event_store.get_open_run(event.event_id)
            return {
                "status": run.status,
                "event": _event_record_api_payload(event),
                "run": _event_run_api_payload(run, blocking_run=blocking_run),
            }

        if run.request_id:
            _cancel_agent_request(
                run.request_id,
                scheduled_event_id=event.event_id,
                scheduled_event_run_id=run.event_run_id,
            )
        if run.task_id:
            task_store.update_task(
                run.task_id,
                AgentTaskUpdate(
                    status="cancelled",
                    current_request_id="",
                    current_attempt_id="",
                    blocker_reason="This scheduled task was cancelled from Event History.",
                ),
            )
        cancelled = event_store.update_run(
            run.event_run_id,
            status="cancelled",
            final_response_preview=(
                "This scheduled event run was cancelled from Event History. "
                "Future scheduled ticks are unblocked."
            ),
            completed_at=utc_now_iso(),
        ) or run
        _maybe_create_event_notification(
            event=event,
            status="cancelled",
            preview=cancelled.final_response_preview,
            event_run_id=cancelled.event_run_id,
            request_id=cancelled.request_id,
        )
        blocking_run = event_store.get_open_run(event.event_id)
        return {
            "status": "cancelled",
            "event": _event_record_api_payload(event),
            "run": _event_run_api_payload(cancelled, blocking_run=blocking_run),
        }

    @app.get("/api/agent/notifications")
    def list_agent_notifications(
        status: str | None = None,
        limit: int = 50,
        include_dismissed: bool = False,
    ) -> dict[str, Any]:
        """List persisted in-app Agent UI notifications."""

        normalized_status = str(status or "").strip().lower() or None
        if normalized_status and normalized_status not in {"unread", "read", "dismissed"}:
            raise HTTPException(status_code=400, detail="Invalid notification status.")
        notifications = event_store.list_notifications(
            status=normalized_status,  # type: ignore[arg-type]
            include_dismissed=include_dismissed,
            limit=max(1, min(int(limit or 50), 500)),
        )
        return {
            "notifications": [
                _notification_api_payload(notification) for notification in notifications
            ],
            "counts": event_store.notification_counts(),
        }

    @app.patch("/api/agent/notifications/{notification_id}")
    def update_agent_notification(
        notification_id: str,
        payload: AgentNotificationUpdate,
    ) -> dict[str, Any]:
        """Mark one persisted in-app notification read or dismissed."""

        if payload.status not in {"read", "dismissed"}:
            raise HTTPException(status_code=400, detail="Notification status must be read or dismissed.")
        notification = event_store.update_notification(notification_id, payload)
        if notification is None:
            raise HTTPException(status_code=404, detail="Notification not found.")
        return {
            "notification": _notification_api_payload(notification),
            "counts": event_store.notification_counts(),
        }

    @app.post("/api/agent/notifications/mark-all-read")
    def mark_all_agent_notifications_read() -> dict[str, Any]:
        """Mark all unread Agent UI notifications as read."""

        updated = event_store.mark_all_notifications_read()
        return {"updated": updated, "counts": event_store.notification_counts()}

    for _name in (
        'draft_agent_events',
        'create_agent_events_from_prompt',
        'list_agent_events',
        'create_agent_event',
        'update_agent_event',
        'delete_agent_event',
        'trigger_agent_event',
        'list_agent_event_runs',
        'cancel_agent_event_run',
        'list_agent_notifications',
        'update_agent_notification',
        'mark_all_agent_notifications_read',
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
