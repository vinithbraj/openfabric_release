from __future__ import annotations

from agent_runtime.core.user_errors import user_error_detail, user_error_message
from agent_runtime.llm.client import LLMClientError
from agent_runtime.llm.structured_call import StructuredCallDiagnostics, StructuredCallError


def test_user_error_detail_classifies_llm_transport_error() -> None:
    detail = user_error_detail(
        LLMClientError(
            error_kind="transport_error",
            error_message="LLM request failed: connection refused",
        ),
        stage="prompt_classification",
        context={"llm_base_url": "http://127.0.0.1:8000/v1", "llm_model": "local"},
    )

    assert detail["category"] == "llm_transport_error"
    assert "LLM service unavailable" in detail["title"]
    assert "model server is running" in detail["fix_hint"]
    assert detail["metadata"]["llm_base_url"] == "http://127.0.0.1:8000/v1"


def test_user_error_detail_classifies_llm_schema_error() -> None:
    detail = user_error_detail(
        StructuredCallError(
            StructuredCallDiagnostics(
                error_kind="schema_validation_error",
                error_message="Structured LLM response did not match the expected schema.",
                schema_name="OperatorPlan",
                validation_errors=["actions: Field required"],
            )
        ),
        stage="operator",
    )

    assert detail["category"] == "llm_schema_validation_error"
    assert "expected schema" in detail["title"]
    assert detail["metadata"]["schema_name"] == "OperatorPlan"
    assert "OperatorPlan" in detail["technical_detail"]


def test_user_error_detail_classifies_validation_permission_execution_and_unknown() -> None:
    validation = user_error_detail(
        "1 validation error for OperatorPlan: action ids must be unique.",
        stage="runtime",
    )
    permission = user_error_detail(PermissionError("permission denied: /secure"), stage="execution")
    execution = user_error_detail(
        "Command failed with exit code 127. stderr: tool not found",
        stage="execution",
    )
    unknown = user_error_detail(RuntimeError("boom"), stage="runtime")

    assert validation["category"] == "validation_error"
    assert permission["category"] == "permission_error"
    assert execution["category"] == "execution_error"
    assert unknown["category"] == "unexpected_error"
    assert "RuntimeError: boom" in unknown["technical_detail"]
    assert "RuntimeError: boom" in user_error_message(unknown)
