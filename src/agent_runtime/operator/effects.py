"""Shared operator action effect classification."""

from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActionEffect:
    """Structured classification for whether an operator action changes state."""

    mutates_state: bool
    read_only: bool
    effect_type: str
    confidence: str
    source: str
    reason: str


_READ_ONLY_TASK_VERBS = {
    "analyze",
    "answer",
    "calculate",
    "check",
    "compare",
    "count",
    "discover",
    "filter",
    "find",
    "get",
    "inspect",
    "list",
    "measure",
    "observe",
    "print",
    "query",
    "read",
    "render",
    "retrieve",
    "review",
    "search",
    "show",
    "sort",
    "summarize",
    "verify",
}

_MUTATING_TASK_VERBS = {
    "add",
    "apply",
    "build",
    "checkout",
    "commit",
    "configure",
    "copy",
    "create",
    "delete",
    "deploy",
    "down",
    "edit",
    "install",
    "launch",
    "merge",
    "mount",
    "move",
    "pull",
    "publish",
    "push",
    "rebase",
    "remove",
    "rename",
    "restart",
    "stage",
    "start",
    "stop",
    "sync",
    "transfer",
    "uninstall",
    "update",
    "upgrade",
    "up",
    "write",
}

_MUTATING_INTENT_RE = re.compile(
    r"\b(?:add|apply|build|checkout|commit|configure|copy|create|delete|deploy|"
    r"edit|install|launch|merge|mount|move|pull|publish|push|rebase|remove|rename|"
    r"restart|stage|start|stop|sync|synchroni[sz]e|transfer|uninstall|update|"
    r"upgrade|write|bring\s+up|shut\s+down|state[-_ ]?change|side[-_ ]?effect)\b",
    re.IGNORECASE,
)

_READ_ONLY_INTENT_RE = re.compile(
    r"\b(?:analy[sz]e|answer|calculate|check|compare|count|discover|filter|find|"
    r"gather|inspect|list|measure|observe|print|query|read|render|retrieve|"
    r"review|search|show|sort|summari[sz]e|verify|status|report|output|emit|"
    r"display|return)\b"
    r"|\b(?:info|information|metadata|metrics?|stats?|statistics|usage|available|"
    r"free|current)\b",
    re.IGNORECASE,
)

_SHELL_REDIRECTION_RE = re.compile(r"(^|[\s;&|])(?:\d?>|>>)(?!\s*/dev/null\b)")

