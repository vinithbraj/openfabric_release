"""Generic LLM-authored operator capability manifests."""

from __future__ import annotations

from typing import Any

from agent_runtime.capabilities.base import BaseCapability
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.core.errors import ValidationError
from agent_runtime.core.types import ALLOWED_SEMANTIC_VERBS
from agent_runtime.operator.models import OperatorAction


OPERATOR_SHELL_SEMANTIC_VERBS = [verb for verb in ALLOWED_SEMANTIC_VERBS if verb != "unknown"]
OPERATOR_TRANSFORM_SEMANTIC_VERBS = [
    "search",
    "transform",
    "analyze",
    "calculate",
    "count",
    "filter",
    "sort",
    "summarize",
    "compare",
    "render",
]


OPERATOR_OBJECT_TYPES = [
    "file",
    "files",
    "filesystem.file",
    "filesystem.directory",
    "filesystem.path",
    "directory",
    "path",
    "shell",
    "command",
    "process",
    "container",
    "containers",
    "container_name",
    "container_names",
    "docker",
    "docker.container",
    "docker.containers",
    "docker_container",
    "docker_containers",
    "docker.image",
    "docker.images",
    "docker_image",
    "docker_images",
    "network",
    "network.interface",
    "network.address",
    "ip",
    "ip_address",
    "host",
    "hostname",
    "machine",
    "git",
    "repo",
    "repository",
    "source_control",
    "version_control",
    "git.repository",
    "git.branch",
    "git.commit",
    "git.status",
    "git.diff",
    "git.remote",
    "git.log",
    "current_system_state",
    "system.memory",
    "system.cpu",
    "system.disk",
    "system.uptime",
    "system.environment",
    "data",
    "records",
    "table",
    "json",
    "text",
    "markdown",
    "report",
]

def _operator_argument_schema(*, command: bool, python_action: bool = False) -> dict[str, Any]:
    payload_key = "command" if command else "code"
    if command:
        payload_description = (
            "Concrete shell command string. When output will be parsed, filtered, "
            "counted, transformed, joined, or used to select targets, prefer "
            "machine-readable CLI output such as --format, --json, --porcelain, "
            "-o/--output, or explicit columns when supported. For destructive "
            "commands, select targets by stable ids or exact names from structured output. "
            "For yes/no existence checks, print an explicit found/not-found answer and "
            "exit 0 for both outcomes instead of letting grep no-match exit 1 fail the run. "
            "For mutation verification, prove the exact requested postcondition with fresh, "
            "stable state evidence and avoid grepping narrative human prose when structured "
            "or machine-readable output is available."
        )
    elif python_action:
        payload_description = (
            "Complete Python source string. Must define a top-level def main(inputs): "
            "function. Use this when one Python program should run commands, inspect files, "
            "manipulate data, and print or return the final answer through the gateway."
        )
    else:
        payload_description = (
            "Complete Python source string. Must define a top-level "
            "def transform(inputs): function that returns the final transformed value. "
            "The code must parse as valid Python."
        )
    schema = {
        payload_key: {
            "type": "string",
            "dataflow_bindable": False,
            "description": payload_description,
        },
        "cwd": {"type": "string", "default": ".", "dataflow_bindable": False},
        "inputs": {
            "type": "object",
            "dataflow_bindable": False,
            "description": (
                "Literal operator inputs only. Upstream action outputs must be declared "
                "through input_bindings, not by binding a producer output into inputs. "
                "For shell_command actions, each literal input is exposed as an OF_INPUT_* "
                "environment variable and must be referenced explicitly by command."
            ),
        },
        "input_bindings": {
            "type": "array",
            "dataflow_bindable": False,
            "description": "Operator-native upstream bindings resolved by the operator executor.",
        },
        "declared_output_shape": {
            "type": "string",
            "enum": ["text", "json", "table", "file", "status", "interactive_stream"],
            "default": "text",
            "dataflow_bindable": False,
        },
        "defer_code_generation": {
            "type": "boolean",
            "default": False,
            "dataflow_bindable": False,
            "description": (
                "For Python actions with upstream input_bindings, set true and omit code "
                "when the runtime should generate Python after it sees the real upstream shape."
            ),
        },
        "allow_zero_result": {
            "type": "boolean",
            "default": False,
            "dataflow_bindable": False,
            "description": "Set true only when a zero result is semantically valid.",
        },
        "interaction_mode": {
            "type": "string",
            "enum": ["non_interactive", "may_prompt", "long_running"],
            "default": "non_interactive",
            "dataflow_bindable": False,
            "description": (
                "Use non_interactive for normal captured actions. Use may_prompt only for "
                "finite shell commands that may require terminal input such as a passphrase, "
                "password, confirmation prompt, or controlling TTY. Use long_running for "
                "terminal-owned commands intended to keep running."
            ),
        },
        "risk": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
            "default": "medium",
            "dataflow_bindable": False,
        },
        "timeout_seconds": {"type": "integer", "dataflow_bindable": False},
        "reason": {"type": "string", "dataflow_bindable": False},
    }
    if command:
        schema["execution_mode"] = {
            "type": "string",
            "enum": ["captured", "terminal_detached"],
            "default": "captured",
            "dataflow_bindable": False,
            "description": (
                "Use captured for normal commands whose stdout/stderr/exit_code are needed. "
                "Use terminal_detached only for long-running interactive terminal commands "
                "such as watch, tail -f, htop, or REPLs; detached actions do not produce "
                "dataflow output for later actions."
            ),
        }
    return schema


