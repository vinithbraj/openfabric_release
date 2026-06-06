"""Shared helpers for compiling standard DAG nodes into operator actions."""

from __future__ import annotations

import base64
import importlib
import json
import re
import shlex
from typing import Any

from agent_runtime.core.types import ActionNode
from agent_runtime.operator.effects import classify_action_effect, classify_shell_effect
from agent_runtime.operator.models import OperatorAction, OperatorPlan, OperatorTask


_APPROVAL_RISK_LEVELS = {"high", "critical"}
_TERMINAL_PROMPT_COMMAND_PATTERNS = [
    r"(^|[\s;&|()])sudo(?:\s|$)",
    r"\bgit\s+(?:push|pull|fetch|clone|ls-remote)\b",
    r"\bgit\s+submodule\s+update\b",
    r"\bgit\s+commit\b.*(?:-S|--gpg-sign)\b",
    r"\bgit\s+tag\b.*(?:-s|-u|--sign)\b",
    r"\bssh-add\b",
    r"\bssh(?=\s|$)",
    r"\bscp(?=\s|$)",
    r"\bsftp(?=\s|$)",
    r"\brsync\b.*(?:\b-e\s+ssh\b|(?:^|[\s'\"])[^\s'\"]+@[^\s'\"]+:)",
    r"\bgh\s+auth\b",
    r"\bgh\s+(?:repo\s+)?clone\b",
    r"\bgh\s+(?:repo\s+)?(?:sync|fork|create)\b",
    r"\bdocker\s+login\b",
    r"\bdocker\s+(?:push|pull)\b",
    r"\bpodman\s+login\b",
    r"\bpodman\s+(?:push|pull)\b",
    r"\bnpm\s+(?:login|adduser|publish)\b",
    r"\byarn\s+npm\s+login\b",
    r"\bpnpm\s+login\b",
    r"\bpip\s+install\b.*(?:git\+ssh|ssh://|[^\s]+@[^\s]+:)",
    r"\buv\s+(?:pip\s+)?install\b.*(?:git\+ssh|ssh://|[^\s]+@[^\s]+:)",
    r"\baws\s+(?:configure|sso\s+login)\b",
    r"\baz\s+login\b",
    r"\bgcloud\s+auth\b",
    r"\bhuggingface-cli\s+login\b",
    r"\bhf\s+auth\s+login\b",
    r"\bwandb\s+login\b",
    r"(^|[\s;&|()])gpg(?:\s|$)",
    r"(^|[\s;&|()])gpg-agent(?:\s|$)",
    r"(^|[\s;&|()])pass(?:\s|$)",
]
_TERMINAL_PROMPT_TEXT_RE = re.compile(
    r"\b("
    r"interactive(?:\s+(?:input|prompt|terminal|tty))?"
    r"|terminal\s+(?:input|prompt)"
    r"|controlling\s+tty"
    r"|tty"
    r"|prompt(?:ed|s|ing)?"
    r"|passphrase"
    r"|password"
    r"|credential(?:s)?"
    r"|authentication"
    r"|auth(?:orization)?"
    r"|login"
    r"|username"
    r"|verification\s+code"
    r"|otp"
    r"|2fa"
    r"|mfa"
    r"|pin"
    r"|confirmation"
    r")\b",
    re.IGNORECASE,
)
_COMMON_OPERATOR_PYTHON_MODULES = (
    "collections",
    "csv",
    "datetime",
    "decimal",
    "fractions",
    "functools",
    "glob",
    "html",
    "itertools",
    "json",
    "math",
    "operator",
    "os",
    "pathlib",
    "re",
    "shlex",
    "statistics",
    "subprocess",
    "textwrap",
    "time",
)
_ZERO_VALUE_RE = r"0+(?:\.0+)?"
_OPTIONAL_SIZE_UNIT_RE = r"(?:b|bytes?|kb|kib|mb|mib|gb|gib|tb|tib|%)?"
_EMBEDDED_ZERO_AGGREGATE_RE = re.compile(
    rf"""
    ^\s*(?:[-*]\s*)?
    (?:
        total(?:\s+\w+){{0,8}}
        | sum(?:\s+\w+){{0,8}}
        | aggregate(?:\s+\w+){{0,8}}
        | combined(?:\s+\w+){{0,8}}
        | overall(?:\s+\w+){{0,8}}
        | (?:\w+\s+){{0,4}}(?:size|space|usage|bytes?)(?:\s+\w+){{0,6}}
    )
    \s*[:=-]\s*
    {_ZERO_VALUE_RE}
    \s*{_OPTIONAL_SIZE_UNIT_RE}\b
    """,
    flags=re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)


