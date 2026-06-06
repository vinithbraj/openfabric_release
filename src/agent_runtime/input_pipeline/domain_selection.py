"""Capability selection for typed task frames."""

from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.capabilities.consistency import manifest_contract, manifest_contract_hash
from agent_runtime.capabilities.registry import CapabilityRegistry
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.core.semantic_compatibility import (
    canonical_domain,
    canonical_object_family,
    canonical_semantic_verb,
    domains_compatible,
    has_hard_cross_domain_conflict,
    object_types_compatible,
    semantic_verbs_compatible,
)
from agent_runtime.core.errors import CapabilityNotFoundError
from agent_runtime.core.types import CapabilityRef, TaskFrame
from agent_runtime.input_pipeline.plan_selection import CandidateEvaluation, select_best_candidate
from agent_runtime.llm.proposals import (
    CapabilityCandidateEvaluationProposal,
    CapabilitySelectionProposal,
    collect_n_best_structured_attempts,
)
from agent_runtime.llm.reproducibility import (
    PlanningTrace,
    PlanningTraceEntry,
    append_trace_entry,
    llm_client_metadata,
)
from agent_runtime.prompts import prompt_lines


class CapabilitySelectionResult(BaseModel):
    """Ranked candidate capabilities for one task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    candidates: list[CapabilityRef] = Field(default_factory=list)
    selected: CapabilityRef | None = None
    unresolved_reason: str | None = None


class _SelectionResponse(BaseModel):
    """Internal structured response returned by the LLM for one shortlist judgment."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    evaluations: list[CapabilityCandidateEvaluationProposal] = Field(default_factory=list)
    unresolved_reason: str | None = None


class DomainSelector:
    """Legacy placeholder domain selector.

    The active typed runtime uses LLM evaluation over declared registry domains.
    This placeholder intentionally avoids phrase-specific routing.
    """

    def select(self, prompt: str) -> str:
        """Return an unknown domain without phrase-specific matching."""

        _ = prompt
        return "unknown"


_SHORTLIST_SIZE = 5
_OPERATOR_PAYLOAD_ARGUMENTS = {
    "operator.shell_command": "command",
    "operator.python_action": "code",
    "operator.python_transform": "code",
}


def _normalized_likely_domains(classification_context: dict[str, Any] | None) -> list[str]:
    """Return canonical likely domains from classification context."""

    likely_domains = (classification_context or {}).get("likely_domains")
    if not isinstance(likely_domains, list):
        return []
    normalized: list[str] = []
    for value in likely_domains:
        canonical = canonical_domain(value)
        if canonical is not None and canonical not in normalized:
            normalized.append(canonical)
    return normalized


def _manifest_risk_score(manifest: CapabilityManifest) -> int:
    """Return a comparable risk score for one manifest."""

    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(manifest.risk_level, 0)


def _exact_object_match(task: TaskFrame, manifest: CapabilityManifest) -> bool:
    """Return whether the task object family exactly matches one manifest object family."""

    task_object = canonical_object_family(task.object_type)
    manifest_objects = {
        canonical_object_family(value)
        for value in manifest.object_types
    }
    return task_object is not None and task_object in manifest_objects


def _preferred_capability_bias(task: TaskFrame, manifest: CapabilityManifest) -> int:
    """Return declarative manifest-tag bias without capability-id phrase rules."""

    task_tags = {
        value.strip().lower()
        for value in (
            task.object_type,
            task.operation_intent or "",
            task.side_effect_type or "",
        )
        if value
    }
    manifest_tags = {
        value.strip().lower()
        for value in [
            *manifest.semantic_tags,
            *manifest.object_types,
            *manifest.produced_roles,
            *manifest.consumed_roles,
            manifest.side_effect_type or "",
        ]
        if value
    }
    return 4 if task_tags & manifest_tags else 0


