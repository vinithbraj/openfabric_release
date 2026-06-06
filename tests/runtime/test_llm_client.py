from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError

import pytest

import agent_runtime.llm.client as client_module
from agent_runtime.llm.client import LLMClientError, OpenAICompatLLMClient


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeStreamingResponse:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def __enter__(self) -> "FakeStreamingResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def __iter__(self):
        for line in self.lines:
            yield line.encode("utf-8")


def test_openai_compat_client_auto_discovers_vllm_model(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        calls.append({"url": request.full_url, "data": request.data, "timeout": timeout})
        if request.full_url.endswith("/models"):
            return FakeResponse({"data": [{"id": "served-by-vllm"}]})
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["model"] == "served-by-vllm"
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    llm = OpenAICompatLLMClient(
        base_url="http://127.0.0.1:8000/v1",
        api_key="local",
        model="auto",
    )

    assert llm.complete_json("hello", {"type": "object"}) == {"ok": True}
    assert llm.complete_json("again", {"type": "object"}) == {"ok": True}
    assert [call["url"] for call in calls].count("http://127.0.0.1:8000/v1/models") == 1
    assert llm.model == "served-by-vllm"


def test_openai_compat_client_keeps_explicit_model(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        calls.append(request.full_url)
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["model"] == "pinned-model"
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    llm = OpenAICompatLLMClient(
        base_url="http://127.0.0.1:8000/v1",
        api_key="local",
        model="pinned-model",
    )

    assert llm.complete_json("hello", {"type": "object"}) == {"ok": True}
    assert calls == ["http://127.0.0.1:8000/v1/chat/completions"]


def test_openai_compat_client_sends_max_tokens(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    payloads: list[dict[str, Any]] = []

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    llm = OpenAICompatLLMClient(
        base_url="http://127.0.0.1:8000/v1",
        api_key="local",
        model="pinned-model",
        max_tokens=777,
    )

    assert llm.complete_json("hello", {"type": "object"}) == {"ok": True}
    assert payloads[0]["max_tokens"] == 777


def test_openai_compat_client_streams_and_parses_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    payloads: list[dict[str, Any]] = []

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        assert payload["stream"] is True
        return FakeStreamingResponse(
            [
                'data: {"choices":[{"delta":{"content":"{\\"ok\\""}}]}',
                'data: {"choices":[{"delta":{"content":": true}"}}]}',
                "data: [DONE]",
            ]
        )

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    llm = OpenAICompatLLMClient(
        base_url="http://127.0.0.1:8000/v1",
        api_key="local",
        model="pinned-model",
    )
    deltas: list[str] = []

    assert llm.complete_json_stream("hello", {"type": "object"}, deltas.append) == {"ok": True}
    assert "".join(deltas) == '{"ok": true}'
    assert payloads[0]["stream"] is True


def test_openai_compat_client_streaming_rejection_falls_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    payloads: list[dict[str, Any]] = []

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        payload = json.loads(request.data.decode("utf-8"))
        payloads.append(payload)
        if payload.get("stream") is True:
            raise HTTPError(request.full_url, 400, "streaming rejected", hdrs=None, fp=None)
        return FakeResponse({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    llm = OpenAICompatLLMClient(
        base_url="http://127.0.0.1:8000/v1",
        api_key="local",
        model="pinned-model",
    )

    assert llm.complete_json_stream("hello", {"type": "object"}, lambda _delta: None) == {"ok": True}
    assert any(payload.get("stream") is True for payload in payloads)
    assert any(payload.get("stream") is not True for payload in payloads)


def test_openai_compat_client_wraps_malformed_assistant_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    malformed = '{"ok": true "missing_comma": false}'

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        return FakeResponse({"choices": [{"message": {"content": malformed}}]})

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    llm = OpenAICompatLLMClient(
        base_url="http://127.0.0.1:8000/v1",
        api_key="local",
        model="pinned-model",
    )

    with pytest.raises(LLMClientError) as raised:
        llm.complete_json("hello", {"type": "object"})

    assert raised.value.error_kind == "invalid_json"
    assert "Expecting ',' delimiter" in raised.value.error_message
    assert raised.value.raw_response_preview == malformed
