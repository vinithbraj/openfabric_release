"""Validation helpers for input semantic contracts."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.core.errors import ValidationError
from agent_runtime.core.types import ALLOWED_SEMANTIC_VERBS, TaskFrame


DECOMPOSITION_FORBIDDEN_CONSTRAINT_KEYS = {
    "arguments",
    "capability_id",
    "code",
    "command",
    "cwd",
    "declared_output_shape",
    "input_bindings",
    "inputs",
    "operation_id",
    "output_key",
    "output_keys",
    "python",
    "python_code",
    "query",
    "shell",
    "shell_command",
    "sql",
    "timeout_seconds",
}

_STORAGE_CONTEXT_RE = re.compile(
    r"\b(?:block\s+device|device|disk|drive|file\s*system|filesystem|fs|"
    r"mount|partition|storage|usb|volume)\b|/dev/[A-Za-z0-9_/.-]+",
    re.IGNORECASE,
)
_STORAGE_RESET_RE = re.compile(
    r"\b(?:reformat|wipe|erase|repartition|factory\s+reset)\b|"
    r"\bformat\s+(?:a\s+|an\s+|the\s+|this\s+|that\s+|my\s+)?"
    r"(?:block\s+device|device|disk|drive|partition|storage|usb|volume|/dev/[A-Za-z0-9_/.-]+)\b|"
    r"\b(?:create|initialize|initialise|reset)\s+"
    r"(?:a\s+|an\s+|the\s+|new\s+)?"
    r"(?:file\s*system|filesystem|partition\s+table|disk|drive|device|volume)\b",
    re.IGNORECASE,
)
_TROUBLESHOOTING_REPAIR_RE = re.compile(
    r"\b(?:fix|repair|recover|troubleshoot|diagnose|resolve|mount\s+error|"
    r"wrong\s+fs\s+type|bad\s+superblock|bad\s+option)\b",
    re.IGNORECASE,
)


class PlanningContractIssue(BaseModel):
    """One generic planning-contract validation issue."""

    model_config = ConfigDict(extra="forbid")

    error: str
    message: str
    field: str | None = None
    task_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PlanningContractValidationResult(BaseModel):
    """Result of generic planning-contract validation."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    issues: list[PlanningContractIssue] = Field(default_factory=list)

    def feedback(self) -> list[dict[str, Any]]:
        """Return structured feedback suitable for one LLM repair attempt."""

        return [issue.model_dump(mode="json") for issue in self.issues]


