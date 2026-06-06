from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from gateway_core import __version__
from gateway_core.config import Settings, get_settings
from gateway_core.executor import cancel_command, execute_command, stream_command
from gateway_core.models import (
    CapabilitiesResponse,
    CapabilityInfo,
    ExecCancelRequest,
    ExecCancelResponse,
    ExecRequest,
    ExecResponse,
    HealthResponse,
    LlmRuntimeLogResponse,
    LlmRuntimeStartRequest,
    LlmRuntimeStatusResponse,
    LlmRuntimeStopRequest,
    RestartRequest,
    RestartResponse,
    TerminalClientMessage,
    TerminalDetachedExecResponse,
    TerminalExecRequest,
    TerminalSessionRequest,
    TerminalSessionResponse,
    TerminalWriteRequest,
)
from gateway_core.terminal import (
    cancel_terminal_command,
    close_terminal_session,
    get_terminal_session,
    lookup_terminal_session,
    start_detached_terminal_command,
    stream_terminal_command,
)


LOGGER = logging.getLogger(__name__)
TRACE_LOGGER = logging.getLogger("uvicorn.error")


@dataclass
class ManagedLlmRuntime:
    process: subprocess.Popen[bytes]
    command: str
    conda_env: str
    cwd: str
    log_path: str
    started_at: str


_LLM_RUNTIME: ManagedLlmRuntime | None = None
_LLM_RUNTIME_LOCK = threading.RLock()


def _schedule_process_restart(delay_seconds: float = 0.5) -> None:
    """Restart the current gateway process after the HTTP response can flush."""

    def restart() -> None:
        time.sleep(max(0.05, float(delay_seconds)))
        os.execv(sys.executable, _restart_argv())

    threading.Thread(target=restart, daemon=True, name="gateway-agent-restart").start()


def _restart_argv() -> list[str]:
    """Return argv for restarting without running package __main__.py as a script."""

    original = list(sys.argv)
    if not original:
        return [sys.executable, "-m", "uvicorn", "--app-dir", "src", "gateway_agent.app:app"]

    script_path = Path(original[0]).resolve(strict=False)
    if script_path.name == "__main__.py" and script_path.parent.name == "uvicorn":
        return [sys.executable, "-m", "uvicorn", *original[1:]]
    if script_path.name == "uvicorn":
        return [sys.executable, "-m", "uvicorn", *original[1:]]
    return [sys.executable, *original]


def _ensure_node_matches(*, requested_node: str, runtime_node: str) -> None:
    if not requested_node:
        raise HTTPException(status_code=400, detail="Node is required.")
    if requested_node != runtime_node:
        raise HTTPException(
            status_code=400,
            detail=f"Node mismatch. This agent serves node '{runtime_node}'.",
        )


def _llm_runtime_state() -> ManagedLlmRuntime | None:
    global _LLM_RUNTIME
    with _LLM_RUNTIME_LOCK:
        if _LLM_RUNTIME is None:
            return None
        if _LLM_RUNTIME.process.poll() is None:
            return _LLM_RUNTIME
        return _LLM_RUNTIME


def _llm_runtime_response(
    runtime: ManagedLlmRuntime | None,
    *,
    status: str | None = None,
    message: str = "",
) -> LlmRuntimeStatusResponse:
    if runtime is None:
        return LlmRuntimeStatusResponse(
            status=status or "stopped",
            running=False,
            message=message or "No managed LLM runtime has been started.",
        )
    exit_code = runtime.process.poll()
    running = exit_code is None
    return LlmRuntimeStatusResponse(
        status=status or ("running" if running else "stopped"),
        running=running,
        pid=runtime.process.pid,
        command=runtime.command,
        conda_env=runtime.conda_env,
        cwd=runtime.cwd,
        log_path=runtime.log_path,
        started_at=runtime.started_at,
        exit_code=exit_code,
        message=message,
    )


