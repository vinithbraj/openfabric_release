from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_runtime.api.app import create_app
from agent_runtime.api.agent_ui_support.directory import RUNTIME_CONTROLS_SETTINGS_NAMESPACE
from agent_runtime.api.config import Settings
from agent_runtime.api.settings_store import AgentUiSettingsStore
from agent_runtime.settings_consolidation import (
    PROFILE_REPLACED_INTERNAL_KEYS,
    PUBLIC_RUNTIME_CONTROL_KEYS,
    REMOVED_PUBLIC_SETTING_KEYS,
    operator_policy_mode_from_profile,
    operator_profile_policy,
    removed_public_settings_in,
    repair_settings_from_profile,
    reasoning_settings_from_profile,
    settings_inventory_for_keys,
    workflow_uses_streaming,
)


class _FakeRuntime:
    def __init__(self) -> None:
        self.last_context: dict[str, Any] = {}

    def handle_request(self, raw_prompt: str, context: dict[str, Any] | None = None) -> str:
        self.last_context = dict(context or {})
        return f"handled: {raw_prompt}"


def _client(tmp_path: Path, runtime: _FakeRuntime | None = None) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=tmp_path / "settings.db",
            ),
            agent_runtime=runtime,
        )
    )


def test_settings_registry_exposes_only_backend_owned_runtime_controls(tmp_path: Path) -> None:
    client = _client(tmp_path)

    registry = client.get("/api/agent/settings/registry").json()
    runtime_controls = client.get("/api/agent/runtime-controls").json()

    assert registry["version"] == "20260602-text-odometer"
    assert set(registry["runtime_control_keys"]) == PUBLIC_RUNTIME_CONTROL_KEYS
    assert set(runtime_controls) == PUBLIC_RUNTIME_CONTROL_KEYS
    assert REMOVED_PUBLIC_SETTING_KEYS.isdisjoint(registry["defaults"])
    assert PROFILE_REPLACED_INTERNAL_KEYS.isdisjoint(registry["defaults"])
    assert REMOVED_PUBLIC_SETTING_KEYS.isdisjoint(registry["runtime_control_keys"])
    assert PROFILE_REPLACED_INTERNAL_KEYS.isdisjoint(registry["runtime_control_keys"])
    assert {
        "operator_policy_profile",
        "reasoning_profile",
        "repair_profile",
        "workflow_execution_mode",
        "prompt_rephrase_enabled",
        "response_streaming_enabled",
        "llm_operator_final_response_mode",
        "llm_operator_cardinality_judge_mode",
        "llm_operator_verification_enforced",
        "llm_operator_max_clarification_rounds",
        "agent_memory_enabled",
        "agent_memory_prompt_max_chars",
        "llm_base_url",
    } <= set(registry["defaults"])
    assert registry["defaults"]["llm_operator_cardinality_judge_mode"] == "auto"


def test_removed_settings_are_rejected_at_backend_boundaries(tmp_path: Path) -> None:
    client = _client(tmp_path)

    runtime_response = client.post(
        "/api/agent/runtime-controls",
        json={"operator_execution_mode": "streaming"},
    )
    preferences_response = client.put(
        "/api/agent/settings/preferences",
        json={"settings": {"backend_persistence_enabled": False}},
    )

    assert runtime_response.status_code == 422
    assert "Removed runtime settings" in runtime_response.text
    assert preferences_response.status_code == 422
    assert preferences_response.json()["detail"]["removed_keys"] == [
        "backend_persistence_enabled"
    ]


def test_runtime_controls_seed_and_backfill_settings_db_without_overwrites(tmp_path: Path) -> None:
    db_path = tmp_path / "settings.db"
    first = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
                llm_base_url="https://bootstrap.local:9443/openai/v1",
                llm_timeout_seconds=321,
                llm_max_tokens=7777,
            ),
            agent_runtime=_FakeRuntime(),
        )
    )

    initial = first.get("/api/agent/runtime-controls").json()
    store = AgentUiSettingsStore(db_path)
    seeded = store.get_namespace(RUNTIME_CONTROLS_SETTINGS_NAMESPACE)

    assert set(seeded) == PUBLIC_RUNTIME_CONTROL_KEYS
    assert seeded["llm_base_url"] == "https://bootstrap.local:9443/openai/v1"
    assert seeded["llm_timeout_seconds"] == 321
    assert seeded["llm_max_tokens"] == 7777
    assert initial["llm_base_url"] == "https://bootstrap.local:9443/openai/v1"

    store.replace_namespace_values(
        RUNTIME_CONTROLS_SETTINGS_NAMESPACE,
        {
            "workflow_execution_mode": "streaming",
            "llm_base_url": "https://persisted.local:9555/runtime/v1",
        },
    )
    second = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
                llm_base_url="http://bootstrap-two.local:8123/v1",
            ),
            agent_runtime=_FakeRuntime(),
        )
    )

    restarted = second.get("/api/agent/runtime-controls").json()
    backfilled = AgentUiSettingsStore(db_path).get_namespace(
        RUNTIME_CONTROLS_SETTINGS_NAMESPACE
    )

    assert set(backfilled) == PUBLIC_RUNTIME_CONTROL_KEYS
    assert restarted["workflow_execution_mode"] == "streaming"
    assert restarted["llm_base_host"] == "persisted.local"
    assert restarted["llm_base_port"] == 9555
    assert restarted["llm_base_path"] == "/runtime/v1"
    assert restarted["llm_base_url"] == "https://persisted.local:9555/runtime/v1"
    assert backfilled["workflow_execution_mode"] == "streaming"
    assert backfilled["llm_base_url"] == "https://persisted.local:9555/runtime/v1"


