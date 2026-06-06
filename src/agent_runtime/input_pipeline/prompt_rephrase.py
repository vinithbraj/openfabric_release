"""Conservative preflight prompt rephrasing for new user prompts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from agent_runtime.llm.proposals import PromptRephraseProposal
from agent_runtime.llm.structured_call import StructuredCallError, structured_call
from agent_runtime.prompts import render_prompt

PROMPT_REPHRASE_CONFIDENCE_THRESHOLD = 0.70
PROMPT_REPHRASE_MAX_TOKENS = 512

_PROTECTED_PLACEHOLDER_RE = re.compile(
    r"\[[^\]\n]{0,160}(?:payload|placeholder|redacted|macro)[^\]\n]{0,160}\]",
    re.IGNORECASE,
)
_QUOTED_TEXT_RE = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`\n]+`")
_SLASH_MACRO_RE = re.compile(
    r"(?<!\S)/(?:checkonlineai|checkonline|autoapprove|restart|runlater|remind|todo|typein)\b",
    re.IGNORECASE,
)
_CONTROL_MACRO_RE = re.compile(
    r"\b(?:typein|checkonlineai)\s+\"(?:\\.|[^\"\\])*\"",
    re.IGNORECASE,
)
_PATH_RE = re.compile(
    r"(?<!\w)(?:~/?|\.{1,2}/|/[A-Za-z0-9_.\-/]+|[A-Za-z]:\\[^\s,;:]+)"
)
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
    r"\s+\d{1,2}(?:,\s*\d{4})?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PromptRephraseOutcome:
    """Validated result of one prompt rephrase attempt."""

    original_prompt: str
    rephrased_prompt: str
    enabled: bool
    changed: bool
    applied: bool
    confidence: float | None = None
    reason: str = ""
    fallback_reason: str | None = None
    proposal: PromptRephraseProposal | None = None


def build_prompt_rephrase_prompt(original_prompt: str) -> str:
    """Build the typed prompt-normalization request."""

    return render_prompt(
        "input.prompt_rephrase",
        {
            "original_prompt": str(original_prompt or ""),
            "schema_json": PromptRephraseProposal.model_json_schema(),
        },
    )


def _llm_chain(client: Any) -> list[Any]:
    chain: list[Any] = []
    seen: set[int] = set()
    current = client
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = getattr(current, "inner", None)
    return chain


def _structured_call_with_max_tokens(
    client: Any,
    prompt: str,
    *,
    max_tokens: int,
) -> PromptRephraseProposal:
    targets: list[tuple[Any, Any]] = []
    for item in _llm_chain(client):
        attrs = getattr(item, "__dict__", {})
        if isinstance(attrs, dict) and "max_tokens" in attrs:
            previous = attrs.get("max_tokens")
            try:
                effective = int(max_tokens)
                if previous is not None:
                    effective = min(effective, int(previous))
                setattr(item, "max_tokens", max(1, effective))
                targets.append((item, previous))
            except (TypeError, ValueError):
                continue
    try:
        return structured_call(
            client,
            prompt,
            PromptRephraseProposal,
            repair_attempts=0,
        )
    finally:
        for item, previous in reversed(targets):
            setattr(item, "max_tokens", previous)


def _protected_spans(prompt: str) -> list[str]:
    spans: list[str] = []
    for pattern in (
        _PROTECTED_PLACEHOLDER_RE,
        _CONTROL_MACRO_RE,
        _SLASH_MACRO_RE,
        _QUOTED_TEXT_RE,
        _PATH_RE,
        _DATE_RE,
    ):
        spans.extend(match.group(0) for match in pattern.finditer(prompt))
    deduped: list[str] = []
    seen: set[str] = set()
    for span in spans:
        if span and span not in seen:
            seen.add(span)
            deduped.append(span)
    return deduped


def dropped_protected_spans(original_prompt: str, candidate_prompt: str) -> list[str]:
    """Return protected literal spans missing from a candidate rephrase."""

    return [span for span in _protected_spans(original_prompt) if span not in candidate_prompt]


def validate_prompt_rephrase(
    original_prompt: str,
    proposal: PromptRephraseProposal,
    *,
    confidence_threshold: float = PROMPT_REPHRASE_CONFIDENCE_THRESHOLD,
) -> PromptRephraseOutcome:
    """Accept a rephrase only when it is non-empty, confident, changed, and literal-safe."""

    original = str(original_prompt or "")
    candidate = str(proposal.rephrased_prompt or "").strip()
    if not candidate:
        return PromptRephraseOutcome(
            original_prompt=original,
            rephrased_prompt=original,
            enabled=True,
            changed=False,
            applied=False,
            confidence=proposal.confidence,
            reason=proposal.reason,
            fallback_reason="empty_output",
            proposal=proposal,
        )
    if float(proposal.confidence) < confidence_threshold:
        return PromptRephraseOutcome(
            original_prompt=original,
            rephrased_prompt=original,
            enabled=True,
            changed=False,
            applied=False,
            confidence=proposal.confidence,
            reason=proposal.reason,
            fallback_reason="low_confidence",
            proposal=proposal,
        )
    if not bool(proposal.changed) or candidate == original.strip():
        return PromptRephraseOutcome(
            original_prompt=original,
            rephrased_prompt=original,
            enabled=True,
            changed=False,
            applied=False,
            confidence=proposal.confidence,
            reason=proposal.reason,
            fallback_reason="unchanged",
            proposal=proposal,
        )
    missing = dropped_protected_spans(original, candidate)
    if missing:
        return PromptRephraseOutcome(
            original_prompt=original,
            rephrased_prompt=original,
            enabled=True,
            changed=False,
            applied=False,
            confidence=proposal.confidence,
            reason=proposal.reason,
            fallback_reason="dropped_protected_content",
            proposal=proposal,
        )
    if len(candidate) < min(20, max(1, len(original.strip()) // 4)):
        return PromptRephraseOutcome(
            original_prompt=original,
            rephrased_prompt=original,
            enabled=True,
            changed=False,
            applied=False,
            confidence=proposal.confidence,
            reason=proposal.reason,
            fallback_reason="too_short",
            proposal=proposal,
        )
    return PromptRephraseOutcome(
        original_prompt=original,
        rephrased_prompt=candidate,
        enabled=True,
        changed=True,
        applied=True,
        confidence=proposal.confidence,
        reason=proposal.reason,
        proposal=proposal,
    )


def rephrase_prompt_preflight(
    original_prompt: str,
    llm_client: Any,
) -> PromptRephraseOutcome:
    """Run one low-cost preflight rephrase attempt with fail-open behavior."""

    original = str(original_prompt or "")
    try:
        proposal = _structured_call_with_max_tokens(
            llm_client,
            build_prompt_rephrase_prompt(original),
            max_tokens=PROMPT_REPHRASE_MAX_TOKENS,
        )
    except StructuredCallError as exc:
        return PromptRephraseOutcome(
            original_prompt=original,
            rephrased_prompt=original,
            enabled=True,
            changed=False,
            applied=False,
            fallback_reason=f"llm_schema_failure:{exc.diagnostics.error_kind}",
        )
    except Exception:
        return PromptRephraseOutcome(
            original_prompt=original,
            rephrased_prompt=original,
            enabled=True,
            changed=False,
            applied=False,
            fallback_reason="llm_failure",
        )
    return validate_prompt_rephrase(original, proposal)
