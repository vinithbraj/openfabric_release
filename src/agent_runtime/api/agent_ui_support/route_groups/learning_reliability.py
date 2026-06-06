"""Learning Reliability Agent UI routes."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared import *


def register_learning_reliability_routes(ctx: AgentUiRouteContext) -> None:
    """Register learning reliability routes."""

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

    def _learning_lesson_payload(lesson: Any) -> dict[str, Any]:
        payload = lesson.model_dump(mode="json") if hasattr(lesson, "model_dump") else dict(lesson)
        payload["is_active"] = str(payload.get("status") or "") == "approved"
        return payload

    def _capability_proposal_payload(proposal: Any) -> dict[str, Any]:
        payload = proposal.model_dump(mode="json") if hasattr(proposal, "model_dump") else dict(proposal)
        payload["is_active"] = str(payload.get("status") or "") in {
            "approved",
            "applied",
            "approved_pending_apply",
        }
        return payload

    @app.get("/api/agent/learning-ledger/summary")
    def get_learning_ledger_summary() -> dict[str, Any]:
        """Return a small learning ledger dashboard summary."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        return {
            "summary": ledger.summary().model_dump(mode="json"),
            "db_path": str(settings.agent_learning_ledger_db_path),
            "enabled": bool(settings.agent_learning_ledger_enabled),
            "auto_learn_enabled": bool(
                _runtime_controls_snapshot()["agent_learning_ledger_auto_learn_enabled"]
            ),
        }

    @app.post("/api/agent/learning-ledger/clear")
    def clear_learning_ledger() -> dict[str, Any]:
        """Clear auto-learning ledger history without touching memory."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        return {
            "summary": ledger.clear().model_dump(mode="json"),
            "db_path": str(settings.agent_learning_ledger_db_path),
            "enabled": bool(settings.agent_learning_ledger_enabled),
            "auto_learn_enabled": bool(
                _runtime_controls_snapshot()["agent_learning_ledger_auto_learn_enabled"]
            ),
            "cleared": True,
        }

    @app.get("/api/agent/reliability/profile")
    def get_reliability_profiles(limit: int = 100) -> dict[str, Any]:
        """Return Reliability Kernel model profiles and dashboard summary."""

        profiles = reliability_store.list_profiles(limit=max(1, min(int(limit or 100), 500)))
        controls = _runtime_controls_snapshot()
        return {
            "summary": reliability_store.summary(),
            "profiles": [profile.model_dump(mode="json") for profile in profiles],
            "count": len(profiles),
            "db_path": str(settings.agent_reliability_db_path),
            "controls": {
                key: controls[key]
                for key in (
                    "reliability_mode",
                    "reliability_verifier_enforced",
                    "reliability_max_recovery_probes",
                    "reliability_max_autonomous_repair_attempts",
                    "reliability_weak_model_plan_action_cap",
                    "reliability_approval_envelope_budget",
                )
            },
        }

    @app.get("/api/agent/reliability/profile/{model_id:path}")
    def get_reliability_profile(model_id: str) -> dict[str, Any]:
        """Return one Reliability Kernel model capability profile."""

        profile = reliability_store.get_profile(model_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Reliability profile not found")
        return {
            "profile": profile.model_dump(mode="json"),
            "events": [
                event.model_dump(mode="json")
                for event in reliability_store.list_events(model_id=model_id, limit=100)
            ],
        }

    @app.get("/api/agent/reliability/runs")
    def list_reliability_runs(limit: int = 100) -> dict[str, Any]:
        """Return recent Reliability Kernel request timelines."""

        runs = reliability_store.list_runs(limit=max(1, min(int(limit or 100), 500)))
        return {
            "runs": runs,
            "count": len(runs),
            "db_path": str(settings.agent_reliability_db_path),
        }

    @app.get("/api/agent/reliability/runs/{request_id}")
    def get_reliability_run(request_id: str) -> dict[str, Any]:
        """Return one normalized Reliability Kernel recovery timeline."""

        payload = _reliability_run_payload(request_id)
        if not payload["events"]:
            raise HTTPException(status_code=404, detail="Reliability run not found")
        return payload

    @app.get("/api/agent/reliability/runs/{request_id}/report.json")
    def get_reliability_run_report_json(request_id: str) -> dict[str, Any]:
        """Export one Reliability Kernel recovery report as JSON."""

        payload = _reliability_run_payload(request_id)
        if not payload["events"]:
            raise HTTPException(status_code=404, detail="Reliability run not found")
        return payload

    @app.get("/api/agent/reliability/runs/{request_id}/report.md")
    def get_reliability_run_report_markdown(request_id: str) -> StreamingResponse:
        """Export one Reliability Kernel recovery report as Markdown."""

        payload = _reliability_run_payload(request_id)
        if not payload["events"]:
            raise HTTPException(status_code=404, detail="Reliability run not found")
        markdown = _reliability_markdown_report(request_id)
        return StreamingResponse(iter([markdown]), media_type="text/markdown")

    @app.get("/api/agent/reliability/evals")
    def list_reliability_evals(limit: int = 50) -> dict[str, Any]:
        """Return recent deterministic Reliability Kernel eval summaries."""

        evals = reliability_store.list_evals(limit=max(1, min(int(limit or 50), 200)))
        return {
            "evals": [item.model_dump(mode="json") for item in evals],
            "count": len(evals),
            "case_count": len(DEFAULT_RELIABILITY_EVAL_CASES),
            "db_path": str(settings.agent_reliability_db_path),
        }

    @app.post("/api/agent/reliability/evals/run")
    def run_reliability_evals() -> dict[str, Any]:
        """Run deterministic weak-model Reliability Kernel eval cases."""

        result = run_reliability_eval(reliability_store)
        return {
            "eval": result.model_dump(mode="json"),
            "summary": reliability_store.summary(),
        }

    @app.get("/api/agent/learning-ledger/runs")
    def list_learning_ledger_runs(limit: int = 50, outcome: str = "") -> dict[str, Any]:
        """Return recent learning-ledger run records."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        runs = ledger.list_runs(limit=max(1, min(int(limit or 50), 200)), outcome=outcome)
        return {
            "runs": [run.model_dump(mode="json") for run in runs],
            "count": len(runs),
            "db_path": str(settings.agent_learning_ledger_db_path),
        }

    @app.get("/api/agent/learning-ledger/runs/{request_id}")
    def get_learning_ledger_run(request_id: str) -> dict[str, Any]:
        """Return one learning-ledger run with action, cache, and lesson evidence."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        run = ledger.get_run(request_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Learning run not found")
        lessons = [
            lesson
            for lesson in ledger.list_lessons(status=None, limit=500)
            if lesson.source_request_id == request_id
        ]
        proposals = ledger.list_proposals(source_request_id=request_id, limit=500)
        return {
            "run": run.model_dump(mode="json"),
            "actions": [item.model_dump(mode="json") for item in ledger.run_actions(request_id)],
            "cache_events": [item.model_dump(mode="json") for item in ledger.run_cache_events(request_id)],
            "lessons": [_learning_lesson_payload(lesson) for lesson in lessons],
            "proposals": [_capability_proposal_payload(proposal) for proposal in proposals],
        }

    @app.get("/api/agent/learning-ledger/proposals")
    def list_capability_proposals(
        status: str | None = None,
        target_kind: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return capability evolution proposals by lifecycle status and target."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        proposals = ledger.list_proposals(
            status=status or None,
            target_kind=target_kind or None,
            limit=max(1, min(int(limit or 100), 500)),
        )
        return {
            "proposals": [_capability_proposal_payload(proposal) for proposal in proposals],
            "count": len(proposals),
            "db_path": str(settings.agent_learning_ledger_db_path),
        }

    @app.get("/api/agent/learning-ledger/proposals/{proposal_id}")
    def get_capability_proposal(proposal_id: str) -> dict[str, Any]:
        """Return one capability evolution proposal."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        proposal = ledger.get_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="Capability proposal not found")
        return {"proposal": _capability_proposal_payload(proposal)}

    @app.patch("/api/agent/learning-ledger/proposals/{proposal_id}")
    def update_capability_proposal(
        proposal_id: str,
        payload: CapabilityProposalUpdatePayload,
    ) -> dict[str, Any]:
        """Edit one capability evolution proposal."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        try:
            proposal = ledger.update_proposal(
                proposal_id,
                title=payload.title,
                summary=payload.summary,
                rationale=payload.rationale,
                confidence=payload.confidence,
                draft=payload.draft,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if proposal is None:
            raise HTTPException(status_code=404, detail="Capability proposal not found")
        return {"proposal": _capability_proposal_payload(proposal)}

    @app.post("/api/agent/learning-ledger/proposals/{proposal_id}/approve")
    def approve_capability_proposal(proposal_id: str) -> dict[str, Any]:
        """Approve one capability evolution proposal and apply safe targets."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        memory_store = _memory_store_for_learning(settings, agent_runtime)
        try:
            proposal = ledger.approve_proposal(
                proposal_id,
                memory_store=memory_store,
                prompt_template_store=prompt_template_store,
                actor="user",
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if proposal is None:
            raise HTTPException(status_code=404, detail="Capability proposal not found")
        configure_prompt_fetcher(settings.agent_prompts_db_path)
        if str(proposal.status or "") == "applied":
            _append_capability_proposal_trace_event(
                trace_store=trace_store,
                proposal=proposal,
                event_type="learning.proposal.applied",
                title="Capability proposal applied",
                summary="A reviewed capability evolution proposal was applied.",
            )
        elif str(proposal.status or "") == "approved_pending_apply":
            _append_capability_proposal_trace_event(
                trace_store=trace_store,
                proposal=proposal,
                event_type="learning.proposal.approved_pending_apply",
                title="Capability proposal awaiting apply",
                summary="An executable capability patch was approved and is waiting for explicit code application.",
            )
        return {"proposal": _capability_proposal_payload(proposal)}

    @app.post("/api/agent/learning-ledger/proposals/{proposal_id}/apply")
    def apply_capability_proposal(proposal_id: str) -> dict[str, Any]:
        """Apply one approved capability evolution proposal."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        memory_store = _memory_store_for_learning(settings, agent_runtime)
        try:
            proposal = ledger.apply_proposal(
                proposal_id,
                memory_store=memory_store,
                prompt_template_store=prompt_template_store,
                actor="user",
            )
        except (KeyError, ValueError) as exc:
            failed = ledger.get_proposal(proposal_id)
            if failed is not None:
                _append_capability_proposal_trace_event(
                    trace_store=trace_store,
                    proposal=failed,
                    event_type="learning.proposal.apply_failed",
                    title="Capability proposal apply failed",
                    summary=str(exc),
                    level="warning",
                )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if proposal is None:
            raise HTTPException(status_code=404, detail="Capability proposal not found")
        configure_prompt_fetcher(settings.agent_prompts_db_path)
        _append_capability_proposal_trace_event(
            trace_store=trace_store,
            proposal=proposal,
            event_type="learning.proposal.applied",
            title="Capability proposal applied",
            summary="A reviewed capability evolution proposal was applied.",
        )
        return {"proposal": _capability_proposal_payload(proposal)}

    @app.post("/api/agent/learning-ledger/proposals/{proposal_id}/reject")
    def reject_capability_proposal(
        proposal_id: str,
        payload: CapabilityProposalRejectPayload | None = None,
    ) -> dict[str, Any]:
        """Reject one capability evolution proposal."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        proposal = ledger.reject_proposal(proposal_id, reason=(payload.reason if payload else "") or "")
        if proposal is None:
            raise HTTPException(status_code=404, detail="Capability proposal not found")
        _append_capability_proposal_trace_event(
            trace_store=trace_store,
            proposal=proposal,
            event_type="learning.proposal.rejected",
            title="Capability proposal rejected",
            summary="A capability evolution proposal was rejected.",
        )
        return {"proposal": _capability_proposal_payload(proposal)}

    @app.post("/api/agent/learning-ledger/proposals/{proposal_id}/retire")
    def retire_capability_proposal(proposal_id: str) -> dict[str, Any]:
        """Retire one capability evolution proposal."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        proposal = ledger.retire_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="Capability proposal not found")
        _append_capability_proposal_trace_event(
            trace_store=trace_store,
            proposal=proposal,
            event_type="learning.proposal.retired",
            title="Capability proposal retired",
            summary="A capability evolution proposal was retired.",
        )
        return {"proposal": _capability_proposal_payload(proposal)}

    @app.get("/api/agent/learning-ledger/lessons")
    def list_learning_ledger_lessons(status: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Return learning lessons by lifecycle status."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        lessons = ledger.list_lessons(status=status or None, limit=max(1, min(int(limit or 100), 500)))
        return {
            "lessons": [_learning_lesson_payload(lesson) for lesson in lessons],
            "count": len(lessons),
            "db_path": str(settings.agent_learning_ledger_db_path),
        }

    @app.patch("/api/agent/learning-ledger/lessons/{lesson_id}")
    def update_learning_ledger_lesson(
        lesson_id: str,
        payload: LearningLessonUpdatePayload,
    ) -> dict[str, Any]:
        """Edit one learning lesson draft."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        try:
            lesson = ledger.update_lesson(
                lesson_id,
                title=payload.title,
                instruction=payload.instruction,
                summary=payload.summary,
                scope=payload.scope,
                tags=payload.tags,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if lesson is None:
            raise HTTPException(status_code=404, detail="Learning lesson not found")
        if lesson.status == "approved" and lesson.mirrored_memory_id:
            memory_store = _memory_store_or_error(settings, agent_runtime)
            refreshed = ledger.approve_lesson(
                lesson_id,
                memory_store=memory_store,
                actor="user",
                auto_approved=lesson.auto_approved,
            )
            if refreshed is not None:
                lesson = refreshed
        return {"lesson": _learning_lesson_payload(lesson)}

    @app.post("/api/agent/learning-ledger/lessons/{lesson_id}/approve")
    def approve_learning_ledger_lesson(lesson_id: str) -> dict[str, Any]:
        """Approve one learning lesson and mirror it into active memory."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        memory_store = _memory_store_or_error(settings, agent_runtime)
        try:
            lesson = ledger.approve_lesson(lesson_id, memory_store=memory_store, actor="user")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if lesson is None:
            raise HTTPException(status_code=404, detail="Learning lesson not found")
        return {"lesson": _learning_lesson_payload(lesson)}

    @app.post("/api/agent/learning-ledger/lessons/{lesson_id}/reject")
    def reject_learning_ledger_lesson(
        lesson_id: str,
        payload: LearningLessonRejectPayload | None = None,
    ) -> dict[str, Any]:
        """Reject one learning lesson so it will not affect future behavior."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        lesson = ledger.reject_lesson(lesson_id, reason=(payload.reason if payload else "") or "")
        if lesson is None:
            raise HTTPException(status_code=404, detail="Learning lesson not found")
        return {"lesson": _learning_lesson_payload(lesson)}

    @app.post("/api/agent/learning-ledger/lessons/{lesson_id}/retire")
    def retire_learning_ledger_lesson(lesson_id: str) -> dict[str, Any]:
        """Retire one approved learning lesson and its mirrored memory."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        memory_store = _memory_store_or_error(settings, agent_runtime)
        lesson = ledger.retire_lesson(lesson_id, memory_store=memory_store)
        if lesson is None:
            raise HTTPException(status_code=404, detail="Learning lesson not found")
        return {"lesson": _learning_lesson_payload(lesson)}

    @app.post("/api/agent/learning-ledger/lessons/{lesson_id}/restore")
    def restore_learning_ledger_lesson(lesson_id: str) -> dict[str, Any]:
        """Restore one retired lesson to active memory."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        memory_store = _memory_store_or_error(settings, agent_runtime)
        lesson = ledger.restore_lesson(lesson_id, memory_store=memory_store)
        if lesson is None:
            raise HTTPException(status_code=404, detail="Learning lesson not found")
        return {"lesson": _learning_lesson_payload(lesson)}

    @app.post("/api/agent/learning-ledger/lessons/{lesson_id}/digest-note")
    def digest_learning_ledger_note(
        lesson_id: str,
        payload: LearningLessonDigestNotePayload,
    ) -> dict[str, Any]:
        """Merge user-supplied guidance into one learning draft."""

        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
        try:
            lesson = ledger.digest_note(lesson_id, payload.note)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if lesson is None:
            raise HTTPException(status_code=404, detail="Learning lesson not found")
        if lesson.status == "approved" and lesson.mirrored_memory_id:
            memory_store = _memory_store_or_error(settings, agent_runtime)
            refreshed = ledger.approve_lesson(
                lesson_id,
                memory_store=memory_store,
                actor="user",
                auto_approved=lesson.auto_approved,
            )
            if refreshed is not None:
                lesson = refreshed
        return {"lesson": _learning_lesson_payload(lesson)}

    @app.get("/api/agent/chats")
    def list_agent_chats(limit: int = 50) -> dict[str, Any]:
        """Return recent durable Agent UI chat summaries."""

        return {
            "chats": [
                _chat_summary_payload(chat)
                for chat in conversation_store.list(limit=max(1, min(int(limit or 50), 200)))
            ]
        }

    @app.get("/api/agent/chats/{conversation_id}")
    def get_agent_chat(conversation_id: str) -> dict[str, Any]:
        """Return one durable Agent UI chat for pane restoration."""

        chat = conversation_store.get(conversation_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"chat": _chat_detail_payload(chat)}

    @app.delete("/api/agent/chats/{conversation_id}")
    def delete_agent_chat(conversation_id: str) -> dict[str, Any]:
        """Delete one durable Agent UI chat."""

        deleted = conversation_store.delete(conversation_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"deleted": True, "conversation_id": conversation_id}

    @app.get("/api/agent/runtime-controls")
    def agent_runtime_controls() -> dict[str, Any]:
        """Return server-global Agent UI runtime controls."""

        return _public_runtime_controls_snapshot()

    @app.post("/api/agent/runtime-controls")
    def update_agent_runtime_controls(payload: AgentRuntimeControlsPayload) -> dict[str, Any]:
        """Update server-global Agent UI runtime controls."""

        _apply_runtime_control_values(payload.model_dump(exclude_none=True))
        return _public_runtime_controls_snapshot()

    @app.get("/api/agent/command-allowlist")
    def list_agent_command_allowlist() -> dict[str, Any]:
        """List persisted exact command exceptions."""

        return {
            "entries": [
                entry.model_dump(mode="json")
                for entry in command_allowlist_store.list(include_disabled=False)
            ],
            **_command_allowlist_context(),
        }

    @app.post("/api/agent/command-allowlist")
    def create_agent_command_allowlist(payload: AgentCommandAllowlistPayload) -> dict[str, Any]:
        """Persist one exact command exception for overrideable hard blocks."""

        command = " ".join(str(payload.command or "").split()).strip()
        if not command:
            raise HTTPException(status_code=400, detail="Command is required")
        entry = command_allowlist_store.upsert(
            command,
            block_reason=str(payload.reason or "").strip(),
        )
        source_request_id = str(payload.source_request_id or "").strip()
        if source_request_id and trace_store.get_trace(source_request_id) is not None:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=source_request_id,
                    stage="validation",
                    level="warning",
                    event_type="operator.command_allowlist.added",
                    title="Command exception added",
                    summary="The user allowed an exact overrideable blocked command for future requests.",
                    detail={
                        "entry_id": entry.entry_id,
                        "command_hash": entry.command_hash,
                        "block_reason": entry.block_reason,
                    },
                )
            )
        return {
            "entry": entry.model_dump(mode="json"),
            **_command_allowlist_context(),
        }

    @app.delete("/api/agent/command-allowlist/{entry_id}")
    def delete_agent_command_allowlist(entry_id: str) -> dict[str, Any]:
        """Disable one exact command exception."""

        removed = command_allowlist_store.delete(entry_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Command exception not found")
        return {"deleted": True, "entry_id": entry_id, **_command_allowlist_context()}

    for _name in (
        '_learning_lesson_payload',
        '_capability_proposal_payload',
        'get_learning_ledger_summary',
        'clear_learning_ledger',
        'get_reliability_profiles',
        'get_reliability_profile',
        'list_reliability_runs',
        'get_reliability_run',
        'get_reliability_run_report_json',
        'get_reliability_run_report_markdown',
        'list_reliability_evals',
        'run_reliability_evals',
        'list_learning_ledger_runs',
        'get_learning_ledger_run',
        'list_capability_proposals',
        'get_capability_proposal',
        'update_capability_proposal',
        'approve_capability_proposal',
        'apply_capability_proposal',
        'reject_capability_proposal',
        'retire_capability_proposal',
        'list_learning_ledger_lessons',
        'update_learning_ledger_lesson',
        'approve_learning_ledger_lesson',
        'reject_learning_ledger_lesson',
        'retire_learning_ledger_lesson',
        'restore_learning_ledger_lesson',
        'digest_learning_ledger_note',
        'list_agent_chats',
        'get_agent_chat',
        'delete_agent_chat',
        'agent_runtime_controls',
        'update_agent_runtime_controls',
        'list_agent_command_allowlist',
        'create_agent_command_allowlist',
        'delete_agent_command_allowlist',
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
