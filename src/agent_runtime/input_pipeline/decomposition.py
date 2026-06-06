"""Prompt classification and typed task decomposition interfaces."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import model_json_schema

from agent_runtime.capabilities import CapabilityRegistry
from agent_runtime.capabilities.sql import sql_prompt_lines_from_context
from agent_runtime.core.types import ALLOWED_SEMANTIC_VERBS, TaskFrame, UserRequest
from agent_runtime.input_pipeline.plan_selection import CandidateEvaluation, select_best_candidate
from agent_runtime.input_pipeline.validators import (
    DECOMPOSITION_FORBIDDEN_CONSTRAINT_KEYS,
    PlanningContractValidator,
)
from agent_runtime.llm.critique import critique_decomposition, critique_requires_repair
from agent_runtime.llm.proposals import (
    PromptClassificationProposal,
    TaskDecompositionProposal,
    collect_n_best_structured_attempts,
)
from agent_runtime.llm.reproducibility import (
    PlanningTrace,
    PlanningTraceEntry,
    append_trace_entry,
    llm_client_metadata,
)
from agent_runtime.llm.structured_call import structured_call
from agent_runtime.memory import memory_prompt_lines_from_context
from agent_runtime.parameters import parameter_prompt_lines_from_context
from agent_runtime.prompts import prompt_lines


class PromptClassification(BaseModel):
    """Typed prompt classification produced by the first input-pipeline stage."""

    model_config = ConfigDict(extra="forbid")

    prompt_type: Literal[
        "simple_question",
        "simple_tool_task",
        "compound_tool_task",
        "complex_workflow",
        "ambiguous",
        "unsupported",
    ]
    requires_tools: bool
    likely_domains: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high", "critical"]
    needs_clarification: bool
    clarification_question: str | None = None
    reason: str


class DecompositionResult(BaseModel):
    """Typed decomposition output for a user prompt."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskFrame] = Field(default_factory=list)
    global_constraints: dict[str, Any] = Field(default_factory=dict)
    unresolved_references: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_task_dependencies(self) -> "DecompositionResult":
        """Ensure task dependencies point at task ids within the same result."""

        task_ids = {task.id for task in self.tasks}
        for task in self.tasks:
            for dependency in task.dependencies:
                if dependency not in task_ids:
                    raise ValueError(
                        f"Task {task.id} depends on unknown task id {dependency}."
                    )
        return self


_RUNTIME_CONTEXT_WORDS = {
    "active",
    "attached",
    "available",
    "connected",
    "current",
    "existing",
    "here",
    "installed",
    "inserted",
    "local",
    "locally",
    "mounted",
    "my",
    "open",
    "pending",
    "plugged",
    "removable",
    "running",
    "staged",
    "these",
    "this",
    "unstaged",
    "workspace",
}

_RUNTIME_OBSERVABLE_WORDS = {
    "branch",
    "branches",
    "change",
    "changes",
    "conda",
    "container",
    "containers",
    "cpu",
    "cwd",
    "device",
    "devices",
    "directories",
    "directory",
    "disk",
    "docker",
    "drive",
    "drives",
    "env",
    "environment",
    "environments",
    "envs",
    "file",
    "files",
    "folder",
    "folders",
    "git",
    "graphics",
    "gpu",
    "hardware",
    "hostname",
    "image",
    "images",
    "label",
    "labels",
    "make",
    "manufacturer",
    "memory",
    "model",
    "models",
    "mount",
    "mountpoint",
    "mountpoints",
    "partition",
    "partitions",
    "port",
    "ports",
    "process",
    "processes",
    "repo",
    "repository",
    "server",
    "service",
    "services",
    "status",
    "storage",
    "usb",
    "vendor",
    "volume",
    "volumes",
}

_LIVE_OBSERVABLE_WORDS = {
    "availability",
    "available",
    "clock",
    "date",
    "delay",
    "delays",
    "forecast",
    "forecasts",
    "humidity",
    "news",
    "price",
    "prices",
    "rate",
    "rates",
    "score",
    "scores",
    "status",
    "stock",
    "stocks",
    "temperature",
    "temperatures",
    "time",
    "traffic",
    "weather",
}