def _capability_product_diagnostics(task: TaskFrame, manifest: CapabilityManifest) -> dict[str, Any]:
    """Return side-effect compatibility evidence for one task/capability pair.

    Legacy product-contract ranking has been removed from capability selection;
    operator-native input bindings now carry value flow. Keep this compact shape
    only for trace compatibility and side-effect filtering.
    """

    side_effect = task.side_effect_type
    score = 0
    reasons: list[str] = []
    hard_reject = False

    if side_effect:
        if manifest.side_effect_type and side_effect == manifest.side_effect_type:
            score += 6
            reasons.append(f"matches declared side effect {side_effect}")
        elif not manifest.mutates_state:
            hard_reject = True
            reasons.append(f"does not perform required side effect {side_effect}")

    return {
        "score": score,
        "reasons": reasons,
        "hard_reject": hard_reject,
        "side_effect_type": side_effect,
    }


def _build_shortlist(
    task: TaskFrame,
    registry: CapabilityRegistry,
    classification_context: dict[str, Any] | None = None,
    shortlist_size: int = _SHORTLIST_SIZE,
) -> list[CapabilityManifest]:
    """Return a deterministic ranked shortlist of candidate manifests for one task."""

    likely_domains = _normalized_likely_domains(classification_context)
    task_domain = canonical_domain(canonical_object_family(task.object_type))
    task_verb = canonical_semantic_verb(task.semantic_verb)

    scored: list[tuple[tuple[int, int, int, int, int, int, int, str], CapabilityManifest]] = []
    for manifest in registry.list_manifests():
        operator_native = manifest.domain.strip().lower() == "operator"
        if not operator_native and has_hard_cross_domain_conflict(
            task_domain,
            manifest.domain,
            task.object_type,
            list(manifest.object_types),
            likely_domains,
        ):
            continue
        domain_match = domains_compatible(
            task_domain,
            manifest.domain,
            likely_domains,
            task.object_type,
            list(manifest.object_types),
        )
        object_match = object_types_compatible(task.object_type, list(manifest.object_types), likely_domains)
        verb_match = semantic_verbs_compatible(task_verb, list(manifest.semantic_verbs))
        exact_object = _exact_object_match(task, manifest)
        bias = _preferred_capability_bias(task, manifest)
        product_diagnostics = _capability_product_diagnostics(task, manifest)
        if product_diagnostics["hard_reject"] and not operator_native:
            continue
        product_score = int(product_diagnostics["score"])
        safe_read_alignment = 1 if (task_verb not in {"create", "update", "delete"} and manifest.read_only) else 0
        if not any((domain_match, object_match, verb_match, bias > 0)):
            if product_score <= 0:
                continue
        if product_score <= 0 and task.side_effect_type and not operator_native:
            continue
        score = (
            bias,
            product_score,
            1 if exact_object else 0,
            1 if object_match else 0,
            1 if domain_match else 0,
            1 if verb_match else 0,
            safe_read_alignment,
            -_manifest_risk_score(manifest),
            manifest.capability_id,
        )
        scored.append((score, manifest))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [manifest for _, manifest in scored[:shortlist_size]]


def _shortlist_candidate_confidence(rank: int) -> float:
    """Return one stable heuristic confidence for a shortlist position."""

    return max(0.60, 0.96 - (rank * 0.06))


