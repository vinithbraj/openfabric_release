from __future__ import annotations

from pathlib import Path

from agent_runtime.plan_cache import (
    AgentPlanCacheStore,
    PlanCacheLookupContext,
    PlanCacheWrite,
    normalize_cache_text_shape,
    redact_and_clip,
    request_signature,
)


def test_plan_cache_store_persists_retrieves_and_clears(tmp_path: Path) -> None:
    store = AgentPlanCacheStore(tmp_path / "cache.db")
    store.upsert_entry(
        PlanCacheWrite(
            prompt="push current branch to origin",
            mode="Agent operator",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="mutation_succeeded",
            tool_type="git",
            intent_type="fresh",
            tags=["git", "push", "origin"],
            plan={"tasks": [], "actions": []},
            status="success",
        )
    )

    reopened = AgentPlanCacheStore(tmp_path / "cache.db")
    candidates = reopened.retrieve(
        PlanCacheLookupContext(
            prompt="push current branch to origin",
            mode="Agent operator",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="mutation_succeeded",
            tool_type="git",
            intent_type="fresh",
            tags=["git", "push", "origin"],
        )
    )

    assert len(candidates) == 1
    assert candidates[0].entry.tool_type == "git"
    assert reopened.stats().total_entries == 1
    assert reopened.clear().total_entries == 0


def test_plan_cache_store_retrieves_exact_streaming_step_tree(tmp_path: Path) -> None:
    store = AgentPlanCacheStore(tmp_path / "cache.db")
    exact_key = "exact-step-key"
    direct_plan = {
        "schema_version": 1,
        "tasks": [{"task_ref": "t1", "goal": "Stage all changes"}],
        "actions": [
            {"action_ref": "a1", "task_ref": "t1", "kind": "shell_command", "command": "git status"},
            {
                "action_ref": "a2",
                "task_ref": "t1",
                "kind": "shell_command",
                "command": "git add --all",
                "depends_on": ["a1"],
            },
        ],
    }
    entry = store.upsert_entry(
        PlanCacheWrite(
            prompt="Streaming step 1: Stage all changes",
            mode="Conversational",
            model_name="fake-operator",
            tool_type="git.repository",
            tags=["git", "stage"],
            exact_step_key=exact_key,
            exact_step_prompt_excerpt="Streaming step 1: Stage all changes",
            intent_snapshot={"step_description": "Stage all changes"},
            intent_signature="intent-a",
            direct_plan=direct_plan,
            status="success",
        )
    )

    candidates = store.retrieve_exact_step_tree(exact_key, intent_signature="intent-a")

    assert len(candidates) == 1
    assert candidates[0].entry.cache_id == entry.cache_id
    assert candidates[0].entry.direct_plan == direct_plan
    assert candidates[0].entry.intent_snapshot == {"step_description": "Stage all changes"}
    assert store.retrieve_exact_step_tree(exact_key, intent_signature="intent-b") == []
    by_intent = store.retrieve_step_tree_by_intent_signature("intent-a")
    assert len(by_intent) == 1
    assert by_intent[0].entry.cache_id == entry.cache_id


def test_plan_cache_streaming_tree_uses_distinct_signature_from_generic_failures(
    tmp_path: Path,
) -> None:
    store = AgentPlanCacheStore(tmp_path / "cache.db")
    store.upsert_entry(
        PlanCacheWrite(
            prompt="stage all and commit",
            mode="Conversational",
            model_name="fake-operator",
            plan={"tasks": [], "actions": []},
            records=[{"status": "error", "error": "bad generated commit command"}],
            status="failure",
        )
    )
    direct_plan = {
        "schema_version": 1,
        "tasks": [{"task_ref": "t1", "goal": "Stage all changes"}],
        "actions": [
            {"action_ref": "a1", "task_ref": "t1", "kind": "shell_command", "command": "git status"}
        ],
    }
    entry = store.upsert_entry(
        PlanCacheWrite(
            prompt="stage all and commit",
            mode="Conversational",
            model_name="fake-operator",
            exact_step_key="exact-stage",
            exact_step_prompt_excerpt="Streaming step 1: Stage all changes",
            intent_snapshot={"step_description": "Stage all changes"},
            intent_signature="intent-stage",
            direct_plan=direct_plan,
            status="success",
        )
    )

    assert entry.failure_count == 0
    assert store.stats().total_entries == 2
    assert store.retrieve_exact_step_tree("exact-stage", intent_signature="intent-stage")


def test_plan_cache_retrieval_requires_compatible_tool_shape(tmp_path: Path) -> None:
    store = AgentPlanCacheStore(tmp_path / "cache.db")
    store.upsert_entry(
        PlanCacheWrite(
            prompt="push all changes upstream",
            mode="Agent operator",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="mutation_succeeded",
            tool_type="git",
            intent_type="fresh",
            tags=["git", "push"],
            plan={"tasks": [], "actions": []},
            status="success",
        )
    )

    docker_candidates = store.retrieve(
        PlanCacheLookupContext(
            prompt="list docker images and calculate total size in GB",
            mode="Agent operator",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="computed_answer",
            tool_type="docker",
            intent_type="fresh",
            tags=["docker", "images", "size"],
        )
    )

    assert docker_candidates == []


