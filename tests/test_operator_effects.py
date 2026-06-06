from __future__ import annotations

import pytest

from agent_runtime.operator.effects import (
    classify_action_effect,
    classify_python_effect,
    classify_shell_effect,
)
from agent_runtime.operator.models import OperatorAction


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "find . -type f",
        "git status --short",
        "docker ps -a",
        "systemctl status example.service",
        "cat pyproject.toml",
    ],
)
def test_classify_shell_effect_known_read_only(command: str) -> None:
    effect = classify_shell_effect(command)

    assert effect.read_only is True
    assert effect.mutates_state is False


@pytest.mark.parametrize(
    "command",
    [
        "cp source.txt dest.txt",
        "mv old.txt new.txt",
        "rsync -a source/ dest/",
        "printf hi > out.txt",
        "git add .",
        "docker compose up -d",
        "systemctl restart example.service",
        "pip install sample-package",
        "update users set active = true",
    ],
)
def test_classify_shell_effect_known_mutating(command: str) -> None:
    effect = classify_shell_effect(command)

    assert effect.mutates_state is True
    assert effect.read_only is False


def test_classify_shell_effect_ambiguous_command_in_mutating_task_is_plausible_mutation() -> None:
    effect = classify_shell_effect(
        "custom-sync-tool --target dest",
        task={
            "semantic_verb": "update",
            "description": "Transfer files to destination",
            "requires_confirmation": True,
        },
    )

    assert effect.mutates_state is True
    assert effect.effect_type == "unknown_mutation"
    assert effect.confidence == "low"


def test_classify_shell_effect_ambiguous_command_in_read_only_task_is_not_mutating() -> None:
    effect = classify_shell_effect(
        "custom-inspect-tool --target dest",
        task={
            "semantic_verb": "list",
            "description": "List destination state",
            "requires_confirmation": False,
            "risk_level": "low",
        },
    )

    assert effect.read_only is True
    assert effect.mutates_state is False


def test_classify_shell_effect_read_only_command_stays_read_only_in_mutating_task() -> None:
    effect = classify_shell_effect(
        "git status --short",
        task={
            "semantic_verb": "update",
            "description": "Stage files for commit",
            "requires_confirmation": True,
        },
    )

    assert effect.read_only is True
    assert effect.mutates_state is False


def test_classify_shell_effect_does_not_treat_subcommand_env_as_read_only() -> None:
    bare = classify_shell_effect("conda env remove --name poop1")
    mutating_task = classify_shell_effect(
        "conda env remove --name poop1",
        task={
            "semantic_verb": "delete",
            "description": "Remove a conda environment",
            "requires_confirmation": True,
            "risk_level": "medium",
        },
    )

    assert bare.read_only is False
    assert bare.mutates_state is False
    assert mutating_task.mutates_state is True
    assert mutating_task.effect_type == "unknown_mutation"


def test_classify_shell_effect_read_only_pipeline_command_still_matches() -> None:
    effect = classify_shell_effect("printf '%s\\n' hello | wc -l")

    assert effect.read_only is True
    assert effect.mutates_state is False


def test_classify_python_effect_detects_read_only_and_mutating_code() -> None:
    read_only = classify_python_effect(
        "def main(inputs):\n"
        "    import subprocess\n"
        "    return subprocess.run(['git', 'status'], capture_output=True, text=True).stdout"
    )
    mutating = classify_python_effect(
        "def main(inputs):\n"
        "    from pathlib import Path\n"
        "    Path('out.txt').write_text('hi')\n"
        "    return 'ok'"
    )

    assert read_only.read_only is True
    assert read_only.mutates_state is False
    assert mutating.mutates_state is True


def test_classify_action_effect_uses_current_streaming_task_for_ambiguous_shell() -> None:
    action = OperatorAction.model_validate(
        {
            "action_id": "action_1",
            "task_id": "task_1",
            "kind": "shell_command",
            "command": "custom-sync-tool --target dest",
            "cwd": ".",
            "inputs": {},
            "declared_output_shape": "text",
            "risk": "low",
            "timeout_seconds": 5,
            "reason": "Synchronize destination state.",
            "depends_on": [],
        }
    )

    effect = classify_action_effect(
        action,
        task={"semantic_verb": "execute"},
        current_task={
            "semantic_verb": "update",
            "description": "Transfer files to destination",
            "requires_confirmation": True,
        },
    )

    assert effect.mutates_state is True
    assert effect.effect_type == "unknown_mutation"


def test_llm_effect_mode_does_not_use_shell_command_allowlist_as_truth() -> None:
    effect = classify_shell_effect("git add .", policy_mode="llm")

    assert effect.mutates_state is False
    assert effect.read_only is False
    assert effect.effect_type == "unknown"


def test_llm_effect_mode_uses_declared_action_effect() -> None:
    action = OperatorAction.model_validate(
        {
            "action_id": "action_1",
            "task_id": "task_1",
            "kind": "shell_command",
            "command": "custom-env-tool poop1",
            "cwd": ".",
            "inputs": {},
            "declared_output_shape": "text",
            "risk": "low",
            "timeout_seconds": 5,
            "reason": "Create a local environment.",
            "depends_on": [],
            "effect_intent": "mutates_state",
            "effect_confidence": 0.82,
            "effect_summary": "Creates a local environment.",
        }
    )

    effect = classify_action_effect(action, policy_mode="llm")

    assert effect.mutates_state is True
    assert effect.source == "action_effect_intent"
    assert effect.confidence == "high"
