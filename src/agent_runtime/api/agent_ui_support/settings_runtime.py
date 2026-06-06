"""Agent settings, model, restart, and runtime-construction helpers for Agent UI."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.common import *
from agent_runtime.api.agent_ui_support.directory import *
from agent_runtime.api.agent_ui_support.models import *
from agent_runtime.api.agent_ui_support.prompt_context import *
from agent_runtime.api.agent_ui_support.memory import *
from agent_runtime.api.agent_ui_support.events import *
from agent_runtime.api.agent_ui_support.memory_drafts import *
from agent_runtime.api.agent_ui_support.gateway import *

def _drop_removed_operator_context_keys(context: dict[str, Any]) -> None:
    strip_public_runtime_overrides(context)
    for key in _REMOVED_OPERATOR_CONTEXT_KEYS:
        context.pop(key, None)


def _request_followup_context(
    *,
    state_context: dict[str, Any] | None,
    followup_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build follow-up context while preserving the active request's routing binding."""

    merged = dict(state_context or {})
    incoming = dict(followup_context or {})
    if str(merged.get("terminal_session_id") or "").strip():
        for key in (
            "terminal_session_id",
            "terminal_cwd",
            "execute_in_terminal",
            "gateway_id",
            "gateway_node",
            "gateway_url",
            "gateway_endpoints",
        ):
            incoming.pop(key, None)
    elif str(merged.get("gateway_id") or "").strip():
        for key in ("gateway_id", "gateway_node", "gateway_url", "gateway_endpoints"):
            incoming.pop(key, None)
    merged.update(incoming)
    _drop_removed_operator_context_keys(merged)
    return merged


