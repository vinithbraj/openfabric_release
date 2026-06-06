"""Agent request, advisory, trace enrichment, and conversation helpers for Agent UI."""

from __future__ import annotations

import shutil
import subprocess

from agent_runtime.api.agent_ui_support.common import *
from agent_runtime.api.agent_ui_support.directory import *
from agent_runtime.api.agent_ui_support.models import *
from agent_runtime.api.agent_ui_support.prompt_context import *
from agent_runtime.api.agent_ui_support.memory import *
from agent_runtime.api.agent_ui_support.events import *
from agent_runtime.api.agent_ui_support.memory_drafts import *
from agent_runtime.api.agent_ui_support.gateway import *
from agent_runtime.api.agent_ui_support.settings_runtime import *
from agent_runtime.core.user_errors import user_error_detail, user_error_message

ADVISORY_TERMINAL_OUTPUT_MAX_CHARS = 8000
V1_FAST_PATH_COMMAND_TIMEOUT_SECONDS = 15
TERMINAL_RUNTIME_FAILURE_CATEGORIES = frozenset(
    {
        "execution_error",
        "runtime_error",
        "safety_block",
        "unexpected_error",
        "validation_error",
    }
)
LEARNED_COMMAND_FAILURE_NOTICE = (
    "A learnt command used in this run failed. You may want to delete that learnt step "
    "so it can be re-learnt."
)
OPENFABRIC_PRODUCT_IDENTITY = {
    "name": "OpenFabric",
    "positioning": "local-first typed agent runtime",
    "authoritative_description": (
        "OpenFabric is a local-first typed agent runtime for safe local execution, "
        "operator approvals, trace evidence, memory, tasks, schedules, audio input, "
        "integration APIs, and local or OpenAI-compatible models."
    ),
    "core_capabilities": [
        "safe local command execution through a gateway",
        "confirmation-gated actions and operator approvals",
        "trace evidence for planning, execution, failures, and final responses",
        "agent memory and prompt templates",
        "tasks, schedules, events, notifications, and monitors",
        "audio transcription and voice input through a local service",
        "integration APIs for non-streaming agent execution",
        "local and OpenAI-compatible model endpoints",
    ],
    "avoid_positioning": [
        "generic cloud orchestration platform",
        "edge AI marketplace",
        "distributed model hosting service",
    ],
}

