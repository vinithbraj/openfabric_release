"""Argument extraction for selected capability tasks."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import model_json_schema

from agent_runtime.capabilities.sql import SQL_AGENTIC_CONTEXT_KEY, sql_prompt_lines_from_context
from agent_runtime.capabilities.registry import CapabilityRegistry
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.core.errors import CapabilityNotFoundError
from agent_runtime.core.semantic_compatibility import canonical_semantic_verb
from agent_runtime.core.types import InputRef, TaskFrame
from agent_runtime.input_pipeline.dataflow_planning import ValidatedDataflowPlan
from agent_runtime.input_pipeline.capability_fit import CapabilityFitDecision
from agent_runtime.input_pipeline.domain_selection import CapabilitySelectionResult
from agent_runtime.input_pipeline.plan_selection import CandidateEvaluation, select_best_candidate
from agent_runtime.llm.proposals import (
    ArgumentExtractionProposal,
    GeneratedArgumentProposal,
    collect_n_best_structured_attempts,
)
from agent_runtime.llm.reproducibility import (
    PlanningTrace,
    PlanningTraceEntry,
    append_trace_entry,
    llm_client_metadata,
)
from agent_runtime.operator.models import OperatorInputBinding
from agent_runtime.parameters import parameter_prompt_lines_from_context
from agent_runtime.prompts import prompt_lines


class ArgumentExtractionResult(BaseModel):
    """Normalized argument payload for one selected capability."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    capability_id: str
    operation_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    generated_arguments: list[dict[str, Any]] = Field(default_factory=list)
    rejected_generated_arguments: list[dict[str, Any]] = Field(default_factory=list)
    missing_required_arguments: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class _ArgumentExtractionResponse(BaseModel):
    """Structured LLM response for capability argument extraction."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    capability_id: str
    operation_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    generated_arguments: list[GeneratedArgumentProposal] = Field(default_factory=list)
    missing_required_arguments: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class _ArgumentReviewResponse(BaseModel):
    """Structured LLM audit of one proposed operator argument payload."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    corrected_arguments: dict[str, Any] = Field(default_factory=dict)
    feedback: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class ArgumentExtractor:
    """Compatibility placeholder used by the lightweight scaffold orchestrator."""

    def extract(self, prompt: str) -> list[object]:
        """Return no inferred arguments until the orchestrator is upgraded."""

        _ = prompt
        return []


