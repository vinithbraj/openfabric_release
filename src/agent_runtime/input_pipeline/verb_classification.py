"""Semantic verb assignment for decomposed task frames."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import model_json_schema

from agent_runtime.capabilities.registry import CapabilityRegistry
from agent_runtime.core.semantic_compatibility import (
    canonical_domain,
    canonical_object_family,
    canonical_semantic_verb,
    match_object_family_from_text,
    object_domain,
)
from agent_runtime.core.types import ALLOWED_SEMANTIC_VERBS, RiskLevel, SemanticVerb, TaskFrame
from agent_runtime.llm.proposals import VerbAssignmentProposal
from agent_runtime.llm.reproducibility import (
    PlanningTrace,
    PlanningTraceEntry,
    append_trace_entry,
    llm_client_metadata,
)
from agent_runtime.llm.structured_call import structured_call
from agent_runtime.prompts import prompt_lines

class TaskVerbAssignment(BaseModel):
    """Typed LLM response for one task-frame verb assignment."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    semantic_verb: SemanticVerb
    object_type: str
    intent_confidence: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    requires_confirmation: bool = False


class TaskVerbAssignmentResult(BaseModel):
    """Collection of verb assignments returned by the LLM."""

    model_config = ConfigDict(extra="forbid")

    assignments: list[TaskVerbAssignment] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_task_ids(self) -> "TaskVerbAssignmentResult":
        """Ensure each task id appears at most once in the response."""

        task_ids = [assignment.task_id for assignment in self.assignments]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Task verb assignments must reference unique task ids.")
        return self


class VerbClassifier:
    """Small deterministic fallback used by the placeholder orchestrator."""

    def classify(self, prompt: str) -> str:
        """Return a conservative verb guess without calling an LLM."""

        lowered = prompt.strip().lower()
        if lowered.startswith("list"):
            return "read"
        if lowered.startswith("count"):
            return "count"
        if lowered.startswith("search"):
            return "search"
        if lowered.startswith("delete") or lowered.startswith("remove"):
            return "delete"
        if lowered.startswith("run"):
            return "execute"
        if lowered.startswith("summarize"):
            return "summarize"
        return "read"


_DIRECT_VERB_NORMALIZATIONS = {
    "append": "update",
    "check": "read",
    "confirm": "read",
    "copy": "create",
    "cp": "create",
    "deploy": "execute",
    "discover": "search",
    "find": "search",
    "install": "execute",
    "invoke": "execute",
    "launch": "execute",
    "locate": "search",
    "mkdir": "create",
    "move": "update",
    "mv": "update",
    "open": "read",
    "probe": "read",
    "publish": "execute",
    "pull": "execute",
    "push": "execute",
    "remove": "delete",
    "restart": "execute",
    "rm": "delete",
    "run": "execute",
    "save": "create",
    "stage": "execute",
    "start": "execute",
    "status": "read",
    "stop": "execute",
    "verify": "read",
    "write": "create",
}

_DOCKER_COMPOSE_COMMAND_RE = re.compile(r"\b(?:docker\s+)?compose\s+(?:up|down|start|stop|restart|rm|pull|push|build)\b")
_EXECUTION_HINT_RE = re.compile(
    r"\b(?:apply|build|call|deploy|execute|install|invoke|launch|publish|pull|push|restart|run|stage|start|stop)\b"
)
_DELETE_HINT_RE = re.compile(r"\b(?:delete|remove|rm|rmdir|unlink)\b")
_CREATE_HINT_RE = re.compile(r"\b(?:append|copy|create|mkdir|save|touch|write)\b")
_SEARCH_HINT_RE = re.compile(r"\b(?:discover|find|locate|search)\b")
_READ_HINT_RE = re.compile(r"\b(?:check|confirm|inspect|list|read|show|status|verify)\b")


def _task_context_text(task: TaskFrame | None) -> str:
    """Return compact text used for deterministic verb fallback inference."""

    if task is None:
        return ""
    return " ".join(
        str(part)
        for part in (
            task.description,
            task.raw_evidence or "",
            task.object_type,
            task.operation_intent or "",
            task.side_effect_type or "",
            task.constraints,
        )
        if str(part).strip()
    ).lower()