def _llm_runtime_log_response(
    runtime: ManagedLlmRuntime | None,
    *,
    offset: int = 0,
    max_bytes: int = 65536,
) -> LlmRuntimeLogResponse:
    if runtime is None:
        return LlmRuntimeLogResponse(
            status="stopped",
            running=False,
            message="No managed LLM runtime has been started.",
        )
    exit_code = runtime.process.poll()
    running = exit_code is None
    log_path = Path(runtime.log_path)
    if not log_path.exists():
        return LlmRuntimeLogResponse(
            status="running" if running else "stopped",
            running=running,
            log_path=runtime.log_path,
            message="LLM runtime log has not been created yet.",
        )
    size = log_path.stat().st_size
    safe_offset = max(0, int(offset or 0))
    safe_limit = min(max(1024, int(max_bytes or 65536)), 262144)
    truncated = False
    if safe_offset > size:
        safe_offset = 0
    if safe_offset == 0 and size > safe_limit:
        safe_offset = size - safe_limit
        truncated = True
    read_size = min(size - safe_offset, safe_limit)
    text = ""
    if read_size > 0:
        with log_path.open("rb") as handle:
            handle.seek(safe_offset)
            text = handle.read(read_size).decode("utf-8", errors="replace")
    next_offset = safe_offset + read_size
    return LlmRuntimeLogResponse(
        status="running" if running else "stopped",
        running=running,
        log_path=runtime.log_path,
        offset=safe_offset,
        next_offset=next_offset,
        text=text,
        truncated=truncated,
    )


def _resolve_runtime_cwd(settings: Settings, raw_cwd: str | None) -> Path:
    raw = str(raw_cwd or "").strip()
    candidate = Path(raw).expanduser() if raw else settings.workdir or Path.cwd()
    if not candidate.is_absolute():
        candidate = (settings.workdir or Path.cwd()) / candidate
    resolved = candidate.resolve(strict=False)
    if not resolved.exists() or not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"LLM runtime cwd is not a directory: {resolved}")
    return resolved


def _llm_runtime_shell(command: str, conda_env: str, shell_path: str = "/bin/bash") -> str:
    env_name = str(conda_env or "").strip()
    command_text = str(command or "").strip()
    shell = str(shell_path or "/bin/bash").strip() or "/bin/bash"
    lines = ["set -euo pipefail"]
    if env_name:
        lines.extend(
            [
                'CONDA_HOME="${CONDA_HOME:-$HOME/miniconda3}"',
                'if [ -f "$CONDA_HOME/etc/profile.d/conda.sh" ]; then',
                '  # shellcheck source=/dev/null',
                '  source "$CONDA_HOME/etc/profile.d/conda.sh"',
                f"  conda activate {sh_quote(env_name)}",
                "fi",
            ]
        )
    lines.append(f"exec {sh_quote(shell)} -lc {sh_quote(command_text)}")
    return "\n".join(lines)


def sh_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _stop_llm_runtime(runtime: ManagedLlmRuntime, timeout_seconds: float = 10.0) -> None:
    if runtime.process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(runtime.process.pid), signal.SIGTERM)
        runtime.process.wait(timeout=timeout_seconds)
    except Exception:
        with suppress(Exception):
            os.killpg(os.getpgid(runtime.process.pid), signal.SIGKILL)


def _capability_list(settings: Settings) -> list[CapabilityInfo]:
    capabilities = [
        CapabilityInfo(name="healthz", description="Report agent health and the configured logical node."),
        CapabilityInfo(
            name="exec",
            description="Execute a local shell command when the request node matches the configured node.",
        ),
        CapabilityInfo(
            name="exec_stream",
            description="Execute a local shell command and stream stdout/stderr when the request node matches the configured node.",
        ),
        CapabilityInfo(
            name="exec_cancel",
            description="Cancel an in-flight shell command by execution id when the request node matches the configured node.",
        ),
        CapabilityInfo(
            name="terminal_session",
            description="Open an interactive PTY-backed terminal session when the request node matches the configured node.",
        ),
        CapabilityInfo(
            name="terminal_exec_stream",
            description="Execute a shell command inside an active terminal session and stream structured results.",
        ),
        CapabilityInfo(
            name="terminal_exec_detach",
            description="Start a terminal-owned shell command inside an active terminal session.",
        ),
        CapabilityInfo(
            name="terminal_write",
            description="Display runtime-produced text inside an active terminal session.",
        ),
        CapabilityInfo(
            name="terminal_input",
            description="Write runtime-provided input bytes into an active PTY-backed terminal session.",
        ),
        CapabilityInfo(
            name="restart",
            description="Restart the gateway agent process when the request node matches the configured node.",
        ),
    ]
    if bool(settings.enable_llm_runtime):
        capabilities.append(
            CapabilityInfo(
                name="llm_runtime",
                description="Start, stop, and inspect one gateway-managed local vLLM launch command.",
            )
        )
    return capabilities


