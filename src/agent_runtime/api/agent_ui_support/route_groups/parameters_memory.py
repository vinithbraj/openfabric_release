"""Parameters Memory Agent UI routes."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared import *


def register_parameters_memory_routes(ctx: AgentUiRouteContext) -> None:
    """Register parameters memory routes."""

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

    @app.post("/api/agent/name/suggest")
    def suggest_agent_name(payload: AgentNameSuggestionPayload) -> dict[str, str]:
        """Return one LLM-authored local agent display name."""

        complete_json = getattr(getattr(agent_runtime, "llm_client", None), "complete_json", None)
        avoid_keys = {_agent_name_key(name) for name in _agent_name_avoid_names(payload)}
        if callable(complete_json):
            for _attempt in range(2):
                try:
                    raw = complete_json(
                        _agent_name_suggestion_prompt(
                            payload,
                            style_lane=random.choice(_AGENT_NAME_STYLE_LANES),
                            inspiration_words=random.sample(_AGENT_NAME_INSPIRATION_WORDS, k=3),
                            creative_nonce=str(random.randrange(1_000_000, 999_999_999)),
                        ),
                        AgentNameSuggestionResponse.model_json_schema(),
                    )
                    suggestion = AgentNameSuggestionResponse.model_validate(raw)
                    name = _sanitize_agent_display_name(suggestion.name)
                    if _agent_name_allowed(name, avoid_keys):
                        return {"name": name}
                    avoid_keys.add(_agent_name_key(name))
                except Exception:
                    pass
        return {"name": _fallback_agent_name(avoid_keys)}

    @app.post("/api/agent/parameters/draft")
    def draft_agent_parameter(payload: AgentParameterDraftRequest) -> dict[str, Any]:
        """Draft a parameter-store entry from a natural-language storage request."""

        _parameter_store_or_error(settings, agent_runtime)
        response = draft_parameter_from_prompt(
            payload,
            llm_client=getattr(agent_runtime, "llm_client", None),
            planner_enabled=True,
        )
        return response.model_dump(mode="json")

    @app.get("/api/agent/parameters")
    def list_agent_parameters(q: str = "", tag: str = "", limit: int = 200) -> dict[str, Any]:
        """List local parameter-store entries with masked values."""

        parameter_store = _parameter_store_or_error(settings, agent_runtime)
        safe_limit = max(1, min(1000, int(limit)))
        records = parameter_store.list(query=q, tag=tag, limit=safe_limit)
        all_records = parameter_store.list(limit=1000)
        return {
            "entries": [
                masked_parameter_summary(record).model_dump(mode="json")
                for record in records
            ],
            "db_path": str(settings.agent_parameters_db_path),
            "stats": {
                "total": len(all_records),
                "filtered": len(records),
                "sensitive": sum(1 for record in all_records if record.sensitive),
                "total_uses": sum(int(record.use_count or 0) for record in all_records),
            },
        }

    @app.post("/api/agent/parameters")
    def create_agent_parameter(payload: AgentParameterCreate) -> dict[str, Any]:
        """Create one confirmed parameter-store entry."""

        parameter_store = _parameter_store_or_error(settings, agent_runtime)
        try:
            record = parameter_store.create(payload, actor="user")
        except Exception as exc:
            message = str(exc)
            if "UNIQUE" in message.upper() or "constraint" in message.lower():
                raise HTTPException(status_code=409, detail="Parameter key already exists.") from exc
            raise HTTPException(status_code=400, detail=message) from exc
        return {"entry": masked_parameter_summary(record).model_dump(mode="json")}

    @app.get("/api/agent/parameters/{key}")
    def get_agent_parameter(key: str, include_audit: bool = False) -> dict[str, Any]:
        """Get one parameter-store entry with masked value metadata."""

        parameter_store = _parameter_store_or_error(settings, agent_runtime)
        record = parameter_store.get(key)
        if record is None:
            raise HTTPException(status_code=404, detail="Parameter not found.")
        payload: dict[str, Any] = {
            "entry": masked_parameter_summary(record).model_dump(mode="json"),
            "context_json": record.context_json,
        }
        if include_audit:
            payload["audit_events"] = [
                event.model_dump(mode="json")
                for event in parameter_store.audit_events(record.normalized_key)
            ]
        return payload

    @app.patch("/api/agent/parameters/{key}")
    def update_agent_parameter(key: str, payload: AgentParameterUpdate) -> dict[str, Any]:
        """Update one confirmed parameter-store entry."""

        parameter_store = _parameter_store_or_error(settings, agent_runtime)
        try:
            record = parameter_store.update(key, payload, actor="user")
        except Exception as exc:
            message = str(exc)
            if "UNIQUE" in message.upper() or "constraint" in message.lower():
                raise HTTPException(status_code=409, detail="Parameter key already exists.") from exc
            raise HTTPException(status_code=400, detail=message) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="Parameter not found.")
        return {"entry": masked_parameter_summary(record).model_dump(mode="json")}

    @app.delete("/api/agent/parameters/{key}")
    def delete_agent_parameter(key: str) -> dict[str, Any]:
        """Delete one parameter-store entry."""

        parameter_store = _parameter_store_or_error(settings, agent_runtime)
        deleted = parameter_store.delete(key, actor="user")
        if not deleted:
            raise HTTPException(status_code=404, detail="Parameter not found.")
        return {"deleted": True, "key": key}

    @app.post("/api/agent/parameters/{key}/reveal")
    def reveal_agent_parameter(key: str) -> dict[str, Any]:
        """Reveal one parameter value after an explicit user action."""

        parameter_store = _parameter_store_or_error(settings, agent_runtime)
        record = parameter_store.reveal(key, actor="user")
        if record is None:
            raise HTTPException(status_code=404, detail="Parameter not found.")
        return {
            "entry": masked_parameter_summary(record).model_dump(mode="json"),
            "value_json": record.value_json,
            "context_json": record.context_json,
        }

    @app.get("/api/agent/memory")
    def list_agent_memory(
        status: str | None = None,
        q: str = "",
        memory_kind: str = "",
        scope: str = "",
        model_name: str = "",
        model_family: str = "",
        task_type: str = "",
        tool_type: str = "",
        intent_type: str = "",
        validator_error_type: str = "",
        tag: str = "",
    ) -> dict[str, Any]:
        """List persistent agent memory entries and pending proposals."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        entries = memory_store.list_entries(
            status=status or None,
            query=q,
            memory_kind=memory_kind,
            scope=scope,
            model_name=model_name,
            model_family=model_family,
            task_type=task_type,
            tool_type=tool_type,
            intent_type=intent_type,
            validator_error_type=validator_error_type,
            tag=tag,
        )
        picker_entries = memory_store.list_entries(status=status or None)
        all_entries = memory_store.list_entries()
        proposals = memory_store.list_proposals(status="proposed")
        status_counts = {"active": 0, "proposed": 0, "retired": 0}
        total_uses = 0
        for entry in all_entries:
            if entry.status in status_counts:
                status_counts[entry.status] += 1
            total_uses += int(entry.use_count or 0)
        return {
            "entries": [entry.model_dump(mode="json") for entry in entries],
            "picker_entries": [entry.model_dump(mode="json") for entry in picker_entries],
            "proposals": [proposal.model_dump(mode="json") for proposal in proposals],
            "stats": {
                "total": len(all_entries),
                "active": status_counts["active"],
                "proposed": status_counts["proposed"],
                "retired": status_counts["retired"],
                "pending_proposals": len(proposals),
                "total_uses": total_uses,
                "filtered": len(entries),
                "picker": len(picker_entries),
            },
            "active_model": _active_agent_model_for_memory(settings),
        }

    @app.post("/api/agent/memory")
    def create_agent_memory(payload: MemoryEntryCreate) -> dict[str, Any]:
        """Create one user-authored active memory entry."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        entry = memory_store.create_entry(payload, actor="user")
        return {"entry": entry.model_dump(mode="json")}

    @app.post("/api/agent/memory/context")
    def draft_agent_memory_context(payload: MemoryContextDraftRequest) -> dict[str, Any]:
        """Return LLM-authored memory metadata for a request/response pair."""

        _memory_store_or_error(settings, agent_runtime)
        trace = trace_store.get_trace(payload.request_id) if payload.request_id else None
        complete_json = getattr(getattr(agent_runtime, "llm_client", None), "complete_json", None)
        if not callable(complete_json):
            raise HTTPException(status_code=503, detail="LLM client is not available for memory context drafting.")
        raw = complete_json(
            _memory_context_draft_prompt(payload, settings, trace),
            MemoryContextDraftResponse.model_json_schema(),
        )
        draft = MemoryContextDraftResponse.model_validate(raw)
        if not draft.model_name:
            draft.model_name = str(payload.model_name or _active_agent_model_for_memory(settings)).strip()
        if not draft.model_family:
            draft.model_family = normalize_model_family(draft.model_name)
        return {"draft": draft.model_dump(mode="json")}

    @app.patch("/api/agent/memory/{memory_id}")
    def update_agent_memory(memory_id: str, payload: MemoryEntryUpdate) -> dict[str, Any]:
        """Update one persistent memory entry."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        entry = memory_store.update_entry(memory_id, payload, actor="user")
        if entry is None:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        return {"entry": entry.model_dump(mode="json")}

    @app.post("/api/agent/memory/{memory_id}/retire")
    def retire_agent_memory(memory_id: str) -> dict[str, Any]:
        """Retire one memory entry without deleting audit history."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        entry = memory_store.set_status(memory_id, "retired", actor="user")
        if entry is None:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        return {"entry": entry.model_dump(mode="json")}

    @app.post("/api/agent/memory/{memory_id}/restore")
    def restore_agent_memory(memory_id: str) -> dict[str, Any]:
        """Restore one retired or proposed memory entry as active."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        entry = memory_store.set_status(memory_id, "active", actor="user")
        if entry is None:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        return {"entry": entry.model_dump(mode="json")}

    @app.get("/api/agent/memory/{memory_id}/audit")
    def audit_agent_memory(memory_id: str) -> dict[str, Any]:
        """Return audit events for one memory entry."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        if memory_store.get_entry(memory_id) is None:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        return {
            "events": [
                event.model_dump(mode="json")
                for event in memory_store.audit_events(memory_id)
            ]
        }

    @app.post("/api/agent/memory/feedback")
    def propose_agent_memory_from_feedback(payload: MemoryFeedbackRequest) -> dict[str, Any]:
        """Create proposed memory drafts from user feedback."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        trace = trace_store.get_trace(payload.request_id) if payload.request_id else None
        complete_json = _memory_feedback_complete_json(settings, agent_runtime, payload)
        response = MemoryDraftResponse()
        if callable(complete_json):
            try:
                raw = _call_memory_feedback_llm(
                    complete_json,
                    _memory_feedback_prompt(payload, settings, trace),
                    MemoryDraftResponse.model_json_schema(),
                    settings,
                )
                response = MemoryDraftResponse.model_validate(raw)
            except Exception:
                response = MemoryDraftResponse(
                    rationale="LLM memory feedback conversion failed; using direct user feedback fallback."
                )
        proposals = []
        for draft in response.drafts:
            draft_payload = _normalized_memory_draft_payload(draft, payload, settings)
            if draft.proposal_type in {"create", "update"} and not draft_payload["instruction"]:
                continue
            proposals.append(
                memory_store.create_proposal(
                    proposal_type=draft.proposal_type,
                    memory_id=draft.memory_id,
                    draft=draft_payload,
                    rationale=draft.rationale or response.rationale,
                    provenance="feedback",
                    actor="llm",
                )
            )
        if not proposals:
            draft_payload = _fallback_memory_feedback_draft_payload(payload, settings)
            if draft_payload["instruction"]:
                proposals.append(
                    memory_store.create_proposal(
                        proposal_type="create",
                        draft=draft_payload,
                        rationale=draft_payload["rationale"] or response.rationale,
                        provenance="feedback",
                        actor="user",
                    )
                )
        learning_lesson = None
        if bool(getattr(settings, "agent_learning_ledger_enabled", True)):
            try:
                ledger = _learning_ledger_store_or_error(settings, agent_runtime)
                run_feedback = payload.run_feedback
                lesson_write = feedback_lesson_write(
                    request_id=str(payload.request_id or ""),
                    feedback=payload.feedback,
                    prompt=str(
                        payload.prompt
                        or (run_feedback.prompt if run_feedback is not None else "")
                        or getattr(trace, "prompt", "")
                        or ""
                    ),
                    outcome=str(run_feedback.outcome if run_feedback is not None else ""),
                    model_name=str(payload.model_name or payload.llm_model or _active_agent_model_for_memory(settings)),
                    feedback_target=str(payload.feedback_target or ""),
                )
                if lesson_write is not None:
                    learning_lesson = ledger.create_lesson(lesson_write)
                    if learning_lesson is not None and trace is not None:
                        auto_learned_feedback = False
                        if (
                            _runtime_controls_snapshot()["agent_learning_ledger_auto_learn_enabled"]
                            and _auto_learning_lesson_is_safe(learning_lesson)
                        ):
                            approved = ledger.approve_lesson(
                                learning_lesson.lesson_id,
                                memory_store=memory_store,
                                actor="auto-learning",
                                auto_approved=True,
                            )
                            if approved is not None:
                                learning_lesson = approved
                                auto_learned_feedback = True
                        trace_store.append_event(
                            AgentTraceEvent(
                                request_id=trace.request_id,
                                stage="learning",
                                level="info",
                                event_type=(
                                    "learning.lesson.learned"
                                    if auto_learned_feedback
                                    else "learning.lesson.proposed"
                                ),
                                title=(
                                    "Learning saved"
                                    if auto_learned_feedback
                                    else "Learning draft proposed"
                                ),
                                summary=(
                                    "User feedback was safely remembered automatically."
                                    if auto_learned_feedback
                                    else "User feedback was captured as a draft lesson."
                                ),
                                detail=_learning_lesson_trace_detail(learning_lesson),
                            )
                        )
            except Exception:
                learning_lesson = None
        response_payload = {"proposals": [proposal.model_dump(mode="json") for proposal in proposals]}
        if learning_lesson is not None:
            response_payload["learning_lesson"] = _learning_lesson_payload(learning_lesson)
        return response_payload

    @app.post("/api/agent/memory/feedback/draft")
    def draft_agent_memory_feedback(payload: MemoryFeedbackDraftRequest) -> dict[str, Any]:
        """Draft editable post-run feedback text from a run outcome."""

        _memory_store_or_error(settings, agent_runtime)
        trace = trace_store.get_trace(payload.request_id) if payload.request_id else None
        complete_json = _memory_feedback_complete_json(settings, agent_runtime, payload)
        response = MemoryFeedbackDraftResponse(
            feedback=_fallback_memory_feedback_text(payload, trace),
            rationale="Fallback draft created from run outcome.",
        )
        if callable(complete_json):
            try:
                raw = _call_memory_feedback_llm(
                    complete_json,
                    _memory_feedback_draft_prompt(payload, settings, trace),
                    MemoryFeedbackDraftResponse.model_json_schema(),
                    settings,
                )
                llm_response = MemoryFeedbackDraftResponse.model_validate(raw)
                if str(llm_response.feedback or "").strip():
                    response = llm_response
            except Exception:
                pass
        return response.model_dump(mode="json")

    @app.post("/api/agent/memory/optimize")
    def optimize_agent_memory(payload: MemoryOptimizationRequest) -> dict[str, Any]:
        """Create proposed memory cleanup changes without applying them."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        entries = memory_store.list_entries(status="active")
        if payload.memory_ids:
            wanted = set(payload.memory_ids)
            entries = [entry for entry in entries if entry.memory_id in wanted]
        complete_json = getattr(getattr(agent_runtime, "llm_client", None), "complete_json", None)
        if not callable(complete_json):
            raise HTTPException(status_code=503, detail="LLM client is not available for memory optimization.")
        raw = complete_json(
            _memory_optimizer_prompt([entry.model_dump(mode="json") for entry in entries], payload.query),
            MemoryDraftResponse.model_json_schema(),
        )
        response = MemoryDraftResponse.model_validate(raw)
        proposals = [
            memory_store.create_proposal(
                proposal_type=draft.proposal_type,
                memory_id=draft.memory_id,
                draft=draft.model_dump(mode="json", exclude={"proposal_type", "memory_id", "confidence"}),
                rationale=draft.rationale or response.rationale,
                provenance="optimizer",
                actor="llm",
            )
            for draft in response.drafts
        ]
        return {"proposals": [proposal.model_dump(mode="json") for proposal in proposals]}

    @app.post("/api/agent/memory/proposals/{proposal_id}/apply")
    def apply_agent_memory_proposal(proposal_id: str) -> dict[str, Any]:
        """Apply one proposed memory change after user approval."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        entry = memory_store.apply_proposal(proposal_id, actor="user")
        if entry is None:
            raise HTTPException(status_code=404, detail="Memory proposal not found or already handled")
        return {"entry": entry.model_dump(mode="json")}

    @app.post("/api/agent/conversation/summary")
    def summarize_agent_conversation(payload: AgentConversationSummaryPayload) -> dict[str, str]:
        """Return one compact LLM-authored summary for the collapsed conversation header."""

        summary = _conversation_header_summary(
            payload.text,
            getattr(agent_runtime, "llm_client", None),
        )
        return {"summary": summary}

    for _name in (
        'suggest_agent_name',
        'draft_agent_parameter',
        'list_agent_parameters',
        'create_agent_parameter',
        'get_agent_parameter',
        'update_agent_parameter',
        'delete_agent_parameter',
        'reveal_agent_parameter',
        'list_agent_memory',
        'create_agent_memory',
        'draft_agent_memory_context',
        'update_agent_memory',
        'retire_agent_memory',
        'restore_agent_memory',
        'audit_agent_memory',
        'propose_agent_memory_from_feedback',
        'draft_agent_memory_feedback',
        'optimize_agent_memory',
        'apply_agent_memory_proposal',
        'summarize_agent_conversation',
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
