from __future__ import annotations

import socket

from agent_runtime.api.config import Settings
from agent_runtime.api.llm_health import (
    check_llm_service,
    endpoint_from_base_url,
    main,
)


def test_endpoint_from_base_url_uses_explicit_port() -> None:
    endpoint = endpoint_from_base_url("http://192.168.100.25:8000/v1")

    assert endpoint.host == "192.168.100.25"
    assert endpoint.port == 8000
    assert endpoint.base_url == "http://192.168.100.25:8000/v1"


def test_endpoint_from_base_url_uses_scheme_defaults() -> None:
    assert endpoint_from_base_url("http://llm.example/v1").port == 80
    assert endpoint_from_base_url("https://llm.example/v1").port == 443


def test_check_llm_service_reports_success(monkeypatch) -> None:
    calls: list[tuple[tuple[str, int], float]] = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_create_connection(address, timeout):
        calls.append((address, timeout))
        return FakeSocket()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    result = check_llm_service(Settings(llm_base_url="http://127.0.0.1:8000/v1"), 2.5)

    assert result.ok is True
    assert result.endpoint is not None
    assert result.endpoint.host == "127.0.0.1"
    assert calls == [(("127.0.0.1", 8000), 2.5)]


def test_check_llm_service_reports_connection_failure(monkeypatch) -> None:
    def fake_create_connection(address, timeout):
        _ = address, timeout
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    result = check_llm_service(Settings(llm_base_url="http://127.0.0.1:8000/v1"), 0.01)

    assert result.ok is False
    assert "not reachable" in result.message
    assert "connection refused" in result.message


def test_llm_health_cli_exits_nonzero_for_missing_config(tmp_path, capsys) -> None:
    missing = tmp_path / "missing.yaml"

    exit_code = main(["--config", str(missing), "--timeout", "0.01"])

    assert exit_code == 1
    assert "LLM preflight failed while loading settings" in capsys.readouterr().err
