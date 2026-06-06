from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from agent_runtime.llm.client import LLMClientError
from agent_runtime.llm.structured_call import StructuredCallError, structured_call


class TinyProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    needs_clarification: bool


class QueueClient:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.schemas: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if not self.responses:
            raise AssertionError("unexpected call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return dict(response)


def test_structured_call_repairs_missing_required_field() -> None:
    client = QueueClient(
        {"answer": "ok"},
        {"answer": "ok", "needs_clarification": False},
    )

    parsed = structured_call(client, "classify", TinyProposal)

    assert parsed.needs_clarification is False
    assert len(client.prompts) == 2
    assert "Repair a malformed JSON object" in client.prompts[1]
    assert "Do not answer the user's task again" in client.prompts[1]


def test_structured_call_repairs_extra_forbidden_alias_field() -> None:
    client = QueueClient(
        {"answer": "ok", "needs_clarity": False},
        {"answer": "ok", "needs_clarification": False},
    )

    parsed = structured_call(client, "classify", TinyProposal)

    assert parsed.answer == "ok"
    assert "needs_clarity" in client.prompts[1]
    assert "needs_clarification" in client.prompts[1]


def test_structured_call_repairs_invalid_json_response() -> None:
    client = QueueClient(
        LLMClientError(
            error_kind="invalid_json",
            error_message="Expecting ',' delimiter: line 1 column 17 (char 16)",
            raw_response_preview='{"answer": "ok" "needs_clarification": false}',
            raw_payload_preview='{"choices": [{"message": {"content": "..."} }]}',
        ),
        {"answer": "ok", "needs_clarification": False},
    )

    parsed = structured_call(client, "classify", TinyProposal)

    assert parsed.answer == "ok"
    assert parsed.needs_clarification is False
    assert len(client.prompts) == 2
    assert "Repair malformed JSON text" in client.prompts[1]
    assert "Parser error:" in client.prompts[1]


def test_structured_call_repair_failure_keeps_schema_error_diagnostics() -> None:
    client = QueueClient(
        {"answer": "ok", "needs_clarity": False},
        {"answer": "ok", "needs_clarity": False},
    )

    with pytest.raises(StructuredCallError) as raised:
        structured_call(client, "classify", TinyProposal)

    diagnostics = raised.value.diagnostics
    assert diagnostics.error_kind == "schema_validation_error"
    assert diagnostics.repair_attempted is True
    assert diagnostics.repaired_payload_preview
    assert diagnostics.repair_validation_errors


def test_structured_call_invalid_json_repair_failure_keeps_diagnostics() -> None:
    client = QueueClient(
        LLMClientError(
            error_kind="invalid_json",
            error_message="Expecting ',' delimiter: line 1 column 17 (char 16)",
            raw_response_preview='{"answer": "ok" "needs_clarification": false}',
        ),
        {"answer": "ok"},
    )

    with pytest.raises(StructuredCallError) as raised:
        structured_call(client, "classify", TinyProposal)

    diagnostics = raised.value.diagnostics
    assert diagnostics.error_kind == "invalid_json"
    assert diagnostics.repair_attempted is True
    assert diagnostics.raw_response_preview
    assert diagnostics.repaired_payload_preview
    assert diagnostics.repair_validation_errors


def test_structured_call_transport_error_does_not_trigger_repair() -> None:
    client = QueueClient(
        LLMClientError(
            error_kind="transport_error",
            error_message="backend unavailable",
        )
    )

    with pytest.raises(StructuredCallError) as raised:
        structured_call(client, "classify", TinyProposal)

    assert len(client.prompts) == 1
    assert raised.value.diagnostics.error_kind == "transport_error"
    assert raised.value.diagnostics.repair_attempted is False