_SHELL_MUTATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (effect_type, re.compile(pattern, re.IGNORECASE))
    for effect_type, pattern in (
        ("filesystem_delete", r"(^|[\s;&|()])rm(?:dir)?\b"),
        ("filesystem_move", r"(^|[\s;&|()])mv\b"),
        ("filesystem_copy", r"(^|[\s;&|()])cp\b"),
        ("filesystem_write", r"(^|[\s;&|()])mkdir\b"),
        ("filesystem_write", r"(^|[\s;&|()])touch\b"),
        ("filesystem_permissions", r"(^|[\s;&|()])chmod\b"),
        ("filesystem_permissions", r"(^|[\s;&|()])chown\b"),
        ("filesystem_permissions", r"(^|[\s;&|()])chgrp\b"),
        ("filesystem_write", r"(^|[\s;&|()])ln\b"),
        ("filesystem_write", r"(^|[\s;&|()])truncate\b"),
        ("filesystem_write", r"(^|[\s;&|()])tee\b"),
        ("filesystem_write", r"(^|[\s;&|()])dd\b"),
        (
            "filesystem_mount",
            r"(^|[\s;&|()])(?:sudo\s+)?mount\b"
            r"(?!\s+(?:-l|--show-labels|-v|--verbose)"
            r"(?:\s+(?:-l|--show-labels|-v|--verbose))*\s*(?:$|[;&|]))"
            r"(?=\s+[^;&|\s])",
        ),
        ("filesystem_mount", r"(^|[\s;&|()])(?:sudo\s+)?umount\b"),
        (
            "filesystem_repair",
            r"(^|[\s;&|()])(?:sudo\s+)?"
            r"(?:fsck(?:\.[A-Za-z0-9_-]+)?|e2fsck|xfs_repair|ntfsfix|dosfsck)\b",
        ),
        (
            "block_device_change",
            r"(^|[\s;&|()])(?:sudo\s+)?(?:parted|fdisk|sfdisk|sgdisk|wipefs)\b",
        ),
        ("filesystem_sync", r"(^|[\s;&|()])rsync\b"),
        ("filesystem_write", r"\bsed\b[^\n;&|]*\s-i(?:\b|[=.])"),
        ("filesystem_write", r"\bperl\b[^\n;&|]*\s-pi(?:\b|[=.])"),
        (
            "container_mutation",
            r"\bdocker\s+(?:container\s+)?(?:rm|stop|start|restart|kill|exec|run|create)\b",
        ),
        (
            "container_mutation",
            r"\bdocker\s+(?:image\s+)?(?:rm|rmi|tag|pull|push|build|prune)\b",
        ),
        (
            "container_mutation",
            r"\bdocker\s+(?:system|container|image|volume|network|builder)\s+prune\b",
        ),
        (
            "service_control",
            r"\bdocker\s+compose\s+(?:up|down|restart|rm|pull|push|build)\b",
        ),
        (
            "service_control",
            r"\b(?:systemctl|service)\s+"
            r"(?:start|stop|restart|reload|enable|disable|mask|unmask)\b",
        ),
        (
            "repo_mutation",
            r"\bgit\s+"
            r"(?:add|commit|push|pull|merge|rebase|reset|clean|stash|apply|am|"
            r"cherry-pick|revert)\b",
        ),
        ("repo_mutation", r"\bgit\s+(?:checkout|switch)\b"),
        ("repo_mutation", r"\bgit\s+branch\s+(?:-d|-D|--delete)\b"),
        (
            "cluster_mutation",
            r"\bkubectl\s+(?:apply|create|delete|patch|replace|scale|cordon|uncordon|drain)\b",
        ),
        (
            "package_change",
            r"\b(?:apt|apt-get|dnf|yum|brew|pip|uv|npm|pnpm|yarn)\s+"
            r"(?:install|uninstall|remove|upgrade|update|add|publish)\b",
        ),
        (
            "database_write",
            r"\b(?:delete\s+from|insert\s+into|update\s+\w+|drop\s+(?:table|database)|"
            r"alter\s+table|truncate\s+table)\b",
        ),
    )
)

_SHELL_READ_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(^|[;&|()])\s*(?:sudo\s+)?"
        r"(?:ls|find|locate|rg|grep|egrep|fgrep|cat|head|tail|wc|pwd|stat|du|df|"
        r"whoami|id|uname|date|printenv|env|which|whereis|test|true|false|"
        r"lsblk|blkid|findmnt|mountpoint)\b",
        r"(^|[;&|()])\s*(?:sudo\s+)?(?:echo|printf)\b",
        r"(^|[;&|()])\s*(?:sudo\s+)?mount"
        r"(?:\s+(?:-l|--show-labels|-v|--verbose))*\s*(?=$|[;&|])",
        r"(^|[;&|()])\s*(?:sudo\s+)?file\s+-s\b",
        r"(^|[;&|()])\s*(?:sudo\s+)?blockdev\s+--get(?:bsz|size64|ss|ro|ra)\b",
        r"\bgit\s+(?:status|diff|log|show|rev-parse|ls-files|grep|remote\s+-v)\b",
        r"\bgit\s+branch(?:\s+(?:--list|-a|-r))?(?:\s|$)",
        r"\bdocker\s+(?:ps|images|info|version|logs|inspect)\b",
        r"\bdocker\s+(?:container|image|volume|network)\s+(?:ls|inspect)\b",
        r"\bdocker\s+compose\s+(?:ps|logs|config|images|ls)\b",
        r"\b(?:systemctl|service)\s+"
        r"(?:status|is-active|is-enabled|list-units|list-unit-files|show)\b",
        r"\bkubectl\s+(?:get|describe|logs|version|config\s+view)\b",
        r"\b(?:apt|apt-get|dnf|yum|brew|pip|uv|npm|pnpm|yarn)\s+"
        r"(?:list|show|info|search|view|outdated|why)\b",
        r"\b(?:select\s+.+\s+from|show\s+tables|describe\s+\w+|explain\s+select)\b",
    )
)

