"""Helpers for validating structured LLM output."""

from __future__ import annotations

import json
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError

from agent_runtime.llm.client import LLMClient, LLMClientError
from agent_runtime.prompts import render_prompt

ModelT = TypeVar("ModelT", bound=BaseModel)


StructuredCallErrorKind = Literal[
    "transport_error",
    "invalid_json",
    "schema_validation_error",
    "empty_response",
    "parsing_error",
]


class StructuredCallDiagnostics(BaseModel):
    """Typed diagnostics for a failed structured LLM call."""

    error_kind: StructuredCallErrorKind
    error_message: str
    raw_response_preview: str | None = None
    raw_payload_preview: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    schema_name: str
    repair_attempted: bool = False
    repair_error_message: str | None = None
    repaired_payload_preview: str | None = None
    repair_validation_errors: list[str] = Field(default_factory=list)


class StructuredCallError(RuntimeError):
    """Raised when a structured LLM call fails before producing a valid model."""

    def __init__(self, diagnostics: StructuredCallDiagnostics) -> None:
        super().__init__(diagnostics.error_message)
        self.diagnostics = diagnostics


def _truncate_preview(value: Any, max_length: int = 1000) -> str | None:
    """Return a safe truncated preview for diagnostics."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return text[:max_length] + "...[truncated]"


def _stable_preview(value: Any) -> str | None:
    """Serialize one structure stably for diagnostics previews."""

    try:
        serialized = json.dumps(value, sort_keys=True, default=str, ensure_ascii=True)
    except Exception:
        serialized = repr(value)
    return _truncate_preview(serialized)


def _stable_json(value: Any, max_length: int = 16000) -> str:
    """Serialize one structure for a repair prompt with a defensive length cap."""

    try:
        serialized = json.dumps(value, sort_keys=True, default=str, ensure_ascii=True, indent=2)
    except Exception:
        serialized = repr(value)
    if len(serialized) <= max_length:
        return serialized
    return serialized[:max_length] + "\n...[truncated]"


def _schema_name(schema: dict[str, Any], output_model: type[BaseModel]) -> str:
    """Return a human-readable schema name for prompts and diagnostics."""

    return str(schema.get("title") or schema.get("$id") or output_model.__name__)


def _schema_validation_diagnostics(
    *,
    schema_name: str,
    payload: Any,
    validation_error: PydanticValidationError,
    repair_attempted: bool = False,
    repair_error_message: str | None = None,
    repaired_payload: Any = None,
    repair_validation_error: PydanticValidationError | None = None,
) -> StructuredCallDiagnostics:
    """Build one consistent diagnostics object for schema validation failures."""

    repair_errors = [str(repair_validation_error)] if repair_validation_error is not None else []
    message = "Structured LLM response did not match the expected schema."
    if repair_validation_error is not None:
        message = "Structured LLM response did not match the expected schema after repair."
    elif repair_error_message:
        message = "Structured LLM response failed schema validation and repair did not complete."
    return StructuredCallDiagnostics(
        error_kind="schema_validation_error",
        error_message=message,
        raw_response_preview=None,
        raw_payload_preview=_stable_preview(payload),
        validation_errors=[str(validation_error)],
        schema_name=schema_name,
        repair_attempted=repair_attempted,
        repair_error_message=repair_error_message,
        repaired_payload_preview=_stable_preview(repaired_payload),
        repair_validation_errors=repair_errors,
    )


def _schema_repair_prompt(
    *,
    schema_name: str,
    schema: dict[str, Any],
    invalid_payload: Any,
    validation_error: PydanticValidationError,
) -> str:
    """Build the internal prompt for one typed schema repair attempt."""

    return render_prompt(
        "llm.schema_repair",
        {
            "schema_name": schema_name,
            "schema_json": _stable_json(schema),
            "invalid_payload_json": _stable_json(invalid_payload),
            "validation_error": str(validation_error),
        },
    )


def _json_syntax_repair_prompt(
    *,
    schema_name: str,
    schema: dict[str, Any],
    raw_response_preview: str,
    parser_error: str,
) -> str:
    """Build the internal prompt for repairing malformed JSON text."""

    return render_prompt(
        "llm.json_syntax_repair",
        {
            "schema_name": schema_name,
            "schema_json": _stable_json(schema),
            "raw_response_preview": raw_response_preview,
            "parser_error": parser_error,
        },
    )


def _invalid_json_diagnostics(
    *,
    schema_name: str,
    error_message: str,
    raw_response_preview: str | None,
    raw_payload_preview: str | None,
    repair_attempted: bool = False,
    repair_error_message: str | None = None,
    repaired_payload: Any = None,
    repair_validation_error: PydanticValidationError | None = None,
) -> StructuredCallDiagnostics:
    """Build diagnostics for invalid JSON and optional syntax-repair failures."""

    repair_errors = [str(repair_validation_error)] if repair_validation_error is not None else []
    message = error_message
    if repair_validation_error is not None:
        message = "Structured LLM response was invalid JSON and the repaired payload failed schema validation."
    elif repair_error_message:
        message = "Structured LLM response was invalid JSON and repair did not complete."
    return StructuredCallDiagnostics(
        error_kind="invalid_json",
        error_message=message,
        raw_response_preview=raw_response_preview,
        raw_payload_preview=raw_payload_preview,
        validation_errors=[],
        schema_name=schema_name,
        repair_attempted=repair_attempted,
        repair_error_message=repair_error_message,
        repaired_payload_preview=_stable_preview(repaired_payload),
        repair_validation_errors=repair_errors,
    )


def structured_call(
    client: LLMClient,
    prompt: str,
    output_model: type[ModelT],
    *,
    schema: dict[str, Any] | None = None,
    repair_attempts: int = 1,
) -> ModelT:
    """Call an LLM client and validate the JSON result as a Pydantic model."""

    call_schema = schema or output_model.model_json_schema()
    schema_name = _schema_name(call_schema, output_model)
    try:
        payload: dict[str, Any] = client.complete_json(prompt, call_schema)
    except LLMClientError as exc:
        if exc.error_kind == "invalid_json" and repair_attempts > 0 and exc.raw_response_preview:
            repair_prompt = _json_syntax_repair_prompt(
                schema_name=schema_name,
                schema=call_schema,
                raw_response_preview=exc.raw_response_preview,
                parser_error=exc.error_message,
            )
            try:
                repaired_payload: dict[str, Any] = client.complete_json(repair_prompt, call_schema)
            except LLMClientError as repair_exc:
                raise StructuredCallError(
                    _invalid_json_diagnostics(
                        schema_name=schema_name,
                        error_message=exc.error_message,
                        raw_response_preview=exc.raw_response_preview,
                        raw_payload_preview=exc.raw_payload_preview or _truncate_preview(prompt),
                        repair_attempted=True,
                        repair_error_message=repair_exc.error_message,
                    )
                ) from exc
            except Exception as repair_exc:
                raise StructuredCallError(
                    _invalid_json_diagnostics(
                        schema_name=schema_name,
                        error_message=exc.error_message,
                        raw_response_preview=exc.raw_response_preview,
                        raw_payload_preview=exc.raw_payload_preview or _truncate_preview(prompt),
                        repair_attempted=True,
                        repair_error_message=str(repair_exc),
                    )
                ) from exc
            try:
                return output_model.model_validate(repaired_payload)
            except PydanticValidationError as repair_validation_exc:
                raise StructuredCallError(
                    _invalid_json_diagnostics(
                        schema_name=schema_name,
                        error_message=exc.error_message,
                        raw_response_preview=exc.raw_response_preview,
                        raw_payload_preview=exc.raw_payload_preview or _truncate_preview(prompt),
                        repair_attempted=True,
                        repaired_payload=repaired_payload,
                        repair_validation_error=repair_validation_exc,
                    )
                ) from exc
        raise StructuredCallError(
            StructuredCallDiagnostics(
                error_kind=exc.error_kind,  # type: ignore[arg-type]
                error_message=exc.error_message,
                raw_response_preview=exc.raw_response_preview,
                raw_payload_preview=exc.raw_payload_preview or _truncate_preview(prompt),
                validation_errors=[],
                schema_name=schema_name,
            )
        ) from exc
    except Exception as exc:
        raise StructuredCallError(
            StructuredCallDiagnostics(
                error_kind="parsing_error",
                error_message=str(exc),
                raw_response_preview=None,
                raw_payload_preview=_truncate_preview(prompt),
                validation_errors=[],
                schema_name=schema_name,
            )
        ) from exc
    try:
        return output_model.model_validate(payload)
    except PydanticValidationError as exc:
        if repair_attempts <= 0:
            raise StructuredCallError(
                _schema_validation_diagnostics(
                    schema_name=schema_name,
                    payload=payload,
                    validation_error=exc,
                )
            ) from exc
        repair_prompt = _schema_repair_prompt(
            schema_name=schema_name,
            schema=call_schema,
            invalid_payload=payload,
            validation_error=exc,
        )
        try:
            repaired_payload: dict[str, Any] = client.complete_json(repair_prompt, call_schema)
        except LLMClientError as repair_exc:
            raise StructuredCallError(
                _schema_validation_diagnostics(
                    schema_name=schema_name,
                    payload=payload,
                    validation_error=exc,
                    repair_attempted=True,
                    repair_error_message=repair_exc.error_message,
                )
            ) from exc
        except Exception as repair_exc:
            raise StructuredCallError(
                _schema_validation_diagnostics(
                    schema_name=schema_name,
                    payload=payload,
                    validation_error=exc,
                    repair_attempted=True,
                    repair_error_message=str(repair_exc),
                )
            ) from exc
        try:
            return output_model.model_validate(repaired_payload)
        except PydanticValidationError as repair_validation_exc:
            raise StructuredCallError(
                _schema_validation_diagnostics(
                    schema_name=schema_name,
                    payload=payload,
                    validation_error=exc,
                    repair_attempted=True,
                    repaired_payload=repaired_payload,
                    repair_validation_error=repair_validation_exc,
                )
            ) from exc
