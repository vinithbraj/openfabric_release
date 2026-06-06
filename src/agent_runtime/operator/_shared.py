"""Shared imports and regex constants for operator pipeline modules."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

import ast
import hashlib
import json
import re
import shlex
import traceback
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from agent_runtime.core.config import RuntimeConfig
from agent_runtime.core.domain_hints import detect_domain_hints
from agent_runtime.core.ids import new_id
from agent_runtime.core.types import ExecutionResult, UserRequest
from agent_runtime.settings_consolidation import workflow_uses_streaming
from agent_runtime.execution.gateway_client import GatewayClient
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.input_pipeline.decomposition import _prompt_requires_runtime_observation
from agent_runtime.llm.reproducibility import llm_client_metadata
from agent_runtime.llm.structured_call import StructuredCallError, structured_call
from agent_runtime.memory import (
    AgentMemoryStore,
    MemoryRetrievalContext,
    evaluate_memory_guard_rules,
    enrich_memory_retrieval_hints,
    memory_directives_from_entries,
    memory_guard_rule_summaries,
    memory_prompt_lines_from_context,
    normalize_model_family,
)
from agent_runtime.parameters import parameter_prompt_lines_from_context
from agent_runtime.onlinelinelookup import online_lookup_prompt_lines
from agent_runtime.onlineaicheck import online_ai_check_prompt_lines
from agent_runtime.observability import ObservabilityContext
from agent_runtime.plan_cache import (
    AgentPlanCacheStore,
    PlanCacheCandidate,
    PlanCacheLookupContext,
    PlanCacheWrite,
)
from agent_runtime.command_template_cache import (
    AgentCommandTemplateCacheStore,
    CommandTemplateCandidate,
    CommandTemplateEntry,
    CommandTemplateLookupContext,
    CommandTemplateWrite,
    LR_MODE_DEFAULT,
    LR_MODE_PAYLOAD,
    exact_step_key as operator_exact_step_key,
    extract_template_input_names,
    lrdirect_canonical_step_excerpt,
    lrdirect_canonical_step_key,
    normalize_lr_mode,
    normalize_lrdirect_step_text,
    normalize_payload_bindings,
    normalize_template_variable_name,
    normalize_template_variables,
    render_template_with_values,
    template_env_name,
)
from agent_runtime.computation_cache import (
    AgentComputationCacheStore,
    ComputationCacheCandidate,
    ComputationCacheLookupContext,
    ComputationCacheWrite,
    input_profile as computation_input_profile,
    input_signature as computation_input_signature,
)
from agent_runtime.reliability import (
    AgentReliabilityStore,
    AnswerCoverageReview,
    AnswerObligationSet,
    ReliabilityController,
    model_id_from_client,
)
from agent_runtime.operator.action_runtime import (
    normalize_operator_plan_interactions,
    operator_action_requires_confirmation,
    operator_plan_requires_confirmation,
    operator_python_has_nonempty_inputs,
    operator_python_output_is_zero_like,
    operator_python_runtime_namespace,
    python_action_command,
    shell_command_looks_mutating,
    validate_operator_python_output,
)
from agent_runtime.operator.effects import (
    ActionEffect,
    classify_action_effect,
    classify_python_effect,
    classify_shell_effect,
    classify_task_effect,
)
from agent_runtime.operator.policy import (
    OPERATOR_POLICY_MODES,
    OPERATOR_POLICY_MODULES,
    normalize_operator_policy_mode,
    operator_policy_mode,
    operator_policy_modes_from_config,
    review_operator_policy_subjects,
)
from agent_runtime.operator.command_exceptions import (
    normalize_operator_command,
    operator_command_hash,
)
from agent_runtime.operator.sudo_policy import *
from agent_runtime.operator.final_formatter import (
    FormatterResult,
    FormatterSource,
    run_llm_final_formatter,
)
from agent_runtime.operator.literal_payloads import (
    OPERATOR_LITERAL_PAYLOADS_CONTEXT_KEY,
    literal_payload_prompt_lines,
    literal_payloads_from_context,
)
from agent_runtime.operator.models import (
    CodeGenerationCritique,
    DeliberationFrame,
    EvidenceSatisfactionReview,
    OperatorAction,
    OperatorClarificationDecision,
    OperatorClarificationOption,
    OperatorClarificationRequest,
    OperatorCommandTemplateDecision,
    OperatorCommandTemplateDraft,
    OperatorComputationCacheDecision,
    OperatorCompletionReview,
    OperatorDependency,
    OperatorStepValidationContract,
    OperatorStepValidationReview,
    OperatorTask,
    OperatorTextGenerationDraft,
    OperatorExecutionRecord,
    OperatorFailureContinuationReview,
    OperatorFollowupDecision,
    GeneratedPythonProofResult,
    OperatorInputBinding,
    MemoryComplianceReview,
    OperatorPlanCacheDecision,
    OperatorPipelineResult,
    OperatorPolicyDecision,
    OperatorPolicyReviewResult,
    OperatorPolicySubject,
    PlanCritique,
    OperatorPlan,
    OperatorPlanReview,
    OperatorPythonCodeProposal,
    OperatorPythonCodeReview,
    OperatorRepair,
    OperatorRepairAdjudication,
    OperatorRephraseRetryProposal,
    OperatorSelfBrief,
    OperatorTryoutCapsule,
    OperatorTryoutCapsuleBatch,
    OperatorTryoutResultReview,
    ShellStdoutErrorJudge,
    SudoRetryJudge,
    ValidationAdjudication,
)
from agent_runtime.operator.user_macros import (
    USER_MACRO_PRIVATE_CONTEXT_KEY,
    consume_typein_macro,
    is_user_macro_input_name,
    macro_value_with_enter,
    private_user_macros_from_context,
    user_macro_public_delivery,
    user_macro_summaries_from_context,
)


_EXECUTION_LEARNING_SENSITIVE_RE = re.compile(
    r"\b(?:password|passwd|passphrase|token|secret|credential|auth|api[_-]?key|ssh[-_\s]?key|typein)\b",
    re.IGNORECASE,
)


_MEMORY_TAG_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "any",
    "are",
    "action",
    "actions",
    "answer",
    "answers",
    "because",
    "before",
    "being",
    "can",
    "command",
    "commands",
    "computed",
    "could",
    "does",
    "failed",
    "failure",
    "for",
    "format",
    "formatted",
    "fresh",
    "from",
    "have",
    "into",
    "just",
    "must",
    "not",
    "only",
    "operator",
    "should",
    "output",
    "outputs",
    "post",
    "read",
    "request",
    "requested",
    "result",
    "results",
    "runtime",
    "shell",
    "state",
    "status",
    "stderr",
    "stdout",
    "success",
    "successful",
    "that",
    "the",
    "then",
    "there",
    "this",
    "use",
    "using",
    "verification",
    "verified",
    "verify",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "with",
    "you",
}

_GUIDED_DELIBERATION_PROMPT_RE = re.compile(
    r"\b("
    r"commit|push|stage|git\s+add|git\s+commit|git\s+push|"
    r"delete|remove|rm|write|create|edit|modify|refactor|implement|"
    r"install|upgrade|migrate|docker\s+compose\s+up|docker\s+run|"
    r"python|script|code|generate|fix|repair|deploy"
    r")\b",
    re.IGNORECASE,
)

_GUIDED_DELIBERATION_MUTATING_COMMAND_RE = re.compile(
    r"\b("
    r"git\s+(?:add|commit|push|reset|checkout|merge|rebase|init)|"
    r"docker\s+(?:compose\s+up|run|start|stop|rm|rmi|exec)|"
    r"rm\s+-|rm\s+|mv\s+|cp\s+|chmod\s+|chown\s+|"
    r"pip\s+install|npm\s+install|uv\s+add|poetry\s+add|"
    r"python\s+-m\s+pip\s+install"
    r")",
    re.IGNORECASE,
)

_BEST_EFFORT_INTENT_RE = re.compile(
    r"\b("
    r"best[-\s]?effort|optional|if\s+possible|try\s+to|attempt\s+to|"
    r"skip\s+if|ok(?:ay)?\s+if|fine\s+if|do\s+not\s+fail|non[-\s]?blocking"
    r")\b",
    re.IGNORECASE,
)
_FAILURE_MASKING_PIPE_RE = re.compile(r"\|\|\s*(?:true|:|echo\b|printf\b)", re.IGNORECASE)
_FAILURE_MASKING_CONDITIONAL_RE = re.compile(
    r"\bif\b.+\bthen\b.+\belse\b\s+(?:echo|printf)\b",
    re.IGNORECASE | re.DOTALL,
)
_STALE_FAILURE_VERIFIER_RE = re.compile(
    r"(\$\?\s+-eq\s+1|"
    r"\b(?:verify|check|ensure)\b.{0,40}\b(?:that|whether|if)\b"
    r".{0,80}\b(?:fail|failed|failure|non[-\s]?zero|exit\s+code\s+1)\b)",
    re.IGNORECASE | re.DOTALL,
)
_USER_REQUESTS_FAILURE_VERIFICATION_RE = re.compile(
    r"\b(?:verify|check|confirm|ensure|prove)\b.{0,80}\b(?:fail|failed|failure|non[-\s]?zero|exit\s+code\s+1)\b",
    re.IGNORECASE | re.DOTALL,
)

__all__ = [name for name in globals() if not name.startswith("__")]
