"""Top-level orchestration for the schema-driven agent runtime."""

from __future__ import annotations

import json
import re
import sys as _sys
import time
from pathlib import Path
from typing import Any

from agent_runtime.capabilities import CapabilityRegistry
from agent_runtime.capabilities.sql import (
    DatabaseDiscoveryCommitRequest,
    DatabaseDiscoveryRequest,
    SQL_AGENTIC_CONTEXT_KEY,
    SQL_PENDING_CONTEXT_KEY,
    SQL_SCHEMA_CACHE_CONTEXT_KEY,
    SqlAgentRequest,
    SqlAgentService,
    SqlDatabaseDiscoveryService,
    looks_like_sql_intent,
    parse_discoverdb_macro,
    render_sql_agent_response,
)
from agent_runtime.capabilities.consistency import (
    manifest_contract,
    manifest_contract_hash,
    registry_capability_ids,
    registry_contract_hash,
    validate_registry_consistency,
    validate_selected_capability_contracts,
)
from agent_runtime.core.errors import AgentRuntimeError, ValidationError
from agent_runtime.core.logging import get_logger, log_event
from agent_runtime.core.types import (
    ActionDAG,
    CapabilityRef,
    InputRef,
    ResultBundle,
    TaskFrame,
    UserRequest,
)
from agent_runtime.core.user_errors import user_error_detail, user_error_message
from agent_runtime.clarification import normalize_agent_clarification_mode
from agent_runtime.settings_consolidation import (
    effective_prompt_rephrase_enabled,
    normalize_cardinality_judge_mode,
    normalize_operator_policy_profile,
    normalize_reasoning_profile,
    normalize_repair_profile,
    normalize_shell_input_bindings_mode,
    normalize_workflow_execution_mode,
    operator_profile_policy,
    workflow_uses_streaming,
)
from agent_runtime.execution.engine import ExecutionEngine
from agent_runtime.execution.failure_repair import attempt_failure_repair
from agent_runtime.execution.gateway_client import GatewayClient
from agent_runtime.execution.safety import evaluate_dag_safety
from agent_runtime.input_pipeline.argument_extraction import (
    ArgumentExtractionResult,
    extract_arguments,
)
from agent_runtime.input_pipeline.capability_fit import (
    CapabilityFitDecision,
    assess_capability_fit,
    resolve_tasks_from_output_contracts,
)
from agent_runtime.input_pipeline.dataflow_planning import plan_dataflow
from agent_runtime.input_pipeline.dag_builder import build_action_dag
from agent_runtime.input_pipeline.decomposition import (
    DecompositionResult,
    classify_prompt,
    decompose_prompt,
)
from agent_runtime.input_pipeline.validators import PlanningContractValidator
from agent_runtime.input_pipeline.domain_selection import CapabilitySelectionResult, select_capabilities
from agent_runtime.input_pipeline.planning_review import review_action_dag
from agent_runtime.input_pipeline.prompt_rephrase import (
    PromptRephraseOutcome,
    rephrase_prompt_preflight,
)
from agent_runtime.input_pipeline.verb_classification import (
    assign_semantic_verbs,
    normalize_semantic_verbs_deterministic,
)
from agent_runtime.llm.profiling import ProfilingLLMClient
from agent_runtime.llm.proposals import DirectAnswerProposal
from agent_runtime.llm.reproducibility import (
    PlanningTrace,
    PlanningTraceEntry,
    append_trace_entry,
    hash_action_dag,
    llm_client_metadata,
    replay_from_validated_dag,
)
from agent_runtime.llm.structured_call import structured_call
from agent_runtime.memory import (
    AgentMemoryStore,
    MemoryRetrievalContext,
    enrich_memory_retrieval_hints,
    memory_directives_from_entries,
    memory_prompt_lines_from_context,
    normalize_model_family,
)
from agent_runtime.parameters import (
    PARAMETER_AGENT_CONTEXT_KEY,
    PARAMETER_CONTEXT_KEY,
    PARAMETER_ENV_CONTEXT_KEY,
    AgentParameterStore,
    masked_parameter_summary,
    parameter_clarification_choices,
    parameter_database_profile,
    parameter_context_summary,
    parameter_prompt_lines_from_context,
    parameter_shell_env,
    normalize_parameter_key,
)
from agent_runtime.onlinelinelookup import (
    COMBINED_ONLINE_LOOKUP_PROVIDER,
    ONLINE_LOOKUP_CONTEXT_KEY,
    ONLINE_LOOKUP_CONTEXTS_KEY,
    ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY,
    lookup_online_answer,
    online_lookup_prompt_lines,
    online_lookup_requested_from_context,
    sanitize_online_lookup_query,
)
from agent_runtime.onlineaicheck import (
    ONLINE_AI_CHECK_CONTEXT_KEY,
    ONLINE_AI_CHECK_PROVIDER,
    ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY,
    lookup_duck_ai_answer,
    online_ai_check_prompt_lines,
    online_ai_check_requested_from_context,
    sanitize_online_ai_check_query,
)
from agent_runtime.plan_cache import AgentPlanCacheStore
from agent_runtime.lrn_total_tasks import (
    AgentLrnTotalTaskStore,
    LrnTotalTaskCandidate,
    LrnTotalTaskLookupContext,
    LrnTotalTaskWrite,
)
from agent_runtime.command_template_cache import AgentCommandTemplateCacheStore
from agent_runtime.computation_cache import AgentComputationCacheStore
from agent_runtime.reliability import AgentReliabilityStore
from agent_runtime.prompts import prompt_lines
from agent_runtime.observability import (
    EVENT_ARGUMENT_EXTRACTED,
    EVENT_ARGUMENT_REJECTED,
    EVENT_CAPABILITY_CANDIDATE,
    EVENT_CAPABILITY_GAP,
    EVENT_CAPABILITY_REJECTED,
    EVENT_CAPABILITY_SELECTED,
    EVENT_DAG_EDGE_CREATED,
    EVENT_DAG_NODE_CREATED,
    EVENT_DATAFLOW_DERIVED_TASK_CREATED,
    EVENT_DATAFLOW_BINDING_ACCEPTED,
    EVENT_DATAFLOW_BINDING_REJECTED,
    EVENT_DATAFLOW_REF_ACCEPTED,
    EVENT_DATAFLOW_REF_REJECTED,
    EVENT_LLM_PROPOSAL_RECEIVED,
    EVENT_REPAIR_ACCEPTED,
    EVENT_REPAIR_PROPOSED,
    EVENT_REPAIR_REJECTED,
    EVENT_SAFETY_ALLOWED,
    EVENT_SAFETY_BLOCKED,
    EVENT_VALIDATION_ACCEPTED,
    EVENT_VALIDATION_REJECTED,
    STAGE_ARGUMENT_EXTRACTION,
    STAGE_CAPABILITY_FIT,
    STAGE_CAPABILITY_SELECTION,
    STAGE_COMPLETED,
    STAGE_DAG_CONSTRUCTION,
    STAGE_DAG_REVIEW,
    STAGE_DATAFLOW_PLANNING,
    STAGE_DECOMPOSITION,
    STAGE_EXECUTION,
    STAGE_FAILURE_REPAIR,
    STAGE_PROMPT_REPHRASE,
    STAGE_PROMPT_CLASSIFICATION,
    STAGE_REQUEST_RECEIVED,
    STAGE_SAFETY_EVALUATION,
    STAGE_VERB_ASSIGNMENT,
    ObservabilityContext,
    build_observability_context,
)
from agent_runtime.operator import (
    LLMOperatorPipeline,
    OperatorClarificationRequest,
    OperatorExecutionRecord,
    OperatorPlan,
    OperatorPipelineResult,
    OperatorStepValidationContract,
    OperatorStepValidationReview,
)
from agent_runtime.operator.execution_shape import (
    classify_execution_shape,
    execution_shape_from_request,
)
from agent_runtime.operator.machine_pipeline import (
    MachineOperatorPipeline,
    decompose_prompt_compact,
)
from agent_runtime.operator.pipeline import (
    OPERATOR_CONVERSATION_CONTEXT_BUILT,
    OPERATOR_CONVERSATION_CONTEXT_TRUNCATED,
    OPERATOR_STAGE,
    OPERATOR_STEP_VALIDATION_ACCEPTED,
    OPERATOR_STEP_VALIDATION_PROPOSED,
    OPERATOR_STEP_VALIDATION_REJECTED,
    OPERATOR_TRYOUT_COMPLETED,
    OperatorValidationError,
    build_operator_step_validation_prompt,
)
from agent_runtime.operator.user_macros import (
    CHECKONLINEAI_MACRO_KIND,
    TYPEIN_INPUT_PREFIX,
    TYPEIN_MACRO_KIND,
    USER_MACRO_PRIVATE_CONTEXT_KEY,
    USER_MACRO_SUMMARY_CONTEXT_KEY,
    typein_macro_payloads_from_parameter_record,
)
from agent_runtime.output_pipeline.orchestrator import OutputPipelineOrchestrator
from agent_runtime.profiling import RuntimeProfiler


_RAW_SYSTEM_MESSAGE_MARKERS = (
    "traceback",
    "validation error",
    "value error",
    "syntaxerror",
    "type=",
    "input_value=",
    "pydantic",
    "for further information visit",
    "stderr",
    "stdout",
)

from agent_runtime.core.orchestrator_support.formatting import *
from agent_runtime.core.orchestrator_support.state import _StateMixin
from agent_runtime.core.orchestrator_support.confirmation import _ConfirmationMixin
from agent_runtime.core.orchestrator_support.memory_parameters import _MemoryParameterMixin
from agent_runtime.core.orchestrator_support.runtime_services import _RuntimeServicesMixin
from agent_runtime.core.orchestrator_support.sql_agentic import _SqlAgenticMixin
from agent_runtime.core.orchestrator_support.streaming_state import _StreamingStateMixin
from agent_runtime.core.orchestrator_support.online_lookup import _OnlineLookupMixin
from agent_runtime.core.orchestrator_support.operator_flow import _OperatorFlowMixin
from agent_runtime.core.orchestrator_support.replay_continue import _ReplayContinueMixin
from agent_runtime.core.orchestrator_support.finalization import _FinalizationMixin


