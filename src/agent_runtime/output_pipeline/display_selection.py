"""LLM-guided display primitive evaluation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.core.errors import ValidationError
from agent_runtime.core.types import DisplayPlan
from agent_runtime.llm.proposals import (
    DisplayPrimitiveEvaluationProposal,
    DisplayPrimitiveSelectionProposal,
)
from agent_runtime.llm.structured_call import structured_call
from agent_runtime.prompts import prompt_lines
from agent_runtime.output_pipeline.display_primitives import (
    DisplayPrimitiveManifest,
    DisplayPrimitiveRegistry,
    build_default_display_primitive_registry,
)
from agent_runtime.output_pipeline.fallback_policy import build_fallback_display_plan


class DisplaySelectionInput(BaseModel):
    """Safe LLM input for evaluating display primitive candidates."""

    model_config = ConfigDict(extra="forbid")

    original_prompt: str
    task_intent: dict[str, Any] = Field(default_factory=dict)
    dag_summary: dict[str, Any] = Field(default_factory=dict)
    result_summary: dict[str, Any] = Field(default_factory=dict)
    normalized_shapes: list[dict[str, Any]] = Field(default_factory=list)
    safe_previews: list[dict[str, Any]] = Field(default_factory=list)
    available_display_types: list[str] = Field(default_factory=list)
    target_ui: str = "openwebui"
    allow_raw_preview: bool = False


class _DisplaySource(BaseModel):
    """One safe source available for display planning."""

    model_config = ConfigDict(extra="forbid")

    source_ref: str
    source_node_id: str | None = None
    source_data_ref: str | None = None
    shape_type: str | None = None
    capability_id: str | None = None
    operation_id: str | None = None
    status: str | None = None
    preview: Any = None


class _DisplayPrimitiveCandidate(BaseModel):
    """One runtime-declared primitive/source candidate for LLM fit judging."""

    model_config = ConfigDict(extra="forbid")

    source_ref: str
    primitive_id: str
    shape_type: str | None = None
    capability_id: str | None = None
    operation_id: str | None = None
    description: str
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    display_type: str
    max_preview_rows: int | None = None
    safety_policy: dict[str, Any] = Field(default_factory=dict)
    candidate_index: int


def _source_ref_for_preview(preview: dict[str, Any]) -> str | None:
    """Return the canonical source ref for one safe preview."""

    node_id = preview.get("node_id")
    if node_id is not None:
        return f"node:{node_id}"
    data_ref = preview.get("data_ref")
    if data_ref is not None:
        return f"data:{data_ref}"
    return None


def _source_from_preview(preview: dict[str, Any]) -> _DisplaySource | None:
    """Convert a safe preview into a display-planning source."""

    source_ref = _source_ref_for_preview(preview)
    if source_ref is None:
        return None
    return _DisplaySource(
        source_ref=source_ref,
        source_node_id=str(preview.get("node_id")) if preview.get("node_id") is not None else None,
        source_data_ref=str(preview.get("data_ref")) if preview.get("data_ref") is not None else None,
        shape_type=str(preview.get("shape_type")) if preview.get("shape_type") is not None else None,
        capability_id=str(preview.get("capability_id")) if preview.get("capability_id") is not None else None,
        operation_id=str(preview.get("operation_id")) if preview.get("operation_id") is not None else None,
        status=str(preview.get("status")) if preview.get("status") is not None else None,
        preview=preview.get("preview"),
    )


def _display_sources(selection_input: DisplaySelectionInput) -> list[_DisplaySource]:
    """Return display-planning sources in safe preview order."""

    sources: list[_DisplaySource] = []
    seen: set[str] = set()
    for preview in selection_input.safe_previews:
        source = _source_from_preview(preview)
        if source is None or source.source_ref in seen:
            continue
        seen.add(source.source_ref)
        sources.append(source)
    return sources


def _public_manifest(manifest: DisplayPrimitiveManifest) -> dict[str, Any]:
    """Return one primitive manifest as safe LLM-facing metadata."""

    return manifest.model_dump(mode="json")


def _candidate_for_source(
    source: _DisplaySource,
    manifest: DisplayPrimitiveManifest,
    candidate_index: int,
) -> _DisplayPrimitiveCandidate:
    """Build one declared primitive/source candidate."""

    return _DisplayPrimitiveCandidate(
        source_ref=source.source_ref,
        primitive_id=manifest.primitive_id,
        shape_type=source.shape_type,
        capability_id=source.capability_id,
        operation_id=source.operation_id,
        description=manifest.description,
        parameters_schema=dict(manifest.parameters_schema),
        display_type=manifest.display_type,
        max_preview_rows=manifest.max_preview_rows,
        safety_policy=dict(manifest.safety_policy),
        candidate_index=candidate_index,
    )


def _build_primitive_candidates(
    sources: list[_DisplaySource],
    registry: DisplayPrimitiveRegistry,
    *,
    include_raw_payload: bool = False,
) -> list[_DisplayPrimitiveCandidate]:
    """Build all compatible primitive/source candidates declared to the LLM."""

    candidates: list[_DisplayPrimitiveCandidate] = []
    for source in sources:
        manifests = registry.compatible_manifests(
            shape_type=source.shape_type,
            capability_id=source.capability_id,
        )
        if include_raw_payload and not any(manifest.primitive_id == "raw_payload" for manifest in manifests):
            manifests = [*manifests, registry.get("raw_payload")]
        for manifest in manifests:
            if manifest.primitive_id == "raw_payload" and not include_raw_payload:
                continue
            candidates.append(_candidate_for_source(source, manifest, len(candidates)))

    if len(sources) > 1:
        manifest = registry.get("multi_section")
        candidates.append(
            _DisplayPrimitiveCandidate(
                source_ref="bundle:all",
                primitive_id=manifest.primitive_id,
                shape_type="multi_section",
                capability_id=None,
                operation_id=None,
                description=manifest.description,
                parameters_schema=dict(manifest.parameters_schema),
                display_type=manifest.display_type,
                candidate_index=len(candidates),
            )
        )
    return candidates


def _build_selection_prompt(
    selection_input: DisplaySelectionInput,
    registry: DisplayPrimitiveRegistry,
    candidates: list[_DisplayPrimitiveCandidate],
    sources: list[_DisplaySource],
) -> str:
    """Build the strict JSON-only prompt for primitive fit evaluation."""

    return "\n".join(
        [
            *prompt_lines("output.display_selection"),
            "Safe display selection context:",
            str(
                {
                    "original_prompt": selection_input.original_prompt,
                    "task_intent": selection_input.task_intent,
                    "dag_summary": selection_input.dag_summary,
                    "result_summary": selection_input.result_summary,
                    "target_ui": selection_input.target_ui,
                    "normalized_shapes": selection_input.normalized_shapes,
                    "sources": [source.model_dump(mode="json") for source in sources],
                    "primitive_manifests": [
                        _public_manifest(manifest) for manifest in registry.list_manifests()
                    ],
                    "primitive_candidates": [
                        candidate.model_dump(mode="json") for candidate in candidates
                    ],
                }
            ),
            "JSON schema:",
            str(DisplayPrimitiveSelectionProposal.model_json_schema()),
        ]
    )


def _rows_from_preview(preview: Any) -> list[dict[str, Any]]:
    """Extract row records from a safe source preview."""

    if isinstance(preview, list) and all(isinstance(item, dict) for item in preview):
        return [dict(item) for item in preview]
    if not isinstance(preview, dict):
        return []
    for key in ("rows", "records", "entries", "processes", "listeners", "matches"):
        value = preview.get(key)
        if not isinstance(value, list):
            continue
        rows: list[dict[str, Any]] = []
        for item in value:
            rows.append(dict(item) if isinstance(item, dict) else {"path": item})
        return rows
    return []


def _source_count(source: _DisplaySource, rows: list[dict[str, Any]]) -> int:
    """Return the preview row count used for validation policy."""

    if isinstance(source.preview, dict) and isinstance(source.preview.get("preview_count"), int):
        return int(source.preview["preview_count"])
    return len(rows)


def _validate_requested_columns(parameters: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Reject advisor-requested columns that are unavailable."""

    requested = parameters.get("columns")
    if requested is None:
        return
    if not isinstance(requested, list) or not all(isinstance(item, str) and item.strip() for item in requested):
        raise ValidationError("display plan columns must be a list of non-empty strings.")
    available: set[str] = set()
    for row in rows:
        available.update(str(key) for key in row)
    missing = [column for column in requested if column not in available]
    if missing:
        raise ValidationError(f"display plan requested missing columns: {', '.join(missing)}")


