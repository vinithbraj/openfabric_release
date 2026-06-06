"""Memory and cache compliance helpers for the operator pipeline."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator._shared import *
from agent_runtime.operator.events import *
from agent_runtime.operator.utils import *
from agent_runtime.memory import (
    SCOPED_CACHE_KEY,
    cached_memory_scope,
    memory_directives_for_prompt_stage,
    memory_scope_from_matches,
    scope_observability_details,
    scoped_current_task,
    scoped_empty_memory_scope,
    scoped_memory_scope_id,
    scoped_memory_tags,
    scoped_retrieval_prompt,
    set_active_memory_scope,
    update_memory_scope_trace,
)

def _memory_directives_for_stage(user_request: UserRequest, stage: str) -> list[dict[str, Any]]:
    """Return memory directives that apply to one operator stage."""

    return memory_directives_for_prompt_stage(user_request, stage=stage)


def _plan_command_text(plan: OperatorPlan) -> str:
    """Return executable and action-intent text for lightweight memory checks."""

    lines: list[str] = [str(plan.summary or "")]
    for task in plan.tasks:
        lines.extend(
            [
                str(task.goal or ""),
                str(task.semantic_verb or ""),
                str(task.object_type or ""),
                str(task.reason or ""),
            ]
        )
    for action in plan.actions:
        lines.extend(
            [
                str(action.kind or ""),
                str(action.command or ""),
                str(action.code or ""),
                str(action.reason or ""),
                str(action.declared_output_shape or ""),
            ]
        )
    return "\n".join(line for line in lines if line.strip())


def _memory_constraint_errors(
    user_request: UserRequest,
    plan: OperatorPlan,
    directives: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deterministic violations for high-confidence memory directives."""

    return evaluate_memory_guard_rules(
        user_prompt=user_request.raw_prompt,
        command_text=_plan_command_text(plan),
        directives=directives,
    )


