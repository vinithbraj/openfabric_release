from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import os
import pty
import queue
import re
import shlex
import select
import signal
import struct
import subprocess
import termios
import threading
import time
import tempfile
import uuid
from pathlib import Path
from typing import Any

from gateway_core.config import Settings
from gateway_core.executor import CANCELLED_EXIT_CODE, TIMEOUT_EXIT_CODE
from gateway_core.models import ExecStreamEvent


CWD_MARKER = "\x1b]777;AOR_CWD="
BEL = "\x07"
DEFAULT_ROWS = 24
DEFAULT_COLS = 80
TERMINAL_OUTPUT_BATCH_BYTES = 32 * 1024
_TERMINAL_LOCK = threading.RLock()
_TERMINAL_SESSIONS: dict[str, "TerminalSession"] = {}
_TERMINAL_EXEC_LOCK = threading.RLock()
_TERMINAL_EXECUTIONS: dict[str, "_TerminalCommandCapture"] = {}


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
INTERACTIVE_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"enter passphrase\b", re.IGNORECASE),
    re.compile(r"\bpassphrase for key\b", re.IGNORECASE),
    re.compile(r"\bpassword(?: for [^:\n]+)?\s*:\s*$", re.IGNORECASE),
    re.compile(r"\bsudo\b[^\n]*password[^\n]*:\s*$", re.IGNORECASE),
    re.compile(r"\bare you sure you want to continue connecting\b.*\?\s*$", re.IGNORECASE),
    re.compile(r"\bdo you want to continue\?\s*$", re.IGNORECASE),
    re.compile(r"\b(?:username|login)(?: for [^\n]+)?\s*:\s*$", re.IGNORECASE),
    re.compile(r"\benter (?:pin|otp|token|verification code|code)\b", re.IGNORECASE),
    re.compile(r"\bpress (?:any )?key\b", re.IGNORECASE),
    re.compile(r"\bpress enter\b", re.IGNORECASE),
    re.compile(r"\bcontinue\?\s*(?:\[[^\]]+\]|\([^)]+\))?\s*$", re.IGNORECASE),
    re.compile(r"\b(?:y/n|yes/no|y/n/q)\??\s*$", re.IGNORECASE),
)