def _candidate_map(
    candidates: list[_DisplayPrimitiveCandidate],
) -> dict[tuple[str, str], _DisplayPrimitiveCandidate]:
    """Return candidates keyed by source ref and primitive id."""

    return {
        (candidate.source_ref, candidate.primitive_id): candidate
        for candidate in candidates
    }


def _source_by_ref(sources: list[_DisplaySource]) -> dict[str, _DisplaySource]:
    """Return display sources keyed by source ref."""

    return {source.source_ref: source for source in sources}


def _validate_evaluations(
    proposal: DisplayPrimitiveSelectionProposal,
    candidates: list[_DisplayPrimitiveCandidate],
) -> list[tuple[DisplayPrimitiveEvaluationProposal, _DisplayPrimitiveCandidate]]:
    """Validate LLM primitive evaluations against declared candidates."""

    declared = _candidate_map(candidates)
    validated: list[tuple[DisplayPrimitiveEvaluationProposal, _DisplayPrimitiveCandidate]] = []
    seen: set[tuple[str, str]] = set()
    errors: list[str] = []

    if not proposal.evaluations:
        raise ValidationError("No display primitive evaluations were returned.")

    for evaluation in proposal.evaluations:
        key = (evaluation.source_ref, evaluation.primitive_id)
        candidate = declared.get(key)
        if candidate is None:
            errors.append(
                f"Display primitive evaluation was not declared: {evaluation.source_ref} / {evaluation.primitive_id}."
            )
            continue
        if key in seen:
            errors.append(
                f"Duplicate display primitive evaluation: {evaluation.source_ref} / {evaluation.primitive_id}."
            )
            continue
        seen.add(key)
        validated.append((evaluation, candidate))

    if errors:
        raise ValidationError("; ".join(errors))
    return validated