def _build_argument_prompt(
    task: TaskFrame,
    manifest: CapabilityManifest,
    fixed_arguments: dict[str, Any] | None = None,
    task_index: dict[str, TaskFrame] | None = None,
    terminal_cwd: str | None = None,
    parameter_context: dict[str, Any] | None = None,
) -> str:
    """Build the strict JSON-only prompt for one argument extraction task."""

    schema = model_json_schema(_ArgumentExtractionResponse)
    is_operator_capability = manifest.domain.strip().lower() == "operator"
    is_operator_shell = manifest.capability_id == "operator.shell_command"
    is_operator_python_transform = manifest.capability_id == "operator.python_transform"
    is_operator_python_action = manifest.capability_id == "operator.python_action"
    defer_python_until_inputs = (
        (is_operator_python_transform or is_operator_python_action)
        and bool(task.dependencies)
    )
    sql_argument_name = (
        "sql"
        if "sql" in (set(manifest.required_arguments) | set(manifest.optional_arguments))
        else ""
    )
    cwd_instruction = (
        f"An Agent UI terminal is active. Use cwd {terminal_cwd!r} for operator actions unless the user explicitly requests another cwd."
        if terminal_cwd
        else "Use cwd '.' unless the user explicitly names a workspace-relative subdirectory."
    )
    global_constraints = task.constraints.get("global_constraints", task.constraints)
    task_index = task_index or {}
    dependency_context = [
        {
            "task_id": dependency_id,
            "description": upstream.description,
            "semantic_verb": upstream.semantic_verb,
            "object_type": upstream.object_type,
            "constraints": upstream.constraints,
        }
        for dependency_id in task.dependencies
        if (upstream := task_index.get(dependency_id)) is not None
    ]
    downstream_context = [
        {
            "task_id": downstream.id,
            "description": downstream.description,
            "semantic_verb": downstream.semantic_verb,
            "object_type": downstream.object_type,
            "constraints": downstream.constraints,
        }
        for downstream in task_index.values()
        if task.id in downstream.dependencies
    ]
    return "\n".join(
        [
            *prompt_lines("input.argument_extraction"),
            *parameter_prompt_lines_from_context(parameter_context or {}, stage="argument_extraction"),
            *sql_prompt_lines_from_context(parameter_context or {}, stage="argument_extraction"),
            (
                "This is an operator capability: author the concrete shell command requested by the task."
                if is_operator_shell
                else (
                    (
                        "This is an operator capability: declare deferred Python code generation for upstream-bound data."
                        if defer_python_until_inputs
                        else "This is an operator capability: author Python code defining main(inputs)."
                    )
                    if is_operator_python_action
                    else (
                        (
                            "This is an operator capability: declare deferred Python code generation for upstream-bound data."
                            if defer_python_until_inputs
                            else "This is an operator capability: author Python code defining transform(inputs)."
                        )
                        if is_operator_python_transform
                        else "Do not generate shell commands."
                    )
                )
            ),
            *(
                [
                    *(
                        [
                            "This python_action depends on previous task output, so do not author arguments.code now.",
                            "Set arguments.defer_code_generation to true and declare arguments.input_bindings from the dependency output.",
                            "The execution stage will ask for def main(inputs): after the upstream output shape and preview are known.",
                        ]
                        if defer_python_until_inputs
                        else [
                            "For operator.python_action, put code in arguments.code; the first non-whitespace characters must be exactly def main(inputs):.",
                            "Use python_action when one Python program should run commands, inspect files, manipulate data, and print or return the final answer.",
                            "python_action may use stdlib modules such as subprocess, os, pathlib, glob, json, re, datetime, csv, math, and statistics.",
                            "If a task can be fully answered by one shell command, shell_command is preferred; otherwise python_action should complete command execution plus data transformation in one action.",
                            "Do not emit transform(inputs) for python_action.",
                        ]
                    ),
                ]
                if is_operator_python_action
                else []
            ),
            *(
                [
                    "Operator actions are approval-gated by the runtime.",
                    cwd_instruction,
                    "Do not access raw payload stores or hidden state.",
                    "If this action needs a prior task output, add an input_bindings item in arguments.",
                    "Each input_bindings item must use canonical keys: input_name, source_action_id, source_field, required, fallback_value.",
                    "Do not use binding aliases such as output_name, output_key, source_task_id, or producer_action_id.",
                    "input_bindings source_action_id may be the producer task id, such as task_1.",
                    "For Python tasks with dependencies, input_bindings must source from the task frame dependencies; if there is one dependency, bind that dependency's stdout.",
                    "For shell actions, put the command string in arguments.command.",
                    "For shell actions, prefer simple well-known CLI invocations over invented shorthand flags; when exact calculation is needed and shell alone is awkward, prefer operator.python_action as the selected capability.",
                    "If this selected shell action's semantic_verb is calculate, analyze, filter, sort, summarize, compare, or render, arguments.command must print the final requested result, not just an intermediate source value.",
                    "If downstream tasks depend on this shell action, author command output that contains the raw fields those downstream tasks need; do not output only a human display summary.",
                    "When downstream calculates a metric from this action, do not output only an identifier/name. Include the source value needed for that calculation, such as a timestamp, count, size, status, or full record.",
                    "Do not put xargs replacement tokens such as {} inside Bash arithmetic $(( ... )); assign values to shell variables first.",
                    "If a shell command needs a path or value discovered by search/list output, recompute that lookup inside the same concrete command; do not assume a bare basename is in the current directory.",
                    "Shell command strings must be concrete. Do not include unresolved placeholders such as {branch_name}, {{ branch_name }}, $BRANCH_NAME, or ${BRANCH_NAME}.",
                    "When a shell action consumes a short value from an earlier action, declare input_bindings and reference the runtime-provided OF_INPUT_* environment variable in arguments.command.",
                    *(
                        [
                            *(
                                [
                                    "This python_transform depends on previous task output, so do not author arguments.code now.",
                                    "Set arguments.defer_code_generation to true and declare arguments.input_bindings from the dependency output.",
                                    "The execution stage will ask for def transform(inputs): after the upstream output shape and preview are known.",
                                    "Do not guess columns, units, table layout, JSON shape, or text format during argument extraction.",
                                ]
                                if defer_python_until_inputs
                                else [
                                    "For operator.python_transform, put code in arguments.code and define exactly transform(inputs).",
                                    "arguments.code must be complete Python source that starts with def transform(inputs): and returns the final cleaned/calculated value.",
                                    "The first non-whitespace characters of arguments.code must be exactly def transform(inputs):; put imports inside that function body.",
                                    "Operator Python is trusted local execution like shell: subprocess, filesystem APIs, imports, and normal data-processing libraries are permitted.",
                                    "Prefer operator.python_action when one Python program should run commands, inspect files, manipulate data, and produce the final answer through the gateway path.",
                                    "When dependency stdout is plain text, parse it as plain text; do not call json.loads unless the dependency command explicitly emits JSON.",
                                    "If this task is a transform, calculate, filter, sort, or summarize step, author arguments.code; do not list code as missing unless the task is genuinely impossible.",
                                    "The code must parse as Python; for multiline strings use escaped \\n or '\\n'.join(...), not raw line breaks inside quotes.",
                                    "Every inputs[...] or inputs.get(...) key used by the code must be supplied by arguments.inputs or arguments.input_bindings.",
                                    "When binding a prior shell command's stdout, treat inputs['stdout'] as the exact stdout string from that command.",
                                    "For a single dependency, use inputs['stdout']; do not use inputs.get('stdout', '') for required bound inputs.",
                                    "Do not emit bare Python snippets, helper-only code, JSON, markdown, or prose in arguments.code.",
                                ]
                            ),
                        ]
                        if is_operator_python_transform
                        else []
                    ),
                ]
                if is_operator_capability
                else []
            ),
            (
                "For sql.query, do not put SQL in arguments; put the database question "
                "in arguments.prompt so the SQL agent's LLM loop can generate, validate, "
                "and retry SQL."
                if manifest.capability_id == "sql.query"
                else (
                    "This capability accepts a SQL string argument named "
                    f"{sql_argument_name}. Only include SQL if the prompt explicitly "
                    "supports it."
                    if sql_argument_name
                    else "Do not generate raw SQL unless the capability explicitly "
                    "accepts a SQL string argument."
                )
            ),
            "Do not invent file paths, table names, database names, or external resources.",
            "Leave generated_arguments empty; the standard path no longer exposes deterministic argument generators.",
            "For operator actions that need random or timestamped values, author the command/code so it generates them safely at execution time.",
            "If required information is missing, leave the argument absent and list it in missing_required_arguments.",
            *(
                [
                    "These arguments are already fixed by validated dataflow planning and must be preserved exactly:",
                    str(fixed_arguments),
                ]
                if fixed_arguments
                else ["No arguments are prefilled by validated dataflow planning."]
            ),
            "Task frame:",
            str(
                {
                    "task_id": task.id,
                    "description": task.description,
                    "semantic_verb": task.semantic_verb,
                    "object_type": task.object_type,
                    "constraints": task.constraints,
                    "dependencies": list(task.dependencies),
                }
            ),
            "Dependency task context:",
            str(dependency_context),
            "Downstream dependent task context:",
            str(downstream_context),
            "Global constraints:",
            str(global_constraints),
            "Selected capability manifest:",
            str(
                {
                    "capability_id": manifest.capability_id,
                    "operation_id": manifest.operation_id,
                    "domain": manifest.domain,
                    "description": manifest.description,
                    "argument_schema": manifest.argument_schema,
                    "required_arguments": manifest.required_arguments,
                    "optional_arguments": manifest.optional_arguments,
                    "examples": manifest.examples,
                }
            ),
            "JSON schema:",
            str(schema),
        ]
    )


def _terminal_cwd_from_context(context: dict[str, Any] | None) -> str | None:
    """Return the trusted terminal cwd carried by the Agent UI request context."""

    if not isinstance(context, dict):
        return None
    session_id = str(context.get("terminal_session_id") or "").strip()
    cwd = str(context.get("terminal_cwd") or "").strip()
    if not session_id or not cwd:
        return None
    return cwd