class _OperatorCapability(BaseCapability):
    """Common validation for standard-pipeline operator actions."""

    operator_kind: str

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = super().validate_arguments(arguments)
        payload.setdefault("cwd", ".")
        payload.setdefault("inputs", {})
        payload.setdefault("input_bindings", [])
        payload.setdefault("declared_output_shape", "text")
        payload.setdefault("risk", "medium")
        payload.setdefault("reason", "LLM-authored operator action.")

        action_payload = {
            "action_id": "validation",
            "task_id": "validation",
            "kind": self.operator_kind,
            "command": payload.get("command") if self.operator_kind == "shell_command" else None,
            "code": payload.get("code") if self.operator_kind in {"python_transform", "python_action"} else None,
            "cwd": payload.get("cwd", "."),
            "execution_mode": payload.get("execution_mode", "captured"),
            "interaction_mode": payload.get("interaction_mode", "non_interactive"),
            "inputs": payload.get("inputs") or {},
            "input_bindings": payload.get("input_bindings") or [],
            "declared_output_shape": payload.get("declared_output_shape", "text"),
            "defer_code_generation": bool(payload.get("defer_code_generation", False)),
            "allow_zero_result": bool(payload.get("allow_zero_result", False)),
            "risk": payload.get("risk", "medium"),
            "timeout_seconds": payload.get("timeout_seconds"),
            "reason": payload.get("reason") or "LLM-authored operator action.",
            "depends_on": [],
        }
        try:
            action = OperatorAction.model_validate(action_payload)
        except Exception as exc:
            raise ValidationError(str(exc)) from exc
        payload.update(
            {
                "cwd": action.cwd,
                "execution_mode": action.execution_mode,
                "interaction_mode": action.interaction_mode,
                "inputs": dict(action.inputs or {}),
                "input_bindings": [
                    binding.model_dump(mode="json") for binding in action.input_bindings
                ],
                "declared_output_shape": action.declared_output_shape,
                "defer_code_generation": action.defer_code_generation,
                "allow_zero_result": action.allow_zero_result,
                "risk": action.risk,
                "timeout_seconds": action.timeout_seconds,
                "reason": action.reason,
            }
        )
        if self.operator_kind == "shell_command":
            payload["command"] = action.command
            payload.pop("code", None)
        else:
            payload["code"] = action.code
            payload.pop("command", None)
        return payload