def _coerce_size(value: int | str | None, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(1, min(number, 500))


def _resolve_initial_cwd(settings: Settings, initial_cwd: str | None) -> Path:
    raw = str(initial_cwd or "").strip()
    candidate = Path(raw).expanduser() if raw else settings.workdir or Path.cwd()
    if not candidate.is_absolute():
        candidate = (settings.workdir or Path.cwd()) / candidate
    resolved = candidate.resolve(strict=False)
    if not resolved.exists():
        raise ValueError(f"Terminal cwd does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"Terminal cwd is not a directory: {resolved}")
    return resolved


@dataclass
class _TerminalCommandCapture:
    marker_id: str
    execution_id: str
    start_marker: str
    end_marker_prefix: str
    events: "queue.Queue[ExecStreamEvent]" = field(default_factory=queue.Queue)
    buffer: str = ""
    capturing: bool = False
    completed: bool = False
    cancelled: bool = False
    exit_code: int | None = None
    input_probe: str = ""
    input_required: bool = False
    input_required_notified: bool = False
    input_required_last_notice: float = 0.0


def _terminal_output_needs_user_input(text: str) -> bool:
    normalized = ANSI_RE.sub("", str(text or "")).replace("\r", "\n")[-1600:]
    if not normalized.strip():
        return False
    return any(pattern.search(normalized) for pattern in INTERACTIVE_PROMPT_PATTERNS)


def _marker_prefix_suffix_length(text: str, marker: str) -> int:
    """Return how much of text's suffix could be the next marker prefix."""

    max_length = min(len(text), max(0, len(marker) - 1))
    for length in range(max_length, 0, -1):
        if marker.startswith(text[-length:]):
            return length
    return 0


class TerminalSession:
    """One persistent interactive POSIX shell PTY session."""

    def __init__(
        self,
        *,
        settings: Settings,
        session_id: str,
        initial_cwd: str | None,
        rows: int | str | None = None,
        cols: int | str | None = None,
    ) -> None:
        self.session_id = str(session_id or "").strip()
        if not self.session_id:
            raise ValueError("Terminal session id is required.")
        self.settings = settings
        self.current_cwd = str(_resolve_initial_cwd(settings, initial_cwd))
        self.last_activity = time.monotonic()
        self._events: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=1000)
        self._closed = threading.Event()
        self._parse_buffer = ""
        self._pending_event: dict[str, Any] | None = None
        self._capture_lock = threading.RLock()
        self._dispatch_lock = threading.Lock()
        self._active_capture: _TerminalCommandCapture | None = None
        self._master_fd, slave_fd = pty.openpty()
        self.resize(rows=rows, cols=cols)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["PS1"] = r"\u@\h:\w\$ "
        env["PROMPT_COMMAND"] = (
            "__aor_cwd_marker(){ printf '\\033]777;AOR_CWD=%s\\007' \"$PWD\"; }; "
            "__aor_cwd_marker"
        )
        try:
            shell = str(settings.shell_path or "/bin/bash").strip() or "/bin/bash"
            self.process = subprocess.Popen(
                [shell, "--noprofile", "--norc", "-i"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.current_cwd,
                env=env,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            try:
                os.close(slave_fd)
            except OSError:
                pass
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _touch(self) -> None:
        self.last_activity = time.monotonic()

    def _put_event(self, event: dict[str, Any]) -> None:
        try:
            self._events.put_nowait(event)
        except queue.Full:
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass
            self._events.put_nowait(event)

    def _emit_output(self, data: str) -> None:
        visible = self._process_command_capture(data)
        if visible:
            self._put_event({"type": "output", "data": visible})

    def emit_output(self, data: str) -> None:
        """Display runtime-authored text in this terminal session."""

        self._emit_output(str(data or ""))

    @staticmethod
    def _capture_stdout(capture: _TerminalCommandCapture, text: str) -> None:
        if not text:
            return
        capture.events.put(ExecStreamEvent(type="stdout", text=text))
        capture.input_probe = (capture.input_probe + text)[-1600:]
        if _terminal_output_needs_user_input(capture.input_probe):
            capture.input_required = True
            if not capture.input_required_notified:
                capture.input_required_notified = True
                capture.input_required_last_notice = time.monotonic()
                capture.events.put(
                    ExecStreamEvent(
                        type="terminal_input_required",
                        text=(
                            "Terminal command is waiting for user input. "
                            "Enter the requested value in the terminal to continue.\n"
                        ),
                    )
                )

    def _process_command_capture(self, data: str) -> str:
        if not data:
            return ""
        with self._capture_lock:
            capture = self._active_capture
            if capture is None or capture.completed:
                return data

            capture.buffer += data
            visible_parts: list[str] = []

            if not capture.capturing:
                marker_index = capture.buffer.find(capture.start_marker)
                if marker_index < 0:
                    keep = _marker_prefix_suffix_length(capture.buffer, capture.start_marker)
                    capture.buffer = capture.buffer[-keep:] if keep else ""
                    return ""
                visible_parts.append(capture.buffer[:marker_index])
                capture.buffer = capture.buffer[marker_index + len(capture.start_marker) :]
                capture.capturing = True

            while capture.buffer and not capture.completed:
                end_index = capture.buffer.find(capture.end_marker_prefix)
                if end_index >= 0:
                    before = capture.buffer[:end_index]
                    if before:
                        visible_parts.append(before)
                        self._capture_stdout(capture, before)
                    after = capture.buffer[end_index + len(capture.end_marker_prefix) :]
                    newline_index = after.find("\n")
                    if newline_index < 0:
                        capture.buffer = capture.buffer[end_index:]
                        break
                    code_text = after[:newline_index].strip() or "1"
                    try:
                        capture.exit_code = int(code_text)
                    except ValueError:
                        capture.exit_code = 1
                    capture.completed = True
                    capture.events.put(ExecStreamEvent(type="completed", exit_code=capture.exit_code))
                    trailing = after[newline_index + 1 :]
                    if trailing:
                        visible_parts.append(trailing)
                    capture.buffer = ""
                    break

                keep = _marker_prefix_suffix_length(capture.buffer, capture.end_marker_prefix)
                safe_text = capture.buffer[:-keep] if keep else capture.buffer
                if safe_text:
                    visible_parts.append(safe_text)
                    self._capture_stdout(capture, safe_text)
                capture.buffer = capture.buffer[-keep:] if keep else ""
                break
            return "".join(visible_parts)

    def _process_terminal_output(self, data: str) -> None:
        text = self._parse_buffer + data
        self._parse_buffer = ""
        while text:
            marker_index = text.find(CWD_MARKER)
            if marker_index < 0:
                self._emit_output(text)
                return
            self._emit_output(text[:marker_index])
            marker_end = text.find(BEL, marker_index)
            if marker_end < 0:
                self._parse_buffer = text[marker_index:]
                return
            cwd = text[marker_index + len(CWD_MARKER) : marker_end]
            if cwd:
                self.current_cwd = cwd
                self._put_event({"type": "cwd", "cwd": cwd})
            text = text[marker_end + len(BEL) :]

    @staticmethod
    def _build_capture_script(marker_id: str, command_text: str) -> str:
        """Write one captured command wrapper to a script file.

        Keeping the post-command marker in a script prevents interactive
        subprocesses, such as SSH passphrase prompts, from reading the wrapper
        trailer as terminal input.
        """

        script = "\n".join(
            [
                "#!/usr/bin/env bash",
                "__aor_s='__AOR_EXEC_START_'",
                "__aor_e='__AOR_EXEC_END_'",
                f"__aor_marker={shlex.quote(marker_id)}",
                "stty -echo 2>/dev/null || true",
                "printf '\\n%s%s__\\n' \"$__aor_s\" \"$__aor_marker\"",
                "stty echo 2>/dev/null || true",
                "(",
                command_text,
                ")",
                "__aor_exit=$?",
                "stty -echo 2>/dev/null || true",
                "printf '\\n%s%s__:%s\\n' \"$__aor_e\" \"$__aor_marker\" \"$__aor_exit\"",
                "stty echo 2>/dev/null || true",
                "rm -f -- \"$0\" 2>/dev/null || true",
                "exit \"$__aor_exit\"",
                "",
            ]
        )
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix=".openfabric-terminal-",
            suffix=".sh",
            delete=False,
        )
        try:
            handle.write(script)
            path = handle.name
        finally:
            handle.close()
        os.chmod(path, 0o700)
        return path

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                if self.process.poll() is not None:
                    break
                readable, _, _ = select.select([self._master_fd], [], [], 0.1)
                if not readable:
                    continue
                try:
                    chunk = os.read(self._master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                self._touch()
                self._process_terminal_output(chunk.decode("utf-8", errors="replace"))
        finally:
            if self._parse_buffer:
                self._emit_output(self._parse_buffer)
                self._parse_buffer = ""
            exit_code = self.process.poll()
            self._put_event({"type": "exit", "exit_code": int(exit_code or 0)})
            self.close()

    def write(self, data: str) -> None:
        if self._closed.is_set():
            return
        payload = str(data or "").encode("utf-8", errors="replace")
        if payload:
            with self._capture_lock:
                capture = self._active_capture
                if capture is not None and capture.input_required and not capture.completed:
                    capture.input_required = False
                    capture.input_required_notified = False
                    capture.input_required_last_notice = 0.0
                    capture.input_probe = ""
            self._touch()
            os.write(self._master_fd, payload)

    def stream_command(
        self,
        command: str,
        *,
        execution_id: str | None,
        timeout_seconds: float,
        display_command: str | None = None,
    ):
        """Run one command inside this interactive terminal and yield captured output."""

        if not self.is_alive():
            yield ExecStreamEvent(type="error", text="Terminal session is not running.\n")
            yield ExecStreamEvent(type="completed", exit_code=1)
            return
        if not self._dispatch_lock.acquire(blocking=False):
            yield ExecStreamEvent(type="error", text="Terminal session is already running an agent command.\n")
            yield ExecStreamEvent(type="completed", exit_code=1)
            return

        normalized_execution_id = str(execution_id or "").strip()
        marker_id = uuid.uuid4().hex
        capture = _TerminalCommandCapture(
            marker_id=marker_id,
            execution_id=normalized_execution_id,
            start_marker=f"__AOR_EXEC_START_{marker_id}__",
            end_marker_prefix=f"__AOR_EXEC_END_{marker_id}__:",
        )
        with self._capture_lock:
            self._active_capture = capture
        if normalized_execution_id:
            with _TERMINAL_EXEC_LOCK:
                _TERMINAL_EXECUTIONS[normalized_execution_id] = capture

        command_text = str(command or "").rstrip()
        display_text = str(display_command or command_text).rstrip()
        script_path = self._build_capture_script(marker_id, command_text)
        self._put_event({"type": "output", "data": f"\r\n$ {display_text}\r\n"})
        shell = str(self.settings.shell_path or "/bin/bash").strip() or "/bin/bash"
        self.write(f"{shlex.quote(shell)} {shlex.quote(script_path)}\n")

        timeout = float(timeout_seconds)
        deadline = time.monotonic() + max(1.0, timeout) if timeout > 0 else None
        try:
            while True:
                now = time.monotonic()
                if capture.cancelled:
                    yield ExecStreamEvent(
                        type="cancelled",
                        text="Command cancelled by user.\n",
                        exit_code=CANCELLED_EXIT_CODE,
                    )
                    yield ExecStreamEvent(type="completed", exit_code=CANCELLED_EXIT_CODE)
                    return
                if capture.input_required and (
                    not capture.input_required_notified
                    or now - capture.input_required_last_notice >= 5.0
                ):
                    capture.input_required_notified = True
                    capture.input_required_last_notice = now
                    yield ExecStreamEvent(
                        type="terminal_input_required",
                        text=(
                            "Terminal command is waiting for user input. "
                            "Enter the requested value in the terminal to continue.\n"
                        ),
                    )
                    continue
                if deadline is not None and now >= deadline and not capture.input_required:
                    capture.cancelled = True
                    self.write("\x03stty echo\n")
                    yield ExecStreamEvent(
                        type="stderr",
                        text=f"Command timed out after {timeout_seconds:g} seconds.\n",
                    )
                    yield ExecStreamEvent(type="completed", exit_code=TIMEOUT_EXIT_CODE)
                    return
                try:
                    event = capture.events.get(timeout=0.1)
                except queue.Empty:
                    continue
                yield event
                if event.type == "completed":
                    return
        finally:
            with self._capture_lock:
                if self._active_capture is capture:
                    if capture.buffer and capture.capturing and not capture.completed:
                        self._capture_stdout(capture, capture.buffer)
                    self._active_capture = None
            if normalized_execution_id:
                with _TERMINAL_EXEC_LOCK:
                    _TERMINAL_EXECUTIONS.pop(normalized_execution_id, None)
            self._dispatch_lock.release()

    def start_detached_command(self, command: str) -> None:
        """Start a terminal-owned command without runtime output capture."""

        if not self.is_alive():
            raise ValueError("Terminal session is not running.")
        with self._capture_lock:
            if self._active_capture is not None:
                raise ValueError("Terminal session is already running an agent-captured command.")
        command_text = str(command or "").rstrip()
        if not command_text:
            raise ValueError("Command is required.")
        self.write(f"{command_text}\n")

    def resize(self, *, rows: int | str | None, cols: int | str | None) -> None:
        safe_rows = _coerce_size(rows, DEFAULT_ROWS)
        safe_cols = _coerce_size(cols, DEFAULT_COLS)
        try:
            fcntl.ioctl(
                self._master_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", safe_rows, safe_cols, 0, 0),
            )
        except OSError:
            pass
        self._touch()

    def _next_raw_event(self, timeout: float) -> dict[str, Any] | None:
        if self._pending_event is not None:
            event = self._pending_event
            self._pending_event = None
            return event
        try:
            return self._events.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def next_event(self, timeout: float = 0.1) -> dict[str, Any] | None:
        event = self._next_raw_event(timeout)
        if event is None or event.get("type") != "output":
            return event

        parts = [str(event.get("data") or "")]
        total_bytes = len(parts[0].encode("utf-8", errors="replace"))
        while total_bytes < TERMINAL_OUTPUT_BATCH_BYTES:
            try:
                next_event = self._events.get_nowait()
            except queue.Empty:
                break
            if next_event.get("type") != "output":
                self._pending_event = next_event
                break
            chunk = str(next_event.get("data") or "")
            parts.append(chunk)
            total_bytes += len(chunk.encode("utf-8", errors="replace"))
        return {"type": "output", "data": "".join(parts)}

    def is_alive(self) -> bool:
        return not self._closed.is_set() and self.process.poll() is None

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if getattr(self, "process", None) is not None and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except Exception:
                self.process.terminate()
            try:
                self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except Exception:
                    self.process.kill()
        try:
            os.close(self._master_fd)
        except OSError:
            pass


def cleanup_terminal_sessions(max_idle_seconds: float) -> None:
    deadline = time.monotonic() - max(1.0, float(max_idle_seconds))
    with _TERMINAL_LOCK:
        expired = [
            session_id
            for session_id, session in _TERMINAL_SESSIONS.items()
            if not session.is_alive() or session.last_activity < deadline
        ]
        for session_id in expired:
            session = _TERMINAL_SESSIONS.pop(session_id, None)
            if session is not None:
                session.close()


def get_terminal_session(
    settings: Settings,
    *,
    session_id: str,
    initial_cwd: str | None,
    rows: int | str | None = None,
    cols: int | str | None = None,
) -> TerminalSession:
    cleanup_terminal_sessions(settings.terminal_idle_timeout_seconds)
    with _TERMINAL_LOCK:
        existing = _TERMINAL_SESSIONS.get(session_id)
        if existing is not None and existing.is_alive():
            existing.resize(rows=rows, cols=cols)
            return existing
        session = TerminalSession(
            settings=settings,
            session_id=session_id,
            initial_cwd=initial_cwd,
            rows=rows,
            cols=cols,
        )
        _TERMINAL_SESSIONS[session_id] = session
        return session


def lookup_terminal_session(session_id: str) -> TerminalSession | None:
    with _TERMINAL_LOCK:
        session = _TERMINAL_SESSIONS.get(str(session_id or "").strip())
    if session is None or not session.is_alive():
        return None
    return session


def stream_terminal_command(
    settings: Settings,
    *,
    session_id: str,
    command: str,
    execution_id: str | None = None,
    display_command: str | None = None,
):
    session = lookup_terminal_session(session_id)
    if session is None:
        raise ValueError(f"Terminal session is not active: {session_id}")
    yield from session.stream_command(
        command,
        execution_id=execution_id,
        timeout_seconds=settings.exec_timeout_seconds,
        display_command=display_command,
    )


def start_detached_terminal_command(
    *,
    session_id: str,
    command: str,
) -> None:
    session = lookup_terminal_session(session_id)
    if session is None:
        raise ValueError(f"Terminal session is not active: {session_id}")
    session.start_detached_command(command)


def cancel_terminal_command(execution_id: str) -> bool:
    normalized = str(execution_id or "").strip()
    if not normalized:
        return False
    with _TERMINAL_EXEC_LOCK:
        capture = _TERMINAL_EXECUTIONS.get(normalized)
    if capture is None:
        return False
    capture.cancelled = True
    with _TERMINAL_LOCK:
        sessions = list(_TERMINAL_SESSIONS.values())
    for session in sessions:
        with session._capture_lock:
            if session._active_capture is capture:
                session.write("\x03stty echo\n")
                return True
    return True


def close_terminal_session(session_id: str) -> bool:
    with _TERMINAL_LOCK:
        session = _TERMINAL_SESSIONS.pop(str(session_id or "").strip(), None)
    if session is None:
        return False
    session.close()
    return True
