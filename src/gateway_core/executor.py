from __future__ import annotations

import queue
import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator

from gateway_core.config import Settings
from gateway_core.models import ExecResponse, ExecStreamEvent


TIMEOUT_EXIT_CODE = 124
CANCELLED_EXIT_CODE = 130
_ACTIVE_LOCK = threading.RLock()
_ACTIVE_PROCESSES: dict[str, subprocess.Popen] = {}
_CANCELLED_EXECUTIONS: set[str] = set()


def _coerce_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _normalize_execution_id(execution_id: str | None) -> str:
    return str(execution_id or "").strip()


def _register_process(execution_id: str | None, process: subprocess.Popen) -> None:
    normalized = _normalize_execution_id(execution_id)
    if not normalized:
        return
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES[normalized] = process
        _CANCELLED_EXECUTIONS.discard(normalized)


def _unregister_process(execution_id: str | None) -> None:
    normalized = _normalize_execution_id(execution_id)
    if not normalized:
        return
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES.pop(normalized, None)
        _CANCELLED_EXECUTIONS.discard(normalized)


def _is_cancelled(execution_id: str | None) -> bool:
    normalized = _normalize_execution_id(execution_id)
    if not normalized:
        return False
    with _ACTIVE_LOCK:
        return normalized in _CANCELLED_EXECUTIONS


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except Exception:
        process.terminate()
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            process.kill()


def cancel_command(execution_id: str) -> bool:
    normalized = _normalize_execution_id(execution_id)
    if not normalized:
        return False
    with _ACTIVE_LOCK:
        process = _ACTIVE_PROCESSES.get(normalized)
        _CANCELLED_EXECUTIONS.add(normalized)
    if process is None:
        return False
    _terminate_process(process)
    return True


def _merged_env(env: dict[str, str] | None) -> dict[str, str] | None:
    if not env:
        return None
    merged = dict(os.environ)
    for key, value in dict(env or {}).items():
        name = str(key or "").strip()
        if name:
            merged[name] = str(value)
    return merged


def _execution_timeout(settings: Settings) -> float | None:
    timeout = float(settings.exec_timeout_seconds)
    return timeout if timeout > 0 else None


def _shell_argv(settings: Settings, command: str) -> list[str]:
    shell = str(settings.shell_path or "bash").strip() or "bash"
    return [shell, "-lc", command]


def execute_command(
    settings: Settings,
    command: str,
    execution_id: str | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> ExecResponse:
    stdin_provided = stdin is not None
    process = subprocess.Popen(
        _shell_argv(settings, command),
        stdin=subprocess.PIPE if stdin_provided else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(settings.workdir) if settings.workdir is not None else None,
        env=_merged_env(env),
        start_new_session=True,
    )
    _register_process(execution_id, process)
    try:
        stdout, stderr = process.communicate(
            input=str(stdin) if stdin_provided else None,
            timeout=_execution_timeout(settings),
        )
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        stdout, timeout_stderr = process.communicate()
        stderr = _coerce_stream(timeout_stderr or exc.stderr).rstrip("\n")
        timeout_message = f"Command timed out after {settings.exec_timeout_seconds:g} seconds."
        if stderr:
            stderr = f"{stderr}\n{timeout_message}\n"
        else:
            stderr = f"{timeout_message}\n"
        return ExecResponse(
            stdout=_coerce_stream(stdout or exc.stdout),
            stderr=stderr,
            exit_code=TIMEOUT_EXIT_CODE,
        )
    finally:
        cancelled = _is_cancelled(execution_id)
        _unregister_process(execution_id)

    if cancelled:
        stderr = _coerce_stream(stderr)
        cancel_message = "Command cancelled by user.\n"
        if cancel_message not in stderr:
            stderr = f"{stderr}{'' if stderr.endswith(chr(10)) or not stderr else chr(10)}{cancel_message}"
        return ExecResponse(
            stdout=_coerce_stream(stdout),
            stderr=stderr,
            exit_code=CANCELLED_EXIT_CODE,
        )
    return ExecResponse(
        stdout=_coerce_stream(stdout),
        stderr=_coerce_stream(stderr),
        exit_code=process.returncode,
    )


def _stream_reader(stream, channel: str, output_queue: "queue.Queue[tuple[str, str | None]]") -> None:
    try:
        for line in iter(stream.readline, ""):
            output_queue.put((channel, _coerce_stream(line)))
    finally:
        try:
            stream.close()
        finally:
            output_queue.put((f"{channel}_eof", None))


def stream_command(
    settings: Settings,
    command: str,
    execution_id: str | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> Iterator[ExecStreamEvent]:
    stdin_provided = stdin is not None
    process = subprocess.Popen(
        _shell_argv(settings, command),
        stdin=subprocess.PIPE if stdin_provided else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(settings.workdir) if settings.workdir is not None else None,
        env=_merged_env(env),
        start_new_session=True,
    )
    _register_process(execution_id, process)
    output_queue: "queue.Queue[tuple[str, str | None]]" = queue.Queue()
    stdout_done = False
    stderr_done = False
    timeout_seconds = _execution_timeout(settings)
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None

    stdout_thread = threading.Thread(
        target=_stream_reader,
        args=(process.stdout, "stdout", output_queue),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_stream_reader,
        args=(process.stderr, "stderr", output_queue),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    if stdin_provided and process.stdin is not None:
        try:
            process.stdin.write(str(stdin))
            process.stdin.close()
        except BrokenPipeError:
            pass

    timed_out = False
    cancelled = False
    cancelled_emitted = False
    while not (stdout_done and stderr_done and process.poll() is not None and output_queue.empty()):
        if _is_cancelled(execution_id) and process.poll() is None:
            cancelled = True
            _terminate_process(process)
            cancelled_emitted = True
            yield ExecStreamEvent(
                type="cancelled",
                text="Command cancelled by user.\n",
                exit_code=CANCELLED_EXIT_CODE,
            )
            break
        remaining = deadline - time.monotonic() if deadline is not None else None
        if remaining is not None and remaining <= 0 and process.poll() is None:
            timed_out = True
            _terminate_process(process)
            yield ExecStreamEvent(
                type="stderr",
                text=f"Command timed out after {settings.exec_timeout_seconds:g} seconds.\n",
            )
            break
        try:
            poll_timeout = 0.1 if remaining is None else min(0.1, max(remaining, 0.0) or 0.1)
            channel, text = output_queue.get(timeout=poll_timeout)
        except queue.Empty:
            continue
        if channel == "stdout_eof":
            stdout_done = True
            continue
        if channel == "stderr_eof":
            stderr_done = True
            continue
        yield ExecStreamEvent(type=channel, text=text or "")

    try:
        process.wait()
    finally:
        stdout_thread.join(timeout=0.2)
        stderr_thread.join(timeout=0.2)
        if _is_cancelled(execution_id):
            cancelled = True
        _unregister_process(execution_id)

    if cancelled and not cancelled_emitted:
        yield ExecStreamEvent(
            type="cancelled",
            text="Command cancelled by user.\n",
            exit_code=CANCELLED_EXIT_CODE,
        )

    while not output_queue.empty():
        channel, text = output_queue.get_nowait()
        if channel == "stdout_eof":
            stdout_done = True
            continue
        if channel == "stderr_eof":
            stderr_done = True
            continue
        yield ExecStreamEvent(type=channel, text=text or "")

    exit_code = CANCELLED_EXIT_CODE if cancelled else TIMEOUT_EXIT_CODE if timed_out else int(process.returncode or 0)
    yield ExecStreamEvent(type="completed", exit_code=exit_code)