_PYTHON_MUTATION_RE = re.compile(
    r"\b(open\s*\([^)]*,\s*['\"][wax+]|\bPath\s*\([^)]*\)\.write_(?:text|bytes)\s*\(|"
    r"\.write\s*\(|os\.(?:remove|unlink|rename|replace|rmdir|mkdir|makedirs)\s*\(|"
    r"shutil\.(?:copy|copy2|copytree|move|rmtree)\s*\(|"
    r"pathlib\.[A-Za-z0-9_]+\.unlink\s*\(|"
    r"requests\.(?:post|put|patch|delete)\s*\()",
    re.IGNORECASE,
)


def _effect(
    *,
    mutates_state: bool,
    read_only: bool,
    effect_type: str,
    confidence: str,
    source: str,
    reason: str,
) -> ActionEffect:
    return ActionEffect(
        mutates_state=mutates_state,
        read_only=read_only,
        effect_type=effect_type,
        confidence=confidence,
        source=source,
        reason=reason,
    )


def _unknown(source: str, reason: str = "No deterministic effect signal matched.") -> ActionEffect:
    return _effect(
        mutates_state=False,
        read_only=False,
        effect_type="unknown",
        confidence="low",
        source=source,
        reason=reason,
    )


def _policy_mode(value: str | None) -> str:
    return "llm" if str(value or "").strip().lower() == "llm" else "deterministic"