def _deterministic_operator_payload_candidate(
    task: TaskFrame,
    shortlist: list[CapabilityManifest],
    classification_context: dict[str, Any] | None = None,
) -> CapabilityRef | None:
    """Return an operator candidate when the task already carries its action payload."""

    constraints = task.constraints if isinstance(task.constraints, dict) else {}
    likely_domains = _normalized_likely_domains(classification_context)
    task_domain = canonical_domain(canonical_object_family(task.object_type))

    for manifest in shortlist:
        payload_argument = _OPERATOR_PAYLOAD_ARGUMENTS.get(manifest.capability_id)
        if payload_argument is None:
            continue
        payload = constraints.get(payload_argument)
        if not isinstance(payload, str) or not payload.strip():
            continue
        allowed_arguments = set(manifest.required_arguments) | set(manifest.optional_arguments)
        if payload_argument not in allowed_arguments:
            continue
        if has_hard_cross_domain_conflict(
            task_domain,
            manifest.domain,
            task.object_type,
            list(manifest.object_types),
            likely_domains,
        ):
            continue
        return CapabilityRef(
            capability_id=manifest.capability_id,
            operation_id=manifest.operation_id,
            confidence=0.98,
            reason=(
                "Deterministic operator selection accepted this candidate because the task "
                f"already includes a concrete {payload_argument!r} payload declared by the "
                "operator manifest."
            ),
        )
    return None


def _deterministic_operator_fallback_candidate(
    task: TaskFrame,
    shortlist: list[CapabilityManifest],
    registry: CapabilityRegistry,
) -> CapabilityRef | None:
    """Return a generic operator backend when LLM shortlist judging stalls.

    Operator capabilities are intentionally broad execution backends for
    ordinary local work. If they made the deterministic shortlist and the LLM
    fails to accept any candidate, prefer a structurally compatible operator
    action rather than surfacing a false capability gap.
    """

    transform_verbs = {"transform", "analyze", "calculate", "filter", "sort", "summarize", "compare", "render"}
    preferred_ids = (
        ["operator.python_transform", "operator.python_action", "operator.shell_command"]
        if canonical_semantic_verb(task.semantic_verb) in transform_verbs and task.dependencies
        else ["operator.shell_command", "operator.python_action", "operator.python_transform"]
    )
    manifest_by_id = {manifest.capability_id: manifest for manifest in shortlist}
    for capability_id in preferred_ids:
        manifest = manifest_by_id.get(capability_id)
        if manifest is None:
            continue
        if not semantic_verbs_compatible(task.semantic_verb, list(manifest.semantic_verbs)):
            continue
        candidate = CapabilityRef(
            capability_id=manifest.capability_id,
            operation_id=manifest.operation_id,
            confidence=0.88,
            reason=(
                "Deterministic operator fallback selected this generic operator backend after "
                "LLM shortlist judging did not accept any candidate. Operator arguments remain "
                "validated by the capability schema and safety policy."
            ),
        )
        validated, error = _validate_candidate(task, candidate, registry)
        if error is None and validated is not None:
            return validated
    return None


def _append_deterministic_operator_selection_trace(
    *,
    trace: PlanningTrace | None,
    task: TaskFrame,
    selected: CapabilityRef,
    shortlist: list[CapabilityManifest],
    shortlist_candidates: list[CapabilityRef],
    registry: CapabilityRegistry,
    llm_client,
    stage_started: float,
    registry_size: int,
) -> None:
    """Record a capability-selection trace entry for LLM-skipped operator payload tasks."""

    if not isinstance(trace, PlanningTrace):
        return
    model_name, temperature = llm_client_metadata(llm_client)
    result = CapabilitySelectionResult(
        task_id=task.id,
        candidates=shortlist_candidates,
        selected=selected,
    )
    append_trace_entry(
        trace,
        PlanningTraceEntry(
            stage="capability_selection",
            request_id=str(trace.request_id),
            model_name=model_name,
            llm_temperature=temperature,
            prompt_template_id="capability_selection_operator_payload",
            selected_candidate={
                **result.model_dump(mode="json"),
                "selected_manifest_contract": manifest_contract(registry.get(selected.capability_id).manifest),
                "selected_manifest_hash": manifest_contract_hash(registry.get(selected.capability_id).manifest),
                "shortlist_manifest_contracts": [manifest_contract(manifest) for manifest in shortlist],
                "capability_selection_metrics": {
                    "registry_size": registry_size,
                    "shortlist_size": len(shortlist),
                    "llm_skipped": True,
                    "duration_ms": round((time.perf_counter() - stage_started) * 1000.0, 2),
                    "llm_call_count": 0,
                    "retry_count": 0,
                },
            },
            deterministic_normalizations=[
                "deterministic_capability_shortlist",
                "operator_payload_selection_skipped_llm",
            ],
        ),
    )