_LIVE_CONTEXT_WORDS = {
    "current",
    "currently",
    "latest",
    "live",
    "now",
    "present",
    "real-time",
    "realtime",
    "right",
    "today",
    "tonight",
    "tomorrow",
    "yesterday",
}

_LIVE_LOCATIONAL_WORDS = {
    "forecast",
    "forecasts",
    "humidity",
    "temperature",
    "temperatures",
    "traffic",
    "weather",
}

_LIVE_QUERY_WORDS = {
    "check",
    "find",
    "get",
    "how",
    "look",
    "show",
    "tell",
    "what",
    "whats",
}

_STABLE_EXPLANATION_WORDS = {
    "algorithm",
    "algorithms",
    "biology",
    "chemistry",
    "code",
    "complexity",
    "concept",
    "definition",
    "formula",
    "history",
    "learning",
    "limiting",
    "meaning",
    "physics",
    "science",
    "theory",
    "unit",
    "units",
}

_RUNTIME_HARDWARE_WORDS = {
    "card",
    "cards",
    "cpu",
    "cpus",
    "device",
    "devices",
    "gpu",
    "gpus",
    "graphics",
    "hardware",
    "processor",
    "processors",
}

_RUNTIME_HARDWARE_IDENTITY_WORDS = {
    "make",
    "manufacturer",
    "model",
    "name",
    "vendor",
}

_RUNTIME_OBSERVATION_PHRASES = (
    "attached drive",
    "attached usb",
    "branch am i on",
    "branch are we on",
    "branch --show-current",
    "conda env",
    "current branch",
    "current git branch",
    "current working directory",
    "docker images",
    "docker ps",
    "block devices",
    "git branch",
    "git status",
    "lsblk",
    "gpu make",
    "gpu model",
    "make of the cpu",
    "make of the gpu",
    "model of the cpu",
    "model of the gpu",
    "pending changes",
    "processor model",
    "staged changes",
    "unstaged changes",
    "usb drive",
    "what cpu",
    "what gpu",
    "what branch",
    "which branch",
    "working directory",
)

_RUNTIME_OBSERVATION_VERBS = {
    "calculate",
    "check",
    "count",
    "detect",
    "discover",
    "figure",
    "find",
    "identify",
    "inspect",
    "list",
    "search",
    "show",
    "summarize",
}


def _prompt_looks_like_stable_explanation(
    lowered: str,
    words: set[str],
) -> bool:
    if bool(words & _LIVE_CONTEXT_WORDS):
        return False
    if not bool(words & _STABLE_EXPLANATION_WORDS):
        return False
    return bool(re.search(r"^\s*(?:define|describe|explain|what\s+is)\b", lowered))


