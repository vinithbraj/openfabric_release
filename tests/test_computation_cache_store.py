from __future__ import annotations

from pathlib import Path

from agent_runtime.computation_cache import (
    AgentComputationCacheStore,
    ComputationCacheLookupContext,
    ComputationCacheWrite,
    exact_step_key,
    input_profile,
    input_signature,
)


def _profile_for(text: str) -> tuple[dict[str, object], str]:
    profile = input_profile({"stdout": text})
    return profile, input_signature(profile)


def test_computation_cache_store_persists_retrieves_and_clears(tmp_path: Path) -> None:
    profile, signature = _profile_for("repo\tlatest\t633MB\nvllm\tlatest\t32.2GB\n")
    store = AgentComputationCacheStore(tmp_path / "cache.db")
    store.upsert_entry(
        ComputationCacheWrite(
            prompt="list docker images and calculate total size",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="computed_answer",
            tool_type="docker",
            tags=["docker", "images", "size"],
            action_kind="python_action",
            action_reason="Calculate total image size.",
            input_profile=profile,
            input_signature=signature,
            code_template="def main(inputs):\n    return '32.83 GB'",
            status="success",
        )
    )

    reopened = AgentComputationCacheStore(tmp_path / "cache.db")
    candidates = reopened.retrieve(
        ComputationCacheLookupContext(
            prompt="list docker images and calculate total size",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            task_type="computed_answer",
            tool_type="docker",
            tags=["docker", "images", "size"],
            action_kind="python_action",
            action_reason="Calculate total image size.",
            input_profile=profile,
            input_signature=signature,
        )
    )

    assert len(candidates) == 1
    assert candidates[0].entry.tool_type == "docker"
    assert reopened.stats().total_entries == 1
    assert reopened.clear().total_entries == 0


def test_computation_cache_retrieval_requires_input_shape_match(tmp_path: Path) -> None:
    docker_profile, docker_signature = _profile_for("repo\tlatest\t633MB\n")
    git_profile, git_signature = _profile_for("M file.py\nA other.py\n")
    store = AgentComputationCacheStore(tmp_path / "cache.db")
    store.upsert_entry(
        ComputationCacheWrite(
            prompt="calculate docker image total",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            tool_type="docker",
            tags=["docker", "size"],
            action_kind="python_action",
            action_reason="Calculate image total.",
            input_profile=docker_profile,
            input_signature=docker_signature,
            code_template="def main(inputs):\n    return '633 MB'",
            status="success",
        )
    )

    assert (
        store.retrieve(
            ComputationCacheLookupContext(
                prompt="calculate git status count",
                mode="Conversational",
                model_name="qwen3-coder-30b-a3b-awq",
                tool_type="git",
                tags=["git", "status"],
                action_kind="python_action",
                action_reason="Count changed files.",
                input_profile=git_profile,
                input_signature=git_signature,
            )
        )
        == []
    )


def test_computation_cache_failure_write_does_not_poison_successful_template(tmp_path: Path) -> None:
    profile, signature = _profile_for("repo\tlatest\t633MB\n")
    store = AgentComputationCacheStore(tmp_path / "cache.db")
    entry = store.upsert_entry(
        ComputationCacheWrite(
            prompt="calculate docker image total",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            tags=["docker", "size"],
            action_kind="python_action",
            action_reason="Calculate image total.",
            input_profile=profile,
            input_signature=signature,
            code_template="def main(inputs):\n    return '633 MB'",
            status="success",
        )
    )

    updated = store.upsert_entry(
        ComputationCacheWrite(
            prompt="calculate docker image total",
            mode="Conversational",
            model_name="qwen3-coder-30b-a3b-awq",
            tags=["docker", "size"],
            action_kind="python_action",
            action_reason="Calculate image total.",
            input_profile=profile,
            input_signature=signature,
            code_template="def main(inputs):\n    return '0.00 GB'",
            status="failure",
            failure_category="zero_like_output",
        )
    )

    assert updated.cache_id == entry.cache_id
    assert updated.status == "success"
    assert updated.failure_count == 1
    assert updated.code_template == entry.code_template
    assert (
        store.retrieve(
            ComputationCacheLookupContext(
                prompt="calculate docker image total",
                mode="Conversational",
                model_name="qwen3-coder-30b-a3b-awq",
                tags=["docker", "size"],
                action_kind="python_action",
                action_reason="Calculate image total.",
                input_profile=profile,
                input_signature=signature,
            )
        )
        == []
    )


