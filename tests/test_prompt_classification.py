from __future__ import annotations

from typing import Any

from agent_runtime.capabilities import build_default_registry
from agent_runtime.core.types import UserRequest
from agent_runtime.input_pipeline.decomposition import PromptClassification, classify_prompt


class FakeLLMClient:
    """Small fake LLM client that returns prompt-specific classification payloads."""

    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.last_prompt = ""
        self.last_schema: dict[str, Any] | None = None

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.last_prompt = prompt
        self.last_schema = schema
        for raw_prompt, payload in self.payloads.items():
            if raw_prompt in prompt:
                return dict(payload)
        raise AssertionError(f"no fake payload configured for prompt: {prompt}")


class SequentialLLMClient:
    """Fake LLM client that returns one payload per structured call."""

    def __init__(self, *payloads: dict[str, Any]) -> None:
        self.payloads = list(payloads)
        self.prompts: list[str] = []
        self.schemas: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if not self.payloads:
            raise AssertionError("unexpected fake LLM call")
        return dict(self.payloads.pop(0))


def _client() -> FakeLLMClient:
    return FakeLLMClient(
        {
            "list all files in this folder": {
                "prompt_type": "simple_tool_task",
                "requires_tools": True,
                "likely_domains": ["filesystem"],
                "risk_level": "low",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "This is a direct read-only filesystem task.",
            },
            "delete all logs older than 30 days": {
                "prompt_type": "simple_tool_task",
                "requires_tools": True,
                "likely_domains": ["filesystem"],
                "risk_level": "high",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "This is a destructive filesystem cleanup request.",
            },
            "show me the top 10 patients by study count": {
                "prompt_type": "simple_tool_task",
                "requires_tools": True,
                "likely_domains": ["sql"],
                "risk_level": "low",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "This is a read-only ranked aggregation query.",
            },
            "read this CSV and summarize it": {
                "prompt_type": "compound_tool_task",
                "requires_tools": True,
                "likely_domains": ["filesystem", "python_data"],
                "risk_level": "low",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "The request requires reading data and then summarizing it.",
            },
            "fix my code and run the tests": {
                "prompt_type": "complex_workflow",
                "requires_tools": True,
                "likely_domains": ["filesystem", "shell", "python_data"],
                "risk_level": "high",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "This combines code modification with execution and verification.",
            },
        }
    )


def test_classify_list_files_prompt() -> None:
    client = _client()

    classification = classify_prompt(UserRequest(raw_prompt="list all files in this folder"), client)

    assert isinstance(classification, PromptClassification)
    assert classification.prompt_type == "simple_tool_task"
    assert classification.likely_domains == ["filesystem"]
    assert "Return JSON only." in client.last_prompt
    assert "Do not produce commands, shell syntax, SQL, code, or executable plans." in client.last_prompt
    assert "list all files in this folder" in client.last_prompt
    assert client.last_schema is not None
    assert "prompt_type" in client.last_schema["properties"]


def test_classify_prompt_repairs_needs_clarity_alias() -> None:
    client = SequentialLLMClient(
        {
            "prompt_type": "simple_tool_task",
            "requires_tools": True,
            "likely_domains": [],
            "risk_level": "low",
            "needs_clarity": False,
            "clarification_question": None,
            "reason": "The user wants current Docker runtime state.",
        },
        {
            "prompt_type": "simple_tool_task",
            "requires_tools": True,
            "likely_domains": [],
            "risk_level": "low",
            "needs_clarification": False,
            "clarification_question": None,
            "reason": "The user wants current Docker runtime state.",
        },
    )

    classification = classify_prompt(
        UserRequest(raw_prompt="list all running docker containers"),
        client,
    )

    assert classification.prompt_type == "simple_tool_task"
    assert classification.requires_tools is True
    assert classification.needs_clarification is False
    assert len(client.prompts) == 2
    assert "Repair a malformed JSON object" in client.prompts[1]
    assert "needs_clarity" in client.prompts[1]
    assert "prompt_type" in client.schemas[0]["properties"]


def test_classify_delete_logs_prompt() -> None:
    classification = classify_prompt(UserRequest(raw_prompt="delete all logs older than 30 days"), _client())

    assert classification.prompt_type == "simple_tool_task"
    assert classification.risk_level == "high"
    assert classification.requires_tools is True