def _sql_agentic_context_from_request(context: dict[str, Any] | None) -> dict[str, Any]:
    """Return the safe SQL context prepared by the orchestrator, if present."""

    if not isinstance(context, dict):
        return {}
    value = context.get(SQL_AGENTIC_CONTEXT_KEY)
    return dict(value) if isinstance(value, dict) else {}


def _sql_prompt_for_task(
    task: TaskFrame,
    sql_context: dict[str, Any],
    dependency_context: list[dict[str, Any]],
) -> str:
    """Build the NLP prompt that sql.query should hand to SqlAgentService."""

    parts = [
        f"User request: {sql_context.get('original_prompt') or task.description}",
        f"SQL task: {task.description}",
    ]
    parameter_key = str(sql_context.get("parameter_key") or "").strip()
    if parameter_key:
        parts.append(f"Database profile key: {parameter_key}")
    selected_entity = sql_context.get("selected_entity")
    if isinstance(selected_entity, dict) and selected_entity:
        parts.append(
            "Selected table/entity: "
            + str(selected_entity.get("qualified_name") or selected_entity)
        )
    if dependency_context:
        parts.append("Dependency task context: " + str(dependency_context))
    return "\n".join(parts)


def _apply_sql_argument_defaults(
    task: TaskFrame,
    manifest: CapabilityManifest,
    normalized: ArgumentExtractionResult,
    request_context: dict[str, Any] | None,
    dependency_context: list[dict[str, Any]],
) -> ArgumentExtractionResult:
    """Fill safe SQL capability defaults from prepared SQL context."""

    if manifest.capability_id not in {"sql.discover", "sql.query"}:
        return normalized
    sql_context = _sql_agentic_context_from_request(request_context)
    if not sql_context:
        return normalized
    arguments = dict(normalized.arguments)
    assumptions = list(normalized.assumptions)
    missing_required = set(normalized.missing_required_arguments)
    parameter_key = str(sql_context.get("parameter_key") or "").strip()
    if parameter_key and (
        "parameter_key" in set(manifest.required_arguments) | set(manifest.optional_arguments)
    ):
        previous = str(arguments.get("parameter_key") or "").strip()
        if previous != parameter_key:
            assumptions.append(
                "Defaulted SQL parameter_key from the prepared database profile context."
            )
        arguments["parameter_key"] = parameter_key
        missing_required.discard("parameter_key")
    if manifest.capability_id == "sql.query" and "sql" in arguments:
        arguments.pop("sql", None)
        assumptions.append(
            "Ignored generated SQL for sql.query so the SQL LLM loop can author SQL "
            "from schema context."
        )
    if manifest.capability_id in {"sql.query", "sql.discover"} and "prompt" not in arguments:
        arguments["prompt"] = _sql_prompt_for_task(task, sql_context, dependency_context)
        missing_required.discard("prompt")
        assumptions.append(
            "Defaulted SQL prompt from the original request, task description, "
            "dependency context, and selected DB key."
        )
    elif manifest.capability_id == "sql.query":
        prompt = str(arguments.get("prompt") or "").strip()
        original_prompt = str(sql_context.get("original_prompt") or "").strip()
        if prompt and original_prompt and original_prompt not in prompt:
            arguments["prompt"] = "\n".join(
                [
                    _sql_prompt_for_task(task, sql_context, dependency_context),
                    f"LLM-extracted task prompt: {prompt}",
                    f"Database profile key: {parameter_key}" if parameter_key else "",
                ]
            ).strip()
            assumptions.append(
                "Augmented SQL prompt with the original request so predicates and "
                "thresholds are preserved."
            )
    return normalized.model_copy(
        update={
            "arguments": arguments,
            "missing_required_arguments": sorted(missing_required),
            "assumptions": assumptions,
        }
    )


def _apply_operator_terminal_cwd(
    manifest: CapabilityManifest,
    arguments: dict[str, Any],
    terminal_cwd: str | None,
) -> None:
    """Use the active terminal cwd as the operator default cwd for this request."""

    if not terminal_cwd:
        return
    if manifest.domain.strip().lower() != "operator":
        return
    cwd = str(arguments.get("cwd") or "").strip()
    if cwd in {"", "."}:
        arguments["cwd"] = terminal_cwd


def _operator_argument_review_required(
    task: TaskFrame,
    manifest: CapabilityManifest,
    task_index: dict[str, TaskFrame],
) -> bool:
    """Return whether one operator argument payload needs semantic self-audit."""

    if manifest.domain.strip().lower() != "operator":
        return False
    if manifest.capability_id != "operator.shell_command":
        return False
    review_verbs = {
        "analyze",
        "calculate",
        "compare",
        "filter",
        "render",
        "sort",
        "summarize",
    }
    if canonical_semantic_verb(task.semantic_verb) in review_verbs:
        return True
    return any(
        canonical_semantic_verb(downstream.semantic_verb) in review_verbs
        for downstream in task_index.values()
        if task.id in downstream.dependencies
    )


def _build_argument_review_prompt(
    task: TaskFrame,
    manifest: CapabilityManifest,
    arguments: dict[str, Any],
    *,
    task_index: dict[str, TaskFrame],
) -> str:
    """Build a compact phase-local LLM audit prompt for operator arguments."""

    dependency_context = [
        {
            "task_id": dependency_id,
            "description": upstream.description,
            "semantic_verb": upstream.semantic_verb,
            "object_type": upstream.object_type,
            "constraints": upstream.constraints,
        }
        for dependency_id in task.dependencies
        if (upstream := task_index.get(dependency_id)) is not None
    ]
    downstream_context = [
        {
            "task_id": downstream.id,
            "description": downstream.description,
            "semantic_verb": downstream.semantic_verb,
            "object_type": downstream.object_type,
            "constraints": downstream.constraints,
        }
        for downstream in task_index.values()
        if task.id in downstream.dependencies
    ]
    return "\n".join(
        [
            *prompt_lines("input.argument_review"),
            "Task frame:",
            str(
                {
                    "task_id": task.id,
                    "description": task.description,
                    "semantic_verb": task.semantic_verb,
                    "object_type": task.object_type,
                    "constraints": task.constraints,
                    "dependencies": list(task.dependencies),
                }
            ),
            "Dependency task context:",
            str(dependency_context),
            "Downstream dependent task context:",
            str(downstream_context),
            "Selected capability contract:",
            str(
                {
                    "capability_id": manifest.capability_id,
                    "operation_id": manifest.operation_id,
                    "argument_schema": manifest.argument_schema,
                    "required_arguments": manifest.required_arguments,
                    "optional_arguments": manifest.optional_arguments,
                }
            ),
            "Proposed arguments:",
            str(arguments),
            "JSON schema:",
            str(model_json_schema(_ArgumentReviewResponse)),
        ]
    )