class OperatorShellCommandCapability(_OperatorCapability):
    """Execute an LLM-authored shell command through the gateway after approval."""

    operator_kind = "shell_command"
    manifest = CapabilityManifest(
        capability_id="operator.shell_command",
        domain="operator",
        operation_id="shell_command",
        name="Conversational Shell Command",
        description=(
            "Run a concrete shell command authored by the LLM for ordinary workspace, "
            "filesystem, Git/source-control, local network inspection, current machine "
            "state, system, and command-line tasks. Runtime validation and approval are "
            "required before execution."
        ),
        semantic_verbs=list(OPERATOR_SHELL_SEMANTIC_VERBS),
        object_types=list(OPERATOR_OBJECT_TYPES),
        semantic_tags=[
            "ordinary_task",
            "llm_operator",
            "shell_command",
            "current_machine",
            "local_system_state",
            "docker",
            "container",
            "container_inspection",
            "network_inspection",
            "ip_address",
            "git",
            "repository",
            "source_control",
            "version_control",
        ],
        argument_schema=_operator_argument_schema(command=True),
        required_arguments=["command"],
        optional_arguments=[
            "cwd",
            "execution_mode",
            "interaction_mode",
            "inputs",
            "input_bindings",
            "declared_output_shape",
            "risk",
            "timeout_seconds",
            "reason",
        ],
        output_schema={
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
            "exit_code": {"type": "integer"},
            "output": {},
        },
        output_object_types=list(OPERATOR_OBJECT_TYPES),
        output_fields=["stdout", "stderr", "exit_code", "output"],
        output_affordances=["operator.stdout", "operator.stderr", "operator.exit_code"],
        side_effect_type="operator_action",
        execution_backend="operator",
        backend_operation="operator.shell_command",
        risk_level="high",
        read_only=False,
        mutates_state=True,
        requires_confirmation=True,
        examples=[
            {"prompt": "list all *.txt files", "arguments": {"command": "find . -name '*.txt' -type f"}},
            {"prompt": "read README.md", "arguments": {"command": "sed -n '1,200p' README.md"}},
            {
                "prompt": "read the first file found by name",
                "arguments": {
                    "command": "target=$(find . -name NAME -type f | sort | head -n 1); test -n \"$target\" && sed -n '1,200p' \"$target\""
                },
            },
            {
                "prompt": "what is the IP address of this machine?",
                "arguments": {"command": "hostname -I || ip -o addr show"},
            },
            {
                "prompt": "show local network interfaces",
                "arguments": {"command": "ip -o addr show"},
            },
            {"prompt": "show git status", "arguments": {"command": "git status --short"}},
            {"prompt": "what branch is this repository on?", "arguments": {"command": "git branch --show-current"}},
            {"prompt": "show the latest git commit", "arguments": {"command": "git log -1 --oneline"}},
            {"prompt": "list changed files in this repo", "arguments": {"command": "git diff --name-only"}},
            {
                "prompt": "check whether a Docker container named NAME is running",
                "arguments": {
                    "command": "docker inspect --format '{{.Name}} {{.State.Status}} {{.State.StartedAt}}' NAME"
                },
            },
            {
                "prompt": "tell me if any Docker container name contains NAME",
                "arguments": {
                    "command": "matches=$(docker ps -a --format '{{.Names}}\\t{{.Status}}' | grep -iF NAME || true); if test -n \"$matches\"; then printf 'Found container(s):\\n%s\\n' \"$matches\"; else echo 'No container name contains NAME'; fi"
                },
            },
            {
                "prompt": "get the Docker container start timestamp",
                "arguments": {"command": "docker inspect --format '{{.State.StartedAt}}' NAME"},
            },
            {
                "prompt": "show Docker image disk usage",
                "arguments": {"command": "docker system df -v --format json"},
            },
        ],
        safety_notes=[
            "Commands are authored by the LLM and require approval.",
            "Commands execute through the gateway within the workspace cwd.",
        ],
    )