def _normalize_assigned_semantic_verb(raw_verb: str, task: TaskFrame | None) -> str:
    """Normalize one LLM-authored verb before strict enum validation."""

    token = str(raw_verb or "").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = set(ALLOWED_SEMANTIC_VERBS)
    if token in allowed:
        return token

    canonical = canonical_semantic_verb(token)
    if canonical in allowed and canonical != "unknown":
        return canonical

    context = _task_context_text(task)
    if token == "compose":
        if "docker" in context or _DOCKER_COMPOSE_COMMAND_RE.search(context):
            return "execute"
        return "create"
    if token in _DIRECT_VERB_NORMALIZATIONS:
        return _DIRECT_VERB_NORMALIZATIONS[token]
    if _DOCKER_COMPOSE_COMMAND_RE.search(context) or _EXECUTION_HINT_RE.search(context):
        return "execute"
    if _DELETE_HINT_RE.search(context):
        return "delete"
    if _CREATE_HINT_RE.search(context):
        return "create"
    if _SEARCH_HINT_RE.search(context):
        return "search"
    if _READ_HINT_RE.search(context):
        return "read"
    return "unknown"


def _known_object_type_vocabulary(
    registry: CapabilityRegistry,
    likely_domains: list[str] | None = None,
) -> list[str]:
    """Return a runtime-owned object-type vocabulary derived from registered manifests."""

    known_types = {"unknown"}
    for manifest in registry.list_manifests():
        for object_type in [*manifest.object_types, *manifest.output_object_types]:
            canonical = canonical_object_family(object_type)
            if canonical is not None:
                known_types.add(canonical)

    normalized_likely_domains = {
        canonical_domain(domain)
        for domain in (likely_domains or [])
        if canonical_domain(domain) is not None
    }
    if not normalized_likely_domains:
        return sorted(known_types)

    prioritized = [
        object_type
        for object_type in sorted(known_types)
        if object_type == "unknown" or object_domain(object_type) in normalized_likely_domains
    ]
    remainder = [object_type for object_type in sorted(known_types) if object_type not in prioritized]
    return prioritized + remainder


def _normalize_assigned_object_type(
    raw_object_type: str,
    task: TaskFrame,
    allowed_object_types: list[str],
    semantic_verb: str,
) -> str:
    """Normalize one assigned object type into the runtime-owned vocabulary."""

    allowed = set(allowed_object_types)
    combined_text = " ".join(
        part
        for part in (
            raw_object_type,
            task.description,
            task.raw_evidence or "",
        )
        if str(part).strip()
    )
    lowered_combined = combined_text.lower()
    if "filesystem.path" in allowed and (
        "full path" in lowered_combined
        or "absolute path" in lowered_combined
        or "file path" in lowered_combined
        or "saved path" in lowered_combined
        or "where it was saved" in lowered_combined
    ):
        return "filesystem.path"
    if (
        semantic_verb in {"create", "update"}
        and "filesystem.file" in allowed
        and (
            " file" in f" {lowered_combined}"
            or ".txt" in lowered_combined
            or ".md" in lowered_combined
            or ".json" in lowered_combined
            or "save" in lowered_combined
            or "write" in lowered_combined
        )
    ):
        return "filesystem.file"
    inferred = match_object_family_from_text(
        combined_text,
        allowed_object_types,
    )
    canonical = canonical_object_family(raw_object_type)
    if canonical in allowed:
        if (
            inferred in allowed
            and inferred is not None
            and object_domain(inferred) == object_domain(canonical)
            and "." not in canonical
            and "." in inferred
        ):
            return inferred
        return canonical
    if inferred in allowed:
        return inferred
    return "unknown"


def _build_assignment_prompt(
    tasks: list[TaskFrame],
    registry: CapabilityRegistry,
    likely_domains: list[str] | None = None,
) -> str:
    """Build the strict JSON-only prompt for semantic verb assignment."""

    schema = model_json_schema(TaskVerbAssignmentResult)
    object_type_vocabulary = _known_object_type_vocabulary(registry, likely_domains)
    task_lines = [
        f"- task_id={task.id}; description={task.description}; current_constraints={task.constraints}"
        for task in tasks
    ]
    return "\n".join(
        [
            *prompt_lines(
                "input.verb_assignment",
                {
                    "semantic_verbs": ", ".join(ALLOWED_SEMANTIC_VERBS),
                    "object_types": ", ".join(object_type_vocabulary),
                    "likely_domains": ", ".join(likely_domains or []) or "unknown",
                },
            ),
            "Task identity rules:",
            "Return exactly one assignment for every task_id in the Task list.",
            "Copy each task_id exactly as written and do not reuse a task_id.",
            "Task list:",
            *task_lines,
            "JSON schema:",
            str(schema),
        ]
    )