def operator_python_runtime_namespace() -> dict[str, Any]:
    """Return the shared namespace used for LLM-authored operator Python.

    Generated code is still expected to import what it uses inside the required
    function body. These stdlib globals are a small resilience layer for common
    generated-code slips such as using ``re`` after forgetting ``import re``.
    """

    namespace: dict[str, Any] = {}
    for module_name in _COMMON_OPERATOR_PYTHON_MODULES:
        try:
            namespace[module_name] = importlib.import_module(module_name)
        except Exception:
            continue
    pathlib_module = namespace.get("pathlib")
    if pathlib_module is not None:
        namespace["Path"] = pathlib_module.Path
    return namespace


def _operator_value_has_content(value: Any) -> bool:
    """Return whether a runtime input value contains meaningful data."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def operator_python_has_nonempty_inputs(
    action: OperatorAction,
    action_inputs: dict[str, Any],
) -> bool:
    """Return true when an operator Python action received meaningful input.

    Bound input may be marked optional by the model even though the generated
    parser still consumes it. Treat any non-empty bound input as real evidence
    so a bogus zero aggregate cannot slip through just because ``required`` was
    false. Literal inputs are checked when no bindings exist.
    """

    input_names = [binding.input_name for binding in action.input_bindings]
    if not input_names:
        input_names = list(action_inputs)
    return any(_operator_value_has_content(action_inputs.get(name)) for name in input_names)


def operator_python_output_is_zero_like(output: Any) -> bool:
    """Detect zero-shaped Python outputs that often mean parsing silently failed."""

    if isinstance(output, bool):
        return False
    if isinstance(output, (int, float)):
        return float(output) == 0.0
    if isinstance(output, str):
        text = output.strip()
        if not text:
            return False
        if re.fullmatch(
            r"0+(?:\.0+)?(?:\s*(?:b|bytes?|kb|kib|mb|mib|gb|gib|tb|tib))?",
            text,
            flags=re.IGNORECASE,
        ):
            return True
        if _EMBEDDED_ZERO_AGGREGATE_RE.search(text):
            return True
        numbers = re.findall(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?", text)
        return bool(numbers) and all(float(number) == 0.0 for number in numbers)
    return False


def validate_operator_python_output(
    action: OperatorAction,
    action_inputs: dict[str, Any],
    output: Any,
) -> None:
    """Reject suspicious zero-like Python output from meaningful inputs."""

    if action.allow_zero_result:
        return
    if not operator_python_has_nonempty_inputs(action, action_inputs):
        return
    if not operator_python_output_is_zero_like(output):
        return
    output_preview = str(repr(output))
    if len(output_preview) > 300:
        output_preview = output_preview[:300] + "\n...[truncated]"
    raise ValueError(
        f"{action.kind} returned a zero-like result from non-empty input: "
        f"{output_preview}. "
        "If zero is semantically valid, set allow_zero_result true and explain why; "
        "otherwise raise ValueError when no rows or values are parsed."
    )


def operator_kind_for_capability(capability_id: str) -> str | None:
    """Return the operator action kind for one generic operator capability."""

    normalized = str(capability_id or "").strip()
    if normalized == "operator.shell_command":
        return "shell_command"
    if normalized == "operator.python_transform":
        return "python_transform"
    if normalized == "operator.python_action":
        return "python_action"
    if normalized == "operator.llm_text":
        return "llm_text"
    return None


def python_action_command(code: str, inputs: dict[str, Any] | None = None) -> str:
    """Return a shell-safe gateway command that runs one Python action.

    The LLM authors ``def main(inputs):``. The gateway still executes a normal
    command, so shell streaming, terminal dispatch, cancellation, cwd, and
    timeout handling stay on the same path as shell actions.
    """

    code_b64 = base64.b64encode(str(code or "").encode("utf-8")).decode("ascii")
    inputs_b64 = base64.b64encode(
        json.dumps(dict(inputs or {}), default=str).encode("utf-8")
    ).decode("ascii")
    runner = f"""
import base64
import importlib
import json

inputs = json.loads(base64.b64decode({inputs_b64!r}).decode("utf-8"))
code = base64.b64decode({code_b64!r}).decode("utf-8")
namespace = {{}}
for _module_name in {_COMMON_OPERATOR_PYTHON_MODULES!r}:
    try:
        namespace[_module_name] = importlib.import_module(_module_name)
    except Exception:
        pass
if "pathlib" in namespace:
    namespace["Path"] = namespace["pathlib"].Path
