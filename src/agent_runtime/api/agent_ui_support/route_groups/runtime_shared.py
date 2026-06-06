"""Install prompt history, runtime control, and backend context helpers."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared_imports import *


def install_runtime_helpers(ctx: Any) -> None:
    """Install prompt history, runtime control, and backend context helpers."""


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

    def _submit_agent_request(payload: AgentRequestPayload) -> dict[str, Any]:
        submitter = getattr(ctx, "submit_agent_request", None)
        if not callable(submitter):
            raise RuntimeError("Agent request route handler is not registered.")
        return submitter(payload)

    def _normalize_prompt_history_items(value: Any) -> list[str]:
        raw_items = value if isinstance(value, list) else []
        items: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if not text:
                continue
            if text in items:
                items.remove(text)
            items.append(text)
        return items[-PROMPT_HISTORY_MAX_ITEMS:]

    def _prompt_history_items() -> list[str]:
        payload = ui_settings_store.get_namespace(PROMPT_HISTORY_SETTINGS_NAMESPACE)
        return _normalize_prompt_history_items(payload.get("items"))

    def _record_prompt_history_item(prompt: str) -> list[str]:
        value = str(prompt or "").strip()
        items = _prompt_history_items()
        if value:
            if value in items:
                items.remove(value)
            items.append(value)
            items = items[-PROMPT_HISTORY_MAX_ITEMS:]
            ui_settings_store.set_namespace_values(
                PROMPT_HISTORY_SETTINGS_NAMESPACE,
                {"items": items},
            )
        return items

    def _command_allowlist_context() -> dict[str, Any]:
        return {"operator_command_allowlist_hashes": command_allowlist_store.enabled_hashes()}

    def _apply_command_allowlist_context(context: dict[str, Any]) -> dict[str, Any]:
        context["operator_command_allowlist_hashes"] = command_allowlist_store.enabled_hashes()
        return context

    def _runtime_controls_snapshot() -> dict[str, Any]:
        with runtime_controls_lock:
            events_enabled = bool(settings.agent_events_enabled) and bool(
                runtime_controls.get("agent_events_enabled")
            )
            sql_agent_chat_route_mode = str(
                runtime_controls.get("sql_agent_chat_route_mode") or "agentic"
            ).strip().lower()
            if sql_agent_chat_route_mode not in {"agentic", "direct"}:
                sql_agent_chat_route_mode = "agentic"
            reliability_mode = _coerce_reliability_mode(runtime_controls.get("reliability_mode"))
            clarification_mode = normalize_agent_clarification_mode(
                runtime_controls.get("agent_clarification_mode"),
            )
            final_response_mode = str(
                runtime_controls.get("llm_operator_final_response_mode") or "detailed"
            ).strip().lower()
            if final_response_mode not in {"detailed", "simple"}:
                final_response_mode = "detailed"
            try:
                llm_endpoint = _agent_llm_endpoint_context(runtime_controls, settings)
            except HTTPException:
                llm_endpoint = _agent_llm_endpoint_defaults(settings)
            profile_values = {
                "operator_policy_profile": normalize_operator_policy_profile(
                    runtime_controls.get("operator_policy_profile")
                ),
                "reasoning_profile": normalize_reasoning_profile(
                    runtime_controls.get("reasoning_profile")
                ),
                "repair_profile": normalize_repair_profile(
                    runtime_controls.get("repair_profile")
                ),
                "workflow_execution_mode": normalize_workflow_execution_mode(
                    runtime_controls.get("workflow_execution_mode")
                ),
                "prompt_rephrase_enabled": bool(
                    runtime_controls.get("prompt_rephrase_enabled", True)
                ),
                "response_streaming_enabled": bool(
                    runtime_controls.get("response_streaming_enabled")
                ),
                "shell_input_bindings_mode": normalize_shell_input_bindings_mode(
                    runtime_controls.get("shell_input_bindings_mode")
                ),
            }
            return {
                "auto_approve_commands": bool(runtime_controls.get("auto_approve_commands")),
                "agent_events_enabled": events_enabled,
                "agent_clarification_mode": clarification_mode,
                "llm_operator_final_response_mode": final_response_mode,
                "llm_operator_cardinality_judge_mode": normalize_cardinality_judge_mode(
                    runtime_controls.get("llm_operator_cardinality_judge_mode")
                ),
                "llm_operator_verification_enforced": bool(
                    runtime_controls.get("llm_operator_verification_enforced")
                ),
                "llm_operator_max_clarification_rounds": _coerce_runtime_int(
                    runtime_controls.get("llm_operator_max_clarification_rounds"),
                    3,
                    minimum=0,
                    maximum=10,
                ),
                **profile_values,
                "sql_agent_chat_route_mode": sql_agent_chat_route_mode,
                "operator_workspace_cwd_guard_enabled": bool(
                    runtime_controls.get("operator_workspace_cwd_guard_enabled")
                ),
                "llm_operator_verbose_enabled": bool(
                    runtime_controls.get("llm_operator_verbose_enabled")
                ),
                "llm_operator_step_validation_enabled": bool(
                    runtime_controls.get("llm_operator_step_validation_enabled")
                ),
                "agent_memory_enabled": bool(runtime_controls.get("agent_memory_enabled")),
                "agent_memory_prompt_max_chars": _coerce_runtime_int(
                    runtime_controls.get("agent_memory_prompt_max_chars"),
                    3000,
                    minimum=200,
                    maximum=20000,
                ),
                "agent_learning_ledger_auto_learn_enabled": bool(
                    settings.agent_learning_ledger_enabled
                    and runtime_controls.get("agent_learning_ledger_auto_learn_enabled")
                ),
                "agent_command_template_cache_similarity_threshold": _coerce_runtime_float(
                    runtime_controls.get(
                        "agent_command_template_cache_similarity_threshold"
                    ),
                    float(
                        getattr(
                            settings,
                            "agent_command_template_cache_similarity_threshold",
                            0.82,
                        )
                    ),
                    minimum=0.0,
                    maximum=1.0,
                ),
                "agent_command_template_cache_secondary_similarity_threshold": _coerce_runtime_float(
                    runtime_controls.get(
                        "agent_command_template_cache_secondary_similarity_threshold"
                    ),
                    float(
                        getattr(
                            settings,
                            "agent_command_template_cache_secondary_similarity_threshold",
                            0.15,
                        )
                    ),
                    minimum=0.0,
                    maximum=1.0,
                ),
                "lrnt_enabled": bool(runtime_controls.get("lrnt_enabled", True)),
                "lrnt_similarity_threshold": _coerce_runtime_float(
                    runtime_controls.get("lrnt_similarity_threshold"),
                    float(getattr(settings, "lrnt_similarity_threshold", 0.92)),
                    minimum=0.0,
                    maximum=1.0,
                ),
                "lrdirect_enabled": bool(runtime_controls.get("lrdirect_enabled", True)),
                "reliability_mode": reliability_mode,
                "reliability_verifier_enforced": bool(
                    runtime_controls.get("reliability_verifier_enforced")
                ),
                "reliability_max_recovery_probes": _coerce_runtime_int(
                    runtime_controls.get("reliability_max_recovery_probes"),
                    3,
                    minimum=0,
                    maximum=20,
                ),
                "reliability_max_autonomous_repair_attempts": _coerce_runtime_int(
                    runtime_controls.get("reliability_max_autonomous_repair_attempts"),
                    2,
                    minimum=0,
                    maximum=20,
                ),
                "reliability_weak_model_plan_action_cap": _coerce_runtime_int(
                    runtime_controls.get("reliability_weak_model_plan_action_cap"),
                    4,
                    minimum=1,
                    maximum=32,
                ),
                "reliability_approval_envelope_budget": _coerce_runtime_int(
                    runtime_controls.get("reliability_approval_envelope_budget"),
                    2,
                    minimum=0,
                    maximum=20,
                ),
                "ui_auto_immersive_min_width_px": _coerce_runtime_int(
                    runtime_controls.get("ui_auto_immersive_min_width_px"),
                    500,
                    minimum=0,
                    maximum=4000,
                ),
                **llm_endpoint,
                "llm_timeout_seconds": _coerce_runtime_int(
                    runtime_controls.get("llm_timeout_seconds"),
                    int(float(settings.llm_timeout_seconds)),
                    minimum=1,
                    maximum=1800,
                ),
                "llm_max_tokens": _coerce_runtime_int(
                    runtime_controls.get("llm_max_tokens"),
                    int(settings.llm_max_tokens),
                    minimum=0,
                    maximum=65536,
                ),
                "audio_transcriber_service_host": _normalize_audio_transcriber_host(
                    runtime_controls.get("audio_transcriber_service_host")
                ),
                "audio_transcriber_service_port": _coerce_runtime_int(
                    runtime_controls.get("audio_transcriber_service_port"),
                    AUDIO_TRANSCRIBER_DEFAULT_PORT,
                    minimum=1,
                    maximum=65535,
                ),
                "audio_transcriber_service_url": _audio_transcriber_service_url(
                    runtime_controls.get("audio_transcriber_service_host"),
                    runtime_controls.get("audio_transcriber_service_port"),
                ),
            }

    def _public_runtime_controls_snapshot() -> dict[str, Any]:
        snapshot = _runtime_controls_snapshot()
        return {key: snapshot.get(key) for key in sorted(PUBLIC_RUNTIME_CONTROL_KEYS) if key in snapshot}

    def _apply_backend_runtime_snapshot(context: dict[str, Any]) -> dict[str, Any]:
        explicit_request_controls = {
            key: context[key]
            for key in ("auto_approve_commands", "auto_approve_scope")
            if key in context
        }
        snapshot = _runtime_controls_snapshot()
        context.update(snapshot)
        context.update(explicit_request_controls)
        return snapshot

    def _apply_runtime_control_values(values: dict[str, Any]) -> dict[str, Any]:
        """Apply live runtime-control values and persist the restart-sensitive subset."""

        if not isinstance(values, dict) or not values:
            return _runtime_controls_snapshot()
        removed_keys = removed_public_settings_in(values)
        if removed_keys:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Removed settings are no longer accepted. Use the consolidated backend-owned profiles.",
                    "removed_keys": removed_keys,
                    "replacement_keys": [
                        "operator_policy_profile",
                        "reasoning_profile",
                        "repair_profile",
                        "workflow_execution_mode",
                        "response_streaming_enabled",
                    ],
                },
            )

        persisted_int_updates: dict[str, int] = {}
        persisted_float_updates: dict[str, float] = {}
        persisted_bool_updates: dict[str, bool] = {}
        persisted_string_updates: dict[str, str] = {}
        with runtime_controls_lock:
            if "auto_approve_commands" in values:
                runtime_controls["auto_approve_commands"] = bool(
                    values.get("auto_approve_commands")
                )
                persisted_bool_updates["auto_approve_commands"] = bool(
                    runtime_controls["auto_approve_commands"]
                )
            if "agent_events_enabled" in values:
                runtime_controls["agent_events_enabled"] = bool(
                    values.get("agent_events_enabled")
                )
                persisted_bool_updates["agent_events_enabled"] = bool(
                    runtime_controls["agent_events_enabled"]
                )
            if (
                "agent_clarification_mode" in values
            ):
                clarification_mode = normalize_agent_clarification_mode(
                    values.get("agent_clarification_mode"),
                )
                runtime_controls["agent_clarification_mode"] = clarification_mode
                persisted_string_updates["agent_clarification_mode"] = clarification_mode
            if "llm_operator_final_response_mode" in values:
                final_response_mode = str(
                    values.get("llm_operator_final_response_mode") or "detailed"
                ).strip().lower()
                runtime_controls["llm_operator_final_response_mode"] = (
                    final_response_mode
                    if final_response_mode in {"detailed", "simple"}
                    else "detailed"
                )
                persisted_string_updates["llm_operator_final_response_mode"] = str(
                    runtime_controls["llm_operator_final_response_mode"]
                )
            if "llm_operator_cardinality_judge_mode" in values:
                cardinality_mode = normalize_cardinality_judge_mode(
                    values.get("llm_operator_cardinality_judge_mode")
                )
                runtime_controls["llm_operator_cardinality_judge_mode"] = cardinality_mode
                persisted_string_updates["llm_operator_cardinality_judge_mode"] = cardinality_mode
            if "operator_policy_profile" in values:
                profile = normalize_operator_policy_profile(values.get("operator_policy_profile"))
                runtime_controls["operator_policy_profile"] = profile
                persisted_string_updates["operator_policy_profile"] = profile
            if "reasoning_profile" in values:
                profile = normalize_reasoning_profile(values.get("reasoning_profile"))
                runtime_controls["reasoning_profile"] = profile
                persisted_string_updates["reasoning_profile"] = profile
            if "repair_profile" in values:
                profile = normalize_repair_profile(values.get("repair_profile"))
                runtime_controls["repair_profile"] = profile
                persisted_string_updates["repair_profile"] = profile
            if "workflow_execution_mode" in values:
                mode = normalize_workflow_execution_mode(values.get("workflow_execution_mode"))
                runtime_controls["workflow_execution_mode"] = mode
                persisted_string_updates["workflow_execution_mode"] = mode
            if "response_streaming_enabled" in values:
                runtime_controls["response_streaming_enabled"] = bool(
                    values.get("response_streaming_enabled")
                )
                persisted_bool_updates["response_streaming_enabled"] = bool(
                    runtime_controls["response_streaming_enabled"]
                )
            if "prompt_rephrase_enabled" in values:
                runtime_controls["prompt_rephrase_enabled"] = bool(
                    values.get("prompt_rephrase_enabled")
                )
                persisted_bool_updates["prompt_rephrase_enabled"] = bool(
                    runtime_controls["prompt_rephrase_enabled"]
                )
            if "sql_agent_chat_route_mode" in values:
                sql_route_mode = (
                    str(values.get("sql_agent_chat_route_mode") or "").strip().lower()
                )
                if sql_route_mode in {"agentic", "direct"}:
                    runtime_controls["sql_agent_chat_route_mode"] = sql_route_mode
                    persisted_string_updates["sql_agent_chat_route_mode"] = str(
                        runtime_controls["sql_agent_chat_route_mode"]
                    )
            if "operator_workspace_cwd_guard_enabled" in values:
                runtime_controls["operator_workspace_cwd_guard_enabled"] = bool(
                    values.get("operator_workspace_cwd_guard_enabled")
                )
                persisted_bool_updates["operator_workspace_cwd_guard_enabled"] = bool(
                    runtime_controls["operator_workspace_cwd_guard_enabled"]
                )
            if "llm_operator_verbose_enabled" in values:
                runtime_controls["llm_operator_verbose_enabled"] = bool(
                    values.get("llm_operator_verbose_enabled")
                )
                persisted_bool_updates["llm_operator_verbose_enabled"] = bool(
                    runtime_controls["llm_operator_verbose_enabled"]
                )
            if "llm_operator_step_validation_enabled" in values:
                runtime_controls["llm_operator_step_validation_enabled"] = bool(
                    values.get("llm_operator_step_validation_enabled")
                )
                persisted_bool_updates["llm_operator_step_validation_enabled"] = bool(
                    runtime_controls["llm_operator_step_validation_enabled"]
                )
            if "llm_operator_verification_enforced" in values:
                runtime_controls["llm_operator_verification_enforced"] = bool(
                    values.get("llm_operator_verification_enforced")
                )
                persisted_bool_updates["llm_operator_verification_enforced"] = bool(
                    runtime_controls["llm_operator_verification_enforced"]
                )
            if "agent_memory_enabled" in values:
                runtime_controls["agent_memory_enabled"] = bool(
                    values.get("agent_memory_enabled")
                )
                persisted_bool_updates["agent_memory_enabled"] = bool(
                    runtime_controls["agent_memory_enabled"]
                )
            if "agent_learning_ledger_auto_learn_enabled" in values:
                runtime_controls["agent_learning_ledger_auto_learn_enabled"] = bool(
                    values.get("agent_learning_ledger_auto_learn_enabled")
                )
                persisted_bool_updates["agent_learning_ledger_auto_learn_enabled"] = bool(
                    runtime_controls["agent_learning_ledger_auto_learn_enabled"]
                )
            if "lrnt_enabled" in values:
                runtime_controls["lrnt_enabled"] = bool(values.get("lrnt_enabled"))
                persisted_bool_updates["lrnt_enabled"] = bool(runtime_controls["lrnt_enabled"])
            if "lrdirect_enabled" in values:
                runtime_controls["lrdirect_enabled"] = bool(values.get("lrdirect_enabled"))
                persisted_bool_updates["lrdirect_enabled"] = bool(
                    runtime_controls["lrdirect_enabled"]
                )
            if "reliability_mode" in values:
                runtime_controls["reliability_mode"] = _coerce_reliability_mode(
                    values.get("reliability_mode")
                )
                persisted_string_updates["reliability_mode"] = str(
                    runtime_controls["reliability_mode"]
                )
            if "reliability_verifier_enforced" in values:
                runtime_controls["reliability_verifier_enforced"] = bool(
                    values.get("reliability_verifier_enforced")
                )
                persisted_bool_updates["reliability_verifier_enforced"] = bool(
                    runtime_controls["reliability_verifier_enforced"]
                )
            if "audio_transcriber_service_host" in values:
                runtime_controls["audio_transcriber_service_host"] = (
                    _normalize_audio_transcriber_host(
                        values.get("audio_transcriber_service_host")
                    )
                )
                persisted_string_updates["audio_transcriber_service_host"] = str(
                    runtime_controls["audio_transcriber_service_host"]
                )
            if any(
                key in values
                for key in (
                    "llm_base_scheme",
                    "llm_base_host",
                    "llm_base_port",
                    "llm_base_path",
                    "llm_base_url",
                )
            ):
                endpoint_payload = {**runtime_controls, **values}
                if "llm_base_url" not in values or any(
                    key in values
                    for key in (
                        "llm_base_scheme",
                        "llm_base_host",
                        "llm_base_port",
                        "llm_base_path",
                    )
                ):
                    endpoint_payload.pop("llm_base_url", None)
                endpoint = _agent_llm_endpoint_context(
                    endpoint_payload,
                    settings,
                )
                runtime_controls.update(endpoint)
                persisted_string_updates["llm_base_scheme"] = str(endpoint["llm_base_scheme"])
                persisted_string_updates["llm_base_host"] = str(endpoint["llm_base_host"])
                persisted_int_updates["llm_base_port"] = int(endpoint["llm_base_port"])
                persisted_string_updates["llm_base_path"] = str(endpoint["llm_base_path"])
                persisted_string_updates["llm_base_url"] = str(endpoint["llm_base_url"])
            for key, (minimum, maximum) in RUNTIME_FLOAT_CONTROL_BOUNDS.items():
                if key in values:
                    runtime_controls[key] = _coerce_runtime_float(
                        values.get(key),
                        float(runtime_controls.get(key) or minimum),
                        minimum=minimum,
                        maximum=maximum,
                    )
                    persisted_float_updates[key] = float(runtime_controls[key])
            for key, (minimum, maximum) in RUNTIME_INT_CONTROL_BOUNDS.items():
                if key in values:
                    runtime_controls[key] = _coerce_runtime_int(
                        values.get(key),
                        int(runtime_controls.get(key) or 0),
                        minimum=minimum,
                        maximum=maximum,
                    )
                    persisted_int_updates[key] = int(runtime_controls[key])
            runtime_controls["audio_transcriber_service_url"] = _audio_transcriber_service_url(
                runtime_controls.get("audio_transcriber_service_host"),
                runtime_controls.get("audio_transcriber_service_port"),
            )
            settings.audio_transcriber_service_url = str(
                runtime_controls["audio_transcriber_service_url"]
            )
        if persisted_int_updates:
            ui_settings_store.set_namespace_values(
                RUNTIME_CONTROLS_SETTINGS_NAMESPACE,
                persisted_int_updates,
            )
        if persisted_float_updates:
            ui_settings_store.set_namespace_values(
                RUNTIME_CONTROLS_SETTINGS_NAMESPACE,
                persisted_float_updates,
            )
        if persisted_bool_updates:
            ui_settings_store.set_namespace_values(
                RUNTIME_CONTROLS_SETTINGS_NAMESPACE,
                persisted_bool_updates,
            )
        if persisted_string_updates:
            ui_settings_store.set_namespace_values(
                RUNTIME_CONTROLS_SETTINGS_NAMESPACE,
                persisted_string_updates,
            )
        return _runtime_controls_snapshot()

    def _events_enabled() -> bool:
        return bool(_runtime_controls_snapshot()["agent_events_enabled"])

    def _apply_runtime_repair_attempt_context(context: dict[str, Any]) -> dict[str, Any]:
        snapshot = _runtime_controls_snapshot()
        if "agent_learning_ledger_auto_learn_enabled" not in context:
            context["agent_learning_ledger_auto_learn_enabled"] = snapshot[
                "agent_learning_ledger_auto_learn_enabled"
            ]
        if "lrnt_enabled" not in context:
            context["lrnt_enabled"] = snapshot["lrnt_enabled"]
        if "lrdirect_enabled" not in context:
            context["lrdirect_enabled"] = snapshot["lrdirect_enabled"]
        for key in RUNTIME_FLOAT_CONTROL_BOUNDS:
            if key not in context:
                context[key] = snapshot[key]
        for key in RUNTIME_INT_CONTROL_BOUNDS:
            if key.startswith("reliability_") and key not in context:
                context[key] = snapshot[key]
        for key in ("reliability_mode", "reliability_verifier_enforced"):
            if key not in context:
                context[key] = snapshot[key]
        _apply_sql_agent_route_context(context, snapshot=snapshot)
        return context

    def _apply_sql_agent_route_context(
        context: dict[str, Any],
        *,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = snapshot or _runtime_controls_snapshot()
        sql_route_mode = str(context.get("sql_agent_chat_route_mode") or "").strip().lower()
        if sql_route_mode not in {"agentic", "direct"}:
            context["sql_agent_chat_route_mode"] = snapshot["sql_agent_chat_route_mode"]
        return context

    for _name in (
        '_submit_agent_request',
        '_normalize_prompt_history_items',
        '_prompt_history_items',
        '_record_prompt_history_item',
        '_command_allowlist_context',
        '_apply_command_allowlist_context',
        '_runtime_controls_snapshot',
        '_public_runtime_controls_snapshot',
        '_apply_backend_runtime_snapshot',
        '_apply_runtime_control_values',
        '_events_enabled',
        '_apply_runtime_repair_attempt_context',
        '_apply_sql_agent_route_context'
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