class PlanningContractValidator:
    """Validate LLM semantic planning output without deciding semantic meaning."""

    def validate_tasks(
        self,
        tasks: list[TaskFrame],
        *,
        original_prompt: str = "",
    ) -> PlanningContractValidationResult:
        """Validate task-frame contracts and provenance generically."""

        issues: list[PlanningContractIssue] = []
        prompt_text = str(original_prompt or "")
        allowed_verbs = set(ALLOWED_SEMANTIC_VERBS)
        task_ids = {task.id for task in tasks}
        for task in tasks:
            if task.semantic_verb not in allowed_verbs:
                issues.append(
                    PlanningContractIssue(
                        error="invalid_semantic_verb",
                        message=(
                            f"semantic_verb {task.semantic_verb!r} is not declared. "
                            f"Use one of: {list(ALLOWED_SEMANTIC_VERBS)}."
                        ),
                        field="semantic_verb",
                        task_id=task.id,
                    )
                )
            for dependency in task.dependencies:
                if dependency not in task_ids:
                    issues.append(
                        PlanningContractIssue(
                            error="unknown_dependency",
                            message=f"Task {task.id} depends on unknown task {dependency}.",
                            field="dependencies",
                            task_id=task.id,
                            details={"dependency": dependency},
                        )
                    )
            issues.extend(
                self._validate_constraint_provenance(
                    task,
                    original_prompt=prompt_text,
                )
            )
            issues.extend(self._validate_stage_boundary(task))
            issues.extend(
                self._validate_destructive_storage_reset_intent(
                    task,
                    original_prompt=prompt_text,
                )
            )
        return PlanningContractValidationResult(accepted=not issues, issues=issues)

    def _validate_destructive_storage_reset_intent(
        self,
        task: TaskFrame,
        *,
        original_prompt: str,
    ) -> list[PlanningContractIssue]:
        """Reject invented storage-reset tasks for troubleshooting prompts."""

        task_text = _task_planning_text(task)
        prompt_text = str(original_prompt or "")
        if not _looks_like_storage_reset(task_text):
            return []
        if _prompt_explicitly_requests_storage_reset(prompt_text):
            return []
        details = {
            "task_description": task.description,
            "object_type": task.object_type,
            "semantic_verb": task.semantic_verb,
        }
        if _TROUBLESHOOTING_REPAIR_RE.search(prompt_text):
            details["prompt_intent"] = "troubleshooting_or_repair"
        return [
            PlanningContractIssue(
                error="unrequested_destructive_storage_reset",
                message=(
                    f"Task {task.id} appears to introduce a destructive storage reset "
                    "such as formatting, wiping, erasing, repartitioning, or creating a "
                    "new filesystem, but the original prompt did not explicitly request "
                    "that data-destructive operation. For troubleshooting or repair "
                    "requests, decompose into inspect/diagnose/repair/ask-user tasks "
                    "instead of destructive reset tasks."
                ),
                field="description",
                task_id=task.id,
                details=details,
            )
        ]

    def _validate_stage_boundary(self, task: TaskFrame) -> list[PlanningContractIssue]:
        """Reject downstream executable artifacts from semantic decomposition."""

        issues: list[PlanningContractIssue] = []
        for path, key in _iter_constraint_keys(task.constraints):
            if key.casefold() not in DECOMPOSITION_FORBIDDEN_CONSTRAINT_KEYS:
                continue
            issues.append(
                PlanningContractIssue(
                    error="decomposition_downstream_artifact",
                    message=(
                        f"Task {task.id} constraint {path!r} contains downstream executable "
                        "planning data. Decomposition must describe semantic intent only; "
                        "commands, code, capability ids, operation ids, SQL, and operator "
                        "argument payloads belong to later pipeline stages."
                    ),
                    field=f"constraints.{path}",
                    task_id=task.id,
                    details={"forbidden_key": key},
                )
            )
        return issues

    def _validate_constraint_provenance(
        self,
        task: TaskFrame,
        *,
        original_prompt: str,
    ) -> list[PlanningContractIssue]:
        """Validate user-explicit provenance without interpreting the value."""

        issues: list[PlanningContractIssue] = []
        provenance = task.constraints.get("constraint_provenance")
        if not isinstance(provenance, dict):
            return issues
        for key, metadata in provenance.items():
            if not isinstance(metadata, dict):
                continue
            source_type = str(metadata.get("source_type") or "").strip()
            if source_type != "user_explicit":
                continue
            source_text = str(metadata.get("source_text") or metadata.get("value") or "").strip()
            source_span = metadata.get("source_span")
            if source_text and source_text.casefold() not in original_prompt.casefold() and source_span is None:
                issues.append(
                    PlanningContractIssue(
                        error="invalid_user_explicit_provenance",
                        message=(
                            f"Constraint {key!r} was marked user_explicit, but "
                            f"{source_text!r} does not appear in the original prompt."
                        ),
                        field=f"constraints.constraint_provenance.{key}",
                        task_id=task.id,
                        details={"source_text": source_text},
                    )
                )
        return issues


def _iter_constraint_keys(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Return dotted paths and leaf keys from a nested constraints value."""

    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            found.append((path, key_text))
            found.extend(_iter_constraint_keys(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            found.extend(_iter_constraint_keys(nested, path))
    return found


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        parts: list[str] = []
        for key, nested in value.items():
            parts.append(str(key))
            parts.append(_flatten_text(nested))
        return " ".join(part for part in parts if part)
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value or "")


def _task_planning_text(task: TaskFrame) -> str:
    return " ".join(
        str(part or "")
        for part in (
            task.id,
            task.description,
            task.semantic_verb,
            task.object_type,
            task.operation_intent,
            task.side_effect_type,
            _flatten_text(task.constraints),
        )
    )


def _looks_like_storage_reset(text: str) -> bool:
    value = str(text or "")
    return bool(_STORAGE_RESET_RE.search(value) and _STORAGE_CONTEXT_RE.search(value))


def _prompt_explicitly_requests_storage_reset(prompt: str) -> bool:
    value = str(prompt or "")
    return bool(_STORAGE_RESET_RE.search(value) and _STORAGE_CONTEXT_RE.search(value))


class TaskFrameValidator:
    """Validate required task-frame invariants."""

    def validate(self, frame: TaskFrame) -> None:
        """Raise when a frame is too incomplete to lower."""

        if not frame.description.strip():
            raise ValidationError("task frame description is required")
        if not frame.semantic_verb:
            raise ValidationError("task frame semantic_verb is required")
        if not frame.object_type.strip():
            raise ValidationError("task frame object_type is required")