exec(compile(code, "<operator_python_action>", "exec"), namespace, namespace)
main = namespace.get("main")
if not callable(main):
    raise RuntimeError("python_action code must define main(inputs).")
result = main(inputs)
if result is not None:
    if isinstance(result, (dict, list, tuple)):
        print(json.dumps(result, default=str))
    else:
        print(result)
""".strip()
    runner_b64 = base64.b64encode(runner.encode("utf-8")).decode("ascii")
    return "python3 -c \"$(printf %s " + shlex.quote(runner_b64) + " | base64 -d)\""


def node_id_for_operator_source(source_action_id: str, task_to_node_id: dict[str, str] | None = None) -> str:
    """Normalize common LLM source identifiers to standard ActionDAG node ids."""

    source = str(source_action_id or "").strip()
    task_to_node_id = dict(task_to_node_id or {})
    if not source:
        return source
    if source in task_to_node_id:
        return task_to_node_id[source]
    if source.startswith("node::"):
        return source
    if source.startswith("task_"):
        return f"node::{source}"
    if source.startswith("action_"):
        suffix = source.removeprefix("action_")
        task_id = f"task_{suffix}"
        return task_to_node_id.get(task_id, f"node::{task_id}")
    return source


def normalize_operator_input_bindings(
    bindings: Any,
    task_to_node_id: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return input bindings with source_action_id normalized to node ids."""

    if not isinstance(bindings, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in bindings:
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        payload["source_action_id"] = node_id_for_operator_source(
            str(payload.get("source_action_id") or ""),
            task_to_node_id,
        )
        normalized.append(payload)
    return normalized


def operator_action_from_node(
    node: ActionNode,
    arguments: dict[str, Any],
    *,
    task_to_node_id: dict[str, str] | None = None,
) -> OperatorAction:
    """Compile one standard ActionNode into an OperatorAction."""

    kind = operator_kind_for_capability(node.capability_id)
    if kind is None:
        raise ValueError(f"Not an operator capability: {node.capability_id}")

    payload = dict(arguments or {})
    payload["input_bindings"] = normalize_operator_input_bindings(
        payload.get("input_bindings", []),
        task_to_node_id,
    )
    return normalize_operator_action_interaction(
        OperatorAction.model_validate(
            {
                "action_id": node.id,
                "task_id": node.task_id,
                "kind": kind,
                "command": payload.get("command") if kind == "shell_command" else None,
                "code": payload.get("code") if kind in {"python_transform", "python_action"} else None,
                "llm_prompt": payload.get("llm_prompt") if kind == "llm_text" else None,
                "execution_mode": payload.get("execution_mode") or "captured",
                "interaction_mode": payload.get("interaction_mode") or "non_interactive",
                "cwd": payload.get("cwd") or ".",
                "inputs": payload.get("inputs") or {},
                "input_bindings": payload.get("input_bindings") or [],
                "stdin_mode": payload.get("stdin_mode") or "none",
                "stdin_text": payload.get("stdin_text"),
                "stdin_input_name": payload.get("stdin_input_name"),
                "declared_output_shape": payload.get("declared_output_shape") or "text",
                "defer_code_generation": bool(payload.get("defer_code_generation", False)),
                "allow_zero_result": bool(payload.get("allow_zero_result", False)),
                "risk": payload.get("risk") or "medium",
                "timeout_seconds": payload.get("timeout_seconds"),
                "reason": payload.get("reason") or node.description or "LLM-authored operator action.",
                "depends_on": list(node.depends_on),
            }
        )
    )


def shell_command_looks_mutating(command: str, *, policy_mode: str | None = None) -> bool:
    """Return whether a shell command appears to modify local or remote state."""

    return classify_shell_effect(str(command or ""), policy_mode=policy_mode).mutates_state


def shell_command_may_prompt(command: str, *, policy_mode: str | None = None) -> bool:
    """Return whether a shell command should own a real terminal when possible.

    This is a runtime routing guard, not a domain fallback. Some commands are
    finite but may need a controlling TTY for passphrases, passwords, host-key
    prompts, or credential helpers. If the LLM forgets ``interaction_mode:
    may_prompt``, normalize the action before validation/execution so prompts
    are visible and answerable in the Agent UI terminal.
    """

    normalized = str(command or "").strip()
    if not normalized:
        return False
    return any(
        re.search(pattern, normalized, re.IGNORECASE)
        for pattern in _TERMINAL_PROMPT_COMMAND_PATTERNS
    )


def terminal_prompt_text_may_prompt(text: str) -> bool:
    """Return whether generic plan text indicates interactive terminal input."""

    return bool(_TERMINAL_PROMPT_TEXT_RE.search(str(text or "")))


def normalize_operator_action_interaction(
    action: OperatorAction,
    *,
    policy_mode: str | None = None,
) -> OperatorAction:
    """Return an action with terminal-prompt routing inferred where necessary."""

    if action.kind != "shell_command":
        return action
    if action.interaction_mode != "non_interactive":
        return action
    prompt_text = " ".join(
        str(value or "")
        for value in (
            action.command,
            action.reason,
            action.effect_summary,
        )
    )
    if not (
        terminal_prompt_text_may_prompt(prompt_text)
        or shell_command_may_prompt(str(action.command or ""), policy_mode=policy_mode)
    ):
        return action
    return action.model_copy(update={"interaction_mode": "may_prompt"})


def normalize_operator_plan_interactions(
    plan: OperatorPlan,
    *,
    policy_mode: str | None = None,
) -> tuple[OperatorPlan, list[dict[str, Any]]]:
    """Infer terminal-prompt routing for every action in one plan."""

    changes: list[dict[str, Any]] = []
    actions: list[OperatorAction] = []
    for action in plan.actions:
        normalized = normalize_operator_action_interaction(action, policy_mode=policy_mode)
        if normalized.interaction_mode != action.interaction_mode:
            changes.append(
                {
                    "action_id": action.action_id,
                    "command": action.command,
                    "from": action.interaction_mode,
                    "to": normalized.interaction_mode,
                    "reason": "Command may require a terminal prompt or credential helper.",
                }
            )
        actions.append(normalized)
    if not changes:
        return plan, []
    return plan.model_copy(update={"actions": actions}), changes


def operator_action_requires_confirmation(
    action: OperatorAction,
    *,
    semantic_verb: str | None = None,
    policy_mode: str | None = None,
) -> bool:
    """Return whether one concrete operator action needs explicit approval."""

    if str(action.risk or "").strip().lower() in _APPROVAL_RISK_LEVELS:
        return True
    task = {"semantic_verb": semantic_verb} if semantic_verb else None
    return classify_action_effect(action, task=task, policy_mode=policy_mode).mutates_state


def operator_node_requires_confirmation(node: ActionNode) -> bool:
    """Return whether a standard operator-backed DAG node needs approval."""

    try:
        action = operator_action_from_node(node, node.arguments)
    except Exception:
        return True
    return operator_action_requires_confirmation(action, semantic_verb=node.semantic_verb)


def operator_plan_requires_confirmation(plan: OperatorPlan, *, policy_mode: str | None = None) -> bool:
    """Return whether any action in an operator plan needs approval."""

    task_by_id = {task.task_id: task for task in plan.tasks}
    return any(
        str(action.risk or "").strip().lower() in _APPROVAL_RISK_LEVELS
        or classify_action_effect(
            action,
            task=task_by_id.get(action.task_id),
            policy_mode=policy_mode,
        ).mutates_state
        for action in plan.actions
    )


def operator_plan_from_nodes(nodes: list[ActionNode]) -> OperatorPlan:
    """Build a validation plan from operator ActionDAG nodes."""

    task_to_node_id = {node.task_id: node.id for node in nodes}
    actions = [
        operator_action_from_node(node, node.arguments, task_to_node_id=task_to_node_id)
        for node in nodes
    ]
    action_ids = {action.action_id for action in actions}
    actions = [
        action.model_copy(
            update={
                "depends_on": [
                    dependency for dependency in action.depends_on if dependency in action_ids
                ]
            }
        )
        for action in actions
    ]
    return OperatorPlan(
        summary="Validate operator-backed ActionDAG nodes.",
        tasks=[
            OperatorTask(
                task_id=node.task_id,
                goal=node.description,
                semantic_verb=node.semantic_verb,
                object_type=None,
                dependencies=[],
                reason=node.description,
            )
            for node in nodes
        ],
        actions=actions,
        dependencies=[],
        expected_outputs=[],
        assumptions=[],
        confidence=1.0,
    )


__all__ = [
    "node_id_for_operator_source",
    "normalize_operator_input_bindings",
    "normalize_operator_action_interaction",
    "normalize_operator_plan_interactions",
    "operator_action_from_node",
    "operator_action_requires_confirmation",
    "operator_kind_for_capability",
    "operator_node_requires_confirmation",
    "operator_plan_requires_confirmation",
    "operator_plan_from_nodes",
    "python_action_command",
    "shell_command_looks_mutating",
    "shell_command_may_prompt",
    "terminal_prompt_text_may_prompt",
]
