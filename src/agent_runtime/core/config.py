"""Configuration schema for the foundational runtime."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_runtime.settings_consolidation import (
    OPERATOR_POLICY_MODE_KEYS,
    PROFILE_REPLACED_INTERNAL_KEYS,
    normalize_cardinality_judge_mode,
    normalize_operator_policy_profile,
    normalize_reasoning_profile,
    normalize_repair_profile,
    normalize_shell_input_bindings_mode,
    normalize_workflow_execution_mode,
)
from agent_runtime.clarification import normalize_agent_clarification_mode


class RuntimeConfig(BaseModel):
    """Process-level settings shared by all three pipelines."""

    model_config = ConfigDict(extra="forbid")

    max_decomposition_depth: int = Field(default=10, gt=0)
    max_dag_nodes: int = Field(default=64, gt=0)
    allow_mutating_capabilities: bool = False
    default_output_format: str = "markdown"
    workspace_root: str = "."
    allow_shell_execution: bool = False
    allow_network_operations: bool = False
    gateway_default_node: str = "localhost"
    gateway_url: str | None = None
    gateway_endpoints: dict[str, str] = Field(default_factory=dict)
    gateway_timeout_seconds: float = Field(default=30.0, gt=0.0)
    confirmation_granted: bool = False
    max_files_listed: int = Field(default=1000, gt=0)
    max_rows_returned: int = Field(default=100, gt=0)
    max_output_preview_bytes: int = Field(default=4096, gt=0)
    low_risk_mutation_allowlist: list[str] = Field(default_factory=list)
    operator_command_allowlist_hashes: list[str] = Field(default_factory=list)
    stop_on_error: bool = True
    llm_operator_enabled: bool = True
    llm_operator_requires_approval: bool = True
    llm_operator_max_actions: int = Field(default=8, gt=0)
    operator_policy_profile: Literal["deterministic", "assisted"] = "deterministic"
    reasoning_profile: Literal["fast", "balanced", "deep"] = "fast"
    repair_profile: Literal["conservative", "balanced", "aggressive"] = "balanced"
    operator_legacy_overrides: dict[str, object] = Field(
        default_factory=dict,
        exclude=True,
        repr=False,
    )
    workflow_execution_mode: Literal["auto", "full_plan", "streaming"] = "streaming"
    prompt_rephrase_enabled: bool = True
    response_streaming_enabled: bool = False
    shell_input_bindings_mode: Literal["off", "confirm_bound", "allow"] = "allow"
    llm_operator_verbose_enabled: bool = False
    online_lookup_gemini_api_key: str = ""
    online_lookup_gemini_model: str = "gemini-2.5-flash"
    online_lookup_gemini_api_version: str = "v1beta"
    online_ai_check_timeout_seconds: float = Field(default=120.0, gt=0.0)
    online_ai_check_reuse_browser: bool = True
    online_ai_check_headless: bool = False
    online_ai_check_profile_dir: str = "artifacts/playwright-duckai-profile"
    llm_operator_step_validation_enabled: bool = False
    llm_operator_final_response_mode: str = "detailed"
    llm_operator_cardinality_judge_mode: Literal["off", "auto", "on"] = "auto"
    llm_operator_verification_enforced: bool = True
    operator_workspace_cwd_guard_enabled: bool = False
    llm_operator_formatter_source_preview_chars: int = Field(default=3000, gt=0)
    agent_clarification_mode: Literal["auto_pilot", "balanced", "pedantic"] = "balanced"
    llm_operator_max_clarification_rounds: int = Field(default=3, ge=0)
    reliability_mode: Literal["off", "standard", "aggressive"] = "aggressive"
    reliability_db_path: str = "artifacts/agent_reliability.db"
    reliability_max_recovery_probes: int = Field(default=3, ge=0)
    reliability_max_autonomous_repair_attempts: int = Field(default=2, ge=0)
    reliability_weak_model_plan_action_cap: int = Field(default=4, gt=0)
    reliability_verifier_enforced: bool = True
    reliability_approval_envelope_budget: int = Field(default=2, ge=0)
    agent_memory_enabled: bool = True
    agent_memory_prompt_max_chars: int = Field(default=3000, gt=0)
    agent_parameter_store_enabled: bool = True
    agent_parameter_prompt_max_chars: int = Field(default=4000, gt=0)
    sql_agent_enabled: bool = True
    sql_agent_chat_route_mode: Literal["agentic", "direct"] = "agentic"
    sql_agent_default_limit: int = Field(default=100, gt=0)
    sql_agent_max_rows: int = Field(default=1000, gt=0)
    sql_agent_max_repair_attempts: int = Field(default=2, ge=0)
    agent_plan_cache_enabled: bool = True
    agent_plan_cache_prompt_max_chars: int = Field(default=4000, gt=0)
    agent_plan_cache_similarity_threshold: float = Field(default=0.86, ge=0.0, le=1.0)
    agent_plan_cache_max_entries: int = Field(default=5000, gt=0)
    agent_command_template_cache_enabled: bool = True
    agent_command_template_cache_prompt_max_chars: int = Field(default=4000, gt=0)
    agent_command_template_cache_similarity_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    agent_command_template_cache_secondary_similarity_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    agent_command_template_cache_max_entries: int = Field(default=5000, gt=0)
    agent_computation_cache_enabled: bool = True
    agent_computation_cache_prompt_max_chars: int = Field(default=4000, gt=0)
    agent_computation_cache_similarity_threshold: float = Field(default=0.84, ge=0.0, le=1.0)
    agent_computation_cache_max_entries: int = Field(default=5000, gt=0)
    lrnt_enabled: bool = True
    lrnt_similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    lrnt_max_entries: int = Field(default=5000, gt=0)
    lrdirect_enabled: bool = True
    terminal_session_id: str | None = None
    terminal_cwd: str | None = None
    terminal_execution_enabled: bool = False

    @model_validator(mode="before")
    @classmethod
    def collect_legacy_operator_overrides(cls, values: object) -> object:
        """Keep removed public knobs out of the schema while honoring internal callers."""

        if not isinstance(values, dict):
            return values
        payload = dict(values)
        legacy = dict(payload.get("operator_legacy_overrides") or {})
        legacy_keys = set(PROFILE_REPLACED_INTERNAL_KEYS) | {
            *OPERATOR_POLICY_MODE_KEYS,
            "operator_auto_rephrase_retry_enabled",
            "operator_tryout_mode",
            "operator_tryout_max_attempts",
            "operator_tryout_timeout_seconds",
            "operator_online_mode_enabled",
        }
        for key in sorted(legacy_keys):
            if key in payload:
                legacy[key] = payload.pop(key)
        if (
            "llm_operator_self_brief_mode" not in legacy
            and any(
                key in legacy
                for key in (
                    "guided_deliberation_mode",
                    "llm_operator_plan_review_enabled",
                    "llm_operator_answer_judge_enabled",
                )
            )
        ):
            legacy["llm_operator_self_brief_mode"] = "auto"
        if (
            "operator_execution_mode" in legacy
            and "workflow_execution_mode" not in payload
        ):
            payload["workflow_execution_mode"] = legacy["operator_execution_mode"]
        if (
            "llm_response_streaming_enabled" in legacy
            and "response_streaming_enabled" not in payload
        ):
            payload["response_streaming_enabled"] = bool(
                legacy["llm_response_streaming_enabled"]
            )
        if legacy:
            payload["operator_legacy_overrides"] = legacy
        return payload

    @model_validator(mode="after")
    def normalize_profiles_and_clarification_mode(self) -> "RuntimeConfig":
        self.operator_policy_profile = normalize_operator_policy_profile(
            self.operator_policy_profile
        )
        self.reasoning_profile = normalize_reasoning_profile(self.reasoning_profile)
        self.repair_profile = normalize_repair_profile(self.repair_profile)
        self.workflow_execution_mode = normalize_workflow_execution_mode(
            self.workflow_execution_mode
        )
        self.shell_input_bindings_mode = normalize_shell_input_bindings_mode(
            self.shell_input_bindings_mode
        )
        self.llm_operator_cardinality_judge_mode = normalize_cardinality_judge_mode(
            self.llm_operator_cardinality_judge_mode
        )
        explicit_fields = getattr(self, "model_fields_set", set())
        mode_was_supplied = "agent_clarification_mode" in explicit_fields
        self.agent_clarification_mode = normalize_agent_clarification_mode(
            self.agent_clarification_mode if mode_was_supplied else "",
        )
        return self
