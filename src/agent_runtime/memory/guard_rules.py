"""Declarative deterministic guard rules derived from retrieved memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern
from typing import Any


REQUIRED_MEMORY_STRENGTHS = {"must_consider", "required_unless_conflict"}


@dataclass(frozen=True)
class MemoryGuardRule:
    """One deterministic memory guard that can produce repair feedback."""

    rule_id: str
    summary: str
    required_terms: tuple[str, ...]
    trigger_terms: tuple[str, ...]
    command_pattern: Pattern[str]
    required_command_text: str
    violation_message: str
    repair_hint: str
    forbidden_command_pattern: Pattern[str] | None = None
    forbidden_trigger_terms: tuple[str, ...] = ()
    forbidden_allow_prompt_pattern: Pattern[str] | None = None
    forbidden_message: str = ""
    forbidden_repair_hint: str = ""

    def matching_memory_ids(self, directives: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        for directive in directives:
            if str(directive.get("strength") or "") not in REQUIRED_MEMORY_STRENGTHS:
                continue
            text = _directive_text(directive)
            if not all(term in text for term in self.required_terms):
                continue
            if any(term in text for term in self.trigger_terms):
                memory_id = str(directive.get("memory_id") or "").strip()
                if memory_id:
                    ids.append(memory_id)
        return ids

    def matching_forbidden_memory_ids(self, directives: list[dict[str, Any]]) -> list[str]:
        if not self.forbidden_trigger_terms:
            return []
        ids: list[str] = []
        for directive in directives:
            if str(directive.get("strength") or "") not in REQUIRED_MEMORY_STRENGTHS:
                continue
            text = _directive_text(directive)
            if not all(term in text for term in self.required_terms):
                continue
            if any(term in text for term in self.forbidden_trigger_terms):
                memory_id = str(directive.get("memory_id") or "").strip()
                if memory_id:
                    ids.append(memory_id)
        return ids

    def evaluate(
        self,
        *,
        user_prompt: str,
        command_text: str,
        directives: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        command_lower = str(command_text or "").lower()
        memory_ids = self.matching_memory_ids(directives)
        if (
            memory_ids
            and self.command_pattern.search(command_lower)
            and self.required_command_text.lower() not in command_lower
        ):
            errors.append(
                {
                    "error": "memory_constraint_violation",
                    "rule_id": self.rule_id,
                    "message": self.violation_message,
                    "memory_ids": sorted(set(memory_ids)),
                    "repair_hint": self.repair_hint,
                }
            )
        if self.forbidden_command_pattern is None:
            return errors
        forbidden_ids = self.matching_forbidden_memory_ids(directives)
        prompt = str(user_prompt or "").lower()
        explicitly_allowed = bool(
            self.forbidden_allow_prompt_pattern and self.forbidden_allow_prompt_pattern.search(prompt)
        )
        if forbidden_ids and self.forbidden_command_pattern.search(command_lower) and not explicitly_allowed:
            errors.append(
                {
                    "error": "memory_constraint_violation",
                    "rule_id": self.rule_id,
                    "message": self.forbidden_message,
                    "memory_ids": sorted(set(forbidden_ids)),
                    "repair_hint": self.forbidden_repair_hint,
                }
            )
        return errors


def _pattern(value: str) -> Pattern[str]:
    return re.compile(value, re.IGNORECASE)


def _directive_text(directive: dict[str, Any]) -> str:
    return " ".join(
        [
            str(directive.get("instruction") or ""),
            str(directive.get("summary") or ""),
            " ".join(str(value) for value in directive.get("blocked_examples") or []),
        ]
    ).lower()


MEMORY_GUARD_RULES: tuple[MemoryGuardRule, ...] = (
    MemoryGuardRule(
        rule_id="git_repo_guard",
        summary=(
            "For Git repository safety memory, before git add or git commit, prove the "
            "directory is a Git work tree with git rev-parse --is-inside-work-tree; if false, "
            "stop and report; do not run git init unless explicitly requested."
        ),
        required_terms=("git",),
        trigger_terms=("repo", "repository", "rev-parse", "work-tree", "work tree"),
        command_pattern=_pattern(r"\bgit\s+(?:add|commit)\b"),
        required_command_text="git rev-parse --is-inside-work-tree",
        violation_message=(
            "Retrieved memory requires proving the target directory is an existing Git work "
            "tree before git add or git commit."
        ),
        repair_hint=(
            "Before any git add or git commit, run git rev-parse --is-inside-work-tree. "
            "If it is not true, stop and report that the directory is not a Git repository. "
            "Do not initialize a repository unless the user explicitly asked for that."
        ),
        forbidden_command_pattern=_pattern(r"\bgit\s+init\b"),
        forbidden_trigger_terms=("git init", "initialize", "initialise", "create git"),
        forbidden_allow_prompt_pattern=_pattern(
            r"\bgit\s+init\b"
            r"|\binitiali[sz]e(?:\s+a)?\s+git\b"
            r"|\bcreate(?:\s+a)?\s+(?:new\s+)?git\s+(?:repo|repository)\b"
        ),
        forbidden_message="Retrieved memory forbids creating a Git repository unless explicitly requested.",
        forbidden_repair_hint="Remove git init. If the directory is not a repo, stop and report instead.",
    ),
)


def evaluate_memory_guard_rules(
    *,
    user_prompt: str,
    command_text: str,
    directives: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deterministic memory guard violations for a plan command packet."""

    errors: list[dict[str, Any]] = []
    for rule in MEMORY_GUARD_RULES:
        errors.extend(rule.evaluate(user_prompt=user_prompt, command_text=command_text, directives=directives))
    return errors


def memory_guard_rule_summaries() -> list[dict[str, str]]:
    """Return compact rule descriptions for prompts and trace diagnostics."""

    return [{"rule_id": rule.rule_id, "summary": rule.summary} for rule in MEMORY_GUARD_RULES]