class OperatorPythonTransformCapability(_OperatorCapability):
    """Run an LLM-authored trusted Python transform after approval."""

    operator_kind = "python_transform"
    manifest = CapabilityManifest(
        capability_id="operator.python_transform",
        domain="operator",
        operation_id="python_transform",
        name="Conversational Python Transform",
        description=(
            "Run trusted Python code authored by the LLM to transform declared "
            "structured inputs from previous operator actions. Use this for ordinary "
            "intermediate data-shaping tasks such as extracting, filtering, selecting, "
            "sorting, summarizing, or formatting values from prior command output."
        ),
        semantic_verbs=list(OPERATOR_TRANSFORM_SEMANTIC_VERBS),
        object_types=list(OPERATOR_OBJECT_TYPES),
        semantic_tags=[
            "ordinary_task",
            "llm_operator",
            "python_transform",
            "data_shaping",
            "extract",
            "filter",
            "select",
            "sort",
            "format",
            "datetime",
            "elapsed_time",
            "uptime_days",
            "git",
            "repository",
            "source_control",
            "version_control",
        ],
        argument_schema=_operator_argument_schema(command=False),
        required_arguments=[],
        optional_arguments=[
            "code",
            "cwd",
            "interaction_mode",
            "inputs",
            "input_bindings",
            "declared_output_shape",
            "risk",
            "timeout_seconds",
            "reason",
        ],
        output_schema={"output": {}},
        output_object_types=list(OPERATOR_OBJECT_TYPES),
        output_fields=["output"],
        output_affordances=["operator.output"],
        side_effect_type=None,
        execution_backend="operator",
        backend_operation="operator.python_transform",
        risk_level="medium",
        read_only=True,
        mutates_state=False,
        requires_confirmation=True,
        examples=[
            {
                "prompt": "convert shell output to a markdown report",
                "arguments": {
                    "code": "def transform(inputs):\n    return str(inputs['stdout'])",
                    "input_bindings": [
                        {
                            "input_name": "stdout",
                            "source_action_id": "task_1",
                            "source_field": "stdout",
                        }
                    ],
                },
            },
            {
                "prompt": "extract branch names from git branch output",
                "arguments": {
                    "code": (
                        "def transform(inputs):\n"
                        "    lines = str(inputs['branches']).splitlines()\n"
                        "    return [line.strip().lstrip('* ').strip() for line in lines if line.strip()]"
                    ),
                    "input_bindings": [
                        {
                            "input_name": "branches",
                            "source_action_id": "task_2",
                            "source_field": "stdout",
                        }
                    ],
                    "declared_output_shape": "json",
                },
            },
            {
                "prompt": "select a branch containing distil from prior branch names",
                "arguments": {
                    "code": (
                        "def transform(inputs):\n"
                        "    branches = inputs['branches']\n"
                        "    matches = [branch for branch in branches if 'distil' in str(branch).lower()]\n"
                        "    return matches[0] if matches else ''"
                    ),
                    "input_bindings": [
                        {
                            "input_name": "branches",
                            "source_action_id": "task_3",
                            "source_field": "output",
                        }
                    ],
                    "declared_output_shape": "text",
                },
            },
            {
                "prompt": "calculate uptime in days from an ISO timestamp",
                "arguments": {
                    "code": (
                        "def transform(inputs):\n"
                        "    import datetime\n"
                        "    started = str(inputs['started_at']).strip()\n"
                        "    start_dt = datetime.datetime.fromisoformat(started.replace('Z', '+00:00'))\n"
                        "    now = datetime.datetime.now(datetime.timezone.utc)\n"
                        "    return (now - start_dt).days"
                    ),
                    "input_bindings": [
                        {
                            "input_name": "started_at",
                            "source_action_id": "task_1",
                            "source_field": "stdout",
                        }
                    ],
                    "declared_output_shape": "json",
                },
            },
        ],
        safety_notes=[
            "Code must define transform(inputs).",
            "Imports, file access, network access, subprocesses, and raw payload stores are blocked.",
        ],
    )