def _normalize_path_phrase(value: str) -> str:
    """Normalize common current-directory phrases into the workspace root token."""

    normalized = str(value or "").strip().lower()
    if normalized in {"this folder", "current folder", "current directory", "here"}:
        return "."
    return str(value)


def _normalize_workspace_path(value: Any, workspace_root: Path) -> tuple[str | None, str | None]:
    """Normalize safe workspace paths while preserving supplied paths for safety review."""

    if not isinstance(value, str) or not value.strip():
        return None, "Path value must be a non-empty string."

    raw_value = _normalize_path_phrase(value).strip()
    if raw_value == ".":
        return ".", None

    candidate = Path(raw_value)
    resolved = (
        (workspace_root / candidate).resolve(strict=False)
        if not candidate.is_absolute()
        else candidate.resolve(strict=False)
    )

    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError:
        return (
            raw_value,
            f"Path {value!r} resolves outside the workspace root and will be evaluated by safety policy.",
        )

    normalized = relative.as_posix()
    return normalized or ".", None


def _extract_declared_path(task: TaskFrame) -> str | None:
    """Extract an explicit path already captured in task or global constraints."""

    global_constraints = task.constraints.get("global_constraints", {})
    sources = [task.constraints]
    if isinstance(global_constraints, dict):
        sources.append(global_constraints)

    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("path", "directory", "dir"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _task_text_declares_path(task: TaskFrame) -> bool:
    """Return whether task text itself explicitly names an escaping/path-like location."""

    text = " ".join(str(part or "") for part in (task.description, task.raw_evidence))
    if not text.strip():
        return False
    patterns = [
        r"\b(?:path|directory|dir|folder|in|under|from|inside)\s+/(?:\S*)?",
        r"\b(?:path|directory|dir|folder|in|under|from|inside)\s+\.\.(?:/\S*)?",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _omitted_path_default(manifest: CapabilityManifest) -> str | None:
    """Return a declarative default for path-like arguments that allow omission."""

    path_schema = manifest.argument_schema.get("path", {})
    default_value = path_schema.get("default_when_omitted")
    if not isinstance(default_value, str) or not default_value.strip():
        return None
    if "path" not in manifest.required_arguments:
        return None
    if not manifest.read_only or manifest.mutates_state:
        return None
    return default_value.strip()


def _extract_search_pattern(task: TaskFrame) -> str | None:
    """Extract one generic filename/glob search pattern from constraints or task text."""

    global_constraints = task.constraints.get("global_constraints", {})
    sources = [task.constraints]
    if isinstance(global_constraints, dict):
        sources.append(global_constraints)

    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("pattern", "file_pattern", "glob", "file_name", "filename", "name"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    text = " ".join(str(part or "") for part in (task.description, task.raw_evidence))
    glob_match = re.search(r"(?<!\S)\*+\.[A-Za-z0-9][A-Za-z0-9._-]*", text)
    if glob_match:
        return glob_match.group(0)
    filename_match = re.search(r"(?<!\S)[A-Za-z0-9_.-]+\.[A-Za-z0-9][A-Za-z0-9._-]*(?!\S)", text)
    if filename_match:
        return filename_match.group(0)
    return None


def _extract_limit_from_text(text: str) -> int | None:
    """Extract an explicit row limit from common prompt phrasings."""

    patterns = [
        r"\btop\s+(\d+)\b",
        r"\bfirst\s+(\d+)\b",
        r"\blimit\s+(\d+)\b",
    ]
    lowered = str(text or "").lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return int(match.group(1))
    return None


def _path_would_escape_workspace(value: Any, workspace_root: Path) -> bool:
    """Return whether a path currently resolves outside the configured workspace."""

    if not isinstance(value, str) or not value.strip():
        return False
    _, error = _normalize_workspace_path(value, workspace_root)
    return bool(error and "outside the workspace root" in error.lower())


def _supports_argument(manifest: CapabilityManifest, argument_name: str) -> bool:
    """Return whether a capability manifest allows one argument name."""

    allowed = set(manifest.required_arguments) | set(manifest.optional_arguments)
    return argument_name in allowed


def _is_table_like_read(task: TaskFrame, manifest: CapabilityManifest) -> bool:
    """Return whether the task/capability pair represents a row-oriented read."""

    object_type = task.object_type.strip().lower()
    if not manifest.read_only:
        return False
    if task.semantic_verb not in {"read", "search", "analyze"}:
        return False
    manifest_objects = {value.strip().lower() for value in manifest.object_types}
    return bool({"table", "dataset", "records"} & manifest_objects) or any(
        token in object_type for token in {"table", "row", "dataset", "records"}
    )


def _apply_fixed_argument_precedence(
    arguments: dict[str, Any],
    fixed_arguments: dict[str, Any],
    manifest: CapabilityManifest,
    assumptions: list[str],
) -> None:
    """Let validated fixed/dataflow arguments override conflicting generated arguments."""

    declared_arguments = set(manifest.required_arguments) | set(manifest.optional_arguments)
    if {"content", "input_ref"} <= declared_arguments:
        if "input_ref" in fixed_arguments and "content" in fixed_arguments:
            fixed_arguments.pop("content", None)
            assumptions.append(
                "Removed duplicate fixed content because validated upstream input_ref supplies the write payload."
            )
        if "input_ref" in fixed_arguments and "content" in arguments:
            arguments.pop("content", None)
            assumptions.append(
                "Removed generated content because validated upstream input_ref supplies the write payload."
            )
        elif "content" in fixed_arguments and "input_ref" in arguments:
            arguments.pop("input_ref", None)
            assumptions.append(
                "Removed generated input_ref because validated content supplies the write payload."
            )


def _apply_operator_input_binding_defaults(
    task: TaskFrame,
    manifest: CapabilityManifest,
    arguments: dict[str, Any],
    assumptions: list[str],
) -> None:
    """Add structural operator input bindings when a dependent transform omitted them."""

    if manifest.capability_id not in {"operator.python_transform", "operator.python_action"}:
        return
    if not task.dependencies:
        return
    if arguments.get("input_bindings"):
        return
    bindings: list[dict[str, Any]] = []
    if len(task.dependencies) == 1:
        bindings.append(
            {
                "input_name": "stdout",
                "source_action_id": task.dependencies[0],
                "source_field": "stdout",
                "required": True,
            }
        )
    else:
        for dependency in task.dependencies:
            bindings.append(
                {
                    "input_name": f"{dependency}_stdout",
                    "source_action_id": dependency,
                    "source_field": "stdout",
                    "required": True,
                }
            )
    arguments["input_bindings"] = bindings
    assumptions.append(
        f"Defaulted operator {manifest.operation_id} input_bindings from declared task dependencies."
    )


def _apply_operator_deferred_python_defaults(
    task: TaskFrame,
    manifest: CapabilityManifest,
    arguments: dict[str, Any],
    assumptions: list[str],
) -> None:
    """Defer upstream-dependent operator Python code until execution."""

    if manifest.capability_id not in {"operator.python_transform", "operator.python_action"}:
        return
    if not task.dependencies:
        return
    arguments["defer_code_generation"] = True
    if str(arguments.get("code") or "").strip():
        arguments.pop("code", None)
        assumptions.append(
            "Deferred upstream-dependent Python code generation until runtime input shape is available."
        )


def _normalize_operator_input_bindings_argument(arguments: dict[str, Any]) -> None:
    """Canonicalize operator input binding dictionaries in-place when possible."""

    bindings = arguments.get("input_bindings")
    if not isinstance(bindings, list):
        return
    normalized: list[dict[str, Any]] = []
    changed = False
    for binding in bindings:
        if not isinstance(binding, dict):
            normalized.append(binding)
            continue
        try:
            canonical = OperatorInputBinding.model_validate(binding).model_dump(mode="json")
        except Exception:
            normalized.append(binding)
            continue
        normalized.append(canonical)
        changed = changed or canonical != binding
    if changed:
        arguments["input_bindings"] = normalized


def _apply_deterministic_normalization(
    task: TaskFrame,
    manifest: CapabilityManifest,
    response: _ArgumentExtractionResponse,
    workspace_root: Path,
    fixed_arguments: dict[str, Any] | None = None,
) -> ArgumentExtractionResult:
    """Normalize extracted arguments and compute deterministic missing fields."""

    fixed_arguments = dict(fixed_arguments or {})
    allowed_arguments = set(manifest.required_arguments) | set(manifest.optional_arguments)
    arguments = {
        key: value for key, value in dict(response.arguments).items() if key in allowed_arguments
    }
    assumptions = list(response.assumptions)
    missing_required = set(response.missing_required_arguments)
    generated_arguments: list[dict[str, Any]] = []
    rejected_generated_arguments: list[dict[str, Any]] = []

    if _supports_argument(manifest, "path"):
        declared_path = _extract_declared_path(task)
        has_user_declared_path = declared_path is not None or _task_text_declares_path(task)
        omitted_default = _omitted_path_default(manifest)
        if "path" not in arguments:
            inferred_path = declared_path or _extract_current_directory_path(
                task.description
            )
            if inferred_path is not None:
                arguments["path"] = inferred_path
            elif omitted_default is not None:
                arguments["path"] = omitted_default
                assumptions.append(
                    "Defaulted omitted read-only filesystem path to the workspace root."
                )
        elif isinstance(arguments["path"], str):
            arguments["path"] = _normalize_path_phrase(arguments["path"])
            if (
                omitted_default is not None
                and not has_user_declared_path
                and _path_would_escape_workspace(arguments["path"], workspace_root)
            ):
                arguments["path"] = omitted_default
                assumptions.append(
                    "Replaced an invented escaping filesystem path with the workspace root because the user did not declare a path."
                )

    if _supports_argument(manifest, "pattern") and "pattern" not in arguments:
        inferred_pattern = _extract_search_pattern(task)
        if inferred_pattern is not None:
            arguments["pattern"] = inferred_pattern

    if _supports_argument(manifest, "limit") and "limit" not in arguments:
        explicit_limit = _extract_limit_from_text(task.description)
        if explicit_limit is not None:
            arguments["limit"] = explicit_limit
        elif _is_table_like_read(task, manifest):
            arguments["limit"] = 100

    if response.generated_arguments:
        rejected_generated_arguments.extend(
            proposal.model_dump(mode="json") for proposal in response.generated_arguments
        )
        assumptions.append(
            "Ignored generated argument proposals because deterministic argument generators have been retired from the standard operator-backed path."
        )

    _apply_operator_input_binding_defaults(task, manifest, arguments, assumptions)
    _apply_operator_deferred_python_defaults(task, manifest, arguments, assumptions)
    if bool(arguments.get("defer_code_generation")):
        missing_required.discard("code")
    if manifest.domain.strip().lower() == "operator":
        _normalize_operator_input_bindings_argument(arguments)

    for key in list(arguments):
        if key == "path" or key.endswith("_path"):
            normalized_path, error = _normalize_workspace_path(arguments[key], workspace_root)
            if normalized_path is None:
                arguments.pop(key, None)
                if key in manifest.required_arguments:
                    missing_required.add(key)
                if error:
                    assumptions.append(error)
            else:
                arguments[key] = normalized_path
                if error:
                    assumptions.append(error)

    for required_argument in manifest.required_arguments:
        if required_argument not in arguments:
            missing_required.add(required_argument)

    if fixed_arguments:
        _apply_fixed_argument_precedence(arguments, fixed_arguments, manifest, assumptions)
        arguments.update(fixed_arguments)
        for required_argument in fixed_arguments:
            missing_required.discard(required_argument)

    return ArgumentExtractionResult(
        task_id=task.id,
        capability_id=manifest.capability_id,
        operation_id=manifest.operation_id,
        arguments=arguments,
        generated_arguments=generated_arguments,
        rejected_generated_arguments=rejected_generated_arguments,
        missing_required_arguments=sorted(missing_required),
        assumptions=assumptions,
        confidence=response.confidence,
    )


def _literal_python_input_keys(code: str) -> set[str]:
    """Return literal ``inputs[...]`` keys referenced by operator Python code."""

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id == "inputs":
                slice_node = node.slice
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                    referenced.add(slice_node.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "inputs"
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                referenced.add(node.args[0].value)
    return referenced


def _validate_operator_python_arguments(arguments: dict[str, Any]) -> list[str]:
    """Validate Python-transform argument shape before DAG construction."""

    errors: list[str] = []
    if bool(arguments.get("defer_code_generation")):
        if str(arguments.get("code") or "").strip():
            errors.append(
                "deferred_code_already_present: deferred python_transform arguments must omit code."
            )
        if not arguments.get("input_bindings"):
            errors.append(
                "deferred_code_missing_inputs: deferred python_transform arguments require input_bindings."
            )
        return errors
    code = str(arguments.get("code") or "")
    if not code.lstrip().startswith("def transform(inputs):"):
        errors.append(
            "python_transform_contract_shape: arguments.code must start with "
            "def transform(inputs): and put all executable statements inside that function."
        )
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"python_syntax_error: {exc}"]
    function_names = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    if "transform" not in function_names:
        errors.append("missing_transform_function: python_transform code must define transform(inputs).")

    literal_inputs = arguments.get("inputs")
    allowed_input_names: set[str] = set()
    if isinstance(literal_inputs, dict):
        allowed_input_names.update(
            str(name) for name in literal_inputs.keys() if str(name).strip()
        )
    input_bindings = arguments.get("input_bindings")
    if isinstance(input_bindings, list):
        for binding in input_bindings:
            if isinstance(binding, dict):
                input_name = str(binding.get("input_name") or "").strip()
                if input_name:
                    allowed_input_names.add(input_name)
    for input_name in sorted(_literal_python_input_keys(code) - allowed_input_names):
        errors.append(
            f"python_input_unbound: code references inputs[{input_name!r}], "
            "but no literal input or input_binding supplies that key."
        )
    return errors


def _validate_operator_python_action_arguments(arguments: dict[str, Any]) -> list[str]:
    """Validate gateway Python-action argument shape before DAG construction."""

    errors: list[str] = []
    if bool(arguments.get("defer_code_generation")):
        if str(arguments.get("code") or "").strip():
            errors.append(
                "deferred_code_already_present: deferred python_action arguments must omit code."
            )
        if not arguments.get("input_bindings"):
            errors.append(
                "deferred_code_missing_inputs: deferred python_action arguments require input_bindings."
            )
        return errors
    code = str(arguments.get("code") or "")
    if not code.lstrip().startswith("def main(inputs):"):
        errors.append(
            "python_action_contract_shape: arguments.code must start with "
            "def main(inputs): and put executable work inside that function."
        )
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"python_syntax_error: {exc}"]
    function_names = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    if "main" not in function_names:
        errors.append("missing_python_action_main: python_action code must define main(inputs).")
    return errors


def _validate_operator_arguments(
    manifest: CapabilityManifest,
    arguments: dict[str, Any],
    task: TaskFrame | None = None,
    request_context: dict[str, Any] | None = None,
) -> list[str]:
    """Return operator-native argument validation errors for retry feedback."""

    errors: list[str] = []
    dependencies = {str(dependency).strip() for dependency in (task.dependencies if task else [])}
    dependencies.discard("")
    task_id = str(task.id).strip() if task is not None else ""
    sql_agentic_mode = isinstance((request_context or {}).get(SQL_AGENTIC_CONTEXT_KEY), dict)
    input_bindings = arguments.get("input_bindings")
    if isinstance(input_bindings, list):
        for binding in input_bindings:
            if not isinstance(binding, dict):
                continue
            source_action_id = str(binding.get("source_action_id") or "").strip()
            normalized_source_id = source_action_id.removeprefix("node::")
            if task_id and normalized_source_id == task_id:
                errors.append(
                    "input_binding_self_reference: input_bindings cannot read from the "
                    f"same task {task_id!r}; bind to a declared upstream dependency instead."
                )
            if dependencies and source_action_id and normalized_source_id not in dependencies:
                errors.append(
                    "input_binding_source_not_declared_dependency: "
                    f"input binding source_action_id {source_action_id!r} is not one of "
                    f"the task's declared dependencies {sorted(dependencies)!r}."
                )
            if (
                sql_agentic_mode
                and manifest.capability_id
                in {"operator.python_transform", "operator.python_action"}
                and source_action_id
                and normalized_source_id not in dependencies
            ):
                errors.append(
                    "sql_agentic_input_binding_source_not_declared_dependency: "
                    f"SQL-agentic Python post-processing must bind only to declared "
                    f"upstream SQL dependencies; got {source_action_id!r} with "
                    f"dependencies {sorted(dependencies)!r}."
                )
    if manifest.capability_id == "operator.python_action":
        errors.extend(_validate_operator_python_action_arguments(arguments))
    if manifest.capability_id == "operator.python_transform":
        errors.extend(_validate_operator_python_arguments(arguments))
    return errors


def _review_operator_argument_candidate(
    *,
    task: TaskFrame,
    manifest: CapabilityManifest,
    response: _ArgumentExtractionResponse,
    normalized: ArgumentExtractionResult,
    workspace_root: Path,
    fixed_arguments: dict[str, Any],
    task_index: dict[str, TaskFrame],
    llm_client,
) -> tuple[ArgumentExtractionResult | None, dict[str, Any]]:
    """Ask the LLM to audit a structurally valid operator argument payload."""

    if not _operator_argument_review_required(task, manifest, task_index):
        return normalized, {}

    prompt = _build_argument_review_prompt(
        task,
        manifest,
        normalized.arguments,
        task_index=task_index,
    )
    record: dict[str, Any] = {
        "task_id": task.id,
        "capability_id": manifest.capability_id,
        "operation_id": manifest.operation_id,
        "prompt": prompt,
        "raw_response": None,
        "parsed_response": None,
        "validation_errors": [],
    }
    try:
        raw_response = llm_client.complete_json(prompt, _ArgumentReviewResponse.model_json_schema())
        record["raw_response"] = raw_response
        review = _ArgumentReviewResponse.model_validate(raw_response)
        record["parsed_response"] = review.model_dump(mode="json")
    except Exception as exc:
        record["validation_errors"] = [f"argument_review_invalid: {exc}"]
        return None, record

    if review.accepted:
        return normalized, record

    feedback = "; ".join(review.feedback[:5]) if review.feedback else review.reason
    if not review.corrected_arguments:
        record["validation_errors"] = [
            "argument_review_rejected: proposed arguments do not satisfy the task"
            + (f" ({feedback})" if feedback else "")
        ]
        return None, record

    corrected_response = _ArgumentExtractionResponse(
        task_id=response.task_id,
        capability_id=response.capability_id,
        operation_id=response.operation_id,
        arguments=review.corrected_arguments,
        generated_arguments=[],
        missing_required_arguments=[],
        assumptions=[
            *response.assumptions,
            "Argument self-review replaced the operator arguments before approval.",
            *review.feedback,
        ],
        confidence=min(float(response.confidence), float(review.confidence)),
    )
    corrected = _apply_deterministic_normalization(
        task,
        manifest,
        corrected_response,
        workspace_root,
        fixed_arguments,
    )
    argument_errors = _validate_operator_arguments(manifest, corrected.arguments, task)
    if corrected.missing_required_arguments:
        argument_errors.extend(
            f"missing_required_arguments: {argument_name}"
            for argument_name in corrected.missing_required_arguments
        )
    if argument_errors:
        record["validation_errors"] = argument_errors
        return None, record

    record["selected_candidate"] = corrected.model_dump(mode="json")
    return corrected, record


def _extract_current_directory_path(description: str) -> str | None:
    """Return the workspace-root token when the task clearly refers to the current directory."""

    lowered = str(description or "").lower()
    markers = ("this folder", "current folder", "current directory", "here")
    if any(marker in lowered for marker in markers):
        return "."
    return None


def _fixed_arguments_for_task(
    task_id: str,
    dataflow_plan: ValidatedDataflowPlan | None,
) -> dict[str, InputRef]:
    """Return validated dataflow-prefilled arguments for one task."""

    if dataflow_plan is None:
        return {}
    fixed: dict[str, InputRef] = {}
    for ref in dataflow_plan.refs:
        if ref.consumer_task_id == task_id:
            fixed[ref.consumer_argument_name] = ref.input_ref
    return fixed


def extract_arguments(
    tasks: list[TaskFrame],
    selections: list[CapabilitySelectionResult],
    registry: CapabilityRegistry,
    llm_client,
    n_best: int = 2,
    trace: PlanningTrace | None = None,
    dataflow_plan: ValidatedDataflowPlan | None = None,
    capability_fit_decisions: list[CapabilityFitDecision] | None = None,
    request_context: dict[str, Any] | None = None,
) -> list[ArgumentExtractionResult]:
    """Extract and normalize capability arguments for selected task frames.

    The normal path asks once per task. ``n_best`` now allows a single
    corrective retry only when the first structured response is unusable,
    avoiding repeated near-identical argument prompts for successful cases.
    """

    task_by_id = {task.id: task for task in tasks}
    selection_by_task = {selection.task_id: selection for selection in selections}
    fit_by_task = {decision.task_id: decision for decision in (capability_fit_decisions or [])}
    workspace_root = Path.cwd().resolve()
    terminal_cwd = _terminal_cwd_from_context(request_context)
    results: list[ArgumentExtractionResult] = []

    for task in tasks:
        fit_decision = fit_by_task.get(task.id)
        if fit_decision is not None and not fit_decision.is_fit:
            if isinstance(trace, PlanningTrace):
                append_trace_entry(
                    trace,
                    PlanningTraceEntry(
                        stage="argument_extraction_skipped",
                        request_id=str(trace.request_id),
                        prompt_template_id="argument_extraction",
                        selected_candidate={
                            "task_id": task.id,
                            "reason": fit_decision.status,
                            "capability_fit_decision": fit_decision.model_dump(mode="json"),
                        },
                        rejection_reasons=list(fit_decision.deterministic_rejections),
                    ),
                )
            continue
        selection = selection_by_task.get(task.id)
        if selection is None or selection.selected is None:
            results.append(
                ArgumentExtractionResult(
                    task_id=task.id,
                    capability_id="unknown",
                    operation_id="unknown",
                    arguments={},
                    missing_required_arguments=[],
                    assumptions=[
                        selection.unresolved_reason if selection is not None and selection.unresolved_reason else "No capability was selected for this task."
                    ],
                    confidence=0.0,
                )
            )
            continue

        try:
            capability = registry.get(selection.selected.capability_id)
        except CapabilityNotFoundError:
            results.append(
                ArgumentExtractionResult(
                    task_id=task.id,
                    capability_id=selection.selected.capability_id,
                    operation_id=selection.selected.operation_id,
                    arguments={},
                    missing_required_arguments=[],
                    assumptions=[f"Selected capability is not registered: {selection.selected.capability_id}."],
                    confidence=0.0,
                )
            )
            continue

        manifest = capability.manifest
        fixed_arguments = _fixed_arguments_for_task(task.id, dataflow_plan)
        prompt = _build_argument_prompt(
            task_by_id[task.id],
            manifest,
            fixed_arguments,
            task_by_id,
            terminal_cwd,
            request_context,
        )
        dependency_context = [
            {
                "task_id": dependency_id,
                "description": upstream.description,
                "semantic_verb": upstream.semantic_verb,
                "object_type": upstream.object_type,
                "constraints": upstream.constraints,
            }
            for dependency_id in task.dependencies
            if (upstream := task_by_id.get(dependency_id)) is not None
        ]
        downstream_context = [
            {
                "task_id": downstream.id,
                "description": downstream.description,
                "semantic_verb": downstream.semantic_verb,
                "object_type": downstream.object_type,
                "constraints": downstream.constraints,
            }
            for downstream in task_by_id.values()
            if task.id in downstream.dependencies
        ]
        max_attempts = max(1, int(n_best))
        attempts = collect_n_best_structured_attempts(
            llm_client=llm_client,
            system_prompt=prompt,
            user_payload={
                "task_id": task.id,
                "description": task.description,
                "constraints": task.constraints,
                "dependencies": list(task.dependencies),
                "dependency_context": dependency_context,
                "downstream_context": downstream_context,
                "capability_id": manifest.capability_id,
                "operation_id": manifest.operation_id,
            },
            output_model=ArgumentExtractionProposal,
            n=1,
        )
        evaluations: list[CandidateEvaluation[ArgumentExtractionResult]] = []
        argument_review_records: list[dict[str, Any]] = []

        def evaluate_attempts(attempt_items) -> list[CandidateEvaluation[ArgumentExtractionResult]]:
            evaluated: list[CandidateEvaluation[ArgumentExtractionResult]] = []
            for attempt in attempt_items:
                evaluation = CandidateEvaluation[ArgumentExtractionResult](
                    raw_response=attempt.raw_llm_response,
                    validation_errors=list(attempt.validation_errors),
                )
                if attempt.parsed_proposal is None or attempt.validation_errors:
                    evaluated.append(evaluation)
                    continue
                proposal = attempt.parsed_proposal
                response = _ArgumentExtractionResponse.model_validate(proposal.model_dump(mode="json"))
                normalized = _apply_deterministic_normalization(
                    task,
                    manifest,
                    response,
                    workspace_root,
                    fixed_arguments,
                )
                normalized = _apply_sql_argument_defaults(
                    task,
                    manifest,
                    normalized,
                    request_context,
                    dependency_context,
                )
                _apply_operator_terminal_cwd(manifest, normalized.arguments, terminal_cwd)
                argument_errors = _validate_operator_arguments(
                    manifest,
                    normalized.arguments,
                    task,
                    request_context,
                )
                if normalized.missing_required_arguments:
                    argument_errors.extend(
                        f"missing_required_arguments: {argument_name}"
                        for argument_name in normalized.missing_required_arguments
                    )
                if argument_errors:
                    evaluation.validation_errors.extend(argument_errors)
                    evaluated.append(evaluation)
                    continue
                reviewed, review_record = _review_operator_argument_candidate(
                    task=task,
                    manifest=manifest,
                    response=response,
                    normalized=normalized,
                    workspace_root=workspace_root,
                    fixed_arguments=fixed_arguments,
                    task_index=task_by_id,
                    llm_client=llm_client,
                )
                if review_record:
                    argument_review_records.append(review_record)
                if reviewed is None:
                    evaluation.validation_errors.extend(
                        str(error)
                        for error in review_record.get("validation_errors", [])
                    )
                    evaluated.append(evaluation)
                    continue
                normalized = reviewed
                _apply_operator_terminal_cwd(manifest, normalized.arguments, terminal_cwd)
                evaluation.proposal = normalized
                evaluation.confidence = normalized.confidence
                evaluation.missing_required_count = len(normalized.missing_required_arguments)
                evaluation.assumption_count = len(normalized.assumptions)
                evaluated.append(evaluation)
            return evaluated

        evaluations.extend(evaluate_attempts(attempts))
        selected = select_best_candidate(evaluations)
        if (
            (selected is None or selected.proposal is None)
            and max_attempts > len(attempts)
        ):
            feedback = [
                error
                for evaluation in evaluations
                for error in (evaluation.validation_errors + evaluation.rejection_reasons)
            ]
            retry_prompt = "\n".join(
                [
                    prompt,
                    "Previous argument extraction attempt did not produce a usable validated argument set.",
                    f"Validation feedback: {'; '.join(feedback[:5]) if feedback else 'No valid argument extraction proposal was accepted.'}",
                    "Missing required arguments are not acceptable for this selected capability; author every required argument unless the task is genuinely impossible.",
                    "For operator.python_action, rewrite arguments.code so the first non-whitespace characters are exactly def main(inputs): and all executable statements are inside that function body.",
                    "For operator.python_transform, rewrite arguments.code so the first non-whitespace characters are exactly def transform(inputs): and all executable statements are inside that function body.",
                    "Return one complete corrected argument extraction using only declared capability arguments.",
                    "Include generated_arguments, missing_required_arguments, assumptions, and confidence even when they are empty/default values.",
                ]
            )
            retry_attempts = collect_n_best_structured_attempts(
                llm_client=llm_client,
                system_prompt=retry_prompt,
                user_payload={
                    "task_id": task.id,
                    "description": task.description,
                    "constraints": task.constraints,
                    "dependencies": list(task.dependencies),
                    "dependency_context": dependency_context,
                    "downstream_context": downstream_context,
                    "capability_id": manifest.capability_id,
                    "operation_id": manifest.operation_id,
                },
                output_model=ArgumentExtractionProposal,
                n=1,
            )
            attempts.extend(retry_attempts)
            evaluations.extend(evaluate_attempts(retry_attempts))
            selected = select_best_candidate(evaluations)
        if selected is None or selected.proposal is None:
            results.append(
                ArgumentExtractionResult(
                    task_id=task.id,
                    capability_id=manifest.capability_id,
                    operation_id=manifest.operation_id,
                    arguments={},
                    missing_required_arguments=list(manifest.required_arguments),
                    assumptions=["No valid argument extraction proposal was accepted."],
                    confidence=0.0,
                )
            )
            continue

        if isinstance(trace, PlanningTrace):
            model_name, temperature = llm_client_metadata(llm_client)
            if argument_review_records:
                append_trace_entry(
                    trace,
                    PlanningTraceEntry(
                        stage="argument_extraction_review",
                        request_id=str(trace.request_id),
                        model_name=model_name,
                        llm_temperature=temperature,
                        prompt_template_id="argument_extraction_review",
                        raw_llm_response=[
                            record.get("raw_response") for record in argument_review_records
                        ],
                        parsed_proposal=[
                            record.get("parsed_response") for record in argument_review_records
                        ],
                        validation_errors=[
                            error
                            for record in argument_review_records
                            for error in record.get("validation_errors", [])
                        ],
                        selected_candidate=[
                            record.get("selected_candidate")
                            for record in argument_review_records
                            if record.get("selected_candidate") is not None
                        ],
                    ),
                )
            append_trace_entry(
                trace,
                PlanningTraceEntry(
                    stage="argument_extraction",
                    request_id=str(trace.request_id),
                    model_name=model_name,
                    llm_temperature=temperature,
                    prompt_template_id="argument_extraction",
                    raw_llm_response=[attempt.raw_llm_response for attempt in attempts],
                    parsed_proposal=[
                        attempt.parsed_proposal.model_dump(mode="json")
                        if attempt.parsed_proposal is not None
                        else None
                        for attempt in attempts
                    ],
                    validation_errors=[
                        error for evaluation in evaluations for error in evaluation.validation_errors
                    ],
                    selected_candidate=selected.proposal.model_dump(mode="json"),
                    rejection_reasons=[
                        reason for evaluation in evaluations for reason in evaluation.rejection_reasons
                    ],
                ),
            )
        results.append(selected.proposal)

    return results