def _build_selection_prompt(task: TaskFrame, shortlist_manifest: list[dict[str, object]]) -> str:
    """Build the strict JSON-only prompt for one-task shortlist judging."""

    shortlist_json = json.dumps(shortlist_manifest, sort_keys=True, default=str, ensure_ascii=True)
    return "\n".join(
        [
            *prompt_lines("input.capability_selection"),
            "Task frame:",
            str(
                {
                    "task_id": task.id,
                    "description": task.description,
                    "semantic_verb": task.semantic_verb,
                    "object_type": task.object_type,
                    "operation_intent": task.operation_intent,
                    "side_effect_type": task.side_effect_type,
                    "constraints": task.constraints,
                }
            ),
            "Deterministic capability shortlist:",
            shortlist_json,
        ]
    )


def _reason_is_explicit(reason: str) -> bool:
    """Return whether a selection reason is detailed enough to justify a mismatch."""

    words = [word for word in str(reason or "").strip().split() if word]
    return len(words) >= 5


def _verb_matches(task: TaskFrame, manifest: CapabilityManifest) -> bool:
    """Return whether the task verb is declared on the manifest."""

    return task.semantic_verb in {verb.strip().lower() for verb in manifest.semantic_verbs}


def _validate_candidate(
    task: TaskFrame,
    candidate: CapabilityRef,
    registry: CapabilityRegistry,
) -> tuple[CapabilityRef | None, str | None]:
    """Validate one selected candidate against registry and semantic rules."""

    try:
        capability = registry.get(candidate.capability_id)
    except CapabilityNotFoundError:
        return None, f"Selected capability does not exist: {candidate.capability_id}."

    manifest = capability.manifest
    if candidate.operation_id != manifest.operation_id:
        return None, (
            f"Selected operation does not match capability manifest for {candidate.capability_id}: "
            f"{candidate.operation_id} != {manifest.operation_id}."
        )

    if candidate.confidence < 0.60:
        return None, (
            f"Selected capability confidence is below threshold for task {task.id}: "
            f"{candidate.confidence:.2f} < 0.60."
        )

    if not _verb_matches(task, manifest):
        side_effect_diagnostics = _capability_product_diagnostics(task, manifest)
        side_effect_supports_candidate = (
            int(side_effect_diagnostics["score"]) >= 6
            and not bool(side_effect_diagnostics["hard_reject"])
        )
        if not (
            side_effect_supports_candidate
            or (candidate.confidence >= 0.85 and _reason_is_explicit(candidate.reason))
        ):
            return None, (
                f"Selected capability semantic verbs do not match task verb {task.semantic_verb} "
                f"for {candidate.capability_id}."
            )

    if manifest.requires_confirmation and manifest.execution_backend != "operator":
        task.requires_confirmation = True

    return candidate, None


def _proposal_candidate_to_trusted(candidate) -> CapabilityRef:
    """Convert one untrusted capability candidate proposal into the trusted model."""

    return CapabilityRef.model_validate(
        {
            "capability_id": candidate.capability_id,
            "operation_id": candidate.operation_id,
            "confidence": candidate.confidence,
            "reason": candidate.reason,
        }
    )


def _evaluation_to_trusted_candidate(
    evaluation: CapabilityCandidateEvaluationProposal,
    *,
    candidate_reason_prefix: str = "",
) -> CapabilityRef:
    """Convert one candidate evaluation into a trusted selected-candidate reference."""

    reason = str(evaluation.reason or "").strip()
    if candidate_reason_prefix and reason:
        reason = f"{candidate_reason_prefix} {reason}".strip()
    elif candidate_reason_prefix:
        reason = candidate_reason_prefix
    return CapabilityRef.model_validate(
        {
            "capability_id": evaluation.capability_id,
            "operation_id": evaluation.operation_id,
            "confidence": evaluation.confidence,
            "reason": reason,
        }
    )