def _agent_operator_settings_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return non-runtime request presentation hints from the Agent UI.

    Runtime controls are backend-owned; client context is never allowed to
    override planning, repair, policy, or streaming behavior.
    """

    payload = dict(context or {})
    result: dict[str, Any] = {}
    agent_display_name = _sanitize_agent_display_name(payload.get("agent_display_name"))
    if agent_display_name:
        result["agent_display_name"] = agent_display_name
    if "llm_timeout_seconds" in payload:
        try:
            timeout_seconds = float(payload.get("llm_timeout_seconds"))
        except (TypeError, ValueError):
            timeout_seconds = None
        if timeout_seconds is not None:
            result["llm_timeout_seconds"] = max(1.0, min(1800.0, timeout_seconds))
    if "llm_max_tokens" in payload:
        try:
            max_tokens = int(payload.get("llm_max_tokens"))
        except (TypeError, ValueError):
            max_tokens = None
        if max_tokens is not None:
            result["llm_max_tokens"] = max(0, min(65536, max_tokens))
    return result


def _sanitize_agent_display_name(value: Any) -> str:
    """Return a compact one-line display name for the local agent."""

    cleaned = " ".join(str(value or "").replace("<", "").replace(">", "").split()).strip()
    if not cleaned:
        return "Agent"
    return cleaned[:40] or "Agent"


_AGENT_NAME_OVERUSED_NAMES = (
    "Agent",
    "CodeWhisper",
    "Code Whisper",
    "CodeNook",
    "Code Nook",
)

_AGENT_NAME_STYLE_LANES = (
    "quiet and capable",
    "warm workshop",
    "crisp command center",
    "curious lab notebook",
    "calm night-ops",
    "playful but professional",
    "minimal and focused",
    "soft sci-fi utility",
)

_AGENT_NAME_INSPIRATION_WORDS = (
    "Anchor",
    "Beacon",
    "Bright",
    "Canvas",
    "Cipher",
    "Drift",
    "Forge",
    "Glint",
    "Harbor",
    "Kindle",
    "Lantern",
    "Mosaic",
    "Nimbus",
    "Pilot",
    "Prism",
    "Signal",
    "Slate",
    "Spark",
    "Thread",
    "Vector",
)


def _agent_name_key(value: Any) -> str:
    """Return a loose comparison key for display-name de-duplication."""

    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _agent_name_avoid_names(payload: AgentNameSuggestionPayload) -> list[str]:
    """Return sanitized names the name generator should avoid."""

    seen: set[str] = set()
    names: list[str] = []
    for item in [payload.current_name, *payload.recent_names, *_AGENT_NAME_OVERUSED_NAMES]:
        name = _sanitize_agent_display_name(item)
        key = _agent_name_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names[:24]


def _agent_name_allowed(name: str, avoid_keys: set[str]) -> bool:
    """Return whether a suggested name is useful enough to show."""

    key = _agent_name_key(name)
    return bool(key and key != "agent" and key not in avoid_keys)


def _agent_name_suggestion_prompt(
    payload: AgentNameSuggestionPayload,
    *,
    style_lane: str = "warm workshop",
    inspiration_words: list[str] | None = None,
    creative_nonce: str = "",
) -> str:
    """Build the LLM prompt for one random-ish Agent UI display name."""

    avoid_names = _agent_name_avoid_names(payload)
    inspiration = ", ".join(inspiration_words or [])
    return "\n".join(
        [
            *prompt_lines("agent.name_suggestion"),
            "",
            f"Creative lane: {style_lane}",
            f"Inspiration words: {inspiration or 'none'}",
            f"Creative seed: {creative_nonce or 'none'}",
            f"Avoid exact names: {', '.join(avoid_names) or 'none'}",
            f"Current name: {_sanitize_agent_display_name(payload.current_name)}",
            f"Current model: {str(payload.model_name or 'auto')[:200]}",
        ]
    )


def _fallback_agent_name(avoid_keys: set[str] | None = None) -> str:
    """Return a local fallback if the LLM cannot suggest a name."""

    avoid = avoid_keys or set()
    names = [
        "Aster",
        "Coda",
        "Lumen",
        "Nova",
        "Orbit",
        "Patch",
        "Quill",
        "Sol",
        "Tinker",
        "Vega",
    ]
    random.shuffle(names)
    for name in names:
        if _agent_name_allowed(name, avoid):
            return name
    return "Coda"


def _agent_llm_endpoint_defaults(settings: Settings) -> dict[str, Any]:
    """Return browser-safe pieces of the configured OpenAI-compatible base URL."""

    raw_base_url = str(settings.llm_base_url or "").strip() or "http://127.0.0.1:8000/v1"
    try:
        parsed = urlsplit(raw_base_url)
        port = parsed.port
    except ValueError:
        parsed = urlsplit("http://127.0.0.1:8000/v1")
        port = 8000
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "http"
    host = parsed.hostname or "127.0.0.1"
    if port is None:
        port = 443 if scheme == "https" else 80
    path = parsed.path or "/v1"
    if not path.startswith("/"):
        path = f"/{path}"
    return {
        "llm_base_scheme": scheme,
        "llm_base_host": host,
        "llm_base_port": int(port),
        "llm_base_path": path,
        "llm_base_url": urlunsplit((scheme, f"{host}:{int(port)}", path, "", "")),
    }


def _agent_llm_endpoint_context(context: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Return a sanitized request-scoped OpenAI-compatible endpoint override."""

    payload = dict(context or {})
    defaults = _agent_llm_endpoint_defaults(settings)
    raw_url = str(payload.get("llm_base_url") or "").strip()
    if raw_url:
        try:
            parsed = urlsplit(raw_url)
            if not parsed.scheme:
                parsed = urlsplit(f"http://{raw_url}")
            scheme = parsed.scheme
            host = parsed.hostname or ""
            port = parsed.port
            path = parsed.path or defaults["llm_base_path"]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid LLM endpoint URL: {exc}") from exc
    else:
        raw_host = str(payload.get("llm_base_host") or defaults["llm_base_host"]).strip()
        if "://" in raw_host:
            try:
                parsed_host = urlsplit(raw_host)
                scheme = str(payload.get("llm_base_scheme") or parsed_host.scheme or defaults["llm_base_scheme"])
                host = parsed_host.hostname or ""
                port = parsed_host.port
                path = parsed_host.path or str(payload.get("llm_base_path") or defaults["llm_base_path"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid LLM host/IP: {exc}") from exc
        else:
            scheme = str(payload.get("llm_base_scheme") or defaults["llm_base_scheme"])
            host = raw_host
            port = None
            path = str(payload.get("llm_base_path") or defaults["llm_base_path"])
        if port is None:
            raw_port = payload.get("llm_base_port", defaults["llm_base_port"])
            try:
                port = int(raw_port)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="LLM endpoint port must be a number.") from exc

    scheme = str(scheme or "").strip().lower()
    if scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="LLM endpoint scheme must be http or https.")
    host = str(host or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="LLM endpoint host/IP is required.")
    try:
        safe_port = int(port)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="LLM endpoint port must be a number.") from exc
    if safe_port <= 0 or safe_port > 65535:
        raise HTTPException(status_code=400, detail="LLM endpoint port must be between 1 and 65535.")
    safe_path = str(path or "/v1").strip() or "/v1"
    if not safe_path.startswith("/"):
        safe_path = f"/{safe_path}"
    base_url = urlunsplit((scheme, f"{host}:{safe_port}", safe_path, "", ""))
    return {
        "llm_base_scheme": scheme,
        "llm_base_host": host,
        "llm_base_port": safe_port,
        "llm_base_path": safe_path,
        "llm_base_url": base_url,
    }