_READ_ONLY_INTENT_VERBS = {
    "answer",
    "calculate",
    "check",
    "count",
    "discover",
    "find",
    "get",
    "inspect",
    "list",
    "read",
    "review",
    "search",
    "show",
    "summarize",
    "verify",
}
_MUTATING_INTENT_VERBS = {
    "add",
    "apply",
    "build",
    "commit",
    "configure",
    "copy",
    "create",
    "delete",
    "deploy",
    "down",
    "edit",
    "execute",
    "install",
    "launch",
    "merge",
    "move",
    "publish",
    "push",
    "remove",
    "restart",
    "run",
    "start",
    "stop",
    "sync",
    "transfer",
    "update",
    "up",
    "write",
}
_READ_ONLY_INTENT_RE = re.compile(
    r"\b(?:read|list|show|check|status|inspect|verify|confirm|find|search|discover|review|summari[sz]e|count|report)\b",
    re.IGNORECASE,
)
_MUTATING_INTENT_RE = re.compile(
    r"\b(?:create|update|write|edit|delete|remove|start|stop|restart|launch|run|execute|commit|push|publish|install|uninstall|apply|deploy|merge|copy|move|transfer|sync|synchroni[sz]e|build|bring\s+up|shut\s+down)\b",
    re.IGNORECASE,
)
_NORMALIZED_MUTATING_VERBS = {
    "create",
    "delete",
    "execute",
    "install",
    "move",
    "push",
    "publish",
    "remove",
    "restart",
    "run",
    "stage",
    "start",
    "stop",
    "sync",
    "transfer",
    "uninstall",
    "update",
    "write",
}
_NORMALIZED_STATEFUL_OBJECT_RE = re.compile(
    r"\b(?:aws|conda|docker|git|kubectl|npm|pip|service|ssh|systemctl)\b",
    re.IGNORECASE,
)
_NORMALIZED_SIDE_EFFECT_RE = re.compile(
    r"\b(?:action|create|delete|external|install|mutat|operator|publish|push|remove|start|stop|update|write)\b",
    re.IGNORECASE,
)
_RISKY_LEVELS = {"medium", "high", "critical"}
_STREAMING_OPERATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("stage", re.compile(r"\b(?:git\s+add|stage|staging)\b", re.IGNORECASE)),
    (
        "commit",
        re.compile(r"\b(?:git\s+commit|commit|committed|committing)\b", re.IGNORECASE),
    ),
    ("push", re.compile(r"\b(?:git\s+push|push|pushed|pushing)\b", re.IGNORECASE)),
    ("pull", re.compile(r"\b(?:git\s+pull|pull|pulled|pulling)\b", re.IGNORECASE)),
    (
        "start",
        re.compile(
            r"\b(?:docker\s+compose\s+up|systemctl\s+start|start|started|"
            r"starting|launch|bring\s+up)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "stop",
        re.compile(
            r"\b(?:docker\s+compose\s+(?:down|stop)|systemctl\s+stop|stop|"
            r"stopped|stopping|shut\s+down)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "restart",
        re.compile(r"\b(?:systemctl\s+restart|restart|restarted|restarting)\b", re.IGNORECASE),
    ),
    (
        "status",
        re.compile(
            r"\b(?:git\s+status|docker\s+ps|status|verify|verified|confirm|"
            r"confirmed|check|checked|inspect)\b",
            re.IGNORECASE,
        ),
    ),
    ("search", re.compile(r"\b(?:find|search|locate|discover|list|ls)\b", re.IGNORECASE)),
    (
        "delete",
        re.compile(
            r"\b(?:rm\s+|delete|deleted|deleting|remove|removed|removing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "install",
        re.compile(
            r"\b(?:pip\s+install|npm\s+install|uv\s+add|poetry\s+add|install|"
            r"installed|installing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "build",
        re.compile(
            r"\b(?:docker\s+build|npm\s+run\s+build|build|built|building)\b",
            re.IGNORECASE,
        ),
    ),
    ("copy", re.compile(r"\b(?:cp\s+|copy|copied|copying)\b", re.IGNORECASE)),
    (
        "move",
        re.compile(r"\b(?:mv\s+|move|moved|moving|rename|renamed|renaming)\b", re.IGNORECASE),
    ),
    (
        "write",
        re.compile(
            r"\b(?:touch\s+|printf\s+|cat\s+>|tee\s+|write|written|writing|"
            r"append|appended|appending)\b",
            re.IGNORECASE,
        ),
    ),
)


def _mask_prompt_literal_regions(text: str) -> str:
    """Blank quoted/code regions before raw-prompt fallback intent scans."""

    source = str(text or "")
    result: list[str] = []
    quote: str | None = None
    in_inline_code = False
    in_fence = False
    escaped = False
    index = 0
    while index < len(source):
        if source.startswith("```", index):
            in_fence = not in_fence
            result.extend("   ")
            index += 3
            escaped = False
            continue
        char = source[index]
        if in_fence:
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
                result.append(" ")
                index += 1
                continue
            if char == "\\":
                escaped = True
                result.append(" ")
                index += 1
                continue
            if char == quote:
                quote = None
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if in_inline_code:
            if char == "`":
                in_inline_code = False
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            result.append(" ")
            index += 1
            continue
        if char == "`":
            in_inline_code = True
            result.append(" ")
            index += 1
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _normalized_task_trigger_reason(task: Any, *, scope: str) -> str:
    if not isinstance(task, dict):
        return ""
    risk = str(task.get("risk_level") or "").strip().lower()
    if task.get("requires_confirmation") or risk in _RISKY_LEVELS:
        return f"{scope}_risky_step" if scope == "streaming" else f"{scope}_risky_task"
    side_effect = re.sub(
        r"[_-]+",
        " ",
        str(task.get("side_effect_type") or "").strip().lower(),
    )
    if side_effect and side_effect not in {"none", "read", "read_only", "readonly", "status"}:
        if _NORMALIZED_SIDE_EFFECT_RE.search(side_effect):
            return f"{scope}_side_effect_step" if scope == "streaming" else f"{scope}_side_effect_task"
    verb = str(task.get("semantic_verb") or "").strip().lower()
    if verb in _NORMALIZED_MUTATING_VERBS:
        return f"{scope}_mutating_step" if scope == "streaming" else f"{scope}_mutating_task"
    object_type = re.sub(r"[_-]+", " ", str(task.get("object_type") or "").strip().lower())
    if object_type and _NORMALIZED_STATEFUL_OBJECT_RE.search(object_type):
        return f"{scope}_stateful_tool_step" if scope == "streaming" else f"{scope}_stateful_tool_task"
    return ""


def _operator_intent_tasks_from_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    intent_block = context.get("operator_intent_block")
    if not isinstance(intent_block, dict):
        return []
    tasks = intent_block.get("tasks")
    if not isinstance(tasks, list):
        return []
    return [dict(task) for task in tasks if isinstance(task, dict)]
_NEGATED_MUTATION_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|never|without|avoid|not)\s+"
    r"(?:\w+\s+){0,4}?"
    r"(?:create|update|write|edit|delete|remove|start|stop|restart|launch|run|execute|commit|push|publish|install|uninstall|apply|deploy|merge|copy|move|transfer|sync|synchroni[sz]e|build|bring\s+up|shut\s+down)\b",
    re.IGNORECASE,
)
_READ_ONLY_MEMORY_RE = re.compile(
    r"\b(?:read[-\s]?only|status|list|listing|show|inspect|health|query|question|summary)\b",
    re.IGNORECASE,
)
_CONDITIONAL_MEMORY_RE = re.compile(
    r"\b(?:unless\s+explicitly|only\s+when\s+explicitly|only\s+if\s+explicitly|if\s+explicitly|when\s+explicitly|unless\s+the\s+user|only\s+when\s+the\s+user)\b",
    re.IGNORECASE,
)
_MUTATION_RESTRICTION_MEMORY_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|never|not\s+use|not\s+run|instead|rather\s+than|substitute|replace)\b",
    re.IGNORECASE,
)