def test_backend_runtime_settings_apply_to_next_same_chat_request(tmp_path: Path) -> None:
    runtime = _FakeRuntime()
    client = _client(tmp_path, runtime)

    update = client.post(
        "/api/agent/runtime-controls",
        json={
            "operator_policy_profile": "assisted",
            "reasoning_profile": "deep",
            "repair_profile": "conservative",
            "workflow_execution_mode": "streaming",
            "prompt_rephrase_enabled": False,
            "response_streaming_enabled": True,
        },
    )
    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "list files",
            "context": {
                "operator_execution_mode": "full_plan",
                "operator_effect_policy_mode": "deterministic",
                "guided_deliberation_mode": "off",
            },
        },
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        response.read()

    assert update.status_code == 200
    assert runtime.last_context["workflow_execution_mode"] == "streaming"
    assert runtime.last_context["operator_policy_profile"] == "assisted"
    assert runtime.last_context["reasoning_profile"] == "deep"
    assert runtime.last_context["repair_profile"] == "conservative"
    assert runtime.last_context["prompt_rephrase_enabled"] is False
    assert runtime.last_context["response_streaming_enabled"] is True
    assert "operator_execution_mode" not in runtime.last_context
    assert "operator_effect_policy_mode" not in runtime.last_context
    assert "guided_deliberation_mode" not in runtime.last_context
    assert "llm_response_streaming_enabled" not in runtime.last_context


def test_persisted_runtime_endpoint_overrides_bootstrap_and_request_context(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "settings.db"
    first = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
                llm_base_url="http://bootstrap-one.local:8123/v1",
            ),
            agent_runtime=_FakeRuntime(),
        )
    )
    updated = first.post(
        "/api/agent/runtime-controls",
        json={"llm_base_url": "https://persisted.local:9443/openai/v1"},
    )
    runtime = _FakeRuntime()
    second = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
                llm_base_url="http://bootstrap-two.local:8124/v1",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = second.post(
        "/api/agent/request",
        json={
            "prompt": "list files",
            "context": {
                "llm_base_scheme": "http",
                "llm_base_host": "request.local",
                "llm_base_port": 8999,
                "llm_base_path": "/request/v1",
                "llm_base_url": "http://request.local:8999/request/v1",
            },
        },
    )
    with second.stream("GET", submitted.json()["stream_url"]) as response:
        response.read()

    assert updated.status_code == 200
    assert second.get("/api/agent/runtime-controls").json()["llm_base_url"] == (
        "https://persisted.local:9443/openai/v1"
    )
    assert runtime.last_context["llm_base_scheme"] == "https"
    assert runtime.last_context["llm_base_host"] == "persisted.local"
    assert runtime.last_context["llm_base_port"] == 9443
    assert runtime.last_context["llm_base_path"] == "/openai/v1"
    assert runtime.last_context["llm_base_url"] == "https://persisted.local:9443/openai/v1"


def test_backend_preferences_persist_theme_and_runtime_profiles(tmp_path: Path) -> None:
    first = _client(tmp_path)

    updated = first.put(
        "/api/agent/settings/preferences",
        json={
            "settings": {
                "ui_theme": "midnight",
                "workflow_execution_mode": "streaming",
                "prompt_rephrase_enabled": False,
                "response_streaming_enabled": True,
            }
        },
    )
    second = _client(tmp_path)

    assert updated.status_code == 200
    assert second.get("/api/agent/settings/preferences").json()["settings"]["ui_theme"] == "midnight"
    runtime_controls = second.get("/api/agent/runtime-controls").json()
    assert runtime_controls["workflow_execution_mode"] == "streaming"
    assert runtime_controls["prompt_rephrase_enabled"] is False
    assert runtime_controls["response_streaming_enabled"] is True


def test_settings_consolidation_inventory_and_profile_expansion() -> None:
    keys = {
        "operator_policy_profile",
        "operator_effect_policy_mode",
        "backend_persistence_enabled",
        "workspace_root",
    }

    inventory = settings_inventory_for_keys(keys)
    reasoning = reasoning_settings_from_profile("balanced")
    balanced_repair = repair_settings_from_profile("balanced")
    repair = repair_settings_from_profile("aggressive")

    assert inventory["operator_policy_profile"] == "keep_backend_mutable"
    assert inventory["operator_effect_policy_mode"] == "remove"
    assert inventory["backend_persistence_enabled"] == "remove"
    assert inventory["workspace_root"] == "keep_server_only"
    assert removed_public_settings_in({"operator_execution_mode": "streaming"}) == [
        "operator_execution_mode"
    ]
    assert workflow_uses_streaming("streaming")
    assert operator_policy_mode_from_profile("assisted") == "llm"
    assert reasoning.deliberation_mode == "auto"
    assert balanced_repair.max_execution_repairs == 2
    assert repair.max_execution_repairs == 2


def test_operator_profile_policy_keeps_fast_and_balanced_compact() -> None:
    fast = operator_profile_policy("fast", llm_operator_verbose_enabled=True)
    balanced = operator_profile_policy("balanced", llm_operator_verbose_enabled=True)
    deep = operator_profile_policy("deep", llm_operator_verbose_enabled=True)
    deep_suppressed = operator_profile_policy("deep", llm_operator_verbose_enabled=False)

    assert fast.contract_mode == "compact"
    assert not fast.run_decomposition_critique
    assert not fast.run_semantic_verb_llm
    assert not fast.run_final_formatter
    assert balanced.contract_mode == "compact"
    assert not balanced.include_rationales
    assert deep.contract_mode == "verbose"
    assert deep.run_plan_review
    assert deep.run_answer_coverage
    assert deep_suppressed.contract_mode == "compact"
    assert not deep_suppressed.run_plan_review