def _prompt_asks_for_live_observation(lowered: str, words: set[str]) -> bool:
    """Return whether the prompt asks for live observable state.

    Live observable state covers facts that can change while the model is
    answering: local clocks, service status, market data, environmental
    conditions, scores, availability, and similar external measurements. This
    keeps direct answers for stable explanations while routing dynamic facts to
    the tool-capable path.
    """

    if not bool(words & _LIVE_OBSERVABLE_WORDS):
        return False
    if _prompt_looks_like_stable_explanation(lowered, words):
        return False
    if bool(words & _LIVE_CONTEXT_WORDS):
        return True

    first_word = next(iter(re.findall(r"[a-z]+", lowered.strip())), "")
    if first_word in _RUNTIME_OBSERVATION_VERBS and bool(words & _LIVE_OBSERVABLE_WORDS):
        return True

    if re.search(
        r"\b(?:show|tell(?:\s+me)?|get|check|find|look(?:\s+up)?)\s+"
        r"(?:the\s+)?(?:current\s+)?(?:clock|date|time)\b",
        lowered,
    ):
        return True
    if re.search(
        r"\b(?:what(?:'s| is)|whats)\s+(?:the\s+|current\s+)"
        r"(?:clock|date|time)\b",
        lowered,
    ):
        return True
    if re.search(r"\bwhat\s+(?:clock|date|time)\s+is\s+it\b", lowered):
        return True
    if re.search(r"\b(?:clock|date|time)\s+(?:is\s+it|now|today)\b", lowered):
        return True

    has_live_location = bool(words & _LIVE_LOCATIONAL_WORDS) and bool(
        re.search(r"\b(?:around|at|for|in|near)\s+[a-z0-9]", lowered)
    )
    if has_live_location and (
        bool(words & _LIVE_QUERY_WORDS) or first_word in _LIVE_LOCATIONAL_WORDS
    ):
        return True

    report_words = {
        "availability",
        "available",
        "delay",
        "delays",
        "news",
        "price",
        "prices",
        "rate",
        "rates",
        "score",
        "scores",
        "status",
        "stock",
        "stocks",
    }
    if bool(words & report_words) and (
        bool(words & _LIVE_QUERY_WORDS) or first_word in _RUNTIME_OBSERVATION_VERBS
    ):
        return True

    return False


def _prompt_requires_runtime_observation(prompt: str) -> bool:
    """Return whether the prompt asks for observable state.

    This is a classification safety guard, not a domain-specific executor. A
    direct answer is only valid for stable knowledge. If the wording points at
    the current machine, repository, workspace, shell, or other live observable
    state, the trusted classification must route to a tool-capable path even
    when the LLM classifier guesses otherwise.
    """

    lowered = f" {str(prompt or '').strip().lower()} "
    if not lowered.strip():
        return False
    if any(phrase in lowered for phrase in _RUNTIME_OBSERVATION_PHRASES):
        return True

    words = set(re.findall(r"[a-z0-9_./-]+", lowered))
    if _prompt_asks_for_live_observation(lowered, words):
        return True
    if bool(words & _RUNTIME_HARDWARE_WORDS) and bool(
        words & _RUNTIME_HARDWARE_IDENTITY_WORDS
    ):
        return True
    has_context = bool(words & _RUNTIME_CONTEXT_WORDS)
    has_observable = bool(words & _RUNTIME_OBSERVABLE_WORDS)
    if has_context and has_observable:
        return True

    first_word = next(iter(re.findall(r"[a-z]+", lowered.strip())), "")
    return first_word in _RUNTIME_OBSERVATION_VERBS and has_observable


class Decomposer:
    """Minimal decomposer that keeps a prompt as one task."""

    def decompose(self, request: UserRequest) -> DecompositionResult:
        """Return the request prompt as a single undecomposed task."""

        return DecompositionResult(
            tasks=[
                TaskFrame(
                    description=request.raw_prompt,
                    semantic_verb="unknown",
                    object_type="unknown",
                    intent_confidence=0.0,
                    constraints={},
                    raw_evidence=request.raw_prompt,
                )
            ]
        )


def _classification_schema_description() -> str:
    """Return a short human-readable summary of the classification schema."""

    return (
        "PromptClassification fields: "
        "prompt_type, requires_tools, likely_domains, risk_level, "
        "needs_clarification, clarification_question, reason. "
        "PromptClassificationProposal may also include domain_evaluations, "
        "where each declared domain is evaluated with fits=true|false and confidence."
    )


def _decomposition_schema_description() -> str:
    """Return a short human-readable summary of the decomposition schema."""

    return (
        "DecompositionResult fields: tasks, global_constraints, "
        "unresolved_references, assumptions. "
        "Each task is a TaskFrame with id, description, semantic_verb, "
        "object_type, intent_confidence, constraints, dependencies, "
        "raw_evidence, requires_confirmation, risk_level, operation_intent, "
        "side_effect_type, and dependency_hints."
    )