def _ensure_llm_runtime_enabled(settings: Settings) -> None:
    if not bool(settings.enable_llm_runtime):
        raise HTTPException(
            status_code=501,
            detail="Gateway-managed local LLM runtime is not enabled on this gateway.",
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    app = FastAPI(title=runtime_settings.app_title, version=__version__)

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok", node=runtime_settings.node_name)

    @app.get("/capabilities", response_model=CapabilitiesResponse)
    def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse(
            node=runtime_settings.node_name,
            version=__version__,
            platform=runtime_settings.platform,
            platform_label=runtime_settings.platform_label,
            platform_version=runtime_settings.platform_version,
            architecture=runtime_settings.architecture,
            shell=runtime_settings.shell_path,
            command_profile=runtime_settings.command_profile,
            capability_tags=runtime_settings.capability_tags,
            capabilities=_capability_list(runtime_settings),
        )

    @app.post("/restart", response_model=RestartResponse)
    def restart_gateway(payload: RestartRequest) -> RestartResponse:
        node = str(payload.node or "").strip()
        _ensure_node_matches(requested_node=node, runtime_node=runtime_settings.node_name)
        callback = getattr(app.state, "gateway_restart_callback", None)
        if callable(callback):
            callback()
            return RestartResponse(status="restarting", mode="callback")
        _schedule_process_restart()
        return RestartResponse(status="restarting", mode="exec", pid=os.getpid())

    @app.get("/llm/status", response_model=LlmRuntimeStatusResponse)
    def llm_runtime_status(node: str) -> LlmRuntimeStatusResponse:
        _ensure_llm_runtime_enabled(runtime_settings)
        requested_node = str(node or "").strip()
        _ensure_node_matches(requested_node=requested_node, runtime_node=runtime_settings.node_name)
        with _LLM_RUNTIME_LOCK:
            return _llm_runtime_response(_llm_runtime_state())

    @app.get("/llm/log", response_model=LlmRuntimeLogResponse)
    def llm_runtime_log(node: str, offset: int = 0, max_bytes: int = 65536) -> LlmRuntimeLogResponse:
        _ensure_llm_runtime_enabled(runtime_settings)
        requested_node = str(node or "").strip()
        _ensure_node_matches(requested_node=requested_node, runtime_node=runtime_settings.node_name)
        with _LLM_RUNTIME_LOCK:
            return _llm_runtime_log_response(
                _llm_runtime_state(),
                offset=offset,
                max_bytes=max_bytes,
            )

    @app.post("/llm/start", response_model=LlmRuntimeStatusResponse)
    def llm_runtime_start(payload: LlmRuntimeStartRequest) -> LlmRuntimeStatusResponse:
        _ensure_llm_runtime_enabled(runtime_settings)
        global _LLM_RUNTIME
        node = str(payload.node or "").strip()
        command = str(payload.command or "").strip()
        _ensure_node_matches(requested_node=node, runtime_node=runtime_settings.node_name)
        if not command:
            raise HTTPException(status_code=400, detail="LLM startup command is required.")
        cwd = _resolve_runtime_cwd(runtime_settings, payload.cwd)
        conda_env = str(payload.conda_env or "").strip()
        with _LLM_RUNTIME_LOCK:
            current = _llm_runtime_state()
            if current is not None and current.process.poll() is None:
                if not payload.restart:
                    return _llm_runtime_response(
                        current,
                        message="Managed LLM runtime is already running.",
                    )
                _stop_llm_runtime(current)
            log_dir = Path(os.getenv("GATEWAY_LLM_LOG_DIR", "/tmp/openfabric-llm"))
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"vllm-{int(time.time())}.log"
            log_handle = log_path.open("ab")
            try:
                process = subprocess.Popen(
                    [
                        runtime_settings.shell_path,
                        "-lc",
                        _llm_runtime_shell(command, conda_env, runtime_settings.shell_path),
                    ],
                    cwd=str(cwd),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                log_handle.close()
            _LLM_RUNTIME = ManagedLlmRuntime(
                process=process,
                command=command,
                conda_env=conda_env,
                cwd=str(cwd),
                log_path=str(log_path),
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            return _llm_runtime_response(
                _LLM_RUNTIME,
                status="starting",
                message="Managed LLM runtime started.",
            )

    @app.post("/llm/stop", response_model=LlmRuntimeStatusResponse)
    def llm_runtime_stop(payload: LlmRuntimeStopRequest) -> LlmRuntimeStatusResponse:
        _ensure_llm_runtime_enabled(runtime_settings)
        node = str(payload.node or "").strip()
        _ensure_node_matches(requested_node=node, runtime_node=runtime_settings.node_name)
        with _LLM_RUNTIME_LOCK:
            runtime = _llm_runtime_state()
            if runtime is None:
                return _llm_runtime_response(None, message="No managed LLM runtime is active.")
            _stop_llm_runtime(runtime)
            return _llm_runtime_response(runtime, status="stopped", message="Managed LLM runtime stopped.")

    @app.post("/exec", response_model=ExecResponse)
    def exec_command(payload: ExecRequest) -> ExecResponse:
        node = str(payload.node or "").strip()
        command = str(payload.command or "").strip()

        if not node:
            raise HTTPException(status_code=400, detail="Node is required.")
        if not command:
            raise HTTPException(status_code=400, detail="Command is required.")
        if node != runtime_settings.node_name:
            raise HTTPException(
                status_code=400,
                detail=f"Node mismatch. This agent serves node '{runtime_settings.node_name}'.",
            )

        try:
            if runtime_settings.trace_commands:
                TRACE_LOGGER.info("Gateway exec on %s: %s", runtime_settings.node_name, command)
            return execute_command(
                runtime_settings,
                command,
                execution_id=payload.execution_id,
                env=payload.env,
                stdin=payload.stdin,
            )
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - defensive server guard
            LOGGER.exception("Gateway agent execution failed.")
            raise HTTPException(status_code=500, detail="Internal server error.") from exc

    @app.post("/exec/stream")
    def exec_command_stream(payload: ExecRequest) -> StreamingResponse:
        node = str(payload.node or "").strip()
        command = str(payload.command or "").strip()

        if not node:
            raise HTTPException(status_code=400, detail="Node is required.")
        if not command:
            raise HTTPException(status_code=400, detail="Command is required.")
        if node != runtime_settings.node_name:
            raise HTTPException(
                status_code=400,
                detail=f"Node mismatch. This agent serves node '{runtime_settings.node_name}'.",
            )

        def event_stream():
            try:
                if runtime_settings.trace_commands:
                    TRACE_LOGGER.info("Gateway exec stream on %s: %s", runtime_settings.node_name, command)
                for chunk in stream_command(
                    runtime_settings,
                    command,
                    execution_id=payload.execution_id,
                    env=payload.env,
                    stdin=payload.stdin,
                ):
                    yield f"event: {chunk.type}\ndata: {json.dumps(chunk.model_dump(), default=str)}\n\n"
            except Exception as exc:  # pragma: no cover - defensive server guard
                LOGGER.exception("Gateway agent streaming execution failed.")
                error_payload = {"type": "error", "text": "Internal server error."}
                yield f"event: error\ndata: {json.dumps(error_payload, default=str)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/exec/cancel", response_model=ExecCancelResponse)
    def exec_cancel(payload: ExecCancelRequest) -> ExecCancelResponse:
        node = str(payload.node or "").strip()
        execution_id = str(payload.execution_id or "").strip()

        if not node:
            raise HTTPException(status_code=400, detail="Node is required.")
        if not execution_id:
            raise HTTPException(status_code=400, detail="Execution id is required.")
        if node != runtime_settings.node_name:
            raise HTTPException(
                status_code=400,
                detail=f"Node mismatch. This agent serves node '{runtime_settings.node_name}'.",
            )

        cancelled = cancel_command(execution_id) or cancel_terminal_command(execution_id)
        return ExecCancelResponse(
            cancelled=cancelled,
            execution_id=execution_id,
            message="Cancellation signal sent." if cancelled else "No active command matched that execution id.",
        )

    @app.post("/terminal/exec/stream")
    def terminal_exec_command_stream(payload: TerminalExecRequest) -> StreamingResponse:
        node = str(payload.node or "").strip()
        session_id = str(payload.session_id or "").strip()
        command = str(payload.command or "").strip()
        display_command = str(payload.display_command or "").strip() or None

        if not node:
            raise HTTPException(status_code=400, detail="Node is required.")
        if node != runtime_settings.node_name:
            raise HTTPException(
                status_code=400,
                detail=f"Node mismatch. This agent serves node '{runtime_settings.node_name}'.",
            )
        if not session_id:
            raise HTTPException(status_code=400, detail="Terminal session id is required.")
        if not command:
            raise HTTPException(status_code=400, detail="Command is required.")

        def event_stream():
            try:
                if runtime_settings.trace_commands:
                    TRACE_LOGGER.info(
                        "Gateway terminal exec stream on %s/%s: %s",
                        runtime_settings.node_name,
                        session_id,
                        command,
                    )
                for chunk in stream_terminal_command(
                    runtime_settings,
                    session_id=session_id,
                    command=command,
                    execution_id=payload.execution_id,
                    display_command=display_command,
                ):
                    yield f"event: {chunk.type}\ndata: {json.dumps(chunk.model_dump(), default=str)}\n\n"
            except ValueError as exc:
                error_payload = {"type": "error", "text": f"{exc}\n"}
                yield f"event: error\ndata: {json.dumps(error_payload, default=str)}\n\n"
                completed_payload = {"type": "completed", "exit_code": 1}
                yield f"event: completed\ndata: {json.dumps(completed_payload, default=str)}\n\n"
            except Exception as exc:  # pragma: no cover - defensive server guard
                LOGGER.exception("Gateway agent terminal streaming execution failed.")
                error_payload = {"type": "error", "text": "Internal server error."}
                yield f"event: error\ndata: {json.dumps(error_payload, default=str)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/terminal/session", response_model=TerminalSessionResponse)
    def terminal_session(payload: TerminalSessionRequest) -> TerminalSessionResponse:
        node = str(payload.node or "").strip()
        session_id = str(payload.session_id or "").strip() or f"term-{uuid.uuid4().hex[:12]}"
        initial_cwd = str(payload.initial_cwd or "").strip() or None

        if not node:
            raise HTTPException(status_code=400, detail="Node is required.")
        if node != runtime_settings.node_name:
            raise HTTPException(
                status_code=400,
                detail=f"Node mismatch. This agent serves node '{runtime_settings.node_name}'.",
            )
        session = get_terminal_session(
            runtime_settings,
            session_id=session_id,
            initial_cwd=initial_cwd,
            rows=payload.rows,
            cols=payload.cols,
        )
        return TerminalSessionResponse(ok=True, session_id=session.session_id, cwd=session.current_cwd)

    @app.post("/terminal/exec/detach", response_model=TerminalDetachedExecResponse)
    def terminal_exec_detach(payload: TerminalExecRequest) -> TerminalDetachedExecResponse:
        node = str(payload.node or "").strip()
        session_id = str(payload.session_id or "").strip()
        command = str(payload.command or "").strip()

        if not node:
            raise HTTPException(status_code=400, detail="Node is required.")
        if node != runtime_settings.node_name:
            raise HTTPException(
                status_code=400,
                detail=f"Node mismatch. This agent serves node '{runtime_settings.node_name}'.",
            )
        if not session_id:
            raise HTTPException(status_code=400, detail="Terminal session id is required.")
        if not command:
            raise HTTPException(status_code=400, detail="Command is required.")
        try:
            start_detached_terminal_command(session_id=session_id, command=command)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return TerminalDetachedExecResponse(
            ok=True,
            session_id=session_id,
            message="Command started in terminal.",
        )

    @app.post("/terminal/write")
    def terminal_write(payload: TerminalWriteRequest) -> dict[str, bool]:
        node = str(payload.node or "").strip()
        session_id = str(payload.session_id or "").strip()
        text = str(payload.text or "")

        if not node:
            raise HTTPException(status_code=400, detail="Node is required.")
        if node != runtime_settings.node_name:
            raise HTTPException(
                status_code=400,
                detail=f"Node mismatch. This agent serves node '{runtime_settings.node_name}'.",
            )
        if not session_id:
            raise HTTPException(status_code=400, detail="Terminal session id is required.")
        session = lookup_terminal_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Terminal session is not active: {session_id}")
        session.emit_output(text)
        return {"ok": True}

    @app.post("/terminal/input")
    def terminal_input(payload: TerminalWriteRequest) -> dict[str, bool]:
        node = str(payload.node or "").strip()
        session_id = str(payload.session_id or "").strip()
        text = str(payload.text or "")

        if not node:
            raise HTTPException(status_code=400, detail="Node is required.")
        if node != runtime_settings.node_name:
            raise HTTPException(
                status_code=400,
                detail=f"Node mismatch. This agent serves node '{runtime_settings.node_name}'.",
            )
        if not session_id:
            raise HTTPException(status_code=400, detail="Terminal session id is required.")
        session = lookup_terminal_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Terminal session is not active: {session_id}")
        session.write(text)
        return {"ok": True}

    @app.websocket("/terminal/ws")
    async def terminal_ws(websocket: WebSocket) -> None:
        node = str(websocket.query_params.get("node") or "").strip()
        session_id = str(websocket.query_params.get("session_id") or "").strip()
        initial_cwd = str(websocket.query_params.get("initial_cwd") or "").strip() or None
        rows = websocket.query_params.get("rows")
        cols = websocket.query_params.get("cols")

        await websocket.accept()
        if not node:
            await websocket.send_json({"type": "error", "message": "Node is required."})
            await websocket.close(code=1008)
            return
        if node != runtime_settings.node_name:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Node mismatch. This agent serves node '{runtime_settings.node_name}'.",
                }
            )
            await websocket.close(code=1008)
            return
        if not session_id:
            await websocket.send_json({"type": "error", "message": "Terminal session id is required."})
            await websocket.close(code=1008)
            return

        try:
            session = get_terminal_session(
                runtime_settings,
                session_id=session_id,
                initial_cwd=initial_cwd,
                rows=rows,
                cols=cols,
            )
        except Exception as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close(code=1008)
            return

        await websocket.send_json({"type": "cwd", "cwd": session.current_cwd})

        async def send_events() -> None:
            while True:
                event = await asyncio.to_thread(session.next_event, 0.1)
                if event is None:
                    if not session.is_alive():
                        await websocket.send_json({"type": "exit", "exit_code": 0})
                        return
                    continue
                try:
                    await websocket.send_json(event)
                except (RuntimeError, WebSocketDisconnect):
                    return
                if event.get("type") == "exit":
                    return

        sender = asyncio.create_task(send_events())
        try:
            while True:
                payload = await websocket.receive_json()
                message = TerminalClientMessage.model_validate(payload)
                message_type = str(message.type or "").strip().lower()
                if message_type == "input":
                    session.write(message.data)
                elif message_type == "resize":
                    session.resize(rows=message.rows, cols=message.cols)
                elif message_type == "close":
                    close_terminal_session(session.session_id)
                    await websocket.send_json({"type": "exit", "exit_code": 0})
                    await websocket.close()
                    return
                else:
                    await websocket.send_json(
                        {"type": "error", "message": f"Unsupported terminal message type: {message.type}"}
                    )
        except WebSocketDisconnect:
            return
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError, RuntimeError):
                await sender

    return app


app = create_app()
