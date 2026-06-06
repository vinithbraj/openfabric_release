"""OpenFABRIC runtime settings and environment-driven defaults.

Purpose:
    Define typed settings for the API, CLI, gateway bridge, and compatibility
    surfaces that now route into the typed agent runtime.

Responsibilities:
    Preserve existing configuration keys so deployments and config files remain
    compatible while the active runtime continues to evolve.

Data flow / Interfaces:
    Consumes app config and environment variables and provides typed settings to
    the CLI, API, and compatibility bridge.

Boundaries:
    Configuration values are validated here, but planning, execution, and tool
    behavior live in the runtime and gateway layers.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from agent_runtime.api.app_config import APP_CONFIG_PATH_ENV, load_app_config
from agent_runtime.api.constants import DEFAULT_COMPAT_SPEC_PATH, is_compat_spec_placeholder
from agent_runtime.api.model_identity import DEFAULT_OPENAI_COMPAT_MODEL_NAME, normalize_openai_compat_model_name
from agent_runtime.clarification import normalize_agent_clarification_mode
from agent_runtime.prompts import configure_prompt_fetcher
from agent_runtime.settings_consolidation import (
    normalize_cardinality_judge_mode,
    normalize_operator_policy_profile,
    normalize_reasoning_profile,
    normalize_repair_profile,
    normalize_shell_input_bindings_mode,
    normalize_workflow_execution_mode,
)


def _env_bool(name: str, default: bool = False) -> bool:
    """Handle the internal env bool helper path for this module.

    Inputs:
        Receives name, default for this function; type hints and validators define accepted shapes.

    Returns:
        Returns the computed value described by the function name and type hints.

    Used by:
        Used by OpenFABRIC runtime support code paths that import or call agent_runtime.api.config._env_bool.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_model_path_for_tier(tier: str, model_dir: Path) -> Path:
    """Resolve a whisper model path from a quality tier."""

    normalized = (tier or "high").strip().lower()
    if normalized == "med":
        filename = "ggml-medium.en.bin"
    elif normalized == "low":
        filename = "ggml-base.en.bin"
    else:
        filename = "ggml-large-v3.bin"
    return Path(model_dir) / filename