def _post_process_assignment(task: TaskFrame) -> TaskFrame:
    """Apply deterministic risk and confirmation rules after LLM assignment."""

    dry_run = bool(task.constraints.get("dry_run"))
    elevated_risk = bool(task.constraints.get("elevated_risk"))
    low_risk_verbs = {
        "read",
        "search",
        "analyze",
        "calculate",
        "count",
        "filter",
        "sort",
        "summarize",
        "render",
    }
    if task.semantic_verb == "delete" and not dry_run:
        task.requires_confirmation = True
    if task.semantic_verb in {"create", "update"} and not dry_run:
        task.requires_confirmation = True
    if task.semantic_verb in low_risk_verbs and not elevated_risk:
        task.risk_level = "low"
        task.requires_confirmation = False
    if task.semantic_verb == "execute" and task.risk_level == "low":
        task.risk_level = "medium"
    if task.semantic_verb == "execute" and bool(task.constraints.get("read_only_capability")):
        task.risk_level = "low"
    return task


def _fallback_assignment_payload(task: TaskFrame) -> dict[str, object]:
    """Build a conservative assignment from the already-validated task frame."""

    semantic_verb = _normalize_assigned_semantic_verb(task.semantic_verb, task)
    if semantic_verb not in set(ALLOWED_SEMANTIC_VERBS):
        semantic_verb = "unknown"
    return {
        "task_id": task.id,
        "semantic_verb": semantic_verb,
        "object_type": task.object_type or "unknown",
        "intent_confidence": max(0.0, min(1.0, float(task.intent_confidence or 0.5))),
        "risk_level": task.risk_level,
        "requires_confirmation": bool(task.requires_confirmation),
    }


def _aligned_assignment_payloads(
    proposal_assignments: list[object],
    tasks: list[TaskFrame],
    *,
    deterministic_normalizations: list[str],
) -> list[dict[str, object]]:
    """Align untrusted LLM assignments to the known task list.

    The task frames are the source of truth for task identity.  Smaller models
    sometimes copy the first task id for every row even when the row order and
    content are otherwise useful.  Repair that by using exact unique matches
    when available and falling back to positional alignment for missing ids.
    """

    exact_matches: dict[str, object] = {}
    duplicate_task_ids: set[str] = set()
    for assignment in proposal_assignments:
        task_id = str(getattr(assignment, "task_id", "") or "")
        if not task_id:
            continue
        if task_id in exact_matches:
            duplicate_task_ids.add(task_id)
            continue
        exact_matches[task_id] = assignment

    aligned: list[dict[str, object]] = []
    for index, task in enumerate(tasks):
        assignment = exact_matches.get(task.id)
        if assignment is None and index < len(proposal_assignments):
            assignment = proposal_assignments[index]
        if assignment is None:
            deterministic_normalizations.append(task.id)
            aligned.append(_fallback_assignment_payload(task))
            continue

        proposed_task_id = str(getattr(assignment, "task_id", "") or "")
        if proposed_task_id != task.id or proposed_task_id in duplicate_task_ids:
            deterministic_normalizations.append(task.id)

        normalized_semantic_verb = _normalize_assigned_semantic_verb(
            str(getattr(assignment, "semantic_verb", "") or ""),
            task,
        )
        if normalized_semantic_verb != str(getattr(assignment, "semantic_verb", "") or "").strip().lower():
            deterministic_normalizations.append(task.id)
        risk_level = str(getattr(assignment, "risk_level", "") or task.risk_level).strip().lower()
        if risk_level not in {"low", "medium", "high", "critical"}:
            risk_level = task.risk_level
            deterministic_normalizations.append(task.id)
        try:
            confidence = float(getattr(assignment, "intent_confidence", task.intent_confidence))
        except (TypeError, ValueError):
            confidence = float(task.intent_confidence or 0.5)
            deterministic_normalizations.append(task.id)
        aligned.append(
            {
                "task_id": task.id,
                "semantic_verb": normalized_semantic_verb,
                "object_type": str(getattr(assignment, "object_type", "") or task.object_type or "unknown"),
                "intent_confidence": max(0.0, min(1.0, confidence)),
                "risk_level": risk_level,
                "requires_confirmation": bool(getattr(assignment, "requires_confirmation", task.requires_confirmation)),
            }
        )
    return aligned