def test_computation_cache_retrieves_exact_step_python_direct_action(tmp_path: Path) -> None:
    profile, signature = _profile_for("repo latest 2.43GB\nubuntu 24.04 119MB\n")
    store = AgentComputationCacheStore(tmp_path / "cache.db")
    key = exact_step_key("calculate total docker image size")
    entry = store.upsert_entry(
        ComputationCacheWrite(
            prompt="calculate total docker image size",
            action_kind="python_action",
            action_reason="Calculate total image size.",
            input_profile=profile,
            input_signature=signature,
            exact_step_key=key,
            exact_step_prompt_excerpt="calculate total docker image size",
            code_template="def main(inputs):\n    return {'total_gb': 2.549}",
            direct_action={
                "action_id": "action_cached",
                "task_id": "task_cached",
                "kind": "python_action",
                "code": "def main(inputs):\n    return {'total_gb': 2.549}",
                "reason": "Calculate total image size.",
            },
            status="success",
        )
    )

    candidates = store.retrieve_exact_step(
        key,
        action_kind="python_action",
        input_signature=signature,
    )

    assert len(candidates) == 1
    assert candidates[0].entry.cache_id == entry.cache_id
    assert candidates[0].entry.direct_action["kind"] == "python_action"


def test_computation_cache_retrieves_direct_shape_candidates(tmp_path: Path) -> None:
    profile, signature = _profile_for("repo latest 2.43GB\nubuntu 24.04 119MB\n")
    store = AgentComputationCacheStore(tmp_path / "cache.db")
    entry = store.upsert_entry(
        ComputationCacheWrite(
            prompt="combine sizes from prior listing",
            mode="Conversational",
            model_name="fake-model",
            task_type="image.size",
            tool_type="image.size",
            intent_type="calculate",
            tags=["combine", "sizes"],
            action_kind="python_action",
            action_reason="Combine sizes from the prior listing.",
            input_profile=profile,
            input_signature=signature,
            exact_step_key=exact_step_key("old combine sizes step"),
            code_template="def main(inputs):\n    return {'total': '2.55GB'}",
            direct_action={
                "kind": "python_action",
                "code": "def main(inputs):\n    return {'total': '2.55GB'}",
                "input_bindings": [
                    {
                        "input_name": "listing_payload",
                        "source": "prior_streaming_result",
                        "source_field": "stdout",
                        "required": True,
                    }
                ],
                "risk": "low",
                "effect_intent": "read_only",
                "lrdirect_schema_version": 2,
            },
            status="success",
        )
    )

    candidates = store.retrieve_direct_shape_candidates(
        ComputationCacheLookupContext(
            prompt="calculate the total size from the previous image list",
            mode="Conversational",
            model_name="fake-model",
            task_type="image.size",
            tool_type="image.size",
            intent_type="calculate",
            tags=["total", "sizes"],
            action_kind="",
            action_reason="Calculate the total size from the previous image list.",
            input_profile={},
            input_signature="",
        ),
        exact_key=exact_step_key("new combine sizes step"),
    )

    assert [candidate.entry.cache_id for candidate in candidates] == [entry.cache_id]


def test_computation_cache_exact_step_failed_reuse_is_suppressed(tmp_path: Path) -> None:
    profile, signature = _profile_for("repo latest 2.43GB\n")
    store = AgentComputationCacheStore(tmp_path / "cache.db")
    key = exact_step_key("calculate total docker image size")
    entry = store.upsert_entry(
        ComputationCacheWrite(
            prompt="calculate total docker image size",
            action_kind="python_action",
            input_profile=profile,
            input_signature=signature,
            exact_step_key=key,
            code_template="def main(inputs):\n    return {'total_gb': 2.43}",
            direct_action={
                "action_id": "action_cached",
                "task_id": "task_cached",
                "kind": "python_action",
                "code": "def main(inputs):\n    return {'total_gb': 2.43}",
                "reason": "Calculate total image size.",
            },
            status="success",
        )
    )

    store.mark_failed(entry.cache_id, failure_category="direct_replay_failed")

    assert store.retrieve_exact_step(key, action_kind="python_action", input_signature=signature) == []