def _validate_shortlist_evaluations(
    task: TaskFrame,
    shortlist: list[CapabilityManifest],
    response: CapabilitySelectionProposal,
) -> tuple[dict[tuple[str, str], CapabilityCandidateEvaluationProposal], list[str]]:
    """Validate that one LLM shortlist judgment refers only to the runtime shortlist."""

    errors: list[str] = []
    shortlist_keys = {
        (manifest.capability_id, manifest.operation_id): manifest for manifest in shortlist
    }
    evaluations: dict[tuple[str, str], CapabilityCandidateEvaluationProposal] = {}
    if response.task_id != task.id:
        errors.append(f"Shortlist evaluation task_id mismatch: {response.task_id} != {task.id}.")
    for evaluation in response.evaluations:
        key = (evaluation.capability_id, evaluation.operation_id)
        if key not in shortlist_keys:
            errors.append(
                f"Evaluated capability is not in the deterministic shortlist: {evaluation.capability_id}."
            )
            continue
        if key in evaluations:
            errors.append(f"Duplicate evaluation for capability {evaluation.capability_id}.")
            continue
        evaluations[key] = evaluation
    if not evaluations:
        errors.append(f"No shortlisted capabilities were evaluated for task {task.id}.")
    return evaluations, errors


def _select_from_shortlist(
    task: TaskFrame,
    shortlist: list[CapabilityManifest],
    evaluation_map: dict[tuple[str, str], CapabilityCandidateEvaluationProposal],
    classification_context: dict[str, Any] | None = None,
) -> CapabilityRef | None:
    """Return the best candidate from one evaluated shortlist."""

    likely_domains = _normalized_likely_domains(classification_context)
    task_domain = canonical_domain(canonical_object_family(task.object_type))
    task_object = canonical_object_family(task.object_type)
    accepted: list[tuple[tuple[int, int, int, float, int, int, str], CapabilityRef]] = []
    fallback: list[tuple[tuple[int, int, int, int, str], CapabilityRef]] = []

    for manifest in shortlist:
        key = (manifest.capability_id, manifest.operation_id)
        evaluation = evaluation_map.get(key)
        if evaluation is None:
            continue
        domain_match = domains_compatible(
            task_domain,
            manifest.domain,
            likely_domains,
            task.object_type,
            list(manifest.object_types),
        )
        object_match = object_types_compatible(task.object_type, list(manifest.object_types), likely_domains)
        exact_object = _exact_object_match(task, manifest)
        hard_conflict = has_hard_cross_domain_conflict(
            task_domain,
            manifest.domain,
            task.object_type,
            list(manifest.object_types),
            likely_domains,
        )
        missing_args = len(evaluation.missing_arguments_likely)
        trusted = _evaluation_to_trusted_candidate(evaluation)
        if evaluation.fits and evaluation.confidence >= 0.60 and not hard_conflict:
            accepted.append(
                (
                    (
                        1 if exact_object else 0,
                        1 if object_match else 0,
                        1 if domain_match else 0,
                        float(evaluation.confidence),
                        -_manifest_risk_score(manifest),
                        -missing_args,
                        manifest.capability_id,
                    ),
                    trusted,
                )
            )
        elif (
            task_object is not None
            and exact_object
            and domain_match
            and semantic_verbs_compatible(task.semantic_verb, list(manifest.semantic_verbs))
            and not hard_conflict
            and evaluation.confidence < 0.85
        ):
            fallback.append(
                (
                    (
                        1 if exact_object else 0,
                        1 if object_match else 0,
                        1 if domain_match else 0,
                        -_manifest_risk_score(manifest),
                        manifest.capability_id,
                    ),
                    trusted.model_copy(
                        update={
                            "reason": (
                                "Deterministic shortlist fallback selected this candidate after inconclusive binary judging. "
                                + trusted.reason
                            ).strip()
                        }
                    ),
                )
            )

    if accepted:
        accepted.sort(key=lambda item: item[0], reverse=True)
        return accepted[0][1]
    if fallback:
        fallback.sort(key=lambda item: item[0], reverse=True)
        return fallback[0][1]
    return None