def _declared_action_effect(action: Any) -> ActionEffect | None:
    intent = str(getattr(action, "effect_intent", "") or "").strip().lower()
    if intent not in {"read_only", "mutates_state"}:
        return None
    try:
        numeric_confidence = float(getattr(action, "effect_confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        numeric_confidence = 0.0
    confidence = "high" if numeric_confidence >= 0.8 else "medium" if numeric_confidence >= 0.45 else "low"
    reason = str(getattr(action, "effect_summary", "") or "").strip()
    if not reason:
        reason = f"Action declares effect_intent={intent!r}."
    return _effect(
        mutates_state=intent == "mutates_state",
        read_only=intent == "read_only",
        effect_type="declared_action_effect",
        confidence=confidence,
        source="action_effect_intent",
        reason=reason,
    )


def _task_value(task: Any, key: str) -> Any:
    if isinstance(task, dict):
        return task.get(key)
    if task is None:
        return None
    return getattr(task, key, None)


def _task_text(task: Any) -> str:
    return " ".join(
        str(value or "")
        for value in (
            _task_value(task, "description"),
            _task_value(task, "goal"),
            _task_value(task, "reason"),
            _task_value(task, "operation_intent"),
            _task_value(task, "side_effect_type"),
            _task_value(task, "object_type"),
        )
    )


def classify_task_effect(
    task: Any = None,
    *,
    semantic_verb: str | None = None,
    risk: str | None = None,
    requires_confirmation: bool | None = None,
    policy_mode: str | None = None,
) -> ActionEffect:
    """Classify task intent independently from a concrete action."""

    mode = _policy_mode(policy_mode)
    verb = str(semantic_verb or _task_value(task, "semantic_verb") or "").strip().lower()
    risk_value = str(
        risk
        if risk is not None
        else _task_value(task, "risk_level")
        or _task_value(task, "risk")
        or ""
    ).strip().lower()
    requires = (
        bool(requires_confirmation)
        if requires_confirmation is not None
        else bool(_task_value(task, "requires_confirmation"))
    )
    if mode == "llm":
        if requires or risk_value in {"medium", "high", "critical"}:
            return _effect(
                mutates_state=True,
                read_only=False,
                effect_type="unknown_mutation",
                confidence="low",
                source="task_risk",
                reason="LLM-owned mode trusts typed task risk/confirmation as mutation pressure.",
            )
        return _unknown("task", "LLM-owned mode requires typed effect/risk signals.")
    text = _task_text(task).lower()
    if (
        verb in _READ_ONLY_TASK_VERBS
        and not requires
        and risk_value not in {"medium", "high", "critical"}
    ):
        return _effect(
            mutates_state=False,
            read_only=True,
            effect_type="read_only_task",
            confidence="high",
            source="task",
            reason=f"Task semantic verb {verb!r} is read-only.",
        )
    if verb in _MUTATING_TASK_VERBS:
        return _effect(
            mutates_state=True,
            read_only=False,
            effect_type="task_mutation",
            confidence="medium",
            source="task",
            reason=f"Task semantic verb {verb!r} is state-changing.",
        )
    if _MUTATING_INTENT_RE.search(text):
        return _effect(
            mutates_state=True,
            read_only=False,
            effect_type="task_mutation",
            confidence="medium",
            source="task_text",
            reason="Task text contains state-changing intent.",
        )
    if requires or risk_value in {"medium", "high", "critical"}:
        return _effect(
            mutates_state=True,
            read_only=False,
            effect_type="unknown_mutation",
            confidence="low",
            source="task_risk",
            reason="Task requires confirmation or carries mutation-level risk.",
        )
    if verb in _READ_ONLY_TASK_VERBS or _READ_ONLY_INTENT_RE.search(text):
        return _effect(
            mutates_state=False,
            read_only=True,
            effect_type="read_only_task",
            confidence="medium",
            source="task_text",
            reason="Task text appears read-only.",
        )
    return _unknown("task")


def _collapsed_shell_segments(command: str) -> list[str]:
    collapsed_segments: list[str] = []
    for segment in re.split(r"[;&|]+", str(command or "")):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        collapsed: list[str] = []
        skip_next = False
        for token in tokens:
            if skip_next:
                skip_next = False
                continue
            if token == "--":
                continue
            if token.startswith("-"):
                if "=" not in token:
                    skip_next = True
                continue
            collapsed.append(token)
        if collapsed:
            collapsed_segments.append(" ".join(collapsed))
    return collapsed_segments


def _shell_command_variants(command: str) -> list[str]:
    text = str(command or "").strip()
    variants = [text] if text else []
    variants.extend(_collapsed_shell_segments(text))
    return [variant for variant in variants if variant.strip()]


def _known_shell_mutation(command: str) -> ActionEffect | None:
    text = str(command or "")
    if _SHELL_REDIRECTION_RE.search(text):
        return _effect(
            mutates_state=True,
            read_only=False,
            effect_type="filesystem_write",
            confidence="high",
            source="shell_redirection",
            reason="Shell command writes through redirection.",
        )
    for variant in _shell_command_variants(text):
        for effect_type, pattern in _SHELL_MUTATION_PATTERNS:
            if pattern.search(variant):
                return _effect(
                    mutates_state=True,
                    read_only=False,
                    effect_type=effect_type,
                    confidence="high",
                    source="shell_pattern",
                    reason=f"Shell command matches {effect_type} mutation pattern.",
                )
    return None


def _known_shell_read_only(command: str) -> ActionEffect | None:
    variants = _shell_command_variants(command)
    if not variants:
        return None
    if any(
        pattern.search(variant)
        for variant in variants
        for pattern in _SHELL_READ_ONLY_PATTERNS
    ):
        return _effect(
            mutates_state=False,
            read_only=True,
            effect_type="read_only_shell",
            confidence="high",
            source="shell_pattern",
            reason="Shell command matches a read-only inspection/output pattern.",
        )
    return None


def classify_shell_effect(
    command: str,
    task: Any = None,
    *,
    semantic_verb: str | None = None,
    risk: str | None = None,
    requires_confirmation: bool | None = None,
    policy_mode: str | None = None,
) -> ActionEffect:
    """Classify a shell command effect without adding LLM latency."""

    text = str(command or "").strip()
    if not text:
        return _unknown("shell", "Shell command is empty.")
    mode = _policy_mode(policy_mode)
    if mode == "deterministic":
        known_mutation = _known_shell_mutation(text)
        if known_mutation is not None:
            return known_mutation
        known_read_only = _known_shell_read_only(text)
        if known_read_only is not None:
            return known_read_only
    elif _SHELL_REDIRECTION_RE.search(text):
        return _effect(
            mutates_state=True,
            read_only=False,
            effect_type="filesystem_write",
            confidence="high",
            source="shell_redirection",
            reason="Shell command writes through redirection.",
        )
    task_effect = classify_task_effect(
        task,
        semantic_verb=semantic_verb,
        risk=risk,
        requires_confirmation=requires_confirmation,
        policy_mode=mode,
    )
    if task_effect.mutates_state:
        return _effect(
            mutates_state=True,
            read_only=False,
            effect_type="unknown_mutation",
            confidence="low",
            source="task_context",
            reason="Shell command is ambiguous, but the surrounding task is mutating.",
        )
    if task_effect.read_only:
        return _effect(
            mutates_state=False,
            read_only=True,
            effect_type="read_only_shell",
            confidence="low",
            source="task_context",
            reason="Shell command is ambiguous, but the surrounding task is read-only.",
        )
    return _unknown("shell")


def _literal_subprocess_command(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        tokens: list[str] = []
        for item in node.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                tokens.append(item.value)
            else:
                return ""
        return " ".join(shlex.quote(token) for token in tokens)
    return ""


def _subprocess_commands_from_python(code: str) -> list[str]:
    try:
        tree = ast.parse(str(code or ""))
    except SyntaxError:
        return []
    commands: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_subprocess = (
            isinstance(func, ast.Attribute)
            and func.attr in {"run", "check_call", "check_output", "call", "Popen"}
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        )
        if not is_subprocess or not node.args:
            continue
        command = _literal_subprocess_command(node.args[0]).strip()
        if command:
            commands.append(command)
    return commands


def classify_python_effect(
    code: str,
    *,
    reason: str | None = None,
    task: Any = None,
    policy_mode: str | None = None,
) -> ActionEffect:
    """Classify whether Python action code appears state-changing."""

    mode = _policy_mode(policy_mode)
    text = "\n".join([str(code or ""), str(reason or "")])
    if mode == "deterministic" and _PYTHON_MUTATION_RE.search(text):
        return _effect(
            mutates_state=True,
            read_only=False,
            effect_type="python_state_change",
            confidence="high",
            source="python_pattern",
            reason="Python code uses a known state-changing API.",
        )
    commands = _subprocess_commands_from_python(str(code or ""))
    command_effects = [
        classify_shell_effect(command, policy_mode=mode)
        for command in commands
    ]
    if any(effect.mutates_state for effect in command_effects):
        return _effect(
            mutates_state=True,
            read_only=False,
            effect_type="python_subprocess_mutation",
            confidence="high",
            source="python_subprocess",
            reason="Python code runs a state-changing subprocess command.",
        )
    if commands and all(effect.read_only for effect in command_effects):
        return _effect(
            mutates_state=False,
            read_only=True,
            effect_type="read_only_python",
            confidence="high",
            source="python_subprocess",
            reason="Python code only runs read-only subprocess commands.",
        )
    if "subprocess" not in text:
        return _effect(
            mutates_state=False,
            read_only=True,
            effect_type="read_only_python",
            confidence="medium",
            source="python_pattern",
            reason="Python code has no known state-changing API.",
        )
    task_effect = classify_task_effect(task, policy_mode=mode)
    if task_effect.mutates_state:
        return _effect(
            mutates_state=True,
            read_only=False,
            effect_type="unknown_mutation",
            confidence="low",
            source="task_context",
            reason="Python subprocess usage is ambiguous, but the surrounding task is mutating.",
        )
    return _unknown("python", "Python subprocess effect is not statically known.")


def classify_action_effect(
    action: Any,
    task: Any = None,
    current_task: Any = None,
    *,
    policy_mode: str | None = None,
) -> ActionEffect:
    """Classify one operator action effect."""

    mode = _policy_mode(policy_mode)
    effective_task = current_task if current_task is not None else task
    kind = str(getattr(action, "kind", "") or "").strip()
    risk = str(getattr(action, "risk", "") or "").strip()
    declared = _declared_action_effect(action)
    if mode == "llm" and declared is not None:
        return declared
    if kind == "shell_command":
        effect = classify_shell_effect(
            str(getattr(action, "command", "") or ""),
            effective_task,
            risk=risk,
            policy_mode=mode,
        )
        return effect if effect.effect_type != "unknown" or declared is None else declared
    if kind == "llm_text":
        return _effect(
            mutates_state=False,
            read_only=True,
            effect_type="llm_text_generation",
            confidence="high",
            source="action_kind",
            reason="llm_text generates text from provided runtime evidence without mutating runtime state.",
        )
    if kind in {"python_action", "python_transform"}:
        effect = classify_python_effect(
            str(getattr(action, "code", "") or ""),
            reason=str(getattr(action, "reason", "") or ""),
            task=effective_task,
            policy_mode=mode,
        )
        return effect if effect.effect_type != "unknown" or declared is None else declared
    effect = classify_task_effect(effective_task, risk=risk, policy_mode=mode)
    return effect if effect.effect_type != "unknown" or declared is None else declared


__all__ = [
    "ActionEffect",
    "classify_action_effect",
    "classify_python_effect",
    "classify_shell_effect",
    "classify_task_effect",
]
