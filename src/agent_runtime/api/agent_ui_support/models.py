"""Agent UI request, response, and in-memory state models."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.common import *
from agent_runtime.api.agent_ui_support.directory import *

class AgentRequestPayload(BaseModel):
    """Prompt payload accepted by the local agent UI."""

    prompt: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "standard"
    conversation_id: str | None = None
    llm_model: str | None = None


class AgentTaskCreatePayload(BaseModel):
    """Lean durable task creation payload accepted by the Agent UI."""

    prompt: str = Field(min_length=1, max_length=24000)
    title: str | None = Field(default=None, max_length=160)
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "llm_operator"
    gateway_id: str | None = Field(default=None, max_length=160)
    conversation_id: str | None = Field(default=None, max_length=160)
    context: dict[str, Any] = Field(default_factory=dict)
    llm_model: str | None = Field(default=None, max_length=240)
    start_now: bool = True


class AgentMonitorCreatePayload(BaseModel):
    """Lean monitor creation payload accepted by the Agent UI."""

    prompt: str | None = Field(default=None, max_length=24000)
    title: str | None = Field(default=None, max_length=160)
    mode: Literal["sample_command", "raw_stream"] = "sample_command"
    command: str | None = Field(default=None, max_length=4000)
    interval_seconds: int = Field(default=5, ge=1)
    duration_seconds: int = Field(default=300, ge=1)
    condition: str | None = Field(default=None, max_length=1000)
    natural_language_condition: str | None = Field(default=None, max_length=1000)
    trigger_mode: Literal["deterministic", "llm_judged", "hybrid"] = "deterministic"
    action_prompt: str | None = Field(default=None, max_length=24000)
    planner_rationale: str | None = Field(default=None, max_length=1000)
    risk_notes: str | None = Field(default=None, max_length=1000)
    judge_interval_seconds: int = Field(default=5, ge=1)
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "llm_operator"
    gateway_id: str | None = Field(default=None, max_length=160)
    conversation_id: str | None = Field(default=None, max_length=160)
    context: dict[str, Any] = Field(default_factory=dict)
    start_now: bool = True


class AdvisorySnippet(BaseModel):
    """One LLM-authored snippet for Advisory mode."""

    snippet_id: str = ""
    title: str = ""
    language: str = "shell"
    code: str = Field(min_length=1)
    runnable: bool = False
    explanation: str = ""
    cwd_note: str = ""


class AdvisoryResponse(BaseModel):
    """Structured response returned by the Advisory mode LLM call."""

    answer_markdown: str = ""
    snippets: list[AdvisorySnippet] = Field(default_factory=list)


class AgentConfirmationPayload(BaseModel):
    """Confirmation action accepted by the local agent UI."""

    action: Literal["approve", "deny"]
    context: dict[str, Any] = Field(default_factory=dict)


class AgentContinuationPayload(BaseModel):
    """Continuation request accepted by the local agent UI."""

    notes: str | None = Field(default=None, max_length=4000)
    context: dict[str, Any] = Field(default_factory=dict)


class AgentLearnedArtifactRef(BaseModel):
    """Reference to one learned runtime artifact that can be user-corrected."""

    model_config = ConfigDict(extra="forbid")

    artifact_kind: Literal[
        "command_template",
        "lr_d",
        "lr_ex",
        "lr_t",
        "plan_cache",
        "computation_cache",
    ]
    artifact_id: str = Field(min_length=1, max_length=160)
    request_id: str = Field(default="", max_length=160)
    action_id: str | None = Field(default=None, max_length=160)
    event_type: str = Field(default="", max_length=160)
    exact_step_key: str = Field(default="", max_length=500)


class AgentStepCorrectionPayload(BaseModel):
    """User correction for a learned runtime artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_ref: AgentLearnedArtifactRef
    correction_kind: Literal["instruction", "command", "code", "plan", "lr_tasks"]
    corrected_command: str | None = Field(default=None, max_length=8000)
    corrected_code: str | None = Field(default=None, max_length=20000)
    instruction: str | None = Field(default=None, max_length=8000)
    plan: dict[str, Any] | None = None
    lr_tasks: list[dict[str, Any]] | None = None
    global_constraints: dict[str, Any] | None = None
    rationale: str | None = Field(default=None, max_length=2000)