def _section_from_evaluation(
    evaluation: DisplayPrimitiveEvaluationProposal,
    candidate: _DisplayPrimitiveCandidate,
    source: _DisplaySource,
) -> dict[str, Any]:
    """Build one display plan section from a validated primitive evaluation."""

    section: dict[str, Any] = {
        "primitive_id": candidate.primitive_id,
        "display_type": candidate.display_type,
        "parameters": dict(evaluation.parameters),
        "advisor": {
            "fits": evaluation.fits,
            "confidence": evaluation.confidence,
            "reason": evaluation.reason,
            "priority": evaluation.priority,
        },
    }
    if evaluation.title:
        section["title"] = evaluation.title
    if source.source_node_id is not None:
        section["source_node_id"] = source.source_node_id
    elif source.source_data_ref is not None:
        section["source_data_ref"] = source.source_data_ref
    else:
        raise ValidationError(f"Display source has no node or data ref: {source.source_ref}")
    return section


def _select_best_per_source(
    validated: list[tuple[DisplayPrimitiveEvaluationProposal, _DisplayPrimitiveCandidate]],
    sources: list[_DisplaySource],
) -> dict[str, tuple[DisplayPrimitiveEvaluationProposal, _DisplayPrimitiveCandidate]]:
    """Select the highest-confidence accepted primitive for each source."""

    by_source: dict[str, list[tuple[DisplayPrimitiveEvaluationProposal, _DisplayPrimitiveCandidate]]] = {}
    for evaluation, candidate in validated:
        if not evaluation.fits or candidate.source_ref == "bundle:all":
            continue
        by_source.setdefault(candidate.source_ref, []).append((evaluation, candidate))

    selected: dict[str, tuple[DisplayPrimitiveEvaluationProposal, _DisplayPrimitiveCandidate]] = {}
    for source_ref, pairs in by_source.items():
        selected[source_ref] = max(
            pairs,
            key=lambda pair: (
                float(pair[0].confidence),
                -pair[1].candidate_index,
            ),
        )
    return {
        source.source_ref: selected[source.source_ref]
        for source in sources
        if source.source_ref in selected
    }


def _bundle_title(
    proposal: DisplayPrimitiveSelectionProposal,
    validated: list[tuple[DisplayPrimitiveEvaluationProposal, _DisplayPrimitiveCandidate]],
) -> str | None:
    """Return a top-level title, preferring accepted bundle-level evaluation text."""

    for evaluation, candidate in validated:
        if evaluation.fits and candidate.source_ref == "bundle:all" and evaluation.title:
            return evaluation.title
    return proposal.title


def _plan_from_evaluations(
    proposal: DisplayPrimitiveSelectionProposal,
    validated: list[tuple[DisplayPrimitiveEvaluationProposal, _DisplayPrimitiveCandidate]],
    sources: list[_DisplaySource],
) -> DisplayPlan:
    """Build a trusted display plan from validated primitive evaluations."""

    sources_by_ref = _source_by_ref(sources)
    selected_by_source = _select_best_per_source(validated, sources)
    if not selected_by_source:
        raise ValidationError("No display primitive candidate was accepted.")

    sections: list[dict[str, Any]] = []
    for source_ref, (evaluation, candidate) in selected_by_source.items():
        sections.append(
            _section_from_evaluation(
                evaluation,
                candidate,
                sources_by_ref[source_ref],
            )
        )
    sections.sort(
        key=lambda section: (
            int((section.get("advisor") or {}).get("priority", 100)),
            next(
                (
                    index
                    for index, source in enumerate(sources)
                    if source.source_node_id == section.get("source_node_id")
                    or source.source_data_ref == section.get("source_data_ref")
                ),
                9999,
            ),
        )
    )

    if len(sections) == 1:
        section = sections[0]
        return DisplayPlan(
            display_type=section["display_type"],
            primitive_id=section["primitive_id"],
            title=section.get("title") or proposal.title,
            sections=sections,
            constraints={
                "selection_strategy": "boolean_primitive_evaluation",
                "accepted_evaluation_count": len(selected_by_source),
            },
            redaction_policy="standard",
        )

    return DisplayPlan(
        display_type="multi_section",
        primitive_id="multi_section",
        title=_bundle_title(proposal, validated),
        sections=sections,
        constraints={
            "selection_strategy": "boolean_primitive_evaluation",
            "accepted_evaluation_count": len(selected_by_source),
        },
        redaction_policy="standard",
    )