def _resolve_agent_ui_file(settings: Settings, raw_path: str) -> Path:
    """Resolve one UI file link safely inside the configured workspace root."""

    path_text = str(raw_path or "").strip().strip("`'\"")
    if not path_text:
        raise HTTPException(status_code=400, detail="File path is required")
    if path_text.startswith("file://"):
        from urllib.parse import urlparse, unquote

        path_text = unquote(urlparse(path_text).path)
    workspace_root = Path(settings.workspace_root).resolve()
    candidate = Path(path_text).expanduser()
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (workspace_root / candidate).resolve(strict=False)
    )
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail="File link resolves outside the workspace root",
        ) from exc
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return resolved


def _prompt_template_preview(body: str, *, max_chars: int = 220) -> str:
    text = " ".join(str(body or "").split())
    if len(text) <= max_chars:
        return text
    return f"{text[: max(0, max_chars - 3)].rstrip()}..."


def _editor_text_preview(body: str, *, max_chars: int = 220) -> str:
    text = " ".join(str(body or "").split())
    if len(text) <= max_chars:
        return text
    return f"{text[: max(0, max_chars - 3)].rstrip()}..."


def _memory_entry_payload(
    entry: MemoryEntry,
    *,
    include_body: bool = False,
) -> dict[str, Any]:
    payload = entry.model_dump(mode="json")
    payload["preview"] = _editor_text_preview(
        f"{entry.instruction} {entry.summary}".strip()
    )
    if not include_body:
        payload.pop("instruction", None)
        payload.pop("safe_examples", None)
        payload.pop("blocked_examples", None)
        payload.pop("rationale", None)
    return payload