class AgentStepDeletionPayload(BaseModel):
    """User deletion request for one learned runtime artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_ref: AgentLearnedArtifactRef
    reason: str | None = Field(default=None, max_length=2000)


class AgentStepCorrectionResponse(BaseModel):
    """Result returned after validating or saving a learned artifact correction."""

    model_config = ConfigDict(extra="forbid")

    artifact_ref: AgentLearnedArtifactRef
    status: str = "saved"
    saved_ids: list[str] = Field(default_factory=list)
    quarantined_ids: list[str] = Field(default_factory=list)
    marked_failed_ids: list[str] = Field(default_factory=list)
    deleted_ids: list[str] = Field(default_factory=list)
    replacement_ids: dict[str, str] = Field(default_factory=dict)
    lesson_id: str = ""
    memory_id: str = ""
    retry_available: bool = False
    retry_notes: str = ""
    trace_event: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AgentRuntimeControlsPayload(BaseModel):
    """Server-global Agent UI runtime control toggles."""

    model_config = ConfigDict(extra="forbid")

    auto_approve_commands: bool | None = None
    agent_events_enabled: bool | None = None
    agent_clarification_mode: Literal["auto_pilot", "balanced", "pedantic"] | None = None
    operator_policy_profile: Literal["deterministic", "assisted"] | None = None
    reasoning_profile: Literal["fast", "balanced", "deep"] | None = None
    repair_profile: Literal["conservative", "balanced", "aggressive"] | None = None
    workflow_execution_mode: Literal["auto", "full_plan", "streaming"] | None = None
    prompt_rephrase_enabled: bool | None = None
    response_streaming_enabled: bool | None = None
    sql_agent_chat_route_mode: Literal["agentic", "direct"] | None = None
    operator_workspace_cwd_guard_enabled: bool | None = None
    llm_operator_verbose_enabled: bool | None = None
    llm_operator_step_validation_enabled: bool | None = None
    llm_operator_final_response_mode: Literal["detailed", "simple"] | None = None
    llm_operator_cardinality_judge_mode: Literal["off", "auto", "on"] | None = None
    llm_operator_verification_enforced: bool | None = None
    llm_operator_max_clarification_rounds: int | None = Field(default=None, ge=0, le=10)
    agent_memory_enabled: bool | None = None
    agent_memory_prompt_max_chars: int | None = Field(default=None, ge=200, le=20000)
    agent_learning_ledger_auto_learn_enabled: bool | None = None
    agent_command_template_cache_similarity_threshold: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    agent_command_template_cache_secondary_similarity_threshold: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    lrnt_enabled: bool | None = None
    lrnt_similarity_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    lrdirect_enabled: bool | None = None
    reliability_mode: Literal["off", "standard", "aggressive"] | None = None
    reliability_verifier_enforced: bool | None = None
    reliability_max_recovery_probes: int | None = Field(default=None, ge=0, le=20)
    reliability_max_autonomous_repair_attempts: int | None = Field(default=None, ge=0, le=20)
    reliability_weak_model_plan_action_cap: int | None = Field(default=None, ge=0, le=50)
    reliability_approval_envelope_budget: int | None = Field(default=None, ge=0, le=20)
    ui_auto_immersive_min_width_px: int | None = None
    llm_base_scheme: Literal["http", "https"] | None = None
    llm_base_host: str | None = Field(default=None, max_length=255)
    llm_base_port: int | None = Field(default=None, ge=1, le=65535)
    llm_base_path: str | None = Field(default=None, max_length=255)
    llm_base_url: str | None = Field(default=None, max_length=600)
    llm_timeout_seconds: int | None = Field(default=None, ge=1, le=1800)
    llm_max_tokens: int | None = Field(default=None, ge=0, le=65536)
    audio_transcriber_service_host: str | None = Field(default=None, max_length=255)
    audio_transcriber_service_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="before")
    @classmethod
    def reject_removed_runtime_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            removed_keys = removed_public_settings_in(data)
            if removed_keys:
                raise ValueError(
                    "Removed runtime settings are no longer accepted. "
                    "Use operator_policy_profile, reasoning_profile, repair_profile, "
                    "workflow_execution_mode, and response_streaming_enabled instead. "
                    f"Removed keys: {', '.join(removed_keys)}"
                )
        return data


class AgentUiSettingsPreferencesPayload(BaseModel):
    """Shared Agent UI settings persistence update."""

    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any] | None = None


class AgentPromptHistoryPayload(BaseModel):
    """Prompt history entry accepted by the Agent UI."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=24000)


class AgentCommandAllowlistPayload(BaseModel):
    """Exact blocked command exception accepted by the local Agent UI."""

    command: str = Field(min_length=1, max_length=8000)
    reason: str | None = Field(default=None, max_length=1000)
    source_request_id: str | None = Field(default=None, max_length=128)


