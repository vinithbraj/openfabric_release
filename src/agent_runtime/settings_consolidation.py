"""Backend-owned settings profiles and public settings inventory.

This module is intentionally tool-agnostic.  It defines the public settings
surface and the small set of derived behaviors used internally by the runtime.
Legacy knob names remain only as rejected/removed-key identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


SettingDisposition = Literal[
    "keep_server_only",
    "keep_backend_mutable",
    "replace_with_profile",
    "remove",
]

OPERATOR_POLICY_PROFILE_VALUES = {"deterministic", "assisted"}
REASONING_PROFILE_VALUES = {"fast", "balanced", "deep"}
REPAIR_PROFILE_VALUES = {"conservative", "balanced", "aggressive"}
WORKFLOW_EXECUTION_MODE_VALUES = {"auto", "full_plan", "streaming"}
SHELL_INPUT_BINDINGS_MODE_VALUES = {"off", "confirm_bound", "allow"}
CARDINALITY_JUDGE_MODE_VALUES = {"off", "auto", "on"}

OPERATOR_POLICY_MODE_KEYS = (
    "operator_effect_policy_mode",
    "operator_interaction_policy_mode",
    "operator_stdout_policy_mode",
    "operator_failure_policy_mode",
    "operator_memory_policy_mode",
    "operator_streaming_scope_policy_mode",
    "operator_verb_policy_mode",
    "operator_python_code_review_policy_mode",
)


@dataclass(frozen=True)
class ReasoningSettings:
    deliberation_mode: Literal["off", "auto", "always"]
    plan_review_enabled: bool
    answer_judge_enabled: bool
    self_brief_mode: Literal["off", "auto", "always"]
    self_brief_max_tokens: int


@dataclass(frozen=True)
class RepairSettings:
    confidence_threshold: float
    max_validation_repairs: int
    max_deferred_code_repairs: int
    max_answer_judge_repairs: int
    max_execution_repairs: int
    max_completion_repairs: int


@dataclass(frozen=True)
class OperatorProfilePolicy:
    contract_mode: Literal["compact", "verbose"]
    include_rationales: bool
    run_decomposition_critique: bool
    run_semantic_verb_llm: bool
    run_plan_review: bool
    run_answer_coverage: bool
    run_final_formatter: bool
    run_self_brief: bool


REPAIR_ATTEMPT_KEYS = (
    "llm_operator_max_validation_repair_attempts",
    "llm_operator_max_deferred_code_repair_attempts",
    "llm_operator_max_answer_judge_repair_attempts",
    "llm_operator_max_execution_repair_attempts",
    "llm_operator_max_completion_repair_attempts",
)

REMOVED_PUBLIC_SETTING_KEYS = frozenset(
    {
        "backend_persistence_enabled",
        "llm_operator_clarification_strategy",
        "operator_online_mode_enabled",
        "operator_effect_policy_mode",
        "operator_interaction_policy_mode",
        "operator_stdout_policy_mode",
        "operator_failure_policy_mode",
        "operator_memory_policy_mode",
        "operator_streaming_scope_policy_mode",
        "operator_verb_policy_mode",
        "operator_python_code_review_policy_mode",
        "llm_operator_plan_review_enabled",
        "llm_operator_answer_judge_enabled",
        "guided_deliberation_mode",
        "operator_execution_mode",
        "llm_response_streaming_enabled",
        "llm_operator_self_brief_mode",
        "llm_operator_self_brief_max_tokens",
        "llm_operator_repair_confidence_threshold",
        "llm_operator_max_validation_repair_attempts",
        "llm_operator_max_deferred_code_repair_attempts",
        "llm_operator_max_answer_judge_repair_attempts",
        "llm_operator_max_execution_repair_attempts",
        "llm_operator_max_completion_repair_attempts",
        "operator_auto_rephrase_retry_enabled",
        "operator_tryout_mode",
        "operator_tryout_max_attempts",
        "operator_tryout_timeout_seconds",
        "agent_plan_cache_enabled",
        "agent_plan_cache_prompt_max_chars",
        "agent_plan_cache_similarity_threshold",
        "agent_plan_cache_max_entries",
        "agent_computation_cache_enabled",
        "agent_computation_cache_prompt_max_chars",
        "agent_computation_cache_similarity_threshold",
        "agent_computation_cache_max_entries",
    }
)

PUBLIC_RUNTIME_CONTROL_KEYS = frozenset(
    {
        "auto_approve_commands",
        "agent_events_enabled",
        "agent_clarification_mode",
        "llm_operator_final_response_mode",
        "llm_operator_cardinality_judge_mode",
        "llm_operator_verification_enforced",
        "llm_operator_max_clarification_rounds",
        "operator_policy_profile",
        "reasoning_profile",
        "repair_profile",
        "workflow_execution_mode",
        "prompt_rephrase_enabled",
        "response_streaming_enabled",
        "sql_agent_chat_route_mode",
        "operator_workspace_cwd_guard_enabled",
        "llm_operator_verbose_enabled",
        "llm_operator_step_validation_enabled",
        "agent_memory_enabled",
        "agent_memory_prompt_max_chars",
        "agent_learning_ledger_auto_learn_enabled",
        "agent_command_template_cache_similarity_threshold",
        "agent_command_template_cache_secondary_similarity_threshold",
        "lrnt_enabled",
        "lrnt_similarity_threshold",
        "lrdirect_enabled",
        "reliability_mode",
        "reliability_verifier_enforced",
        "reliability_max_recovery_probes",
        "reliability_max_autonomous_repair_attempts",
        "reliability_weak_model_plan_action_cap",
        "reliability_approval_envelope_budget",
        "ui_auto_immersive_min_width_px",
        "llm_base_scheme",
        "llm_base_host",
        "llm_base_port",
        "llm_base_path",
        "llm_base_url",
        "llm_timeout_seconds",
        "llm_max_tokens",
        "audio_transcriber_service_host",
        "audio_transcriber_service_port",
        "audio_transcriber_service_url",
    }
)

PUBLIC_UI_PREFERENCE_KEYS = frozenset(
    {
        "agent_display_name",
        "ui_agent_mode",
        "ui_theme",
        "ui_number_animation",
        "ui_chat_pop_animation",
        "ui_thinking_text_animation",
        "ui_terminal_visible",
        "ui_trace_visible",
        "ui_visualization_visible",
        "ui_chat_bubbles_enabled",
        "ui_command_output_expanded_by_default",
        "browser_notifications_enabled",
        "notification_sound_enabled",
        "notification_sound_variant",
        "notification_sound_volume",
        "audio_voice_input_enabled",
        "audio_capture_preset",
        "audio_silence_timeout_seconds",
    }
)

PROFILE_REPLACED_INTERNAL_KEYS = frozenset(
    {
        *OPERATOR_POLICY_MODE_KEYS,
        *REPAIR_ATTEMPT_KEYS,
        "guided_deliberation_mode",
        "operator_execution_mode",
        "llm_response_streaming_enabled",
        "llm_operator_plan_review_enabled",
        "llm_operator_answer_judge_enabled",
        "llm_operator_self_brief_mode",
        "llm_operator_self_brief_max_tokens",
        "llm_operator_repair_confidence_threshold",
    }
)


def normalize_operator_policy_profile(value: Any) -> str:
    normalized = str(value or "assisted").strip().lower()
    return normalized if normalized in OPERATOR_POLICY_PROFILE_VALUES else "assisted"


def normalize_reasoning_profile(value: Any) -> str:
    normalized = str(value or "balanced").strip().lower()
    return normalized if normalized in REASONING_PROFILE_VALUES else "balanced"


def normalize_repair_profile(value: Any) -> str:
    normalized = str(value or "balanced").strip().lower()
    return normalized if normalized in REPAIR_PROFILE_VALUES else "balanced"


def normalize_workflow_execution_mode(value: Any) -> str:
    normalized = str(value or "streaming").strip().lower()
    return normalized if normalized in WORKFLOW_EXECUTION_MODE_VALUES else "streaming"


def normalize_shell_input_bindings_mode(value: Any) -> str:
    normalized = str(value or "allow").strip().lower()
    return normalized if normalized in SHELL_INPUT_BINDINGS_MODE_VALUES else "allow"


def normalize_cardinality_judge_mode(value: Any) -> str:
    normalized = str(value or "auto").strip().lower()
    return normalized if normalized in CARDINALITY_JUDGE_MODE_VALUES else "auto"


def operator_policy_mode_from_profile(profile: Any) -> Literal["deterministic", "llm"]:
    return "llm" if normalize_operator_policy_profile(profile) == "assisted" else "deterministic"


def operator_legacy_override(config: Any, key: str, default: Any = None) -> Any:
    """Return a non-public compatibility override collected by RuntimeConfig."""

    overrides = getattr(config, "operator_legacy_overrides", {}) or {}
    if isinstance(overrides, dict) and key in overrides:
        return overrides[key]
    return default


def effective_prompt_rephrase_enabled(config: Any, *, default: bool = True) -> bool:
    """Return the public prompt-rephrase switch with legacy retry-key fallback."""

    if isinstance(config, dict):
        if "prompt_rephrase_enabled" in config:
            return bool(config.get("prompt_rephrase_enabled"))
        if "operator_auto_rephrase_retry_enabled" in config:
            return bool(config.get("operator_auto_rephrase_retry_enabled"))
        return bool(default)

    explicit_fields = set(getattr(config, "model_fields_set", set()) or set())
    if "prompt_rephrase_enabled" in explicit_fields:
        return bool(getattr(config, "prompt_rephrase_enabled", default))

    legacy = operator_legacy_override(
        config,
        "operator_auto_rephrase_retry_enabled",
    )
    if legacy is not None:
        return bool(legacy)
    return bool(getattr(config, "prompt_rephrase_enabled", default))


def reasoning_settings_from_profile(profile: Any) -> ReasoningSettings:
    normalized = normalize_reasoning_profile(profile)
    if normalized == "deep":
        return ReasoningSettings(
            deliberation_mode="always",
            plan_review_enabled=True,
            answer_judge_enabled=True,
            self_brief_mode="always",
            self_brief_max_tokens=4096,
        )
    if normalized == "balanced":
        return ReasoningSettings(
            deliberation_mode="auto",
            plan_review_enabled=False,
            answer_judge_enabled=False,
            self_brief_mode="auto",
            self_brief_max_tokens=2048,
        )
    return ReasoningSettings(
        deliberation_mode="off",
        plan_review_enabled=False,
        answer_judge_enabled=False,
        self_brief_mode="off",
        self_brief_max_tokens=1024,
    )


def operator_profile_policy(
    reasoning_profile: Any,
    *,
    llm_operator_verbose_enabled: Any = False,
) -> OperatorProfilePolicy:
    """Return the effective operator contract/review policy for one request.

    The public verbose toggle is now an upper bound: it can suppress verbose
    behavior, but fast and balanced profiles remain compact even when it is on.
    """

    normalized = normalize_reasoning_profile(reasoning_profile)
    verbose_requested = bool(llm_operator_verbose_enabled)
    verbose = normalized == "deep" and verbose_requested
    return OperatorProfilePolicy(
        contract_mode="verbose" if verbose else "compact",
        include_rationales=verbose,
        run_decomposition_critique=verbose,
        run_semantic_verb_llm=verbose,
        run_plan_review=verbose,
        run_answer_coverage=verbose,
        run_final_formatter=verbose,
        run_self_brief=verbose,
    )


def repair_settings_from_profile(profile: Any) -> RepairSettings:
    normalized = normalize_repair_profile(profile)
    if normalized == "aggressive":
        return RepairSettings(0.70, 2, 3, 1, 2, 2)
    if normalized == "conservative":
        return RepairSettings(0.65, 0, 0, 0, 1, 0)
    return RepairSettings(0.55, 1, 2, 1, 2, 1)


def workflow_uses_streaming(mode: Any) -> bool:
    return normalize_workflow_execution_mode(mode) != "full_plan"


def canonical_runtime_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Return only backend-owned public runtime controls from an arbitrary mapping."""

    source = dict(values or {})
    return {
        "operator_policy_profile": normalize_operator_policy_profile(
            source.get("operator_policy_profile")
        ),
        "reasoning_profile": normalize_reasoning_profile(source.get("reasoning_profile")),
        "repair_profile": normalize_repair_profile(source.get("repair_profile")),
        "workflow_execution_mode": normalize_workflow_execution_mode(
            source.get("workflow_execution_mode")
        ),
        "prompt_rephrase_enabled": effective_prompt_rephrase_enabled(source),
        "response_streaming_enabled": bool(source.get("response_streaming_enabled")),
    }


def removed_public_settings_in(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    return sorted(str(key) for key in payload if key in REMOVED_PUBLIC_SETTING_KEYS)


def strip_public_runtime_overrides(context: dict[str, Any]) -> None:
    for key in REMOVED_PUBLIC_SETTING_KEYS | PUBLIC_RUNTIME_CONTROL_KEYS | PROFILE_REPLACED_INTERNAL_KEYS:
        context.pop(key, None)


def settings_inventory_for_keys(keys: set[str]) -> dict[str, SettingDisposition]:
    inventory: dict[str, SettingDisposition] = {}
    for key in sorted(keys):
        if key in REMOVED_PUBLIC_SETTING_KEYS:
            inventory[key] = "remove"
        elif key in PROFILE_REPLACED_INTERNAL_KEYS:
            inventory[key] = "replace_with_profile"
        elif key in PUBLIC_RUNTIME_CONTROL_KEYS or key in PUBLIC_UI_PREFERENCE_KEYS:
            inventory[key] = "keep_backend_mutable"
        else:
            inventory[key] = "keep_server_only"
    return inventory