def _selection_user_payload(task: TaskFrame) -> dict[str, Any]:
    """Return the stable payload sent with one capability selection prompt."""

    return {
        "task_id": task.id,
        "description": task.description,
        "semantic_verb": task.semantic_verb,
        "object_type": task.object_type,
    }


def _evaluate_selection_attempts(
    *,
    attempts,
    task: TaskFrame,
    shortlist: list[CapabilityManifest],
    shortlist_candidates: list[CapabilityRef],
    registry: CapabilityRegistry,
    classification_context: dict[str, Any] | None,
) -> list[CandidateEvaluation[CapabilitySelectionResult]]:
    """Validate one or more untrusted capability-selection attempts."""

    evaluations: list[CandidateEvaluation[CapabilitySelectionResult]] = []
    for attempt in attempts:
        evaluation = CandidateEvaluation[CapabilitySelectionResult](
            raw_response=attempt.raw_llm_response,
            validation_errors=list(attempt.validation_errors),
        )
        if attempt.parsed_proposal is None or attempt.validation_errors:
            evaluations.append(evaluation)
            continue
        response = attempt.parsed_proposal
        evaluation_map, validation_errors = _validate_shortlist_evaluations(task, shortlist, response)
        evaluation.rejection_reasons.extend(validation_errors)
        validated_selected = _select_from_shortlist(
            task,
            shortlist,
            evaluation_map,
            classification_context,
        )
        unresolved_reason = response.unresolved_reason
        if validated_selected is not None:
            validated_selected, validation_error = _validate_candidate(
                task,
                validated_selected,
                registry,
            )
            if validation_error:
                unresolved_reason = validation_error
                evaluation.rejection_reasons.append(validation_error)
        if validated_selected is None and not unresolved_reason:
            unresolved_reason = (
                evaluation.rejection_reasons[0]
                if evaluation.rejection_reasons
                else f"No shortlisted capability was accepted for task {task.id}."
            )
            evaluation.rejection_reasons.append(unresolved_reason)
        evaluation.proposal = CapabilitySelectionResult(
            task_id=task.id,
            candidates=shortlist_candidates,
            selected=validated_selected,
            unresolved_reason=unresolved_reason,
        )
        evaluation.confidence = (
            float(validated_selected.confidence) if validated_selected is not None else 0.0
        )
        evaluation.capability_compatibility = 1 if validated_selected is not None else 0
        if validated_selected is not None:
            manifest = registry.get(validated_selected.capability_id).manifest
            evaluation.risk_score = {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(
                manifest.risk_level,
                0,
            )
            selected_key = (validated_selected.capability_id, validated_selected.operation_id)
            selected_evaluation = evaluation_map.get(selected_key)
            evaluation.missing_required_count = (
                len(selected_evaluation.missing_arguments_likely)
                if selected_evaluation is not None
                else 0
            )
        evaluation.assumption_count = sum(
            len(item.missing_arguments_likely) for item in response.evaluations
        )
        evaluation.unresolved_count = 0 if unresolved_reason is None else 1
        evaluations.append(evaluation)
    return evaluations


def _selection_is_accepted(
    selected: CandidateEvaluation[CapabilitySelectionResult] | None,
) -> bool:
    """Return whether selected evaluation contains an accepted capability."""

    return (
        selected is not None
        and selected.proposal is not None
        and selected.proposal.selected is not None
    )


def _selection_retry_feedback(evaluations: list[CandidateEvaluation[CapabilitySelectionResult]]) -> str:
    """Return concise deterministic validation feedback for a retry prompt."""

    reasons: list[str] = []
    for evaluation in evaluations:
        reasons.extend(evaluation.validation_errors)
        reasons.extend(evaluation.rejection_reasons)
    if not reasons:
        return "No selected candidate passed deterministic validation."
    return "; ".join(reasons[:5])


def select_capabilities(
    tasks: list[TaskFrame],
    registry: CapabilityRegistry,
    llm_client,
    classification_context: dict[str, Any] | None = None,
    n_best: int = 2,
    trace: PlanningTrace | None = None,
) -> list[CapabilitySelectionResult]:
    """Select ranked capability candidates via deterministic shortlist plus binary LLM judging.

    ``n_best`` now controls whether one corrective retry is allowed. The
    runtime asks once in the normal path and spends at most one alternate
    attempt only when the first proposal does not yield an accepted selected
    capability.
    """

    results: list[CapabilitySelectionResult] = []

    for task in tasks:
        stage_started = time.perf_counter()
        registry_size = len(registry.list_manifests())
        shortlist = _build_shortlist(task, registry, classification_context, shortlist_size=_SHORTLIST_SIZE)
        shortlist_candidates = [
            CapabilityRef(
                capability_id=manifest.capability_id,
                operation_id=manifest.operation_id,
                confidence=_shortlist_candidate_confidence(index),
                reason="Deterministic shortlist candidate from canonical semantic compatibility.",
            )
            for index, manifest in enumerate(shortlist)
        ]
        deterministic_operator_candidate = _deterministic_operator_payload_candidate(
            task,
            shortlist,
            classification_context,
        )
        if deterministic_operator_candidate is not None:
            _append_deterministic_operator_selection_trace(
                trace=trace,
                task=task,
                selected=deterministic_operator_candidate,
                shortlist=shortlist,
                shortlist_candidates=shortlist_candidates,
                registry=registry,
                llm_client=llm_client,
                stage_started=stage_started,
                registry_size=registry_size,
            )
            results.append(
                CapabilitySelectionResult(
                    task_id=task.id,
                    candidates=shortlist_candidates,
                    selected=deterministic_operator_candidate,
                )
            )
            continue
        shortlist_manifest = [
            {
                "capability_id": manifest.capability_id,
                "operation_id": manifest.operation_id,
                "domain": manifest.domain,
                "description": manifest.description,
                "semantic_verbs": list(manifest.semantic_verbs),
                "object_types": list(manifest.object_types),
                "output_object_types": list(manifest.output_object_types),
                "output_fields": list(manifest.output_fields),
                "output_affordances": list(manifest.output_affordances),
                "argument_schema": dict(manifest.argument_schema),
                "required_arguments": list(manifest.required_arguments),
                "optional_arguments": list(manifest.optional_arguments),
                "output_schema": dict(manifest.output_schema),
                "product_compatibility": _capability_product_diagnostics(task, manifest),
                "risk_level": manifest.risk_level,
                "read_only": manifest.read_only,
                "examples": list(manifest.examples),
            }
            for manifest in shortlist
        ]
        if not shortlist:
            results.append(
                CapabilitySelectionResult(
                    task_id=task.id,
                    candidates=[],
                    selected=None,
                    unresolved_reason=f"No deterministic shortlist candidates were available for task {task.id}.",
                )
            )
            continue

        prompt = _build_selection_prompt(task, shortlist_manifest)
        max_attempts = max(1, int(n_best))
        user_payload = _selection_user_payload(task)
        attempts = collect_n_best_structured_attempts(
            llm_client=llm_client,
            system_prompt=prompt,
            user_payload=user_payload,
            output_model=CapabilitySelectionProposal,
            n=1,
        )
        evaluations = _evaluate_selection_attempts(
            attempts=attempts,
            task=task,
            shortlist=shortlist,
            shortlist_candidates=shortlist_candidates,
            registry=registry,
            classification_context=classification_context,
        )
        selected = select_best_candidate(evaluations)
        retried = False
        retry_allowed = max_attempts > len(attempts)
        if not _selection_is_accepted(selected) and retry_allowed:
            retry_prompt = "\n".join(
                [
                    prompt,
                    "Previous capability selection attempt did not yield a valid selected capability after deterministic validation.",
                    f"Validation feedback: {_selection_retry_feedback(evaluations)}",
                    "Re-evaluate the same shortlist and return the best valid fit if one exists.",
                ]
            )
            retry_attempts = collect_n_best_structured_attempts(
                llm_client=llm_client,
                system_prompt=retry_prompt,
                user_payload=user_payload,
                output_model=CapabilitySelectionProposal,
                n=1,
            )
            attempts.extend(retry_attempts)
            evaluations.extend(
                _evaluate_selection_attempts(
                    attempts=retry_attempts,
                    task=task,
                    shortlist=shortlist,
                    shortlist_candidates=shortlist_candidates,
                    registry=registry,
                    classification_context=classification_context,
                )
            )
            selected = select_best_candidate(evaluations)
            retried = True

        used_operator_fallback = False
        if not _selection_is_accepted(selected):
            operator_fallback = _deterministic_operator_fallback_candidate(
                task,
                shortlist,
                registry,
            )
            if operator_fallback is not None:
                selected = CandidateEvaluation[CapabilitySelectionResult](
                    proposal=CapabilitySelectionResult(
                        task_id=task.id,
                        candidates=shortlist_candidates,
                        selected=operator_fallback,
                    ),
                    confidence=operator_fallback.confidence,
                    capability_compatibility=1,
                )
                used_operator_fallback = True

        if selected is None or selected.proposal is None:
            results.append(
                CapabilitySelectionResult(
                    task_id=task.id,
                    candidates=shortlist_candidates,
                    selected=None,
                    unresolved_reason=f"No shortlisted capability was accepted for task {task.id}.",
                )
            )
            continue

        capability_selection_metrics = {
            "registry_size": registry_size,
            "shortlist_size": len(shortlist),
            "llm_skipped": False,
            "duration_ms": round((time.perf_counter() - stage_started) * 1000.0, 2),
            "llm_call_count": len(attempts),
            "retry_count": 1 if retried else 0,
        }
        if isinstance(trace, PlanningTrace):
            model_name, temperature = llm_client_metadata(llm_client)
            append_trace_entry(
                trace,
                PlanningTraceEntry(
                    stage="capability_selection",
                    request_id=str(trace.request_id),
                    model_name=model_name,
                    llm_temperature=temperature,
                    prompt_template_id="capability_selection",
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
                    selected_candidate={
                        **selected.proposal.model_dump(mode="json"),
                        "selected_manifest_contract": (
                            manifest_contract(registry.get(selected.proposal.selected.capability_id).manifest)
                            if selected.proposal.selected is not None
                            else None
                        ),
                        "selected_manifest_hash": (
                            manifest_contract_hash(registry.get(selected.proposal.selected.capability_id).manifest)
                            if selected.proposal.selected is not None
                            else None
                        ),
                        "shortlist_manifest_contracts": [
                            manifest_contract(manifest) for manifest in shortlist
                        ],
                        "capability_selection_metrics": capability_selection_metrics,
                    },
                    rejection_reasons=[
                        reason for evaluation in evaluations for reason in evaluation.rejection_reasons
                    ],
                    deterministic_normalizations=[
                        "deterministic_capability_shortlist",
                        *(["retried_after_unresolved_selection"] if retried else []),
                        *(["operator_backend_fallback_after_unresolved_selection"] if used_operator_fallback else []),
                    ],
                ),
            )

        results.append(selected.proposal)

    return results