class AgentClarificationPayload(BaseModel):
    """Clarification answer accepted by the local agent UI."""

    answer: str = Field(min_length=1)
    selected_option_id: str | None = None
    parameter_choice_id: str | None = None
    parameter_key: str | None = None
    parameter_field: str | None = None
    answer_is_secret: bool = False
    persist_for_event: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class AgentIntegrationExecutePayload(BaseModel):
    """Synchronous external integration request for one agent prompt."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=24000)
    context: dict[str, Any] = Field(default_factory=dict)
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "llm_operator"
    conversation_id: str | None = Field(default=None, max_length=160)
    llm_model: str | None = Field(default=None, max_length=240)
    timeout_seconds: float = Field(default=120.0, ge=0.0, le=1800.0)


class AgentIntegrationConfirmationPayload(BaseModel):
    """Synchronous external confirmation action for a gated integration request."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "deny"] = "approve"
    context: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=120.0, ge=0.0, le=1800.0)


class AgentIntegrationClarificationPayload(BaseModel):
    """Synchronous external clarification answer for a gated integration request."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    selected_option_id: str | None = None
    parameter_choice_id: str | None = None
    parameter_key: str | None = None
    parameter_field: str | None = None
    answer_is_secret: bool = False
    persist_for_event: bool = False
    context: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=120.0, ge=0.0, le=1800.0)


class AgentIntegrationOutput(BaseModel):
    """Compact command or artifact output exposed to external integrations."""

    data_ref: str = ""
    source_node_id: str | None = None
    data_type: str | None = None
    status: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    output: Any = None
    error: str | None = None
    preview: Any = None
    metadata: Any = None
    full_payload_available: bool = False


class AgentIntegrationNeedsAction(BaseModel):
    """Action needed before an integration request can continue."""

    type: Literal["confirmation", "clarification"]
    confirmation_actions: list[dict[str, Any]] = Field(default_factory=list)
    clarification_request: dict[str, Any] | None = None


class AgentIntegrationExecutionResponse(BaseModel):
    """Non-streaming execution envelope returned to external integrations."""

    request_id: str
    conversation_id: str = ""
    status: Literal[
        "completed",
        "failed",
        "cancelled",
        "running",
        "awaiting_confirmation",
        "awaiting_clarification",
    ]
    trace_url: str
    final_response: str = ""
    error: str = ""
    error_detail: dict[str, Any] | None = None
    outputs: list[AgentIntegrationOutput] = Field(default_factory=list)
    display_document: dict[str, Any] | None = None
    response_metrics: dict[str, Any] | None = None
    needs_action: AgentIntegrationNeedsAction | None = None


class AgentTerminalCwdPayload(BaseModel):
    """Current cwd reported by one Agent UI terminal session."""

    session_id: str = Field(min_length=1)
    cwd: str = Field(min_length=1)


class PromptTemplateUpdatePayload(BaseModel):
    """Prompt editor request to update one template body."""

    body: str = Field(min_length=1)


class PromptTemplateRenderPayload(BaseModel):
    """Prompt editor request to dry-run render one template."""

    variables: dict[str, Any] = Field(default_factory=dict)


class PromptEditorMemoryUpdatePayload(BaseModel):
    """Prompt editor request to update one persistent memory entry."""

    instruction: str | None = None
    summary: str | None = None
    status: Literal["active", "proposed", "retired"] | None = None
    memory_kind: Literal["task_memory", "preference_memory", "validation_policy"] | None = None
    scope: Literal["exact_model", "model_family", "global"] | None = None
    model_name: str | None = None
    model_family: str | None = None
    task_type: str | None = None
    tool_type: str | None = None
    intent_type: str | None = None
    validator_error_type: str | None = None
    safe_examples: list[str] | None = None
    blocked_examples: list[str] | None = None
    tags: list[str] | None = None
    rationale: str | None = None


class LearningLessonUpdatePayload(BaseModel):
    """Learning ledger request to edit one draft or active lesson."""

    title: str | None = Field(default=None, max_length=240)
    instruction: str | None = Field(default=None, max_length=4000)
    summary: str | None = Field(default=None, max_length=500)
    scope: dict[str, Any] | None = None
    tags: list[str] | None = None


class LearningLessonRejectPayload(BaseModel):
    """Learning ledger request to reject one lesson."""

    reason: str | None = Field(default=None, max_length=1000)


class CapabilityProposalUpdatePayload(BaseModel):
    """Learning ledger request to edit one capability evolution proposal."""

    title: str | None = Field(default=None, max_length=240)
    summary: str | None = Field(default=None, max_length=1000)
    rationale: str | None = Field(default=None, max_length=2000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    draft: dict[str, Any] | None = None


class CapabilityProposalRejectPayload(BaseModel):
    """Learning ledger request to reject one capability evolution proposal."""

    reason: str | None = Field(default=None, max_length=1000)


class LearningLessonDigestNotePayload(BaseModel):
    """Learning ledger request to merge a user note into one lesson."""

    note: str = Field(min_length=1, max_length=1200)


class AgentConversationSummaryPayload(BaseModel):
    """Visible conversation text to compress into one collapsed-header line."""

    text: str = Field(min_length=1, max_length=24000)


class AgentConversationHeaderSummary(BaseModel):
    """One-line conversation summary for the collapsed Agent UI header."""

    summary: str = Field(default="", max_length=160)


class AgentNameSuggestionPayload(BaseModel):
    """Optional context for LLM-authored Agent UI display names."""

    current_name: str | None = Field(default=None, max_length=80)
    model_name: str | None = Field(default=None, max_length=200)
    recent_names: list[str] = Field(default_factory=list, max_length=20)


class AgentNameSuggestionResponse(BaseModel):
    """One short display name for the local agent."""

    name: str = Field(default="Agent", max_length=40)


class AgentLlmRuntimePayload(BaseModel):
    """Browser-submitted local LLM server launch configuration."""

    command: str = Field(min_length=1, max_length=8000)
    conda_env: str = Field(default="vllm", max_length=120)
    cwd: str | None = Field(default=None, max_length=2000)
    restart: bool = False


class AgentGatewayCreatePayload(BaseModel):
    """User-submitted gateway endpoint details."""

    label: str = Field(min_length=1, max_length=120)
    scheme: Literal["http", "https"] = "http"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    node: str | None = Field(default=None, max_length=120)
    terminal_cwd: str | None = Field(default=None, max_length=2000)
    enabled: bool = True


class AgentGatewayUpdatePayload(BaseModel):
    """Partial update for one registered gateway."""

    label: str | None = Field(default=None, max_length=120)
    scheme: Literal["http", "https"] | None = None
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    node: str | None = Field(default=None, max_length=120)
    terminal_cwd: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None


@dataclass
class AgentUiRequestState:
    """Private local UI state needed to continue confirmation-gated requests."""

    request_id: str
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    planning_trace: Any = None
    confirmation_required: bool = False
    confirmation_actions: list[dict[str, Any]] = field(default_factory=list)
    confirmation_handled: bool = False
    clarification_required: bool = False
    clarification_request: dict[str, Any] | None = None
    clarification_handled: bool = False
    continuation_request_id: str = ""
    parent_request_id: str | None = None
    conversation_id: str | None = None


class AgentUiRequestStateStore:
    """Small bounded store for prompt/trace state behind the local UI buttons."""

    def __init__(self, max_requests: int = 100) -> None:
        self.max_requests = max(1, int(max_requests))
        self._items: OrderedDict[str, AgentUiRequestState] = OrderedDict()
        self._lock = threading.RLock()

    def create(
        self,
        *,
        request_id: str,
        prompt: str,
        context: dict[str, Any] | None = None,
        parent_request_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        with self._lock:
            self._items[request_id] = AgentUiRequestState(
                request_id=request_id,
                prompt=str(prompt or ""),
                context=dict(context or {}),
                parent_request_id=parent_request_id,
                conversation_id=conversation_id,
            )
            self._items.move_to_end(request_id)
            self._evict_locked()

    def get(self, request_id: str) -> AgentUiRequestState | None:
        with self._lock:
            item = self._items.get(str(request_id or ""))
            if item is None:
                return None
            self._items.move_to_end(item.request_id)
            return AgentUiRequestState(
                request_id=item.request_id,
                prompt=item.prompt,
                context=dict(item.context),
                planning_trace=item.planning_trace,
                confirmation_required=item.confirmation_required,
                confirmation_actions=[dict(action) for action in item.confirmation_actions],
                confirmation_handled=item.confirmation_handled,
                clarification_required=item.clarification_required,
                clarification_request=(
                    dict(item.clarification_request)
                    if isinstance(item.clarification_request, dict)
                    else None
                ),
                clarification_handled=item.clarification_handled,
                continuation_request_id=item.continuation_request_id,
                parent_request_id=item.parent_request_id,
                conversation_id=item.conversation_id,
            )

    def finish(
        self,
        request_id: str,
        *,
        planning_trace: Any,
        confirmation_required: bool,
        confirmation_actions: list[dict[str, Any]],
        clarification_required: bool = False,
        clarification_request: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            item = self._items.get(str(request_id or ""))
            if item is None:
                return
            item.planning_trace = planning_trace
            item.confirmation_required = bool(confirmation_required)
            item.confirmation_actions = [
                dict(action)
                for action in list(confirmation_actions or [])
                if isinstance(action, dict)
            ]
            item.clarification_required = bool(clarification_required)
            item.clarification_request = (
                dict(clarification_request)
                if isinstance(clarification_request, dict)
                else None
            )
            self._items.move_to_end(item.request_id)

    def mark_handled(self, request_id: str) -> None:
        with self._lock:
            item = self._items.get(str(request_id or ""))
            if item is not None:
                item.confirmation_handled = True
                self._items.move_to_end(item.request_id)

    def mark_clarification_handled(self, request_id: str) -> None:
        with self._lock:
            item = self._items.get(str(request_id or ""))
            if item is not None:
                item.clarification_handled = True
                self._items.move_to_end(item.request_id)

    def link_continuation(self, request_id: str, continuation_request_id: str) -> None:
        with self._lock:
            item = self._items.get(str(request_id or ""))
            if item is not None:
                item.continuation_request_id = str(continuation_request_id or "").strip()
                self._items.move_to_end(item.request_id)

    def find_child_request_id(self, parent_request_id: str) -> str:
        normalized = str(parent_request_id or "").strip()
        if not normalized:
            return ""
        with self._lock:
            for item in reversed(self._items.values()):
                if str(item.parent_request_id or "").strip() == normalized:
                    self._items.move_to_end(item.request_id)
                    return item.request_id
        return ""

    def _evict_locked(self) -> None:
        while len(self._items) > self.max_requests:
            self._items.popitem(last=False)


@dataclass
class AgentTerminalSession:
    """One Agent UI terminal session known to the API layer."""

    session_id: str
    cwd: str
    gateway_id: str = ""
    gateway_node: str = ""
    created_at: str = ""
    updated_at: str = ""


class AgentTerminalSessionStore:
    """Small bounded store for terminal cwd trust state."""

    def __init__(self, max_sessions: int = 100) -> None:
        self.max_sessions = max(1, int(max_sessions))
        self._items: OrderedDict[str, AgentTerminalSession] = OrderedDict()
        self._lock = threading.RLock()

    def create(self, initial_cwd: str, *, gateway_id: str = "", gateway_node: str = "") -> AgentTerminalSession:
        session_id = new_id("term")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        session = AgentTerminalSession(
            session_id=session_id,
            cwd=str(initial_cwd or ""),
            gateway_id=str(gateway_id or ""),
            gateway_node=str(gateway_node or ""),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._items[session_id] = session
            self._items.move_to_end(session_id)
            self._evict_locked()
        return session

    def update_cwd(self, session_id: str | None, cwd: str | None) -> AgentTerminalSession | None:
        normalized_session_id = str(session_id or "").strip()
        normalized_cwd = str(cwd or "").strip()
        if not normalized_session_id or not normalized_cwd:
            return None
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            session = self._items.get(normalized_session_id)
            if session is None:
                return None
            session.cwd = normalized_cwd
            session.updated_at = now
            self._items.move_to_end(normalized_session_id)
            return AgentTerminalSession(**session.__dict__)

    def trusted_cwd(
        self,
        session_id: str | None,
        cwd: str | None,
        *,
        gateway_id: str | None = None,
        gateway_node: str | None = None,
    ) -> str | None:
        normalized_session_id = str(session_id or "").strip()
        normalized_cwd = str(cwd or "").strip()
        if not normalized_session_id or not normalized_cwd:
            return None
        normalized_gateway_id = str(gateway_id or "").strip()
        normalized_gateway_node = str(gateway_node or "").strip()
        with self._lock:
            session = self._items.get(normalized_session_id)
            if session is None:
                return None
            if normalized_gateway_id and session.gateway_id and normalized_gateway_id != session.gateway_id:
                return None
            if normalized_gateway_node and session.gateway_node and normalized_gateway_node != session.gateway_node:
                return None
            try:
                requested = Path(normalized_cwd).expanduser().resolve(strict=False)
                trusted = Path(session.cwd).expanduser().resolve(strict=False)
            except Exception:
                return None
            if requested != trusted:
                return None
            self._items.move_to_end(normalized_session_id)
            return str(trusted)

    def _evict_locked(self) -> None:
        while len(self._items) > self.max_sessions:
            self._items.popitem(last=False)

__all__ = [name for name in globals() if not name.startswith("__")]
