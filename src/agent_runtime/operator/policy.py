"""Shared configurable operator policy review helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agent_runtime.llm.structured_call import structured_call
from agent_runtime.operator.models import (
    OperatorPolicyDecision,
    OperatorPolicyMode,
    OperatorPolicyModule,
    OperatorPolicyReviewResult,
    OperatorPolicySubject,
)
from agent_runtime.prompts import prompt_lines
from agent_runtime.settings_consolidation import (
    operator_legacy_override,
    operator_policy_mode_from_profile,
)


OPERATOR_POLICY_MODULES: tuple[OperatorPolicyModule, ...] = (
    "effect",
    "interaction",
    "stdout",
    "failure",
    "memory",
    "memory_question",
    "streaming_scope",
    "verb",
    "python_code_review",
)
OPERATOR_POLICY_MODES: tuple[OperatorPolicyMode, ...] = ("deterministic", "llm")
def normalize_operator_policy_mode(value: Any, *, default: OperatorPolicyMode = "deterministic") -> OperatorPolicyMode:
    """Normalize one policy ownership mode."""

    text = str(value or "").strip().lower()
    return text if text in OPERATOR_POLICY_MODES else default


def operator_policy_mode(config: Any, module: OperatorPolicyModule) -> OperatorPolicyMode:
    """Return the selected mode for one policy module from a config-like object."""

    legacy_key = (
        "operator_python_code_review_policy_mode"
        if module == "python_code_review"
        else f"operator_{module}_policy_mode"
    )
    override = operator_legacy_override(config, legacy_key)
    if override is not None:
        return normalize_operator_policy_mode(override)
    if module == "memory_question":
        return "llm"
    return operator_policy_mode_from_profile(getattr(config, "operator_policy_profile", None))


def operator_policy_modes_from_config(config: Any) -> dict[OperatorPolicyModule, OperatorPolicyMode]:
    """Return every configured policy mode from a config-like object."""

    return {
        module: operator_policy_mode(config, module)
        for module in OPERATOR_POLICY_MODULES
    }


def stable_policy_subject_fingerprint(subject: OperatorPolicySubject) -> str:
    """Return a stable cache key component for one policy subject."""

    payload = subject.model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def operator_policy_cache_key(
    *,
    request_id: str,
    mode: OperatorPolicyMode,
    subject: OperatorPolicySubject,
) -> str:
    """Return a stable policy judgment cache key."""

    return ":".join(
        [
            "operator-policy",
            str(request_id or ""),
            str(mode),
            subject.module,
            stable_policy_subject_fingerprint(subject),
        ]
    )


def build_operator_policy_review_prompt(subjects: list[OperatorPolicySubject]) -> str:
    """Build one compact prompt for a batched LLM-owned policy review."""

    payload = [subject.model_dump(mode="json") for subject in subjects]
    return "\n".join(
        [
            *prompt_lines("operator.policy_review"),
            "Subjects:",
            json.dumps(payload, sort_keys=True, default=str, indent=2),
        ]
    )


def review_operator_policy_subjects(
    llm_client: Any,
    subjects: list[OperatorPolicySubject],
    *,
    request_id: str = "",
    mode: OperatorPolicyMode = "llm",
    cache: dict[str, OperatorPolicyDecision] | None = None,
) -> OperatorPolicyReviewResult:
    """Run one batched LLM-owned policy review with optional in-memory caching."""

    if not subjects:
        return OperatorPolicyReviewResult(decisions=[])
    cache = cache if cache is not None else {}
    decisions: list[OperatorPolicyDecision] = []
    missing: list[OperatorPolicySubject] = []
    for subject in subjects:
        key = operator_policy_cache_key(request_id=request_id, mode=mode, subject=subject)
        cached = cache.get(key)
        if cached is not None:
            decisions.append(cached)
        else:
            missing.append(subject)
    if missing:
        result = structured_call(
            llm_client,
            build_operator_policy_review_prompt(missing),
            OperatorPolicyReviewResult,
        )
        decision_by_subject = {
            (decision.module, decision.subject_id): decision
            for decision in result.decisions
        }
        for subject in missing:
            decision = decision_by_subject.get((subject.module, subject.subject_id))
            if decision is None:
                decision = OperatorPolicyDecision(
                    module=subject.module,
                    subject_id=subject.subject_id,
                    decision="unknown",
                    effect_intent="unknown",
                    risk_level="medium",
                    requires_confirmation=True,
                    confidence=0.0,
                    reason="LLM policy review did not return a decision for this subject.",
                    evidence_refs=[],
                )
            key = operator_policy_cache_key(request_id=request_id, mode=mode, subject=subject)
            cache[key] = decision
            decisions.append(decision)
    return OperatorPolicyReviewResult(decisions=decisions)


__all__ = [
    "OPERATOR_POLICY_MODES",
    "OPERATOR_POLICY_MODULES",
    "build_operator_policy_review_prompt",
    "normalize_operator_policy_mode",
    "operator_policy_cache_key",
    "operator_policy_mode",
    "operator_policy_modes_from_config",
    "review_operator_policy_subjects",
    "stable_policy_subject_fingerprint",
]