def _memory_entry_or_404(store: AgentMemoryStore, memory_id: str) -> MemoryEntry:
    entry = store.get_entry(memory_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return entry


def _memory_update_from_editor_payload(
    payload: PromptEditorMemoryUpdatePayload,
) -> MemoryEntryUpdate:
    data = payload.model_dump(exclude_unset=True)
    data.pop("status", None)
    if "instruction" in data and not str(data.get("instruction") or "").strip():
        raise HTTPException(status_code=400, detail="Memory instruction cannot be empty")
    return MemoryEntryUpdate.model_validate(data)


def _prompt_template_payload(
    comparison: PromptTemplateComparison,
    *,
    include_body: bool = False,
) -> dict[str, Any]:
    record = comparison.active_record
    if record is None:
        raise HTTPException(status_code=404, detail="Prompt template not found")
    default = comparison.default
    validation_error = ""
    try:
        variables = extract_template_variables(record.body)
    except PromptTemplateRenderError as exc:
        variables = []
        validation_error = str(exc)
    payload: dict[str, Any] = {
        "prompt_key": comparison.prompt_key,
        "version": int(record.version),
        "status": str(record.status or "active"),
        "metadata": dict(record.metadata or {}),
        "created_at": str(record.created_at or ""),
        "updated_at": str(record.updated_at or ""),
        "source": comparison.source,
        "edited": comparison.edited,
        "has_default": default is not None,
        "default_version": int(default.version) if default is not None else None,
        "default_status": str(default.status or "") if default is not None else "",
        "default_metadata": dict(default.metadata or {}) if default is not None else {},
        "variables": variables,
        "validation_error": validation_error,
        "preview": _prompt_template_preview(record.body),
    }
    if include_body:
        payload["body"] = record.body
        payload["default_body"] = default.body if default is not None else ""
    return payload


def _prompt_template_or_404(
    store: PromptTemplateStore,
    prompt_key: str,
) -> PromptTemplateComparison:
    comparison = store.get_for_editor(prompt_key)
    if comparison is None or comparison.active_record is None:
        raise HTTPException(status_code=404, detail="Prompt template not found")
    return comparison


def _schedule_process_restart(delay_seconds: float = 0.5) -> None:
    """Restart the current server process after the HTTP response can flush."""

    def restart() -> None:
        time.sleep(max(0.05, float(delay_seconds)))
        os.execv(sys.executable, _restart_argv())

    threading.Thread(target=restart, daemon=True, name="agent-ui-restart").start()


def _restart_argv() -> list[str]:
    """Return argv for restarting without running package __main__.py as a script."""

    original = list(sys.argv)
    if not original:
        return [sys.executable, "-m", "uvicorn", *_default_uvicorn_args()]

    script_path = Path(original[0]).resolve(strict=False)
    if script_path.name == "__main__.py" and script_path.parent.name == "uvicorn":
        return [sys.executable, "-m", "uvicorn", *original[1:]]

    if script_path.name == "uvicorn":
        return [sys.executable, "-m", "uvicorn", *original[1:]]

    return [sys.executable, *original]


def _request_gateway_restart(settings: Settings) -> dict[str, Any]:
    """Ask the configured gateway to restart, if a gateway URL is available."""

    try:
        node = str(settings.resolved_default_node() or "localhost").strip() or "localhost"
        gateway_url = _gateway_base_url(settings.resolve_gateway_url(node))
    except ValueError as exc:
        return {"status": "skipped", "reason": str(exc)}

    payload = json.dumps({"node": node}).encode("utf-8")
    request = urllib_request.Request(
        f"{gateway_url}/restart",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(
            request,
            timeout=min(float(settings.gateway_timeout_seconds), 3.0),
        ) as response:
            raw_body = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return {
            "status": "failed",
            "reason": f"Gateway restart failed with HTTP {exc.code}: {detail or exc.reason}",
        }
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        return {"status": "failed", "reason": f"Gateway restart request failed: {exc}"}

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"status": "failed", "reason": "Gateway restart returned invalid JSON."}
    if not isinstance(body, dict):
        return {"status": "failed", "reason": "Gateway restart returned a non-object payload."}
    return body


def _gateway_restart_supported(settings: Settings) -> bool:
    """Return whether the configured gateway restart endpoint can be addressed."""

    try:
        node = str(settings.resolved_default_node() or "localhost").strip() or "localhost"
        return bool(_gateway_base_url(settings.resolve_gateway_url(node)))
    except ValueError:
        return False


def _request_gateway_json(
    settings: Settings,
    *,
    path: str,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Send one JSON request to the configured gateway."""

    try:
        node = str(settings.resolved_default_node() or "localhost").strip() or "localhost"
        gateway_url = _gateway_base_url(settings.resolve_gateway_url(node))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    body = dict(payload or {})
    if method.upper() != "GET":
        body["node"] = node
        data = json.dumps(body).encode("utf-8")
        url = f"{gateway_url}{path}"
    else:
        query_body = dict(body)
        query_body["node"] = node
        query = urlencode(query_body)
        data = None
        url = f"{gateway_url}{path}?{query}"
    request = urllib_request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method.upper(),
    )
    try:
        with urllib_request.urlopen(
            request,
            timeout=timeout_seconds or min(float(settings.gateway_timeout_seconds), 5.0),
        ) as response:
            raw_body = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise HTTPException(
            status_code=exc.code,
            detail=f"Gateway request failed with HTTP {exc.code}: {detail or exc.reason}",
        ) from exc
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"Gateway request failed: {exc}") from exc

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Gateway returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Gateway returned a non-object payload.")
    return parsed


def _request_gateway_json_at(
    *,
    gateway_url: str,
    node: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Send one JSON request to a selected gateway endpoint."""

    base_url = _gateway_base_url(gateway_url)
    if not base_url:
        raise HTTPException(status_code=503, detail="Gateway URL is not configured.")
    request_payload = {"node": str(node or "").strip(), **dict(payload or {})}
    request = urllib_request.Request(
        f"{base_url}{path}",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout_seconds or 5.0) as response:
            raw_body = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise HTTPException(
            status_code=exc.code,
            detail=f"Gateway request failed with HTTP {exc.code}: {detail or exc.reason}",
        ) from exc
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"Gateway request failed: {exc}") from exc

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Gateway returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Gateway returned a non-object payload.")
    return parsed


def _agent_llm_launch_defaults(settings: Settings) -> dict[str, Any]:
    """Return browser-safe defaults for the Settings LLM startup section."""

    cwd = str(settings.agent_ui_llm_launch_cwd or "").strip() or "/tmp"
    return {
        "llm_launch_conda_env": str(settings.agent_ui_llm_launch_conda_env or "vllm").strip() or "vllm",
        "llm_launch_command": str(settings.agent_ui_llm_launch_command or "").strip(),
        "llm_launch_cwd": cwd,
    }


def _gateway_terminal_cwd(settings: Settings, gateway: Any | None, *, override: str | None = None) -> str:
    """Return the initial cwd for one gateway-backed browser terminal."""

    raw = override if override is not None else getattr(gateway, "terminal_cwd", "")
    cwd = str(raw or "").strip()
    if cwd:
        return cwd
    return str(settings.agent_ui_llm_launch_cwd or "").strip() or "/tmp"


def _agent_model_options(settings: Settings) -> list[dict[str, str]]:
    configured_model = str(settings.default_model or "").strip() or "auto"
    options = [{"value": "auto", "label": "Auto"}]
    if configured_model.lower() != "auto":
        options.append({"value": configured_model, "label": configured_model})
    return options


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _draft_monitor_from_prompt(payload: AgentMonitorDraftRequest) -> AgentMonitorDraftResponse:
    return draft_monitor_from_prompt(payload)


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _agent_model_context_window_tokens(item: Any) -> int | None:
    """Extract a context window hint from one OpenAI-compatible model item."""

    if not isinstance(item, dict):
        return None
    for key in (
        "max_model_len",
        "max_model_length",
        "context_window_tokens",
        "context_length",
        "max_context_length",
        "max_seq_len",
        "max_sequence_length",
        "max_position_embeddings",
    ):
        parsed = _positive_int(item.get(key))
        if parsed:
            return parsed
    for key in ("metadata", "config", "model_config"):
        parsed = _agent_model_context_window_tokens(item.get(key))
        if parsed:
            return parsed
    return None


def _agent_model_item_id(item: Any) -> str | None:
    if isinstance(item, str) and item.strip():
        return item.strip()
    if not isinstance(item, dict):
        return None
    for key in ("id", "name", "model"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return None


def _agent_model_candidates(payload: Any) -> list[Any]:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            candidates = data
        else:
            models = payload.get("models")
            if isinstance(models, list):
                candidates = models
    elif isinstance(payload, list):
        candidates = payload
    return candidates


def _extract_agent_model_info(payload: Any, *, preferred_model: str | None = None) -> dict[str, Any]:
    """Extract model id and safe serving metadata from an OpenAI-compatible models payload."""

    candidates = _agent_model_candidates(payload)
    preferred = str(preferred_model or "").strip()
    if preferred and preferred.lower() != "auto":
        for item in candidates:
            model_id = _agent_model_item_id(item)
            if model_id == preferred:
                return {
                    "name": model_id,
                    "context_window_tokens": _agent_model_context_window_tokens(item),
                }
    for item in candidates:
        model_id = _agent_model_item_id(item)
        if model_id:
            return {
                "name": model_id,
                "context_window_tokens": _agent_model_context_window_tokens(item),
            }
    return {"name": None, "context_window_tokens": None}


def _extract_agent_model_id(payload: Any) -> str | None:
    """Extract the first model id from an OpenAI-compatible models payload."""

    return _extract_agent_model_info(payload).get("name")


def _agent_active_model(settings: Settings) -> dict[str, Any]:
    """Return the configured or discovered model name without requiring LLM availability."""

    configured_model = str(settings.default_model or "").strip() or "auto"
    endpoint = _agent_llm_endpoint_defaults(settings)
    request = urllib_request.Request(
        f"{endpoint['llm_base_url'].rstrip('/')}/models",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        method="GET",
    )
    try:
        with urllib_request.urlopen(request, timeout=1.2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib_error.HTTPError, urllib_error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        if configured_model.lower() != "auto":
            return {"name": configured_model, "source": "configured", "available": False}
        return {"name": "auto", "source": "auto", "available": False}
    model_info = _extract_agent_model_info(payload, preferred_model=configured_model)
    model_id = model_info.get("name")
    if model_id:
        result: dict[str, Any] = {"name": model_id, "source": "discovered", "available": True}
        context_window_tokens = _positive_int(model_info.get("context_window_tokens"))
        if context_window_tokens:
            result["context_window_tokens"] = context_window_tokens
        return result
    if configured_model.lower() != "auto":
        return {"name": configured_model, "source": "configured", "available": False}
    return {"name": "auto", "source": "auto", "available": False}


def _agent_context_window_tokens(settings: Settings, context: dict[str, Any] | None = None) -> tuple[int, str]:
    """Resolve the active LLM context window from live model metadata when possible."""

    effective_settings = settings
    payload = dict(context or {})
    raw_base_url = str(payload.get("llm_base_url") or "").strip()
    if raw_base_url:
        effective_settings = settings.model_copy(update={"llm_base_url": raw_base_url})
    active_model = _agent_active_model(effective_settings)
    context_window_tokens = _positive_int(active_model.get("context_window_tokens"))
    if context_window_tokens:
        return context_window_tokens, "model_api"
    return int(settings.llm_context_window_tokens), "settings"


def _resolve_agent_request_model(settings: Settings, requested_model: str | None) -> str | None:
    raw_model = str(requested_model or "").strip()
    if not raw_model:
        return None
    configured_model = str(settings.default_model or "").strip() or "auto"
    if raw_model.lower() == "auto":
        return "auto"
    if raw_model.lower() == "configured":
        return configured_model
    if configured_model.lower() != "auto" and raw_model == configured_model:
        return configured_model
    raise HTTPException(status_code=400, detail="Requested LLM model is not available for this Agent UI session.")


def _default_uvicorn_args() -> list[str]:
    """Build a conservative fallback Uvicorn launch command from runtime env."""

    args = [
        "--app-dir",
        "src",
        "agent_runtime.api.app:create_app",
        "--factory",
        "--host",
        os.getenv("AOR_HOST", "0.0.0.0"),
        "--port",
        os.getenv("AOR_PORT", "8011"),
    ]
    if os.getenv("AOR_RELOAD", "").strip().lower() in {"1", "true", "yes", "on"}:
        args.append("--reload")
    return args

__all__ = [name for name in globals() if not name.startswith("__")]