def _validate_display_plan_references(
    display_plan: DisplayPlan,
    selection_input: DisplaySelectionInput,
    registry: DisplayPrimitiveRegistry,
) -> DisplayPlan:
    """Validate display primitive/source refs against safe available sources."""

    available_sources = {
        source.source_ref: source
        for source in _display_sources(selection_input)
    }
    registry.get(display_plan.primitive_id or display_plan.display_type)

    for section in display_plan.sections:
        primitive_id = str(section.get("primitive_id") or display_plan.primitive_id or "")
        manifest = registry.get(primitive_id)
        if primitive_id == "raw_payload" and not selection_input.allow_raw_preview:
            raise ValidationError("raw payload display primitive is not allowed for this target.")
        source_node_id = section.get("source_node_id")
        source_data_ref = section.get("source_data_ref")
        if source_node_id is not None:
            source_ref = f"node:{source_node_id}"
        elif source_data_ref is not None:
            source_ref = f"data:{source_data_ref}"
        else:
            raise ValidationError("Display plan section must reference source_node_id or source_data_ref.")

        source = available_sources.get(source_ref)
        if source is None:
            raise ValidationError(f"display plan references missing source_ref: {source_ref}")
        if not manifest.accepts(shape_type=source.shape_type, capability_id=source.capability_id):
            raise ValidationError(
                f"display primitive {primitive_id!r} cannot render source {source_ref} "
                f"with shape {source.shape_type!r} and capability {source.capability_id!r}."
            )
        parameters = section.get("parameters") if isinstance(section.get("parameters"), dict) else {}
        rows = _rows_from_preview(source.preview)
        _validate_requested_columns(parameters, rows)
        if manifest.max_preview_rows is not None and _source_count(source, rows) > manifest.max_preview_rows:
            raise ValidationError(
                f"display primitive {primitive_id!r} received {_source_count(source, rows)} preview rows, "
                f"which exceeds its max_preview_rows={manifest.max_preview_rows}."
            )

    return display_plan


class DisplayValidationPhase:
    """Deterministic display plan validation phase."""

    def __init__(self, registry: DisplayPrimitiveRegistry | None = None) -> None:
        self.registry = registry or build_default_display_primitive_registry()

    def validate(self, display_plan: DisplayPlan, selection_input: DisplaySelectionInput) -> DisplayPlan:
        """Validate a display plan against known sources and primitive policy."""

        return _validate_display_plan_references(display_plan, selection_input, self.registry)


def select_display_plan(
    selection_input: DisplaySelectionInput,
    llm_client,
    registry: DisplayPrimitiveRegistry | None = None,
) -> DisplayPlan:
    """Select a display plan from boolean primitive evaluations."""

    resolved_registry = registry or build_default_display_primitive_registry()
    sources = _display_sources(selection_input)
    candidates = _build_primitive_candidates(
        sources,
        resolved_registry,
        include_raw_payload=selection_input.allow_raw_preview,
    )
    if not candidates:
        raise ValidationError("No compatible display primitive candidates were available.")
    prompt = _build_selection_prompt(selection_input, resolved_registry, candidates, sources)
    proposal = structured_call(llm_client, prompt, DisplayPrimitiveSelectionProposal)
    validated = _validate_evaluations(proposal, candidates)
    plan = _plan_from_evaluations(proposal, validated, sources)
    return _validate_display_plan_references(plan, selection_input, resolved_registry)


class DisplaySelectionAdvisor:
    """LLM advisor phase for display primitive/source fit evaluation."""

    def __init__(self, registry: DisplayPrimitiveRegistry | None = None) -> None:
        self.registry = registry or build_default_display_primitive_registry()

    def advise(self, selection_input: DisplaySelectionInput, llm_client) -> DisplayPlan:
        """Return a validated display plan advised by the LLM."""

        return select_display_plan(selection_input, llm_client, self.registry)


class DisplaySelector:
    """Compatibility selector for older orchestrator code paths."""

    def select(self, selection_input: DisplaySelectionInput) -> DisplayPlan:
        """Return a deterministic fallback display plan when no LLM is used."""

        return build_fallback_display_plan(selection_input)
