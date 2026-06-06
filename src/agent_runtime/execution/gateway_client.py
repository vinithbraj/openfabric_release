"""HTTP client for gateway-backed capability execution."""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterator
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from pydantic import BaseModel, ConfigDict

from agent_runtime.core.config import RuntimeConfig
from agent_runtime.execution.errors import GatewayConfigurationError, GatewayExecutionError


class _GatewayExecResponse(BaseModel):
    """Compact HTTP response returned by the generic gateway exec endpoint."""

    model_config = ConfigDict(extra="forbid")

    stdout: str
    stderr: str
    exit_code: int


class GatewayClient:
    """Execute gateway-backed tools through the generic gateway HTTP API."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def resolve_node(self, execution_context: dict[str, Any]) -> str:
        """Resolve the target gateway node from request context or runtime defaults."""

        for key in ("gateway_node", "node", "target_node"):
            value = str(execution_context.get(key) or "").strip()
            if value:
                return value
        value = str(self.config.gateway_default_node or "").strip()
        if value:
            return value
        raise GatewayConfigurationError("Gateway default node is not configured.")

    def resolve_url(
        self,
        node: str,
        execution_context: dict[str, Any] | None = None,
    ) -> str:
        """Resolve the gateway base URL for one node."""

        context = dict(execution_context or {})
        context_endpoints = context.get("gateway_endpoints")
        if isinstance(context_endpoints, dict):
            gateway_url = str(context_endpoints.get(node, "") or "").strip()
            if gateway_url:
                return gateway_url.rstrip("/")
        context_node = ""
        for key in ("gateway_node", "node", "target_node"):
            context_node = str(context.get(key) or "").strip()
            if context_node:
                break
        context_gateway_url = str(context.get("gateway_url") or "").strip()
        if context_gateway_url and (not context_node or context_node == node):
            return context_gateway_url.rstrip("/")
        gateway_url = str(self.config.gateway_endpoints.get(node, "") or self.config.gateway_url or "").strip()
        if gateway_url:
            return gateway_url.rstrip("/")
        raise GatewayConfigurationError(f"Gateway URL is not configured for node: {node}.")

    def _post_exec(
        self,
        url: str,
        node: str,
        command: str,
        execution_id: str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> _GatewayExecResponse:
        """Send one command to the gateway exec endpoint and validate the HTTP payload."""

        request_payload = {"node": node, "command": command}
        if env:
            request_payload["env"] = dict(env)
        if stdin is not None:
            request_payload["stdin"] = str(stdin)
        if execution_id:
            request_payload["execution_id"] = execution_id
        payload = json.dumps(request_payload).encode("utf-8")
        request = urllib_request.Request(
            f"{url}/exec",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=None) as response:
                raw_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise GatewayExecutionError(
                f"Gateway request failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise GatewayExecutionError(f"Gateway request failed: {exc.reason}") from exc

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise GatewayExecutionError("Gateway returned invalid JSON for exec response.") from exc
        return _GatewayExecResponse.model_validate(body)

    @staticmethod
    def _iter_sse_events(response: Any) -> Iterator[tuple[str, str]]:
        """Yield ``(event_type, data)`` pairs from one SSE HTTP response."""

        event_type = "message"
        data_lines: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    yield event_type, "\n".join(data_lines)
                event_type = "message"
                data_lines = []
                continue
            if line.startswith("event:"):
                event_type = line.removeprefix("event:").strip() or "message"
                continue
            if line.startswith("data:"):
                data = line.removeprefix("data:")
                if data.startswith(" "):
                    data = data[1:]
                data_lines.append(data)
        if data_lines:
            yield event_type, "\n".join(data_lines)

    def _post_exec_stream(
        self,
        url: str,
        node: str,
        command: str,
        execution_id: str | None = None,
        *,
        path: str = "/exec/stream",
        extra_payload: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Send one command to the gateway streaming exec endpoint."""

        request_payload = {"node": node, "command": command}
        request_payload.update(dict(extra_payload or {}))
        if env:
            request_payload["env"] = dict(env)
        if stdin is not None:
            request_payload["stdin"] = str(stdin)
        if execution_id:
            request_payload["execution_id"] = execution_id
        payload = json.dumps(request_payload).encode("utf-8")
        request = urllib_request.Request(
            f"{url}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=None) as response:
                for event_type, data in self._iter_sse_events(response):
                    try:
                        body = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise GatewayExecutionError("Gateway returned invalid JSON for stream event.") from exc
                    if not isinstance(body, dict):
                        raise GatewayExecutionError("Gateway returned non-object JSON for stream event.")
                    body.setdefault("type", event_type)
                    yield body
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise GatewayExecutionError(
                f"Gateway stream request failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise GatewayExecutionError(f"Gateway stream request failed: {exc.reason}") from exc

    def _post_exec_cancel(self, url: str, node: str, execution_id: str) -> dict[str, Any]:
        """Send one cancellation request to the gateway."""

        payload = json.dumps({"node": node, "execution_id": execution_id}).encode("utf-8")
        request = urllib_request.Request(
            f"{url}/exec/cancel",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.config.gateway_timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise GatewayExecutionError(
                f"Gateway cancel request failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise GatewayExecutionError(f"Gateway cancel request failed: {exc.reason}") from exc

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise GatewayExecutionError("Gateway returned invalid JSON for cancel response.") from exc
        if not isinstance(body, dict):
            raise GatewayExecutionError("Gateway returned non-object JSON for cancel response.")
        return body

    def _post_terminal_write(self, url: str, node: str, session_id: str, text: str) -> dict[str, Any]:
        payload = json.dumps({"node": node, "session_id": session_id, "text": text}).encode("utf-8")
        request = urllib_request.Request(
            f"{url}/terminal/write",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.config.gateway_timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise GatewayExecutionError(
                f"Gateway terminal write failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise GatewayExecutionError(f"Gateway terminal write failed: {exc.reason}") from exc

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise GatewayExecutionError("Gateway returned invalid JSON for terminal write response.") from exc
        if not isinstance(body, dict):
            raise GatewayExecutionError("Gateway returned non-object JSON for terminal write response.")
        return body

    def _post_terminal_session(
        self,
        url: str,
        node: str,
        *,
        session_id: str | None = None,
        initial_cwd: str | None = None,
        rows: int | None = None,
        cols: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "node": node,
            **({"session_id": session_id} if session_id else {}),
            **({"initial_cwd": initial_cwd} if initial_cwd else {}),
            **({"rows": rows} if rows else {}),
            **({"cols": cols} if cols else {}),
        }
        request = urllib_request.Request(
            f"{url}/terminal/session",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.config.gateway_timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise GatewayExecutionError(
                f"Gateway terminal session failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise GatewayExecutionError(f"Gateway terminal session failed: {exc.reason}") from exc

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise GatewayExecutionError("Gateway returned invalid JSON for terminal session response.") from exc
        if not isinstance(body, dict):
            raise GatewayExecutionError("Gateway returned non-object JSON for terminal session response.")
        return body

    def _post_terminal_input(self, url: str, node: str, session_id: str, text: str) -> dict[str, Any]:
        payload = json.dumps({"node": node, "session_id": session_id, "text": text}).encode("utf-8")
        request = urllib_request.Request(
            f"{url}/terminal/input",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.config.gateway_timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise GatewayExecutionError(
                f"Gateway terminal input failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise GatewayExecutionError(f"Gateway terminal input failed: {exc.reason}") from exc

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise GatewayExecutionError("Gateway returned invalid JSON for terminal input response.") from exc
        if not isinstance(body, dict):
            raise GatewayExecutionError("Gateway returned non-object JSON for terminal input response.")
        return body

    def _post_terminal_detach(self, url: str, node: str, session_id: str, command: str) -> dict[str, Any]:
        payload = json.dumps({"node": node, "session_id": session_id, "command": command}).encode("utf-8")
        request = urllib_request.Request(
            f"{url}/terminal/exec/detach",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.config.gateway_timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise GatewayExecutionError(
                f"Gateway terminal detach failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise GatewayExecutionError(f"Gateway terminal detach failed: {exc.reason}") from exc

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise GatewayExecutionError("Gateway returned invalid JSON for terminal detach response.") from exc
        if not isinstance(body, dict):
            raise GatewayExecutionError("Gateway returned non-object JSON for terminal detach response.")
        return body

    @staticmethod
    def execution_id_from_context(execution_context: dict[str, Any] | None) -> str | None:
        """Return the gateway execution id for the request, when one is available."""

        context = dict(execution_context or {})
        for key in ("gateway_execution_id", "request_id", "run_id"):
            value = str(context.get(key) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def shell_env_from_context(execution_context: dict[str, Any] | None) -> dict[str, str]:
        """Return shell environment values supplied by the operator runtime."""

        context = dict(execution_context or {})
        raw = context.get("shell_env")
        if not isinstance(raw, dict):
            return {}
        env: dict[str, str] = {}
        for key, value in raw.items():
            name = str(key or "").strip()
            if name:
                env[name] = str(value)
        return env

    @staticmethod
    def shell_stdin_from_context(execution_context: dict[str, Any] | None) -> str | None:
        """Return shell stdin supplied by the operator runtime, if any."""

        context = dict(execution_context or {})
        if "shell_stdin" not in context:
            return None
        return str(context.get("shell_stdin") or "")

    @staticmethod
    def shell_env_export_prefix(shell_env: dict[str, str]) -> str:
        """Return shell assignments that remain visible across compound commands."""

        if not shell_env:
            return ""
        exports = [
            f"export {name}={shlex.quote(value)}"
            for name, value in sorted(shell_env.items())
        ]
        return "; ".join(exports) + "; "

    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one already-validated operator command through gateway /exec."""

        context = dict(execution_context or {})
        target_node = self.resolve_node(context)
        gateway_url = self.resolve_url(target_node, context)
        cwd_prefix = f"cd {shlex.quote(str(cwd or '.'))} && "
        shell_env = self.shell_env_from_context(execution_context)
        shell_stdin = self.shell_stdin_from_context(execution_context)
        exec_response = self._post_exec(
            gateway_url,
            target_node,
            cwd_prefix + str(command or ""),
            self.execution_id_from_context(execution_context),
            shell_env,
            shell_stdin,
        )
        return {
            "stdout": exec_response.stdout,
            "stderr": exec_response.stderr,
            "exit_code": exec_response.exit_code,
            "gateway_node": target_node,
            "gateway_url": gateway_url,
        }

    def stream_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream one already-validated operator command through gateway /exec/stream."""

        context = dict(execution_context or {})
        target_node = self.resolve_node(context)
        gateway_url = self.resolve_url(target_node, context)
        cwd_prefix = f"cd {shlex.quote(str(cwd or '.'))} && "
        shell_env = self.shell_env_from_context(execution_context)
        shell_stdin = self.shell_stdin_from_context(execution_context)
        for event in self._post_exec_stream(
            gateway_url,
            target_node,
            cwd_prefix + str(command or ""),
            self.execution_id_from_context(execution_context),
            env=shell_env,
            stdin=shell_stdin,
        ):
            payload = dict(event)
            payload["gateway_node"] = target_node
            payload["gateway_url"] = gateway_url
            yield payload

    def stream_terminal_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
        display_command: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream a command through the active gateway terminal session."""

        context = dict(execution_context or {})
        session_id = str(context.get("terminal_session_id") or "").strip()
        if not session_id:
            raise GatewayExecutionError("Terminal execution requires an active terminal session.")
        target_node = self.resolve_node(context)
        gateway_url = self.resolve_url(target_node, context)
        command_text = str(command or "")
        shell_env = self.shell_env_from_context(execution_context)
        if shell_env:
            command_text = f"{self.shell_env_export_prefix(shell_env)}{command_text}"
        terminal_cwd = str(context.get("terminal_cwd") or "").strip()
        requested_cwd = str(cwd or "").strip()
        if requested_cwd and terminal_cwd and requested_cwd != terminal_cwd:
            command_text = f"cd {shlex.quote(requested_cwd)} && {command_text}"
            if display_command:
                display_command = f"cd {shlex.quote(requested_cwd)} && {display_command}"
        for event in self._post_exec_stream(
            gateway_url,
            target_node,
            command_text,
            self.execution_id_from_context(execution_context),
            path="/terminal/exec/stream",
            extra_payload={
                "session_id": session_id,
                **({"display_command": display_command} if display_command else {}),
            },
        ):
            payload = dict(event)
            payload["gateway_node"] = target_node
            payload["gateway_url"] = gateway_url
            payload["terminal_session_id"] = session_id
            payload["terminal_dispatch"] = True
            yield payload

    def create_terminal_session(
        self,
        *,
        session_id: str | None = None,
        initial_cwd: str | None = None,
        rows: int | None = None,
        cols: int | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or revive a gateway PTY terminal session without a browser WebSocket."""

        context = dict(execution_context or {})
        target_node = self.resolve_node(context)
        gateway_url = self.resolve_url(target_node, context)
        payload = self._post_terminal_session(
            gateway_url,
            target_node,
            session_id=session_id,
            initial_cwd=initial_cwd,
            rows=rows,
            cols=cols,
        )
        payload["gateway_node"] = target_node
        payload["gateway_url"] = gateway_url
        return payload

    def cancel_raw_command(
        self,
        *,
        execution_id: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Cancel an in-flight operator command through gateway /exec/cancel."""

        context = dict(execution_context or {})
        target_node = self.resolve_node(context)
        gateway_url = self.resolve_url(target_node, context)
        payload = self._post_exec_cancel(gateway_url, target_node, str(execution_id or ""))
        payload["gateway_node"] = target_node
        payload["gateway_url"] = gateway_url
        return payload

    def write_terminal_output(
        self,
        *,
        text: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mirror runtime-produced output into the active terminal session."""

        context = dict(execution_context or {})
        session_id = str(context.get("terminal_session_id") or "").strip()
        if not session_id:
            raise GatewayExecutionError("Terminal output mirroring requires an active terminal session.")
        target_node = self.resolve_node(context)
        gateway_url = self.resolve_url(target_node, context)
        payload = self._post_terminal_write(gateway_url, target_node, session_id, str(text or ""))
        payload["gateway_node"] = target_node
        payload["gateway_url"] = gateway_url
        payload["terminal_session_id"] = session_id
        return payload

    def write_terminal_input(
        self,
        *,
        text: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write runtime-provided input into the active terminal PTY."""

        context = dict(execution_context or {})
        session_id = str(context.get("terminal_session_id") or "").strip()
        if not session_id:
            raise GatewayExecutionError("Terminal input delivery requires an active terminal session.")
        target_node = self.resolve_node(context)
        gateway_url = self.resolve_url(target_node, context)
        payload = self._post_terminal_input(gateway_url, target_node, session_id, str(text or ""))
        payload["gateway_node"] = target_node
        payload["gateway_url"] = gateway_url
        payload["terminal_session_id"] = session_id
        return payload

    def start_terminal_detached_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a terminal-owned command without runtime output capture."""

        context = dict(execution_context or {})
        session_id = str(context.get("terminal_session_id") or "").strip()
        if not session_id:
            raise GatewayExecutionError("Detached terminal execution requires an active terminal session.")
        target_node = self.resolve_node(context)
        gateway_url = self.resolve_url(target_node, context)
        command_text = str(command or "")
        terminal_cwd = str(context.get("terminal_cwd") or "").strip()
        requested_cwd = str(cwd or "").strip()
        if requested_cwd and terminal_cwd and requested_cwd != terminal_cwd:
            command_text = f"cd {shlex.quote(requested_cwd)} && {command_text}"
        payload = self._post_terminal_detach(gateway_url, target_node, session_id, command_text)
        payload["gateway_node"] = target_node
        payload["gateway_url"] = gateway_url
        payload["terminal_session_id"] = session_id
        payload["terminal_dispatch"] = True
        payload["terminal_detached"] = True
        return payload