def _domain_manifest(registry: CapabilityRegistry | None) -> list[dict[str, Any]]:
    """Return a compact declared-domain manifest for classification."""

    if registry is None:
        return []
    grouped: dict[str, dict[str, Any]] = {}
    for manifest in registry.list_manifests():
        domain = str(manifest.domain or "").strip()
        if not domain:
            continue
        bucket = grouped.setdefault(
            domain,
            {
                "domain": domain,
                "capabilities": [],
                "semantic_verbs": set(),
                "object_types": set(),
            },
        )
        bucket["capabilities"].append(
            {
                "capability_id": manifest.capability_id,
                "operation_id": manifest.operation_id,
                "description": manifest.description,
                "semantic_verbs": list(manifest.semantic_verbs),
                "object_types": list(manifest.object_types),
                "examples": list(manifest.examples),
                "read_only": manifest.read_only,
                "risk_level": manifest.risk_level,
            }
        )
        bucket["semantic_verbs"].update(str(item) for item in manifest.semantic_verbs)
        bucket["object_types"].update(str(item) for item in manifest.object_types)
    exported: list[dict[str, Any]] = []
    for domain in sorted(grouped):
        item = grouped[domain]
        exported.append(
            {
                "domain": item["domain"],
                "semantic_verbs": sorted(item["semantic_verbs"]),
                "object_types": sorted(item["object_types"]),
                "capabilities": sorted(item["capabilities"], key=lambda value: value["capability_id"]),
            }
        )
    return exported


def _allowed_domains(registry: CapabilityRegistry | None) -> list[str]:
    """Return declared runtime domains in stable order."""

    return [item["domain"] for item in _domain_manifest(registry)]


def _classification_schema(allowed_domains: list[str]) -> dict[str, Any]:
    """Return the dynamic classifier schema with declared domain enums."""

    schema = PromptClassificationProposal.model_json_schema()
    if not allowed_domains:
        return schema
    properties = schema.setdefault("properties", {})
    likely_domains = properties.setdefault("likely_domains", {})
    likely_domains.setdefault("items", {})["enum"] = list(allowed_domains)
    defs = schema.setdefault("$defs", {})
    domain_def = defs.setdefault("DomainEvaluationProposal", {})
    domain_properties = domain_def.setdefault("properties", {})
    domain_properties.setdefault("domain", {})["enum"] = list(allowed_domains)
    return schema


def _build_classification_prompt(
    user_request: UserRequest,
    registry: CapabilityRegistry | None = None,
) -> str:
    """Build the strict JSON-only prompt sent to the LLM classifier."""

    domains = _domain_manifest(registry)
    domain_text = (
        "No tool domains are available."
        if not domains
        else str(domains)
    )
    return "\n".join(
        [
            *prompt_lines("input.classification"),
            *memory_prompt_lines_from_context(user_request, stage="classification"),
            *parameter_prompt_lines_from_context(user_request, stage="classification"),
            *sql_prompt_lines_from_context(user_request, stage="classification"),
            "For tool-backed prompts, set requires_tools=true and select likely_domains only "
            "from the declared domains below.",
            "Do not invent domains. Do not use generic domains that are not declared.",
            "Evaluate declared domains with domain_evaluations: domain, fits, confidence, reason.",
            _classification_schema_description(),
            "Declared runtime domains and capabilities:",
            domain_text,
            "JSON schema:",
            str(_classification_schema(_allowed_domains(registry))),
            "User prompt:",
            user_request.raw_prompt,
        ]
    )