def assign_semantic_verbs(
    tasks: list[TaskFrame],
    llm_client,
    registry: CapabilityRegistry,
    likely_domains: list[str] | None = None,
    trace: PlanningTrace | None = None,
) -> list[TaskFrame]:
    """Assign semantic verbs and semantic metadata to task frames through an LLM."""

    if not tasks:
        return []

    allowed_object_types = _known_object_type_vocabulary(registry, likely_domains)
    prompt = _build_assignment_prompt(tasks, registry, likely_domains)
    proposal = structured_call(llm_client, prompt, VerbAssignmentProposal)
    deterministic_normalizations: list[str] = []
    assignment_payloads = _aligned_assignment_payloads(
        list(proposal.assignments or []),
        tasks,
        deterministic_normalizations=deterministic_normalizations,
    )
    assignment_result = TaskVerbAssignmentResult.model_validate(
        {"assignments": assignment_payloads}
    )
    assignments = {assignment.task_id: assignment for assignment in assignment_result.assignments}

    updated_tasks: list[TaskFrame] = []
    for task in tasks:
        assignment = assignments.get(task.id)
        if assignment is None:
            raise ValueError(f"Missing semantic verb assignment for task {task.id}.")
        normalized_object_type = _normalize_assigned_object_type(
            assignment.object_type,
            task,
            allowed_object_types,
            assignment.semantic_verb,
        )
        updated_task = task.model_copy(
            update={
                "semantic_verb": assignment.semantic_verb,
                "object_type": normalized_object_type,
                "intent_confidence": assignment.intent_confidence,
                "risk_level": assignment.risk_level,
                "requires_confirmation": assignment.requires_confirmation,
            }
        )
        processed_task = _post_process_assignment(updated_task)
        if processed_task != updated_task or normalized_object_type != assignment.object_type:
            if task.id not in deterministic_normalizations:
                deterministic_normalizations.append(task.id)
        updated_tasks.append(processed_task)
    if isinstance(trace, PlanningTrace):
        model_name, temperature = llm_client_metadata(llm_client)
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="semantic_verb_assignment",
                request_id=str(trace.request_id),
                model_name=model_name,
                llm_temperature=temperature,
                prompt_template_id="input.verb_assignment",
                raw_llm_response=proposal.model_dump(mode="json"),
                parsed_proposal=proposal.model_dump(mode="json"),
                selected_candidate=assignment_result.model_dump(mode="json"),
                deterministic_normalizations=deterministic_normalizations,
            ),
        )
    return updated_tasks


def normalize_semantic_verbs_deterministic(
    tasks: list[TaskFrame],
    registry: CapabilityRegistry,
    likely_domains: list[str] | None = None,
    trace: PlanningTrace | None = None,
) -> list[TaskFrame]:
    """Normalize already-proposed task verb metadata without an LLM call."""

    if not tasks:
        return []

    allowed_object_types = _known_object_type_vocabulary(registry, likely_domains)
    deterministic_normalizations = ["semantic_verb_llm_skipped:compact_profile"]
    assignment_payloads = [_fallback_assignment_payload(task) for task in tasks]
    assignment_result = TaskVerbAssignmentResult.model_validate(
        {"assignments": assignment_payloads}
    )
    assignments = {assignment.task_id: assignment for assignment in assignment_result.assignments}

    updated_tasks: list[TaskFrame] = []
    for task in tasks:
        assignment = assignments.get(task.id)
        if assignment is None:
            raise ValueError(f"Missing semantic verb assignment for task {task.id}.")
        normalized_object_type = _normalize_assigned_object_type(
            assignment.object_type,
            task,
            allowed_object_types,
            assignment.semantic_verb,
        )
        updated_task = task.model_copy(
            update={
                "semantic_verb": assignment.semantic_verb,
                "object_type": normalized_object_type,
                "intent_confidence": assignment.intent_confidence,
                "risk_level": assignment.risk_level,
                "requires_confirmation": assignment.requires_confirmation,
            }
        )
        processed_task = _post_process_assignment(updated_task)
        if processed_task != updated_task or normalized_object_type != assignment.object_type:
            deterministic_normalizations.append(task.id)
        updated_tasks.append(processed_task)

    if isinstance(trace, PlanningTrace):
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="semantic_verb_assignment",
                request_id=str(trace.request_id),
                prompt_template_id="input.verb_assignment",
                raw_llm_response=None,
                parsed_proposal=None,
                selected_candidate=assignment_result.model_dump(mode="json"),
                deterministic_normalizations=deterministic_normalizations,
            ),
        )
    return updated_tasks
