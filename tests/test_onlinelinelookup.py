from __future__ import annotations

import agent_runtime.onlinelinelookup as onlinelinelookup
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.onlinelinelookup import (
    COMBINED_ONLINE_LOOKUP_PROVIDER,
    GEMINI_GROUNDING_PROVIDER,
    ONLINE_LOOKUP_CONTEXT_KEY,
    ONLINE_LOOKUP_CONTEXTS_KEY,
    OnlineLookupResult,
    lookup_online_answer,
    online_lookup_prompt_lines,
    parse_duck_ai_lookup_html,
    parse_gemini_grounding_response,
    sanitize_online_lookup_query,
)


def test_online_lookup_extracts_duck_ai_answer() -> None:
    html = """
    <html><body>
      <div>how to use nvidia-smi</div>
      <div>NVIDIA SMI lists GPU process and utilization information.</div>
    </body></html>
    """

    result = parse_duck_ai_lookup_html(html, query="how to use nvidia-smi")

    assert result.available is True
    assert "NVIDIA SMI lists" in result.answer_text
    assert result.source_url.startswith("https://duck.ai")


def test_online_lookup_returns_unavailable_on_duck_ai_challenge() -> None:
    html = """
    <html><body>Unfortunately, bots use DuckDuckGo too.</body></html>
    """

    result = parse_duck_ai_lookup_html(html, query="nvidia-smi query")

    assert result.available is False
    assert "human-verification" in result.error


def test_online_lookup_caps_prompt_context() -> None:
    result = OnlineLookupResult(
        provider="duck_ai",
        query="q",
        available=True,
        answer_text="x" * 5000,
        source_title="title",
        source_url="https://example.com",
        fetched_at="2026-05-15T00:00:00Z",
    )

    lines = online_lookup_prompt_lines({ONLINE_LOOKUP_CONTEXT_KEY: result.to_context()})
    joined = "\n".join(lines)

    assert "User-requested online check context" in joined
    assert len(joined) < 1800


def test_online_lookup_prompt_lines_include_online_mode_results() -> None:
    result = OnlineLookupResult(
        provider="duck_ai",
        query="Task 1: use nvidia-smi",
        available=True,
        answer_text="Use --query-gpu=memory.free with --format=csv,noheader,nounits.",
        source_title="NVIDIA docs",
        source_url="https://docs.nvidia.com/deploy/nvidia-smi/",
        fetched_at="2026-05-15T00:00:00Z",
    ).to_context()
    result["source"] = "online_mode"
    result["task_id"] = "task-1"

    lines = online_lookup_prompt_lines({ONLINE_LOOKUP_CONTEXTS_KEY: [result]})
    joined = "\n".join(lines)

    assert "Online mode lookup context" in joined
    assert "task-1" in joined
    assert "source_url" in joined


def test_gemini_grounding_response_extracts_answer_and_source() -> None:
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                "Use nvidia-smi --query-gpu=memory.free "
                                "--format=csv,noheader,nounits."
                            )
                        }
                    ]
                },
                "groundingMetadata": {
                    "groundingChunks": [
                        {
                            "web": {
                                "title": "NVIDIA SMI docs",
                                "uri": "https://docs.nvidia.com/deploy/nvidia-smi/",
                            }
                        }
                    ]
                },
            }
        ]
    }

    result = parse_gemini_grounding_response(payload, query="free gpu ram")

    assert result.provider == GEMINI_GROUNDING_PROVIDER
    assert result.available is True
    assert "--query-gpu=memory.free" in result.answer_text
    assert result.source_url == "https://docs.nvidia.com/deploy/nvidia-smi/"


def test_online_lookup_combines_duck_ai_and_gemini_provider_results(monkeypatch) -> None:
    def fake_duck_ai(
        query: str,
        *,
        timeout_seconds: float = 30.0,
        **_: object,
    ) -> OnlineLookupResult:
        return OnlineLookupResult(
            provider="duck_ai",
            query=query,
            available=True,
            answer_text="Duck.ai says use --query-gpu=memory.free.",
            source_title="Duck.ai",
            source_url="https://duck.ai/?origin=funnel_home_website",
            fetched_at="2026-05-15T00:00:00Z",
        )

    def fake_gemini(query: str, *, timeout_seconds: float = 6.0, **_: object) -> OnlineLookupResult:
        return OnlineLookupResult(
            provider=GEMINI_GROUNDING_PROVIDER,
            query=query,
            available=True,
            answer_text="Gemini grounding says use csv,noheader,nounits.",
            source_title="Gemini source",
            source_url="https://example.com/gemini",
            fetched_at="2026-05-15T00:00:01Z",
        )

    monkeypatch.setattr(onlinelinelookup, "lookup_duck_ai_answer", fake_duck_ai)
    monkeypatch.setattr(onlinelinelookup, "lookup_gemini_grounding_answer", fake_gemini)

    result = lookup_online_answer("/checkonline free gpu ram")
    context = result.to_context()

    assert result.provider == COMBINED_ONLINE_LOOKUP_PROVIDER
    assert result.available is True
    assert "Duck.ai says" in result.answer_text
    assert "Gemini grounding says" in result.answer_text
    assert [item["provider"] for item in context["provider_results"]] == [
        "duck_ai",
        GEMINI_GROUNDING_PROVIDER,
    ]


def test_online_lookup_query_strips_typein_macro_values() -> None:
    query = sanitize_online_lookup_query('/checkonline use sudo typein "secret" for password')

    assert "secret" not in query
    assert "typein" not in query.lower()
    assert "/checkonline" not in query.lower()


def test_streaming_online_lookup_is_scoped_to_marked_step() -> None:
    state = {
        "online_lookup_requested": True,
        "online_lookup_task_ids": [],
        "current_index": 0,
        "tasks": [
            {"task_id": "task-1", "description": "List local GPU devices"},
            {"task_id": "task-2", "description": "Check online for nvidia-smi docs"},
        ],
    }

    assert AgentRuntime._streaming_task_should_lookup_online(
        state,
        state["tasks"][0],
    ) is False
    state["current_index"] = 1
    assert AgentRuntime._streaming_task_should_lookup_online(
        state,
        state["tasks"][1],
    ) is True


def test_streaming_online_mode_lookup_runs_for_each_unconsumed_step() -> None:
    state = {
        "online_lookup_requested": True,
        "online_lookup_mode_enabled": True,
        "online_lookup_task_ids": ["task-1"],
        "current_index": 0,
        "tasks": [
            {"task_id": "task-1", "description": "List local GPU devices"},
            {"task_id": "task-2", "description": "Calculate free GPU RAM"},
        ],
    }

    assert AgentRuntime._streaming_task_should_lookup_online(
        state,
        state["tasks"][0],
    ) is False
    state["current_index"] = 1
    assert AgentRuntime._streaming_task_should_lookup_online(
        state,
        state["tasks"][1],
    ) is True