class OperatorPythonActionCapability(_OperatorCapability):
    """Execute an LLM-authored Python program through the gateway after approval."""

    operator_kind = "python_action"
    manifest = CapabilityManifest(
        capability_id="operator.python_action",
        domain="operator",
        operation_id="python_action",
        name="Gateway Python Action",
        description=(
            "Run a self-contained Python program authored by the LLM through the gateway. "
            "Use this for ordinary tasks that are not best handled by a single shell command: "
            "Python may run subprocesses, inspect files, aggregate data, and print or return "
            "the final answer in one action."
        ),
        semantic_verbs=list(OPERATOR_SHELL_SEMANTIC_VERBS),
        object_types=list(OPERATOR_OBJECT_TYPES),
        semantic_tags=[
            "ordinary_task",
            "llm_operator",
            "python_action",
            "gateway_python",
            "subprocess",
            "filesystem",
            "data_aggregation",
            "calculation",
            "docker",
            "git",
            "system",
            "report",
        ],
        argument_schema=_operator_argument_schema(command=False, python_action=True),
        required_arguments=[],
        optional_arguments=[
            "code",
            "cwd",
            "interaction_mode",
            "inputs",
            "input_bindings",
            "declared_output_shape",
            "risk",
            "timeout_seconds",
            "reason",
        ],
        output_schema={
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
            "exit_code": {"type": "integer"},
            "output": {},
        },
        output_object_types=list(OPERATOR_OBJECT_TYPES),
        output_fields=["stdout", "stderr", "exit_code", "output"],
        output_affordances=["operator.stdout", "operator.stderr", "operator.exit_code"],
        side_effect_type="operator_action",
        execution_backend="operator",
        backend_operation="operator.python_action",
        risk_level="high",
        read_only=False,
        mutates_state=True,
        requires_confirmation=True,
        examples=[
            {
                "prompt": "list all txt files and give me the total count and size",
                "arguments": {
                    "code": (
                        "def main(inputs):\n"
                        "    import os\n"
                        "    total = 0\n"
                        "    count = 0\n"
                        "    for root, dirs, files in os.walk('.'):\n"
                        "        for name in files:\n"
                        "            if name.endswith('.txt'):\n"
                        "                path = os.path.join(root, name)\n"
                        "                try:\n"
                        "                    total += os.path.getsize(path)\n"
                        "                    count += 1\n"
                        "                except OSError:\n"
                        "                    pass\n"
                        "    return f'{count} txt files, total size {total / (1024 * 1024):.2f} MB'"
                    )
                },
            },
            {
                "prompt": "calculate total Docker image size",
                "arguments": {
                    "code": (
                        "def main(inputs):\n"
                        "    import subprocess\n"
                        "    import re\n"
                        "    out = subprocess.check_output(['docker', 'images', '--format', '{{.Size}}'], text=True)\n"
                        "    total_mb = 0.0\n"
                        "    for raw in out.splitlines():\n"
                        "        match = re.match(r'([0-9.]+)\\s*([KMG]B)', raw.strip(), re.I)\n"
                        "        if not match:\n"
                        "            continue\n"
                        "        value = float(match.group(1))\n"
                        "        unit = match.group(2).upper()\n"
                        "        total_mb += value / 1024 if unit == 'KB' else value * 1024 if unit == 'GB' else value\n"
                        "    return f'Total Docker image size: {total_mb / 1024:.2f} GB'"
                    )
                },
            },
        ],
        safety_notes=[
            "Code must define main(inputs).",
            "Python actions run through the gateway with cwd, timeout, cancellation, streaming, and approval.",
            "Use shell_command instead when a single shell command can fully answer the request.",
        ],
    )


__all__ = [
    "OperatorPythonActionCapability",
    "OperatorPythonTransformCapability",
    "OperatorShellCommandCapability",
]