class AgentRuntime(
    _StateMixin,
    _ConfirmationMixin,
    _MemoryParameterMixin,
    _RuntimeServicesMixin,
    _SqlAgenticMixin,
    _StreamingStateMixin,
    _OnlineLookupMixin,
    _OperatorFlowMixin,
    _ReplayContinueMixin,
    _FinalizationMixin,
):
    """Coordinate the full typed agent pipeline from prompt to rendered output."""

    def __init__(
        self,
        llm_client,
        registry: CapabilityRegistry,
        execution_engine: ExecutionEngine,
        output_orchestrator: OutputPipelineOrchestrator,
        memory_store: AgentMemoryStore | None = None,
        parameter_store: AgentParameterStore | None = None,
        plan_cache_store: AgentPlanCacheStore | None = None,
        lrn_total_task_store: AgentLrnTotalTaskStore | None = None,
        command_template_cache_store: AgentCommandTemplateCacheStore | None = None,
        computation_cache_store: AgentComputationCacheStore | None = None,
        reliability_store: AgentReliabilityStore | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.registry = registry
        self.execution_engine = execution_engine
        self.output_orchestrator = output_orchestrator
        self.memory_store = memory_store
        self.parameter_store = parameter_store
        self.plan_cache_store = plan_cache_store
        self.lrn_total_task_store = lrn_total_task_store
        self.command_template_cache_store = command_template_cache_store
        self.computation_cache_store = computation_cache_store
        self.reliability_store = reliability_store
        self.logger = get_logger("orchestrator")
        self.last_planning_trace: PlanningTrace | None = None
        self.last_plan_summary: dict[str, Any] | None = None
        self.last_failure_summary: dict[str, Any] | None = None
        self.last_display_document: dict[str, Any] | None = None
        self.last_profile_summary: dict[str, Any] | None = None
        self._assert_registry_consistency()

    @staticmethod
    def _lrnt_classification_snapshot(classification_context: dict[str, Any]) -> dict[str, Any]:
        """Return the conservative classification snapshot used by LRN-T."""

        likely_domains = sorted(
            {
                str(domain or "").strip().lower()
                for domain in list(classification_context.get("likely_domains") or [])
                if str(domain or "").strip()
            }
        )
        return {
            "prompt_type": str(classification_context.get("prompt_type") or "").strip().lower(),
            "requires_tools": bool(classification_context.get("requires_tools")),
            "likely_domains": likely_domains,
            "risk_level": str(classification_context.get("risk_level") or "").strip().lower(),
        }

    def _lrnt_workflow_mode(self, request_context: dict[str, Any]) -> str:
        config = self._runtime_config_for_context(request_context)
        return "streaming" if workflow_uses_streaming(config.workflow_execution_mode) else "full_plan"

    def _lrnt_model_identity(self, llm_client: Any) -> tuple[str, str]:
        model_name, _temperature = llm_client_metadata(llm_client)
        model_name = str(model_name or "")
        return model_name, normalize_model_family(model_name)

    def _lrt_lookup_total_tasks(
        self,
        *,
        user_request: UserRequest,
        request_context: dict[str, Any],
        llm_client: Any,
        planning_registry: CapabilityRegistry,
        classification_context: dict[str, Any],
        observability: ObservabilityContext,
        trace: PlanningTrace,
    ) -> tuple[DecompositionResult, LrnTotalTaskCandidate] | None:
        """Return cached typed tasks when an LR-T entry safely applies."""

        request_config = self._runtime_config_for_context(request_context)
        store = self.lrn_total_task_store
        if store is None or not bool(getattr(request_config, "lrnt_enabled", True)):
            return None
        model_name, model_family = self._lrnt_model_identity(llm_client)
        lookup = LrnTotalTaskLookupContext(
            prompt=user_request.raw_prompt,
            classification_context=self._lrnt_classification_snapshot(classification_context),
            model_name=model_name,
            model_family=model_family,
            workflow_mode=self._lrnt_workflow_mode(request_context),
            registry_contract_hash=registry_contract_hash(planning_registry),
            similarity_threshold=float(getattr(request_config, "lrnt_similarity_threshold", 0.92)),
            limit=3,
        )
        candidates = store.retrieve(lookup)
        rejected_candidates = store.rejected_candidates(lookup)
        best_rejected = rejected_candidates[0] if rejected_candidates else None
        trace.metadata["lrt_lookup"] = {
            "candidate_count": len(candidates),
            "rejected_candidate_count": len(rejected_candidates),
            "best_rejected_score": best_rejected.score if best_rejected is not None else None,
            "best_rejected_reason": best_rejected.reason if best_rejected is not None else "",
            "workflow_mode": lookup.workflow_mode,
            "model_family": lookup.model_family,
        }
        observability.info(
            STAGE_DECOMPOSITION,
            "operator.lrt.lookup",
            "LR-T lookup",
            "The runtime checked for a learned total-task structure before decomposition.",
            details={
                "enabled": True,
                "candidate_count": len(candidates),
                "rejected_candidate_count": len(rejected_candidates),
                "best_rejected_score": best_rejected.score if best_rejected is not None else None,
                "best_rejected_reason": best_rejected.reason if best_rejected is not None else "",
                "workflow_mode": lookup.workflow_mode,
                "model_family": lookup.model_family,
                "similarity_threshold": lookup.similarity_threshold,
            },
        )
        for candidate in candidates:
            try:
                tasks = [
                    TaskFrame.model_validate(task_payload)
                    for task_payload in list(candidate.entry.tasks or [])
                    if isinstance(task_payload, dict)
                ]
                decomposition = DecompositionResult(
                    tasks=tasks,
                    global_constraints=dict(candidate.entry.global_constraints or {}),
                    unresolved_references=[],
                    assumptions=[],
                )
                validation = PlanningContractValidator().validate_tasks(
                    decomposition.tasks,
                    original_prompt=user_request.raw_prompt,
                )
                if not validation.accepted:
                    raise ValidationError(
                        "LR-T cached task validation failed: "
                        + "; ".join(issue.message for issue in validation.issues[:4])
                    )
            except Exception as exc:
                quarantined = store.mark_failed(
                    candidate.entry.entry_id,
                    failure_category=f"validation:{type(exc).__name__}",
                    quarantine=True,
                )
                observability.warning(
                    STAGE_DECOMPOSITION,
                    "operator.lrt.quarantined",
                    "LR-T entry quarantined",
                    "A learned total-task structure failed validation before reuse.",
                    details={
                        "entry_id": candidate.entry.entry_id,
                        "reason": str(exc)[:500],
                        "failure_count": quarantined.failure_count if quarantined else None,
                    },
                )
                continue
            used_entry = store.mark_used(candidate.entry.entry_id)
            if used_entry is not None:
                candidate = candidate.model_copy(update={"entry": used_entry})
            trace.metadata["lrt_hit"] = {
                "entry_id": candidate.entry.entry_id,
                "score": candidate.score,
                "reason": candidate.reason,
                "task_count": len(decomposition.tasks),
            }
            observability.info(
                STAGE_DECOMPOSITION,
                "operator.lrt.hit",
                "LR-T hit",
                "A learned total-task structure was reused; decomposition and semantic verb assignment were skipped.",
                details={
                    "entry_id": candidate.entry.entry_id,
                    "score": candidate.score,
                    "reason": candidate.reason,
                    "task_count": len(decomposition.tasks),
                },
            )
            observability.stage_completed(
                STAGE_DECOMPOSITION,
                "LR-T task structure reused",
                "The runtime reused a validated typed task structure.",
                details={
                    "entry_id": candidate.entry.entry_id,
                    "task_count": len(decomposition.tasks),
                },
            )
            observability.stage_completed(
                STAGE_VERB_ASSIGNMENT,
                "LR-T semantic verbs reused",
                "Semantic task annotations came from the learned LR-T structure.",
                details={
                    "entry_id": candidate.entry.entry_id,
                    "task_count": len(decomposition.tasks),
                },
            )
            return decomposition, candidate
        return None

    def _lrnt_write_payload(
        self,
        *,
        user_request: UserRequest,
        request_context: dict[str, Any],
        llm_client: Any,
        planning_registry: CapabilityRegistry,
        classification_context: dict[str, Any],
        execution_shape: dict[str, Any],
        typed_tasks: list[TaskFrame],
        global_constraints: dict[str, Any],
        streaming_operator_mode: bool,
    ) -> LrnTotalTaskWrite | None:
        request_config = self._runtime_config_for_context(request_context)
        if self.lrn_total_task_store is None or not bool(getattr(request_config, "lrnt_enabled", True)):
            return None
        if not typed_tasks:
            return None
        model_name, model_family = self._lrnt_model_identity(llm_client)
        return LrnTotalTaskWrite(
            prompt=user_request.raw_prompt,
            classification_context=self._lrnt_classification_snapshot(classification_context),
            model_name=model_name,
            model_family=model_family,
            workflow_mode=self._lrnt_workflow_mode(request_context),
            registry_contract_hash=registry_contract_hash(planning_registry),
            tasks=[task.model_dump(mode="json") for task in typed_tasks],
            global_constraints=dict(global_constraints or {}),
            routing_metadata={
                "execution_shape": dict(execution_shape or {}),
                "streaming_operator_mode": bool(streaming_operator_mode),
                "reasoning_profile": str(getattr(request_config, "reasoning_profile", "")),
                "operator_policy_profile": str(getattr(request_config, "operator_policy_profile", "")),
            },
        )

    def _lrnt_after_request(
        self,
        *,
        learning_payload: LrnTotalTaskWrite | None,
        lrt_candidate: LrnTotalTaskCandidate | None,
        final_status: str,
        observability: ObservabilityContext | None,
        trace: PlanningTrace | None,
    ) -> None:
        store = self.lrn_total_task_store
        if store is None:
            return
        normalized_status = str(final_status or "").strip().lower()
        if lrt_candidate is not None:
            if normalized_status in {"error", "partial", "unsupported"}:
                entry = store.mark_failed(
                    lrt_candidate.entry.entry_id,
                    failure_category=f"reuse_final_status:{normalized_status}",
                    quarantine=True,
                )
                if trace is not None:
                    trace.metadata["lrt_quarantined"] = {
                        "entry_id": lrt_candidate.entry.entry_id,
                        "final_status": normalized_status,
                    }
                if observability is not None:
                    observability.warning(
                        STAGE_COMPLETED,
                        "operator.lrt.quarantined",
                        "LR-T entry quarantined",
                        "A reused LR-T total-task structure failed and was quarantined.",
                        details={
                            "entry_id": lrt_candidate.entry.entry_id,
                            "final_status": normalized_status,
                            "failure_count": entry.failure_count if entry else None,
                        },
                    )
            return
        if learning_payload is None or normalized_status != "success":
            return
        entry = store.upsert_entry(learning_payload)
        if trace is not None:
            trace.metadata["lrnt_write"] = {
                "entry_id": entry.entry_id,
                "task_count": len(entry.tasks),
                "workflow_mode": entry.workflow_mode,
            }
        if observability is not None:
            observability.info(
                STAGE_COMPLETED,
                "operator.lrnt.write",
                "LRN-T learned task structure",
                "A successful request taught a reusable total-task structure.",
                details={
                    "entry_id": entry.entry_id,
                    "task_count": len(entry.tasks),
                    "workflow_mode": entry.workflow_mode,
                    "success_count": entry.success_count,
                },
            )

    def _profile_final_status(self, default: str = "success") -> str:
        summary = self.last_profile_summary if isinstance(self.last_profile_summary, dict) else {}
        return str(summary.get("final_status") or default or "success")

    def handle_request(self, raw_prompt: str, context: dict[str, Any] = {}) -> str:
        """Run the full agent pipeline and return final user-facing text."""

        self._assert_registry_consistency()
        request_context = dict(context or {})
        database_discovery_response = self._maybe_handle_database_discovery_macro_request(
            raw_prompt,
            request_context,
        )
        if database_discovery_response is not None:
            return database_discovery_response
        user_request = self._build_user_request(raw_prompt, request_context)
        profiler = RuntimeProfiler(user_request.request_id)
        self.last_profile_summary = None
        user_request.safety_context["runtime_profiler"] = profiler
        request_context["runtime_profiler"] = profiler
        observability_config = request_context.get("observability")
        if isinstance(observability_config, dict):
            observability_config = dict(observability_config)
            observability_config.setdefault("runtime_profiler", profiler)
            request_context["observability"] = observability_config
        trace = self._bind_trace_to_request(user_request)
        planning_registry = self.registry.planning_view()
        trace.metadata["llm_visible_capability_registry"] = {
            "contract_hash": registry_contract_hash(planning_registry),
            "capability_ids": registry_capability_ids(planning_registry),
        }
        observability = build_observability_context(user_request.request_id, request_context)
        user_request.safety_context["observability"] = observability
        user_request.session_context["observability"] = observability
        llm_client = ProfilingLLMClient(self.llm_client, profiler)
        log_event(
            self.logger,
            "agent_runtime.request_received",
            request_id=user_request.request_id,
            prompt=user_request.raw_prompt,
        )
        observability.info(
            STAGE_REQUEST_RECEIVED,
            "request.received",
            "Request received",
            "The runtime accepted a new request and is starting the planning pipeline.",
            details={
                "request_id": user_request.request_id,
                "prompt_preview": user_request.raw_prompt[:240],
            },
        )
        user_request = self._maybe_rephrase_user_request(
            user_request,
            request_context,
            llm_client,
            observability,
            trace,
        )
        self._attach_agent_parameters(user_request, request_context)
        self._emit_agent_parameter_check(user_request, trace, observability)
        for safe_key in (
            PARAMETER_CONTEXT_KEY,
            PARAMETER_ENV_CONTEXT_KEY,
            "agent_parameters",
            "agent_parameter_matches",
            "agent_parameter_shell_env_names",
            USER_MACRO_SUMMARY_CONTEXT_KEY,
        ):
            if safe_key in user_request.session_context:
                request_context[safe_key] = user_request.session_context[safe_key]
        private_macros = user_request.safety_context.get(USER_MACRO_PRIVATE_CONTEXT_KEY)
        if isinstance(private_macros, list) and private_macros:
            request_context[USER_MACRO_PRIVATE_CONTEXT_KEY] = [
                dict(item) for item in private_macros if isinstance(item, dict)
            ]
        sql_response = self._maybe_handle_sql_agent_request(
            user_request,
            request_context,
            llm_client,
            trace,
        )
        if sql_response is not None:
            sql_final_status = (
                "clarification_required"
                if trace.metadata.get("operator_clarification_pending")
                and trace.metadata.get("operator_clarification_phase") == "sql_agent"
                else "confirmation_required"
                if trace.metadata.get("sql_pending_confirmation")
                else "success"
            )
            observability.stage_completed(
                STAGE_COMPLETED,
                (
                    "Request paused by SQL agent"
                    if sql_final_status == "clarification_required"
                    else "Request awaiting SQL confirmation"
                    if sql_final_status == "confirmation_required"
                    else "Request completed by SQL agent"
                ),
                (
                    "The SQL agent needs one user clarification before continuing."
                    if sql_final_status == "clarification_required"
                    else "The SQL agent needs explicit confirmation before executing generated SQL."
                    if sql_final_status == "confirmation_required"
                    else "The SQL agent handled the database request before generic operator planning."
                ),
                details={"final_status": sql_final_status, "output_type": "sql_agent"},
            )
            self._finalize_profile(user_request, final_status=sql_final_status)
            return sql_response
        sql_agentic_context_present = self._sql_agentic_context_present(request_context)
        if sql_agentic_context_present and (
            self._operator_mode_requested(request_context)
            or self._streaming_operator_requested(request_context)
        ):
            trace.metadata["sql_agent_operator_route_suppressed"] = True
        operator_mode = self._operator_mode_requested(request_context) and not sql_agentic_context_present
        streaming_operator_mode = (
            self._streaming_operator_requested(request_context)
            and not sql_agentic_context_present
        )
        operator_conversation_context_present = isinstance(
            request_context.get("operator_conversation_context"), dict
        )
        lrdirect_streaming_suppresses_followup = (
            streaming_operator_mode
            and operator_conversation_context_present
            and bool(
                request_context.get(
                    "lrdirect_enabled",
                    getattr(
                        self._runtime_config_for_context(request_context),
                        "lrdirect_enabled",
                        False,
                    ),
                )
            )
        )
        if lrdirect_streaming_suppresses_followup:
            trace.metadata["operator_conversation_followup_suppressed_for_lrdirect_streaming"] = True
        if operator_mode and (
            not streaming_operator_mode
            or (
                operator_conversation_context_present
                and not lrdirect_streaming_suppresses_followup
            )
        ):
            try:
                return self._handle_operator_request(
                    user_request,
                    request_context,
                    llm_client,
                    observability,
                    trace,
                )
            except Exception as exc:
                return self._safe_failure(user_request, OPERATOR_STAGE, str(exc))

        lrnt_learning_payload: LrnTotalTaskWrite | None = None
        lrt_reuse_candidate: LrnTotalTaskCandidate | None = None
        try:
            observability.stage_started(
                STAGE_PROMPT_CLASSIFICATION,
                "Prompt classification started",
                "The runtime is classifying the prompt and deciding whether tools are needed.",
            )
            request_config = self._runtime_config_for_context(request_context)
            request_profile_policy = operator_profile_policy(
                request_config.reasoning_profile,
                llm_operator_verbose_enabled=request_config.llm_operator_verbose_enabled,
            )
            # Keep the legacy classifier on the standard-Agent route. The compact
            # classifier is cheap, but in practice it can confuse ordinary local
            # work (Git/Docker/shell) with the runtime-control domain, which then
            # sends simple operator requests through the noisy typed-capability
            # planner. Fast/balanced still use compact downstream operator
            # contracts; this restores only the routing decision.
            classification = classify_prompt(user_request, llm_client, planning_registry)
            classification = self._apply_sql_agentic_classification(classification, request_context)
            observability.info(
                STAGE_PROMPT_CLASSIFICATION,
                EVENT_LLM_PROPOSAL_RECEIVED,
                "Classification proposal received",
                "The runtime received a structured classification proposal.",
                details={
                    "prompt_type": classification.prompt_type,
                    "likely_domains": classification.likely_domains,
                    "risk_level": classification.risk_level,
                    "needs_clarification": classification.needs_clarification,
                },
            )
            observability.info(
                STAGE_PROMPT_CLASSIFICATION,
                EVENT_VALIDATION_ACCEPTED,
                "Classification accepted",
                "The classification passed deterministic validation.",
                details={
                    "prompt_type": classification.prompt_type,
                    "requires_tools": classification.requires_tools,
                },
            )
            log_event(
                self.logger,
                "agent_runtime.classified",
                request_id=user_request.request_id,
                prompt_type=classification.prompt_type,
                requires_tools=classification.requires_tools,
                likely_domains=classification.likely_domains,
            )
            observability.stage_completed(
                STAGE_PROMPT_CLASSIFICATION,
                "Prompt classification completed",
                "Prompt classification finished successfully.",
                details={
                    "prompt_type": classification.prompt_type,
                    "requires_tools": classification.requires_tools,
                },
            )
            self._attach_agent_memory(
                user_request,
                request_context,
                task_type=str(classification.prompt_type or ""),
                intent_type=str(classification.risk_level or ""),
                tags=list(classification.likely_domains or []),
            )
            self._emit_agent_memory_check(user_request, trace, observability)

            if classification.prompt_type == "simple_question" and not classification.requires_tools:
                observability.stage_started(
                    "direct_answer",
                    "Direct answer started",
                    "The runtime is answering a no-tool question directly with the LLM.",
                )
                self._attach_online_ai_direct_lookup(user_request, request_context, observability)
                self._attach_online_mode_direct_lookup(user_request, request_context, observability)
                content = self._direct_answer(user_request, llm_client)
                observability.info(
                    "direct_answer",
                    EVENT_LLM_PROPOSAL_RECEIVED,
                    "Direct answer received",
                    "The runtime received a structured no-tool direct answer.",
                    details={"content_length": len(content)},
                )
                observability.stage_completed(
                    "direct_answer",
                    "Direct answer completed",
                    "The no-tool direct answer finished successfully.",
                    details={"content_length": len(content)},
                )
                observability.stage_completed(
                    STAGE_COMPLETED,
                    "Request completed",
                    "The request finished without invoking any capabilities.",
                    details={"final_status": "success", "output_type": "direct_answer"},
                )
                self._finalize_profile(user_request, final_status="success")
                return content

            classification_context = {
                "original_prompt": user_request.raw_prompt,
                "prompt_type": classification.prompt_type,
                "requires_tools": classification.requires_tools,
                "likely_domains": classification.likely_domains,
                "risk_level": classification.risk_level,
            }
            execution_shape = self._attach_execution_shape_hint(
                user_request,
                request_context,
                trace,
                observability,
                classification_context=classification_context,
            )

            if (
                (
                    self._single_report_operator_route_requested(
                        execution_shape,
                        classification_context,
                    )
                    or self._standard_operator_classification_route_requested(
                        classification_context
                    )
                )
                and not streaming_operator_mode
            ):
                self._attach_online_ai_direct_lookup(user_request, request_context, observability)
                self._attach_online_mode_direct_lookup(user_request, request_context, observability)
                intent_block = self._operator_intent_block(
                    tasks=[],
                    global_constraints={},
                    classification_context=classification_context,
                    execution_shape=execution_shape,
                )
                observability.info(
                    STAGE_PROMPT_CLASSIFICATION,
                    EVENT_VALIDATION_ACCEPTED,
                    "Operator-native route selected",
                    "The standard Agent pipeline routed ordinary local work directly to the shared operator planner.",
                    details={
                        "likely_domains": classification.likely_domains,
                        "prompt_type": classification.prompt_type,
                    },
                )
                return self._handle_standard_operator_request(
                    user_request,
                    request_context,
                    llm_client,
                    observability,
                    trace,
                    intent_block=intent_block,
                )

            lrnt_lookup = self._lrt_lookup_total_tasks(
                user_request=user_request,
                request_context=request_context,
                llm_client=llm_client,
                planning_registry=planning_registry,
                classification_context=classification_context,
                observability=observability,
                trace=trace,
            )
            if lrnt_lookup is not None:
                decomposition, lrt_reuse_candidate = lrnt_lookup
                typed_tasks = list(decomposition.tasks)
            else:
                observability.stage_started(
                    STAGE_DECOMPOSITION,
                    "Task decomposition started",
                    "The runtime is breaking the prompt into atomic tasks.",
                )
                if sql_agentic_context_present:
                    decomposition = self._sql_agentic_single_query_decomposition(
                        user_request,
                        request_context,
                    )
                    trace.metadata["sql_agentic_decomposition_normalized"] = {
                        "strategy": "single_sql_query_node",
                        "task_ids": [task.id for task in decomposition.tasks],
                    }
                elif request_profile_policy.contract_mode == "verbose":
                    decomposition = decompose_prompt(
                        user_request,
                        classification,
                        llm_client,
                        available_domains=sorted({manifest.domain for manifest in planning_registry.list_manifests()}),
                        registry=planning_registry,
                    )
                else:
                    decomposition = decompose_prompt_compact(
                        user_request,
                        classification,
                        llm_client,
                        available_domains=sorted({manifest.domain for manifest in planning_registry.list_manifests()}),
                        registry=planning_registry,
                    )
                observability.info(
                    STAGE_DECOMPOSITION,
                    EVENT_LLM_PROPOSAL_RECEIVED,
                    "Task decomposition received",
                    "The runtime received a structured task decomposition.",
                    details={
                        "task_count": len(decomposition.tasks),
                        "tasks": [
                            {
                                "task_id": task.id,
                                "description": task.description,
                                "semantic_verb": task.semantic_verb,
                                "object_type": task.object_type,
                                "constraints": dict(task.constraints),
                                "depends_on": list(task.dependencies),
                            }
                            for task in decomposition.tasks
                        ],
                        "unresolved_references": decomposition.unresolved_references,
                    },
                )
                log_event(
                    self.logger,
                    "agent_runtime.decomposed",
                    request_id=user_request.request_id,
                    task_count=len(decomposition.tasks),
                    global_constraints=decomposition.global_constraints,
                )
                observability.stage_completed(
                    STAGE_DECOMPOSITION,
                    "Task decomposition completed",
                    "Task decomposition completed with validated task frames.",
                    details={
                        "task_count": len(decomposition.tasks),
                        "global_constraints": decomposition.global_constraints,
                        "unresolved_references": decomposition.unresolved_references,
                    },
                )

                enriched_tasks = []
                for task in decomposition.tasks:
                    task_constraints = dict(task.constraints)
                    task.constraints = {
                        **task_constraints,
                        "global_constraints": dict(decomposition.global_constraints),
                    }
                    enriched_tasks.append(task)

                observability.stage_started(
                    STAGE_VERB_ASSIGNMENT,
                    "Semantic verb assignment started",
                    "The runtime is assigning semantic verbs and object types to each task.",
                )
                if sql_agentic_context_present:
                    typed_tasks = list(enriched_tasks)
                    trace.metadata["sql_agentic_verb_assignment_forced"] = [
                        task.id for task in typed_tasks
                    ]
                elif request_profile_policy.run_semantic_verb_llm:
                    typed_tasks = assign_semantic_verbs(
                        enriched_tasks,
                        llm_client,
                        planning_registry,
                        likely_domains=classification.likely_domains,
                        trace=trace,
                    )
                else:
                    typed_tasks = normalize_semantic_verbs_deterministic(
                        enriched_tasks,
                        planning_registry,
                        likely_domains=classification.likely_domains,
                        trace=trace,
                    )
                observability.info(
                    STAGE_VERB_ASSIGNMENT,
                    EVENT_VALIDATION_ACCEPTED,
                    "Verb assignments accepted",
                    "Semantic task annotations passed deterministic validation.",
                    details={
                        "tasks": [
                            {
                                "task_id": task.id,
                                "description": task.description,
                                "semantic_verb": task.semantic_verb,
                                "object_type": task.object_type,
                                "risk_level": task.risk_level,
                                "operation_intent": task.operation_intent,
                                "side_effect_type": task.side_effect_type,
                                "constraints": dict(task.constraints),
                            }
                            for task in typed_tasks
                        ]
                    },
                )
                log_event(
                    self.logger,
                    "agent_runtime.verbs_assigned",
                    request_id=user_request.request_id,
                    tasks=[{"task_id": task.id, "verb": task.semantic_verb} for task in typed_tasks],
                )
                observability.stage_completed(
                    STAGE_VERB_ASSIGNMENT,
                    "Semantic verb assignment completed",
                    "Each task now has a semantic verb and object type.",
                    details={"task_count": len(typed_tasks)},
                )

            lrnt_learning_payload = self._lrnt_write_payload(
                user_request=user_request,
                request_context=request_context,
                llm_client=llm_client,
                planning_registry=planning_registry,
                classification_context=classification_context,
                execution_shape=execution_shape,
                typed_tasks=typed_tasks,
                global_constraints=decomposition.global_constraints,
                streaming_operator_mode=streaming_operator_mode,
            )

            if (
                streaming_operator_mode
                and typed_tasks
                and self._standard_operator_route_requested(typed_tasks, classification_context)
            ):
                observability.info(
                    STAGE_CAPABILITY_SELECTION,
                    EVENT_VALIDATION_ACCEPTED,
                    "Streaming operator route selected",
                    "The runtime will execute decomposed tasks one operator step at a time.",
                    details={
                        "task_count": len(typed_tasks),
                        "likely_domains": classification.likely_domains,
                    },
                )
                content = self._handle_streaming_operator_request(
                    user_request,
                    request_context,
                    llm_client,
                    observability,
                    trace,
                    tasks=typed_tasks,
                    global_constraints=decomposition.global_constraints,
                    classification_context=classification_context,
                    execution_shape=execution_shape,
                )
                self._lrnt_after_request(
                    learning_payload=lrnt_learning_payload,
                    lrt_candidate=lrt_reuse_candidate,
                    final_status=self._profile_final_status("success"),
                    observability=observability,
                    trace=trace,
                )
                return content

            if self._standard_operator_route_requested(typed_tasks, classification_context):
                self._attach_online_ai_direct_lookup(user_request, request_context, observability)
                self._attach_online_mode_task_lookups(
                    user_request,
                    request_context,
                    observability,
                    typed_tasks,
                )
                intent_block = self._operator_intent_block(
                    tasks=typed_tasks,
                    global_constraints=decomposition.global_constraints,
                    classification_context=classification_context,
                    execution_shape=execution_shape,
                )
                observability.info(
                    STAGE_CAPABILITY_SELECTION,
                    EVENT_VALIDATION_ACCEPTED,
                    "Operator-native route selected",
                    "The standard Agent pipeline routed ordinary local work to the shared operator planner.",
                    details={
                        "task_count": len(typed_tasks),
                        "likely_domains": classification.likely_domains,
                    },
                )
                content = self._handle_standard_operator_request(
                    user_request,
                    request_context,
                    llm_client,
                    observability,
                    trace,
                    intent_block=intent_block,
                )
                self._lrnt_after_request(
                    learning_payload=lrnt_learning_payload,
                    lrt_candidate=lrt_reuse_candidate,
                    final_status=self._profile_final_status("success"),
                    observability=observability,
                    trace=trace,
                )
                return content

            observability.stage_started(
                STAGE_CAPABILITY_SELECTION,
                "Capability selection started",
                "The runtime is ranking capability candidates for each task.",
            )
            if sql_agentic_context_present:
                selections = self._sql_agentic_capability_selections(typed_tasks)
                trace.metadata["sql_agentic_forced_query_capability_task_ids"] = [
                    selection.task_id for selection in selections
                ]
            else:
                selections = select_capabilities(
                    typed_tasks,
                    planning_registry,
                    llm_client,
                    classification_context=classification_context,
                    trace=trace,
                )
            selection_contract_errors = validate_selected_capability_contracts(
                selections,
                planning_registry,
            )
            if selection_contract_errors:
                trace.validation_errors.extend(selection_contract_errors)
                raise ValidationError(
                    "Selected capability contract drift detected: "
                    + "; ".join(selection_contract_errors[:8])
                )
            for selection in selections:
                observability.info(
                    STAGE_CAPABILITY_SELECTION,
                    EVENT_CAPABILITY_CANDIDATE,
                    "Capability candidates considered",
                    "The runtime ranked capability candidates for one task.",
                    details={
                        "task_id": selection.task_id,
                        "candidates": [
                            {
                                "capability_id": candidate.capability_id,
                                "operation_id": candidate.operation_id,
                                "confidence": candidate.confidence,
                                "manifest_hash": (
                                    manifest_contract_hash(
                                    planning_registry.get(candidate.capability_id).manifest
                                    )
                                    if candidate.capability_id
                                    in set(registry_capability_ids(planning_registry))
                                    else None
                                ),
                            }
                            for candidate in selection.candidates
                        ],
                    },
                )
                if selection.selected is not None:
                    observability.info(
                        STAGE_CAPABILITY_SELECTION,
                        EVENT_CAPABILITY_SELECTED,
                        "Capability selected",
                        "A candidate capability was selected for this task.",
                        details={
                            "task_id": selection.task_id,
                            "capability_id": selection.selected.capability_id,
                            "operation_id": selection.selected.operation_id,
                            "confidence": selection.selected.confidence,
                            "manifest_contract": manifest_contract(
                                planning_registry.get(selection.selected.capability_id).manifest
                            ),
                            "manifest_hash": manifest_contract_hash(
                                planning_registry.get(selection.selected.capability_id).manifest
                            ),
                        },
                    )
                elif selection.unresolved_reason:
                    observability.warning(
                        STAGE_CAPABILITY_SELECTION,
                        EVENT_VALIDATION_REJECTED,
                        "Capability unresolved",
                        "No capability candidate was accepted for this task.",
                        details={
                            "task_id": selection.task_id,
                            "reason": selection.unresolved_reason,
                        },
                    )
            log_event(
                self.logger,
                "agent_runtime.capabilities_selected",
                request_id=user_request.request_id,
                selections=[
                    {
                        "task_id": result.task_id,
                        "selected": (
                            result.selected.capability_id if result.selected is not None else None
                        ),
                        "unresolved_reason": result.unresolved_reason,
                    }
                    for result in selections
                ],
            )
            observability.stage_completed(
                STAGE_CAPABILITY_SELECTION,
                "Capability selection completed",
                "Capability ranking and preliminary selection completed.",
                details={"task_count": len(selections)},
            )

            observability.stage_started(
                STAGE_CAPABILITY_FIT,
                "Capability fit started",
                "The runtime is checking whether selected capabilities truly fit each task.",
            )
            if sql_agentic_context_present:
                fit_decisions = self._sql_agentic_fit_decisions(
                    typed_tasks,
                    planning_registry,
                    classification_context,
                )
                capability_gaps = []
                output_contract_resolutions = []
            else:
                fit_decisions, capability_gaps = assess_capability_fit(
                    typed_tasks,
                    selections,
                    planning_registry,
                    classification_context,
                    llm_client,
                    trace=trace,
                )
                output_contract_resolutions = resolve_tasks_from_output_contracts(
                    typed_tasks,
                    fit_decisions,
                    selections,
                    planning_registry,
                    classification_context,
                    llm_client=llm_client,
                    trace=trace,
                )
            resolved_output_task_ids = {item.task_id for item in output_contract_resolutions}
            if output_contract_resolutions:
                trace.metadata["output_contract_resolutions"] = [
                    item.model_dump(mode="json") for item in output_contract_resolutions
                ]
                decomposition.global_constraints["resolved_output_contracts"] = [
                    item.model_dump(mode="json") for item in output_contract_resolutions
                ]
                for task_id in resolved_output_task_ids:
                    trace.capability_gaps_by_task.pop(task_id, None)
            fit_by_task = {decision.task_id: decision for decision in fit_decisions}
            selections, reconciled_selection_task_ids = (
                self._reconcile_fit_approved_capability_selections(
                    selections,
                    fit_decisions,
                )
            )
            if reconciled_selection_task_ids:
                trace.metadata["capability_selection_reconciled_from_fit"] = (
                    reconciled_selection_task_ids
                )
                observability.info(
                    STAGE_CAPABILITY_FIT,
                    EVENT_VALIDATION_ACCEPTED,
                    "Capability selection reconciled",
                    "A fit-approved fallback candidate was promoted into downstream selection state.",
                    details={"task_ids": reconciled_selection_task_ids},
                )
            for decision in fit_decisions:
                if decision.task_id in resolved_output_task_ids:
                    continue
                if decision.is_fit:
                    llm_raw_preview = (
                        decision.llm_diagnostics.raw_response_preview
                        if observability.debug and decision.llm_diagnostics is not None
                        else None
                    )
                    observability.info(
                        STAGE_CAPABILITY_FIT,
                        EVENT_VALIDATION_ACCEPTED,
                        "Capability fit accepted",
                        "The selected capability passed semantic and deterministic fit checks.",
                        details={
                            "task_id": decision.task_id,
                            "capability_id": decision.candidate_capability_id,
                            "llm_fits": (
                                decision.llm_proposal.fits
                                if decision.llm_proposal is not None
                                else None
                            ),
                            "llm_primary_failure_mode": (
                                decision.llm_proposal.primary_failure_mode
                                if decision.llm_proposal is not None
                                else None
                            ),
                            "llm_confidence": (
                                decision.llm_proposal.confidence
                                if decision.llm_proposal is not None
                                else None
                            ),
                            "llm_error_kind": (
                                decision.llm_diagnostics.error_kind
                                if decision.llm_diagnostics is not None
                                else None
                            ),
                            "llm_error_message": (
                                decision.llm_diagnostics.error_message
                                if decision.llm_diagnostics is not None
                                else None
                            ),
                            "llm_raw_response_preview": llm_raw_preview,
                            "status": decision.status,
                            "reasons": decision.reasons,
                            "manifest_contract": decision.candidate_manifest_contract,
                            "manifest_hash": decision.candidate_manifest_hash,
                            "normalized_task_domain": decision.normalized_task_domain,
                            "normalized_likely_domains": decision.normalized_likely_domains,
                            "normalized_task_object_type": decision.normalized_task_object_type,
                            "normalized_manifest_domain": decision.normalized_manifest_domain,
                            "normalized_manifest_object_types": decision.normalized_manifest_object_types,
                        },
                    )
                else:
                    llm_raw_preview = (
                        decision.llm_diagnostics.raw_response_preview
                        if observability.debug and decision.llm_diagnostics is not None
                        else None
                    )
                    observability.warning(
                        STAGE_CAPABILITY_FIT,
                        EVENT_CAPABILITY_REJECTED,
                        "Capability rejected",
                        "A selected candidate was rejected by capability fit validation.",
                        details={
                            "task_id": decision.task_id,
                            "capability_id": decision.candidate_capability_id,
                            "llm_fits": (
                                decision.llm_proposal.fits
                                if decision.llm_proposal is not None
                                else None
                            ),
                            "llm_primary_failure_mode": (
                                decision.llm_proposal.primary_failure_mode
                                if decision.llm_proposal is not None
                                else None
                            ),
                            "llm_confidence": (
                                decision.llm_proposal.confidence
                                if decision.llm_proposal is not None
                                else None
                            ),
                            "llm_error_kind": (
                                decision.llm_diagnostics.error_kind
                                if decision.llm_diagnostics is not None
                                else None
                            ),
                            "llm_error_message": (
                                decision.llm_diagnostics.error_message
                                if decision.llm_diagnostics is not None
                                else None
                            ),
                            "llm_raw_response_preview": llm_raw_preview,
                            "status": decision.status,
                            "reasons": decision.reasons,
                            "deterministic_rejections": decision.deterministic_rejections,
                            "manifest_contract": decision.candidate_manifest_contract,
                            "manifest_hash": decision.candidate_manifest_hash,
                            "normalized_task_domain": decision.normalized_task_domain,
                            "normalized_likely_domains": decision.normalized_likely_domains,
                            "normalized_task_object_type": decision.normalized_task_object_type,
                            "normalized_manifest_domain": decision.normalized_manifest_domain,
                            "normalized_manifest_object_types": decision.normalized_manifest_object_types,
                        },
                    )
            for resolution in output_contract_resolutions:
                observability.info(
                    STAGE_CAPABILITY_FIT,
                    EVENT_VALIDATION_ACCEPTED,
                    "Task satisfied from upstream output",
                    "A downstream task was satisfied by metadata already returned from an upstream capability.",
                    details={
                        "task_id": resolution.task_id,
                        "producer_task_id": resolution.producer_task_id,
                        "producer_capability_id": resolution.producer_capability_id,
                        "producer_operation_id": resolution.producer_operation_id,
                        "matched_output_object_types": resolution.matched_output_object_types,
                        "matched_output_fields": resolution.matched_output_fields,
                        "matched_output_affordances": resolution.matched_output_affordances,
                        "resolution_source": resolution.resolution_source,
                        "llm_confidence": resolution.llm_confidence,
                        "reason": resolution.reason,
                    },
                )
            capability_gaps = [
                gap for gap in capability_gaps if gap.task_id not in resolved_output_task_ids
            ]
            for gap in capability_gaps:
                observability.warning(
                    STAGE_CAPABILITY_FIT,
                    EVENT_CAPABILITY_GAP,
                    "Capability gap detected",
                    "The runtime understood the task but does not have a compatible capability.",
                    details={
                        "task_id": gap.task_id,
                        "suggested_domain": gap.suggested_domain,
                        "suggested_object_type": gap.suggested_object_type,
                        "message": gap.user_facing_message,
                    },
                )
            log_event(
                self.logger,
                "agent_runtime.capability_fit_assessed",
                request_id=user_request.request_id,
                decisions=[
                    {
                        "task_id": decision.task_id,
                        "capability_id": decision.candidate_capability_id,
                        "status": decision.status,
                    }
                    for decision in fit_decisions
                ],
            )
            observability.stage_completed(
                STAGE_CAPABILITY_FIT,
                "Capability fit completed",
                "Capability fit validation completed.",
                details={
                    "fit_count": sum(1 for decision in fit_decisions if decision.is_fit),
                    "gap_count": len(capability_gaps),
                },
            )

            fit_tasks = [
                task
                for task in typed_tasks
                if task.id not in resolved_output_task_ids
                and (fit_by_task.get(task.id, None) is None or fit_by_task[task.id].is_fit)
            ]
            fit_selections = [
                selection
                for selection in selections
                if selection.task_id not in resolved_output_task_ids
                and (fit_by_task.get(selection.task_id, None) is None or fit_by_task[selection.task_id].is_fit)
            ]
            if not fit_tasks:
                message = self._render_capability_gaps(capability_gaps)
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="capability_gap",
                    stage="capability_fit",
                    reason=message,
                    metadata={
                        "gaps": [gap.model_dump(mode="json") for gap in capability_gaps],
                    },
                )
                if message not in trace.user_facing_errors:
                    trace.user_facing_errors.append(message)
                observability.stage_completed(
                    STAGE_COMPLETED,
                    "Request completed with capability gap",
                    "The runtime could not find a compatible capability for the request.",
                    details={"final_status": "unsupported", "gap_count": len(capability_gaps)},
                )
                self._lrnt_after_request(
                    learning_payload=None,
                    lrt_candidate=lrt_reuse_candidate,
                    final_status="unsupported",
                    observability=observability,
                    trace=trace,
                )
                self._finalize_profile(user_request, final_status="unsupported")
                return message

            observability.stage_started(
                STAGE_DATAFLOW_PLANNING,
                "Dataflow planning started",
                "The runtime is looking for producer-consumer relationships between tasks.",
            )
            dataflow_plan = plan_dataflow(
                original_prompt=user_request.raw_prompt,
                tasks=fit_tasks,
                capability_selections=fit_selections,
                registry=planning_registry,
                llm_client=llm_client,
                trace=trace,
            )
            for ref in dataflow_plan.refs:
                observability.info(
                    STAGE_DATAFLOW_PLANNING,
                    EVENT_DATAFLOW_REF_ACCEPTED,
                    "Dataflow reference accepted",
                    "A producer output was wired into a downstream task argument.",
                    details={
                        "consumer_task_id": ref.consumer_task_id,
                        "consumer_argument_name": ref.consumer_argument_name,
                        "producer_task_id": ref.producer_task_id,
                        "producer_output_key": ref.producer_output_key,
                    },
                )
            for binding in dataflow_plan.bindings:
                observability.info(
                    STAGE_DATAFLOW_PLANNING,
                    EVENT_DATAFLOW_BINDING_ACCEPTED,
                    "Dataflow binding accepted",
                    "A producer output key was bound into a declared downstream argument.",
                    details={
                        "consumer_task_id": binding.consumer_task_id,
                        "consumer_argument_name": binding.consumer_argument_name,
                        "producer_task_id": binding.producer_task_id,
                        "producer_output_key": binding.producer_output_key,
                        "transform": binding.transform,
                        "transform_parameters": binding.transform_parameters,
                        "parameter_provenance": binding.parameter_provenance,
                    },
                    debug_only=True,
                )
            for rejected in dataflow_plan.rejected_refs:
                observability.warning(
                    STAGE_DATAFLOW_PLANNING,
                    EVENT_DATAFLOW_REF_REJECTED,
                    "Dataflow reference rejected",
                    "A proposed producer-consumer reference did not pass validation.",
                    details=rejected,
                )
            for rejected in dataflow_plan.rejected_bindings:
                observability.warning(
                    STAGE_DATAFLOW_PLANNING,
                    EVENT_DATAFLOW_BINDING_REJECTED,
                    "Dataflow binding rejected",
                    "A proposed producer-output binding did not pass validation.",
                    details=rejected,
                    debug_only=True,
                )
            for derived in dataflow_plan.derived_tasks:
                observability.info(
                    STAGE_DATAFLOW_PLANNING,
                    EVENT_DATAFLOW_DERIVED_TASK_CREATED,
                    "Derived task created",
                    "The runtime created a derived internal data task.",
                    details={
                        "task_id": derived.task.id,
                        "description": derived.task.description,
                        "capability_id": derived.capability_id,
                        "depends_on": derived.depends_on,
                    },
                )
            log_event(
                self.logger,
                "agent_runtime.dataflow_planned",
                request_id=user_request.request_id,
                ref_count=len(dataflow_plan.refs),
                binding_count=len(dataflow_plan.bindings),
                derived_task_count=len(dataflow_plan.derived_tasks),
                unresolved_dataflows=dataflow_plan.unresolved_dataflows,
            )
            observability.stage_completed(
                STAGE_DATAFLOW_PLANNING,
                "Dataflow planning completed",
                "Dataflow planning completed.",
                details={
                    "accepted_refs": len(dataflow_plan.refs),
                    "accepted_bindings": len(dataflow_plan.bindings),
                    "rejected_bindings": len(dataflow_plan.rejected_bindings),
                    "derived_tasks": len(dataflow_plan.derived_tasks),
                    "unresolved_dataflows": dataflow_plan.unresolved_dataflows,
                },
            )

            observability.stage_started(
                STAGE_ARGUMENT_EXTRACTION,
                "Argument extraction started",
                "The runtime is filling validated capability arguments.",
            )
            if sql_agentic_context_present:
                extraction_results = self._sql_agentic_argument_results(
                    fit_tasks,
                    request_context,
                )
                trace.metadata["sql_agentic_argument_extraction_forced"] = [
                    result.task_id for result in extraction_results
                ]
            else:
                extraction_results = extract_arguments(
                    fit_tasks,
                    fit_selections,
                    planning_registry,
                    llm_client,
                    trace=trace,
                    dataflow_plan=dataflow_plan,
                    capability_fit_decisions=fit_decisions,
                    request_context=request_context,
                )
            for result in extraction_results:
                details = {
                    "task_id": result.task_id,
                    "capability_id": result.capability_id,
                    "arguments": {
                        key: (
                            "dataflow_binding"
                            if hasattr(value, "producer_node_id")
                            else "input_ref" if hasattr(value, "source_node_id") else value
                        )
                        for key, value in result.arguments.items()
                    },
                    "argument_sources": {
                        key: (
                            "dataflow_binding"
                            if hasattr(value, "producer_node_id")
                            else "input_ref" if hasattr(value, "source_node_id") else "extracted_or_normalized"
                        )
                        for key, value in result.arguments.items()
                    },
                    "assumptions": list(result.assumptions),
                    "generated_arguments": list(result.generated_arguments),
                    "rejected_generated_arguments": list(result.rejected_generated_arguments),
                    "missing_required_arguments": result.missing_required_arguments,
                }
                if result.missing_required_arguments:
                    observability.warning(
                        STAGE_ARGUMENT_EXTRACTION,
                        EVENT_ARGUMENT_REJECTED,
                        "Missing required arguments",
                        "Argument extraction could not fully satisfy this task.",
                        details=details,
                    )
                else:
                    observability.info(
                        STAGE_ARGUMENT_EXTRACTION,
                        EVENT_ARGUMENT_EXTRACTED,
                        "Arguments extracted",
                        "Validated arguments are ready for DAG construction.",
                        details=details,
                    )
            log_event(
                self.logger,
                "agent_runtime.arguments_extracted",
                request_id=user_request.request_id,
                extraction_results=[
                    {
                        "task_id": result.task_id,
                        "capability_id": result.capability_id,
                        "missing_required_arguments": result.missing_required_arguments,
                    }
                    for result in extraction_results
                ],
            )
            observability.stage_completed(
                STAGE_ARGUMENT_EXTRACTION,
                "Argument extraction completed",
                "Argument extraction completed for fit-approved tasks.",
                details={"task_count": len(extraction_results)},
            )

            dag_decomposition = decomposition.model_copy(update={"tasks": fit_tasks})
            observability.stage_started(
                STAGE_DAG_CONSTRUCTION,
                "DAG construction started",
                "The runtime is building a validated action DAG.",
            )
            dag = build_action_dag(
                user_request,
                dag_decomposition,
                fit_selections,
                extraction_results,
                dataflow_plan=dataflow_plan,
                capability_fit_decisions=fit_decisions,
            )
            trace.dag_raw = dag.model_dump(mode="json")
            append_trace_entry(
                trace,
                PlanningTraceEntry(
                    stage="dag_construction",
                    request_id=user_request.request_id,
                    prompt_template_id="dag_construction",
                    raw_llm_response=trace.dag_raw,
                    selected_candidate=trace.dag_raw,
                ),
            )
            for node in dag.nodes:
                observability.info(
                    STAGE_DAG_CONSTRUCTION,
                    EVENT_DAG_NODE_CREATED,
                    "DAG node created",
                    "An executable node was added to the action DAG.",
                    details={
                        "node_id": node.id,
                        "task_id": node.task_id,
                        "capability_id": node.capability_id,
                        "operation_id": node.operation_id,
                        "depends_on": list(node.depends_on),
                    },
                )
            for source, target in dag.edges:
                observability.info(
                    STAGE_DAG_CONSTRUCTION,
                    EVENT_DAG_EDGE_CREATED,
                    "DAG edge created",
                    "A dependency edge was added to the action DAG.",
                    details={"source_node_id": source, "target_node_id": target},
                )
            observability.stage_completed(
                STAGE_DAG_CONSTRUCTION,
                "DAG construction completed",
                "The action DAG is validated and ready for review.",
                details={"node_count": len(dag.nodes), "edge_count": len(dag.edges)},
            )

            observability.stage_started(
                STAGE_DAG_REVIEW,
                "DAG review started",
                "The runtime is running an advisory DAG review over sanitized metadata.",
            )
            skip_review, skip_reason = self._should_skip_dag_review(dag)
            if skip_review:
                self._record_dag_review_skipped(trace, user_request.request_id, dag, skip_reason)
                observability.info(
                    STAGE_DAG_REVIEW,
                    EVENT_VALIDATION_ACCEPTED,
                    "DAG review skipped",
                    "The runtime skipped the advisory DAG review for a trivial read-only DAG.",
                    details={"reason": skip_reason, "node_count": len(dag.nodes)},
                )
                review_confidence = None
            else:
                review = review_action_dag(user_request, dag, llm_client, registry=self.registry)
                self._record_dag_review(trace, user_request.request_id, review, dag, llm_client=llm_client)
                observability.info(
                    STAGE_DAG_REVIEW,
                    EVENT_LLM_PROPOSAL_RECEIVED,
                    "DAG review received",
                    "The advisory DAG review completed.",
                    details={
                        "missing_user_intents": review.missing_user_intents,
                        "suspicious_nodes": review.suspicious_nodes,
                        "dependency_warnings": review.dependency_warnings,
                        "dataflow_warnings": review.dataflow_warnings,
                        "output_expectation_warnings": review.output_expectation_warnings,
                    },
                )
                review_confidence = review.confidence
            log_event(
                self.logger,
                "agent_runtime.dag_built",
                request_id=user_request.request_id,
                dag_id=dag.dag_id,
                node_count=len(dag.nodes),
            )
            observability.stage_completed(
                STAGE_DAG_REVIEW,
                "DAG review completed",
                (
                    "Advisory DAG review was skipped by deterministic policy."
                    if skip_review
                    else "Advisory DAG review finished without changing the trusted DAG."
                ),
                details={"confidence": review_confidence, "skipped": skip_review},
            )

            observability.stage_started(
                STAGE_SAFETY_EVALUATION,
                "Safety evaluation started",
                "The runtime is checking the DAG against deterministic safety policy.",
            )
            safety_decision = evaluate_dag_safety(
                dag,
                self.registry,
                self._runtime_config_for_context(request_context),
            )
            ready_dag = self._mark_dag_execution_ready(dag, safety_decision)
            trace.final_dag_hash = ready_dag.final_dag_hash
            trace.dag_validated = ready_dag.model_dump(mode="json")
            trace.validated_dag = trace.dag_validated
            trace.safety_decision = safety_decision.model_dump(mode="json")
            trace.execution_ready = bool(safety_decision.allowed)
            self._record_safety(trace, user_request.request_id, safety_decision, ready_dag.final_dag_hash)
            self._record_last_plan(
                user_request=user_request,
                tasks=typed_tasks,
                selections=fit_selections,
                dag=ready_dag,
                safety_decision=safety_decision,
            )
            if safety_decision.allowed:
                observability.info(
                    STAGE_SAFETY_EVALUATION,
                    EVENT_SAFETY_ALLOWED,
                    "Safety evaluation allowed execution",
                    "The DAG passed deterministic safety checks.",
                    details={
                        "requires_confirmation": safety_decision.requires_confirmation,
                        "warnings": safety_decision.warnings,
                    },
                )
            else:
                observability.warning(
                    STAGE_SAFETY_EVALUATION,
                    EVENT_SAFETY_BLOCKED,
                    "Safety evaluation blocked execution",
                    "The DAG was blocked by deterministic safety policy.",
                    details={
                        "blocked_reasons": safety_decision.blocked_reasons,
                        "warnings": safety_decision.warnings,
                    },
                )
            log_event(
                self.logger,
                "agent_runtime.safety_evaluated",
                request_id=user_request.request_id,
                allowed=safety_decision.allowed,
                requires_confirmation=safety_decision.requires_confirmation,
                blocked_reasons=safety_decision.blocked_reasons,
            )
            observability.stage_completed(
                STAGE_SAFETY_EVALUATION,
                "Safety evaluation completed",
                "Safety evaluation finished.",
                details={
                    "allowed": safety_decision.allowed,
                    "requires_confirmation": safety_decision.requires_confirmation,
                },
            )

            execution_context = self._execution_context_with_agent_parameters(
                user_request,
                user_request.session_context,
            )
            execution_context.setdefault("raw_prompt", user_request.raw_prompt)
            execution_context["observability"] = observability
            execution_context["llm_client"] = llm_client
            monitor_manager = getattr(self, "monitor_manager", None)
            if monitor_manager is not None:
                execution_context["monitor_manager"] = monitor_manager
            result_bundle: ResultBundle
            observability.stage_started(
                STAGE_EXECUTION,
                "Execution started",
                "The runtime is executing the validated DAG.",
                details={"dag_id": ready_dag.dag_id, "node_count": len(ready_dag.nodes)},
            )
            if not safety_decision.allowed:
                result_bundle = ResultBundle(
                    dag_id=dag.dag_id,
                    results=[],
                    status="error",
                    safe_summary="Execution blocked by safety policy.",
                    metadata={
                        "blocked_reasons": list(safety_decision.blocked_reasons),
                        "warnings": list(safety_decision.warnings),
                        "confirmation_required": False,
                    },
                )
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="safety_block",
                    stage="safety",
                    reason="Execution blocked by safety policy.",
                    metadata={"blocked_reasons": list(safety_decision.blocked_reasons)},
                )
                observability.warning(
                    STAGE_EXECUTION,
                    EVENT_SAFETY_BLOCKED,
                    "Execution blocked",
                    "Execution did not start because safety policy blocked the DAG.",
                    details={"blocked_reasons": safety_decision.blocked_reasons},
                )
            elif safety_decision.requires_confirmation and not bool(execution_context.get("confirmation", False)):
                result_bundle = ResultBundle(
                    dag_id=dag.dag_id,
                    results=[],
                    status="confirmation_required",
                    safe_summary="Execution requires confirmation before proceeding.",
                    metadata={
                        "blocked_reasons": [],
                        "warnings": list(safety_decision.warnings),
                        "confirmation_required": True,
                        "confirmation_actions": self._confirmation_actions_for_dag(ready_dag),
                    },
                )
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="confirmation_required",
                    stage="safety",
                    reason="Execution requires confirmation before proceeding.",
                )
                observability.warning(
                    STAGE_EXECUTION,
                    EVENT_SAFETY_BLOCKED,
                    "Confirmation required",
                    "Execution is waiting for explicit confirmation.",
                    details={"warnings": safety_decision.warnings},
                )
            else:
                result_bundle = self.execution_engine.execute(ready_dag, execution_context)

            sql_clarification_payload = self._sql_clarification_payload_from_bundle(result_bundle)
            if sql_clarification_payload is not None:
                sql_clarification_response = self._record_sql_clarification(
                    user_request=user_request,
                    request_context=request_context,
                    payload=sql_clarification_payload,
                    trace=trace,
                    phase="sql_agentic",
                )
                observability.stage_completed(
                    STAGE_EXECUTION,
                    "Execution paused by SQL agent",
                    "A SQL capability needs one user clarification before continuing.",
                    details={"final_status": "clarification_required", "output_type": "sql_agent"},
                )
                observability.stage_completed(
                    STAGE_COMPLETED,
                    "Request paused by SQL agent",
                    "The request is waiting for one SQL clarification.",
                    details={"final_status": "clarification_required", "output_type": "sql_agent"},
                )
                self._finalize_profile(user_request, final_status="clarification_required")
                return sql_clarification_response

            sql_confirmation_payload = self._sql_confirmation_payload_from_bundle(result_bundle)
            if sql_confirmation_payload is not None:
                result_bundle = self._bundle_with_sql_confirmation(
                    result_bundle,
                    sql_confirmation_payload,
                )
                trace.metadata["operator_confirmation_actions"] = list(
                    result_bundle.metadata.get("confirmation_actions") or []
                )
                if isinstance(result_bundle.metadata.get("sql_pending_confirmation"), dict):
                    trace.metadata["sql_pending_confirmation"] = dict(
                        result_bundle.metadata["sql_pending_confirmation"]
                    )
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="confirmation_required",
                    stage="execution",
                    reason="The SQL agent needs confirmation before executing generated SQL.",
                    metadata={
                        "confirmation_actions": result_bundle.metadata.get(
                            "confirmation_actions"
                        )
                    },
                )
                observability.warning(
                    STAGE_EXECUTION,
                    EVENT_SAFETY_BLOCKED,
                    "SQL confirmation required",
                    "Execution is waiting for explicit confirmation of generated SQL.",
                    details={
                        "output_type": "sql_agent",
                        "generated_sql": sql_confirmation_payload.get("generated_sql"),
                        "executed_sql": sql_confirmation_payload.get("executed_sql"),
                    },
                )

            repair_metadata: dict[str, Any] | None = None
            if (
                result_bundle.status in {"error", "partial"}
                and not bool(execution_context.get("repair_attempted", False))
                and not bool(result_bundle.metadata.get("confirmation_required", False))
            ):
                observability.stage_started(
                    STAGE_FAILURE_REPAIR,
                    "Failure repair started",
                    "The runtime is evaluating one safe repair attempt.",
                )
                repaired_dag, repair_metadata = attempt_failure_repair(
                    user_request=user_request,
                    dag=ready_dag,
                    result_bundle=result_bundle,
                    registry=self.registry,
                    llm_client=llm_client,
                    safety_config=self._runtime_config_for_context(request_context),
                    repair_attempt_count=int(execution_context.get("repair_attempt_count", 0)),
                )
                if repair_metadata is not None:
                    observability.info(
                        STAGE_FAILURE_REPAIR,
                        EVENT_REPAIR_PROPOSED,
                        "Repair proposal received",
                        "The runtime evaluated one advisory repair proposal.",
                        details={
                            "repair_attempt_count": repair_metadata.get("repair_attempt_count"),
                            "proposed_action": (
                                dict(repair_metadata.get("proposal") or {}).get("proposed_action")
                            ),
                        },
                    )
                if repaired_dag is not None:
                    repaired_decision = evaluate_dag_safety(
                        repaired_dag,
                        self.registry,
                        self._runtime_config_for_context(request_context),
                    )
                    if repaired_decision.allowed:
                        ready_repaired_dag = self._mark_dag_execution_ready(repaired_dag, repaired_decision)
                        trace.final_dag_hash = ready_repaired_dag.final_dag_hash
                        trace.validated_dag = ready_repaired_dag.model_dump(mode="json")
                        trace.safety_decision = repaired_decision.model_dump(mode="json")
                        execution_context["repair_attempted"] = True
                        execution_context["repair_attempt_count"] = int(
                            repair_metadata.get("repair_attempt_count", 1)
                        )
                        observability.info(
                            STAGE_FAILURE_REPAIR,
                            EVENT_REPAIR_ACCEPTED,
                            "Repair accepted",
                            "A repaired DAG passed validation and safety checks, so execution will retry once.",
                            details={
                                "repair_attempt_count": execution_context["repair_attempt_count"],
                            },
                        )
                        result_bundle = self.execution_engine.execute(ready_repaired_dag, execution_context)
                if repair_metadata is not None:
                    model_name, temperature = llm_client_metadata(llm_client)
                    append_trace_entry(
                        trace,
                        PlanningTraceEntry(
                            stage="failure_repair",
                            request_id=user_request.request_id,
                            model_name=model_name,
                            llm_temperature=temperature,
                            prompt_template_id="failure_repair",
                            raw_llm_response=repair_metadata.get("proposal"),
                            parsed_proposal=repair_metadata.get("proposal"),
                            selected_candidate=repair_metadata,
                            rejection_reasons=(
                                [str(repair_metadata.get("rejected"))]
                                if repair_metadata.get("rejected")
                                else []
                            ),
                        ),
                    )
                    if repair_metadata.get("rejected"):
                        observability.warning(
                            STAGE_FAILURE_REPAIR,
                            EVENT_REPAIR_REJECTED,
                            "Repair rejected",
                            "The repair proposal did not pass deterministic validation.",
                            details={"reason": repair_metadata.get("rejected")},
                        )
                    observability.stage_completed(
                        STAGE_FAILURE_REPAIR,
                        "Failure repair completed",
                        "Failure repair handling completed.",
                        details={"attempted": bool(repair_metadata.get("attempted", False))},
                    )

            post_repair_sql_clarification = self._sql_clarification_payload_from_bundle(result_bundle)
            if post_repair_sql_clarification is not None:
                sql_clarification_response = self._record_sql_clarification(
                    user_request=user_request,
                    request_context=request_context,
                    payload=post_repair_sql_clarification,
                    trace=trace,
                    phase="sql_agentic",
                )
                observability.stage_completed(
                    STAGE_EXECUTION,
                    "Execution paused by SQL agent",
                    "A SQL capability needs one user clarification before continuing.",
                    details={"final_status": "clarification_required", "output_type": "sql_agent"},
                )
                observability.stage_completed(
                    STAGE_COMPLETED,
                    "Request paused by SQL agent",
                    "The request is waiting for one SQL clarification.",
                    details={"final_status": "clarification_required", "output_type": "sql_agent"},
                )
                self._finalize_profile(user_request, final_status="clarification_required")
                return sql_clarification_response

            post_repair_sql_confirmation = self._sql_confirmation_payload_from_bundle(result_bundle)
            if post_repair_sql_confirmation is not None:
                result_bundle = self._bundle_with_sql_confirmation(
                    result_bundle,
                    post_repair_sql_confirmation,
                )
                trace.metadata["operator_confirmation_actions"] = list(
                    result_bundle.metadata.get("confirmation_actions") or []
                )
                if isinstance(result_bundle.metadata.get("sql_pending_confirmation"), dict):
                    trace.metadata["sql_pending_confirmation"] = dict(
                        result_bundle.metadata["sql_pending_confirmation"]
                    )
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="confirmation_required",
                    stage="execution",
                    reason="The SQL agent needs confirmation before executing generated SQL.",
                    metadata={
                        "confirmation_actions": result_bundle.metadata.get(
                            "confirmation_actions"
                        )
                    },
                )

            log_event(
                self.logger,
                "agent_runtime.executed",
                request_id=user_request.request_id,
                dag_id=dag.dag_id,
                bundle_status=result_bundle.status,
                result_count=len(result_bundle.results),
            )
            observability.stage_completed(
                STAGE_EXECUTION,
                "Execution completed",
                "DAG execution finished.",
                details={
                    "bundle_status": result_bundle.status,
                    "result_count": len(result_bundle.results),
                },
            )
            if result_bundle.status in {"error", "partial"} and not bool(
                result_bundle.metadata.get("confirmation_required", False)
            ):
                first_error = next((result.error for result in result_bundle.results if result.error), None)
                self._record_last_failure(
                    request_id=user_request.request_id,
                    prompt=user_request.raw_prompt,
                    category="execution_error",
                    stage="execution",
                    reason=first_error or str(result_bundle.safe_summary or "Execution failed."),
                    metadata={
                        "bundle_status": result_bundle.status,
                        "safe_summary": result_bundle.safe_summary,
                    },
                )
            if repair_metadata is not None:
                result_bundle.metadata["repair_attempt"] = repair_metadata

            rendered = self.output_orchestrator.render(
                result_bundle,
                user_request=user_request,
                dag=dag,
                llm_client=llm_client,
            )
            display_document = rendered.metadata.get("display_document")
            self.last_display_document = (
                dict(display_document)
                if isinstance(display_document, dict)
                else None
            )
            final_request_status = self._final_request_status(
                result_bundle,
                len(capability_gaps),
            )
            log_event(
                self.logger,
                "agent_runtime.rendered",
                request_id=user_request.request_id,
                dag_id=dag.dag_id,
                content_length=len(rendered.content),
                final_status=final_request_status,
            )
            observability.stage_completed(
                STAGE_COMPLETED,
                (
                    "Request awaiting confirmation"
                    if final_request_status == "confirmation_required"
                    else "Request partially completed"
                    if final_request_status == "partial"
                    else "Request completed"
                ),
                (
                    "The runtime is waiting for explicit confirmation before running the approved plan."
                    if final_request_status == "confirmation_required"
                    else "The runtime rendered the supported subset of the request."
                    if final_request_status == "partial"
                    else "The request completed and a final response was rendered."
                ),
                details={
                    "final_status": final_request_status,
                    "content_length": len(rendered.content),
                    "display_type": rendered.display_plan.display_type,
                    "gap_count": len(capability_gaps),
                },
            )
            self._lrnt_after_request(
                learning_payload=lrnt_learning_payload,
                lrt_candidate=lrt_reuse_candidate,
                final_status=final_request_status,
                observability=observability,
                trace=trace,
            )
            self._finalize_profile(user_request, final_status=final_request_status)
            return rendered.content

        except (AgentRuntimeError, ValidationError, ValueError) as exc:
            self._lrnt_after_request(
                learning_payload=None,
                lrt_candidate=lrt_reuse_candidate,
                final_status="error",
                observability=observability,
                trace=trace,
            )
            return self._safe_failure(user_request, "runtime", str(exc))
        except Exception as exc:  # pragma: no cover - kept as a final safety boundary
            detail = user_error_detail(
                exc,
                stage="runtime",
                category="unexpected_error",
                context=request_context,
                request_id=user_request.request_id,
            )
            safe_message = user_error_message(detail)
            observability = self._observability(user_request)
            if observability is not None:
                observability.stage_failed(
                    "runtime",
                    str(detail.get("title") or "Unexpected runtime error"),
                    str(detail.get("likely_cause") or safe_message),
                    details={"error_type": type(exc).__name__, "error_detail": detail},
                )
                observability.stage_completed(
                    STAGE_COMPLETED,
                    "Request completed with error",
                    str(detail.get("fix_hint") or "The request ended with a runtime error."),
                    details={
                        "final_status": "error",
                        "failed_stage": "runtime",
                        "error_detail": detail,
                    },
                )
            log_event(
                self.logger,
                "agent_runtime.unexpected_error",
                request_id=user_request.request_id,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            self._record_last_failure(
                request_id=user_request.request_id,
                prompt=user_request.raw_prompt,
                category="unexpected_error",
                stage="runtime",
                reason=str(exc),
                metadata={"error_type": type(exc).__name__, "error_detail": detail},
            )
            if safe_message not in trace.user_facing_errors:
                trace.user_facing_errors.append(safe_message)
            self._lrnt_after_request(
                learning_payload=None,
                lrt_candidate=lrt_reuse_candidate,
                final_status="error",
                observability=observability,
                trace=trace,
            )
            self._finalize_profile(user_request, final_status="error", failed_stage="runtime")
            return safe_message


del _StateMixin, _ConfirmationMixin, _MemoryParameterMixin, _RuntimeServicesMixin, _SqlAgenticMixin, _StreamingStateMixin, _OnlineLookupMixin, _OperatorFlowMixin, _ReplayContinueMixin, _FinalizationMixin

_core_package = _sys.modules.get("agent_runtime.core")
if _core_package is not None and hasattr(_core_package, "orchestrator_support"):
    delattr(_core_package, "orchestrator_support")
del _core_package, _sys
