"""Shared context and helper installer facade for Agent UI route groups."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared_imports import *
from agent_runtime.api.agent_ui_support.route_groups.runtime_shared import install_runtime_helpers
from agent_runtime.api.agent_ui_support.route_groups.terminal_shared import install_terminal_helpers
from agent_runtime.api.agent_ui_support.route_groups.reliability_shared import install_reliability_helpers
from agent_runtime.api.agent_ui_support.route_groups.event_shared import install_event_helpers
from agent_runtime.api.agent_ui_support.route_groups.task_monitor_shared import install_task_monitor_helpers
from agent_runtime.api.agent_ui_support.route_groups.scheduled_event_shared import install_scheduled_event_helpers


@dataclass
class AgentUiRouteContext:
    """Mutable state shared by Agent UI route group registrars."""

    app: Any
    settings: Any
    agent_runtime: Any
    trace_store: Any
    prompt_fetcher: Any
    prompt_template_store: Any
    state_store: Any
    conversation_store: Any
    terminal_store: Any
    gateway_store: Any
    task_store: Any
    monitor_store: Any
    event_store: Any
    command_allowlist_store: Any
    ui_settings_store: Any
    learning_ledger_store: Any
    reliability_store: Any
    cancel_events: Any
    cancel_events_lock: Any
    task_queue_lock: Any
    active_non_task_requests_lock: Any
    monitored_task_requests: Any
    active_non_task_request_ids: Any
    llm_endpoint_defaults: Any
    runtime_controls: Any
    runtime_controls_lock: Any
    pending_event_macro_contexts: Any
    pending_event_macro_contexts_lock: Any
    default_gateway_node: Any = None
    monitor_manager: Any = None


def install_shared_route_helpers(ctx: AgentUiRouteContext) -> None:
    """Install helper clusters shared by Agent UI route groups."""

    install_runtime_helpers(ctx)
    install_terminal_helpers(ctx)
    install_reliability_helpers(ctx)
    install_event_helpers(ctx)
    install_task_monitor_helpers(ctx)
    install_scheduled_event_helpers(ctx)
    if STATIC_DIR.exists():
        ctx.app.mount(
            "/agent-ui/static",
            StaticFiles(directory=str(STATIC_DIR)),
            name="agent-ui-static",
        )


__all__ = [name for name in globals() if not name.startswith("__")]