def test_classify_top_patients_prompt() -> None:
    classification = classify_prompt(UserRequest(raw_prompt="show me the top 10 patients by study count"), _client())

    assert classification.prompt_type == "simple_tool_task"
    assert classification.likely_domains == ["sql"]
    assert classification.reason.startswith("This is a read-only")


def test_classify_csv_summary_prompt() -> None:
    classification = classify_prompt(UserRequest(raw_prompt="read this CSV and summarize it"), _client())

    assert classification.prompt_type == "compound_tool_task"
    assert classification.likely_domains == ["filesystem", "python_data"]


def test_classify_fix_code_prompt() -> None:
    classification = classify_prompt(UserRequest(raw_prompt="fix my code and run the tests"), _client())

    assert classification.prompt_type == "complex_workflow"
    assert classification.likely_domains == ["filesystem", "shell", "python_data"]
    assert classification.risk_level == "high"


def test_classification_requires_tools_for_runtime_observation_prompt() -> None:
    client = FakeLLMClient(
        {
            "what is the current git branch?": {
                "prompt_type": "simple_question",
                "requires_tools": False,
                "likely_domains": [],
                "risk_level": "low",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "This looks like a direct question.",
            }
        }
    )

    classification = classify_prompt(
        UserRequest(raw_prompt="what is the current git branch?"),
        client,
        build_default_registry(),
    )

    assert classification.prompt_type == "simple_tool_task"
    assert classification.requires_tools is True
    assert classification.likely_domains == ["operator"]


def test_classification_uses_declared_domain_evaluations() -> None:
    client = FakeLLMClient(
        {
            "what is the current working directory?": {
                "prompt_type": "simple_tool_task",
                "requires_tools": True,
                "likely_domains": [],
                "risk_level": "low",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "The prompt needs a declared shell/runtime capability.",
                "domain_evaluations": [
                    {
                        "domain": "shell",
                        "fits": True,
                        "confidence": 0.95,
                        "reason": "The shell domain exposes current working directory inspection.",
                    },
                    {
                        "domain": "filesystem",
                        "fits": False,
                        "confidence": 0.8,
                        "reason": "This is not a file content request.",
                    },
                ],
            }
        }
    )

    classification = classify_prompt(
        UserRequest(raw_prompt="what is the current working directory?"),
        client,
        build_default_registry(),
    )

    assert classification.prompt_type == "simple_tool_task"
    assert classification.requires_tools is True
    assert classification.likely_domains == []
    assert "Declared runtime domains and capabilities:" in client.last_prompt


def test_classification_manifest_includes_capability_examples() -> None:
    client = FakeLLMClient(
        {
            "what are your capabilities": {
                "prompt_type": "simple_tool_task",
                "requires_tools": True,
                "likely_domains": [],
                "risk_level": "low",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "The prompt asks about declared runtime capabilities.",
                "domain_evaluations": [
                    {
                        "domain": "runtime",
                        "fits": True,
                        "confidence": 0.95,
                        "reason": "The runtime domain declares capability registry introspection.",
                    }
                ],
            }
        }
    )

    classification = classify_prompt(
        UserRequest(raw_prompt="what are your capabilities"),
        client,
        build_default_registry(),
    )

    assert classification.prompt_type == "simple_tool_task"
    assert classification.requires_tools is True
    assert classification.likely_domains == ["runtime"]
    assert "what are my capabilities?" in client.last_prompt
    assert "References such as you, your, this agent, or this runtime" in client.last_prompt


def test_domain_evaluation_implies_tool_requirement() -> None:
    client = FakeLLMClient(
        {
            "what are your capabilities": {
                "prompt_type": "simple_question",
                "requires_tools": False,
                "likely_domains": [],
                "risk_level": "low",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "The prompt has a matching declared runtime domain.",
                "domain_evaluations": [
                    {
                        "domain": "runtime",
                        "fits": True,
                        "confidence": 0.92,
                        "reason": "Runtime capabilities are exposed by the runtime domain.",
                    }
                ],
            }
        }
    )

    classification = classify_prompt(
        UserRequest(raw_prompt="what are your capabilities"),
        client,
        build_default_registry(),
    )

    assert classification.prompt_type == "simple_tool_task"
    assert classification.requires_tools is True
    assert classification.likely_domains == ["runtime"]
