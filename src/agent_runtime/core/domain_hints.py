"""Declarative domain hint detection for runtime planning and memory lookup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern


@dataclass(frozen=True)
class DomainHint:
    """One domain's deterministic keyword and tag extraction hints."""

    domain: str
    keyword_patterns: tuple[Pattern[str], ...] = ()
    context_patterns: tuple[Pattern[str], ...] = ()
    action_tag_patterns: tuple[tuple[str, Pattern[str]], ...] = ()

    def matches(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in (*self.keyword_patterns, *self.context_patterns))

    def action_tags(self, text: str) -> list[str]:
        return [tag for tag, pattern in self.action_tag_patterns if pattern.search(text)]


@dataclass(frozen=True)
class DomainHintDetection:
    """Detected domains and enriched tags for a request-like text packet."""

    domains: list[str]
    tags: list[str]

    @property
    def single_domain(self) -> str:
        unique = list(dict.fromkeys(self.domains))
        return unique[0] if len(unique) == 1 else ""


def _pattern(value: str) -> Pattern[str]:
    return re.compile(value, re.IGNORECASE)


DOMAIN_HINTS: tuple[DomainHint, ...] = (
    DomainHint(
        domain="git",
        keyword_patterns=(
            _pattern(
                r"\b(?:"
                r"git|repo|repos|repository|repositories|commit|commits|committed|committing|"
                r"branch|branches|checkout|merge|rebase|push|pull|"
                r"remote|origin|diff|stash|worktree|uncommitted"
                r")\b"
            ),
        ),
        context_patterns=(
            _pattern(
                r"\b(?:stage|staged|staging)\b.*\b(?:change|changes|file|files|commit|push|repo|repository|git)\b"
                r"|\b(?:change|changes|file|files|commit|push|repo|repository|git)\b.*\b(?:stage|staged|staging)\b"
            ),
        ),
        action_tag_patterns=(
            ("commit", _pattern(r"\bcommit(?:s|ted|ting)?\b")),
            ("stage", _pattern(r"\bstag(?:e|ed|ing)\b")),
            ("push", _pattern(r"\bpush(?:ed|ing)?\b")),
            ("branch", _pattern(r"\bbranches?\b")),
            ("remote", _pattern(r"\b(?:remote|origin)\b")),
            ("diff", _pattern(r"\bdiff\b")),
            ("status", _pattern(r"\bstatus\b")),
            ("repository", _pattern(r"\b(?:repo|repos|repository|repositories)\b")),
        ),
    ),
    DomainHint(
        domain="docker",
        keyword_patterns=(
            _pattern(r"\b(?:docker|container|containers|compose|dockerfile|image|images|volume|volumes)\b"),
        ),
    ),
    DomainHint(
        domain="sql",
        keyword_patterns=(
            _pattern(r"\b(?:sql|database|databases|postgres|postgresql|schema|schemas|query|queries|joins?)\b"),
        ),
        action_tag_patterns=(
            ("query", _pattern(r"\bquer(?:y|ies|ied|ying)\b")),
            ("join", _pattern(r"\bjoins?\b")),
            ("schema", _pattern(r"\bschemas?\b")),
        ),
    ),
    DomainHint(
        domain="dicom",
        keyword_patterns=(
            _pattern(
                r"\b(?:"
                r"dicom|orthanc|rtplan|rtstruct|rtdose|rtrecord|"
                r"sopinstanceuid|studyinstanceuid|seriesinstanceuid|patientid|"
                r"beamsequence|roicontoursequence|registrationsequence"
                r")\b"
            ),
        ),
        action_tag_patterns=(
            ("orthanc", _pattern(r"\borthanc\b")),
            ("rtplan", _pattern(r"\brtplan\b")),
            ("rtstruct", _pattern(r"\brtstruct\b")),
            ("rtdose", _pattern(r"\brtdose\b")),
            ("rtrecord", _pattern(r"\brtrecord\b")),
            ("registration", _pattern(r"\b(?:reg|registrationsequence)\b")),
            ("roi", _pattern(r"\broi\b")),
        ),
    ),
)


def _append_unique(values: list[str], *items: str) -> list[str]:
    seen = {str(value or "").strip().lower() for value in values if str(value or "").strip()}
    for item in items:
        value = str(item or "").strip()
        key = value.lower()
        if value and key not in seen:
            values.append(value)
            seen.add(key)
    return values


def detect_domain_hints(
    text: str,
    *,
    existing_tags: list[str] | tuple[str, ...] | None = None,
) -> DomainHintDetection:
    """Return deterministic domain and tag hints for request or context text."""

    normalized_text = " ".join([str(text or ""), " ".join(existing_tags or [])]).lower()
    tags = [str(tag or "").strip() for tag in list(existing_tags or []) if str(tag or "").strip()]
    domains: list[str] = []
    for hint in DOMAIN_HINTS:
        if not hint.matches(normalized_text):
            continue
        domains.append(hint.domain)
        _append_unique(tags, hint.domain, *hint.action_tags(normalized_text))
    return DomainHintDetection(domains=domains, tags=tags)