class Settings(BaseModel):
    """Represent settings within the OpenFABRIC runtime. It extends BaseModel.

    Responsibilities:
        Encapsulates state, validation, or behavior owned by Settings.

    Data flow / Interfaces:
        Instances are created and consumed by OpenFABRIC runtime support code paths according to type hints and validators.

    Used by:
        Used by callers of agent_runtime.api.config.Settings and related tests.
    """
    workspace_root: Path = Field(default_factory=lambda: Path.cwd())
    prompts_root: Path = Field(default_factory=lambda: Path.cwd() / "prompts")
    run_store_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "runtime.db")
    agent_gateways_db_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "agent_gateways.db")
    agent_memory_db_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "agent_memory.db")
    agent_prompts_db_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "prompts.db")
    agent_plan_cache_db_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "agent_plan_cache.db")
    agent_lrn_total_tasks_db_path: Path = Field(
        default_factory=lambda: Path.cwd() / "artifacts" / "agent_lrn_total_tasks.db"
    )
    agent_command_template_cache_db_path: Path = Field(
        default_factory=lambda: Path.cwd() / "artifacts" / "agent_command_template_cache.db"
    )
    agent_computation_cache_db_path: Path = Field(
        default_factory=lambda: Path.cwd() / "artifacts" / "agent_computation_cache.db"
    )
    agent_learning_ledger_db_path: Path = Field(
        default_factory=lambda: Path.cwd() / "artifacts" / "agent_learning_ledger.db"
    )
    agent_reliability_db_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv(
                "AOR_AGENT_RELIABILITY_DB_PATH",
                str(Path.cwd() / "artifacts" / "agent_reliability.db"),
            )
        )
    )
    agent_events_db_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "agent_events.db")
    agent_command_allowlist_db_path: Path = Field(
        default_factory=lambda: Path.cwd() / "artifacts" / "agent_command_allowlist.db"
    )
    agent_chats_db_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "chats.db")
    agent_tasks_db_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "agent_tasks.db")
    agent_monitors_db_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "agent_monitors.db")
    agent_parameters_db_path: Path = Field(default_factory=lambda: Path.cwd() / "artifacts" / "agent_parameters.db")
    llm_monitor_planner_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_LLM_MONITOR_PLANNER_ENABLED", True))
    llm_trigger_judge_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_LLM_TRIGGER_JUDGE_ENABLED", True))
    monitor_judge_min_interval_seconds: int = Field(
        default_factory=lambda: int(os.getenv("AOR_MONITOR_JUDGE_MIN_INTERVAL_SECONDS", "5"))
    )
    monitor_judge_output_preview_cap: int = Field(
        default_factory=lambda: int(os.getenv("AOR_MONITOR_JUDGE_OUTPUT_PREVIEW_CAP", "4000"))
    )
    max_inferred_monitor_duration_seconds: int = Field(
        default_factory=lambda: int(os.getenv("AOR_MAX_INFERRED_MONITOR_DURATION_SECONDS", "300"))
    )
    agent_ui_settings_db_path: Path = Field(
        default_factory=lambda: Path.cwd() / "artifacts" / "agent_ui_settings.db"
    )
    app_config_path: Path | None = None
    server_host: str = "127.0.0.1"
    server_port: int = 8011
    available_nodes_raw: str | None = Field(default_factory=lambda: os.getenv("AOR_AVAILABLE_NODES") or None)
    default_node: str | None = Field(default_factory=lambda: os.getenv("AOR_DEFAULT_NODE") or None)
    gateway_url: str | None = Field(default_factory=lambda: os.getenv("AOR_GATEWAY_URL") or None)
    gateway_endpoints: dict[str, str] = Field(default_factory=dict)
    gateway_timeout_seconds: float = Field(default_factory=lambda: float(os.getenv("AOR_GATEWAY_TIMEOUT_SECONDS", "30")))
    sql_database_url: str | None = None
    sql_databases: dict[str, str] = Field(default_factory=dict)
    sql_default_database: str | None = None
    sql_row_limit: int = 0
    sql_timeout_seconds: int = 10
    sql_agent_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_SQL_AGENT_ENABLED", True))
    sql_agent_chat_route_mode: str = Field(
        default_factory=lambda: os.getenv("AOR_SQL_AGENT_CHAT_ROUTE_MODE", "agentic").strip().lower()
    )
    sql_agent_default_limit: int = Field(default_factory=lambda: int(os.getenv("AOR_SQL_AGENT_DEFAULT_LIMIT", "100")))
    sql_agent_max_rows: int = Field(default_factory=lambda: int(os.getenv("AOR_SQL_AGENT_MAX_ROWS", "1000")))
    sql_agent_max_repair_attempts: int = Field(
        default_factory=lambda: int(os.getenv("AOR_SQL_AGENT_MAX_REPAIR_ATTEMPTS", "2"))
    )
    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_api_key: str = "local"
    default_model: str = "auto"
    default_temperature: float = 0.1
    llm_timeout_seconds: float = 120.0
    llm_max_tokens: int = Field(default_factory=lambda: int(os.getenv("AOR_LLM_MAX_TOKENS", "0")))
    llm_context_window_tokens: int = Field(default_factory=lambda: int(os.getenv("AOR_LLM_CONTEXT_WINDOW_TOKENS", "32768")))
    allow_destructive_shell: bool = False
    shell_mode: str = Field(default_factory=lambda: os.getenv("AOR_SHELL_MODE", "read_only"))
    shell_allow_mutation_with_approval: bool = Field(default_factory=lambda: _env_bool("AOR_SHELL_ALLOW_MUTATION_WITH_APPROVAL"))
    shell_allowed_roots_raw: str | None = Field(default_factory=lambda: os.getenv("AOR_SHELL_ALLOWED_ROOTS") or None)
    shell_default_cwd: str | None = Field(default_factory=lambda: os.getenv("AOR_SHELL_DEFAULT_CWD") or None)
    shell_max_output_chars: int = Field(default_factory=lambda: int(os.getenv("AOR_SHELL_MAX_OUTPUT_CHARS", "20000")))
    shell_command_timeout_seconds: int = Field(default_factory=lambda: int(os.getenv("AOR_SHELL_COMMAND_TIMEOUT_SECONDS", "30")))
    shutdown_grace_seconds: float = Field(default_factory=lambda: float(os.getenv("AOR_SHUTDOWN_GRACE_SECONDS", "5")))
    worker_join_timeout_seconds: float = Field(default_factory=lambda: float(os.getenv("AOR_WORKER_JOIN_TIMEOUT_SECONDS", "2")))
    tool_process_kill_grace_seconds: float = Field(default_factory=lambda: float(os.getenv("AOR_TOOL_PROCESS_KILL_GRACE_SECONDS", "1")))
    runtime_timezone: str = Field(default_factory=lambda: os.getenv("AOR_RUNTIME_TIMEZONE", "").strip())
    enable_llm_intent_extraction: bool = Field(
        default_factory=lambda: os.getenv("AOR_ENABLE_LLM_INTENT_EXTRACTION", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    enable_sql_llm_generation: bool = Field(
        default_factory=lambda: os.getenv("AOR_ENABLE_SQL_LLM_GENERATION", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    presentation_mode: str = Field(default_factory=lambda: os.getenv("AOR_PRESENTATION_MODE", "user"))
    enable_llm_summary: bool = Field(
        default_factory=lambda: os.getenv("AOR_ENABLE_LLM_SUMMARY", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    llm_summary_max_facts: int = Field(default_factory=lambda: int(os.getenv("AOR_LLM_SUMMARY_MAX_FACTS", "50")))
    include_internal_telemetry: bool = Field(
        default_factory=lambda: os.getenv("AOR_INCLUDE_INTERNAL_TELEMETRY", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    response_render_mode: str = Field(
        default_factory=lambda: os.getenv("AOR_RESPONSE_RENDER_MODE") or os.getenv("AOR_PRESENTATION_MODE", "user")
    )
    show_executed_commands: bool = Field(default_factory=lambda: _env_bool("AOR_SHOW_EXECUTED_COMMANDS", True))
    show_validation_events: bool = Field(default_factory=lambda: _env_bool("AOR_SHOW_VALIDATION_EVENTS"))
    show_planner_events: bool = Field(default_factory=lambda: _env_bool("AOR_SHOW_PLANNER_EVENTS"))
    show_tool_events: bool = Field(default_factory=lambda: _env_bool("AOR_SHOW_TOOL_EVENTS"))
    openwebui_trace_mode: str = Field(default_factory=lambda: os.getenv("AOR_OPENWEBUI_TRACE_MODE", "").strip().lower())
    show_response_stats: bool = Field(default_factory=lambda: _env_bool("AOR_SHOW_RESPONSE_STATS", True))
    show_prompt_suggestions: bool = Field(default_factory=lambda: _env_bool("AOR_SHOW_PROMPT_SUGGESTIONS"))
    show_debug_metadata: bool = Field(default_factory=lambda: _env_bool("AOR_SHOW_DEBUG_METADATA"))
    enable_presentation_llm_summary: bool = Field(default_factory=lambda: _env_bool("AOR_ENABLE_PRESENTATION_LLM_SUMMARY"))
    presentation_llm_max_facts: int = Field(default_factory=lambda: int(os.getenv("AOR_PRESENTATION_LLM_MAX_FACTS", "50")))
    presentation_llm_max_input_chars: int = Field(default_factory=lambda: int(os.getenv("AOR_PRESENTATION_LLM_MAX_INPUT_CHARS", "4000")))
    presentation_llm_max_output_chars: int = Field(default_factory=lambda: int(os.getenv("AOR_PRESENTATION_LLM_MAX_OUTPUT_CHARS", "1500")))
    presentation_llm_include_row_samples: bool = Field(default_factory=lambda: _env_bool("AOR_PRESENTATION_LLM_INCLUDE_ROW_SAMPLES"))
    presentation_llm_include_paths: bool = Field(default_factory=lambda: _env_bool("AOR_PRESENTATION_LLM_INCLUDE_PATHS"))
    intelligent_output_mode: str = Field(default_factory=lambda: os.getenv("AOR_INTELLIGENT_OUTPUT_MODE", "off").strip().lower())
    intelligent_output_max_fields: int = Field(default_factory=lambda: int(os.getenv("AOR_INTELLIGENT_OUTPUT_MAX_FIELDS", "8")))
    semantic_frame_mode: str = Field(default_factory=lambda: os.getenv("AOR_SEMANTIC_FRAME_MODE", "enforce").strip().lower())
    semantic_frame_max_depth: int = Field(default_factory=lambda: int(os.getenv("AOR_SEMANTIC_FRAME_MAX_DEPTH", "10")))
    semantic_frame_max_children: int = Field(default_factory=lambda: int(os.getenv("AOR_SEMANTIC_FRAME_MAX_CHILDREN", "8")))
    llm_stage_max_depth: int = Field(default_factory=lambda: int(os.getenv("AOR_LLM_STAGE_MAX_DEPTH", "10")))
    presentation_intent_max_depth: int = Field(default_factory=lambda: int(os.getenv("AOR_PRESENTATION_INTENT_MAX_DEPTH", "10")))
    enable_insight_layer: bool = Field(default_factory=lambda: _env_bool("AOR_ENABLE_INSIGHT_LAYER", True))
    enable_llm_insights: bool = Field(default_factory=lambda: _env_bool("AOR_ENABLE_LLM_INSIGHTS"))
    insight_max_facts: int = Field(default_factory=lambda: int(os.getenv("AOR_INSIGHT_MAX_FACTS", "50")))
    insight_max_input_chars: int = Field(default_factory=lambda: int(os.getenv("AOR_INSIGHT_MAX_INPUT_CHARS", "4000")))
    insight_max_output_chars: int = Field(default_factory=lambda: int(os.getenv("AOR_INSIGHT_MAX_OUTPUT_CHARS", "1500")))
    action_planner_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_ACTION_PLANNER_ENABLED", True))
    legacy_execution_planner_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_LEGACY_EXECUTION_PLANNER_ENABLED"))
    auto_artifacts_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_AUTO_ARTIFACTS_ENABLED", True))
    auto_artifact_row_threshold: int = Field(default_factory=lambda: int(os.getenv("AOR_AUTO_ARTIFACT_ROW_THRESHOLD", "50")))
    auto_artifact_dir: str = Field(default_factory=lambda: os.getenv("AOR_AUTO_ARTIFACT_DIR", "outputs"))
    auto_artifact_format: str = Field(default_factory=lambda: os.getenv("AOR_AUTO_ARTIFACT_FORMAT", "csv"))
    agent_ui_allow_raw_previews: bool = Field(default_factory=lambda: _env_bool("AOR_AGENT_UI_ALLOW_RAW_PREVIEWS", True))
    agent_ui_allow_full_payloads: bool = Field(default_factory=lambda: _env_bool("AOR_AGENT_UI_ALLOW_FULL_PAYLOADS"))
    agent_ui_llm_launch_conda_env: str = Field(
        default_factory=lambda: os.getenv("AOR_AGENT_UI_LLM_LAUNCH_CONDA_ENV", "vllm").strip()
    )
    agent_ui_llm_launch_command: str = Field(
        default_factory=lambda: os.getenv("AOR_AGENT_UI_LLM_LAUNCH_COMMAND", "").strip()
    )
    agent_ui_llm_launch_cwd: str | None = Field(default_factory=lambda: os.getenv("AOR_AGENT_UI_LLM_LAUNCH_CWD") or None)
    agent_ui_number_animation: str = Field(
        default_factory=lambda: os.getenv("AOR_AGENT_UI_NUMBER_ANIMATION", "odometer").strip().lower()
    )
    agent_ui_auto_immersive_min_width_px: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_UI_AUTO_IMMERSIVE_MIN_WIDTH_PX", "500"))
    )
    agent_memory_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_AGENT_MEMORY_ENABLED", True))
    agent_memory_prompt_max_chars: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_MEMORY_PROMPT_MAX_CHARS", "3000"))
    )
    agent_parameter_store_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_AGENT_PARAMETER_STORE_ENABLED", True)
    )
    agent_parameter_prompt_max_chars: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_PARAMETER_PROMPT_MAX_CHARS", "4000"))
    )
    agent_plan_cache_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_AGENT_PLAN_CACHE_ENABLED", True))
    agent_plan_cache_prompt_max_chars: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_PLAN_CACHE_PROMPT_MAX_CHARS", "4000"))
    )
    agent_plan_cache_similarity_threshold: float = Field(
        default_factory=lambda: float(os.getenv("AOR_AGENT_PLAN_CACHE_SIMILARITY_THRESHOLD", "0.86"))
    )
    agent_plan_cache_max_entries: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_PLAN_CACHE_MAX_ENTRIES", "5000"))
    )
    agent_command_template_cache_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_AGENT_COMMAND_TEMPLATE_CACHE_ENABLED", True)
    )
    agent_command_template_cache_prompt_max_chars: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_COMMAND_TEMPLATE_CACHE_PROMPT_MAX_CHARS", "4000"))
    )
    agent_command_template_cache_similarity_threshold: float = Field(
        default_factory=lambda: float(os.getenv("AOR_AGENT_COMMAND_TEMPLATE_CACHE_SIMILARITY_THRESHOLD", "0.82"))
    )
    agent_command_template_cache_secondary_similarity_threshold: float = Field(
        default_factory=lambda: float(os.getenv("AOR_AGENT_COMMAND_TEMPLATE_CACHE_SECONDARY_SIMILARITY_THRESHOLD", "0.15"))
    )
    agent_command_template_cache_max_entries: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_COMMAND_TEMPLATE_CACHE_MAX_ENTRIES", "5000"))
    )
    agent_computation_cache_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_AGENT_COMPUTATION_CACHE_ENABLED", True)
    )
    agent_computation_cache_prompt_max_chars: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_COMPUTATION_CACHE_PROMPT_MAX_CHARS", "4000"))
    )
    agent_computation_cache_similarity_threshold: float = Field(
        default_factory=lambda: float(os.getenv("AOR_AGENT_COMPUTATION_CACHE_SIMILARITY_THRESHOLD", "0.84"))
    )
    agent_computation_cache_max_entries: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_COMPUTATION_CACHE_MAX_ENTRIES", "5000"))
    )
    lrnt_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_LRNT_ENABLED", True))
    lrnt_similarity_threshold: float = Field(
        default_factory=lambda: float(os.getenv("AOR_LRNT_SIMILARITY_THRESHOLD", "0.92"))
    )
    lrnt_max_entries: int = Field(
        default_factory=lambda: int(os.getenv("AOR_LRNT_MAX_ENTRIES", "5000"))
    )
    agent_learning_ledger_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_AGENT_LEARNING_LEDGER_ENABLED", True)
    )
    agent_learning_ledger_auto_learn_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_AGENT_LEARNING_LEDGER_AUTO_LEARN_ENABLED", True)
    )
    lrdirect_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_LRDIRECT_ENABLED", True))
    reliability_mode: str = Field(
        default_factory=lambda: os.getenv("AOR_RELIABILITY_MODE", "standard").strip().lower()
    )
    reliability_max_recovery_probes: int = Field(
        default_factory=lambda: int(os.getenv("AOR_RELIABILITY_MAX_RECOVERY_PROBES", "3"))
    )
    reliability_max_autonomous_repair_attempts: int = Field(
        default_factory=lambda: int(os.getenv("AOR_RELIABILITY_MAX_AUTONOMOUS_REPAIR_ATTEMPTS", "2"))
    )
    reliability_weak_model_plan_action_cap: int = Field(
        default_factory=lambda: int(os.getenv("AOR_RELIABILITY_WEAK_MODEL_PLAN_ACTION_CAP", "4"))
    )
    reliability_verifier_enforced: bool = Field(
        default_factory=lambda: _env_bool("AOR_RELIABILITY_VERIFIER_ENFORCED", True)
    )
    reliability_approval_envelope_budget: int = Field(
        default_factory=lambda: int(os.getenv("AOR_RELIABILITY_APPROVAL_ENVELOPE_BUDGET", "2"))
    )
    audio_transcriber_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_AUDIO_TRANSCRIBER_ENABLED", True)
    )
    audio_transcriber_service_url: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_SERVICE_URL", "http://localhost:8012").strip()
    )
    audio_transcriber_proxy_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("AOR_AUDIO_TRANSCRIBER_PROXY_TIMEOUT_SECONDS", "180"))
    )
    audio_transcriber_engine: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_ENGINE", "whisper_cpp").strip().lower()
    )
    audio_transcriber_model_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_PATH", "/data/models/whisper/ggml-large-v3.bin")
        )
    )
    audio_transcriber_model_tier: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_TIER", "high").strip().lower()
    )
    audio_transcriber_binary: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_BINARY", "whisper-cli").strip()
    )
    audio_transcriber_ffmpeg_binary: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_FFMPEG_BINARY", "ffmpeg").strip()
    )
    audio_transcriber_ffprobe_binary: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_FFPROBE_BINARY", "ffprobe").strip()
    )
    audio_transcriber_max_seconds: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AUDIO_TRANSCRIBER_MAX_SECONDS", "120"))
    )
    audio_transcriber_max_upload_mb: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB", "25"))
    )
    audio_transcriber_language: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_LANGUAGE", "auto").strip().lower()
    )
    agent_events_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_AGENT_EVENTS_ENABLED", True))
    agent_events_poll_seconds: float = Field(
        default_factory=lambda: float(os.getenv("AOR_AGENT_EVENTS_POLL_SECONDS", "10"))
    )
    agent_events_max_run_history: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AGENT_EVENTS_MAX_RUN_HISTORY", "5000"))
    )
    llm_operator_enabled: bool = Field(default_factory=lambda: _env_bool("AOR_LLM_OPERATOR_ENABLED", True))
    llm_operator_requires_approval: bool = Field(default_factory=lambda: _env_bool("AOR_LLM_OPERATOR_REQUIRES_APPROVAL", True))
    llm_operator_max_actions: int = Field(default_factory=lambda: int(os.getenv("AOR_LLM_OPERATOR_MAX_ACTIONS", "8")))
    operator_policy_profile: str = Field(
        default_factory=lambda: os.getenv("AOR_OPERATOR_POLICY_PROFILE", "assisted").strip().lower()
    )
    reasoning_profile: str = Field(
        default_factory=lambda: os.getenv("AOR_REASONING_PROFILE", "balanced").strip().lower()
    )
    repair_profile: str = Field(
        default_factory=lambda: os.getenv("AOR_REPAIR_PROFILE", "balanced").strip().lower()
    )
    workflow_execution_mode: str = Field(
        default_factory=lambda: os.getenv("AOR_WORKFLOW_EXECUTION_MODE", "streaming").strip().lower()
    )
    prompt_rephrase_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_PROMPT_REPHRASE_ENABLED", True)
    )
    response_streaming_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_RESPONSE_STREAMING_ENABLED", True)
    )
    shell_input_bindings_mode: str = Field(
        default_factory=lambda: os.getenv("AOR_SHELL_INPUT_BINDINGS_MODE", "allow").strip().lower()
    )
    llm_operator_cardinality_judge_mode: str = Field(
        default_factory=lambda: os.getenv("AOR_LLM_OPERATOR_CARDINALITY_JUDGE_MODE", "auto").strip().lower()
    )
    llm_operator_verbose_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_LLM_OPERATOR_VERBOSE_ENABLED", False)
    )
    online_lookup_gemini_api_key: str = Field(
        default_factory=lambda: (
            os.getenv("AOR_GEMINI_GROUNDING_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or ""
        ).strip()
    )
    online_lookup_gemini_model: str = Field(
        default_factory=lambda: os.getenv("AOR_GEMINI_GROUNDING_MODEL", "gemini-2.5-flash").strip()
    )
    online_lookup_gemini_api_version: str = Field(
        default_factory=lambda: os.getenv("AOR_GEMINI_GROUNDING_API_VERSION", "v1beta").strip()
    )
    online_ai_check_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("AOR_ONLINE_AI_CHECK_TIMEOUT_SECONDS", "120"))
    )
    online_ai_check_reuse_browser: bool = Field(
        default_factory=lambda: _env_bool("AOR_ONLINE_AI_CHECK_REUSE_BROWSER", True)
    )
    online_ai_check_headless: bool = Field(
        default_factory=lambda: _env_bool("AOR_ONLINE_AI_CHECK_HEADLESS", False)
    )
    online_ai_check_profile_dir: Path = Field(
        default_factory=lambda: Path(os.getenv("AOR_ONLINE_AI_CHECK_PROFILE_DIR", ""))
        if os.getenv("AOR_ONLINE_AI_CHECK_PROFILE_DIR")
        else Path.cwd() / "artifacts" / "playwright-duckai-profile"
    )
    llm_operator_step_validation_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_LLM_OPERATOR_STEP_VALIDATION_ENABLED", True)
    )
    llm_operator_verification_enforced: bool = Field(
        default_factory=lambda: _env_bool("AOR_LLM_OPERATOR_VERIFICATION_ENFORCED", True)
    )
    operator_workspace_cwd_guard_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_OPERATOR_WORKSPACE_CWD_GUARD_ENABLED", False)
    )
    llm_operator_formatter_source_preview_chars: int = Field(
        default_factory=lambda: int(os.getenv("AOR_LLM_OPERATOR_FORMATTER_SOURCE_PREVIEW_CHARS", "3000"))
    )
    agent_clarification_mode: str = Field(
        default_factory=lambda: normalize_agent_clarification_mode(
            os.getenv("AOR_AGENT_CLARIFICATION_MODE", ""),
        )
    )
    llm_operator_max_clarification_rounds: int = Field(
        default_factory=lambda: int(os.getenv("AOR_LLM_OPERATOR_MAX_CLARIFICATION_ROUNDS", "3"))
    )
    llm_operator_conversation_max_turns: int = Field(
        default_factory=lambda: int(os.getenv("AOR_LLM_OPERATOR_CONVERSATION_MAX_TURNS", "6"))
    )
    llm_operator_conversation_max_context_chars: int = Field(
        default_factory=lambda: int(os.getenv("AOR_LLM_OPERATOR_CONVERSATION_MAX_CONTEXT_CHARS", "24000"))
    )
    llm_operator_conversation_max_record_chars: int = Field(
        default_factory=lambda: int(os.getenv("AOR_LLM_OPERATOR_CONVERSATION_MAX_RECORD_CHARS", "8000"))
    )
    max_plan_retries: int = 2
    openai_compat_enabled: bool = True
    openai_compat_model_name: str = DEFAULT_OPENAI_COMPAT_MODEL_NAME
    openai_compat_spec_path: str = DEFAULT_COMPAT_SPEC_PATH

    @property
    def available_nodes(self) -> list[str]:
        """Available nodes for Settings instances.

        Inputs:
            Uses module or instance state; no caller-supplied data parameters are required.

        Returns:
            Returns the computed property value for callers that need this runtime fact.

        Used by:
            Used by OpenFABRIC runtime support through Settings.available_nodes calls and related tests.
        """
        raw_value = str(self.available_nodes_raw or "")
        nodes: list[str] = []
        seen: set[str] = set()
        for chunk in raw_value.split(","):
            node = chunk.strip()
            if not node or node in seen:
                continue
            nodes.append(node)
            seen.add(node)
        for node in self.gateway_endpoints:
            if node in seen:
                continue
            nodes.append(node)
            seen.add(node)
        default_node = str(self.default_node or "").strip()
        if default_node and default_node not in seen:
            nodes.append(default_node)
        if not nodes:
            nodes.append("localhost")
        return nodes

    def resolved_default_node(self) -> str | None:
        """Resolved default node for Settings instances.

        Inputs:
            Uses module or instance state; no caller-supplied data parameters are required.

        Returns:
            Returns the computed value described by the function name and type hints.

        Used by:
            Used by OpenFABRIC runtime support through Settings.resolved_default_node calls and related tests.
        """
        normalized_default = str(self.default_node or "").strip()
        if normalized_default:
            return normalized_default
        available = self.available_nodes
        if len(available) == 1:
            return available[0]
        return None

    def resolve_node(self, node: str = "") -> str:
        """Resolve node for Settings instances.

        Inputs:
            Receives node for this Settings method; type hints and validators define accepted shapes.

        Returns:
            Returns the computed value described by the function name and type hints.

        Used by:
            Used by OpenFABRIC runtime support through Settings.resolve_node calls and related tests.
        """
        requested = str(node or "").strip()
        if not requested:
            requested = str(self.resolved_default_node() or "").strip()
        if not requested:
            raise ValueError("No node specified and no default node is configured.")
        if requested not in self.available_nodes:
            allowed = ", ".join(self.available_nodes) or "<none configured>"
            raise ValueError(f"Node is not available: {requested}. Available nodes: {allowed}.")
        return requested

    def resolve_gateway_url(self, node: str = "") -> str:
        """Resolve gateway url for Settings instances.

        Inputs:
            Receives node for this Settings method; type hints and validators define accepted shapes.

        Returns:
            Returns the computed value described by the function name and type hints.

        Used by:
            Used by OpenFABRIC runtime support through Settings.resolve_gateway_url calls and related tests.
        """
        resolved_node = self.resolve_node(node)
        gateway_url = str(self.gateway_endpoints.get(resolved_node, "") or self.gateway_url or "").strip()
        if not gateway_url:
            raise ValueError(f"Gateway URL is not configured for node: {resolved_node}.")
        return gateway_url

    def resolve_openai_compat_spec_path(self) -> Path | None:
        """Resolve openai compat spec path for Settings instances.

        Inputs:
            Uses module or instance state; no caller-supplied data parameters are required.

        Returns:
            Returns the computed value described by the function name and type hints.

        Used by:
            Used by OpenFABRIC runtime support through Settings.resolve_openai_compat_spec_path calls and related tests.
        """
        raw_path = str(self.openai_compat_spec_path or "").strip()
        if is_compat_spec_placeholder(raw_path):
            return None
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            return candidate
        return (self.workspace_root / candidate).resolve()

    @model_validator(mode="after")
    def validate_default_node(self) -> "Settings":
        """Validate validate default node invariants before runtime data crosses this boundary.

        Inputs:
            Uses module or instance state; no caller-supplied data parameters are required.

        Returns:
            Returns the validated value or model instance after enforcing the declared invariant.

        Used by:
            Used by OpenFABRIC runtime support through Settings.validate_default_node calls and related tests.
        """
        if self.server_port <= 0 or self.server_port > 65535:
            raise ValueError("server_port must be between 1 and 65535.")
        normalized_default = str(self.default_node or "").strip()
        self.default_node = normalized_default or None
        normalized_database_url = str(self.sql_database_url or "").strip()
        self.sql_database_url = normalized_database_url or None
        normalized_sql_databases: dict[str, str] = {}
        for raw_name, raw_url in dict(self.sql_databases or {}).items():
            name = str(raw_name or "").strip()
            url = str(raw_url or "").strip()
            if not name:
                raise ValueError("SQL database names must be non-empty.")
            if not url:
                raise ValueError(f"SQL database URL must be non-empty for database {name!r}.")
            normalized_sql_databases[name] = url
        self.sql_databases = normalized_sql_databases
        normalized_sql_default = str(self.sql_default_database or "").strip()
        self.sql_default_database = normalized_sql_default or None
        self.llm_base_url = str(self.llm_base_url or "").strip()
        self.llm_api_key = str(self.llm_api_key or "").strip()
        self.default_model = str(self.default_model or "").strip()
        if not self.llm_base_url:
            raise ValueError("llm_base_url is required.")
        if not self.llm_api_key:
            raise ValueError("llm_api_key is required.")
        self.default_model = self.default_model or "auto"
        if self.default_temperature < 0:
            raise ValueError("default_temperature must be zero or greater.")
        if self.llm_timeout_seconds <= 0:
            raise ValueError("llm_timeout_seconds must be greater than zero.")
        if self.llm_max_tokens < 0:
            raise ValueError("llm_max_tokens must be zero or greater.")
        if self.sql_row_limit < 0:
            raise ValueError("sql_row_limit must be zero or greater.")
        if self.sql_timeout_seconds <= 0:
            raise ValueError("sql_timeout_seconds must be greater than zero.")
        self.sql_agent_chat_route_mode = (
            str(self.sql_agent_chat_route_mode or "agentic").strip().lower() or "agentic"
        )
        if self.sql_agent_chat_route_mode not in {"agentic", "direct"}:
            self.sql_agent_chat_route_mode = "agentic"
        if self.sql_agent_default_limit <= 0:
            raise ValueError("sql_agent_default_limit must be greater than zero.")
        if self.sql_agent_max_rows <= 0:
            raise ValueError("sql_agent_max_rows must be greater than zero.")
        if self.sql_agent_max_repair_attempts < 0:
            raise ValueError("sql_agent_max_repair_attempts must be zero or greater.")
        self.shell_mode = str(self.shell_mode or "read_only").strip().lower() or "read_only"
        if self.shell_mode not in {"disabled", "read_only", "approval_required", "permissive"}:
            raise ValueError("shell_mode must be one of: disabled, read_only, approval_required, permissive.")
        if self.shell_max_output_chars <= 0:
            raise ValueError("shell_max_output_chars must be greater than zero.")
        if self.shell_command_timeout_seconds <= 0:
            raise ValueError("shell_command_timeout_seconds must be greater than zero.")
        if self.llm_operator_max_actions <= 0:
            raise ValueError("llm_operator_max_actions must be greater than zero.")
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
        self.llm_operator_verbose_enabled = bool(self.llm_operator_verbose_enabled)
        self.online_lookup_gemini_api_key = str(self.online_lookup_gemini_api_key or "").strip()
        self.online_lookup_gemini_model = (
            str(self.online_lookup_gemini_model or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        )
        self.online_lookup_gemini_api_version = (
            str(self.online_lookup_gemini_api_version or "v1beta").strip() or "v1beta"
        )
        if self.online_ai_check_timeout_seconds <= 0:
            raise ValueError("online_ai_check_timeout_seconds must be greater than zero.")
        self.online_ai_check_reuse_browser = bool(self.online_ai_check_reuse_browser)
        self.online_ai_check_headless = bool(self.online_ai_check_headless)
        profile_dir = Path(self.online_ai_check_profile_dir).expanduser()
        if not profile_dir.is_absolute():
            profile_dir = Path(self.workspace_root) / profile_dir
        self.online_ai_check_profile_dir = profile_dir
        self.agent_ui_number_animation = str(self.agent_ui_number_animation or "odometer").strip().lower()
        if self.agent_ui_number_animation not in {"odometer", "fade", "slide", "pop", "flip", "none"}:
            self.agent_ui_number_animation = "odometer"
        self.agent_ui_auto_immersive_min_width_px = max(
            0,
            min(4000, int(self.agent_ui_auto_immersive_min_width_px or 0)),
        )
        if self.llm_operator_formatter_source_preview_chars <= 0:
            raise ValueError("llm_operator_formatter_source_preview_chars must be greater than zero.")
        self.operator_workspace_cwd_guard_enabled = bool(self.operator_workspace_cwd_guard_enabled)
        if self.agent_memory_prompt_max_chars <= 0:
            raise ValueError("agent_memory_prompt_max_chars must be greater than zero.")
        if self.agent_plan_cache_prompt_max_chars <= 0:
            raise ValueError("agent_plan_cache_prompt_max_chars must be greater than zero.")
        if self.agent_plan_cache_max_entries <= 0:
            raise ValueError("agent_plan_cache_max_entries must be greater than zero.")
        if not 0.0 <= self.agent_plan_cache_similarity_threshold <= 1.0:
            raise ValueError("agent_plan_cache_similarity_threshold must be between 0 and 1.")
        if self.agent_command_template_cache_prompt_max_chars <= 0:
            raise ValueError("agent_command_template_cache_prompt_max_chars must be greater than zero.")
        if self.agent_command_template_cache_max_entries <= 0:
            raise ValueError("agent_command_template_cache_max_entries must be greater than zero.")
        if not 0.0 <= self.agent_command_template_cache_similarity_threshold <= 1.0:
            raise ValueError("agent_command_template_cache_similarity_threshold must be between 0 and 1.")
        if not 0.0 <= self.agent_command_template_cache_secondary_similarity_threshold <= 1.0:
            raise ValueError("agent_command_template_cache_secondary_similarity_threshold must be between 0 and 1.")
        if self.agent_computation_cache_prompt_max_chars <= 0:
            raise ValueError("agent_computation_cache_prompt_max_chars must be greater than zero.")
        if self.agent_computation_cache_max_entries <= 0:
            raise ValueError("agent_computation_cache_max_entries must be greater than zero.")
        if not 0.0 <= self.agent_computation_cache_similarity_threshold <= 1.0:
            raise ValueError("agent_computation_cache_similarity_threshold must be between 0 and 1.")
        if self.lrnt_max_entries <= 0:
            raise ValueError("lrnt_max_entries must be greater than zero.")
        if not 0.0 <= self.lrnt_similarity_threshold <= 1.0:
            raise ValueError("lrnt_similarity_threshold must be between 0 and 1.")
        self.audio_transcriber_service_url = (
            str(self.audio_transcriber_service_url or "http://localhost:8012").strip()
            or "http://localhost:8012"
        ).rstrip("/")
        if self.audio_transcriber_proxy_timeout_seconds <= 0:
            raise ValueError("audio_transcriber_proxy_timeout_seconds must be greater than zero.")
        self.audio_transcriber_engine = (
            str(self.audio_transcriber_engine or "whisper_cpp").strip().lower() or "whisper_cpp"
        )
        if self.audio_transcriber_engine not in {"whisper_cpp"}:
            raise ValueError("audio_transcriber_engine must be whisper_cpp.")
        audio_model_path = Path(self.audio_transcriber_model_path).expanduser()
        if not audio_model_path.is_absolute():
            audio_model_path = Path(self.workspace_root) / audio_model_path
        model_tier = str(self.audio_transcriber_model_tier or "high").strip().lower()
        if model_tier not in {"low", "med", "high"}:
            model_tier = "high"
            self.audio_transcriber_model_tier = model_tier
        if os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_PATH") is None:
            default_model_path = Path(
                os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_PATH", "/data/models/whisper/ggml-large-v3.bin")
            ).expanduser()
            requested_model_path = Path(audio_model_path).expanduser()
            if requested_model_path == default_model_path:
                model_dir = Path(
                    os.getenv(
                        "AOR_AUDIO_TRANSCRIBER_MODEL_DIR",
                        os.fspath(requested_model_path.parent),
                    )
                ).expanduser()
                audio_model_path = _resolve_model_path_for_tier(model_tier, model_dir)
            else:
                audio_model_path = requested_model_path
        else:
            audio_model_path = Path(self.audio_transcriber_model_path).expanduser()
        self.audio_transcriber_model_path = audio_model_path
        self.audio_transcriber_binary = str(self.audio_transcriber_binary or "whisper-cli").strip() or "whisper-cli"
        self.audio_transcriber_ffmpeg_binary = str(self.audio_transcriber_ffmpeg_binary or "ffmpeg").strip() or "ffmpeg"
        self.audio_transcriber_ffprobe_binary = str(self.audio_transcriber_ffprobe_binary or "ffprobe").strip() or "ffprobe"
        if self.audio_transcriber_max_seconds <= 0:
            raise ValueError("audio_transcriber_max_seconds must be greater than zero.")
        if self.audio_transcriber_max_upload_mb <= 0:
            raise ValueError("audio_transcriber_max_upload_mb must be greater than zero.")
        self.audio_transcriber_max_seconds = min(3600, int(self.audio_transcriber_max_seconds))
        self.audio_transcriber_max_upload_mb = min(1024, int(self.audio_transcriber_max_upload_mb))
        self.audio_transcriber_language = (
            str(self.audio_transcriber_language or "auto").strip().lower() or "auto"
        )
        if self.agent_events_poll_seconds <= 0:
            raise ValueError("agent_events_poll_seconds must be greater than zero.")
        if self.agent_events_max_run_history <= 0:
            raise ValueError("agent_events_max_run_history must be greater than zero.")
        explicit_fields = getattr(self, "model_fields_set", set())
        env_mode = str(os.getenv("AOR_AGENT_CLARIFICATION_MODE", "") or "").strip()
        mode_was_supplied = "agent_clarification_mode" in explicit_fields or bool(env_mode)
        self.agent_clarification_mode = normalize_agent_clarification_mode(
            self.agent_clarification_mode if mode_was_supplied else "",
        )
        if self.llm_operator_max_clarification_rounds < 0:
            raise ValueError("llm_operator_max_clarification_rounds must be zero or greater.")
        if self.llm_operator_conversation_max_turns <= 0:
            raise ValueError("llm_operator_conversation_max_turns must be greater than zero.")
        if self.llm_operator_conversation_max_context_chars <= 0:
            raise ValueError("llm_operator_conversation_max_context_chars must be greater than zero.")
        if self.llm_operator_conversation_max_record_chars <= 0:
            raise ValueError("llm_operator_conversation_max_record_chars must be greater than zero.")
        if self.shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be greater than zero.")
        if self.worker_join_timeout_seconds <= 0:
            raise ValueError("worker_join_timeout_seconds must be greater than zero.")
        if self.tool_process_kill_grace_seconds <= 0:
            raise ValueError("tool_process_kill_grace_seconds must be greater than zero.")
        self.runtime_timezone = str(self.runtime_timezone or "").strip()
        if self.max_plan_retries < 0:
            raise ValueError("max_plan_retries must be zero or greater.")
        self.presentation_mode = str(self.presentation_mode or "user").strip().lower() or "user"
        if self.presentation_mode not in {"user", "debug", "raw"}:
            raise ValueError("presentation_mode must be one of: user, debug, raw.")
        self.response_render_mode = str(self.response_render_mode or self.presentation_mode or "user").strip().lower() or "user"
        if self.response_render_mode not in {"user", "debug", "raw"}:
            raise ValueError("response_render_mode must be one of: user, debug, raw.")
        if self.llm_summary_max_facts <= 0:
            raise ValueError("llm_summary_max_facts must be greater than zero.")
        if self.presentation_llm_max_facts <= 0:
            raise ValueError("presentation_llm_max_facts must be greater than zero.")
        if self.presentation_llm_max_input_chars <= 0:
            raise ValueError("presentation_llm_max_input_chars must be greater than zero.")
        if self.presentation_llm_max_output_chars <= 0:
            raise ValueError("presentation_llm_max_output_chars must be greater than zero.")
        self.intelligent_output_mode = str(self.intelligent_output_mode or "off").strip().lower() or "off"
        if self.intelligent_output_mode not in {"off", "compare", "replace"}:
            raise ValueError("intelligent_output_mode must be one of: off, compare, replace.")
        if self.intelligent_output_max_fields <= 0:
            raise ValueError("intelligent_output_max_fields must be greater than zero.")
        self.semantic_frame_mode = str(self.semantic_frame_mode or "enforce").strip().lower() or "enforce"
        if self.semantic_frame_mode not in {"off", "shadow", "enforce"}:
            raise ValueError("semantic_frame_mode must be one of: off, shadow, enforce.")
        if self.semantic_frame_max_depth <= 0:
            raise ValueError("semantic_frame_max_depth must be greater than zero.")
        if self.semantic_frame_max_children <= 0:
            raise ValueError("semantic_frame_max_children must be greater than zero.")
        if self.llm_stage_max_depth <= 0:
            raise ValueError("llm_stage_max_depth must be greater than zero.")
        if self.presentation_intent_max_depth <= 0:
            raise ValueError("presentation_intent_max_depth must be greater than zero.")
        if self.llm_context_window_tokens <= 0:
            raise ValueError("llm_context_window_tokens must be greater than zero.")
        if self.insight_max_facts <= 0:
            raise ValueError("insight_max_facts must be greater than zero.")
        if self.insight_max_input_chars <= 0:
            raise ValueError("insight_max_input_chars must be greater than zero.")
        if self.insight_max_output_chars <= 0:
            raise ValueError("insight_max_output_chars must be greater than zero.")
        if self.auto_artifact_row_threshold < 0:
            raise ValueError("auto_artifact_row_threshold must be zero or greater.")
        self.auto_artifact_dir = str(self.auto_artifact_dir or "outputs").strip() or "outputs"
        self.auto_artifact_format = str(self.auto_artifact_format or "csv").strip().lower() or "csv"
        if self.auto_artifact_format not in {"csv"}:
            raise ValueError("auto_artifact_format must be csv.")
        self.reliability_mode = str(self.reliability_mode or "standard").strip().lower()
        if self.reliability_mode not in {"off", "standard", "aggressive"}:
            self.reliability_mode = "standard"
        self.reliability_max_recovery_probes = max(
            0,
            int(self.reliability_max_recovery_probes),
        )
        self.reliability_max_autonomous_repair_attempts = max(
            0,
            int(self.reliability_max_autonomous_repair_attempts),
        )
        self.reliability_weak_model_plan_action_cap = max(
            1,
            int(self.reliability_weak_model_plan_action_cap),
        )
        self.reliability_approval_envelope_budget = max(
            0,
            int(self.reliability_approval_envelope_budget),
        )
        self.openai_compat_model_name = normalize_openai_compat_model_name(self.openai_compat_model_name)
        normalized_spec_path = str(self.openai_compat_spec_path or "").strip()
        self.openai_compat_spec_path = normalized_spec_path or DEFAULT_COMPAT_SPEC_PATH
        normalized_endpoints: dict[str, str] = {}
        for raw_node, raw_url in dict(self.gateway_endpoints or {}).items():
            node = str(raw_node or "").strip()
            url = str(raw_url or "").strip()
            if not node:
                raise ValueError("Gateway endpoint names must be non-empty.")
            if not url:
                raise ValueError(f"Gateway URL must be non-empty for node {node!r}.")
            normalized_endpoints[node] = url
        self.gateway_endpoints = normalized_endpoints
        if self.default_node and self.default_node not in self.available_nodes:
            allowed = ", ".join(self.available_nodes) or "<none configured>"
            raise ValueError(f"Default node must be one of the available nodes. Available nodes: {allowed}.")
        return self


@lru_cache(maxsize=8)
def _cached_settings(config_path: str, cwd: str) -> Settings:
    """Handle the internal cached settings helper path for this module.

    Inputs:
        Receives config_path, cwd for this function; type hints and validators define accepted shapes.

    Returns:
        Returns the computed value described by the function name and type hints.

    Used by:
        Used by OpenFABRIC runtime support code paths that import or call agent_runtime.api.config._cached_settings.
    """
    app_config, resolved_config_path = load_app_config(config_path=config_path or None, cwd=cwd or None)
    workspace_root = Path(cwd).resolve()
    run_store_path = workspace_root / "artifacts" / "runtime.db"
    if app_config.runtime.run_store_path:
        configured_run_store_path = Path(app_config.runtime.run_store_path).expanduser()
        run_store_path = (
            configured_run_store_path
            if configured_run_store_path.is_absolute()
            else workspace_root / configured_run_store_path
        )
    agent_memory_db_path = Path(
        os.getenv("AOR_AGENT_MEMORY_DB_PATH", str(workspace_root / "artifacts" / "agent_memory.db"))
    ).expanduser()
    if not agent_memory_db_path.is_absolute():
        agent_memory_db_path = workspace_root / agent_memory_db_path
    agent_prompts_db_path = Path(
        os.getenv("AOR_AGENT_PROMPTS_DB_PATH", str(workspace_root / "artifacts" / "prompts.db"))
    ).expanduser()
    if not agent_prompts_db_path.is_absolute():
        agent_prompts_db_path = workspace_root / agent_prompts_db_path
    agent_gateways_db_path = Path(
        os.getenv("AOR_AGENT_GATEWAYS_DB_PATH", str(workspace_root / "artifacts" / "agent_gateways.db"))
    ).expanduser()
    if not agent_gateways_db_path.is_absolute():
        agent_gateways_db_path = workspace_root / agent_gateways_db_path
    agent_plan_cache_db_path = Path(
        os.getenv("AOR_AGENT_PLAN_CACHE_DB_PATH", str(workspace_root / "artifacts" / "agent_plan_cache.db"))
    ).expanduser()
    if not agent_plan_cache_db_path.is_absolute():
        agent_plan_cache_db_path = workspace_root / agent_plan_cache_db_path
    agent_lrn_total_tasks_db_path = Path(
        os.getenv("AOR_LRNT_DB_PATH", str(workspace_root / "artifacts" / "agent_lrn_total_tasks.db"))
    ).expanduser()
    if not agent_lrn_total_tasks_db_path.is_absolute():
        agent_lrn_total_tasks_db_path = workspace_root / agent_lrn_total_tasks_db_path
    agent_command_template_cache_db_path = Path(
        os.getenv(
            "AOR_AGENT_COMMAND_TEMPLATE_CACHE_DB_PATH",
            str(workspace_root / "artifacts" / "agent_command_template_cache.db"),
        )
    ).expanduser()
    if not agent_command_template_cache_db_path.is_absolute():
        agent_command_template_cache_db_path = workspace_root / agent_command_template_cache_db_path
    agent_computation_cache_db_path = Path(
        os.getenv(
            "AOR_AGENT_COMPUTATION_CACHE_DB_PATH",
            str(workspace_root / "artifacts" / "agent_computation_cache.db"),
        )
    ).expanduser()
    if not agent_computation_cache_db_path.is_absolute():
        agent_computation_cache_db_path = workspace_root / agent_computation_cache_db_path
    agent_learning_ledger_db_path = Path(
        os.getenv(
            "AOR_AGENT_LEARNING_LEDGER_DB_PATH",
            str(workspace_root / "artifacts" / "agent_learning_ledger.db"),
        )
    ).expanduser()
    if not agent_learning_ledger_db_path.is_absolute():
        agent_learning_ledger_db_path = workspace_root / agent_learning_ledger_db_path
    agent_events_db_path = Path(
        os.getenv("AOR_AGENT_EVENTS_DB_PATH", str(workspace_root / "artifacts" / "agent_events.db"))
    ).expanduser()
    if not agent_events_db_path.is_absolute():
        agent_events_db_path = workspace_root / agent_events_db_path
    agent_command_allowlist_db_path = Path(
        os.getenv(
            "AOR_AGENT_COMMAND_ALLOWLIST_DB_PATH",
            str(workspace_root / "artifacts" / "agent_command_allowlist.db"),
        )
    ).expanduser()
    if not agent_command_allowlist_db_path.is_absolute():
        agent_command_allowlist_db_path = workspace_root / agent_command_allowlist_db_path
    agent_chats_db_path = Path(
        os.getenv("AOR_AGENT_CHATS_DB_PATH", str(workspace_root / "artifacts" / "chats.db"))
    ).expanduser()
    if not agent_chats_db_path.is_absolute():
        agent_chats_db_path = workspace_root / agent_chats_db_path
    agent_ui_settings_db_path = Path(
        os.getenv(
            "AOR_AGENT_UI_SETTINGS_DB_PATH",
            str(workspace_root / "artifacts" / "agent_ui_settings.db"),
        )
    ).expanduser()
    if not agent_ui_settings_db_path.is_absolute():
        agent_ui_settings_db_path = workspace_root / agent_ui_settings_db_path
    online_ai_check_profile_dir = Path(
        os.getenv(
            "AOR_ONLINE_AI_CHECK_PROFILE_DIR",
            str(workspace_root / "artifacts" / "playwright-duckai-profile"),
        )
    ).expanduser()
    if not online_ai_check_profile_dir.is_absolute():
        online_ai_check_profile_dir = workspace_root / online_ai_check_profile_dir
    audio_transcriber_model_tier = os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_TIER", "high").strip().lower() or "high"
    audio_transcriber_model_path = Path(
        os.getenv(
            "AOR_AUDIO_TRANSCRIBER_MODEL_PATH",
            str(
                _resolve_model_path_for_tier(
                    audio_transcriber_model_tier,
                    Path(
                        os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_DIR", "/data/models/whisper")
                    ).expanduser(),
                )
            ),
        )
    ).expanduser()
    if not audio_transcriber_model_path.is_absolute():
        audio_transcriber_model_path = workspace_root / audio_transcriber_model_path
    settings = Settings(
        workspace_root=workspace_root,
        prompts_root=workspace_root / "prompts",
        run_store_path=run_store_path,
        agent_gateways_db_path=agent_gateways_db_path,
        agent_memory_db_path=agent_memory_db_path,
        agent_prompts_db_path=agent_prompts_db_path,
        agent_plan_cache_db_path=agent_plan_cache_db_path,
        agent_lrn_total_tasks_db_path=agent_lrn_total_tasks_db_path,
        agent_command_template_cache_db_path=agent_command_template_cache_db_path,
        agent_computation_cache_db_path=agent_computation_cache_db_path,
        agent_learning_ledger_db_path=agent_learning_ledger_db_path,
        agent_events_db_path=agent_events_db_path,
        agent_command_allowlist_db_path=agent_command_allowlist_db_path,
        agent_chats_db_path=agent_chats_db_path,
        agent_ui_settings_db_path=agent_ui_settings_db_path,
        audio_transcriber_model_path=audio_transcriber_model_path,
        audio_transcriber_model_tier=audio_transcriber_model_tier,
        app_config_path=resolved_config_path,
        server_host=app_config.server.host,
        server_port=app_config.server.port,
        llm_base_url=app_config.llm.base_url,
        llm_api_key=app_config.llm.api_key,
        default_model=app_config.llm.default_model,
        default_temperature=app_config.llm.default_temperature,
        llm_timeout_seconds=app_config.llm.timeout_seconds,
        llm_max_tokens=int(os.getenv("AOR_LLM_MAX_TOKENS", str(app_config.llm.max_tokens))),
        llm_context_window_tokens=int(
            os.getenv("AOR_LLM_CONTEXT_WINDOW_TOKENS", str(app_config.llm.context_window_tokens))
        ),
        allow_destructive_shell=app_config.runtime.allow_destructive_shell,
        shell_mode=os.getenv("AOR_SHELL_MODE", "").strip().lower() or app_config.runtime.shell_mode,
        shell_allow_mutation_with_approval=_env_bool(
            "AOR_SHELL_ALLOW_MUTATION_WITH_APPROVAL", app_config.runtime.shell_allow_mutation_with_approval
        ),
        shell_allowed_roots_raw=os.getenv("AOR_SHELL_ALLOWED_ROOTS") or app_config.runtime.shell_allowed_roots,
        shell_default_cwd=os.getenv("AOR_SHELL_DEFAULT_CWD") or app_config.runtime.shell_default_cwd,
        shell_max_output_chars=int(os.getenv("AOR_SHELL_MAX_OUTPUT_CHARS", str(app_config.runtime.shell_max_output_chars))),
        shell_command_timeout_seconds=int(
            os.getenv("AOR_SHELL_COMMAND_TIMEOUT_SECONDS", str(app_config.runtime.shell_command_timeout_seconds))
        ),
        shutdown_grace_seconds=float(os.getenv("AOR_SHUTDOWN_GRACE_SECONDS", str(app_config.runtime.shutdown_grace_seconds))),
        worker_join_timeout_seconds=float(
            os.getenv("AOR_WORKER_JOIN_TIMEOUT_SECONDS", str(app_config.runtime.worker_join_timeout_seconds))
        ),
        tool_process_kill_grace_seconds=float(
            os.getenv("AOR_TOOL_PROCESS_KILL_GRACE_SECONDS", str(app_config.runtime.tool_process_kill_grace_seconds))
        ),
        runtime_timezone=os.getenv("AOR_RUNTIME_TIMEZONE", "").strip() or app_config.runtime.runtime_timezone,
        enable_llm_intent_extraction=app_config.runtime.enable_llm_intent_extraction,
        enable_sql_llm_generation=(
            os.getenv("AOR_ENABLE_SQL_LLM_GENERATION", "").strip().lower() in {"1", "true", "yes", "on"}
            or app_config.runtime.enable_sql_llm_generation
        ),
        presentation_mode=os.getenv("AOR_PRESENTATION_MODE", "").strip().lower() or app_config.runtime.presentation_mode,
        enable_llm_summary=(
            os.getenv("AOR_ENABLE_LLM_SUMMARY", "").strip().lower() in {"1", "true", "yes", "on"}
            or app_config.runtime.enable_llm_summary
        ),
        llm_summary_max_facts=int(os.getenv("AOR_LLM_SUMMARY_MAX_FACTS", str(app_config.runtime.llm_summary_max_facts))),
        include_internal_telemetry=(
            os.getenv("AOR_INCLUDE_INTERNAL_TELEMETRY", "").strip().lower() in {"1", "true", "yes", "on"}
            or app_config.runtime.include_internal_telemetry
        ),
        response_render_mode=(
            os.getenv("AOR_RESPONSE_RENDER_MODE", "").strip().lower()
            or os.getenv("AOR_PRESENTATION_MODE", "").strip().lower()
            or app_config.runtime.response_render_mode
            or app_config.runtime.presentation_mode
        ),
        show_executed_commands=_env_bool("AOR_SHOW_EXECUTED_COMMANDS", app_config.runtime.show_executed_commands),
        show_validation_events=_env_bool("AOR_SHOW_VALIDATION_EVENTS", app_config.runtime.show_validation_events),
        show_planner_events=_env_bool("AOR_SHOW_PLANNER_EVENTS", app_config.runtime.show_planner_events),
        show_tool_events=_env_bool("AOR_SHOW_TOOL_EVENTS", app_config.runtime.show_tool_events),
        openwebui_trace_mode=(
            os.getenv("AOR_OPENWEBUI_TRACE_MODE", "").strip().lower()
            or app_config.runtime.openwebui_trace_mode
        ),
        show_response_stats=_env_bool("AOR_SHOW_RESPONSE_STATS", app_config.runtime.show_response_stats),
        show_prompt_suggestions=_env_bool("AOR_SHOW_PROMPT_SUGGESTIONS", app_config.runtime.show_prompt_suggestions),
        show_debug_metadata=_env_bool("AOR_SHOW_DEBUG_METADATA", app_config.runtime.show_debug_metadata),
        enable_presentation_llm_summary=_env_bool(
            "AOR_ENABLE_PRESENTATION_LLM_SUMMARY",
            app_config.runtime.enable_presentation_llm_summary or app_config.runtime.enable_llm_summary,
        ),
        presentation_llm_max_facts=int(os.getenv("AOR_PRESENTATION_LLM_MAX_FACTS", str(app_config.runtime.presentation_llm_max_facts))),
        presentation_llm_max_input_chars=int(
            os.getenv("AOR_PRESENTATION_LLM_MAX_INPUT_CHARS", str(app_config.runtime.presentation_llm_max_input_chars))
        ),
        presentation_llm_max_output_chars=int(
            os.getenv("AOR_PRESENTATION_LLM_MAX_OUTPUT_CHARS", str(app_config.runtime.presentation_llm_max_output_chars))
        ),
        presentation_llm_include_row_samples=_env_bool(
            "AOR_PRESENTATION_LLM_INCLUDE_ROW_SAMPLES", app_config.runtime.presentation_llm_include_row_samples
        ),
        presentation_llm_include_paths=_env_bool("AOR_PRESENTATION_LLM_INCLUDE_PATHS", app_config.runtime.presentation_llm_include_paths),
        intelligent_output_mode=(
            os.getenv("AOR_INTELLIGENT_OUTPUT_MODE", "").strip().lower()
            or app_config.runtime.intelligent_output_mode
        ),
        intelligent_output_max_fields=int(
            os.getenv("AOR_INTELLIGENT_OUTPUT_MAX_FIELDS", str(app_config.runtime.intelligent_output_max_fields))
        ),
        semantic_frame_mode=(
            os.getenv("AOR_SEMANTIC_FRAME_MODE", "").strip().lower()
            or app_config.runtime.semantic_frame_mode
        ),
        semantic_frame_max_depth=int(
            os.getenv("AOR_SEMANTIC_FRAME_MAX_DEPTH", str(app_config.runtime.semantic_frame_max_depth))
        ),
        semantic_frame_max_children=int(
            os.getenv("AOR_SEMANTIC_FRAME_MAX_CHILDREN", str(app_config.runtime.semantic_frame_max_children))
        ),
        llm_stage_max_depth=int(os.getenv("AOR_LLM_STAGE_MAX_DEPTH", str(app_config.runtime.llm_stage_max_depth))),
        presentation_intent_max_depth=int(
            os.getenv("AOR_PRESENTATION_INTENT_MAX_DEPTH", str(app_config.runtime.presentation_intent_max_depth))
        ),
        enable_insight_layer=_env_bool("AOR_ENABLE_INSIGHT_LAYER", app_config.runtime.enable_insight_layer),
        enable_llm_insights=_env_bool("AOR_ENABLE_LLM_INSIGHTS", app_config.runtime.enable_llm_insights),
        insight_max_facts=int(os.getenv("AOR_INSIGHT_MAX_FACTS", str(app_config.runtime.insight_max_facts))),
        insight_max_input_chars=int(os.getenv("AOR_INSIGHT_MAX_INPUT_CHARS", str(app_config.runtime.insight_max_input_chars))),
        insight_max_output_chars=int(os.getenv("AOR_INSIGHT_MAX_OUTPUT_CHARS", str(app_config.runtime.insight_max_output_chars))),
        action_planner_enabled=_env_bool("AOR_ACTION_PLANNER_ENABLED", app_config.runtime.action_planner_enabled),
        legacy_execution_planner_enabled=_env_bool(
            "AOR_LEGACY_EXECUTION_PLANNER_ENABLED", app_config.runtime.legacy_execution_planner_enabled
        ),
        auto_artifacts_enabled=_env_bool("AOR_AUTO_ARTIFACTS_ENABLED", app_config.runtime.auto_artifacts_enabled),
        auto_artifact_row_threshold=int(
            os.getenv("AOR_AUTO_ARTIFACT_ROW_THRESHOLD", str(app_config.runtime.auto_artifact_row_threshold))
        ),
        auto_artifact_dir=os.getenv("AOR_AUTO_ARTIFACT_DIR", app_config.runtime.auto_artifact_dir),
        auto_artifact_format=os.getenv("AOR_AUTO_ARTIFACT_FORMAT", app_config.runtime.auto_artifact_format),
        agent_ui_allow_raw_previews=_env_bool("AOR_AGENT_UI_ALLOW_RAW_PREVIEWS", True),
        agent_ui_allow_full_payloads=_env_bool("AOR_AGENT_UI_ALLOW_FULL_PAYLOADS", False),
        agent_ui_number_animation=os.getenv("AOR_AGENT_UI_NUMBER_ANIMATION", "odometer"),
        agent_ui_auto_immersive_min_width_px=int(
            os.getenv("AOR_AGENT_UI_AUTO_IMMERSIVE_MIN_WIDTH_PX", "500")
        ),
        agent_memory_enabled=_env_bool("AOR_AGENT_MEMORY_ENABLED", True),
        agent_memory_prompt_max_chars=int(os.getenv("AOR_AGENT_MEMORY_PROMPT_MAX_CHARS", "3000")),
        agent_plan_cache_enabled=_env_bool("AOR_AGENT_PLAN_CACHE_ENABLED", True),
        agent_plan_cache_prompt_max_chars=int(os.getenv("AOR_AGENT_PLAN_CACHE_PROMPT_MAX_CHARS", "4000")),
        agent_plan_cache_similarity_threshold=float(
            os.getenv("AOR_AGENT_PLAN_CACHE_SIMILARITY_THRESHOLD", "0.86")
        ),
        agent_plan_cache_max_entries=int(os.getenv("AOR_AGENT_PLAN_CACHE_MAX_ENTRIES", "5000")),
        agent_command_template_cache_enabled=_env_bool("AOR_AGENT_COMMAND_TEMPLATE_CACHE_ENABLED", True),
        agent_command_template_cache_prompt_max_chars=int(
            os.getenv("AOR_AGENT_COMMAND_TEMPLATE_CACHE_PROMPT_MAX_CHARS", "4000")
        ),
        agent_command_template_cache_similarity_threshold=float(
            os.getenv("AOR_AGENT_COMMAND_TEMPLATE_CACHE_SIMILARITY_THRESHOLD", "0.82")
        ),
        agent_command_template_cache_secondary_similarity_threshold=float(
            os.getenv("AOR_AGENT_COMMAND_TEMPLATE_CACHE_SECONDARY_SIMILARITY_THRESHOLD", "0.15")
        ),
        agent_command_template_cache_max_entries=int(
            os.getenv("AOR_AGENT_COMMAND_TEMPLATE_CACHE_MAX_ENTRIES", "5000")
        ),
        agent_computation_cache_enabled=_env_bool("AOR_AGENT_COMPUTATION_CACHE_ENABLED", True),
        agent_computation_cache_prompt_max_chars=int(
            os.getenv("AOR_AGENT_COMPUTATION_CACHE_PROMPT_MAX_CHARS", "4000")
        ),
        agent_computation_cache_similarity_threshold=float(
            os.getenv("AOR_AGENT_COMPUTATION_CACHE_SIMILARITY_THRESHOLD", "0.84")
        ),
        agent_computation_cache_max_entries=int(
            os.getenv("AOR_AGENT_COMPUTATION_CACHE_MAX_ENTRIES", "5000")
        ),
        lrnt_enabled=_env_bool("AOR_LRNT_ENABLED", True),
        lrnt_similarity_threshold=float(os.getenv("AOR_LRNT_SIMILARITY_THRESHOLD", "0.92")),
        lrnt_max_entries=int(os.getenv("AOR_LRNT_MAX_ENTRIES", "5000")),
        agent_learning_ledger_enabled=_env_bool("AOR_AGENT_LEARNING_LEDGER_ENABLED", True),
        agent_learning_ledger_auto_learn_enabled=_env_bool(
            "AOR_AGENT_LEARNING_LEDGER_AUTO_LEARN_ENABLED",
            True,
        ),
        lrdirect_enabled=_env_bool("AOR_LRDIRECT_ENABLED", True),
        agent_events_enabled=_env_bool("AOR_AGENT_EVENTS_ENABLED", True),
        agent_events_poll_seconds=float(os.getenv("AOR_AGENT_EVENTS_POLL_SECONDS", "10")),
        agent_events_max_run_history=int(os.getenv("AOR_AGENT_EVENTS_MAX_RUN_HISTORY", "5000")),
        llm_operator_enabled=_env_bool("AOR_LLM_OPERATOR_ENABLED", True),
        llm_operator_requires_approval=_env_bool("AOR_LLM_OPERATOR_REQUIRES_APPROVAL", True),
        llm_operator_max_actions=int(os.getenv("AOR_LLM_OPERATOR_MAX_ACTIONS", "8")),
        operator_policy_profile=os.getenv("AOR_OPERATOR_POLICY_PROFILE", "assisted"),
        reasoning_profile=os.getenv("AOR_REASONING_PROFILE", "balanced"),
        repair_profile=os.getenv("AOR_REPAIR_PROFILE", "balanced"),
        workflow_execution_mode=os.getenv("AOR_WORKFLOW_EXECUTION_MODE", "streaming"),
        prompt_rephrase_enabled=_env_bool("AOR_PROMPT_REPHRASE_ENABLED", True),
        response_streaming_enabled=_env_bool("AOR_RESPONSE_STREAMING_ENABLED", True),
        shell_input_bindings_mode=os.getenv("AOR_SHELL_INPUT_BINDINGS_MODE", "allow"),
        llm_operator_cardinality_judge_mode=os.getenv(
            "AOR_LLM_OPERATOR_CARDINALITY_JUDGE_MODE",
            "auto",
        ),
        llm_operator_verbose_enabled=_env_bool("AOR_LLM_OPERATOR_VERBOSE_ENABLED", False),
        online_lookup_gemini_api_key=(
            os.getenv("AOR_GEMINI_GROUNDING_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or ""
        ),
        online_lookup_gemini_model=os.getenv("AOR_GEMINI_GROUNDING_MODEL", "gemini-2.5-flash"),
        online_lookup_gemini_api_version=os.getenv("AOR_GEMINI_GROUNDING_API_VERSION", "v1beta"),
        online_ai_check_timeout_seconds=float(os.getenv("AOR_ONLINE_AI_CHECK_TIMEOUT_SECONDS", "120")),
        online_ai_check_reuse_browser=_env_bool("AOR_ONLINE_AI_CHECK_REUSE_BROWSER", True),
        online_ai_check_headless=_env_bool("AOR_ONLINE_AI_CHECK_HEADLESS", False),
        online_ai_check_profile_dir=online_ai_check_profile_dir,
        llm_operator_step_validation_enabled=_env_bool(
            "AOR_LLM_OPERATOR_STEP_VALIDATION_ENABLED",
            True,
        ),
        operator_workspace_cwd_guard_enabled=_env_bool("AOR_OPERATOR_WORKSPACE_CWD_GUARD_ENABLED", False),
        llm_operator_formatter_source_preview_chars=int(
            os.getenv("AOR_LLM_OPERATOR_FORMATTER_SOURCE_PREVIEW_CHARS", "3000")
        ),
        agent_clarification_mode=normalize_agent_clarification_mode(
            os.getenv("AOR_AGENT_CLARIFICATION_MODE", ""),
        ),
        llm_operator_max_clarification_rounds=int(
            os.getenv("AOR_LLM_OPERATOR_MAX_CLARIFICATION_ROUNDS", "3")
        ),
        llm_operator_conversation_max_turns=int(os.getenv("AOR_LLM_OPERATOR_CONVERSATION_MAX_TURNS", "6")),
        llm_operator_conversation_max_context_chars=int(
            os.getenv("AOR_LLM_OPERATOR_CONVERSATION_MAX_CONTEXT_CHARS", "24000")
        ),
        llm_operator_conversation_max_record_chars=int(
            os.getenv("AOR_LLM_OPERATOR_CONVERSATION_MAX_RECORD_CHARS", "8000")
        ),
        max_plan_retries=app_config.runtime.max_plan_retries,
        sql_database_url=app_config.sql.database_url,
        sql_databases=app_config.sql.databases,
        sql_default_database=app_config.sql.default_database,
        sql_row_limit=app_config.sql.row_limit,
        sql_timeout_seconds=app_config.sql.timeout_seconds,
        sql_agent_enabled=_env_bool("AOR_SQL_AGENT_ENABLED", True),
        sql_agent_chat_route_mode=os.getenv("AOR_SQL_AGENT_CHAT_ROUTE_MODE", "agentic"),
        sql_agent_default_limit=int(os.getenv("AOR_SQL_AGENT_DEFAULT_LIMIT", "100")),
        sql_agent_max_rows=int(os.getenv("AOR_SQL_AGENT_MAX_ROWS", "1000")),
        sql_agent_max_repair_attempts=int(os.getenv("AOR_SQL_AGENT_MAX_REPAIR_ATTEMPTS", "2")),
        openai_compat_enabled=app_config.runtime.openai_compat_enabled,
        openai_compat_model_name=app_config.runtime.openai_compat_model_name,
        openai_compat_spec_path=app_config.runtime.openai_compat_spec_path,
    )
    settings.run_store_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_memory_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_prompts_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_plan_cache_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_lrn_total_tasks_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_command_template_cache_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_computation_cache_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_learning_ledger_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_events_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_command_allowlist_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_chats_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.agent_ui_settings_db_path.parent.mkdir(parents=True, exist_ok=True)
    configure_prompt_fetcher(settings.agent_prompts_db_path)
    return settings


def get_settings(config_path: str | Path | None = None, cwd: str | Path | None = None) -> Settings:
    """Get settings for the surrounding runtime workflow.

    Inputs:
        Receives config_path, cwd for this function; type hints and validators define accepted shapes.

    Returns:
        Returns the computed value described by the function name and type hints.

    Used by:
        Used by OpenFABRIC runtime support code paths that import or call agent_runtime.api.config.get_settings.
    """
    resolved_cwd = str(Path(cwd).resolve()) if cwd is not None else str(Path.cwd().resolve())
    requested_config = config_path if config_path is not None else os.getenv(APP_CONFIG_PATH_ENV)
    resolved_config = str(Path(requested_config).expanduser().resolve()) if requested_config else ""
    return _cached_settings(resolved_config, resolved_cwd).model_copy(deep=True)