def _validate_classification_proposal(
    proposal: PromptClassificationProposal,
    allowed_domains: list[str] | None = None,
    user_prompt: str | None = None,
) -> tuple[PromptClassification, list[str]]:
    """Convert one untrusted classification proposal into the trusted model."""

    allowed = list(allowed_domains or [])
    allowed_set = {domain.strip().lower(): domain for domain in allowed}
    normalizations: list[str] = []

    selected_domains: list[str] = []
    if proposal.domain_evaluations:
        for evaluation in proposal.domain_evaluations:
            normalized_domain = str(evaluation.domain or "").strip().lower()
            if normalized_domain not in allowed_set:
                normalizations.append(f"rejected_unknown_domain={evaluation.domain}")
                continue
            if evaluation.fits:
                selected_domains.append(allowed_set[normalized_domain])
    else:
        for domain in proposal.likely_domains:
            normalized_domain = str(domain or "").strip().lower()
            if allowed and normalized_domain not in allowed_set:
                normalizations.append(f"rejected_unknown_domain={domain}")
                continue
            selected_domains.append(allowed_set.get(normalized_domain, domain))

    deduped_domains = list(dict.fromkeys(selected_domains))
    prompt_type = proposal.prompt_type
    requires_tools = bool(proposal.requires_tools)
    if deduped_domains and not requires_tools:
        requires_tools = True
        normalizations.append("requires_tools_from_domain_evaluations")
    if not requires_tools and _prompt_requires_runtime_observation(user_prompt or ""):
        requires_tools = True
        if "operator" in allowed_set:
            deduped_domains = ["operator"]
        normalizations.append("requires_tools_from_runtime_observation")
    if requires_tools and prompt_type == "simple_question":
        prompt_type = "simple_tool_task"
        normalizations.append("prompt_type_from_tool_requirement")
    if not requires_tools:
        deduped_domains = []

    normalized = PromptClassification.model_validate(
        {
            "prompt_type": prompt_type,
            "requires_tools": requires_tools,
            "likely_domains": deduped_domains,
            "risk_level": proposal.risk_level,
            "needs_clarification": proposal.needs_clarification,
            "clarification_question": proposal.clarification_question,
            "reason": proposal.reason,
        }
    )
    if proposal.confidence is not None:
        normalizations.insert(0, f"proposal_confidence={proposal.confidence}")
    return normalized, normalizations


def classify_prompt(
    user_request: UserRequest,
    llm_client,
    registry: CapabilityRegistry | None = None,
) -> PromptClassification:
    """Classify a user prompt through a strict structured LLM call."""

    prompt = _build_classification_prompt(user_request, registry)
    allowed_domains = _allowed_domains(registry)
    proposal = structured_call(
        llm_client,
        prompt,
        PromptClassificationProposal,
        schema=_classification_schema(allowed_domains),
    )
    classification, normalizations = _validate_classification_proposal(
        proposal,
        allowed_domains,
        user_prompt=user_request.raw_prompt,
    )
    trace = user_request.safety_context.get("planning_trace")
    if isinstance(trace, PlanningTrace):
        model_name, temperature = llm_client_metadata(llm_client)
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="prompt_classification",
                request_id=user_request.request_id,
                model_name=model_name,
                llm_temperature=temperature,
                prompt_template_id="input.classification",
                raw_llm_response=proposal.model_dump(mode="json"),
                parsed_proposal=proposal.model_dump(mode="json"),
                selected_candidate=classification.model_dump(mode="json"),
                deterministic_normalizations=normalizations,
            ),
        )
    return classification


def _semantic_planning_manifest(registry: CapabilityRegistry | None) -> dict[str, Any]:
    """Return declarative planning vocabulary for the LLM decomposer."""

    domains: dict[str, dict[str, Any]] = {}
    if registry is not None:
        for manifest in registry.list_manifests():
            bucket = domains.setdefault(
                manifest.domain,
                {
                    "domain": manifest.domain,
                    "semantic_verbs": set(),
                    "object_types": set(),
                    "side_effect_types": set(),
                },
            )
            bucket["semantic_verbs"].update(str(item) for item in manifest.semantic_verbs)
            bucket["object_types"].update(str(item) for item in manifest.object_types)
            if manifest.side_effect_type:
                bucket["side_effect_types"].add(str(manifest.side_effect_type))
    domain_vocabulary = []
    for domain in sorted(domains):
        bucket = domains[domain]
        domain_vocabulary.append(
            {
                "domain": bucket["domain"],
                "semantic_verbs": sorted(bucket["semantic_verbs"]),
                "object_types": sorted(bucket["object_types"]),
                "side_effect_types": sorted(bucket["side_effect_types"]),
            }
        )
    return {
        "allowed_semantic_verbs": list(ALLOWED_SEMANTIC_VERBS),
        "domain_vocabulary": domain_vocabulary,
    }


