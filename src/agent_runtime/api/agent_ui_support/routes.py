"""FastAPI route registration coordinator for the local Agent UI."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared import *
from agent_runtime.api.agent_ui_support.route_groups.core import register_core_routes
from agent_runtime.api.agent_ui_support.route_groups.settings_prompt_editor import register_settings_prompt_editor_routes
from agent_runtime.api.agent_ui_support.route_groups.learning_reliability import register_learning_reliability_routes
from agent_runtime.api.agent_ui_support.route_groups.events_notifications import register_events_notifications_routes
from agent_runtime.api.agent_ui_support.route_groups.tasks_monitors import register_tasks_monitors_routes
from agent_runtime.api.agent_ui_support.route_groups.parameters_memory import register_parameters_memory_routes
from agent_runtime.api.agent_ui_support.route_groups.step_corrections import register_step_correction_routes
from agent_runtime.api.agent_ui_support.route_groups.gateway_terminal_llm import register_gateway_terminal_llm_routes
from agent_runtime.api.agent_ui_support.route_groups.request_lifecycle import register_request_lifecycle_routes
from agent_runtime.api.agent_ui_support.route_groups.integrations import register_integration_routes

def register_agent_ui_routes(
    app: FastAPI,
    *,
    settings: Settings,
    agent_runtime: Any,
    store: AgentTraceStore | None = None,
) -> AgentTraceStore:
    """Register local agent UI routes on the provided FastAPI app."""

    trace_store = store or AgentTraceStore()
    prompt_fetcher = configure_prompt_fetcher(settings.agent_prompts_db_path)
    prompt_template_store = prompt_fetcher.store or PromptTemplateStore(settings.agent_prompts_db_path)
    state_store = AgentUiRequestStateStore(max_requests=trace_store.max_requests)
    conversation_store = AgentConversationStore(settings.agent_chats_db_path)
    terminal_store = AgentTerminalSessionStore(max_sessions=trace_store.max_requests)
    gateway_store = AgentGatewayStore(settings.agent_gateways_db_path)
    task_store = AgentTaskStore(settings.agent_tasks_db_path)
    task_store.interrupt_active_tasks()
    monitor_store = AgentMonitorStore(settings.agent_monitors_db_path)
    monitor_store.interrupt_active_monitors()
    event_store = AgentEventStore(
        settings.agent_events_db_path,
        max_run_history=settings.agent_events_max_run_history,
    )
    command_allowlist_store = OperatorCommandAllowlistStore(settings.agent_command_allowlist_db_path)
    ui_settings_store = AgentUiSettingsStore(settings.agent_ui_settings_db_path)
    audio_service_host, audio_service_port = _audio_transcriber_endpoint_parts(
        settings.audio_transcriber_service_url
    )
    learning_ledger_store = (
        AgentLearningLedgerStore(settings.agent_learning_ledger_db_path)
        if bool(getattr(settings, "agent_learning_ledger_enabled", True))
        else None
    )
    reliability_store = _reliability_store(settings, agent_runtime)
    if learning_ledger_store is not None:
        try:
            setattr(agent_runtime, "learning_ledger_store", learning_ledger_store)
        except Exception:
            pass
    try:
        setattr(agent_runtime, "reliability_store", reliability_store)
    except Exception:
        pass
    if bool(getattr(settings, "agent_parameter_store_enabled", True)):
        try:
            setattr(agent_runtime, "parameter_store", _parameter_store_or_error(settings, agent_runtime))
        except HTTPException:
            pass
        except Exception:
            pass
    default_gateway_node = str(settings.resolved_default_node() or "localhost").strip() or "localhost"
    try:
        default_gateway_url = settings.resolve_gateway_url(default_gateway_node)
    except ValueError:
        default_gateway_url = "http://127.0.0.1:8787" if default_gateway_node == "localhost" else None
    gateway_store.seed_from_config(
        default_node=default_gateway_node,
        gateway_url=default_gateway_url,
        gateway_endpoints=dict(settings.gateway_endpoints),
        terminal_cwd=_gateway_terminal_cwd(settings, None),
    )
    cancel_events: dict[str, threading.Event] = {}
    cancel_events_lock = threading.RLock()
    task_queue_lock = threading.RLock()
    active_non_task_requests_lock = threading.RLock()
    monitored_task_requests: set[str] = set()
    active_non_task_request_ids: set[str] = set()
    llm_endpoint_defaults = _agent_llm_endpoint_defaults(settings)
    runtime_controls: dict[str, Any] = {
        "auto_approve_commands": False,
        "agent_events_enabled": bool(settings.agent_events_enabled),
        "agent_clarification_mode": normalize_agent_clarification_mode(
            settings.agent_clarification_mode,
        ),
        "llm_operator_final_response_mode": (
            str(getattr(settings, "llm_operator_final_response_mode", "simple") or "simple")
            .strip()
            .lower()
            if str(getattr(settings, "llm_operator_final_response_mode", "") or "")
            .strip()
            .lower()
            in {"detailed", "simple"}
            else "simple"
        ),
        "llm_operator_cardinality_judge_mode": normalize_cardinality_judge_mode(
            getattr(settings, "llm_operator_cardinality_judge_mode", "auto")
        ),
        "llm_operator_verification_enforced": bool(
            getattr(settings, "llm_operator_verification_enforced", True)
        ),
        "llm_operator_max_clarification_rounds": int(
            getattr(settings, "llm_operator_max_clarification_rounds", 3)
        ),
        "operator_policy_profile": normalize_operator_policy_profile(
            getattr(settings, "operator_policy_profile", "assisted")
        ),
        "reasoning_profile": normalize_reasoning_profile(
            getattr(settings, "reasoning_profile", "balanced")
        ),
        "repair_profile": normalize_repair_profile(
            getattr(settings, "repair_profile", "balanced")
        ),
        "workflow_execution_mode": normalize_workflow_execution_mode(
            getattr(settings, "workflow_execution_mode", "streaming")
        ),
        "prompt_rephrase_enabled": bool(
            getattr(settings, "prompt_rephrase_enabled", True)
        ),
        "response_streaming_enabled": bool(
            getattr(settings, "response_streaming_enabled", True)
        ),
        "shell_input_bindings_mode": normalize_shell_input_bindings_mode(
            getattr(settings, "shell_input_bindings_mode", "allow")
        ),
        "sql_agent_chat_route_mode": str(settings.sql_agent_chat_route_mode or "agentic").strip().lower()
        if str(settings.sql_agent_chat_route_mode or "").strip().lower() in {"agentic", "direct"}
        else "agentic",
        "operator_workspace_cwd_guard_enabled": bool(settings.operator_workspace_cwd_guard_enabled),
        "llm_operator_verbose_enabled": bool(settings.llm_operator_verbose_enabled),
        "llm_operator_step_validation_enabled": bool(
            settings.llm_operator_step_validation_enabled
        ),
        "agent_memory_enabled": bool(getattr(settings, "agent_memory_enabled", True)),
        "agent_memory_prompt_max_chars": int(
            getattr(settings, "agent_memory_prompt_max_chars", 3000)
        ),
        "agent_learning_ledger_auto_learn_enabled": bool(
            settings.agent_learning_ledger_auto_learn_enabled
        ),
        "agent_command_template_cache_similarity_threshold": float(
            getattr(settings, "agent_command_template_cache_similarity_threshold", 0.82)
        ),
        "agent_command_template_cache_secondary_similarity_threshold": float(
            getattr(
                settings,
                "agent_command_template_cache_secondary_similarity_threshold",
                0.15,
            )
        ),
        "lrnt_enabled": bool(getattr(settings, "lrnt_enabled", True)),
        "lrnt_similarity_threshold": float(
            getattr(settings, "lrnt_similarity_threshold", 0.92)
        ),
        "lrdirect_enabled": bool(getattr(settings, "lrdirect_enabled", True)),
        "reliability_mode": _coerce_reliability_mode(settings.reliability_mode),
        "reliability_verifier_enforced": bool(settings.reliability_verifier_enforced),
        "reliability_max_recovery_probes": int(settings.reliability_max_recovery_probes),
        "reliability_max_autonomous_repair_attempts": int(
            settings.reliability_max_autonomous_repair_attempts
        ),
        "reliability_weak_model_plan_action_cap": int(
            settings.reliability_weak_model_plan_action_cap
        ),
        "reliability_approval_envelope_budget": int(
            settings.reliability_approval_envelope_budget
        ),
        "ui_auto_immersive_min_width_px": int(
            settings.agent_ui_auto_immersive_min_width_px
        ),
        **llm_endpoint_defaults,
        "llm_timeout_seconds": int(float(settings.llm_timeout_seconds)),
        "llm_max_tokens": int(settings.llm_max_tokens),
        "audio_transcriber_service_host": audio_service_host,
        "audio_transcriber_service_port": audio_service_port,
        "audio_transcriber_service_url": _audio_transcriber_service_url(
            audio_service_host,
            audio_service_port,
        ),
    }
    persisted_runtime_controls = {
        key: value
        for key, value in ui_settings_store.get_namespace(
            RUNTIME_CONTROLS_SETTINGS_NAMESPACE
        ).items()
        if key in PUBLIC_RUNTIME_CONTROL_KEYS
    }
    for key, (minimum, maximum) in RUNTIME_INT_CONTROL_BOUNDS.items():
        if key in persisted_runtime_controls:
            runtime_controls[key] = _coerce_runtime_int(
                persisted_runtime_controls.get(key),
                int(runtime_controls.get(key) or 0),
                minimum=minimum,
                maximum=maximum,
            )
    for key, (minimum, maximum) in RUNTIME_FLOAT_CONTROL_BOUNDS.items():
        if key in persisted_runtime_controls:
            runtime_controls[key] = _coerce_runtime_float(
                persisted_runtime_controls.get(key),
                float(runtime_controls.get(key) or minimum),
                minimum=minimum,
                maximum=maximum,
            )
    for key in RUNTIME_PERSISTED_BOOL_KEYS:
        if key in persisted_runtime_controls:
            runtime_controls[key] = bool(persisted_runtime_controls.get(key))
    if "llm_operator_final_response_mode" in persisted_runtime_controls:
        final_response_mode = str(
            persisted_runtime_controls.get("llm_operator_final_response_mode")
            or "simple"
        ).strip().lower()
        runtime_controls["llm_operator_final_response_mode"] = (
            final_response_mode
            if final_response_mode in {"detailed", "simple"}
            else "simple"
        )
    if "llm_operator_cardinality_judge_mode" in persisted_runtime_controls:
        runtime_controls["llm_operator_cardinality_judge_mode"] = normalize_cardinality_judge_mode(
            persisted_runtime_controls.get("llm_operator_cardinality_judge_mode")
        )
    for key, normalizer in (
        ("operator_policy_profile", normalize_operator_policy_profile),
        ("reasoning_profile", normalize_reasoning_profile),
        ("repair_profile", normalize_repair_profile),
        ("workflow_execution_mode", normalize_workflow_execution_mode),
    ):
        if key in persisted_runtime_controls:
            runtime_controls[key] = normalizer(persisted_runtime_controls.get(key))
    if "response_streaming_enabled" in persisted_runtime_controls:
        runtime_controls["response_streaming_enabled"] = bool(
            persisted_runtime_controls.get("response_streaming_enabled")
        )
    if "agent_clarification_mode" in persisted_runtime_controls:
        runtime_controls["agent_clarification_mode"] = normalize_agent_clarification_mode(
            persisted_runtime_controls.get("agent_clarification_mode"),
        )
    if "reliability_mode" in persisted_runtime_controls:
        runtime_controls["reliability_mode"] = _coerce_reliability_mode(
            persisted_runtime_controls.get("reliability_mode")
        )
    if "sql_agent_chat_route_mode" in persisted_runtime_controls:
        sql_route_mode = str(
            persisted_runtime_controls.get("sql_agent_chat_route_mode") or "agentic"
        ).strip().lower()
        runtime_controls["sql_agent_chat_route_mode"] = (
            sql_route_mode if sql_route_mode in {"agentic", "direct"} else "agentic"
        )
    if "audio_transcriber_service_host" in persisted_runtime_controls:
        runtime_controls["audio_transcriber_service_host"] = _normalize_audio_transcriber_host(
            persisted_runtime_controls.get("audio_transcriber_service_host")
        )
    if any(
        key in persisted_runtime_controls
        for key in (
            "llm_base_scheme",
            "llm_base_host",
            "llm_base_port",
            "llm_base_path",
            "llm_base_url",
        )
    ):
        endpoint_payload = {**runtime_controls, **persisted_runtime_controls}
        if "llm_base_url" not in persisted_runtime_controls or any(
            key in persisted_runtime_controls
            for key in (
                "llm_base_scheme",
                "llm_base_host",
                "llm_base_port",
                "llm_base_path",
            )
        ):
            endpoint_payload.pop("llm_base_url", None)
        runtime_controls.update(
            _agent_llm_endpoint_context(
                endpoint_payload,
                settings,
            )
        )
    runtime_controls["audio_transcriber_service_url"] = _audio_transcriber_service_url(
        runtime_controls.get("audio_transcriber_service_host"),
        runtime_controls.get("audio_transcriber_service_port"),
    )
    missing_persisted_runtime_controls = {
        key: runtime_controls[key]
        for key in sorted(PUBLIC_RUNTIME_CONTROL_KEYS)
        if key not in persisted_runtime_controls and key in runtime_controls
    }
    if missing_persisted_runtime_controls:
        ui_settings_store.set_namespace_values(
            RUNTIME_CONTROLS_SETTINGS_NAMESPACE,
            missing_persisted_runtime_controls,
        )
    settings.audio_transcriber_service_url = str(runtime_controls["audio_transcriber_service_url"])
    runtime_controls_lock = threading.RLock()
    app.state.agent_trace_store = trace_store
    app.state.agent_conversation_store = conversation_store
    app.state.agent_terminal_store = terminal_store
    app.state.agent_gateway_store = gateway_store
    app.state.agent_event_store = event_store
    app.state.agent_task_store = task_store
    app.state.agent_monitor_store = monitor_store
    app.state.agent_command_allowlist_store = command_allowlist_store
    app.state.agent_reliability_store = reliability_store
    app.state.agent_ui_settings_store = ui_settings_store
    app.state.agent_runtime_controls = runtime_controls
    pending_event_macro_contexts: dict[str, dict[str, Any]] = {}
    pending_event_macro_contexts_lock = threading.RLock()
    app.state.pending_event_macro_contexts = pending_event_macro_contexts
    ctx = AgentUiRouteContext(
        app=app,
        settings=settings,
        agent_runtime=agent_runtime,
        trace_store=trace_store,
        prompt_fetcher=prompt_fetcher,
        prompt_template_store=prompt_template_store,
        state_store=state_store,
        conversation_store=conversation_store,
        terminal_store=terminal_store,
        gateway_store=gateway_store,
        task_store=task_store,
        monitor_store=monitor_store,
        event_store=event_store,
        command_allowlist_store=command_allowlist_store,
        ui_settings_store=ui_settings_store,
        learning_ledger_store=learning_ledger_store,
        reliability_store=reliability_store,
        cancel_events=cancel_events,
        cancel_events_lock=cancel_events_lock,
        task_queue_lock=task_queue_lock,
        active_non_task_requests_lock=active_non_task_requests_lock,
        monitored_task_requests=monitored_task_requests,
        active_non_task_request_ids=active_non_task_request_ids,
        llm_endpoint_defaults=llm_endpoint_defaults,
        runtime_controls=runtime_controls,
        runtime_controls_lock=runtime_controls_lock,
        pending_event_macro_contexts=pending_event_macro_contexts,
        pending_event_macro_contexts_lock=pending_event_macro_contexts_lock,
    )
    ctx.default_gateway_node = default_gateway_node
    install_shared_route_helpers(ctx)
    register_core_routes(ctx)
    register_settings_prompt_editor_routes(ctx)
    register_learning_reliability_routes(ctx)
    register_events_notifications_routes(ctx)
    register_tasks_monitors_routes(ctx)
    register_parameters_memory_routes(ctx)
    register_step_correction_routes(ctx)
    register_gateway_terminal_llm_routes(ctx)
    register_request_lifecycle_routes(ctx)
    register_integration_routes(ctx)
    return trace_store


__all__ = ["register_agent_ui_routes"]
