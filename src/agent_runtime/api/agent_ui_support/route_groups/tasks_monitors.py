"""Tasks Monitors Agent UI routes."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared import *


def register_tasks_monitors_routes(ctx: AgentUiRouteContext) -> None:
    """Register tasks monitors routes."""

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
    monitor_manager = ctx.monitor_manager
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

    @app.post("/api/agent/monitors/draft")
    def draft_agent_monitor(payload: AgentMonitorDraftRequest) -> dict[str, Any]:
        """Draft monitor settings from a user prompt."""

        return {"draft": _monitor_draft_payload(monitor_manager.draft_monitor(payload))}

    @app.get("/api/agent/monitors")
    def list_agent_monitors(
        status: str | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List persistent Agent UI monitors."""

        normalized_status = str(status or "").strip().lower() or None
        valid_statuses = {
            "queued",
            "running",
            "triggered",
            "completed",
            "failed",
            "cancelled",
            "paused",
            "interrupted",
            "archived",
        }
        if normalized_status and normalized_status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Invalid monitor status.")
        return monitor_manager.list_monitors(
            status=normalized_status,
            limit=max(1, min(int(limit or 50), 500)),
            include_archived=include_archived,
        )

    @app.post("/api/agent/monitors")
    def create_agent_monitor(payload: AgentMonitorCreatePayload) -> dict[str, Any]:
        """Create one monitor, optionally starting it immediately."""

        raw_context = dict(payload.context or {})
        _drop_removed_operator_context_keys(raw_context)
        gateway_id = str(payload.gateway_id or raw_context.get("gateway_id") or "").strip()
        if gateway_id:
            raw_context["gateway_id"] = gateway_id
        fields_set = set(getattr(payload, "model_fields_set", set()))
        prompt = str(payload.prompt or "").strip()
        command = str(payload.command or "").strip()
        mode = payload.mode
        title = payload.title or ""
        interval_seconds = max(1, int(payload.interval_seconds or 5))
        duration_seconds = max(1, int(payload.duration_seconds or 300))
        condition = str(payload.condition or "").strip()
        natural_language_condition = str(payload.natural_language_condition or "").strip()
        trigger_mode = payload.trigger_mode
        action_prompt = str(payload.action_prompt or "").strip()
        planner_rationale = str(payload.planner_rationale or "").strip()
        risk_notes = str(payload.risk_notes or "").strip()
        judge_interval_seconds = max(1, int(payload.judge_interval_seconds or 5))
        if not command and prompt:
            draft = monitor_manager.draft_monitor(
                AgentMonitorDraftRequest(
                    prompt=prompt,
                    context=raw_context,
                    agent_mode=payload.agent_mode,
                    llm_model=None,
                )
            )
            first = draft.drafts[0] if draft.drafts else None
            if first is not None and first.command and not first.missing_details:
                command = first.command
                mode = first.mode
                title = title or first.title
                if "interval_seconds" not in fields_set:
                    interval_seconds = first.interval_seconds
                if "duration_seconds" not in fields_set:
                    duration_seconds = first.duration_seconds
                if "condition" not in fields_set:
                    condition = first.condition
                if "natural_language_condition" not in fields_set:
                    natural_language_condition = first.natural_language_condition
                if "trigger_mode" not in fields_set:
                    trigger_mode = first.trigger_mode
                if "action_prompt" not in fields_set:
                    action_prompt = first.action_prompt
                planner_rationale = planner_rationale or first.planner_rationale
                risk_notes = risk_notes or first.risk_notes
                if "judge_interval_seconds" not in fields_set:
                    judge_interval_seconds = first.judge_interval_seconds
            else:
                missing = sorted({item for item in (draft.missing_details or (first.missing_details if first else [])) if item})
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "A safe monitor command could not be inferred.",
                        "missing_details": missing or ["command"],
                    },
                )
        if not command:
            raise HTTPException(status_code=422, detail="A monitor command or prompt is required.")
        create_payload = AgentMonitorCreate(
            prompt=prompt,
            title=title,
            mode=mode,
            command=command,
            interval_seconds=interval_seconds,
            duration_seconds=duration_seconds,
            condition=condition,
            natural_language_condition=natural_language_condition,
            trigger_mode=trigger_mode,
            action_prompt=action_prompt,
            planner_rationale=planner_rationale,
            risk_notes=risk_notes,
            judge_interval_seconds=judge_interval_seconds,
            status="queued",
            agent_mode=payload.agent_mode,
            conversation_id=str(payload.conversation_id or "").strip(),
            gateway_id=gateway_id,
            context=raw_context,
        )
        try:
            return monitor_manager.create_monitor(create_payload, start_now=bool(payload.start_now))
        except AgentMonitorConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/agent/monitors/{monitor_id}")
    def get_agent_monitor(monitor_id: str) -> dict[str, Any]:
        """Return one persistent monitor."""

        try:
            return monitor_manager.get_monitor(monitor_id)
        except AgentMonitorNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/agent/monitors/{monitor_id}/observations")
    def list_agent_monitor_observations(
        monitor_id: str,
        limit: int = 200,
        after_sequence: int = 0,
    ) -> dict[str, Any]:
        """Return observations for one monitor."""

        try:
            return monitor_manager.list_observations(
                monitor_id,
                limit=max(1, min(int(limit or 200), 1000)),
                after_sequence=max(0, int(after_sequence or 0)),
            )
        except AgentMonitorNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/agent/monitors/{monitor_id}/stream")
    def stream_agent_monitor(monitor_id: str, after_sequence: int = 0) -> StreamingResponse:
        """Stream monitor status and observations over SSE."""

        try:
            stream = monitor_manager.stream_events(monitor_id, after_sequence=after_sequence)
        except AgentMonitorNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return StreamingResponse(stream, media_type="text/event-stream")

    @app.post("/api/agent/monitors/{monitor_id}/start")
    def start_agent_monitor(monitor_id: str) -> dict[str, Any]:
        """Start one monitor in a background terminal."""

        return _start_monitor(monitor_id)

    @app.post("/api/agent/monitors/{monitor_id}/pause")
    def pause_agent_monitor(monitor_id: str) -> dict[str, Any]:
        """Pause one monitor."""

        return _cancel_monitor(monitor_id, status="paused")

    @app.post("/api/agent/monitors/{monitor_id}/cancel")
    def cancel_agent_monitor(monitor_id: str) -> dict[str, Any]:
        """Cancel one monitor."""

        return _cancel_monitor(monitor_id, status="cancelled")

    @app.post("/api/agent/monitors/{monitor_id}/archive")
    def archive_agent_monitor(monitor_id: str) -> dict[str, Any]:
        """Archive one monitor."""

        try:
            return monitor_manager.archive_monitor(monitor_id)
        except AgentMonitorNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/agent/tasks")
    def list_agent_tasks(
        status: str | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List durable Agent UI tasks."""

        normalized_status = str(status or "").strip().lower() or None
        valid_statuses = {
            "queued",
            "running",
            "awaiting_confirmation",
            "awaiting_clarification",
            "interrupted",
            "completed",
            "failed",
            "cancelled",
            "archived",
        }
        if normalized_status and normalized_status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Invalid task status.")
        _reconcile_open_tasks()
        tasks = task_store.list_tasks(
            status=normalized_status,
            limit=max(1, min(int(limit or 50), 500)),
            include_archived=include_archived,
        )
        counts: dict[str, int] = {}
        for task in task_store.list_tasks(limit=500, include_archived=True):
            counts[task.status] = counts.get(task.status, 0) + 1
            if task.archived_at:
                counts["archived"] = counts.get("archived", 0) + 1
        return {
            "tasks": [_task_record_payload(task) for task in tasks],
            "counts": counts,
            "active_task_id": (task_store.get_open_task().task_id if task_store.get_open_task() else ""),
        }

    @app.delete("/api/agent/tasks")
    def clear_agent_tasks(
        status: str = "archived",
        limit: int = 500,
        include_archived: bool = True,
    ) -> dict[str, Any]:
        """Hard-delete a filtered batch of inactive durable tasks."""

        normalized_status = str(status or "archived").strip().lower()
        valid_statuses = {
            "queued",
            "running",
            "awaiting_confirmation",
            "awaiting_clarification",
            "interrupted",
            "completed",
            "failed",
            "cancelled",
            "archived",
        }
        if normalized_status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Invalid task status.")
        candidates = task_store.list_tasks(
            status=normalized_status,
            limit=max(1, min(int(limit or 500), 500)),
            include_archived=include_archived,
        )
        skipped_statuses = set(OPEN_TASK_STATUSES)
        skipped = [task for task in candidates if task.status in skipped_statuses]
        deleted = task_store.delete_tasks(
            [task.task_id for task in candidates if task.status not in skipped_statuses]
        )
        return {
            "deleted": deleted,
            "skipped": len(skipped),
            "status": normalized_status,
        }

    @app.post("/api/agent/tasks")
    def create_agent_task(payload: AgentTaskCreatePayload) -> dict[str, Any]:
        """Create one durable task, optionally starting it immediately."""

        raw_context = dict(payload.context or {})
        request_auto_approve = raw_context.get("auto_approve_commands") is True
        request_auto_approve_scope = str(raw_context.get("auto_approve_scope") or "request").strip() or "request"
        _drop_removed_operator_context_keys(raw_context)
        source = "chat" if str(raw_context.pop("durable_task_create_source", "") or "").strip() == "chat" else "task_sheet"
        gateway_id = str(payload.gateway_id or raw_context.get("gateway_id") or "").strip()
        if gateway_id:
            raw_context["gateway_id"] = gateway_id
        llm_model = str(payload.llm_model or raw_context.get("llm_model") or "").strip()
        if llm_model:
            raw_context["llm_model"] = llm_model
        if request_auto_approve:
            raw_context["auto_approve_commands"] = True
            raw_context["auto_approve_scope"] = request_auto_approve_scope
        task = task_store.create_task(
            AgentTaskCreate(
                prompt=payload.prompt,
                title=payload.title or "",
                source=source,
                status="queued",
                agent_mode=payload.agent_mode,
                conversation_id=str(payload.conversation_id or "").strip(),
                gateway_id=gateway_id,
                context=raw_context,
            )
        )
        if payload.start_now:
            result = _queue_or_start_task(task.task_id, trigger="start")
            result["created"] = True
            return result
        return {"status": "queued", "created": True, "task": _task_record_payload(task)}

    @app.get("/api/agent/tasks/{task_id}")
    def get_agent_task(task_id: str) -> dict[str, Any]:
        """Return one durable task."""

        _reconcile_open_tasks()
        task = task_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return {"task": _task_record_payload(task)}

    @app.get("/api/agent/tasks/{task_id}/attempts")
    def list_agent_task_attempts(task_id: str, limit: int = 100) -> dict[str, Any]:
        """Return request attempts for one durable task."""

        if task_store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return {
            "attempts": [
                _task_attempt_payload(attempt)
                for attempt in task_store.list_attempts(task_id, limit=max(1, min(int(limit or 100), 500)))
            ]
        }

    @app.get("/api/agent/tasks/{task_id}/checkpoints")
    def list_agent_task_checkpoints(task_id: str, limit: int = 200) -> dict[str, Any]:
        """Return persisted progress checkpoints for one durable task."""

        if task_store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return {
            "checkpoints": [
                _task_checkpoint_payload(checkpoint)
                for checkpoint in task_store.list_checkpoints(task_id, limit=max(1, min(int(limit or 200), 1000)))
            ]
        }

    @app.post("/api/agent/tasks/{task_id}/start")
    def start_agent_task(task_id: str) -> dict[str, Any]:
        """Start or queue an interrupted/queued durable task."""

        task = task_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        if task.status not in {"queued", "interrupted", "failed", "cancelled"}:
            return {"status": task.status, "task": _task_record_payload(task)}
        task_store.update_task(
            task_id,
            AgentTaskUpdate(
                status="queued",
                current_request_id="",
                current_attempt_id="",
                blocker_reason="",
            ),
        )
        return _queue_or_start_task(task_id, trigger="resume" if task.status == "interrupted" else "start")

    @app.post("/api/agent/tasks/{task_id}/retry")
    def retry_agent_task(task_id: str) -> dict[str, Any]:
        """Retry a finished or interrupted durable task as a new attempt."""

        task = task_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        if task.status in OPEN_TASK_STATUSES:
            raise HTTPException(status_code=409, detail="Task is already active.")
        task_store.update_task(
            task_id,
            AgentTaskUpdate(
                status="queued",
                current_request_id="",
                current_attempt_id="",
                final_response_preview="",
                error_preview="",
                blocker_reason="",
            ),
        )
        return _queue_or_start_task(task_id, trigger="retry")

    @app.post("/api/agent/tasks/{task_id}/cancel")
    def cancel_agent_task(task_id: str) -> dict[str, Any]:
        """Cancel one durable task and its active request if present."""

        task = task_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        request_id = task.current_request_id or task.latest_request_id
        if task.status in OPEN_TASK_STATUSES and request_id:
            _cancel_agent_request(
                request_id,
                scheduled_event_id=task.event_id,
                scheduled_event_run_id=task.event_run_id,
            )
        if task.current_attempt_id:
            task_store.update_attempt(
                task.current_attempt_id,
                status="cancelled",
                error_preview="Task cancelled by the user.",
            )
        updated = task_store.update_task(
            task_id,
            AgentTaskUpdate(
                status="cancelled",
                current_request_id="",
                current_attempt_id="",
                blocker_reason="Task cancelled by the user.",
            ),
        )
        if task.event_run_id:
            event_store.update_run(
                task.event_run_id,
                status="cancelled",
                final_response_preview="Task cancelled by the user.",
                completed_at=utc_now_iso(),
            )
        _maybe_create_task_notification(
            task_id=task_id,
            status="cancelled",
            request_id=request_id,
            attempt_id=task.current_attempt_id,
            message="Task cancelled by the user.",
        )
        _start_next_queued_task()
        return {"status": "cancelled", "task": _task_record_payload(updated)}

    @app.post("/api/agent/tasks/{task_id}/archive")
    def archive_agent_task(task_id: str) -> dict[str, Any]:
        """Archive one durable task."""

        task = task_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        if task.status in OPEN_TASK_STATUSES:
            cancel_agent_task(task_id)
        archived = task_store.archive_task(task_id)
        return {"status": "archived", "task": _task_record_payload(archived)}

    @app.delete("/api/agent/tasks/{task_id}")
    def delete_agent_task(task_id: str) -> dict[str, Any]:
        """Hard-delete one durable task and its persisted run history."""

        task = task_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        if task.status in OPEN_TASK_STATUSES:
            cancel_agent_task(task_id)
        deleted = task_store.delete_task(task_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Task not found.")
        return {"deleted": True, "task_id": task_id}

    @app.get("/api/agent/cache/stats")
    def agent_plan_cache_stats() -> dict[str, Any]:
        """Return private operator and computation cache count metadata."""

        cache_store = _plan_cache_store(settings, agent_runtime)
        computation_store = _computation_cache_store(settings, agent_runtime)
        return {
            "enabled": bool(settings.agent_plan_cache_enabled),
            "stats": cache_store.stats().model_dump(mode="json"),
            "computation_enabled": bool(settings.agent_computation_cache_enabled),
            "computation_stats": computation_store.stats().model_dump(mode="json"),
        }

    @app.post("/api/agent/cache/clear")
    def clear_agent_plan_cache() -> dict[str, Any]:
        """Clear private operator caches without touching memory or run history."""

        cache_store = _plan_cache_store(settings, agent_runtime)
        computation_store = _computation_cache_store(settings, agent_runtime)
        return {
            "stats": cache_store.clear().model_dump(mode="json"),
            "computation_stats": computation_store.clear().model_dump(mode="json"),
        }

    @app.get("/api/agent/lrnt/stats")
    def agent_lrnt_stats() -> dict[str, Any]:
        """Return LRN-T total-task cache count metadata."""

        lrnt_store = _lrn_total_task_store(settings, agent_runtime)
        return {
            "enabled": bool(getattr(settings, "lrnt_enabled", True)),
            "stats": lrnt_store.stats().model_dump(mode="json"),
        }

    @app.post("/api/agent/lrnt/clear")
    def clear_agent_lrnt_cache() -> dict[str, Any]:
        """Clear LRN-T total-task structures without touching per-step LR caches."""

        lrnt_store = _lrn_total_task_store(settings, agent_runtime)
        return {
            "stats": lrnt_store.clear().model_dump(mode="json"),
            "cleared": True,
        }

    @app.post("/api/agent/lrdirect/clear")
    def clear_agent_lrdirect_cache() -> dict[str, Any]:
        """Clear exact-step LR Direct entries without clearing broader LR templates."""

        command_store = _command_template_cache_store(settings, agent_runtime)
        computation_store = _computation_cache_store(settings, agent_runtime)
        return {
            "command_template_stats": command_store.clear_lr_direct().model_dump(mode="json"),
            "computation_stats": computation_store.clear_lr_direct().model_dump(mode="json"),
            "cleared": True,
        }

    for _name in (
        'draft_agent_monitor',
        'list_agent_monitors',
        'create_agent_monitor',
        'get_agent_monitor',
        'list_agent_monitor_observations',
        'stream_agent_monitor',
        'start_agent_monitor',
        'pause_agent_monitor',
        'cancel_agent_monitor',
        'archive_agent_monitor',
        'list_agent_tasks',
        'clear_agent_tasks',
        'create_agent_task',
        'get_agent_task',
        'list_agent_task_attempts',
        'list_agent_task_checkpoints',
        'start_agent_task',
        'retry_agent_task',
        'cancel_agent_task',
        'archive_agent_task',
        'delete_agent_task',
        'agent_plan_cache_stats',
        'clear_agent_plan_cache',
        'clear_agent_lrdirect_cache',
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