def test_plan_cache_normalizes_dynamic_arguments_for_reuse(tmp_path: Path) -> None:
    store = AgentPlanCacheStore(tmp_path / "cache.db")
    store.upsert_entry(
        PlanCacheWrite(
            prompt='run the deployment tool with argument "release candidate 123" from /tmp/build-a',
            mode="Agent operator",
            model_name="qwen3-coder-30b-a3b-awq",
            tool_type="shell",
            tags=["deploy", "tool", "argument"],
            plan={
                "tasks": [],
                "actions": [
                    {
                        "kind": "shell_command",
                        "command": "deploy --arg \"$OF_INPUT_ARG\"",
                    }
                ],
            },
            status="success",
        )
    )

    candidates = store.retrieve(
        PlanCacheLookupContext(
            prompt='run the deployment tool with argument "hotfix 987" from /var/tmp/build-b',
            mode="Agent operator",
            model_name="qwen3-coder-30b-a3b-awq",
            tool_type="shell",
            tags=["deploy", "tool", "argument"],
        )
    )

    assert len(candidates) == 1
    assert candidates[0].score == 1.0


def test_plan_cache_request_signature_ignores_literal_payload_values() -> None:
    first = PlanCacheWrite(
        prompt='stage all changes and commit using description "first long message with `inline` text"',
        mode="Agent operator",
        model_name="qwen3-coder-30b-a3b-awq",
        tool_type="git",
        tags=["stage", "commit"],
        status="success",
    )
    second = PlanCacheLookupContext(
        prompt='stage all changes and commit using description "second unrelated message"',
        mode="Agent operator",
        model_name="qwen3-coder-30b-a3b-awq",
        tool_type="git",
        tags=["stage", "commit"],
    )

    assert "first" not in normalize_cache_text_shape(first.prompt)
    assert "second" not in normalize_cache_text_shape(second.prompt)
    assert request_signature(first) == request_signature(second)


def test_plan_cache_use_count_increments_only_when_marked_used(tmp_path: Path) -> None:
    store = AgentPlanCacheStore(tmp_path / "cache.db")
    entry = store.upsert_entry(
        PlanCacheWrite(
            prompt="git status",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            tool_type="git",
            tags=["git", "status"],
            plan={"tasks": [], "actions": []},
            status="success",
        )
    )

    lookup = PlanCacheLookupContext(
        prompt="git status",
        mode="Conversational",
        model_name="qwen3-coder-30b-a3b-awq",
        tool_type="git",
        tags=["git", "status"],
    )
    assert store.retrieve(lookup)[0].entry.use_count == 0
    store.mark_used(entry.cache_id)
    assert store.get_entry(entry.cache_id).use_count == 1  # type: ignore[union-attr]


def test_plan_cache_failure_write_does_not_poison_successful_skeleton(tmp_path: Path) -> None:
    store = AgentPlanCacheStore(tmp_path / "cache.db")
    entry = store.upsert_entry(
        PlanCacheWrite(
            prompt="list docker images and calculate total size",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="computed_answer",
            tool_type="docker",
            tags=["docker", "size"],
            plan={"tasks": [], "actions": [{"kind": "shell_command", "command": "docker images"}]},
            status="success",
        )
    )

    updated = store.upsert_entry(
        PlanCacheWrite(
            prompt="list docker images and calculate total size",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="computed_answer",
            tool_type="docker",
            tags=["docker", "size"],
            plan={"tasks": [], "actions": [{"kind": "python_action", "command": "bad"}]},
            status="failure",
            failure_category="python_parse_error",
        )
    )

    assert updated.cache_id == entry.cache_id
    assert updated.status == "success"
    assert updated.failure_count == 1
    assert updated.plan == entry.plan
    candidates = store.retrieve(
        PlanCacheLookupContext(
            prompt="list docker images and calculate total size",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="computed_answer",
            tool_type="docker",
            tags=["docker", "size"],
        )
    )
    assert candidates == []


def test_plan_cache_skips_concrete_python_shaped_entries_for_application(tmp_path: Path) -> None:
    store = AgentPlanCacheStore(tmp_path / "cache.db")
    store.upsert_entry(
        PlanCacheWrite(
            prompt="calculate docker image total size",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="computed_answer",
            tool_type="docker",
            tags=["docker", "size"],
            plan={
                "tasks": [],
                "actions": [
                    {
                        "kind": "python_action",
                        "summary": "Calculate a total.",
                        "code": "def main(inputs):\n    return 0",
                        "defer_code_generation": False,
                    }
                ],
            },
            status="success",
        )
    )

    assert (
        store.retrieve(
            PlanCacheLookupContext(
                prompt="calculate docker image total size",
                mode="Conversational",
                model_name="qwen3-coder-30b-a3b-awq",
                task_type="computed_answer",
                tool_type="docker",
                tags=["docker", "size"],
            )
        )
        == []
    )


def test_plan_cache_allows_deferred_python_skeleton_for_application(tmp_path: Path) -> None:
    store = AgentPlanCacheStore(tmp_path / "cache.db")
    store.upsert_entry(
        PlanCacheWrite(
            prompt="calculate docker image total size",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="computed_answer",
            tool_type="docker",
            tags=["docker", "size"],
            plan={
                "tasks": [],
                "actions": [
                    {"kind": "shell_command", "command": "docker images"},
                    {
                        "kind": "python_action",
                        "summary": "Calculate a total after live rows are available.",
                        "code": None,
                        "defer_code_generation": True,
                    },
                ],
            },
            status="success",
        )
    )

    assert store.retrieve(
        PlanCacheLookupContext(
            prompt="calculate docker image total size",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="computed_answer",
            tool_type="docker",
            tags=["docker", "size"],
        )
    )


def test_plan_cache_redacts_secret_shaped_payloads() -> None:
    redacted = redact_and_clip("password=supersecret\nAuthorization: Bearer abc123")

    assert "supersecret" not in redacted
    assert "abc123" not in redacted
    assert "[redacted]" in redacted