def _build_decomposition_prompt(
    user_request: UserRequest,
    classification: PromptClassification,
    critique_feedback: dict[str, Any] | None = None,
    registry: CapabilityRegistry | None = None,
) -> str:
    """Build the strict JSON-only prompt sent to the LLM decomposer."""

    schema = model_json_schema(DecompositionResult)
    return "\n".join(
        [
            *prompt_lines("input.decomposition"),
            *memory_prompt_lines_from_context(user_request, stage="decomposition"),
            *parameter_prompt_lines_from_context(user_request, stage="decomposition"),
            *sql_prompt_lines_from_context(user_request, stage="decomposition"),
            _decomposition_schema_description(),
            "Prompt classification context:",
            str(classification.model_dump()),
            "Declared semantic planning vocabulary:",
            str(_semantic_planning_manifest(registry)),
            "For troubleshooting, recovery, or fix requests, do not invent destructive storage reset tasks. Formatting, wiping, erasing, repartitioning, or creating a new filesystem is valid only when the user explicitly requested that data-destructive reset; otherwise decompose into inspect, diagnose, repair, or ask-user tasks.",
            *(
                [
                    "Critique feedback from a previous proposal attempt:",
                    str(critique_feedback),
                ]
                if critique_feedback
                else []
            ),
            "JSON schema:",
            str(schema),
            "User prompt:",
            user_request.raw_prompt,
        ]
    )


def _normalize_decomposition_proposal(
    proposal: TaskDecompositionProposal,
) -> DecompositionResult:
    """Convert one untrusted decomposition proposal into the trusted model."""

    tasks = [TaskFrame.model_validate(task.model_dump(mode="json")) for task in proposal.tasks]
    return DecompositionResult(
        tasks=tasks,
        global_constraints=dict(proposal.global_constraints),
        unresolved_references=list(proposal.unresolved_references),
        assumptions=list(proposal.assumptions),
    )


def _normalized_user_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip(" `\"'")


def _constraint_value_is_user_explicit(value: Any, original_prompt: str) -> bool:
    if not isinstance(value, str):
        return False
    normalized_value = _normalized_user_text(value)
    if len(normalized_value) < 3:
        return False
    return normalized_value in _normalized_user_text(original_prompt)


def _strip_user_explicit_downstream_constraints(
    constraints: dict[str, Any],
    *,
    original_prompt: str,
) -> tuple[dict[str, Any], list[str]]:
    """Remove accidental executable constraint keys when their value is user text.

    The decomposition stage should not carry executable fields such as
    ``command`` forward. When the model copied a command phrase from the prompt
    into such a field, dropping that field is safer than rejecting an otherwise
    valid semantic task. Non-user-explicit executable payloads remain intact so
    the planning contract validator can still reject them.
    """

    removed: list[str] = []

    def sanitize(value: Any, prefix: str = "") -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for raw_key, nested in value.items():
                key = str(raw_key)
                path = f"{prefix}.{key}" if prefix else key
                if (
                    key.casefold() in DECOMPOSITION_FORBIDDEN_CONSTRAINT_KEYS
                    and _constraint_value_is_user_explicit(nested, original_prompt)
                ):
                    removed.append(path)
                    continue
                sanitized[key] = sanitize(nested, path)
            return sanitized
        if isinstance(value, list):
            return [sanitize(item, f"{prefix}[]") for item in value]
        return value

    return sanitize(dict(constraints or {})), removed


