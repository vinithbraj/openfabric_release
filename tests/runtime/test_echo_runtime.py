from __future__ import annotations

from fastapi.testclient import TestClient

from agent_runtime.api.app import create_app
from agent_runtime.api.config import Settings
from agent_runtime.api.constants import DEFAULT_COMPAT_SPEC_PATH
from agent_runtime.api.runtime.engine import ExecutionEngine, extract_prompt


class FakeAgentRuntime:
    """Small fake runtime injected into API route tests."""

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        payload = dict(context or {})
        callback = payload.get("event_callback")
        if callable(callback):
            callback("[Prompt Classification] Started\n\nPlanning is underway.\n\n")
        return f"handled: {raw_prompt}"


class FailingAgentRuntime:
    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        _ = context
        raise RuntimeError(f"boom: {raw_prompt}")


def _client() -> TestClient:
    settings = Settings()
    return TestClient(create_app(settings, agent_runtime=FakeAgentRuntime()))


def test_create_app_defaults_agent_runtime_gateway_to_localhost() -> None:
    settings = Settings(gateway_url=None, gateway_endpoints={}, default_node=None)

    app = create_app(settings)

    runtime = app.state.agent_runtime
    assert runtime.execution_engine.safety_policy.config.gateway_default_node == "localhost"
    assert runtime.execution_engine.safety_policy.config.gateway_url == "http://127.0.0.1:8787"


def test_create_app_preserves_explicit_gateway_configuration() -> None:
    settings = Settings(
        gateway_url="http://gateway.example:9000",
        gateway_endpoints={"worker-a": "http://gateway.worker-a:8787"},
        default_node="worker-a",
    )

    app = create_app(settings)

    runtime = app.state.agent_runtime
    assert runtime.execution_engine.safety_policy.config.gateway_default_node == "worker-a"
    assert runtime.execution_engine.safety_policy.config.gateway_url == "http://gateway.worker-a:8787"


def test_create_app_enables_shell_when_shell_mode_is_not_disabled() -> None:
    settings = Settings(shell_mode="read_only")

    app = create_app(settings)

    runtime = app.state.agent_runtime
    assert runtime.execution_engine.safety_policy.config.allow_shell_execution is True


def test_create_app_disables_shell_when_shell_mode_is_disabled() -> None:
    settings = Settings(shell_mode="disabled")

    app = create_app(settings)

    runtime = app.state.agent_runtime
    assert runtime.execution_engine.safety_policy.config.allow_shell_execution is False


def test_extract_prompt_prefers_task_field() -> None:
    assert extract_prompt({"task": "hello", "prompt": "ignored"}) == "hello"


def test_engine_echoes_prompt() -> None:
    engine = ExecutionEngine(Settings(), agent_runtime=FakeAgentRuntime())

    result = engine.run_spec(DEFAULT_COMPAT_SPEC_PATH, {"task": "count patients"})

    assert result["status"] == "completed"
    assert result["final_output"]["content"] == "handled: count patients"


def test_engine_returns_failed_session_with_specific_error_detail() -> None:
    engine = ExecutionEngine(Settings(), agent_runtime=FailingAgentRuntime())

    result = engine.run_spec(DEFAULT_COMPAT_SPEC_PATH, {"task": "count patients"})

    assert result["status"] == "failed"
    assert "RuntimeError: boom: count patients" in result["final_output"]["content"]
    assert result["latest_snapshot"]["error_detail"]["category"] == "unexpected_error"


def test_healthz_reports_echo_mode() -> None:
    response = _client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "agent_runtime"}


def test_openai_compatible_endpoints_are_removed() -> None:
    client = _client()

    assert client.get("/v1/models").status_code == 404
    response = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 404


def test_run_endpoint_echoes_prompt() -> None:
    client = _client()

    response = client.post(
        "/runs",
        json={"spec_path": DEFAULT_COMPAT_SPEC_PATH, "input": {"task": "echo me"}},
    )

    assert response.status_code == 200
    assert response.json()["final_output"]["content"] == "handled: echo me"


def test_run_endpoint_uses_neutral_default_spec_path() -> None:
    client = _client()

    response = client.post(
        "/runs",
        json={"input": {"task": "echo default"}},
    )

    assert response.status_code == 200
    assert response.json()["spec_path"] == DEFAULT_COMPAT_SPEC_PATH
    assert response.json()["final_output"]["content"] == "handled: echo default"


def test_session_resume_routes_through_agent_runtime() -> None:
    client = _client()

    created = client.post(
        "/sessions?run_immediately=false",
        json={"input": {"task": "resume me"}},
    )

    assert created.status_code == 200
    session_id = created.json()["id"]
    assert created.json()["status"] == "pending"

    resumed = client.post(
        f"/sessions/{session_id}/resume",
        json={"trigger": "manual"},
    )

    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"
    assert resumed.json()["final_output"]["content"] == "handled: resume me"