_V1_REPO_SUMMARY_RE = re.compile(
    r"\b(?:what\s+(?:is|does)\s+(?:this\s+)?(?:repo|repository|project|codebase)"
    r"|explain\s+(?:this\s+)?(?:repo|repository|project|codebase|agent)"
    r"|summari[sz]e\s+(?:this\s+)?(?:repo|repository|project|codebase)"
    r"|(?:repo|repository|project|codebase)\s+(?:summary|overview)"
    r"|entry\s*points?|onboarding\s+risks?)\b",
    re.IGNORECASE,
)
_V1_MUTATION_INTENT_RE = re.compile(
    r"\b(?:create|write|edit|modify|update|delete|remove|rename|move|install|"
    r"start|stop|restart|commit|push|merge|apply|fix|implement|save|publish)\b",
    re.IGNORECASE,
)
_V1_EXACT_FILE_WRITE_RE = re.compile(
    r"""
    \b(?:create|write)\s+
    (?:a\s+|the\s+)?
    (?:file|text\s+file)
    (?:\s+(?:at|to|named|called))?
    \s+
    (?P<path>`[^`]+`|"[^"]+"|'[^']+'|[^\n\r]+?)
    \s+
    (?:containing|with)\s+
    exactly
    (?:\s+literal\s+text)?
    \s*:?\s*
    (?P<content>.+)
    \s*$
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


def _strip_v1_literal(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"', "`"}:
        return text[1:-1]
    if text.startswith("```") and text.endswith("```"):
        body = text.strip("`")
        lines = body.splitlines()
        if lines and re.fullmatch(r"[A-Za-z0-9_+-]+", lines[0].strip()):
            lines = lines[1:]
        return "\n".join(lines)
    return text


def _workspace_relative_path(raw_path: Any, workspace_root: Any) -> tuple[Path | None, str]:
    workspace = Path(workspace_root).resolve()
    candidate_text = _strip_v1_literal(raw_path)
    if not candidate_text:
        return None, "Path is empty."
    candidate = Path(candidate_text)
    if candidate.is_absolute():
        return None, "Path must be relative to the workspace."
    if any(part in {"..", ""} for part in candidate.parts):
        return None, "Path must stay inside the workspace and cannot contain '..'."
    resolved = (workspace / candidate).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError:
        return None, "Path must stay inside the workspace."
    if resolved == workspace:
        return None, "Path must identify a file, not the workspace root."
    return resolved, ""


def _v1_exact_file_write_request(prompt: str, workspace_root: Any) -> dict[str, Any] | None:
    match = _V1_EXACT_FILE_WRITE_RE.search(str(prompt or "").strip())
    if not match:
        return None
    resolved_path, error = _workspace_relative_path(match.group("path"), workspace_root)
    content = _strip_v1_literal(match.group("content"))
    if resolved_path is None:
        return {"ok": False, "error": error}
    return {
        "ok": True,
        "path": str(resolved_path),
        "relative_path": str(resolved_path.relative_to(Path(workspace_root).resolve())),
        "content": content,
    }


def _v1_repo_summary_requested(prompt: str) -> bool:
    text = str(prompt or "")
    if _V1_MUTATION_INTENT_RE.search(text):
        return False
    return bool(_V1_REPO_SUMMARY_RE.search(text))


def _append_v1_command_event(
    store: AgentTraceStore,
    request_id: str,
    event_type: str,
    title: str,
    summary: str,
    *,
    context: dict[str, Any],
    command: str,
    cwd: str,
    channel: str | None = None,
    text: str | None = None,
    exit_code: int | None = None,
) -> None:
    detail = {
        **merge_gateway_metadata(context),
        "command": command,
        "cwd": cwd,
        "fast_path": True,
    }
    if channel is not None:
        detail["channel"] = channel
    if text is not None:
        detail["text"] = text
    if exit_code is not None:
        detail["exit_code"] = exit_code
    store.append_event(
        AgentTraceEvent(
            request_id=request_id,
            stage="execution",
            level="info" if event_type != "execution.command.error" else "error",
            event_type=event_type,
            title=title,
            summary=summary,
            detail=detail,
        )
    )


def _run_v1_read_only_repo_capsule(
    *,
    request_id: str,
    store: AgentTraceStore,
    context: dict[str, Any],
    workspace_root: Any,
) -> tuple[str, int]:
    workspace = Path(workspace_root).resolve()
    rg_command = "rg --files" if shutil.which("rg") else "find . -type f"
    script = r"""
set -u
printf '__OF_SECTION__:files\n'
if command -v rg >/dev/null 2>&1; then
  rg --files | sed -n '1,240p'
else
  find . -type f | sed 's#^\./##' | sort | sed -n '1,240p'
fi
printf '\n__OF_SECTION__:README.md\n'
sed -n '1,220p' README.md 2>/dev/null || true
printf '\n__OF_SECTION__:pyproject.toml\n'
sed -n '1,220p' pyproject.toml 2>/dev/null || true
printf '\n__OF_SECTION__:startup\n'
for f in docker/server-entrypoint.sh docker/server.Dockerfile docker-compose.yml compose.yml start.sh run.sh scripts/start.sh src/llm/start.sh src/llm/start-vllm.sh; do
  if [ -f "$f" ]; then
    printf '\n--- %s ---\n' "$f"
    sed -n '1,140p' "$f" 2>/dev/null || true
  fi
done
"""
    display_command = f"bash -lc {rg_command!r} repo-summary capsule"
    _append_v1_command_event(
        store,
        request_id,
        "execution.command.started",
        "Repo summary started",
        "The runtime started a bounded read-only repo summary capsule.",
        context=context,
        command=display_command,
        cwd=str(workspace),
    )
    try:
        completed = subprocess.run(
            ["bash", "-lc", script],
            cwd=str(workspace),
            text=True,
            capture_output=True,
            timeout=V1_FAST_PATH_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = str(exc.stdout or "")
        _append_v1_command_event(
            store,
            request_id,
            "execution.command.error",
            "Repo summary timed out",
            "The read-only repo summary capsule exceeded its bounded timeout.",
            context=context,
            command=display_command,
            cwd=str(workspace),
            channel="error",
            text=output[-4000:],
            exit_code=124,
        )
        return output, 124
    output = f"{completed.stdout or ''}{completed.stderr or ''}"
    if completed.stdout:
        _append_v1_command_event(
            store,
            request_id,
            "execution.command.stdout",
            "Repo summary output",
            "The read-only repo summary capsule produced bounded evidence.",
            context=context,
            command=display_command,
            cwd=str(workspace),
            channel="stdout",
            text=completed.stdout[-8000:],
        )
    _append_v1_command_event(
        store,
        request_id,
        "execution.command.completed",
        "Repo summary completed",
        "The read-only repo summary capsule completed.",
        context=context,
        command=display_command,
        cwd=str(workspace),
        channel="completed",
        exit_code=int(completed.returncode),
    )
    return output, int(completed.returncode)


def _split_v1_capsule_sections(output: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in str(output or "").splitlines():
        if line.startswith("__OF_SECTION__:"):
            current = line.split(":", 1)[1].strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _first_readme_sentence(readme: str) -> str:
    for line in str(readme or "").splitlines():
        text = line.strip(" #\t")
        if text:
            return text[:220]
    return ""


def _pyproject_name(pyproject: str, fallback: str) -> str:
    match = re.search(r"(?m)^\s*name\s*=\s*[\"']([^\"']+)[\"']", str(pyproject or ""))
    return str(match.group(1)).strip() if match else fallback


def _pyproject_scripts(pyproject: str) -> list[str]:
    scripts: list[str] = []
    in_scripts = False
    for line in str(pyproject or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_scripts = stripped in {"[project.scripts]", "[tool.poetry.scripts]"}
            continue
        if in_scripts and "=" in stripped:
            scripts.append(stripped[:160])
    return scripts[:6]


def _repo_summary_from_capsule(output: str, workspace_root: Any) -> str:
    sections = _split_v1_capsule_sections(output)
    files = [line.strip() for line in sections.get("files", "").splitlines() if line.strip()]
    readme = sections.get("README.md", "")
    pyproject = sections.get("pyproject.toml", "")
    startup = sections.get("startup", "")
    workspace = Path(workspace_root).resolve()
    project_name = _pyproject_name(pyproject, workspace.name)
    readme_summary = _first_readme_sentence(readme)
    entrypoints: list[str] = []
    for candidate in (
        "src/agent_runtime/api/app.py",
        "src/agent_runtime/api/runtime/engine.py",
        "src/agent_runtime/core/orchestrator.py",
        "docker/server-entrypoint.sh",
        "docker/server.Dockerfile",
        "pyproject.toml",
    ):
        if candidate in files:
            entrypoints.append(candidate)
    entrypoints.extend(_pyproject_scripts(pyproject))
    if startup:
        for line in startup.splitlines():
            if line.startswith("--- "):
                entrypoints.append(line.strip("- "))
    entrypoints = list(dict.fromkeys(entrypoints))[:8]
    signals = [
        item
        for item in (
            "tests/" if any(path.startswith("tests/") for path in files) else "",
            "artifacts/" if any(path.startswith("artifacts/") for path in files) else "",
            "src/agent_runtime/" if any(path.startswith("src/agent_runtime/") for path in files) else "",
            "docker/" if any(path.startswith("docker/") for path in files) else "",
        )
        if item
    ]
    risks = [
        "Confirm local model, gateway, audio, and HTTPS ports before first run.",
        "Use a clean seed state for tracked artifact databases before release packaging.",
    ]
    if any(path.startswith("artifacts/") for path in files):
        risks.append("Artifact databases are present in the repo; keep release seed data non-personal.")
    lines = [
        "## Repo Summary",
        "",
        f"`{project_name}` appears to be the OpenFabric local agent runtime workspace.",
    ]
    if readme_summary:
        lines.append(f"README signal: {readme_summary}")
    lines.extend(
        [
            "",
            "Likely entrypoints:",
            *(f"- `{item}`" for item in (entrypoints or ["README.md", "pyproject.toml"])),
            "",
            "Structure signals:",
            *(f"- `{item}`" for item in (signals or ["No common source/test/docker directories found in the bounded scan."])),
            "",
            "Onboarding risks:",
            *(f"- {item}" for item in risks),
            "",
            f"Evidence: one read-only capsule in `{workspace}` using `rg --files` when available, README, pyproject, and startup scripts.",
        ]
    )
    return "\n".join(lines)


def _v1_file_write_command() -> str:
    return (
        f"{Path(sys.executable).name} -c "
        "\"from pathlib import Path; import os; "
        "p=Path(os.environ['OF_INPUT_PATH']); "
        "p.parent.mkdir(parents=True, exist_ok=True); "
        "p.write_text(os.environ['OF_INPUT_CONTENT'], encoding='utf-8')\""
    )


def _v1_file_write_confirmation(
    *,
    request_id: str,
    prompt: str,
    context: dict[str, Any],
    settings: Settings,
    store: AgentTraceStore,
    state_store: AgentUiRequestStateStore | None,
    conversation_store: AgentConversationStore | None,
    write_request: dict[str, Any],
) -> None:
    if not write_request.get("ok"):
        store.append_event(
            AgentTraceEvent(
                request_id=request_id,
                stage="validation",
                level="warning",
                event_type="v1_fast_path.file_write.rejected",
                title="File write rejected",
                summary=str(write_request.get("error") or "The requested path is not allowed."),
                detail={"fast_path": "exact_file_write"},
            )
        )
        store.complete_request(
            request_id,
            "## File Write Rejected\n\n"
            f"{write_request.get('error') or 'The requested path is not allowed.'} "
            "Use a workspace-relative path without `..`.",
        )
        if conversation_store is not None:
            conversation_store.append_turn(context.get("conversation_id"), store.get_trace(request_id))
        return
    command = _v1_file_write_command()
    action = {
        **merge_gateway_metadata(context),
        "action_id": "v1_exact_file_write",
        "capability_id": "operator.shell_command",
        "operation_id": "shell_command",
        "kind": "shell_command",
        "label": "Create exact file",
        "command": command,
        "cwd": str(Path(settings.workspace_root).resolve()),
        "risk": "high",
        "requires_confirmation": True,
        "arguments": {
            "command": command,
            "cwd": ".",
            "risk": "high",
        },
        "bound_inputs": {
            "OF_INPUT_PATH": str(write_request["path"]),
            "OF_INPUT_CONTENT": str(write_request["content"]),
        },
        "relative_path": str(write_request["relative_path"]),
        "content_length": len(str(write_request["content"])),
    }
    planning_trace = {
        "v1_fast_path": "exact_file_write",
        "path": str(write_request["path"]),
        "relative_path": str(write_request["relative_path"]),
        "content": str(write_request["content"]),
        "command": command,
    }
    if state_store is not None:
        state_store.finish(
            request_id,
            planning_trace=planning_trace,
            confirmation_required=True,
            confirmation_actions=[action],
        )
    store.append_event(
        AgentTraceEvent(
            request_id=request_id,
            stage="validation",
            level="info",
            event_type="v1_fast_path.file_write.confirmation_required",
            title="Exact file write confirmation required",
            summary="The runtime prepared one guarded exact file-write command and is waiting for approval.",
            detail={
                "fast_path": "exact_file_write",
                "relative_path": write_request["relative_path"],
                "content_length": len(str(write_request["content"])),
            },
        )
    )
    store.complete_request(
        request_id,
        "## Confirmation Required\n\n"
        f"Approve creating `{write_request['relative_path']}` with exactly "
        f"{len(str(write_request['content']))} character(s).",
        confirmation_required=True,
        confirmation_actions=[action],
    )
    if conversation_store is not None:
        conversation_store.append_turn(context.get("conversation_id"), store.get_trace(request_id))


def _run_v1_exact_file_write(
    *,
    request_id: str,
    context: dict[str, Any],
    settings: Settings,
    store: AgentTraceStore,
    planning_trace: dict[str, Any],
) -> str:
    workspace = Path(settings.workspace_root).resolve()
    path = str(planning_trace.get("path") or "")
    content = str(planning_trace.get("content") or "")
    command = str(planning_trace.get("command") or _v1_file_write_command())
    env = dict(os.environ)
    env["OF_INPUT_PATH"] = path
    env["OF_INPUT_CONTENT"] = content
    _append_v1_command_event(
        store,
        request_id,
        "execution.command.started",
        "Exact file write started",
        "The approved exact file-write command started.",
        context=context,
        command=command,
        cwd=str(workspace),
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import os; "
                "p=Path(os.environ['OF_INPUT_PATH']); "
                "p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text(os.environ['OF_INPUT_CONTENT'], encoding='utf-8')"
            ),
        ],
        cwd=str(workspace),
        env=env,
        text=True,
        capture_output=True,
        timeout=V1_FAST_PATH_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    stdout = str(completed.stdout or "")
    stderr = str(completed.stderr or "")
    if stdout:
        _append_v1_command_event(
            store,
            request_id,
            "execution.command.stdout",
            "Exact file write output",
            "The exact file-write command produced stdout.",
            context=context,
            command=command,
            cwd=str(workspace),
            channel="stdout",
            text=stdout[-4000:],
        )
    if stderr:
        _append_v1_command_event(
            store,
            request_id,
            "execution.command.stderr",
            "Exact file write stderr",
            "The exact file-write command produced stderr.",
            context=context,
            command=command,
            cwd=str(workspace),
            channel="stderr",
            text=stderr[-4000:],
        )
    _append_v1_command_event(
        store,
        request_id,
        "execution.command.completed" if completed.returncode == 0 else "execution.command.error",
        "Exact file write completed" if completed.returncode == 0 else "Exact file write failed",
        "The approved exact file-write command completed.",
        context=context,
        command=command,
        cwd=str(workspace),
        channel="completed",
        exit_code=int(completed.returncode),
    )
    if completed.returncode != 0:
        raise RuntimeError(stderr or stdout or "Exact file write failed.")
    relative_path = str(planning_trace.get("relative_path") or Path(path).name)
    return f"Created `{relative_path}` with exactly {len(content)} character(s)."


def _build_traced_runtime(
    *,
    settings: Settings,
    base_runtime: Any,
    store: AgentTraceStore,
    request_id: str,
    llm_model: str | None = None,
    llm_base_url: str | None = None,
    llm_timeout_seconds: float | None = None,
    llm_max_tokens: int | None = None,
    response_streaming_enabled: bool = False,
) -> Any:
    """Build a trace-enabled runtime for one local UI request."""

    updates: dict[str, Any] = {}
    if llm_model:
        updates["default_model"] = llm_model
    if llm_base_url:
        updates["llm_base_url"] = llm_base_url
    if llm_timeout_seconds is not None:
        updates["llm_timeout_seconds"] = max(1.0, min(1800.0, float(llm_timeout_seconds)))
    if llm_max_tokens is not None:
        updates["llm_max_tokens"] = max(0, min(65536, int(llm_max_tokens)))
    effective_settings = settings.model_copy(update=updates) if updates else settings
    if isinstance(base_runtime, AgentRuntime):
        runtime = build_agent_runtime(effective_settings)
        runtime.llm_client = TracingLLMClient(
            runtime.llm_client,
            store,
            request_id,
            streaming_enabled=response_streaming_enabled,
        )
        return runtime

    required_attrs = ("llm_client", "registry", "execution_engine", "output_orchestrator")
    if all(hasattr(base_runtime, attr) for attr in required_attrs):
        return AgentRuntime(
            llm_client=TracingLLMClient(
                base_runtime.llm_client,
                store,
                request_id,
                streaming_enabled=response_streaming_enabled,
            ),
            registry=base_runtime.registry,
            execution_engine=base_runtime.execution_engine,
            output_orchestrator=base_runtime.output_orchestrator,
            memory_store=getattr(base_runtime, "memory_store", None),
            parameter_store=getattr(base_runtime, "parameter_store", None),
            plan_cache_store=getattr(base_runtime, "plan_cache_store", None),
            lrn_total_task_store=getattr(base_runtime, "lrn_total_task_store", None),
            command_template_cache_store=getattr(base_runtime, "command_template_cache_store", None),
            computation_cache_store=getattr(base_runtime, "computation_cache_store", None),
            reliability_store=getattr(base_runtime, "reliability_store", None),
        )

    return base_runtime


def _copy_planning_trace_for_request(planning_trace: Any, request_id: str) -> Any:
    """Return a replay trace copy bound to the new local UI request id when possible."""

    if planning_trace is not None and hasattr(planning_trace, "model_copy"):
        return planning_trace.model_copy(deep=True, update={"request_id": request_id})
    return planning_trace


def _runtime_requires_confirmation(runtime: Any, final_response: str) -> bool:
    """Return whether the runtime ended at a confirmation gate."""

    summary = getattr(runtime, "last_failure_summary", None)
    if isinstance(summary, dict) and summary.get("category") == "confirmation_required":
        return True
    return str(final_response or "").lstrip().startswith("## Confirmation Required")


def _runtime_requires_clarification(runtime: Any, final_response: str) -> bool:
    """Return whether the runtime ended at a clarification gate."""

    summary = getattr(runtime, "last_failure_summary", None)
    if isinstance(summary, dict) and summary.get("category") == "clarification_required":
        return True
    return str(final_response or "").lstrip().startswith("## Clarification Required")


def _runtime_terminal_failure_detail(
    runtime: Any,
    final_response: str,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """Return formatted terminal failure detail from a runtime summary, if present."""

    summary = getattr(runtime, "last_failure_summary", None)
    if not isinstance(summary, dict):
        return None
    category = str(summary.get("category") or "").strip()
    if category in {"confirmation_required", "clarification_required"}:
        return None
    if category not in TERMINAL_RUNTIME_FAILURE_CATEGORIES and "error" not in category:
        return None
    metadata = summary.get("metadata")
    reason = str(summary.get("reason") or final_response or "Request failed.").strip()
    return user_error_detail(
        reason,
        stage=str(summary.get("stage") or "runtime"),
        category=category or "runtime_error",
        context=context,
        metadata=metadata if isinstance(metadata, dict) else None,
        request_id=str(summary.get("request_id") or context.get("request_id") or ""),
    )


def _operator_failure_continuation_info(planning_trace: Any) -> dict[str, Any]:
    """Return safe continuation metadata from a planning trace, if available."""

    metadata = getattr(planning_trace, "metadata", None) if planning_trace is not None else None
    if not isinstance(metadata, dict):
        return {"resumable": False}
    existing = metadata.get("operator_failure_continuation")
    if isinstance(existing, dict) and existing.get("resumable"):
        return dict(existing)
    streaming_state = metadata.get("operator_streaming_state")
    if not isinstance(metadata.get("operator_plan"), dict) and not isinstance(streaming_state, dict):
        return {"resumable": False}
    records = [
        item
        for item in list(metadata.get("operator_execution_records") or [])
        if isinstance(item, dict)
    ]
    successful = [
        str(item.get("action_id") or "")
        for item in records
        if item.get("status") == "success" and str(item.get("action_id") or "").strip()
    ]
    failed = [item for item in records if item.get("status") == "error"]
    if (not successful or not failed) and isinstance(streaming_state, dict):
        records = []
        for task_result in list(streaming_state.get("prior_results") or []):
            if not isinstance(task_result, dict):
                continue
            records.extend(
                item
                for item in list(task_result.get("records") or [])
                if isinstance(item, dict)
            )
        successful = [
            str(item.get("action_id") or "")
            for item in records
            if item.get("status") == "success" and str(item.get("action_id") or "").strip()
        ]
        failed = [item for item in records if item.get("status") == "error"]
    if not successful or not failed:
        return {"resumable": False}
    first_failed = failed[0]
    return {
        "resumable": True,
        "agent_mode": str(metadata.get("agent_mode") or "llm_operator"),
        "conversation_id": str(metadata.get("conversation_id") or ""),
        "parent_request_id": str(metadata.get("parent_request_id") or ""),
        "successful_action_ids": successful,
        "failed_action_id": str(first_failed.get("action_id") or ""),
        "failed_action_kind": str(first_failed.get("kind") or ""),
        "failed_exit_code": first_failed.get("exit_code"),
        "failed_error": str(
            first_failed.get("error")
            or first_failed.get("stderr")
            or first_failed.get("stdout")
            or ""
        ),
        "record_count": len(records),
    }


def _learned_command_ref_from_detail(event_type: str, detail: dict[str, Any]) -> dict[str, str] | None:
    """Return a learned command artifact ref from trace detail, if present."""

    cache_type = str(detail.get("cache_type") or "").strip().lower()
    lr_mode = str(detail.get("lr_mode") or "").strip().lower()
    template_id = str(
        detail.get("template_id")
        or detail.get("command_template_id")
        or detail.get("selected_template_id")
        or ""
    ).strip()
    if not template_id and cache_type in {"command_template", "payload_command_template"}:
        template_id = str(detail.get("cache_id") or "").strip()
    if not template_id:
        return None
    if event_type == "operator.lrdirect.hit":
        if cache_type not in {"", "command_template", "payload_command_template"}:
            return None
        kind = "lr_ex" if cache_type == "payload_command_template" or lr_mode == "lr_ex" else "lr_d"
    elif cache_type == "payload_command_template" or lr_mode == "lr_ex":
        kind = "lr_ex"
    else:
        kind = "command_template"
    return {"artifact_kind": kind, "artifact_id": template_id}


def _remember_learned_command_ref(
    refs: dict[str, dict[str, str]],
    ref: dict[str, str] | None,
) -> str:
    if not ref:
        return ""
    artifact_id = str(ref.get("artifact_id") or "").strip()
    artifact_kind = str(ref.get("artifact_kind") or "command_template").strip()
    if not artifact_id:
        return ""
    key = f"{artifact_kind}:{artifact_id}"
    refs.setdefault(key, {"artifact_kind": artifact_kind, "artifact_id": artifact_id})
    return key


def _planning_trace_metadata(planning_trace: Any) -> dict[str, Any]:
    metadata = getattr(planning_trace, "metadata", None) if planning_trace is not None else None
    return dict(metadata or {}) if isinstance(metadata, dict) else {}


def _metadata_execution_records(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    records = [
        dict(item)
        for item in list(metadata.get("operator_execution_records") or [])
        if isinstance(item, dict)
    ]
    streaming_state = metadata.get("operator_streaming_state")
    if isinstance(streaming_state, dict):
        for task_result in list(streaming_state.get("prior_results") or []):
            if not isinstance(task_result, dict):
                continue
            records.extend(
                dict(item)
                for item in list(task_result.get("records") or [])
                if isinstance(item, dict)
            )
    return records


def _learned_command_failure_refs(
    planning_trace: Any,
    trace: AgentRequestTrace | None,
) -> list[dict[str, str]]:
    """Return learned command refs that failed validation, execution, or repair."""

    refs: dict[str, dict[str, str]] = {}
    failed_keys: set[str] = set()
    metadata = _planning_trace_metadata(planning_trace)
    applied_template = metadata.get("operator_command_template_cache_applied")
    if isinstance(applied_template, dict):
        _remember_learned_command_ref(
            refs,
            _learned_command_ref_from_detail(
                "operator.command_template_cache.hit",
                {
                    "template_id": applied_template.get("template_id"),
                    "cache_type": applied_template.get("cache_type"),
                    "lr_mode": applied_template.get("lr_mode"),
                },
            ),
        )
    applied_direct = metadata.get("operator_lrdirect_applied")
    if isinstance(applied_direct, dict):
        _remember_learned_command_ref(
            refs,
            _learned_command_ref_from_detail(
                "operator.lrdirect.hit",
                {
                    "cache_id": applied_direct.get("cache_id"),
                    "template_id": applied_direct.get("template_id"),
                    "cache_type": applied_direct.get("cache_type"),
                    "lr_mode": applied_direct.get("lr_mode"),
                },
            ),
        )
    if any(str(record.get("status") or "").strip() == "error" for record in _metadata_execution_records(metadata)):
        failed_keys.update(refs.keys())
    for event in list(getattr(trace, "events", []) or []):
        event_type = str(getattr(event, "event_type", "") or "")
        detail = getattr(event, "detail", None)
        detail_payload = dict(detail or {}) if isinstance(detail, dict) else {}
        key = _remember_learned_command_ref(
            refs,
            _learned_command_ref_from_detail(event_type, detail_payload),
        )
        failure_signal = (
            str(getattr(event, "level", "") or "").lower() == "error"
            or event_type.endswith(".failed")
            or "failed" in event_type
            or "rejected" in event_type
        )
        if key and failure_signal:
            failed_keys.add(key)
        elif failure_signal and refs:
            failed_keys.update(refs.keys())
    return [refs[key] for key in refs if key in failed_keys]


def _append_learned_command_failure_notice(
    final_response: str,
    *,
    planning_trace: Any,
    trace: AgentRequestTrace | None,
) -> str:
    """Append a user-facing hint when a learned command artifact failed."""

    text = str(final_response or "").strip()
    if LEARNED_COMMAND_FAILURE_NOTICE in text:
        return text
    refs = _learned_command_failure_refs(planning_trace, trace)
    if not refs:
        return text
    labels = []
    for ref in refs[:3]:
        kind = str(ref.get("artifact_kind") or "command_template").replace("_", "-").upper()
        labels.append(f"{kind} `{ref.get('artifact_id')}`")
    affected = ""
    if labels:
        noun = "step" if len(labels) == 1 else "steps"
        affected = f"\n\nAffected learnt {noun}: {', '.join(labels)}."
    notice = f"## Learnt Command Notice\n\n{LEARNED_COMMAND_FAILURE_NOTICE}{affected}"
    return f"{text}\n\n{notice}".strip() if text else notice


def _clarification_request_from_runtime(runtime: Any) -> dict[str, Any] | None:
    """Extract the typed clarification request from the last planning trace."""

    summary = getattr(runtime, "last_failure_summary", None)
    if isinstance(summary, dict):
        metadata = summary.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("clarification_request"), dict):
            return dict(metadata["clarification_request"])
    planning_trace = getattr(runtime, "last_planning_trace", None)
    metadata = getattr(planning_trace, "metadata", None) if planning_trace is not None else None
    if isinstance(metadata, dict) and isinstance(metadata.get("operator_clarification_request"), dict):
        return dict(metadata["operator_clarification_request"])
    return None


def _clean_header_summary(value: Any) -> str:
    """Normalize a model-authored summary into one compact header line."""

    text = " ".join(str(value or "").replace("\n", " ").split()).strip(" \"'`")
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return text


def _fallback_header_summary(text: str) -> str:
    """Return a conservative one-line summary when no UI LLM is available."""

    lines = [
        " ".join(line.split())
        for line in str(text or "").splitlines()
        if " ".join(line.split())
    ]
    for line in reversed(lines):
        lowered = line.lower()
        if lowered in {"final response", "request", "conversation"}:
            continue
        if lowered.startswith("done:") or lowered.startswith("stdout / stderr"):
            continue
        return _clean_header_summary(line)
    return ""


def _conversation_header_summary(text: str, llm_client: Any) -> str:
    """Ask the configured LLM for a short collapsed Conversation header summary."""

    prompt = "\n".join(
        [
            *prompt_lines("conversation.header_summary"),
            "",
            "Visible conversation text:",
            str(text or "")[-12000:],
        ]
    )
    complete_json = getattr(llm_client, "complete_json", None)
    if callable(complete_json):
        try:
            payload = complete_json(prompt, AgentConversationHeaderSummary.model_json_schema())
            summary = _clean_header_summary(dict(payload or {}).get("summary"))
            if summary:
                return summary
        except Exception:
            pass
    return _fallback_header_summary(text)


def _confirmation_actions_from_runtime(runtime: Any) -> list[dict[str, Any]]:
    """Extract safe confirmation action summaries from the last validated DAG."""

    planning_trace = getattr(runtime, "last_planning_trace", None)
    metadata = getattr(planning_trace, "metadata", None) if planning_trace is not None else None
    if isinstance(metadata, dict):
        operator_actions = metadata.get("operator_confirmation_actions")
        if isinstance(operator_actions, list):
            return [
                dict(action)
                for action in operator_actions
                if isinstance(action, dict)
            ]
    dag_payload = (
        getattr(planning_trace, "dag_validated", None)
        or getattr(planning_trace, "validated_dag", None)
        if planning_trace is not None
        else None
    )
    if dag_payload is None:
        return []
    try:
        from agent_runtime.core.types import ActionDAG

        dag = ActionDAG.model_validate(dag_payload)
        return AgentRuntime._confirmation_actions_for_dag(dag)
    except Exception:
        return []


def _enrich_confirmation_actions_with_gateway(
    actions: list[dict[str, Any]],
    request_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Attach user-visible target gateway metadata to confirmation actions."""

    request_gateway = merge_gateway_metadata(request_context)
    enriched: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        payload = dict(action)
        gateway_metadata = action_gateway_metadata(payload, request_context)
        if request_gateway and not gateway_metadata:
            gateway_metadata = dict(request_gateway)
        if gateway_metadata:
            payload.update(gateway_metadata)
            existing_arguments = payload.get("arguments")
            if isinstance(existing_arguments, dict):
                existing_routing = existing_arguments.get("gateway_routing")
                if not isinstance(existing_routing, dict):
                    existing_routing = {}
                payload["arguments"] = {
                    **existing_arguments,
                    "gateway_routing": {
                        **existing_routing,
                        **gateway_metadata,
                    },
                }
        enriched.append(payload)
    return enriched


_OPERATOR_DIAGNOSTIC_MAX_CHARS = 7000


def _truncate_diagnostic_value(value: Any, max_chars: int = _OPERATOR_DIAGNOSTIC_MAX_CHARS) -> Any:
    redacted = redact_debug_value(value)
    if isinstance(redacted, str) and len(redacted) > max_chars:
        head_chars = max(1, max_chars // 2)
        tail_chars = max(1, max_chars - head_chars)
        return redacted[:head_chars] + "\n...[truncated]\n" + redacted[-tail_chars:]
    return redacted


def _operator_payload_diagnostics(payload: Any) -> dict[str, Any] | None:
    """Return bounded operator execution diagnostics for LLM follow-up context."""

    if not isinstance(payload, dict):
        return None
    if not {"action_id", "kind", "status"}.issubset(payload):
        return None
    diagnostics: dict[str, Any] = {
        "action_id": payload.get("action_id"),
        "task_id": payload.get("task_id"),
        "kind": payload.get("kind"),
        "status": payload.get("status"),
        "exit_code": payload.get("exit_code"),
        "error": _truncate_diagnostic_value(payload.get("error") or ""),
        "stderr": _truncate_diagnostic_value(payload.get("stderr") or ""),
    }
    if str(payload.get("status") or "") == "error":
        diagnostics["stdout"] = _truncate_diagnostic_value(payload.get("stdout") or "")
        diagnostics["output"] = _truncate_diagnostic_value(payload.get("output"))
    return diagnostics


def _conversation_diagnostics_for_context(
    diagnostics: dict[str, Any],
    max_record_chars: int,
) -> dict[str, Any]:
    """Bound diagnostics field-by-field so stderr tails remain visible."""

    text_limit = max(500, int(max_record_chars) // 3)
    bounded: dict[str, Any] = {}
    for key, value in dict(diagnostics or {}).items():
        if key in {"stderr", "stdout", "error", "output"}:
            bounded[key] = _truncate_diagnostic_value(value, text_limit)
        else:
            bounded[key] = _truncate_diagnostic_value(value, 2000)
    return bounded


def _collect_runtime_raw_payloads(runtime: Any, *, include_full_payload: bool) -> dict[str, dict[str, Any]]:
    """Collect raw preview/full-payload records from a completed runtime."""

    execution_engine = getattr(runtime, "execution_engine", None)
    result_store = getattr(execution_engine, "result_store", None)
    if result_store is None or not hasattr(result_store, "list_data_refs"):
        return {}
    raw_payloads: dict[str, dict[str, Any]] = {}
    for data_ref in result_store.list_data_refs():
        ref_id = str(data_ref.ref_id)
        record: dict[str, Any] = {
            "data_ref": ref_id,
            "source_node_id": data_ref.producer_node_id,
            "data_type": data_ref.data_type,
            "metadata": redact_value(data_ref.metadata),
            "preview": redact_value(data_ref.preview),
            "full_payload_available": False,
            "full_payload": None,
        }
        full_payload: Any = None
        full_payload_loaded = False
        try:
            full_payload = result_store.get(ref_id)
            full_payload_loaded = True
        except Exception as exc:
            record["diagnostics_error"] = str(exc)
        diagnostics = _operator_payload_diagnostics(full_payload) if full_payload_loaded else None
        if diagnostics is not None:
            record["diagnostics"] = diagnostics
        if include_full_payload:
            if full_payload_loaded:
                record["full_payload"] = redact_value(full_payload)
                record["full_payload_available"] = True
            else:
                record["error"] = record.get("diagnostics_error", "Full payload is unavailable.")
        raw_payloads[ref_id] = record
    return raw_payloads


_GENERIC_TERMINAL_OUTPUT_RESPONSE = (
    "Task completed. Detailed output is available in the terminal or command output capsule."
)


def _event_text_preview(value: Any, *, limit: int = 1200) -> str:
    """Return compact event output text for run-history previews."""

    text = str(value or "").strip()
    if not text:
        return ""
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "\n...[truncated]"
    return text


def _event_output_sections_from_trace(trace: AgentRequestTrace | None) -> list[str]:
    """Return markdown sections with captured scheduled-event command output."""

    if trace is None:
        return []
    sections: list[str] = []
    for payload in dict(trace.raw_payloads or {}).values():
        if not isinstance(payload, dict):
            continue
        sources = []
        preview = payload.get("preview")
        diagnostics = payload.get("diagnostics")
        if isinstance(preview, dict):
            sources.append(preview)
        if isinstance(diagnostics, dict):
            sources.append(diagnostics)
        for source in sources:
            action_label = str(
                source.get("action_id")
                or payload.get("source_node_id")
                or "action"
            ).strip()
            lines = [f"### {action_label}", ""]
            if source.get("status"):
                lines.append(f"- Status: `{source.get('status')}`")
            if source.get("exit_code") is not None:
                lines.append(f"- Exit code: `{source.get('exit_code')}`")
            stdout = _event_text_preview(source.get("stdout"))
            if stdout:
                lines.extend(["", "Stdout:", "", f"```text\n{stdout}\n```"])
            output = source.get("output")
            if output not in (None, "", []):
                output_text = _event_text_preview(
                    json.dumps(output, default=str, ensure_ascii=True),
                )
                lines.extend(["", "Output:", "", f"```text\n{output_text}\n```"])
            stderr = _event_text_preview(source.get("stderr"))
            if stderr:
                lines.extend(["", "Stderr:", "", f"```text\n{stderr}\n```"])
            error = _event_text_preview(source.get("error"))
            if error:
                lines.extend(["", f"Error: `{error}`"])
            if len(lines) > 2:
                sections.append("\n".join(lines))
        preview_text = _event_text_preview(payload.get("preview_text"), limit=800)
        if preview_text:
            sections.append(f"### {payload.get('source_node_id') or 'output'}\n\n```text\n{preview_text}\n```")
    return sections


def _event_enriched_final_response(trace: AgentRequestTrace | None) -> str:
    """Return a scheduled-event final response with captured command output included."""

    if trace is None:
        return ""
    final_response = str(trace.final_response or "").strip()
    sections = _event_output_sections_from_trace(trace)
    if not sections:
        return final_response
    if any(section and section in final_response for section in sections):
        return final_response
    meaningful_sections = [
        section
        for section in sections
        if "Stdout:" in section or "Stderr:" in section or "Output:" in section or "Error:" in section
    ]
    if not meaningful_sections:
        return final_response or "Command completed successfully with no stdout."
    base = "" if final_response == _GENERIC_TERMINAL_OUTPUT_RESPONSE else final_response
    if base:
        return f"{base}\n\n## Event Output\n\n" + "\n\n".join(meaningful_sections)
    return "## Event Output\n\n" + "\n\n".join(meaningful_sections)


def _event_result_preview_from_trace(trace: AgentRequestTrace | None) -> str:
    """Return a useful scheduled-event result preview with captured output."""

    final_response = _event_enriched_final_response(trace)
    if final_response:
        return final_response
    if trace is None:
        return ""
    successful_empty_output = False
    for payload in dict(trace.raw_payloads or {}).values():
        if not isinstance(payload, dict):
            continue
        preview = payload.get("preview")
        diagnostics = payload.get("diagnostics")
        for source in (preview, diagnostics):
            if isinstance(source, dict) and (
                str(source.get("status") or "") == "success" or source.get("exit_code") == 0
            ):
                successful_empty_output = True
    if successful_empty_output:
        return "Command completed successfully with no stdout."
    final_response = str(trace.final_response or "").strip()
    return final_response


def _json_size(value: Any) -> int:
    """Return a conservative JSON character size for one context payload."""

    try:
        return len(json.dumps(value, default=str, ensure_ascii=True))
    except Exception:
        return len(str(value))


def _truncate_context_value(value: Any, max_chars: int) -> Any:
    """Return a bounded value suitable for LLM follow-up context."""

    limit = max(1, int(max_chars))
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + "\n...[truncated]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    try:
        text = json.dumps(value, default=str, ensure_ascii=True, indent=2)
    except Exception:
        text = str(value)
    if len(text) <= limit:
        return value
    return text[:limit] + "\n...[truncated]"


def _conversation_raw_payloads_for_context(
    trace: AgentRequestTrace,
    *,
    include_full_payload: bool,
    max_record_chars: int,
) -> list[dict[str, Any]]:
    """Return policy-bounded raw payload records for one prior turn."""

    records: list[dict[str, Any]] = []
    for data_ref, payload in dict(trace.raw_payloads or {}).items():
        if not isinstance(payload, dict):
            continue
        value_source = "preview"
        value = payload.get("preview")
        if include_full_payload and payload.get("full_payload_available"):
            value_source = "full_payload"
            value = payload.get("full_payload")
        record = {
            "data_ref": data_ref,
            "source_node_id": payload.get("source_node_id"),
            "data_type": payload.get("data_type"),
            "metadata": payload.get("metadata") or {},
            "value_source": value_source,
            "value": _truncate_context_value(value, max_record_chars),
        }
        diagnostics = payload.get("diagnostics")
        if isinstance(diagnostics, dict):
            record["diagnostics"] = _conversation_diagnostics_for_context(
                diagnostics,
                max_record_chars,
            )
        records.append(record)
    return records


def _build_operator_conversation_context(
    *,
    conversation: AgentConversation,
    trace_store: AgentTraceStore,
    settings: Settings,
) -> dict[str, Any]:
    """Build a bounded, policy-filtered LLM context for an operator follow-up."""

    max_turns = max(1, int(settings.llm_operator_conversation_max_turns))
    max_context_chars = max(1, int(settings.llm_operator_conversation_max_context_chars))
    max_record_chars = max(1, int(settings.llm_operator_conversation_max_record_chars))
    include_full_payload = bool(settings.agent_ui_allow_full_payloads)

    base: dict[str, Any] = {
        "conversation_id": conversation.conversation_id,
        "full_payloads_included": include_full_payload,
        "max_context_chars": max_context_chars,
        "max_record_chars": max_record_chars,
        "turns": [],
        "truncated": False,
        "dropped_turn_count": 0,
    }

    candidate_turns: list[dict[str, Any]] = []
    for turn in conversation.turns[-max_turns:]:
        trace = trace_store.get_trace(turn.request_id)
        if trace is not None:
            candidate_turns.append(
                {
                    "request_id": turn.request_id,
                    "prompt": _truncate_context_value(trace.prompt, max_record_chars),
                    "status": trace.status,
                    "confirmation_required": trace.confirmation_required,
                    "clarification_required": trace.clarification_required,
                    "clarification_request": trace.clarification_request,
                    "final_response": _truncate_context_value(
                        trace.final_response or trace.error or "",
                        max_record_chars,
                    ),
                    "raw_payloads": _conversation_raw_payloads_for_context(
                        trace,
                        include_full_payload=include_full_payload,
                        max_record_chars=max_record_chars,
                    ),
                }
            )
            continue
        candidate_turns.append(
            {
                "request_id": turn.request_id,
                "prompt": _truncate_context_value(turn.prompt, max_record_chars),
                "status": turn.status,
                "confirmation_required": turn.confirmation_required,
                "clarification_required": turn.clarification_required,
                "clarification_request": None,
                "final_response": _truncate_context_value(turn.final_response, max_record_chars),
                "raw_payloads": [],
                "source": "chat_history",
            }
        )

    kept_reversed: list[dict[str, Any]] = []
    dropped = 0
    for turn_payload in reversed(candidate_turns):
        proposed = {**base, "turns": [turn_payload, *reversed(kept_reversed)]}
        if _json_size(proposed) <= max_context_chars:
            kept_reversed.append(turn_payload)
            continue
        dropped += 1
        if not kept_reversed:
            reduced = dict(turn_payload)
            reduced["raw_payloads"] = []
            reduced["final_response"] = _truncate_context_value(
                reduced.get("final_response", ""),
                max(400, max_record_chars // 4),
            )
            kept_reversed.append(reduced)

    turns = list(reversed(kept_reversed))
    context = {**base, "turns": turns}
    if dropped:
        context["truncated"] = True
        context["dropped_turn_count"] = dropped
    if _json_size(context) > max_context_chars:
        context["truncated"] = True
        context["turns"] = context["turns"][-1:]
        for turn_payload in context["turns"]:
            turn_payload["raw_payloads"] = []
            turn_payload["final_response"] = _truncate_context_value(
                turn_payload.get("final_response", ""),
                max(400, max_context_chars // 2),
            )
    context["context_chars"] = _json_size(context)
    return context


def _advisory_gateway_context(request_context: dict[str, Any]) -> dict[str, Any]:
    """Return the gateway facts the Advisory prompt is allowed to use."""

    fields = (
        "gateway_id",
        "gateway_node",
        "gateway_platform",
        "gateway_platform_label",
        "gateway_platform_version",
        "gateway_architecture",
        "gateway_shell",
        "gateway_command_profile",
        "gateway_capability_tags",
        "gateway_routing",
    )
    context = {
        key: request_context.get(key)
        for key in fields
        if request_context.get(key) not in (None, "", [])
    }
    if "gateway_platform_label" not in context:
        platform = str(context.get("gateway_platform") or "").strip().lower()
        if platform == "macos":
            context["gateway_platform_label"] = "macOS"
        elif platform == "linux":
            context["gateway_platform_label"] = "Linux"
        elif platform == "windows":
            context["gateway_platform_label"] = "Windows"
        elif platform:
            context["gateway_platform_label"] = platform
        else:
            context["gateway_platform_label"] = "Unknown"
    return context


def _capped_advisory_terminal_output(value: Any) -> str:
    """Return a bounded terminal snapshot for Advisory prompt context."""

    text = str(value or "").strip()
    if len(text) <= ADVISORY_TERMINAL_OUTPUT_MAX_CHARS:
        return text
    return text[-ADVISORY_TERMINAL_OUTPUT_MAX_CHARS:]


def _advisory_prompt(
    *,
    prompt: str,
    request_context: dict[str, Any],
    settings: Settings,
) -> str:
    """Build the DB-backed Advisory mode prompt."""

    gateway_context = _advisory_gateway_context(request_context)
    terminal_cwd = str(
        request_context.get("terminal_cwd")
        or request_context.get("advisory_terminal_cwd")
        or request_context.get("cwd")
        or settings.workspace_root
        or ""
    ).strip()
    context_payload = {
        "product_identity": OPENFABRIC_PRODUCT_IDENTITY,
        "workspace_root": str(settings.workspace_root),
        "terminal_cwd": terminal_cwd,
        "gateway": gateway_context,
        "conversation": request_context.get("advisory_conversation_context")
        or request_context.get("operator_conversation_context")
        or {},
    }
    if request_context.get("advisory_terminal_context_enabled") is True:
        context_payload["advisory_terminal_context_enabled"] = True
        context_payload["advisory_terminal_output"] = _capped_advisory_terminal_output(
            request_context.get("advisory_terminal_output")
        )
    return "\n".join(
        [
            *prompt_lines("advisory.answer"),
            "",
            "Structured response schema:",
            json.dumps(AdvisoryResponse.model_json_schema(), indent=2, sort_keys=True),
            "",
            "Runtime context JSON:",
            json.dumps(context_payload, indent=2, sort_keys=True, default=str),
            "",
            "User prompt:",
            str(prompt or ""),
        ]
    )


def _normalize_advisory_response(raw: Any) -> AdvisoryResponse:
    """Return a clean AdvisoryResponse, tolerating minor model shape drift."""

    payload = dict(raw or {}) if isinstance(raw, dict) else {}
    if "answer" in payload and "answer_markdown" not in payload:
        payload["answer_markdown"] = payload.get("answer")
    snippets: list[AdvisorySnippet] = []
    for index, item in enumerate(list(payload.get("snippets") or []), start=1):
        if not isinstance(item, dict):
            continue
        snippet_payload = dict(item)
        if "snippet_id" not in snippet_payload and "id" in snippet_payload:
            snippet_payload["snippet_id"] = snippet_payload.get("id")
        try:
            snippet = AdvisorySnippet.model_validate(snippet_payload)
        except Exception:
            continue
        code = str(snippet.code or "").strip()
        if not code:
            continue
        snippet.snippet_id = str(snippet.snippet_id or f"snippet_{index}").strip() or f"snippet_{index}"
        snippet.title = str(snippet.title or f"Snippet {index}").strip() or f"Snippet {index}"
        snippet.language = str(snippet.language or "text").strip() or "text"
        snippet.code = code
        snippets.append(snippet)
        if len(snippets) >= 12:
            break
    answer = str(payload.get("answer_markdown") or "").strip()
    if not answer:
        answer = "I can advise on the next steps, but I do not have enough context to give a specific answer."
    return AdvisoryResponse(answer_markdown=answer, snippets=snippets)


def _advisory_display_document(
    *,
    request_id: str,
    response: AdvisoryResponse,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    """Return an Agent UI display document that carries Advisory snippet metadata."""

    snippets = [snippet.model_dump(mode="json") for snippet in response.snippets]
    gateway_context = _advisory_gateway_context(request_context)
    return {
        "document_id": f"advisory-{request_id}",
        "request_id": request_id,
        "target_ui": "agent_ui",
        "summary": "Advisory response",
        "raw_available": False,
        "trace_refs": [],
        "sections": [
            {
                "section_id": "advisory-answer",
                "title": "Advisory",
                "primitive_id": "markdown",
                "display_type": "markdown",
                "shape_type": "text",
                "content": response.answer_markdown,
                "rows": [],
                "columns": [],
                "metadata": {
                    "advisory": True,
                    "snippet_count": len(snippets),
                },
                "language": "markdown",
                "truncated": False,
                "preview_count": None,
                "total_count": None,
                "data_ref": None,
                "raw_available": False,
            }
        ],
        "metadata": {
            "advisory": True,
            "snippets": snippets,
            "gateway": gateway_context,
        },
    }


def _append_unique_chat_tag(tags: list[str], value: Any) -> None:
    tag = " ".join(str(value or "").replace("_", " ").split()).strip().lower()
    if tag and tag not in tags:
        tags.append(tag)


def _chat_tags(chat: AgentConversation) -> list[str]:
    tags: list[str] = []
    turns = list(getattr(chat, "turns", []) or [])
    last_turn = turns[-1] if turns else None
    status = (
        str(getattr(chat, "last_status", "") or "").strip()
        or str(getattr(last_turn, "status", "") or "").strip()
        or "saved"
    )
    _append_unique_chat_tag(tags, status)
    turn_count = int(getattr(chat, "turn_count", 0) or len(turns) or 0)
    if turn_count > 1:
        _append_unique_chat_tag(tags, "multi turn")
    elif turn_count == 1:
        _append_unique_chat_tag(tags, "single turn")
    if bool(getattr(chat, "last_confirmation_required", False)) or bool(
        getattr(last_turn, "confirmation_required", False)
    ):
        _append_unique_chat_tag(tags, "needs approval")
    if bool(getattr(chat, "last_clarification_required", False)) or bool(
        getattr(last_turn, "clarification_required", False)
    ):
        _append_unique_chat_tag(tags, "needs input")
    if str(getattr(chat, "last_preview", "") or "").strip() or (
        last_turn is not None and str(getattr(last_turn, "final_response", "") or "").strip()
    ):
        _append_unique_chat_tag(tags, "has response")
    return tags


def _chat_turn_payload(turn: Any) -> dict[str, Any]:
    return {
        "request_id": str(getattr(turn, "request_id", "") or ""),
        "prompt": str(getattr(turn, "prompt", "") or ""),
        "final_response": str(getattr(turn, "final_response", "") or ""),
        "status": str(getattr(turn, "status", "") or ""),
        "confirmation_required": bool(getattr(turn, "confirmation_required", False)),
        "clarification_required": bool(getattr(turn, "clarification_required", False)),
        "display_document": getattr(turn, "display_document", None),
        "response_metrics": getattr(turn, "response_metrics", None),
        "learning_summary": getattr(turn, "learning_summary", None),
        "created_at": str(getattr(turn, "created_at", "") or ""),
        "updated_at": str(getattr(turn, "updated_at", "") or ""),
    }


def _chat_summary_payload(chat: AgentConversation) -> dict[str, Any]:
    response_metrics = getattr(chat, "last_response_metrics", None)
    if response_metrics is None and chat.turns:
        response_metrics = getattr(chat.turns[-1], "response_metrics", None)
    return {
        "conversation_id": chat.conversation_id,
        "title": chat.title or "Untitled chat",
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "last_request_id": chat.last_request_id,
        "last_status": chat.last_status,
        "last_preview": chat.last_preview,
        "response_metrics": response_metrics,
        "turn_count": chat.turn_count,
        "tags": _chat_tags(chat),
    }


def _chat_detail_payload(chat: AgentConversation) -> dict[str, Any]:
    return {**_chat_summary_payload(chat), "turns": [_chat_turn_payload(turn) for turn in chat.turns]}


def _learning_lesson_trace_detail(lesson: Any) -> dict[str, Any]:
    """Return a compact trace payload for one draft learning lesson."""

    return {
        "lesson_id": str(getattr(lesson, "lesson_id", "") or ""),
        "status": str(getattr(lesson, "status", "") or ""),
        "auto_approved": bool(getattr(lesson, "auto_approved", False)),
        "lesson_type": str(getattr(lesson, "lesson_type", "") or ""),
        "title": str(getattr(lesson, "title", "") or ""),
        "summary": str(getattr(lesson, "summary", "") or ""),
        "instruction": str(getattr(lesson, "instruction", "") or ""),
        "confidence": float(getattr(lesson, "confidence", 0.0) or 0.0),
        "source_request_id": str(getattr(lesson, "source_request_id", "") or ""),
        "mirrored_memory_id": str(getattr(lesson, "mirrored_memory_id", "") or ""),
        "tags": list(getattr(lesson, "tags", []) or []),
    }


def _capability_proposal_trace_detail(proposal: Any) -> dict[str, Any]:
    """Return a compact trace payload for one capability evolution proposal."""

    return {
        "proposal_id": str(getattr(proposal, "proposal_id", "") or ""),
        "status": str(getattr(proposal, "status", "") or ""),
        "target_kind": str(getattr(proposal, "target_kind", "") or ""),
        "title": str(getattr(proposal, "title", "") or ""),
        "summary": str(getattr(proposal, "summary", "") or ""),
        "confidence": float(getattr(proposal, "confidence", 0.0) or 0.0),
        "source": str(getattr(proposal, "source", "") or ""),
        "source_request_id": str(getattr(proposal, "source_request_id", "") or ""),
        "target_id": str(getattr(proposal, "target_id", "") or ""),
        "auto_approved": bool(getattr(proposal, "auto_approved", False)),
        "applied_ref": str(getattr(proposal, "applied_ref", "") or ""),
        "apply_error": str(getattr(proposal, "apply_error", "") or ""),
        "safety_decision": dict(getattr(proposal, "safety_decision", {}) or {}),
    }


def _append_capability_proposal_trace_event(
    *,
    trace_store: AgentTraceStore,
    proposal: Any,
    event_type: str,
    title: str,
    summary: str,
    level: str = "info",
) -> None:
    """Append a proposal lifecycle event to the source request trace when available."""

    request_id = str(getattr(proposal, "source_request_id", "") or "").strip()
    if not request_id:
        return
    try:
        trace_store.append_event(
            AgentTraceEvent(
                request_id=request_id,
                stage="learning",
                level=level,
                event_type=event_type,
                title=title,
                summary=summary,
                detail=_capability_proposal_trace_detail(proposal),
            )
        )
    except KeyError:
        return


def _auto_learning_lesson_is_safe(lesson: Any) -> bool:
    """Return whether a draft lesson is safe enough for automatic memory promotion."""

    if str(getattr(lesson, "status", "") or "") != "draft":
        return False
    try:
        confidence = float(getattr(lesson, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.7:
        return False
    text = f"{getattr(lesson, 'title', '')}\n{getattr(lesson, 'summary', '')}\n{getattr(lesson, 'instruction', '')}".lower()
    blocked = (
        "skip approval",
        "bypass approval",
        "disable approval",
        "skip validation",
        "bypass validation",
        "disable validation",
        "ignore safety",
        "ignore policy",
        "disable sandbox",
        "bypass sandbox",
        "without confirmation",
        "never ask for approval",
        "api key",
        "password",
        "secret",
        "token",
    )
    return not any(fragment in text for fragment in blocked)


def _auto_capability_proposal_is_safe(proposal: Any) -> bool:
    """Return whether one proposal may be approved by auto-learning."""

    if str(getattr(proposal, "status", "") or "") != "draft":
        return False
    try:
        confidence = float(getattr(proposal, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    source = str(getattr(proposal, "source", "") or "").strip().lower()
    threshold = 0.85 if source == "llm" else 0.72
    if confidence < threshold:
        return False
    safety = dict(getattr(proposal, "safety_decision", {}) or {})
    if safety and safety.get("safe") is False:
        return False
    text = "\n".join(
        [
            str(getattr(proposal, "title", "") or ""),
            str(getattr(proposal, "summary", "") or ""),
            str(getattr(proposal, "rationale", "") or ""),
            json_dumps(getattr(proposal, "draft", {}) or {}),
        ]
    ).lower()
    blocked = (
        "skip approval",
        "bypass approval",
        "disable approval",
        "skip validation",
        "bypass validation",
        "disable validation",
        "ignore safety",
        "ignore policy",
        "disable sandbox",
        "bypass sandbox",
        "without confirmation",
        "never ask for approval",
        "api key",
        "password",
        "secret",
        "token",
        "lower risk",
        "remove confirmation",
        "hot load",
        "auto apply executable",
    )
    return not any(fragment in text for fragment in blocked)


def _record_learning_ledger_for_trace(
    *,
    learning_ledger_store: AgentLearningLedgerStore | None,
    memory_store: AgentMemoryStore | None = None,
    prompt_template_store: PromptTemplateStore | None = None,
    trace_store: AgentTraceStore,
    trace: AgentRequestTrace | None,
    request_context: dict[str, Any],
    planning_trace: Any = None,
    auto_learn_enabled: bool = True,
) -> None:
    """Persist learning evidence and emit user-visible draft lesson events."""

    if learning_ledger_store is None or trace is None:
        return
    try:
        lessons = analyze_and_record_run(
            store=learning_ledger_store,
            trace=trace,
            request_context=request_context,
            planning_trace=planning_trace,
        )
    except Exception:
        return
    auto_memory_promotion_done = False
    for lesson in lessons:
        emitted_lesson = lesson
        auto_learned = False
        if auto_learn_enabled and memory_store is not None and _auto_learning_lesson_is_safe(lesson):
            try:
                approved = learning_ledger_store.approve_lesson(
                    lesson.lesson_id,
                    memory_store=memory_store,
                    actor="auto-learning",
                    auto_approved=True,
                )
                if approved is not None:
                    emitted_lesson = approved
                    auto_learned = True
                    auto_memory_promotion_done = True
            except Exception:
                auto_learned = False
        detail = _learning_lesson_trace_detail(emitted_lesson)
        if not detail["lesson_id"]:
            continue
        event_type = "learning.lesson.learned" if auto_learned else "learning.lesson.proposed"
        title = "Learning saved" if auto_learned else "Learning draft proposed"
        summary = (
            "The agent safely remembered this lesson automatically."
            if auto_learned
            else "The agent noticed something reusable and is asking whether to remember it."
        )
        try:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="learning",
                    level="info",
                    event_type=event_type,
                    title=title,
                    summary=summary,
                    detail=detail,
                )
            )
        except KeyError:
            return
    for proposal in learning_ledger_store.list_proposals(
        source_request_id=trace.request_id,
        limit=100,
    ):
        emitted_proposal = proposal
        auto_learned = False
        duplicate_legacy_memory = (
            auto_memory_promotion_done
            and str(getattr(proposal, "source", "") or "") == "deterministic"
            and str(getattr(proposal, "target_kind", "") or "") == "task_memory"
        )
        if (
            auto_learn_enabled
            and not duplicate_legacy_memory
            and _auto_capability_proposal_is_safe(proposal)
        ):
            try:
                approved = learning_ledger_store.approve_proposal(
                    proposal.proposal_id,
                    memory_store=memory_store,
                    prompt_template_store=prompt_template_store,
                    actor="auto-learning",
                    auto_approved=True,
                )
                if approved is not None:
                    emitted_proposal = approved
                    auto_learned = True
            except Exception:
                auto_learned = False
        detail = _capability_proposal_trace_detail(emitted_proposal)
        if not detail["proposal_id"]:
            continue
        event_type = (
            "learning.proposal.auto_approved"
            if auto_learned
            else "learning.proposal.proposed"
        )
        title = "Capability proposal saved" if auto_learned else "Capability proposal drafted"
        summary = (
            "The agent safely applied or approved this capability evolution proposal."
            if auto_learned
            else "The agent drafted a future capability improvement for review."
        )
        try:
            trace_store.append_event(
                AgentTraceEvent(
                    request_id=trace.request_id,
                    stage="learning",
                    level="info",
                    event_type=event_type,
                    title=title,
                    summary=summary,
                    detail=detail,
                )
            )
        except KeyError:
            return


def _run_agent_request(
    *,
    request_id: str,
    prompt: str,
    request_context: dict[str, Any],
    settings: Settings,
    base_runtime: Any,
    store: AgentTraceStore,
    state_store: AgentUiRequestStateStore | None = None,
    conversation_store: AgentConversationStore | None = None,
    confirmation: bool = False,
    continuation: bool = False,
    planning_trace: Any = None,
    cancel_event: threading.Event | None = None,
    learning_ledger_store: AgentLearningLedgerStore | None = None,
) -> None:
    """Run one request in a background worker and store trace events."""

    if cancel_event is not None and cancel_event.is_set():
        store.cancel_request(request_id)
        return
    store.mark_running(request_id)
    sink = AgentTraceSink(store, request_id)
    requested_context = dict(request_context or {})
    auto_learn_enabled = bool(
        requested_context.get(
            "agent_learning_ledger_auto_learn_enabled",
            getattr(settings, "agent_learning_ledger_auto_learn_enabled", True),
        )
    )
    if learning_ledger_store is not None:
        try:
            requested_context.setdefault(
                "learning_ledger_suspect_plan_cache_ids",
                sorted(learning_ledger_store.suspect_cache_ids("plan")),
            )
            requested_context.setdefault(
                "learning_ledger_suspect_computation_cache_ids",
                sorted(learning_ledger_store.suspect_cache_ids("computation")),
            )
            requested_context.setdefault(
                "learning_ledger_suspect_command_template_cache_ids",
                sorted(learning_ledger_store.suspect_cache_ids("command_template")),
            )
        except Exception:
            pass
    include_full_payload = bool(settings.agent_ui_allow_full_payloads)
    allow_full_output_access = bool(requested_context.get("allow_full_output_access", False)) and include_full_payload
    context = {
        **requested_context,
        "request_id": request_id,
        "gateway_execution_id": request_id,
        "workspace_root": str(settings.workspace_root),
        "confirmation": bool(confirmation),
        "llm_context_window_tokens": int(
            _positive_int(requested_context.get("llm_context_window_tokens"))
            or settings.llm_context_window_tokens
        ),
        "allow_full_output_access": allow_full_output_access,
        "allow_raw_preview": bool(settings.agent_ui_allow_raw_previews),
        "target_ui": "agent_ui",
        "cancel_event": cancel_event,
        "observability": {
            "enabled": True,
            "debug": True,
            "redaction_policy": "standard",
            "sinks": [sink],
        },
    }
    reasoning_profile = normalize_reasoning_profile(context.get("reasoning_profile"))
    store.append_event(
        AgentTraceEvent(
            request_id=request_id,
            stage="request_received",
            level="info",
            event_type="operator.reasoning_profile",
            title="Reasoning profile",
            summary=f"Reasoning profile is {reasoning_profile}.",
            detail={"reasoning_profile": reasoning_profile},
        )
    )
    try:
        if cancel_event is not None and cancel_event.is_set():
            store.cancel_request(request_id)
            return
        if (
            confirmation
            and isinstance(planning_trace, dict)
            and planning_trace.get("v1_fast_path") == "exact_file_write"
        ):
            content = _run_v1_exact_file_write(
                request_id=request_id,
                context=context,
                settings=settings,
                store=store,
                planning_trace=planning_trace,
            )
            display_document = None
            confirmation_required = False
            confirmation_actions = []
            clarification_required = False
            clarification_request = None
            latest_planning_trace = planning_trace
            if state_store is not None:
                state_store.finish(
                    request_id,
                    planning_trace=latest_planning_trace,
                    confirmation_required=False,
                    confirmation_actions=[],
                    clarification_required=False,
                    clarification_request=None,
                )
            store.complete_request(request_id, str(content or ""))
            if conversation_store is not None:
                conversation_id = str(requested_context.get("conversation_id") or "").strip()
                if conversation_id and conversation_store.exists(conversation_id):
                    conversation_store.append_turn(
                        conversation_id,
                        store.get_trace(request_id),
                    )
            return
        if not continuation and not confirmation:
            write_request = _v1_exact_file_write_request(prompt, settings.workspace_root)
            if write_request is not None:
                _v1_file_write_confirmation(
                    request_id=request_id,
                    prompt=prompt,
                    context=context,
                    settings=settings,
                    store=store,
                    state_store=state_store,
                    conversation_store=conversation_store,
                    write_request=write_request,
                )
                return
            if _v1_repo_summary_requested(prompt):
                output, exit_code = _run_v1_read_only_repo_capsule(
                    request_id=request_id,
                    store=store,
                    context=context,
                    workspace_root=settings.workspace_root,
                )
                if exit_code not in {0, 1}:
                    store.fail_request(request_id, "Repo summary capsule failed.")
                    return
                content = _repo_summary_from_capsule(output, settings.workspace_root)
                if state_store is not None:
                    state_store.finish(
                        request_id,
                        planning_trace={"v1_fast_path": "repo_summary"},
                        confirmation_required=False,
                        confirmation_actions=[],
                    )
                store.complete_request(request_id, content)
                if conversation_store is not None:
                    conversation_store.append_turn(
                        requested_context.get("conversation_id"),
                        store.get_trace(request_id),
                    )
                return
        runtime = _build_traced_runtime(
            settings=settings,
            base_runtime=base_runtime,
            store=store,
            request_id=request_id,
            llm_model=str(requested_context.get("llm_model") or "").strip() or None,
            llm_base_url=str(requested_context.get("llm_base_url") or "").strip() or None,
            llm_timeout_seconds=_positive_float(requested_context.get("llm_timeout_seconds")),
            llm_max_tokens=_nonnegative_int(requested_context.get("llm_max_tokens")),
            response_streaming_enabled=bool(requested_context.get("response_streaming_enabled")),
        )
        if continuation and planning_trace is not None and hasattr(runtime, "continue_from_trace"):
            continuation_trace = _copy_planning_trace_for_request(planning_trace, request_id)
            metadata = getattr(continuation_trace, "metadata", None)
            if isinstance(metadata, dict):
                metadata["parent_request_id"] = str(requested_context.get("parent_request_id") or "")
            content = runtime.continue_from_trace(
                continuation_trace,
                context=context,
            )
        elif confirmation and planning_trace is not None and hasattr(runtime, "replay_from_trace"):
            content = runtime.replay_from_trace(
                _copy_planning_trace_for_request(planning_trace, request_id),
                context=context,
            )
        else:
            content = runtime.handle_request(prompt, context=context)
        if cancel_event is not None and cancel_event.is_set():
            store.cancel_request(request_id)
            return
        display_document = getattr(runtime, "last_display_document", None)
        confirmation_required = _runtime_requires_confirmation(runtime, str(content or ""))
        confirmation_actions = (
            _confirmation_actions_from_runtime(runtime) if confirmation_required else []
        )
        confirmation_actions = _enrich_confirmation_actions_with_gateway(
            confirmation_actions,
            context,
        )
        clarification_required = _runtime_requires_clarification(runtime, str(content or ""))
        clarification_request = (
            _clarification_request_from_runtime(runtime) if clarification_required else None
        )
        if settings.agent_ui_allow_raw_previews:
            store.attach_raw_payloads(
                request_id,
                _collect_runtime_raw_payloads(runtime, include_full_payload=include_full_payload),
            )
        latest_planning_trace = (
            getattr(runtime, "last_planning_trace", None)
            or getattr(base_runtime, "last_planning_trace", None)
            or planning_trace
        )
        if state_store is not None:
            state_store.finish(
                request_id,
                planning_trace=latest_planning_trace,
                confirmation_required=confirmation_required,
                confirmation_actions=confirmation_actions,
                clarification_required=clarification_required,
                clarification_request=clarification_request,
            )
        terminal_failure_detail = _runtime_terminal_failure_detail(runtime, str(content or ""), context)
        if terminal_failure_detail is not None:
            failure_message = user_error_message(terminal_failure_detail, content)
            failure_message = _append_learned_command_failure_notice(
                failure_message,
                planning_trace=latest_planning_trace,
                trace=store.get_trace(request_id),
            )
            store.fail_request(
                request_id,
                failure_message,
                error_detail=terminal_failure_detail,
            )
            if conversation_store is not None:
                conversation_id = str(requested_context.get("conversation_id") or "").strip()
                if conversation_id and conversation_store.exists(conversation_id):
                    conversation_store.append_turn(
                        conversation_id,
                        store.get_trace(request_id),
                    )
            _record_learning_ledger_for_trace(
                learning_ledger_store=learning_ledger_store,
                memory_store=_memory_store_for_learning(settings, base_runtime),
                prompt_template_store=PromptTemplateStore(settings.agent_prompts_db_path),
                trace_store=store,
                trace=store.get_trace(request_id),
                request_context=context,
                planning_trace=latest_planning_trace,
                auto_learn_enabled=auto_learn_enabled,
            )
            return
        final_content = _append_learned_command_failure_notice(
            str(content or ""),
            planning_trace=latest_planning_trace,
            trace=store.get_trace(request_id),
        )
        store.complete_request(
            request_id,
            final_content,
            display_document if isinstance(display_document, dict) else None,
            confirmation_required=confirmation_required,
            confirmation_actions=confirmation_actions,
            clarification_required=clarification_required,
            clarification_request=clarification_request,
        )
        if conversation_store is not None:
            conversation_id = str(requested_context.get("conversation_id") or "").strip()
            if conversation_id and conversation_store.exists(conversation_id):
                conversation_store.append_turn(
                    conversation_id,
                    store.get_trace(request_id),
                )
        completed_trace = store.get_trace(request_id)
        _record_learning_ledger_for_trace(
            learning_ledger_store=learning_ledger_store,
            memory_store=_memory_store_for_learning(settings, base_runtime),
            prompt_template_store=PromptTemplateStore(settings.agent_prompts_db_path),
            trace_store=store,
            trace=completed_trace,
            request_context=context,
            planning_trace=latest_planning_trace,
            auto_learn_enabled=auto_learn_enabled,
        )
        if conversation_store is not None:
            conversation_id = str(requested_context.get("conversation_id") or "").strip()
            if conversation_id and conversation_store.exists(conversation_id):
                conversation_store.append_turn(
                    conversation_id,
                    store.get_trace(request_id),
                )
    except Exception as exc:  # pragma: no cover - safety boundary for worker threads
        if cancel_event is not None and cancel_event.is_set():
            store.cancel_request(request_id)
            return
        detail = user_error_detail(
            exc,
            stage="runtime",
            category="unexpected_error",
            context=context,
            request_id=request_id,
        )
        store.fail_request(request_id, user_error_message(detail, exc), error_detail=detail)
        _record_learning_ledger_for_trace(
            learning_ledger_store=learning_ledger_store,
            memory_store=_memory_store_for_learning(settings, base_runtime),
            prompt_template_store=PromptTemplateStore(settings.agent_prompts_db_path),
            trace_store=store,
            trace=store.get_trace(request_id),
            request_context=context,
            planning_trace=planning_trace,
            auto_learn_enabled=auto_learn_enabled,
        )
        if conversation_store is not None:
            conversation_store.append_turn(
                dict(request_context or {}).get("conversation_id"),
                store.get_trace(request_id),
            )


def _run_advisory_request(
    *,
    request_id: str,
    prompt: str,
    request_context: dict[str, Any],
    settings: Settings,
    base_runtime: Any,
    store: AgentTraceStore,
    conversation_store: AgentConversationStore | None = None,
    cancel_event: threading.Event | None = None,
    learning_ledger_store: AgentLearningLedgerStore | None = None,
) -> None:
    """Run one Advisory mode request without invoking operator planning/execution."""

    if cancel_event is not None and cancel_event.is_set():
        store.cancel_request(request_id)
        return
    store.mark_running(request_id)
    requested_context = dict(request_context or {})
    auto_learn_enabled = bool(
        requested_context.get(
            "agent_learning_ledger_auto_learn_enabled",
            getattr(settings, "agent_learning_ledger_auto_learn_enabled", True),
        )
    )
    runtime = _build_traced_runtime(
        settings=settings,
        base_runtime=base_runtime,
        store=store,
        request_id=request_id,
        llm_model=str(requested_context.get("llm_model") or "").strip() or None,
        llm_base_url=str(requested_context.get("llm_base_url") or "").strip() or None,
        llm_timeout_seconds=_positive_float(requested_context.get("llm_timeout_seconds")),
        llm_max_tokens=_nonnegative_int(requested_context.get("llm_max_tokens")),
        response_streaming_enabled=bool(requested_context.get("response_streaming_enabled")),
    )
    context = {
        **requested_context,
        "request_id": request_id,
        "workspace_root": str(settings.workspace_root),
        "target_ui": "agent_ui",
    }
    store.append_event(
        AgentTraceEvent(
            request_id=request_id,
            stage="advisory",
            level="info",
            event_type="advisory.started",
            title="Advisory response started",
            summary="The runtime is asking the LLM for advisory guidance only.",
            detail={
                "agent_mode": "advisory",
                "gateway": _advisory_gateway_context(context),
            },
        )
    )
    try:
        if cancel_event is not None and cancel_event.is_set():
            store.cancel_request(request_id)
            return
        llm_client = getattr(runtime, "llm_client", None)
        complete_json = getattr(llm_client, "complete_json", None)
        if not callable(complete_json):
            raise RuntimeError("Advisory mode requires a configured LLM client.")
        prompt_text = _advisory_prompt(
            prompt=prompt,
            request_context=context,
            settings=settings,
        )
        raw = complete_json(prompt_text, AdvisoryResponse.model_json_schema())
        response = _normalize_advisory_response(raw)
        display_document = _advisory_display_document(
            request_id=request_id,
            response=response,
            request_context=context,
        )
        runnable_count = sum(1 for snippet in response.snippets if snippet.runnable)
        store.append_event(
            AgentTraceEvent(
                request_id=request_id,
                stage="advisory",
                level="info",
                event_type="advisory.completed",
                title="Advisory response completed",
                summary=f"Advisory guidance is ready with {runnable_count} runnable snippet(s).",
                detail={
                    "snippet_count": len(response.snippets),
                    "runnable_snippet_count": runnable_count,
                },
                parsed_output=display_document.get("metadata"),
            )
        )
        store.complete_request(
            request_id,
            response.answer_markdown,
            display_document,
        )
        if conversation_store is not None:
            conversation_store.append_turn(
                requested_context.get("conversation_id"),
                store.get_trace(request_id),
            )
        _record_learning_ledger_for_trace(
            learning_ledger_store=learning_ledger_store,
            memory_store=_memory_store_for_learning(settings, base_runtime),
            prompt_template_store=PromptTemplateStore(settings.agent_prompts_db_path),
            trace_store=store,
            trace=store.get_trace(request_id),
            request_context=context,
            auto_learn_enabled=auto_learn_enabled,
        )
        if conversation_store is not None:
            conversation_store.append_turn(
                requested_context.get("conversation_id"),
                store.get_trace(request_id),
            )
    except Exception as exc:  # pragma: no cover - safety boundary for worker threads
        if cancel_event is not None and cancel_event.is_set():
            store.cancel_request(request_id)
            return
        detail = user_error_detail(
            exc,
            stage="advisory",
            category="unexpected_error",
            context=context,
            request_id=request_id,
        )
        store.fail_request(request_id, user_error_message(detail, exc), error_detail=detail)
        _record_learning_ledger_for_trace(
            learning_ledger_store=learning_ledger_store,
            memory_store=_memory_store_for_learning(settings, base_runtime),
            prompt_template_store=PromptTemplateStore(settings.agent_prompts_db_path),
            trace_store=store,
            trace=store.get_trace(request_id),
            request_context=context,
            auto_learn_enabled=auto_learn_enabled,
        )
        if conversation_store is not None:
            conversation_store.append_turn(
                requested_context.get("conversation_id"),
                store.get_trace(request_id),
            )

__all__ = [name for name in globals() if not name.startswith("__")]