def _sanitize_decomposition_for_stage_boundary(
    decomposition: DecompositionResult,
    *,
    original_prompt: str,
) -> tuple[DecompositionResult, list[str]]:
    normalizations: list[str] = []
    tasks: list[TaskFrame] = []
    for task in decomposition.tasks:
        sanitized_constraints, removed_paths = _strip_user_explicit_downstream_constraints(
            dict(task.constraints or {}),
            original_prompt=original_prompt,
        )
        if removed_paths:
            normalizations.extend(
                f"removed_user_explicit_downstream_constraint:{task.id}:{path}"
                for path in removed_paths
            )
            task = task.model_copy(update={"constraints": sanitized_constraints})
        tasks.append(task)
    if not normalizations:
        return decomposition, []
    return decomposition.model_copy(update={"tasks": tasks}), normalizations


_RISK_SCORES: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


def _evaluate_decomposition_attempts(
    attempts,
    *,
    original_prompt: str = "",
) -> list[CandidateEvaluation[DecompositionResult]]:
    """Evaluate N-best decomposition attempts deterministically."""

    evaluations: list[CandidateEvaluation[DecompositionResult]] = []
    for attempt in attempts:
        evaluation = CandidateEvaluation[DecompositionResult](
            raw_response=attempt.raw_llm_response,
            validation_errors=list(attempt.validation_errors),
        )
        if attempt.parsed_proposal is None or attempt.validation_errors:
            evaluations.append(evaluation)
            continue
        proposal = attempt.parsed_proposal
        try:
            normalized = _normalize_decomposition_proposal(proposal)
            normalized, deterministic_normalizations = _sanitize_decomposition_for_stage_boundary(
                normalized,
                original_prompt=original_prompt,
            )
            contract_result = PlanningContractValidator().validate_tasks(
                normalized.tasks,
                original_prompt=original_prompt,
            )
            if not contract_result.accepted:
                evaluation.validation_errors.extend(
                    issue.message for issue in contract_result.issues
                )
            evaluation.proposal = normalized
            evaluation.confidence = float(proposal.confidence or 0.0)
            evaluation.deterministic_normalizations.extend(deterministic_normalizations)
            evaluation.assumption_count = len(proposal.assumptions)
            evaluation.unresolved_count = len(proposal.unresolved_references)
            evaluation.risk_score = sum(
                _RISK_SCORES.get(task.risk_level.strip().lower(), 0) for task in proposal.tasks
            )
        except Exception as exc:
            evaluation.validation_errors.append(str(exc))
        evaluations.append(evaluation)
    return evaluations


