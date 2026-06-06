"""Trace lookup, gateway routing, terminal, and request-context helpers for Agent UI."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.common import *
from agent_runtime.api.agent_ui_support.directory import *
from agent_runtime.api.agent_ui_support.models import *
from agent_runtime.api.agent_ui_support.prompt_context import *
from agent_runtime.api.agent_ui_support.memory import *
from agent_runtime.api.agent_ui_support.events import *
from agent_runtime.api.agent_ui_support.memory_drafts import *

def _trace_or_404(store: AgentTraceStore, request_id: str) -> AgentRequestTrace:
    trace = store.get_trace(request_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


def _trace_payload_from_chat_turn(turn: Any) -> dict[str, Any]:
    """Return a trace-shaped payload from durable chat history."""

    status = str(getattr(turn, "status", "") or "completed").strip()
    if status not in {"pending", "running", "completed", "failed", "cancelled"}:
        status = "completed"
    return {
        "request_id": str(getattr(turn, "request_id", "") or ""),
        "prompt": str(getattr(turn, "prompt", "") or ""),
        "created_at": str(getattr(turn, "created_at", "") or ""),
        "updated_at": str(getattr(turn, "updated_at", "") or ""),
        "status": status,
        "final_response": str(getattr(turn, "final_response", "") or ""),
        "display_document": getattr(turn, "display_document", None),
        "confirmation_required": bool(getattr(turn, "confirmation_required", False)),
        "confirmation_actions": [],
        "clarification_required": bool(getattr(turn, "clarification_required", False)),
        "clarification_request": None,
        "raw_payloads": {},
        "response_metrics": getattr(turn, "response_metrics", None),
        "learning_summary": getattr(turn, "learning_summary", None),
        "error": None,
        "events": [],
        "trace_source": "chat_history",
    }


def _gateway_base_url(raw_url: str | None) -> str | None:
    raw = str(raw_url or "").strip().rstrip("/")
    if not raw:
        return None
    for suffix in ("/exec/stream", "/exec/cancel", "/exec"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)].rstrip("/")
    return raw


def _gateway_ws_url(raw_url: str) -> str:
    parts = urlsplit(raw_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, "/terminal/ws", "", ""))


def _agent_ui_ws_url(request: Request, path: str, query: str = "") -> str:
    parts = urlsplit(str(request.base_url))
    scheme = "wss" if parts.scheme == "https" else "ws"
    root_path = str(request.scope.get("root_path") or "").rstrip("/")
    normalized_path = "/" + str(path or "").lstrip("/")
    return urlunsplit((scheme, parts.netloc, f"{root_path}{normalized_path}", query, ""))


def _resolve_terminal_gateway(settings: Settings) -> tuple[str, str]:
    node = str(settings.resolved_default_node() or "localhost").strip() or "localhost"
    try:
        raw_url = settings.resolve_gateway_url(node)
    except ValueError:
        raw_url = "http://127.0.0.1:8787" if node == "localhost" else ""
    base_url = _gateway_base_url(raw_url)
    if not base_url:
        raise HTTPException(status_code=503, detail=f"Gateway URL is not configured for node: {node}.")
    return node, base_url


def _gateway_context_from_record(record: Any) -> dict[str, Any]:
    node = str(getattr(record, "node", "") or "").strip()
    base_url = _gateway_base_url(str(getattr(record, "base_url", "") or ""))
    if not node or not base_url:
        return {}
    context = {
        "gateway_id": str(getattr(record, "gateway_id", "") or "").strip(),
        "gateway_node": node,
        "gateway_url": base_url,
        "gateway_endpoints": {node: base_url},
    }
    terminal_cwd = str(getattr(record, "terminal_cwd", "") or "").strip()
    if terminal_cwd:
        context["gateway_default_cwd"] = terminal_cwd
    platform_fields = {
        "gateway_platform": str(getattr(record, "platform", "") or "").strip(),
        "gateway_platform_label": str(getattr(record, "platform_label", "") or "").strip(),
        "gateway_platform_version": str(getattr(record, "platform_version", "") or "").strip(),
        "gateway_architecture": str(getattr(record, "architecture", "") or "").strip(),
        "gateway_shell": str(getattr(record, "shell", "") or "").strip(),
        "gateway_command_profile": str(getattr(record, "command_profile", "") or "").strip(),
    }
    context.update({key: value for key, value in platform_fields.items() if value})
    capability_tags = getattr(record, "capability_tags", []) or []
    if isinstance(capability_tags, list) and capability_tags:
        context["gateway_capability_tags"] = [str(tag) for tag in capability_tags if str(tag).strip()]
    return context


def _gateway_display_label(record: Any) -> str:
    label = str(getattr(record, "label", "") or "").strip()
    node = str(getattr(record, "node", "") or "").strip()
    if label and node and node != label:
        return f"{label} ({node})"
    return label or node or str(getattr(record, "gateway_id", "") or "gateway")


def _gateway_routing_metadata(
    mode: str,
    record: Any | None = None,
    *,
    matched_text: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"mode": str(mode or "default").strip() or "default"}
    if record is not None:
        payload.update(
            {
                "gateway_id": str(getattr(record, "gateway_id", "") or "").strip(),
                "gateway_nickname": str(getattr(record, "label", "") or "").strip(),
                "gateway_node": str(getattr(record, "node", "") or "").strip(),
                "gateway_display_name": _gateway_display_label(record),
                "gateway_platform": str(getattr(record, "platform", "") or "").strip(),
                "gateway_platform_label": str(getattr(record, "platform_label", "") or "").strip(),
                "gateway_platform_version": str(getattr(record, "platform_version", "") or "").strip(),
                "gateway_shell": str(getattr(record, "shell", "") or "").strip(),
                "gateway_command_profile": str(getattr(record, "command_profile", "") or "").strip(),
            }
        )
    if matched_text:
        payload["matched_text"] = matched_text
    return {key: value for key, value in payload.items() if value != ""}


def _gateway_nickname_pattern(nickname: str) -> re.Pattern[str] | None:
    parts = [part for part in str(nickname or "").strip().split() if part]
    if not parts:
        return None
    body = r"\s+".join(re.escape(part) for part in parts)
    return re.compile(rf"(?<![A-Za-z0-9_])@?({body})(?![A-Za-z0-9_])", re.IGNORECASE)


def _gateway_nickname_matches(prompt: str, gateway_store: AgentGatewayStore) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    text = str(prompt or "")
    if not text.strip():
        return matches
    for record in gateway_store.list(include_disabled=True):
        pattern = _gateway_nickname_pattern(record.label)
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            matched_text = str(match.group(1) or match.group(0) or "").strip()
            matches.append(
                {
                    "record": record,
                    "matched_text": matched_text,
                    "start": match.start(),
                }
            )
    matches.sort(key=lambda item: int(item.get("start") or 0))
    return matches


def _distinct_gateway_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    distinct: list[dict[str, Any]] = []
    for item in matches:
        record = item.get("record")
        gateway_id = str(getattr(record, "gateway_id", "") or "").strip()
        if not gateway_id or gateway_id in seen:
            continue
        seen.add(gateway_id)
        distinct.append(item)
    return distinct


def _resolve_agent_gateway_routing(
    prompt: str,
    context: dict[str, Any],
    gateway_store: AgentGatewayStore,
) -> dict[str, Any]:
    matches = _gateway_nickname_matches(prompt, gateway_store)
    disabled_matches = _distinct_gateway_matches(
        [item for item in matches if getattr(item.get("record"), "enabled", True) is False]
    )
    if disabled_matches:
        names = ", ".join(_gateway_display_label(item["record"]) for item in disabled_matches)
        raise HTTPException(status_code=400, detail=f"Gateway nickname is disabled: {names}.")

    enabled_matches = _distinct_gateway_matches(
        [item for item in matches if getattr(item.get("record"), "enabled", True) is not False]
    )
    if len(enabled_matches) > 1:
        names = ", ".join(_gateway_display_label(item["record"]) for item in enabled_matches)
        raise HTTPException(
            status_code=400,
            detail=f"Multiple gateway nicknames were mentioned: {names}. Please mention one gateway.",
        )
    if len(enabled_matches) == 1:
        match = enabled_matches[0]
        record = match["record"]
        gateway_context = _gateway_context_from_record(record)
        if not gateway_context:
            raise HTTPException(status_code=400, detail="Matched gateway does not have a resolved node.")
        gateway_context["gateway_routing"] = _gateway_routing_metadata(
            "nickname",
            record,
            matched_text=str(match.get("matched_text") or ""),
        )
        return gateway_context

    selected_context = _resolve_agent_gateway_selection(context, gateway_store)
    if selected_context:
        record = gateway_store.get(str(selected_context.get("gateway_id") or "").strip())
        selected_context["gateway_routing"] = _gateway_routing_metadata("selected", record)
        return selected_context
    default_record = gateway_store.get_default()
    if default_record is not None and default_record.enabled:
        default_context = _gateway_context_from_record(default_record)
        if default_context:
            default_context["gateway_routing"] = _gateway_routing_metadata(
                "default",
                default_record,
            )
            return default_context
    return {"gateway_routing": _gateway_routing_metadata("default")}


def _resolve_agent_gateway_selection(
    context: dict[str, Any],
    gateway_store: AgentGatewayStore,
) -> dict[str, Any]:
    payload = dict(context or {})
    gateway_id = str(payload.get("gateway_id") or "").strip()
    gateway_node = str(payload.get("gateway_node") or payload.get("node") or "").strip()
    record = gateway_store.get(gateway_id) if gateway_id else None
    if record is None and gateway_node:
        record = gateway_store.find_by_node(gateway_node)
    if record is None:
        return {}
    if not record.enabled:
        raise HTTPException(status_code=400, detail="Selected gateway is disabled.")
    gateway_context = _gateway_context_from_record(record)
    if not gateway_context:
        raise HTTPException(status_code=400, detail="Selected gateway does not have a resolved node.")
    return gateway_context


def _resolve_terminal_gateway_selection(
    settings: Settings,
    gateway_store: AgentGatewayStore,
    gateway_id: str | None = None,
    gateway_node: str | None = None,
) -> tuple[str, str, str]:
    record = gateway_store.get(str(gateway_id or "").strip()) if gateway_id else None
    if record is None and gateway_node:
        record = gateway_store.find_by_node(str(gateway_node or "").strip())
    if record is not None:
        if not record.enabled:
            raise HTTPException(status_code=400, detail="Selected gateway is disabled.")
        node = str(record.node or "").strip()
        base_url = _gateway_base_url(record.base_url)
        if not node or not base_url:
            raise HTTPException(status_code=400, detail="Selected gateway has not resolved its node yet.")
        return record.gateway_id, node, base_url
    node, base_url = _resolve_terminal_gateway(settings)
    return "", node, base_url


def _apply_trusted_terminal_context(
    context: dict[str, Any],
    terminal_store: AgentTerminalSessionStore,
) -> dict[str, Any]:
    payload = dict(context or {})
    session_id = str(payload.get("terminal_session_id") or "").strip()
    cwd = str(payload.get("terminal_cwd") or "").strip()
    gateway_id = str(payload.get("gateway_id") or "").strip()
    gateway_node = str(payload.get("gateway_node") or payload.get("node") or "").strip()
    trusted_cwd = terminal_store.trusted_cwd(
        session_id,
        cwd,
        gateway_id=gateway_id,
        gateway_node=gateway_node,
    )
    if trusted_cwd is not None:
        payload["terminal_session_id"] = session_id
        payload["terminal_cwd"] = trusted_cwd
        payload["execute_in_terminal"] = bool(payload.get("execute_in_terminal"))
        return payload
    payload.pop("terminal_session_id", None)
    payload.pop("terminal_cwd", None)
    payload.pop("execute_in_terminal", None)
    return payload


_REMOVED_OPERATOR_CONTEXT_KEYS = frozenset(
    {
        "operator_memory_question_policy_mode",
        "llm_base_scheme",
        "llm_base_host",
        "llm_base_port",
        "llm_base_path",
        "llm_base_url",
        "llm_timeout_seconds",
        "llm_max_tokens",
        "llm_operator_formatter_source_preview_chars",
        "llm_operator_verification_enforced",
        "llm_operator_final_response_mode",
        "llm_operator_max_clarification_rounds",
        "agent_memory_enabled",
        "agent_memory_prompt_max_chars",
        *REMOVED_PUBLIC_SETTING_KEYS,
        *PUBLIC_RUNTIME_CONTROL_KEYS,
        *PROFILE_REPLACED_INTERNAL_KEYS,
    }
)

__all__ = [name for name in globals() if not name.startswith("__")]
