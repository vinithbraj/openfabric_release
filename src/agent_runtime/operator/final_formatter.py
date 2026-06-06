"""Shared LLM-authored final formatter for operator execution results."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_runtime.core.types import UserRequest
from agent_runtime.memory import memory_prompt_lines_from_context
from agent_runtime.parameters import parameter_prompt_lines_from_context
from agent_runtime.onlinelinelookup import online_lookup_prompt_lines
from agent_runtime.onlineaicheck import online_ai_check_prompt_lines
from agent_runtime.operator.models import (
    OperatorAction,
    OperatorAnswerJudge,
    OperatorCardinalityJudge,
    OperatorFinalFormatter,
    OperatorPlan,
    OperatorTask,
)
from agent_runtime.prompts import prompt_lines
from agent_runtime.reliability import AnswerCoverageReview, AnswerObligationSet


FORMATTER_PROPOSED_EVENT = "operator.final_formatter.proposed"
FORMATTER_ACCEPTED_EVENT = "operator.final_formatter.accepted"
FORMATTER_REJECTED_EVENT = "operator.final_formatter.rejected"
FORMATTER_COMPLETED_EVENT = "operator.final_formatter.completed"
ANSWER_JUDGE_PROPOSED_EVENT = "operator.answer_judge.proposed"
ANSWER_JUDGE_REJECTED_EVENT = "operator.answer_judge.rejected"
ANSWER_JUDGE_ACCEPTED_EVENT = "operator.answer_judge.accepted"
CARDINALITY_JUDGE_PROPOSED_EVENT = "operator.cardinality_judge.proposed"
CARDINALITY_JUDGE_REJECTED_EVENT = "operator.cardinality_judge.rejected"
CARDINALITY_JUDGE_ACCEPTED_EVENT = "operator.cardinality_judge.accepted"


@dataclass(frozen=True)
class FormatterSource:
    """One completed action/result available to the final formatter."""

    source_id: str
    label: str
    kind: str
    status: str
    command: str = ""
    declared_output_shape: str = "text"
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    output: Any = None


@dataclass(frozen=True)
class FormatterResult:
    """Result of running an LLM-authored final formatter."""

    content: str
    formatter: OperatorFinalFormatter
    coverage_review: AnswerCoverageReview | None = None
    coverage_accepted: bool = True


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, indent=2, default=str)
    except Exception:
        return str(value)


def _truncate(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars)] + "\n...[truncated]"


def _request_context_lines(user_request: UserRequest) -> list[str]:
    """Return clarification and policy context that applies during rendering."""

    context = dict(user_request.session_context or {})
    lines: list[str] = []
    clarifications = context.get("clarifications")
    if isinstance(clarifications, list) and clarifications:
        safe_clarifications: list[dict[str, Any]] = []
        for entry in clarifications[-5:]:
            if not isinstance(entry, dict):
                continue
            safe_clarifications.append(
                {
                    "question": _truncate(entry.get("question"), 500),
                    "answer": _truncate(entry.get("answer"), 500),
                    "reason": _truncate(entry.get("reason"), 500),
                    "missing_information": _truncate(entry.get("missing_information"), 500),
                }
            )
        if safe_clarifications:
            lines.extend(
                [
                    "User clarification answers already provided:",
                    _stable_json(safe_clarifications),
                    "Treat these answers as explicit user constraints in the final answer.",
                ]
            )
    policy_notes = context.get("operator_policy_notes")
    if isinstance(policy_notes, list) and policy_notes:
        safe_notes: list[dict[str, Any]] = []
        for entry in policy_notes[-5:]:
            if not isinstance(entry, dict):
                continue
            safe_notes.append(
                {
                    "phase": _truncate(entry.get("phase"), 120),
                    "reason": _truncate(entry.get("reason"), 500),
                    "instruction": _truncate(entry.get("instruction"), 1000),
                    "question": _truncate(entry.get("question"), 500),
                    "missing_information": _truncate(entry.get("missing_information"), 500),
                }
            )
        if safe_notes:
            lines.extend(
                [
                    "Runtime-enforced operator policy notes:",
                    _stable_json(safe_notes),
                    "These notes are authoritative context for rendering.",
                ]
            )
    lines.extend(online_lookup_prompt_lines(context))
    lines.extend(online_ai_check_prompt_lines(context))
    return lines


def _line_count(value: Any) -> int:
    text = str(value or "")
    if not text:
        return 0
    return len(text.splitlines())


def _primary_field(source: FormatterSource) -> str | None:
    """Return the preferred user-facing value field for one operator record."""

    if source.kind in {"shell_command", "python_action"}:
        if source.stdout:
            return "stdout"
        if source.output is not None:
            return "output"
        if source.stderr:
            return "stderr"
        return None
    if source.output is not None:
        return "output"
    if source.stdout:
        return "stdout"
    if source.stderr:
        return "stderr"
    return None


def _primary_value(source: FormatterSource) -> Any:
    """Return the preferred user-facing value for one operator record."""

    field = _primary_field(source)
    if field == "stdout":
        return source.stdout
    if field == "stderr":
        return source.stderr
    if field == "output":
        return source.output
    return None


def _authoritative_result_source(sources: list[FormatterSource]) -> FormatterSource | None:
    """Return the last successful Python-derived result, if one exists."""

    for source in reversed(sources):
        if source.status != "success" or source.kind not in {"python_action", "python_transform"}:
            continue
        value = _primary_value(source)
        if value is not None and str(value).strip():
            return source
    return None


def _source_metadata(source: FormatterSource) -> dict[str, Any]:
    """Return safe metadata for the formatter prompt without raw stdout/stderr."""

    output = source.output
    output_type = type(output).__name__ if output is not None else None
    output_keys = sorted(output.keys()) if isinstance(output, dict) else None
    output_count = len(output) if isinstance(output, (list, tuple, dict, set)) else None
    return {
        "source_id": source.source_id,
        "label": source.label,
        "kind": source.kind,
        "status": source.status,
        "command": source.command,
        "declared_output_shape": source.declared_output_shape,
        "exit_code": source.exit_code,
        "stdout_line_count": _line_count(source.stdout),
        "stderr_line_count": _line_count(source.stderr),
        "stdout_char_count": len(str(source.stdout or "")),
        "stderr_char_count": len(str(source.stderr or "")),
        "output_type": output_type,
        "output_keys": output_keys,
        "output_count": output_count,
        "primary_field": _primary_field(source),
    }


def _authoritative_source_metadata(source: FormatterSource) -> dict[str, Any]:
    """Return metadata plus a bounded computed-result preview."""

    return {
        **_source_metadata(source),
        "primary_value_preview": _truncate(_primary_value(source), 500),
    }


def _source_preview(source: FormatterSource, *, max_chars: int) -> dict[str, Any]:
    """Return bounded source data for answer-quality LLM stages."""

    output_preview = _stable_json(source.output) if source.output is not None else ""
    return {
        **_source_metadata(source),
        "stdout_preview": _truncate(source.stdout, max_chars),
        "stderr_preview": _truncate(source.stderr, max_chars),
        "output_preview": _truncate(output_preview, max_chars),
        "primary_value_preview": _truncate(_primary_value(source), max_chars),
    }


def _formatter_inputs(
    user_request: UserRequest,
    sources: list[FormatterSource],
    obligation_set: AnswerObligationSet | None = None,
) -> dict[str, Any]:
    records = [
        {
            "source_id": source.source_id,
            "label": source.label,
            "kind": source.kind,
            "status": source.status,
            "command": source.command,
            "declared_output_shape": source.declared_output_shape,
            "stdout": source.stdout,
            "stderr": source.stderr,
            "exit_code": source.exit_code,
            "output": source.output,
            "primary_field": _primary_field(source),
            "primary_value": _primary_value(source),
        }
        for source in sources
    ]
    inputs: dict[str, Any] = {
        "user_goal": user_request.raw_prompt,
        "records": records,
        "authoritative_result": None,
    }
    if obligation_set is not None:
        inputs["evidence_obligations"] = obligation_set.model_dump(mode="json")
    authoritative_source = _authoritative_result_source(sources)
    if authoritative_source is not None:
        inputs["authoritative_result"] = {
            "source_id": authoritative_source.source_id,
            "label": authoritative_source.label,
            "kind": authoritative_source.kind,
            "primary_field": _primary_field(authoritative_source),
            "primary_value": _primary_value(authoritative_source),
        }
    if records:
        last = records[-1]
        inputs.update(
            {
                "stdout": last.get("stdout") or "",
                "stderr": last.get("stderr") or "",
                "exit_code": last.get("exit_code"),
                "output": last.get("output"),
            }
        )
    return inputs


def build_final_formatter_prompt(
    user_request: UserRequest,
    sources: list[FormatterSource],
    *,
    feedback: list[dict[str, Any]] | None = None,
    include_source_previews: bool = False,
    source_preview_chars: int = 3000,
    obligation_set: AnswerObligationSet | None = None,
) -> str:
    """Build the LLM prompt for a pure final-output formatter."""

    authoritative_source = _authoritative_result_source(sources)
    source_visibility_line = (
        "Bounded stdout/stderr/output previews are included below so you can write formatter code against the real source shape."
        if include_source_previews
        else "Raw stdout/stderr are not included in this prompt; your code receives them at runtime in inputs."
    )
    lines = [
        *prompt_lines(
            "operator.final_formatter",
            {"source_visibility_line": source_visibility_line},
        ),
        "Schema for OperatorFinalFormatter:",
        _stable_json(OperatorFinalFormatter.model_json_schema()),
        "Original user request:",
        user_request.raw_prompt,
        *memory_prompt_lines_from_context(user_request, stage="final_answer"),
        *parameter_prompt_lines_from_context(user_request, stage="final_answer"),
        *_request_context_lines(user_request),
    ]
    self_brief = dict(user_request.session_context or {}).get("operator_self_brief")
    if isinstance(self_brief, dict):
        lines.extend(
            [
                "Operator self-brief:",
                _stable_json(self_brief),
                "Use this brief only to preserve the intended answer/postcondition; do not treat it as execution output.",
            ]
        )
    lines.extend(
        [
            "Safe action metadata available for formatting:",
            _stable_json([_source_metadata(source) for source in sources]),
        ]
    )
    if include_source_previews:
        lines.extend(
            [
                "Bounded source previews:",
                _stable_json(
                    [
                        _source_preview(source, max_chars=source_preview_chars)
                        for source in sources
                    ]
                ),
            ]
        )
    if authoritative_source is not None:
        lines.extend(
            [
                "Authoritative computed result source:",
                _stable_json(_authoritative_source_metadata(authoritative_source)),
                "Formatter code should read inputs['authoritative_result']['primary_value'] for the final computed value.",
            ]
        )
    if obligation_set is not None:
        lines.extend(
            [
                "LLM-audited evidence obligations:",
                _stable_json(obligation_set.model_dump(mode="json")),
                (
                    "Formatter code receives inputs['evidence_obligations']. Preserve all "
                    "must_report public facts in the final answer. Faithful paraphrase is allowed; "
                    "do not repeat sensitive or redacted values verbatim."
                ),
            ]
        )
    if feedback:
        lines.extend(
            [
                "Previous formatter validation/execution feedback:",
                _stable_json(feedback),
                "Repair only the formatter code and return OperatorFinalFormatter JSON.",
            ]
        )
    return "\n".join(lines)


def build_answer_judge_prompt(
    user_request: UserRequest,
    sources: list[FormatterSource],
    content: str,
    *,
    formatter: OperatorFinalFormatter,
    source_preview_chars: int = 3000,
) -> str:
    """Build the LLM prompt that judges whether the final answer is complete."""

    return "\n".join(
        [
            *prompt_lines("operator.answer_judge"),
            *memory_prompt_lines_from_context(user_request, stage="answer_judge"),
            *parameter_prompt_lines_from_context(user_request, stage="answer_judge"),
            "Schema for OperatorAnswerJudge:",
            _stable_json(OperatorAnswerJudge.model_json_schema()),
            "Original user request:",
            user_request.raw_prompt,
            *_request_context_lines(user_request),
            "Source previews:",
            _stable_json(
                [_source_preview(source, max_chars=source_preview_chars) for source in sources]
            ),
            "Formatter proposal metadata:",
            _stable_json(
                {
                    "declared_output_shape": formatter.declared_output_shape,
                    "reason": formatter.reason,
                    "confidence": formatter.confidence,
                }
            ),
            "Final answer under review:",
            content,
        ]
    )


def _cardinality_source_preview(source: FormatterSource, *, max_chars: int) -> dict[str, Any]:
    """Return compact source evidence for LLM-only cardinality review."""

    output_preview = _stable_json(source.output) if source.output is not None else ""
    return {
        "source_id": source.source_id,
        "label": source.label,
        "kind": source.kind,
        "status": source.status,
        "stdout_preview": _truncate(source.stdout, max_chars),
        "output_preview": _truncate(output_preview, max_chars),
        "primary_value_preview": _truncate(_primary_value(source), max_chars),
    }


def build_cardinality_judge_prompt(
    user_request: UserRequest,
    sources: list[FormatterSource],
    content: str,
    *,
    source_preview_chars: int = 1200,
) -> str:
    """Build the compact LLM prompt for cardinality-only answer review."""

    preview_chars = max(300, min(int(source_preview_chars or 1200), 1500))
    return "\n".join(
        [
            *prompt_lines("operator.cardinality_judge"),
            "Schema for OperatorCardinalityJudge:",
            _stable_json(OperatorCardinalityJudge.model_json_schema()),
            "Original user request:",
            user_request.raw_prompt,
            "Action labels and bounded source previews:",
            _stable_json(
                [
                    _cardinality_source_preview(source, max_chars=preview_chars)
                    for source in sources
                    if source.status == "success"
                ]
            ),
            "Final answer under review:",
            content,
        ]
    )


def _format_content(value: Any) -> str:
    """Normalize formatter output into markdown/text content."""

    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
            except Exception:
                return stripped
            if isinstance(parsed, (dict, list)):
                return f"```json\n{_stable_json(parsed).strip()}\n```"
        return stripped
    if isinstance(value, dict):
        for key in ("markdown", "text", "content", "answer", "code"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return _format_content(candidate)
        return f"```json\n{_stable_json(value).strip()}\n```"
    if isinstance(value, (list, tuple)):
        return f"```json\n{_stable_json(value).strip()}\n```"
    return f"```text\n{_stable_json(value).strip()}\n```"


def _numeric_values(value: Any) -> list[float]:
    text = str(value or "").replace(",", "")
    numbers: list[float] = []
    for token in re.findall(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?", text):
        try:
            numbers.append(float(token))
        except ValueError:
            continue
    return numbers


def _content_preserves_authoritative_result(content: str, source: FormatterSource | None) -> bool:
    """Return whether formatter output preserved the computed Python result."""

    if source is None:
        return True
    authoritative_value = _primary_value(source)
    authoritative_numbers = _numeric_values(authoritative_value)
    if not authoritative_numbers:
        return True
    content_numbers = _numeric_values(content)
    for authoritative_number in authoritative_numbers:
        tolerance = max(1e-9, abs(authoritative_number) * 1e-6)
        if not any(abs(candidate - authoritative_number) <= tolerance for candidate in content_numbers):
            return False
    return True


_LIST_REQUEST_WORDS = {
    "all",
    "display",
    "enumerate",
    "files",
    "images",
    "list",
    "rows",
    "show",
    "table",
}

_FORMATTER_TOKEN_STOPWORDS = {
    "and",
    "are",
    "for",
    "from",
    "the",
    "this",
    "that",
    "total",
    "with",
}


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 2 and token not in _FORMATTER_TOKEN_STOPWORDS
    }


def _request_needs_source_rows(user_goal: str) -> bool:
    """Return whether the wording asks for source rows, not only a computed scalar."""

    tokens = _tokens(user_goal)
    return bool(tokens & _LIST_REQUEST_WORDS)


def _content_preserves_requested_source_rows(
    user_request: UserRequest,
    content: str,
    sources: list[FormatterSource],
    authoritative_source: FormatterSource | None,
) -> bool:
    """Reject empty tables when the user asked to list source rows."""

    if not _request_needs_source_rows(user_request.raw_prompt):
        return True
    content_tokens = _tokens(content)
    if not content_tokens:
        return False
    for source in sources:
        if source.status != "success":
            continue
        value = _primary_value(source)
        lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
        if not lines:
            continue
        source_tokens: set[str] = set()
        for line in lines[:20]:
            source_tokens.update(_tokens(line))
        if source_tokens and content_tokens.isdisjoint(source_tokens):
            return False
    return True


def _formatter_action(formatter: OperatorFinalFormatter, inputs: dict[str, Any]) -> OperatorAction:
    return OperatorAction(
        action_id="final_formatter",
        task_id="final_formatter_task",
        kind="python_transform",
        code=formatter.code,
        cwd=".",
        inputs=inputs,
        input_bindings=[],
        declared_output_shape="text",
        risk="low",
        reason=formatter.reason or "Format final operator output for the user.",
        depends_on=[],
    )


def run_llm_final_formatter(
    *,
    user_request: UserRequest,
    sources: list[FormatterSource],
    llm_client: Any,
    validator: Any,
    python_executor: Any,
    observability: Any = None,
    stage: str = "rendering",
    include_source_previews: bool = False,
    answer_judge_enabled: bool = False,
    cardinality_judge_enabled: bool = False,
    source_preview_chars: int = 3000,
    max_repair_attempts: int = 1,
    obligation_set: AnswerObligationSet | None = None,
    coverage_review_callback: Callable[[str], AnswerCoverageReview] | None = None,
    coverage_retry_callback: Callable[[AnswerCoverageReview, int], None] | None = None,
) -> FormatterResult | None:
    """Ask the LLM for formatter code, validate it, and execute locally.

    This is intentionally optional: formatting failure must not hide a successful
    command execution. Raw outputs are passed only to the local Python
    transform, never to the LLM prompt.
    """

    successful_sources = [source for source in sources if source.status == "success"]
    if not successful_sources:
        return None
    if not any(
        source.stdout or source.stderr or source.output is not None
        for source in successful_sources
    ):
        return None

    inputs = _formatter_inputs(user_request, successful_sources, obligation_set=obligation_set)
    authoritative_source = _authoritative_result_source(successful_sources)
    feedback: list[dict[str, Any]] | None = None
    last_errors: list[dict[str, Any]] = []
    cardinality_revisions = 0
    total_attempts = max(1, int(max_repair_attempts) + 1)
    if cardinality_judge_enabled:
        total_attempts = max(total_attempts, 2)
    for attempt in range(total_attempts):
        try:
            prompt = build_final_formatter_prompt(
                user_request,
                successful_sources,
                feedback=feedback,
                include_source_previews=include_source_previews,
                source_preview_chars=source_preview_chars,
                obligation_set=obligation_set,
            )
            payload = llm_client.complete_json(prompt, OperatorFinalFormatter.model_json_schema())
            formatter = OperatorFinalFormatter.model_validate(payload)
        except Exception as exc:
            last_errors = [{"error": "formatter_schema_error", "message": str(exc)}]
            if observability is not None:
                observability.warning(
                    stage,
                    FORMATTER_REJECTED_EVENT,
                    "Final formatter rejected",
                    "The LLM did not produce a valid final formatter payload.",
                    details={"attempt": attempt + 1, "errors": last_errors},
                    debug_only=True,
                )
            feedback = last_errors
            continue

        if observability is not None:
            observability.info(
                stage,
                FORMATTER_PROPOSED_EVENT,
                "Final formatter proposed",
                "The LLM proposed pure Python formatter code for the final response.",
                details={
                    "attempt": attempt + 1,
                    "declared_output_shape": formatter.declared_output_shape,
                    "code_chars": len(formatter.code),
                    "reason": formatter.reason,
                    "confidence": formatter.confidence,
                },
                debug_only=True,
            )

        action = _formatter_action(formatter, inputs)
        plan = OperatorPlan(
            summary="Format final operator output.",
            tasks=[
                OperatorTask(
                    task_id="final_formatter_task",
                    goal="Format final operator output for the user.",
                    semantic_verb="render",
                    object_type="final_response",
                    dependencies=[],
                    reason="The final response should present successful command outputs cleanly.",
                )
            ],
            actions=[action],
            dependencies=[],
            expected_outputs=["User-facing final response"],
            assumptions=[],
            confidence=formatter.confidence,
        )
        errors = list(validator.validate(plan))
        if errors:
            last_errors = errors
            if observability is not None:
                observability.warning(
                    stage,
                    FORMATTER_REJECTED_EVENT,
                    "Final formatter rejected",
                    "The formatter failed deterministic validation.",
                    details={"attempt": attempt + 1, "errors": errors},
                    debug_only=True,
                )
            feedback = errors
            continue

        try:
            output = python_executor.execute(action, inputs)
        except Exception as exc:
            last_errors = [{"error": "formatter_execution_error", "message": str(exc)}]
            if observability is not None:
                observability.warning(
                    stage,
                    FORMATTER_REJECTED_EVENT,
                    "Final formatter failed",
                    "The formatter raised while shaping final output.",
                    details={"attempt": attempt + 1, "errors": last_errors},
                    debug_only=True,
                )
            feedback = last_errors
            continue

        content = _format_content(output)
        if not content:
            last_errors = [{"error": "formatter_empty_output", "message": "Formatter returned empty output."}]
            feedback = last_errors
            continue
        coverage_review: AnswerCoverageReview | None = None
        if coverage_review_callback is not None:
            try:
                coverage_review = coverage_review_callback(content)
            except Exception as exc:
                last_errors = [{"error": "answer_coverage_review_error", "message": str(exc)}]
                feedback = last_errors
                continue
            if coverage_review.verdict != "accept":
                last_errors = [
                    {
                        "error": "answer_coverage_review_requested_revision",
                        "verdict": coverage_review.verdict,
                        "message": coverage_review.repair_instruction
                        or coverage_review.reason
                        or "The coverage verifier requested formatter revision.",
                        "missing_obligations": list(coverage_review.missing_obligations),
                        "contradicted_obligations": list(coverage_review.contradicted_obligations),
                        "unsupported_claims": list(coverage_review.unsupported_claims),
                        "reason": coverage_review.reason,
                    }
                ]
                if observability is not None:
                    observability.warning(
                        stage,
                        "operator.answer_coverage.rejected",
                        "Final answer coverage rejected",
                        "The semantic coverage verifier requested final formatter repair.",
                        details={
                            "attempt": attempt + 1,
                            "verdict": coverage_review.verdict,
                            "missing_obligations": list(coverage_review.missing_obligations),
                            "contradicted_obligations": list(coverage_review.contradicted_obligations),
                            "unsupported_claims": list(coverage_review.unsupported_claims),
                            "reason": coverage_review.reason,
                        },
                        debug_only=True,
                    )
                if attempt + 1 < total_attempts:
                    if coverage_retry_callback is not None:
                        coverage_retry_callback(coverage_review, attempt + 1)
                    feedback = last_errors
                    continue
                return FormatterResult(
                    content=content,
                    formatter=formatter,
                    coverage_review=coverage_review,
                    coverage_accepted=False,
                )
        elif not _content_preserves_authoritative_result(content, authoritative_source):
            last_errors = [
                {
                    "error": "formatter_dropped_authoritative_result",
                    "message": (
                        "Formatter output did not preserve the numeric value from "
                        "inputs['authoritative_result']['primary_value']."
                    ),
                    "authoritative_result_preview": _truncate(
                        _primary_value(authoritative_source),
                        500,
                    ),
                }
            ]
            feedback = last_errors
            continue
        if (
            coverage_review_callback is None
            and not cardinality_judge_enabled
            and not _content_preserves_requested_source_rows(
                user_request,
                content,
                successful_sources,
                authoritative_source,
            )
        ):
            last_errors = [
                {
                    "error": "formatter_dropped_requested_source_rows",
                    "message": (
                        "The user asked for a list/table, but the formatter output did not include "
                        "representative values from the successful source rows."
                    ),
                    "source_previews": [
                        _source_preview(source, max_chars=500)
                        for source in successful_sources
                        if source is not authoritative_source
                    ],
                }
            ]
            feedback = last_errors
            continue
        if cardinality_judge_enabled:
            try:
                cardinality_prompt = build_cardinality_judge_prompt(
                    user_request,
                    successful_sources,
                    content,
                    source_preview_chars=source_preview_chars,
                )
                cardinality_payload = llm_client.complete_json(
                    cardinality_prompt,
                    OperatorCardinalityJudge.model_json_schema(),
                )
                cardinality_judge = OperatorCardinalityJudge.model_validate(
                    cardinality_payload
                )
            except Exception as exc:
                last_errors = [
                    {"error": "cardinality_judge_schema_error", "message": str(exc)}
                ]
                if observability is not None:
                    observability.warning(
                        stage,
                        CARDINALITY_JUDGE_REJECTED_EVENT,
                        "Cardinality judge rejected",
                        "The LLM did not produce a valid cardinality judgment.",
                        details={"attempt": attempt + 1, "errors": last_errors},
                        debug_only=True,
                    )
                feedback = last_errors
                continue
            if observability is not None:
                observability.info(
                    stage,
                    CARDINALITY_JUDGE_PROPOSED_EVENT,
                    "Cardinality judged",
                    "The LLM reviewed whether the final answer preserves requested cardinality.",
                    details={
                        "attempt": attempt + 1,
                        "applies": cardinality_judge.applies,
                        "verdict": cardinality_judge.verdict,
                        "issues": list(cardinality_judge.issues),
                        "confidence": cardinality_judge.confidence,
                    },
                    debug_only=True,
                )
            if cardinality_judge.applies and cardinality_judge.verdict == "revise":
                last_errors = [
                    {
                        "error": "cardinality_judge_requested_revision",
                        "message": cardinality_judge.feedback_for_formatter
                        or cardinality_judge.required_change
                        or "The cardinality judge requested formatter revision.",
                        "issues": list(cardinality_judge.issues),
                        "required_change": cardinality_judge.required_change,
                        "confidence": cardinality_judge.confidence,
                    }
                ]
                feedback = last_errors
                if cardinality_revisions < 1 and attempt + 1 < total_attempts:
                    cardinality_revisions += 1
                    continue
                break
            if observability is not None:
                observability.info(
                    stage,
                    CARDINALITY_JUDGE_ACCEPTED_EVENT,
                    "Cardinality accepted",
                    "The LLM cardinality judge accepted the formatted response.",
                    details={
                        "applies": cardinality_judge.applies,
                        "confidence": cardinality_judge.confidence,
                    },
                    debug_only=True,
                )
        if answer_judge_enabled:
            try:
                judge_prompt = build_answer_judge_prompt(
                    user_request,
                    successful_sources,
                    content,
                    formatter=formatter,
                    source_preview_chars=source_preview_chars,
                )
                judge_payload = llm_client.complete_json(
                    judge_prompt,
                    OperatorAnswerJudge.model_json_schema(),
                )
                judge = OperatorAnswerJudge.model_validate(judge_payload)
            except Exception as exc:
                last_errors = [{"error": "answer_judge_schema_error", "message": str(exc)}]
                if observability is not None:
                    observability.warning(
                        stage,
                        ANSWER_JUDGE_REJECTED_EVENT,
                        "Final answer judge rejected",
                        "The LLM did not produce a valid final answer judgment.",
                        details={"attempt": attempt + 1, "errors": last_errors},
                        debug_only=True,
                    )
                feedback = last_errors
                continue
            if observability is not None:
                observability.info(
                    stage,
                    ANSWER_JUDGE_PROPOSED_EVENT,
                    "Final answer judged",
                    "The LLM reviewed whether the final answer satisfies the user request.",
                    details={
                        "attempt": attempt + 1,
                        "verdict": judge.verdict,
                        "answers_user_request": judge.answers_user_request,
                        "uses_available_outputs": judge.uses_available_outputs,
                        "issues": list(judge.issues),
                        "confidence": judge.confidence,
                    },
                    debug_only=True,
                )
            if judge.verdict != "accept" or not judge.answers_user_request:
                last_errors = [
                    {
                        "error": "answer_judge_requested_revision",
                        "message": judge.feedback_for_formatter
                        or judge.reason
                        or "The answer judge requested formatter revision.",
                        "issues": list(judge.issues),
                        "answers_user_request": judge.answers_user_request,
                        "uses_available_outputs": judge.uses_available_outputs,
                    }
                ]
                feedback = last_errors
                continue
            if observability is not None:
                observability.info(
                    stage,
                    ANSWER_JUDGE_ACCEPTED_EVENT,
                    "Final answer accepted",
                    "The LLM answer judge accepted the formatted response.",
                    details={"confidence": judge.confidence},
                    debug_only=True,
                )
        if observability is not None:
            observability.info(
                stage,
                FORMATTER_COMPLETED_EVENT,
                "Final formatter completed",
                "The final response was produced by a validated local Python formatter.",
                details={
                    "content_length": len(content),
                    "formatter_confidence": formatter.confidence,
                },
                debug_only=True,
            )
        return FormatterResult(
            content=content,
            formatter=formatter,
            coverage_review=coverage_review,
            coverage_accepted=True,
        )

    if observability is not None:
        observability.warning(
            stage,
            FORMATTER_REJECTED_EVENT,
            "Final formatter unavailable",
            "The runtime fell back because final formatting did not pass.",
            details={"errors": last_errors},
            debug_only=True,
        )
    return None


__all__ = [
    "ANSWER_JUDGE_ACCEPTED_EVENT",
    "ANSWER_JUDGE_PROPOSED_EVENT",
    "ANSWER_JUDGE_REJECTED_EVENT",
    "CARDINALITY_JUDGE_ACCEPTED_EVENT",
    "CARDINALITY_JUDGE_PROPOSED_EVENT",
    "CARDINALITY_JUDGE_REJECTED_EVENT",
    "FORMATTER_ACCEPTED_EVENT",
    "FORMATTER_COMPLETED_EVENT",
    "FORMATTER_PROPOSED_EVENT",
    "FORMATTER_REJECTED_EVENT",
    "FormatterResult",
    "FormatterSource",
    "build_answer_judge_prompt",
    "build_cardinality_judge_prompt",
    "build_final_formatter_prompt",
    "run_llm_final_formatter",
]