def decompose_prompt(
    user_request: UserRequest,
    classification: PromptClassification,
    llm_client,
    available_domains: list[str] | None = None,
    registry: CapabilityRegistry | None = None,
    n_best: int = 2,
) -> DecompositionResult:
    """Decompose a prompt into ordered atomic tasks through a structured LLM call.

    The normal path asks once. ``n_best`` is retained as a compatibility knob
    for one corrective retry when the first proposal is malformed or rejected,
    rather than spending multiple near-identical LLM calls on every request.
    """

    prompt = _build_decomposition_prompt(user_request, classification, registry=registry)
    max_attempts = max(1, int(n_best))
    attempts = collect_n_best_structured_attempts(
        llm_client=llm_client,
        system_prompt=prompt,
        user_payload={
            "prompt": user_request.raw_prompt,
            "classification": classification.model_dump(mode="json"),
        },
        output_model=TaskDecompositionProposal,
        n=1,
    )
    evaluations = _evaluate_decomposition_attempts(
        attempts,
        original_prompt=user_request.raw_prompt,
    )
    selected = select_best_candidate(evaluations)
    if (
        (selected is None or selected.proposal is None or not selected.is_valid)
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
                "Previous decomposition attempt did not pass deterministic validation.",
                f"Validation feedback: {'; '.join(feedback[:5]) if feedback else 'No valid task proposal was accepted.'}",
                "Remove any command/code/SQL/operator argument payloads from constraints. Keep only semantic task meaning and dependencies.",
                "Remove any destructive storage reset task unless the original prompt explicitly requested formatting, wiping, erasing, repartitioning, or creating a new filesystem.",
                "Return one corrected decomposition using the same schema.",
            ]
        )
        retry_attempts = collect_n_best_structured_attempts(
            llm_client=llm_client,
            system_prompt=retry_prompt,
            user_payload={
                "prompt": user_request.raw_prompt,
                "classification": classification.model_dump(mode="json"),
            },
            output_model=TaskDecompositionProposal,
            n=1,
        )
        attempts.extend(retry_attempts)
        evaluations.extend(
            _evaluate_decomposition_attempts(
                retry_attempts,
                original_prompt=user_request.raw_prompt,
            )
        )
        selected = select_best_candidate(evaluations)
    if selected is None or selected.proposal is None or not selected.is_valid:
        errors = [
            error
            for evaluation in evaluations
            for error in (evaluation.validation_errors + evaluation.rejection_reasons)
        ]
        raise ValueError(
            "No valid decomposition proposal was accepted."
            + (f" Errors: {' | '.join(errors)}" if errors else "")
        )

    available_domains = available_domains or []
    selected_proposal = TaskDecompositionProposal.model_validate(
        attempts[evaluations.index(selected)].parsed_proposal.model_dump(mode="json")
        if attempts[evaluations.index(selected)].parsed_proposal is not None
        else {}
    )
    critique = critique_decomposition(user_request, selected_proposal, available_domains, llm_client)
    repaired = False
    if critique_requires_repair(critique):
        repair_prompt = _build_decomposition_prompt(
            user_request,
            classification,
            critique_feedback=critique.model_dump(mode="json"),
            registry=registry,
        )
        repair_attempts = collect_n_best_structured_attempts(
            llm_client=llm_client,
            system_prompt=repair_prompt,
            user_payload={
                "prompt": user_request.raw_prompt,
                "classification": classification.model_dump(mode="json"),
                "critique": critique.model_dump(mode="json"),
            },
            output_model=TaskDecompositionProposal,
            n=1,
        )
        repair_evaluations = _evaluate_decomposition_attempts(
            repair_attempts,
            original_prompt=user_request.raw_prompt,
        )
        repaired_candidate = select_best_candidate(repair_evaluations)
        if repaired_candidate is not None and repaired_candidate.is_valid and repaired_candidate.proposal is not None:
            selected = repaired_candidate
            repaired = True

    trace = user_request.safety_context.get("planning_trace")
    if isinstance(trace, PlanningTrace):
        model_name, temperature = llm_client_metadata(llm_client)
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="task_decomposition",
                request_id=user_request.request_id,
                model_name=model_name,
                llm_temperature=temperature,
                prompt_template_id="input.decomposition",
                raw_llm_response=[attempt.raw_llm_response for attempt in attempts],
                parsed_proposal=[
                    attempt.parsed_proposal.model_dump(mode="json")
                    if attempt.parsed_proposal is not None
                    else None
                    for attempt in attempts
                ],
                validation_errors=[
                    error for attempt in attempts for error in attempt.validation_errors
                ],
                selected_candidate=selected.proposal.model_dump(mode="json"),
                rejection_reasons=[
                    reason
                    for evaluation in evaluations
                    if evaluation is not selected
                    for reason in (evaluation.rejection_reasons + evaluation.validation_errors)
                ],
                deterministic_normalizations=(
                    [
                        *(["repaired_after_critique"] if repaired else []),
                        *list(selected.deterministic_normalizations),
                    ]
                ),
            ),
        )
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="task_decomposition_critique",
                request_id=user_request.request_id,
                model_name=model_name,
                llm_temperature=temperature,
                prompt_template_id="task_decomposition_critique",
                raw_llm_response=critique.model_dump(mode="json"),
                parsed_proposal=critique.model_dump(mode="json"),
                selected_candidate=selected.proposal.model_dump(mode="json"),
            ),
        )
    return selected.proposal