def _memory_directive_text(directive: dict[str, Any]) -> str:
    return " ".join(
        [
            str(directive.get("instruction") or ""),
            str(directive.get("summary") or ""),
            " ".join(str(value) for value in directive.get("blocked_examples") or []),
            str(directive.get("intent_type") or ""),
        ]
    )


def _memory_scope_text(user_request: UserRequest, plan: OperatorPlan) -> tuple[str, str]:
    """Return the text used to judge memory intent and the source of that text."""

    context = dict(user_request.session_context or {})
    current_task = context.get("operator_streaming_current_task")
    if isinstance(current_task, dict) and current_task:
        return _stable_json(current_task), "streaming_current_task"
    intent_block = context.get("operator_intent_block")
    if isinstance(intent_block, dict):
        tasks = intent_block.get("tasks")
        if isinstance(tasks, list) and tasks:
            return _stable_json({"tasks": tasks}), "operator_intent_block"
    if str(user_request.raw_prompt or "").strip():
        return str(user_request.raw_prompt), "prompt"
    task_payload = [
        {
            "task_id": task.task_id,
            "goal": task.goal,
            "semantic_verb": task.semantic_verb,
            "object_type": task.object_type,
            "reason": task.reason,
        }
        for task in plan.tasks
    ]
    return _stable_json({"tasks": task_payload}), "plan_tasks"


def _memory_scope_intent(user_request: UserRequest, plan: OperatorPlan) -> dict[str, Any]:
    """Classify the current memory scope as read-only, mutating, or ambiguous."""

    text, source = _memory_scope_text(user_request, plan)
    normalized = _NEGATED_MUTATION_RE.sub(" ", str(text or "").lower())
    words = set(re.findall(r"[a-z][a-z0-9_-]*", normalized))
    read_only_hit = bool(words & _READ_ONLY_INTENT_VERBS) or bool(_READ_ONLY_INTENT_RE.search(normalized))
    mutation_hit = bool(words & _MUTATING_INTENT_VERBS) or bool(_MUTATING_INTENT_RE.search(normalized))
    if source == "streaming_current_task":
        current_task = dict(user_request.session_context or {}).get("operator_streaming_current_task")
        verb = str(current_task.get("semantic_verb") or "").lower() if isinstance(current_task, dict) else ""
        if verb in _READ_ONLY_INTENT_VERBS:
            read_only_hit = True
            mutation_hit = False
        elif verb in _MUTATING_INTENT_VERBS:
            mutation_hit = True
            read_only_hit = False
    if mutation_hit and not read_only_hit:
        intent = "explicit_mutation"
    elif read_only_hit and not mutation_hit:
        intent = "read_only"
    elif mutation_hit and read_only_hit:
        intent = "explicit_mutation" if source != "streaming_current_task" else "ambiguous"
    else:
        intent = "ambiguous"
    return {
        "intent": intent,
        "source": source,
        "read_only_hit": read_only_hit,
        "mutation_hit": mutation_hit,
    }


