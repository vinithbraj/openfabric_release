"""Settings Prompt Editor Agent UI routes."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared import *


def register_settings_prompt_editor_routes(ctx: AgentUiRouteContext) -> None:
    """Register settings prompt editor routes."""

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

    def _agent_settings_defaults() -> dict[str, Any]:
        """Return defaults shared by the chat drawer and Mission Control settings."""

        audio_controls = _runtime_controls_snapshot()
        defaults = {
            "agent_display_name": "Agent",
            "auto_approve_commands": bool(audio_controls.get("auto_approve_commands")),
            "ui_agent_mode": "llm_operator",
            "ui_theme": "github",
            "operator_policy_profile": normalize_operator_policy_profile(
                audio_controls.get("operator_policy_profile")
            ),
            "reasoning_profile": normalize_reasoning_profile(
                audio_controls.get("reasoning_profile")
            ),
            "repair_profile": normalize_repair_profile(audio_controls.get("repair_profile")),
            "workflow_execution_mode": normalize_workflow_execution_mode(
                audio_controls.get("workflow_execution_mode")
            ),
            "prompt_rephrase_enabled": bool(
                audio_controls.get("prompt_rephrase_enabled", True)
            ),
            "response_streaming_enabled": bool(audio_controls.get("response_streaming_enabled")),
            "llm_operator_final_response_mode": str(
                audio_controls.get("llm_operator_final_response_mode") or "simple"
            ),
            "llm_operator_cardinality_judge_mode": normalize_cardinality_judge_mode(
                audio_controls.get("llm_operator_cardinality_judge_mode")
            ),
            "llm_operator_verbose_enabled": bool(
                audio_controls.get("llm_operator_verbose_enabled")
            ),
            "llm_operator_step_validation_enabled": bool(
                audio_controls.get("llm_operator_step_validation_enabled")
            ),
            "llm_operator_verification_enforced": bool(
                audio_controls.get("llm_operator_verification_enforced")
            ),
            "sql_agent_chat_route_mode": str(
                audio_controls.get("sql_agent_chat_route_mode") or "agentic"
            ),
            "operator_workspace_cwd_guard_enabled": bool(
                audio_controls.get("operator_workspace_cwd_guard_enabled")
            ),
            "llm_operator_formatter_source_preview_chars": int(
                settings.llm_operator_formatter_source_preview_chars
            ),
            "agent_clarification_mode": normalize_agent_clarification_mode(
                audio_controls.get("agent_clarification_mode"),
            ),
            "llm_operator_max_clarification_rounds": int(
                audio_controls["llm_operator_max_clarification_rounds"]
                if "llm_operator_max_clarification_rounds" in audio_controls
                else getattr(settings, "llm_operator_max_clarification_rounds", 3)
            ),
            "agent_memory_enabled": bool(audio_controls.get("agent_memory_enabled")),
            "agent_memory_prompt_max_chars": int(
                audio_controls.get("agent_memory_prompt_max_chars")
                or getattr(settings, "agent_memory_prompt_max_chars", 3000)
            ),
            "agent_learning_ledger_enabled": bool(settings.agent_learning_ledger_enabled),
            "agent_learning_ledger_auto_learn_enabled": bool(
                audio_controls.get("agent_learning_ledger_auto_learn_enabled")
            ),
            "agent_command_template_cache_similarity_threshold": float(
                audio_controls["agent_command_template_cache_similarity_threshold"]
                if "agent_command_template_cache_similarity_threshold" in audio_controls
                else getattr(
                    settings,
                    "agent_command_template_cache_similarity_threshold",
                    0.82,
                )
            ),
            "agent_command_template_cache_secondary_similarity_threshold": float(
                audio_controls["agent_command_template_cache_secondary_similarity_threshold"]
                if "agent_command_template_cache_secondary_similarity_threshold" in audio_controls
                else getattr(
                    settings,
                    "agent_command_template_cache_secondary_similarity_threshold",
                    0.25,
                )
            ),
            "lrnt_enabled": bool(audio_controls.get("lrnt_enabled", True)),
            "lrnt_similarity_threshold": _coerce_runtime_float(
                audio_controls.get("lrnt_similarity_threshold"),
                float(getattr(settings, "lrnt_similarity_threshold", 0.92)),
                minimum=0.0,
                maximum=1.0,
            ),
            "lrdirect_enabled": bool(audio_controls.get("lrdirect_enabled", True)),
            "reliability_mode": _coerce_reliability_mode(
                audio_controls.get("reliability_mode") or settings.reliability_mode
            ),
            "reliability_verifier_enforced": bool(
                audio_controls.get("reliability_verifier_enforced")
            ),
            "reliability_max_recovery_probes": int(
                audio_controls.get(
                    "reliability_max_recovery_probes",
                    settings.reliability_max_recovery_probes,
                )
            ),
            "reliability_max_autonomous_repair_attempts": int(
                audio_controls.get(
                    "reliability_max_autonomous_repair_attempts",
                    settings.reliability_max_autonomous_repair_attempts,
                )
            ),
            "reliability_weak_model_plan_action_cap": int(
                audio_controls.get(
                    "reliability_weak_model_plan_action_cap",
                    settings.reliability_weak_model_plan_action_cap,
                )
            ),
            "reliability_approval_envelope_budget": int(
                audio_controls.get(
                    "reliability_approval_envelope_budget",
                    settings.reliability_approval_envelope_budget,
                )
            ),
            "browser_notifications_enabled": True,
            "notification_sound_enabled": True,
            "notification_sound_variant": "chime",
            "notification_sound_volume": 0.55,
            "ui_terminal_visible": True,
            "ui_trace_visible": True,
            "ui_visualization_visible": True,
            "ui_chat_bubbles_enabled": False,
            "audio_voice_input_enabled": bool(settings.audio_transcriber_enabled),
            "audio_transcriber_service_host": str(
                audio_controls.get("audio_transcriber_service_host")
                or AUDIO_TRANSCRIBER_DEFAULT_HOST
            ),
            "audio_transcriber_service_port": int(
                audio_controls.get("audio_transcriber_service_port")
                or AUDIO_TRANSCRIBER_DEFAULT_PORT
            ),
            "audio_transcriber_service_url": str(
                audio_controls.get("audio_transcriber_service_url")
                or f"http://{AUDIO_TRANSCRIBER_DEFAULT_HOST}:{AUDIO_TRANSCRIBER_DEFAULT_PORT}"
            ),
            "audio_capture_preset": "balanced",
            "audio_silence_timeout_seconds": 2,
            "agent_events_enabled": bool(audio_controls.get("agent_events_enabled")),
            "agent_events_poll_seconds": float(settings.agent_events_poll_seconds),
            "agent_events_max_run_history": int(settings.agent_events_max_run_history),
            "ui_number_animation": str(settings.agent_ui_number_animation or "odometer"),
            "ui_chat_pop_animation": "none",
            "ui_thinking_text_animation": "roll-up",
            "ui_command_output_expanded_by_default": False,
            "ui_auto_immersive_min_width_px": int(
                audio_controls["ui_auto_immersive_min_width_px"]
                if "ui_auto_immersive_min_width_px" in audio_controls
                else settings.agent_ui_auto_immersive_min_width_px
            ),
            "llm_timeout_seconds": int(
                audio_controls.get("llm_timeout_seconds") or settings.llm_timeout_seconds
            ),
            "llm_max_tokens": int(
                audio_controls["llm_max_tokens"]
                if "llm_max_tokens" in audio_controls
                else settings.llm_max_tokens
            ),
            "llm_base_scheme": str(audio_controls.get("llm_base_scheme") or "http"),
            "llm_base_host": str(audio_controls.get("llm_base_host") or "127.0.0.1"),
            "llm_base_port": int(audio_controls.get("llm_base_port") or 8000),
            "llm_base_path": str(audio_controls.get("llm_base_path") or "/v1"),
            "llm_base_url": str(audio_controls.get("llm_base_url") or ""),
            **_agent_llm_launch_defaults(settings),
        }
        return {
            key: value
            for key, value in defaults.items()
            if key not in REMOVED_PUBLIC_SETTING_KEYS and key not in PROFILE_REPLACED_INTERNAL_KEYS
        }

    def _agent_settings_limits() -> dict[str, dict[str, int | float]]:
        """Return browser-safe bounds for registry-backed settings."""

        return {
            "llm_operator_max_clarification_rounds": {"min": 0, "max": 10},
            "clarification_rounds": {"min": 0, "max": 10},
            "formatter_source_preview_chars": {"min": 200, "max": 20000},
            "agent_memory_prompt_max_chars": {"min": 200, "max": 20000},
            "agent_events_poll_seconds": {"min": 1, "max": 3600},
            "agent_events_max_run_history": {"min": 1, "max": 100000},
            "ui_auto_immersive_min_width_px": {"min": 0, "max": 4000},
            "reliability_recovery_probes": {"min": 0, "max": 20},
            "reliability_autonomous_repair_attempts": {"min": 0, "max": 20},
            "reliability_plan_action_cap": {"min": 1, "max": 32},
            "reliability_approval_envelope_budget": {"min": 0, "max": 20},
            "llm_max_tokens": {"min": 0, "max": 65536},
            "llm_base_port": {"min": 1, "max": 65535},
            "llm_endpoint_port": {"min": 1, "max": 65535},
            "llm_timeout_seconds": {"min": 1, "max": 1800},
            "audio_transcriber_service_port": {"min": 1, "max": 65535},
            "audio_silence_timeout_seconds": {"min": 1, "max": 10},
            "notification_sound_volume": {"min": 0.0, "max": 1.0},
        }

    def _registry_option(value: str, label: str, description: str = "") -> dict[str, str]:
        item = {"value": value, "label": label}
        if description:
            item["description"] = description
        return item

    def _registry_setting(
        key: str,
        label: str,
        control: str,
        *,
        target: str = "preferences",
        description: str = "",
        impact: str = "",
        options: list[dict[str, str]] | None = None,
        limit: str | None = None,
        readonly: bool = False,
        source: str = "shared setting",
        value: Any | None = None,
    ) -> dict[str, Any]:
        defaults = _agent_settings_defaults()
        limits = _agent_settings_limits()
        payload: dict[str, Any] = {
            "key": key,
            "label": label,
            "control": control,
            "target": "readonly" if readonly else target,
            "readonly": bool(readonly),
            "source": source,
            "default": defaults.get(key),
            "description": description,
            "impact": impact,
        }
        if value is not None:
            payload["value"] = value
        if options is not None:
            payload["options"] = options
        if limit:
            payload["limit"] = limits.get(limit, {})
        elif key in limits:
            payload["limit"] = limits.get(key, {})
        return payload

    def _agent_settings_registry_payload() -> dict[str, Any]:
        defaults_all = _agent_settings_defaults()
        public_settings_keys = PUBLIC_RUNTIME_CONTROL_KEYS | PUBLIC_UI_PREFERENCE_KEYS
        defaults = {
            key: value
            for key, value in defaults_all.items()
            if key in public_settings_keys
        }
        public_limits = {
            key: value
            for key, value in _agent_settings_limits().items()
            if key in public_settings_keys
        }
        clarification_options = [
            _registry_option("auto_pilot", "Auto-pilot"),
            _registry_option("balanced", "Balanced"),
            _registry_option("pedantic", "Pedantic"),
        ]
        final_response_options = [
            _registry_option("detailed", "Detailed"),
            _registry_option("simple", "Simple"),
        ]
        cardinality_judge_options = [
            _registry_option("auto", "Auto"),
            _registry_option("on", "On"),
            _registry_option("off", "Off"),
        ]
        sql_route_options = [
            _registry_option("agentic", "Agentic"),
            _registry_option("direct", "Direct"),
        ]
        reliability_options = [
            _registry_option("off", "Off"),
            _registry_option("standard", "Standard"),
            _registry_option("aggressive", "Aggressive"),
        ]
        llm_scheme_options = [
            _registry_option("http", "http"),
            _registry_option("https", "https"),
        ]
        theme_options = [
            _registry_option("github", "GitHub"),
            _registry_option("light", "Light"),
            _registry_option("daylight", "Daylight"),
            _registry_option("mint", "Mint"),
            _registry_option("citrus", "Citrus"),
            _registry_option("rosewater", "Rosewater"),
            _registry_option("steel", "Steel"),
            _registry_option("graphite", "Graphite"),
            _registry_option("midnight", "Midnight"),
            _registry_option("aurora", "Aurora"),
            _registry_option("ember", "Ember"),
            _registry_option("ubuntu", "Ubuntu"),
            _registry_option("ubuntu-dark", "Ubuntu Dark"),
            _registry_option("dracula", "Dracula"),
            _registry_option("nord", "Nord"),
            _registry_option("tokyo-night", "Tokyo Night"),
            _registry_option("catppuccin-mocha", "Catppuccin Mocha"),
            _registry_option("solarized-dark", "Solarized Dark"),
            _registry_option("gruvbox-dark", "Gruvbox Dark"),
            _registry_option("nebula", "Nebula"),
            _registry_option("moss", "Moss"),
            _registry_option("retro", "Retro"),
            _registry_option("lagoon", "Lagoon"),
            _registry_option("sakura", "Sakura"),
            _registry_option("harbor", "Harbor"),
            _registry_option("circuit", "Circuit"),
            _registry_option("contrast", "Contrast"),
            _registry_option("random-pastel", "Random Pastel"),
        ]
        agent_mode_options = [
            _registry_option("llm_operator", "Operator"),
            _registry_option("standard", "Standard"),
            _registry_option("advisory", "Advisory"),
        ]
        number_animation_options = [
            _registry_option("odometer", "Odometer"),
            _registry_option("fade", "Fade"),
            _registry_option("slide", "Slide"),
            _registry_option("pop", "Pop"),
            _registry_option("flip", "Flip"),
            _registry_option("none", "None"),
        ]
        chat_pop_animation_options = [
            _registry_option("none", "None"),
            _registry_option("soft-rise", "Soft rise"),
            _registry_option("slide-up", "Slide up"),
            _registry_option("slide-side", "Slide side"),
            _registry_option("scale-pop", "Scale pop"),
            _registry_option("spring", "Spring"),
            _registry_option("flip", "Flip"),
            _registry_option("skew-snap", "Skew snap"),
            _registry_option("blur-glow", "Blur glow"),
            _registry_option("drop-in", "Drop in"),
            _registry_option("stream-roll", "Stream roll"),
            _registry_option("odometer", "Odometer"),
        ]
        thinking_text_animation_options = [
            _registry_option("roll-up", "Roll up"),
            _registry_option("soft-rise", "Soft rise"),
            _registry_option("slide-left", "Slide left"),
            _registry_option("snap-down", "Snap down"),
            _registry_option("fade", "Fade"),
            _registry_option("pop", "Pop"),
            _registry_option("flip", "Flip"),
            _registry_option("blur", "Blur"),
            _registry_option("skew", "Skew"),
            _registry_option("bounce", "Bounce"),
            _registry_option("swing", "Swing"),
            _registry_option("odometer", "Odometer"),
            _registry_option("none", "None"),
        ]
        notification_sound_options = [
            _registry_option("chime", "Chime"),
            _registry_option("ping", "Ping"),
            _registry_option("soft", "Soft"),
            _registry_option("alert", "Alert"),
        ]
        audio_capture_options = [
            _registry_option("balanced", "Balanced"),
            _registry_option("noise_reduction", "Noise reduction"),
            _registry_option("low_latency", "Low latency"),
        ]
        profile_section = {
            "id": "agent_behavior",
            "title": "Agent Behavior",
            "description": "Backend-owned profiles that control planning, reasoning, repair, and response streaming.",
            "impact": "Changes apply to the next request in the same chat; complex tool work decomposes into granular streaming steps by default.",
            "settings": [
                _registry_setting(
                    "operator_policy_profile",
                    "Operator policy profile",
                    "select",
                    target="runtime",
                    options=[
                        _registry_option("deterministic", "Deterministic", "Use deterministic policy checks."),
                        _registry_option("assisted", "Assisted", "Use LLM-assisted policy checks where useful."),
                    ],
                    description="Replaces individual operator policy knobs with one coherent profile.",
                    impact="Deterministic is faster and more stable; assisted spends model effort on ambiguous policy decisions.",
                ),
                _registry_setting(
                    "reasoning_profile",
                    "Reasoning profile",
                    "select",
                    target="runtime",
                    options=[
                        _registry_option("fast", "Fast"),
                        _registry_option("balanced", "Balanced"),
                        _registry_option("deep", "Deep"),
                    ],
                    description="Controls deliberation depth, plan review, answer judging, and self-briefing as one profile.",
                    impact="Fast minimizes latency; deep spends more model calls on hard requests.",
                ),
                _registry_setting(
                    "repair_profile",
                    "Repair profile",
                    "select",
                    target="runtime",
                    options=[
                        _registry_option("conservative", "Conservative"),
                        _registry_option("balanced", "Balanced"),
                        _registry_option("aggressive", "Aggressive"),
                    ],
                    description="Controls bounded repair budgets and confidence thresholds as one profile.",
                    impact="Aggressive retries more; conservative avoids repair loops.",
                ),
                _registry_setting(
                    "workflow_execution_mode",
                    "Workflow execution",
                    "select",
                    target="runtime",
                    options=[
                        _registry_option("auto", "Auto"),
                        _registry_option("full_plan", "Full plan"),
                        _registry_option("streaming", "Streaming workflow"),
                    ],
                    description="Controls how staged workflows execute; auto and streaming prefer granular decomposition for non-atomic tool requests.",
                    impact="Atomic requests may still run as one scoped task, while complex requests stay decomposed.",
                ),
                _registry_setting(
                    "prompt_rephrase_enabled",
                    "Rephrase prompts",
                    "boolean",
                    target="runtime",
                    description="Rewrites noisy or duplicated prompts before planning while preserving intent.",
                    impact="Applies to new prompts before classification and operator planning.",
                ),
                _registry_setting(
                    "response_streaming_enabled",
                    "Response token streaming",
                    "boolean",
                    target="runtime",
                    description="Streams LLM response text as tokens when the response path supports it.",
                    impact="Separate from workflow streaming.",
                ),
            ],
        }
        runtime_section = {
            "id": "runtime_controls",
            "title": "Runtime Controls",
            "description": "Backend-owned operational controls that are not profile-derived.",
            "impact": "Values apply to the next request.",
            "settings": [
                _registry_setting("auto_approve_commands", "Auto-approve commands", "boolean", target="runtime"),
                _registry_setting(
                    "agent_clarification_mode",
                    "Clarification mode",
                    "select",
                    target="runtime",
                    options=clarification_options,
                ),
                _registry_setting(
                    "llm_operator_max_clarification_rounds",
                    "Max clarification rounds",
                    "number",
                    target="runtime",
                ),
                _registry_setting(
                    "llm_operator_final_response_mode",
                    "Final response mode",
                    "select",
                    target="runtime",
                    options=final_response_options,
                ),
                _registry_setting(
                    "llm_operator_cardinality_judge_mode",
                    "Cardinality judge",
                    "select",
                    target="runtime",
                    options=cardinality_judge_options,
                ),
                _registry_setting(
                    "llm_operator_verification_enforced",
                    "Verification enforced",
                    "boolean",
                    target="runtime",
                ),
                _registry_setting("llm_operator_verbose_enabled", "Verbose operator", "boolean", target="runtime"),
                _registry_setting("llm_operator_step_validation_enabled", "Step validation", "boolean", target="runtime"),
                _registry_setting("operator_workspace_cwd_guard_enabled", "Workspace cwd guard", "boolean", target="runtime"),
                _registry_setting(
                    "sql_agent_chat_route_mode",
                    "SQL route mode",
                    "select",
                    target="runtime",
                    options=sql_route_options,
                ),
                _registry_setting("agent_memory_enabled", "Agent memory", "boolean", target="runtime"),
                _registry_setting(
                    "agent_memory_prompt_max_chars",
                    "Memory prompt chars",
                    "number",
                    target="runtime",
                ),
                _registry_setting("agent_events_enabled", "Agent events", "boolean", target="runtime"),
                _registry_setting("agent_learning_ledger_auto_learn_enabled", "Auto-learn ledger", "boolean", target="runtime"),
                _registry_setting(
                    "agent_command_template_cache_similarity_threshold",
                    "LR threshold",
                    "number",
                    target="runtime",
                ),
                _registry_setting(
                    "agent_command_template_cache_secondary_similarity_threshold",
                    "LR background threshold",
                    "number",
                    target="runtime",
                ),
                _registry_setting("lrnt_enabled", "LRN-T / LR-T", "boolean", target="runtime"),
                _registry_setting(
                    "lrnt_similarity_threshold",
                    "LR-T threshold",
                    "number",
                    target="runtime",
                ),
                _registry_setting("lrdirect_enabled", "LR Direct", "boolean", target="runtime"),
                _registry_setting(
                    "reliability_mode",
                    "Reliability mode",
                    "select",
                    target="runtime",
                    options=reliability_options,
                ),
                _registry_setting("reliability_verifier_enforced", "Reliability verifier", "boolean", target="runtime"),
                _registry_setting(
                    "reliability_max_recovery_probes",
                    "Max recovery probes",
                    "number",
                    target="runtime",
                ),
                _registry_setting(
                    "reliability_max_autonomous_repair_attempts",
                    "Max autonomous repairs",
                    "number",
                    target="runtime",
                ),
                _registry_setting(
                    "reliability_weak_model_plan_action_cap",
                    "Weak-model action cap",
                    "number",
                    target="runtime",
                ),
                _registry_setting(
                    "reliability_approval_envelope_budget",
                    "Approval envelope budget",
                    "number",
                    target="runtime",
                ),
                _registry_setting("ui_auto_immersive_min_width_px", "Auto immersive width", "number", target="runtime"),
                _registry_setting(
                    "llm_base_scheme",
                    "LLM scheme",
                    "select",
                    target="runtime",
                    options=llm_scheme_options,
                ),
                _registry_setting("llm_base_host", "LLM host", "text", target="runtime"),
                _registry_setting("llm_base_port", "LLM port", "number", target="runtime"),
                _registry_setting("llm_base_path", "LLM base path", "text", target="runtime"),
                _registry_setting("llm_base_url", "LLM base URL", "text", target="runtime", readonly=True),
                _registry_setting("llm_timeout_seconds", "LLM timeout seconds", "number", target="runtime"),
                _registry_setting("llm_max_tokens", "LLM max output tokens", "number", target="runtime"),
                _registry_setting("audio_transcriber_service_host", "Audio service host", "text", target="runtime"),
                _registry_setting("audio_transcriber_service_port", "Audio service port", "number", target="runtime"),
                _registry_setting(
                    "audio_transcriber_service_url",
                    "Audio service URL",
                    "text",
                    target="runtime",
                    readonly=True,
                ),
            ],
        }
        ui_section = {
            "id": "ui_preferences",
            "title": "UI Preferences",
            "description": "Backend-owned UI preferences shared by browser sessions.",
            "impact": "The browser updates these values through the backend settings config endpoint.",
            "settings": [
                _registry_setting("ui_theme", "Theme", "select", target="ui", options=theme_options),
                _registry_setting(
                    "ui_agent_mode",
                    "Default agent mode",
                    "select",
                    target="ui",
                    options=agent_mode_options,
                ),
                _registry_setting("agent_display_name", "Agent display name", "text", target="ui"),
                _registry_setting(
                    "ui_number_animation",
                    "Number animation",
                    "select",
                    target="ui",
                    options=number_animation_options,
                ),
                _registry_setting(
                    "ui_chat_pop_animation",
                    "Chat pop animation",
                    "select",
                    target="ui",
                    options=chat_pop_animation_options,
                ),
                _registry_setting(
                    "ui_thinking_text_animation",
                    "Thinking text animation",
                    "select",
                    target="ui",
                    options=thinking_text_animation_options,
                ),
                _registry_setting("ui_terminal_visible", "Terminal pane", "boolean", target="ui"),
                _registry_setting("ui_trace_visible", "Developer trace", "boolean", target="ui"),
                _registry_setting("ui_visualization_visible", "Visualization pane", "boolean", target="ui"),
                _registry_setting("ui_chat_bubbles_enabled", "Chat bubbles", "boolean", target="ui"),
                _registry_setting(
                    "ui_command_output_expanded_by_default",
                    "Expand command output by default",
                    "boolean",
                    target="ui",
                ),
                _registry_setting("browser_notifications_enabled", "Browser notifications", "boolean", target="ui"),
                _registry_setting("notification_sound_enabled", "Notification sound", "boolean", target="ui"),
                _registry_setting(
                    "notification_sound_variant",
                    "Notification sound",
                    "select",
                    target="ui",
                    options=notification_sound_options,
                ),
                _registry_setting("notification_sound_volume", "Notification volume", "range", target="ui"),
                _registry_setting("audio_voice_input_enabled", "Voice input", "boolean", target="ui"),
                _registry_setting(
                    "audio_capture_preset",
                    "Audio capture preset",
                    "select",
                    target="ui",
                    options=audio_capture_options,
                ),
                _registry_setting(
                    "audio_silence_timeout_seconds",
                    "Audio silence stop seconds",
                    "number",
                    target="ui",
                ),
            ],
        }
        optimization_profiles = {
            "accuracy": {
                "id": "accuracy",
                "label": "Accuracy",
                "description": "Prioritize stronger review and repair without exposing low-level knobs.",
                "values": {
                    "ui_agent_mode": "llm_operator",
                    "operator_policy_profile": "assisted",
                    "reasoning_profile": "deep",
                    "repair_profile": "aggressive",
                    "workflow_execution_mode": "full_plan",
                    "prompt_rephrase_enabled": True,
                    "response_streaming_enabled": True,
                    "agent_clarification_mode": "balanced",
                    "operator_workspace_cwd_guard_enabled": True,
                    "llm_operator_step_validation_enabled": True,
                    "agent_learning_ledger_auto_learn_enabled": True,
                    "reliability_mode": "aggressive",
                    "reliability_verifier_enforced": True,
                },
            },
            "speed": {
                "id": "speed",
                "label": "Speed",
                "description": "Prioritize fast local action and minimal model deliberation.",
                "values": {
                    "ui_agent_mode": "llm_operator",
                    "operator_policy_profile": "deterministic",
                    "reasoning_profile": "fast",
                    "repair_profile": "conservative",
                    "workflow_execution_mode": "streaming",
                    "prompt_rephrase_enabled": True,
                    "response_streaming_enabled": True,
                    "agent_clarification_mode": "balanced",
                    "operator_workspace_cwd_guard_enabled": False,
                    "llm_operator_step_validation_enabled": False,
                    "agent_learning_ledger_auto_learn_enabled": False,
                    "reliability_mode": "standard",
                    "reliability_verifier_enforced": False,
                },
            },
        }
        inventory_keys = set(PUBLIC_RUNTIME_CONTROL_KEYS) | set(PUBLIC_UI_PREFERENCE_KEYS)
        return {
            "version": "20260602-text-odometer",
            "defaults": defaults,
            "limits": public_limits,
            "runtime_control_keys": sorted(PUBLIC_RUNTIME_CONTROL_KEYS),
            "runtime_controls": _public_runtime_controls_snapshot(),
            "optimization_profiles": optimization_profiles,
            "optimization_controlled_keys": [
                "operator_policy_profile",
                "reasoning_profile",
                "repair_profile",
                "workflow_execution_mode",
                "prompt_rephrase_enabled",
                "response_streaming_enabled",
                "agent_clarification_mode",
                "operator_workspace_cwd_guard_enabled",
                "llm_operator_step_validation_enabled",
                "agent_learning_ledger_auto_learn_enabled",
                "reliability_mode",
                "reliability_verifier_enforced",
            ],
            "presets": [],
            "sections": [profile_section, runtime_section, ui_section],
            "settings_inventory": settings_inventory_for_keys(inventory_keys),
        }

    @app.get("/api/agent/settings/registry")
    def agent_settings_registry() -> dict[str, Any]:
        """Return the server-owned settings registry for Mission Control."""

        return _agent_settings_registry_payload()

    @app.get("/api/agent/settings/config")
    def agent_settings_config() -> dict[str, Any]:
        """Return request-scoped Agent UI setting defaults and browser-safe limits."""

        registry = _agent_settings_registry_payload()
        return {"defaults": registry["defaults"], "limits": registry["limits"]}

    @app.get("/api/agent/prompt-macros")
    def agent_prompt_macros() -> dict[str, Any]:
        """Return browser-safe prompt macro registry entries."""

        return {"macros": prompt_macro_registry()}

    @app.get("/api/agent/prompt-editor/templates")
    def list_prompt_editor_templates() -> dict[str, Any]:
        """Return prompt templates available in the local editor."""

        templates = [
            _prompt_template_payload(comparison)
            for comparison in prompt_template_store.list_for_editor()
            if comparison.active_record is not None
        ]
        return {
            "templates": templates,
            "db_path": str(settings.agent_prompts_db_path),
            "count": len(templates),
        }

    @app.get("/api/agent/prompt-editor/templates/{prompt_key}")
    def get_prompt_editor_template(prompt_key: str) -> dict[str, Any]:
        """Return one prompt template for editing."""

        comparison = _prompt_template_or_404(prompt_template_store, prompt_key)
        return {
            "template": _prompt_template_payload(comparison, include_body=True),
            "db_path": str(settings.agent_prompts_db_path),
        }

    @app.patch("/api/agent/prompt-editor/templates/{prompt_key}")
    def update_prompt_editor_template(
        prompt_key: str,
        payload: PromptTemplateUpdatePayload,
    ) -> dict[str, Any]:
        """Update one prompt template body in the active prompt DB."""

        try:
            prompt_template_store.update_body(prompt_key, payload.body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Prompt template not found") from exc
        except PromptTemplateRenderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        configure_prompt_fetcher(settings.agent_prompts_db_path)
        comparison = _prompt_template_or_404(prompt_template_store, prompt_key)
        return {
            "template": _prompt_template_payload(comparison, include_body=True),
            "db_path": str(settings.agent_prompts_db_path),
        }

    @app.post("/api/agent/prompt-editor/templates/{prompt_key}/reset")
    def reset_prompt_editor_template(prompt_key: str) -> dict[str, Any]:
        """Reset one prompt template to its checked-in default."""

        try:
            prompt_template_store.reset_to_default(prompt_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Prompt template default not found") from exc
        configure_prompt_fetcher(settings.agent_prompts_db_path)
        comparison = _prompt_template_or_404(prompt_template_store, prompt_key)
        return {
            "template": _prompt_template_payload(comparison, include_body=True),
            "db_path": str(settings.agent_prompts_db_path),
        }

    @app.post("/api/agent/prompt-editor/templates/{prompt_key}/render")
    def render_prompt_editor_template(
        prompt_key: str,
        payload: PromptTemplateRenderPayload,
    ) -> dict[str, Any]:
        """Dry-run render one prompt template with supplied variables."""

        _prompt_template_or_404(prompt_template_store, prompt_key)
        try:
            rendered = prompt_template_store.render_for_editor(prompt_key, payload.variables)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Prompt template not found") from exc
        except PromptTemplateRenderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        comparison = _prompt_template_or_404(prompt_template_store, prompt_key)
        record = comparison.active_record
        try:
            variables = extract_template_variables(record.body) if record is not None else []
        except PromptTemplateRenderError:
            variables = []
        return {
            "rendered": rendered,
            "variables": variables,
        }

    @app.get("/api/agent/prompt-editor/parameters")
    def list_prompt_editor_parameters(
        q: str = "",
        tag: str = "",
        limit: int = 200,
    ) -> dict[str, Any]:
        """Return parameter-store entries available in the local editor."""

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
            "count": len(records),
            "stats": {
                "total": len(all_records),
                "filtered": len(records),
                "sensitive": sum(1 for record in all_records if record.sensitive),
                "total_uses": sum(int(record.use_count or 0) for record in all_records),
            },
        }

    @app.get("/api/agent/prompt-editor/memories")
    def list_prompt_editor_memories(
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
        """Return memory entries available in the local editor."""

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
        all_entries = memory_store.list_entries()
        status_counts = {"active": 0, "proposed": 0, "retired": 0}
        for entry in all_entries:
            if entry.status in status_counts:
                status_counts[entry.status] += 1
        return {
            "memories": [_memory_entry_payload(entry) for entry in entries],
            "db_path": str(settings.agent_memory_db_path),
            "count": len(entries),
            "stats": {
                "total": len(all_entries),
                "active": status_counts["active"],
                "proposed": status_counts["proposed"],
                "retired": status_counts["retired"],
            },
        }

    @app.post("/api/agent/prompt-editor/memories")
    def create_prompt_editor_memory(payload: MemoryEntryCreate) -> dict[str, Any]:
        """Create one persistent memory entry from the editor."""

        if not str(payload.instruction or "").strip():
            raise HTTPException(status_code=400, detail="Memory instruction cannot be empty")
        memory_store = _memory_store_or_error(settings, agent_runtime)
        entry = memory_store.create_entry(payload, actor="prompt_editor")
        return {
            "memory": _memory_entry_payload(entry, include_body=True),
            "db_path": str(settings.agent_memory_db_path),
        }

    @app.get("/api/agent/prompt-editor/memories/{memory_id}")
    def get_prompt_editor_memory(memory_id: str) -> dict[str, Any]:
        """Return one memory entry for editing."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        entry = _memory_entry_or_404(memory_store, memory_id)
        return {
            "memory": _memory_entry_payload(entry, include_body=True),
            "db_path": str(settings.agent_memory_db_path),
            "audit_events": [
                event.model_dump(mode="json")
                for event in memory_store.audit_events(memory_id)
            ],
        }

    @app.patch("/api/agent/prompt-editor/memories/{memory_id}")
    def update_prompt_editor_memory(
        memory_id: str,
        payload: PromptEditorMemoryUpdatePayload,
    ) -> dict[str, Any]:
        """Update one persistent memory entry from the editor."""

        memory_store = _memory_store_or_error(settings, agent_runtime)
        _memory_entry_or_404(memory_store, memory_id)
        update_payload = _memory_update_from_editor_payload(payload)
        entry = memory_store.update_entry(memory_id, update_payload, actor="prompt_editor")
        if entry is None:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        if payload.status is not None and payload.status != entry.status:
            entry = memory_store.set_status(memory_id, payload.status, actor="prompt_editor")
            if entry is None:
                raise HTTPException(status_code=404, detail="Memory entry not found")
        return {
            "memory": _memory_entry_payload(entry, include_body=True),
            "db_path": str(settings.agent_memory_db_path),
            "audit_events": [
                event.model_dump(mode="json")
                for event in memory_store.audit_events(memory_id)
            ],
        }

    for _name in (
        '_agent_settings_defaults',
        '_agent_settings_limits',
        '_registry_option',
        '_registry_setting',
        '_agent_settings_registry_payload',
        'agent_settings_registry',
        'agent_settings_config',
        'agent_prompt_macros',
        'list_prompt_editor_templates',
        'get_prompt_editor_template',
        'update_prompt_editor_template',
        'reset_prompt_editor_template',
        'render_prompt_editor_template',
        'list_prompt_editor_parameters',
        'list_prompt_editor_memories',
        'create_prompt_editor_memory',
        'get_prompt_editor_memory',
        'update_prompt_editor_memory',
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
