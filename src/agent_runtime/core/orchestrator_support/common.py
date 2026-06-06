"""Shared imports for AgentRuntime orchestrator support modules."""

from __future__ import annotations

import json
import re
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
    explicit_sudo_parameter_record_from_prompt,
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