def _memory_directive_is_read_only_restriction(directive: dict[str, Any]) -> bool:
    """Return whether a memory appears to restrict mutation only for read/status work."""

    text = _memory_directive_text(directive)
    if not _READ_ONLY_MEMORY_RE.search(text):
        return False
    return bool(
        _CONDITIONAL_MEMORY_RE.search(text)
        or _MUTATION_RESTRICTION_MEMORY_RE.search(text)
    )


def _memory_directives_for_current_intent(
    user_request: UserRequest,
    plan: OperatorPlan,
    directives: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Filter memory directives that conflict with explicit current-scope mutation."""

    scope = _memory_scope_intent(user_request, plan)
    if scope.get("intent") != "explicit_mutation":
        return directives, [], scope
    kept: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    for directive in directives:
        if _memory_directive_is_read_only_restriction(directive):
            ignored.append(
                {
                    "memory_id": str(directive.get("memory_id") or ""),
                    "reason": "Directive restricts mutation for read-only/status contexts, but the current scope explicitly requests mutation.",
                }
            )
            continue
        kept.append(directive)
    return kept, ignored, scope


def _plan_has_mutating_action(plan: OperatorPlan) -> bool:
    task_by_id = {task.task_id: task for task in plan.tasks}
    for action in plan.actions:
        if classify_action_effect(action, task=task_by_id.get(action.task_id)).mutates_state:
            return True
    return False


def _memory_repair_preservation_errors(
    user_request: UserRequest,
    original_plan: OperatorPlan,
    candidate_plan: OperatorPlan,
) -> list[dict[str, Any]]:
    """Reject memory repairs that erase an explicit user-requested mutation."""

    scope = _memory_scope_intent(user_request, original_plan)
    if scope.get("intent") != "explicit_mutation":
        return []
    if not _plan_has_mutating_action(original_plan):
        return []
    if _plan_has_mutating_action(candidate_plan):
        return []
    return [
        {
            "error": "memory_repair_erased_requested_mutation",
            "message": (
                "The memory-compliance revision replaced an explicit user-requested "
                "mutation with only read-only work."
            ),
            "repair_hint": (
                "Preserve the requested mutation or an equivalent safe implementation. "
                "Memory may add probes, path/cwd fixes, confirmation, or verification, "
                "but must not erase the current requested action."
            ),
            "memory_intent_scope": scope,
        }
    ]

_memory_directives_for_stage_impl = _memory_directives_for_stage
_mask_prompt_literal_regions_impl = _mask_prompt_literal_regions
_normalized_task_trigger_reason_impl = _normalized_task_trigger_reason
_operator_intent_tasks_from_context_impl = _operator_intent_tasks_from_context

from agent_runtime.operator.prompts import *
from agent_runtime.operator.validation import *
from agent_runtime.operator.clarification import *

_memory_directives_for_stage = _memory_directives_for_stage_impl
_mask_prompt_literal_regions = _mask_prompt_literal_regions_impl
_normalized_task_trigger_reason = _normalized_task_trigger_reason_impl
_operator_intent_tasks_from_context = _operator_intent_tasks_from_context_impl


class _MemoryComplianceCommonMixin:
    """Shared base for memory compliance support mixins."""


__all__ = [
    name
    for name in globals()
    if not name.startswith("__") and name != "_MemoryComplianceCommonMixin"
]
