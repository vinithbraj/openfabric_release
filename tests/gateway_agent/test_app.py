from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from gateway_macos.app import create_app as create_macos_app
from gateway_agent.app import create_app
from gateway_agent.config import Settings
from gateway_agent.executor import CANCELLED_EXIT_CODE, cancel_command, execute_command, stream_command
from gateway_agent.terminal import (
    TerminalSession,
    _TerminalCommandCapture,
    _terminal_output_needs_user_input,
)


def _client(tmp_path: Path, **overrides) -> TestClient:
    payload = {
        "node_name": "localhost",
        "workdir": tmp_path,
        "exec_timeout_seconds": 1,
    }
    payload.update(overrides)
    settings = Settings(**payload)
    return TestClient(create_app(settings))


def test_healthz_returns_configured_node(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "node": "localhost"}


def test_capabilities_returns_agent_version_and_capability_list(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node"] == "localhost"
    assert payload["version"] == "0.4.0"
    assert payload["platform"] == "linux"
    assert payload["platform_label"] == "Linux"
    assert payload["shell"]
    assert payload["command_profile"] == "posix-bash-linux"
    assert "linux_cli" in payload["capability_tags"]
    assert payload["capabilities"] == [
        {"name": "healthz", "description": "Report agent health and the configured logical node."},
        {
            "name": "exec",
            "description": "Execute a local shell command when the request node matches the configured node.",
        },
        {
            "name": "exec_stream",
            "description": "Execute a local shell command and stream stdout/stderr when the request node matches the configured node.",
        },
        {
            "name": "exec_cancel",
            "description": "Cancel an in-flight shell command by execution id when the request node matches the configured node.",
        },
        {
            "name": "terminal_session",
            "description": "Open an interactive PTY-backed terminal session when the request node matches the configured node.",
        },
        {
            "name": "terminal_exec_stream",
            "description": "Execute a shell command inside an active terminal session and stream structured results.",
        },
        {
            "name": "terminal_exec_detach",
            "description": "Start a terminal-owned shell command inside an active terminal session.",
        },
        {
            "name": "terminal_write",
            "description": "Display runtime-produced text inside an active terminal session.",
        },
        {
            "name": "terminal_input",
            "description": "Write runtime-provided input bytes into an active PTY-backed terminal session.",
        },
        {
            "name": "restart",
            "description": "Restart the gateway agent process when the request node matches the configured node.",
        },
        {
            "name": "llm_runtime",
            "description": "Start, stop, and inspect one gateway-managed local vLLM launch command.",
        },
    ]


def test_macos_gateway_reports_platform_and_execution_capabilities(tmp_path: Path) -> None:
    settings = Settings(
        node_name="localhost",
        workdir=tmp_path,
        exec_timeout_seconds=5,
        platform="macos",
        platform_label="macOS",
        shell_path="/bin/bash",
        command_profile="posix-bash-macos",
        capability_tags=["exec", "python", "terminal", "posix_shell", "bash", "macos_cli"],
        enable_llm_runtime=False,
    )
    client = TestClient(create_macos_app(settings))

    response = client.get("/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node"] == "localhost"
    assert payload["platform"] == "macos"
    assert payload["platform_label"] == "macOS"
    assert payload["shell"] == "/bin/bash"
    assert payload["command_profile"] == "posix-bash-macos"
    assert "macos_cli" in payload["capability_tags"]
    assert "llm_runtime" not in {item["name"] for item in payload["capabilities"]}


def test_macos_gateway_exec_and_stream_use_bash_profile(tmp_path: Path) -> None:
    settings = Settings(
        node_name="localhost",
        workdir=tmp_path,
        exec_timeout_seconds=5,
        platform="macos",
        platform_label="macOS",
        shell_path="/bin/bash",
        command_profile="posix-bash-macos",
        enable_llm_runtime=False,
    )
    client = TestClient(create_macos_app(settings))

    response = client.post("/exec", json={"node": "localhost", "command": "printf macos-ok"})
    stream = client.post("/exec/stream", json={"node": "localhost", "command": "printf stream-ok"})

    assert response.status_code == 200
    assert response.json()["stdout"] == "macos-ok"
    assert stream.status_code == 200
    assert "stream-ok" in stream.text
    assert '"type": "completed"' in stream.text


def test_macos_gateway_terminal_websocket_uses_configured_shell(tmp_path: Path) -> None:
    settings = Settings(
        node_name="localhost",
        workdir=tmp_path,
        exec_timeout_seconds=5,
        platform="macos",
        platform_label="macOS",
        shell_path="/bin/bash",
        command_profile="posix-bash-macos",
        enable_llm_runtime=False,
    )
    client = TestClient(create_macos_app(settings))

    with client.websocket_connect(
        f"/terminal/ws?node=localhost&session_id=mac-term-pwd&initial_cwd={tmp_path}&rows=24&cols=80"
    ) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": str(tmp_path)}
        websocket.send_json({"type": "input", "data": "pwd\n"})
        output = ""
        for _ in range(40):
            message = websocket.receive_json()
            if message["type"] == "output":
                output += message["data"]
            if str(tmp_path) in output:
                break
        websocket.send_json({"type": "close"})

    assert str(tmp_path) in output


def test_restart_endpoint_uses_injected_callback(tmp_path: Path) -> None:
    client = _client(tmp_path)
    calls: list[str] = []
    client.app.state.gateway_restart_callback = lambda: calls.append("restart")

    response = client.post("/restart", json={"node": "localhost"})

    assert response.status_code == 200
    assert response.json()["status"] == "restarting"
    assert response.json()["mode"] == "callback"
    assert calls == ["restart"]


def test_restart_endpoint_rejects_node_mismatch(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/restart", json={"node": "other"})

    assert response.status_code == 400
    assert "Node mismatch" in response.json()["detail"]


def test_llm_runtime_start_status_and_stop(tmp_path: Path) -> None:
    client = _client(tmp_path)

    started = client.post(
        "/llm/start",
        json={
            "node": "localhost",
            "command": "python -c 'import time; time.sleep(30)'",
            "conda_env": "",
            "cwd": str(tmp_path),
            "restart": True,
        },
    )

    assert started.status_code == 200
    started_payload = started.json()
    assert started_payload["running"] is True
    assert started_payload["status"] == "starting"
    assert started_payload["cwd"] == str(tmp_path)
    assert started_payload["log_path"]

    status = client.get("/llm/status", params={"node": "localhost"})
    assert status.status_code == 200
    assert status.json()["running"] is True

    log = client.get("/llm/log", params={"node": "localhost", "offset": 0})
    assert log.status_code == 200
    log_payload = log.json()
    assert log_payload["running"] is True
    assert log_payload["log_path"] == started_payload["log_path"]
    assert log_payload["next_offset"] >= log_payload["offset"]

    stopped = client.post("/llm/stop", json={"node": "localhost"})
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False


def test_llm_runtime_rejects_node_mismatch(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/llm/start",
        json={"node": "other", "command": "printf hello", "cwd": str(tmp_path)},
    )

    assert response.status_code == 400
    assert "Node mismatch" in response.json()["detail"]


def test_terminal_websocket_starts_shell_and_returns_pwd(tmp_path: Path) -> None:
    client = _client(tmp_path)

    with client.websocket_connect(
        f"/terminal/ws?node=localhost&session_id=term-pwd&initial_cwd={tmp_path}&rows=24&cols=80"
    ) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": str(tmp_path)}
        websocket.send_json({"type": "input", "data": "pwd\n"})
        output = ""
        for _ in range(40):
            message = websocket.receive_json()
            if message["type"] == "output":
                output += message["data"]
            if str(tmp_path) in output:
                break
        websocket.send_json({"type": "close"})

    assert str(tmp_path) in output


def test_terminal_websocket_emits_cwd_after_cd(tmp_path: Path) -> None:
    client = _client(tmp_path)

    with client.websocket_connect(
        f"/terminal/ws?node=localhost&session_id=term-cd&initial_cwd={tmp_path}&rows=24&cols=80"
    ) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": str(tmp_path)}
        websocket.send_json({"type": "input", "data": "cd /tmp\n"})
        cwd = None
        for _ in range(40):
            message = websocket.receive_json()
            if message["type"] == "cwd" and message["cwd"] == "/tmp":
                cwd = message["cwd"]
                break
        websocket.send_json({"type": "resize", "rows": 30, "cols": 100})
        websocket.send_json({"type": "close"})

    assert cwd == "/tmp"


def test_terminal_exec_stream_runs_inside_active_terminal(tmp_path: Path) -> None:
    client = _client(tmp_path, exec_timeout_seconds=5)

    with client.websocket_connect(
        f"/terminal/ws?node=localhost&session_id=term-exec&initial_cwd={tmp_path}&rows=24&cols=80"
    ) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": str(tmp_path)}
        response = client.post(
            "/terminal/exec/stream",
            json={
                "node": "localhost",
                "session_id": "term-exec",
                "command": "printf 'structured-output\\n'",
                "execution_id": "term-exec-command",
            },
        )
        assert response.status_code == 200
        body = response.text
        assert "structured-output" in body
        assert '"type": "completed"' in body
        assert '"exit_code": 0' in body

        visible = ""
        for _ in range(40):
            message = websocket.receive_json()
            if message["type"] == "output":
                visible += message["data"]
            if "structured-output" in visible:
                break
        websocket.send_json({"type": "close"})

    assert "structured-output" in visible


def test_terminal_exec_stream_waits_for_interactive_prompt_instead_of_timing_out(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, exec_timeout_seconds=0.1)

    with client.websocket_connect(
        f"/terminal/ws?node=localhost&session_id=term-prompt&initial_cwd={tmp_path}&rows=24&cols=80"
    ) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": str(tmp_path)}
        holder: dict[str, object] = {}

        def run_stream() -> None:
            with client.stream(
                "POST",
                "/terminal/exec/stream",
                json={
                    "node": "localhost",
                    "session_id": "term-prompt",
                    "command": (
                        "read -s -p \"Enter passphrase for key '/tmp/id_rsa': \" secret; "
                        "printf '\\nread:%s\\n' \"$secret\""
                    ),
                    "display_command": "git push origin HEAD",
                    "execution_id": "term-prompt-command",
                },
            ) as response:
                holder["status_code"] = response.status_code
                holder["body"] = response.read().decode()

        thread = threading.Thread(target=run_stream)
        thread.start()
        visible = ""
        for _ in range(40):
            message = websocket.receive_json()
            if message["type"] == "output":
                visible += message["data"]
            if "Enter passphrase for key" in visible:
                break
        websocket.send_json({"type": "input", "data": "sekret\n"})
        thread.join(timeout=5)
        websocket.send_json({"type": "close"})

    assert not thread.is_alive()
    assert holder["status_code"] == 200
    body = str(holder["body"])
    assert "event: terminal_input_required" in body
    assert "Enter the requested value in the terminal" in body
    assert "read:sekret" in body
    assert '"exit_code": 0' in body
    assert '"exit_code": 124' not in body


def test_terminal_input_detection_covers_common_ssh_and_auth_prompts() -> None:
    assert _terminal_output_needs_user_input(
        "Are you sure you want to continue connecting (yes/no/[fingerprint])? "
    )
    assert _terminal_output_needs_user_input("Username for 'https://github.com': ")
    assert _terminal_output_needs_user_input("Enter verification code from browser")


def test_terminal_write_displays_runtime_text(tmp_path: Path) -> None:
    client = _client(tmp_path)
    marker = tmp_path / "terminal-write-should-not-run"

    with client.websocket_connect(
        f"/terminal/ws?node=localhost&session_id=term-write&initial_cwd={tmp_path}&rows=24&cols=80"
    ) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": str(tmp_path)}
        response = client.post(
            "/terminal/write",
            json={
                "node": "localhost",
                "session_id": "term-write",
                "text": f"transform result\nprintf should-not-run > {marker}\n",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

        visible = ""
        for _ in range(20):
            message = websocket.receive_json()
            if message["type"] == "output":
                visible += message["data"]
            if "transform result" in visible:
                break
        websocket.send_json({"type": "close"})

    assert "transform result" in visible
    assert not marker.exists()


def test_terminal_input_writes_to_pty_and_submits_command(tmp_path: Path) -> None:
    client = _client(tmp_path)
    marker = tmp_path / "terminal-input-ran"

    with client.websocket_connect(
        f"/terminal/ws?node=localhost&session_id=term-input&initial_cwd={tmp_path}&rows=24&cols=80"
    ) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": str(tmp_path)}
        response = client.post(
            "/terminal/input",
            json={
                "node": "localhost",
                "session_id": "term-input",
                "text": f"printf input-ran > {marker}\n",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        deadline = time.time() + 2
        while time.time() < deadline and not marker.exists():
            time.sleep(0.05)
        websocket.send_json({"type": "close"})

    assert marker.read_text() == "input-ran"


def test_terminal_input_resets_prompt_detection_for_next_prompt(tmp_path: Path) -> None:
    client = _client(tmp_path, exec_timeout_seconds=5)
    holder: dict[str, object] = {}

    with client.websocket_connect(
        f"/terminal/ws?node=localhost&session_id=term-two-prompts&initial_cwd={tmp_path}&rows=24&cols=80"
    ) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": str(tmp_path)}

        def run_stream() -> None:
            with client.stream(
                "POST",
                "/terminal/exec/stream",
                json={
                    "node": "localhost",
                    "session_id": "term-two-prompts",
                    "command": (
                        "read -s -p \"Enter passphrase for key '/tmp/id_rsa': \" first; "
                        "printf '\\nfirst:%s\\n' \"$first\"; "
                        "read -s -p \"Enter passphrase for key '/tmp/id_rsa': \" second; "
                        "printf '\\nsecond:%s\\n' \"$second\""
                    ),
                    "display_command": "two prompted reads",
                    "execution_id": "term-two-prompts-command",
                },
            ) as response:
                holder["status_code"] = response.status_code
                holder["body"] = response.read().decode()

        thread = threading.Thread(target=run_stream)
        thread.start()
        visible = ""
        for _ in range(40):
            message = websocket.receive_json()
            if message["type"] == "output":
                visible += message["data"]
            if "Enter passphrase for key" in visible:
                break
        response = client.post(
            "/terminal/input",
            json={"node": "localhost", "session_id": "term-two-prompts", "text": "one\n"},
        )
        assert response.status_code == 200
        for _ in range(40):
            message = websocket.receive_json()
            if message["type"] == "output":
                visible += message["data"]
            if "first:one" in visible and visible.count("Enter passphrase for key") >= 2:
                break
        time.sleep(0.2)
        response = client.post(
            "/terminal/input",
            json={"node": "localhost", "session_id": "term-two-prompts", "text": "two\n"},
        )
        assert response.status_code == 200
        thread.join(timeout=5)
        websocket.send_json({"type": "close"})

    assert not thread.is_alive()
    assert holder["status_code"] == 200
    body = str(holder["body"])
    assert body.count("event: terminal_input_required") >= 2
    assert "first:one" in body
    assert "second:two" in body
    assert '"exit_code": 0' in body


def test_terminal_session_endpoint_creates_background_pty(tmp_path: Path) -> None:
    client = _client(tmp_path)
    marker = tmp_path / "background-terminal-ran"

    created = client.post(
        "/terminal/session",
        json={
            "node": "localhost",
            "session_id": "term-background",
            "initial_cwd": str(tmp_path),
            "rows": 24,
            "cols": 80,
        },
    )

    assert created.status_code == 200
    assert created.json() == {
        "ok": True,
        "session_id": "term-background",
        "cwd": str(tmp_path),
    }

    response = client.post(
        "/terminal/input",
        json={
            "node": "localhost",
            "session_id": "term-background",
            "text": f"printf background-ran > {marker}\n",
        },
    )
    assert response.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline and not marker.exists():
        time.sleep(0.05)

    assert marker.read_text() == "background-ran"


def test_terminal_exec_detach_starts_terminal_owned_command(tmp_path: Path) -> None:
    client = _client(tmp_path, exec_timeout_seconds=5)

    with client.websocket_connect(
        f"/terminal/ws?node=localhost&session_id=term-detach&initial_cwd={tmp_path}&rows=24&cols=80"
    ) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": str(tmp_path)}
        response = client.post(
            "/terminal/exec/detach",
            json={
                "node": "localhost",
                "session_id": "term-detach",
                "command": "printf 'detached-output\\n'",
            },
        )
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "session_id": "term-detach",
            "message": "Command started in terminal.",
        }

        visible = ""
        for _ in range(40):
            message = websocket.receive_json()
            if message["type"] == "output":
                visible += message["data"]
            if "detached-output" in visible:
                break
        websocket.send_json({"type": "close"})

    assert "detached-output" in visible


def test_terminal_websocket_rejects_node_mismatch(tmp_path: Path) -> None:
    client = _client(tmp_path)

    with client.websocket_connect("/terminal/ws?node=edge&session_id=term-bad") as websocket:
        message = websocket.receive_json()

    assert message == {
        "type": "error",
        "message": "Node mismatch. This agent serves node 'localhost'.",
    }


def test_terminal_session_batches_consecutive_output_events() -> None:
    session = object.__new__(TerminalSession)
    session._events = queue.Queue()
    session._pending_event = None
    session._events.put({"type": "output", "data": "alpha"})
    session._events.put({"type": "output", "data": " beta"})
    session._events.put({"type": "cwd", "cwd": "/tmp"})

    assert session.next_event(0.01) == {"type": "output", "data": "alpha beta"}
    assert session.next_event(0.01) == {"type": "cwd", "cwd": "/tmp"}


def test_terminal_capture_flushes_prompt_without_newline() -> None:
    session = object.__new__(TerminalSession)
    session._capture_lock = threading.RLock()
    capture = _TerminalCommandCapture(
        marker_id="test",
        execution_id="exec-test",
        start_marker="__AOR_EXEC_START_test__",
        end_marker_prefix="__AOR_EXEC_END_test__:",
    )
    session._active_capture = capture

    visible = session._process_command_capture(
        "\n__AOR_EXEC_START_test__\nEnter passphrase for key '/tmp/id_rsa': "
    )

    assert "Enter passphrase" in visible
    event = capture.events.get_nowait()
    assert event.type == "stdout"
    assert "Enter passphrase" in event.text
    assert capture.buffer == ""
    assert capture.capturing is True
    assert capture.completed is False


def test_terminal_capture_preserves_split_end_marker() -> None:
    session = object.__new__(TerminalSession)
    session._capture_lock = threading.RLock()
    marker = "__AOR_EXEC_END_test__:"
    capture = _TerminalCommandCapture(
        marker_id="test",
        execution_id="exec-test",
        start_marker="__AOR_EXEC_START_test__",
        end_marker_prefix=marker,
        capturing=True,
    )
    session._active_capture = capture

    visible = session._process_command_capture("output before marker\n__AOR_EXEC_EN")

    assert visible == "output before marker\n"
    assert capture.events.get_nowait().text == "output before marker\n"
    assert capture.buffer == "__AOR_EXEC_EN"

    session._process_command_capture("D_test__:0\n")

    completed = capture.events.get_nowait()
    assert completed.type == "completed"
    assert completed.exit_code == 0
    assert capture.completed is True


def test_exec_runs_command_successfully(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/exec", json={"node": "localhost", "command": "printf 'hello'"})

    assert response.status_code == 200
    assert response.json() == {"stdout": "hello", "stderr": "", "exit_code": 0}


def test_exec_receives_request_environment(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/exec",
        json={
            "node": "localhost",
            "command": 'printf "%s" "$OF_INPUT_COMMIT_HASH"',
            "env": {"OF_INPUT_COMMIT_HASH": "abc123"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"stdout": "abc123", "stderr": "", "exit_code": 0}


def test_exec_closes_stdin_by_default(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/exec", json={"node": "localhost", "command": "cat"})

    assert response.status_code == 200
    assert response.json() == {"stdout": "", "stderr": "", "exit_code": 0}


def test_exec_receives_request_stdin(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/exec",
        json={"node": "localhost", "command": "cat", "stdin": "hello from stdin\n"},
    )

    assert response.status_code == 200
    assert response.json() == {"stdout": "hello from stdin\n", "stderr": "", "exit_code": 0}


def test_exec_returns_non_zero_exit_without_transport_failure(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/exec", json={"node": "localhost", "command": "echo 'nope' >&2; exit 7"})

    assert response.status_code == 200
    assert response.json() == {"stdout": "", "stderr": "nope\n", "exit_code": 7}


def test_exec_rejects_node_mismatch(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/exec", json={"node": "edge-1", "command": "hostname"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Node mismatch. This agent serves node 'localhost'."


def test_exec_rejects_blank_command(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/exec", json={"node": "localhost", "command": "   "})

    assert response.status_code == 400
    assert response.json()["detail"] == "Command is required."


def test_exec_times_out_with_exit_code_124(tmp_path: Path) -> None:
    client = _client(tmp_path, exec_timeout_seconds=0.01)

    response = client.post("/exec", json={"node": "localhost", "command": "sleep 0.1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["stdout"] == ""
    assert payload["exit_code"] == 124
    assert "timed out" in payload["stderr"]


def test_exec_timeout_zero_disables_timeout(tmp_path: Path) -> None:
    settings = Settings(node_name="localhost", workdir=tmp_path, exec_timeout_seconds=0)

    payload = execute_command(settings, "sleep 0.05; printf done")

    assert payload.exit_code == 0
    assert payload.stdout == "done"
    assert "timed out" not in payload.stderr


def test_exec_stream_emits_stdout_stderr_and_completion(tmp_path: Path) -> None:
    client = _client(tmp_path)

    with client.stream(
        "POST",
        "/exec/stream",
        json={"node": "localhost", "command": "printf 'hello\\n'; printf 'oops\\n' >&2"},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: stdout" in body
    assert "event: stderr" in body
    assert "event: completed" in body
    assert "hello\\n" in body
    assert "oops\\n" in body
    assert '"exit_code": 0' in body


def test_exec_stream_receives_request_stdin(tmp_path: Path) -> None:
    client = _client(tmp_path)

    with client.stream(
        "POST",
        "/exec/stream",
        json={"node": "localhost", "command": "cat", "stdin": "streamed stdin\n"},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: stdout" in body
    assert "streamed stdin\\n" in body
    assert '"exit_code": 0' in body


def test_exec_stream_reports_timeout_completion(tmp_path: Path) -> None:
    client = _client(tmp_path, exec_timeout_seconds=0.01)

    with client.stream(
        "POST",
        "/exec/stream",
        json={"node": "localhost", "command": "sleep 0.1"},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: stderr" in body
    assert "timed out" in body
    assert "event: completed" in body
    assert '"exit_code": 124' in body


def test_exec_stream_timeout_zero_disables_timeout(tmp_path: Path) -> None:
    client = _client(tmp_path, exec_timeout_seconds=0)

    with client.stream(
        "POST",
        "/exec/stream",
        json={"node": "localhost", "command": "sleep 0.05; printf done"},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "done" in body
    assert "timed out" not in body
    assert '"exit_code": 0' in body


def test_stream_command_can_be_cancelled_by_execution_id(tmp_path: Path) -> None:
    settings = Settings(node_name="localhost", workdir=tmp_path, exec_timeout_seconds=10)
    events = []

    def run_stream() -> None:
        events.extend(stream_command(settings, "sleep 10", execution_id="cancel-test"))

    thread = threading.Thread(target=run_stream, daemon=True)
    thread.start()
    deadline = time.time() + 2
    cancelled = False
    while time.time() < deadline:
        cancelled = cancel_command("cancel-test")
        if cancelled:
            break
        time.sleep(0.02)
    thread.join(timeout=2)

    assert cancelled is True
    assert not thread.is_alive()
    assert any(event.type == "cancelled" for event in events)
    assert events[-1].type == "completed"
    assert events[-1].exit_code == CANCELLED_EXIT_CODE


def test_exec_cancel_returns_false_for_unknown_execution(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/exec/cancel",
        json={"node": "localhost", "execution_id": "missing"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "cancelled": False,
        "execution_id": "missing",
        "message": "No active command matched that execution id.",
    }


def test_exec_traces_command_only_when_enabled(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    client = _client(tmp_path, trace_commands=True)

    response = client.post("/exec", json={"node": "localhost", "command": "printf 'hello'"})

    assert response.status_code == 200
    assert "Gateway exec on localhost: printf 'hello'" in caplog.text


def test_exec_does_not_trace_command_by_default(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    client = _client(tmp_path)

    response = client.post("/exec", json={"node": "localhost", "command": "printf 'hello'"})

    assert response.status_code == 200
    assert "Gateway exec on localhost: printf 'hello'" not in caplog.text


def test_exec_stream_traces_command_only_when_enabled(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    client = _client(tmp_path, trace_commands=True)

    with client.stream(
        "POST",
        "/exec/stream",
        json={"node": "localhost", "command": "printf 'hello\\n'"},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: completed" in body
    assert "Gateway exec stream on localhost: printf 'hello\\n'" in caplog.text
