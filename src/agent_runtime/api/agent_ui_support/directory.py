"""Directory, runtime-control, and static asset helpers for Agent UI."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.common import *

STATIC_DIR = Path(__file__).resolve().parents[1] / "static" / "agent_ui"
DIRECTORY_PRIMARY_PAGES: tuple[dict[str, Any], ...] = (
    {
        "path": "/directory",
        "title": "Directory",
        "summary": "Landing page for every available Agent runtime path.",
        "manual_links": (
            {"title": "Manual home", "doc_id": "manual-home"},
            {"title": "Route inventory", "doc_id": "generated-route-inventory"},
        ),
    },
    {
        "path": "/agent-ui",
        "title": "Agent UI",
        "summary": "Main chat, terminal, trace, settings drawer, and runtime control surface.",
        "manual_links": (
            {"title": "First run and Agent UI", "doc_id": "first-run-agent-ui"},
            {
                "title": "Command capsules and terminal gateway",
                "doc_id": "command-capsules-terminal-gateway",
            },
            {"title": "Audio dictation", "doc_id": "audio-dictation"},
        ),
    },
    {
        "path": "/agent-ui-client",
        "title": "Agent UI Client",
        "summary": "Client-facing chat and notification surface.",
        "manual_links": (
            {"title": "First run and Agent UI", "doc_id": "first-run-agent-ui"},
        ),
    },
    {
        "path": "/agent-ui-mobile",
        "title": "Mobile Agent UI",
        "summary": "Touch-first chat, task, monitor, notification, and parameter controls.",
        "manual_links": (
            {"title": "First run and Agent UI", "doc_id": "first-run-agent-ui"},
            {"title": "Audio dictation", "doc_id": "audio-dictation"},
        ),
    },
    {
        "path": "/settings",
        "title": "Mission Control",
        "summary": "Standalone settings registry and runtime preference editor.",
        "manual_links": (
            {"title": "Configuration and storage", "doc_id": "configuration-storage"},
            {"title": "Modes and approvals", "doc_id": "modes-approvals-clarifications"},
        ),
    },
    {
        "path": "/prompt-editor",
        "title": "Prompt Editor",
        "summary": "Edit prompt templates and reusable memory records.",
        "manual_links": (
            {
                "title": "Tasks, parameters, prompts, and learning",
                "doc_id": "tasks-parameters-prompts-learning",
            },
            {"title": "Configuration and storage", "doc_id": "configuration-storage"},
        ),
    },
    {
        "path": "/reliability",
        "title": "Reliability",
        "summary": "Recovery timelines, model capability profiles, and weak-model eval reports.",
        "manual_links": (
            {"title": "Capability reliability", "doc_id": "capability-reliability-kernel"},
            {"title": "Generated route inventory", "doc_id": "generated-route-inventory"},
        ),
    },
    {
        "path": "/learning-ledger",
        "title": "Learning Ledger",
        "summary": "Review run lessons, capability insights, and proposed capability changes.",
        "manual_links": (
            {
                "title": "Capability evolution and Learning Ledger",
                "doc_id": "capability-evolution-learning-ledger",
            },
            {"title": "Generated route inventory", "doc_id": "generated-route-inventory"},
        ),
    },
    {
        "path": "/website",
        "title": "Website",
        "summary": "Product website for technical evaluators and resource entry points.",
        "external": True,
        "manual_links": (
            {"title": "Manual home", "doc_id": "manual-home"},
            {"title": "System surfaces", "doc_id": "system-surfaces-api-reference"},
        ),
    },
    {
        "path": "/manual",
        "title": "Manual",
        "summary": "Searchable product manual and generated living references.",
        "external": True,
        "manual_links": (
            {"title": "Manual home", "doc_id": "manual-home"},
            {"title": "Generated route inventory", "doc_id": "generated-route-inventory"},
        ),
    },
    {
        "path": "/healthz",
        "title": "Health",
        "summary": "Runtime health check endpoint for service monitoring.",
        "manual_links": (
            {"title": "Generated route inventory", "doc_id": "generated-route-inventory"},
        ),
    },
)
DIRECTORY_PAGE_METADATA: dict[str, dict[str, Any]] = {
    str(page["path"]): dict(page) for page in DIRECTORY_PRIMARY_PAGES
}
DIRECTORY_ROUTE_GROUP_LABELS = {
    "api": "Agent API",
    "compatibility": "Compatibility",
    "openapi": "OpenAPI",
    "websocket": "WebSocket",
    "utility": "Utility",
}
DIRECTORY_ROUTE_GROUP_ORDER = {
    "api": 10,
    "compatibility": 20,
    "openapi": 30,
    "websocket": 40,
    "utility": 50,
}
RUNTIME_CONTROLS_SETTINGS_NAMESPACE = "runtime_controls"
AGENT_UI_SETTINGS_NAMESPACE = "agent_settings"
AGENT_UI_SETTINGS_META_NAMESPACE = "agent_settings_meta"
PROMPT_HISTORY_SETTINGS_NAMESPACE = "prompt_history"
PROMPT_HISTORY_MAX_ITEMS = 100
RUNTIME_FLOAT_CONTROL_BOUNDS: dict[str, tuple[float, float]] = {
    "agent_command_template_cache_similarity_threshold": (0.0, 1.0),
    "agent_command_template_cache_secondary_similarity_threshold": (0.0, 1.0),
    "lrnt_similarity_threshold": (0.0, 1.0),
}
RUNTIME_INT_CONTROL_BOUNDS = {
    "ui_auto_immersive_min_width_px": (0, 4000),
    "audio_transcriber_service_port": (1, 65535),
    "llm_operator_max_clarification_rounds": (0, 10),
    "agent_memory_prompt_max_chars": (200, 20000),
    "llm_base_port": (1, 65535),
    "llm_timeout_seconds": (1, 1800),
    "llm_max_tokens": (0, 65536),
    "reliability_max_recovery_probes": (0, 20),
    "reliability_max_autonomous_repair_attempts": (0, 20),
    "reliability_weak_model_plan_action_cap": (1, 32),
    "reliability_approval_envelope_budget": (0, 20),
}
RUNTIME_PERSISTED_BOOL_KEYS = (
    "auto_approve_commands",
    "agent_events_enabled",
    "prompt_rephrase_enabled",
    "response_streaming_enabled",
    "operator_workspace_cwd_guard_enabled",
    "llm_operator_verbose_enabled",
    "llm_operator_step_validation_enabled",
    "llm_operator_verification_enforced",
    "agent_memory_enabled",
    "agent_learning_ledger_auto_learn_enabled",
    "lrnt_enabled",
    "lrdirect_enabled",
    "reliability_verifier_enforced",
)
AUDIO_TRANSCRIBER_DEFAULT_HOST = "localhost"
AUDIO_TRANSCRIBER_DEFAULT_PORT = 8012


def _audio_transcriber_endpoint_parts(service_url: str | None) -> tuple[str, int]:
    """Return editable host/port parts for the audio runtime service URL."""

    value = str(service_url or "").strip() or f"http://{AUDIO_TRANSCRIBER_DEFAULT_HOST}:{AUDIO_TRANSCRIBER_DEFAULT_PORT}"
    parsed = urlsplit(value if "://" in value else f"http://{value}")
    host = str(parsed.hostname or AUDIO_TRANSCRIBER_DEFAULT_HOST).strip() or AUDIO_TRANSCRIBER_DEFAULT_HOST
    port = parsed.port or AUDIO_TRANSCRIBER_DEFAULT_PORT
    if not 1 <= int(port) <= 65535:
        port = AUDIO_TRANSCRIBER_DEFAULT_PORT
    return host, int(port)


def _normalize_audio_transcriber_host(value: Any) -> str:
    """Normalize a user-provided audio endpoint host or URL."""

    raw = str(value or "").strip()
    if not raw:
        return AUDIO_TRANSCRIBER_DEFAULT_HOST
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    host = str(parsed.hostname or raw).strip().strip("/")
    return host or AUDIO_TRANSCRIBER_DEFAULT_HOST


def _audio_transcriber_service_url(host: Any, port: Any) -> str:
    """Build the local audio runtime service URL from persisted UI controls."""

    safe_host = _normalize_audio_transcriber_host(host)
    try:
        safe_port = int(port)
    except (TypeError, ValueError):
        safe_port = AUDIO_TRANSCRIBER_DEFAULT_PORT
    if not 1 <= safe_port <= 65535:
        safe_port = AUDIO_TRANSCRIBER_DEFAULT_PORT
    return f"http://{safe_host}:{safe_port}"
SCHEDULED_EVENT_MACRO_REF_CONTEXT_KEY = "_scheduled_event_user_macro_ref"
SCHEDULED_EVENT_EXTRACTION_HINT_CONTEXT_KEY = "_scheduled_event_extraction_hint"
SCHEDULED_EVENT_ABSOLUTE_AT_CONTEXT_KEY = "_scheduled_event_absolute_at"
SCHEDULED_EVENT_SCHEDULE_SUMMARY_CONTEXT_KEY = "_scheduled_event_schedule_summary"


def _coerce_runtime_repair_attempt(value: Any, default: int) -> int:
    try:
        return max(0, min(10, int(value)))
    except (TypeError, ValueError):
        return max(0, min(10, int(default)))


def _coerce_runtime_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(minimum, min(maximum, number))


def _coerce_runtime_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(minimum, min(maximum, number))


def _coerce_operator_policy_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"deterministic", "llm"} else "deterministic"


def _coerce_reliability_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"off", "standard", "aggressive"} else "aggressive"


def _directory_manual_port() -> str:
    raw_port = str(os.getenv("AOR_MANUAL_PORT", "8013") or "8013").strip()
    try:
        port = int(raw_port)
    except ValueError:
        return "8013"
    return str(port) if 1 <= port <= 65535 else "8013"


def _directory_website_port() -> str:
    raw_port = str(os.getenv("AOR_WEBSITE_PORT", "8014") or "8014").strip()
    try:
        port = int(raw_port)
    except ValueError:
        return "8014"
    return str(port) if 1 <= port <= 65535 else "8014"


def _directory_title_from_path(path: str) -> str:
    clean_path = str(path or "/").strip() or "/"
    if clean_path == "/":
        return "Default Landing"
    parts = [
        part.replace("_", " ").replace("-", " ").title()
        for part in clean_path.strip("/").split("/")
        if part and not part.startswith("{")
    ]
    return " ".join(parts) or clean_path


def _directory_doc_summary(endpoint: Any) -> str:
    docstring = str(getattr(endpoint, "__doc__", "") or "").strip()
    if not docstring:
        return ""
    for line in docstring.splitlines():
        summary = line.strip()
        if summary:
            return summary
    return ""


def _directory_route_methods(route: Any) -> list[str]:
    methods = getattr(route, "methods", None)
    if methods:
        return sorted(str(method) for method in methods if method not in {"HEAD", "OPTIONS"})
    if route.__class__.__name__ == "WebSocketRoute":
        return ["WEBSOCKET"]
    return []


def _directory_route_group(path: str, methods: list[str]) -> str:
    if "WEBSOCKET" in methods:
        return "websocket"
    if path in {"/openapi.json", "/docs", "/redoc"} or path.startswith("/docs/"):
        return "openapi"
    if path.startswith("/api/"):
        return "api"
    if path == "/compile" or path.startswith("/sessions") or path.startswith("/runs"):
        return "compatibility"
    return "utility"


def _directory_include_route(route: Any) -> bool:
    path = str(getattr(route, "path_format", None) or getattr(route, "path", "") or "")
    if not path:
        return False
    if route.__class__.__name__ == "Mount" and "/static" in path:
        return False
    return not path.startswith("/agent-ui/static")


def _directory_route_payload(route: Any) -> dict[str, Any]:
    path = str(getattr(route, "path_format", None) or getattr(route, "path", "") or "")
    methods = _directory_route_methods(route)
    metadata = DIRECTORY_PAGE_METADATA.get(path, {})
    endpoint = getattr(route, "endpoint", None)
    group = _directory_route_group(path, methods)
    summary = str(metadata.get("summary") or _directory_doc_summary(endpoint) or "").strip()
    if not summary:
        summary = f"Runtime path for {_directory_title_from_path(path).lower()}."
    return {
        "path": path,
        "methods": methods,
        "title": str(metadata.get("title") or _directory_title_from_path(path)),
        "summary": summary,
        "group": group,
        "group_label": DIRECTORY_ROUTE_GROUP_LABELS.get(group, group.title()),
        "endpoint": str(getattr(route, "name", "") or getattr(endpoint, "__name__", "") or ""),
        "manual_links": [dict(item) for item in metadata.get("manual_links", ())],
    }


def _directory_payload(app: FastAPI) -> dict[str, Any]:
    routes = [
        _directory_route_payload(route)
        for route in app.routes
        if _directory_include_route(route)
    ]
    routes.sort(key=lambda item: (DIRECTORY_ROUTE_GROUP_ORDER.get(item["group"], 90), item["path"]))
    route_by_path = {str(route["path"]): route for route in routes}
    pages: list[dict[str, Any]] = []
    for page in DIRECTORY_PRIMARY_PAGES:
        path = str(page["path"])
        route = route_by_path.get(path)
        pages.append(
            {
                **dict(page),
                "methods": list(route.get("methods", [])) if route else [],
                "route_present": bool(route),
                "manual_links": [dict(item) for item in page.get("manual_links", ())],
            }
        )

    groups: list[dict[str, Any]] = []
    for group, label in DIRECTORY_ROUTE_GROUP_LABELS.items():
        group_routes = [route for route in routes if route["group"] == group]
        if group_routes:
            groups.append(
                {
                    "group": group,
                    "label": label,
                    "routes": group_routes,
                }
            )

    return {
        "pages": pages,
        "routes": routes,
        "route_groups": groups,
        "manual": {
            "port": _directory_manual_port(),
            "path": "/manual",
            "home_doc_id": "manual-home",
        },
        "website": {
            "port": _directory_website_port(),
            "path": "/website",
        },
    }

__all__ = [name for name in globals() if not name.startswith("__")]
