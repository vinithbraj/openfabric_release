"""Local agent debug UI and trace API routes."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import random
import re
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib import error as urllib_error
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib import request as urllib_request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import websockets

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_runtime.api import get_runtime_version_info, get_runtime_version_update_status
from agent_runtime.api.chat_store import AgentConversation, AgentConversationStore
from agent_runtime.api.config import Settings
from agent_runtime.api.runtime.engine import build_agent_runtime
from agent_runtime.api.settings_store import AgentUiSettingsStore
from agent_runtime.clarification import normalize_agent_clarification_mode
from agent_runtime.core.ids import new_id
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.events import (
    AgentEventCreate,
    AgentEventDraft,
    AgentEventDraftRequest,
    AgentEventDraftResponse,
    AgentEventRecord,
    AgentEventRunRecord,
    AgentEventScheduler,
    AgentEventStore,
    AgentEventUpdate,
    AgentNotificationCreate,
    AgentNotificationUpdate,
    DEFAULT_EVENT_NOTIFY_ON,
)
from agent_runtime.events.store import parse_utc_iso, utc_now_iso
from agent_runtime.execution.gateway_metadata import action_gateway_metadata, merge_gateway_metadata
from agent_runtime.gateways import AgentGatewayStore
from agent_runtime.learning_ledger import (
    AgentLearningLedgerStore,
    analyze_and_record_run,
    feedback_lesson_write,
)
from agent_runtime.learning_ledger.models import LearningLessonWrite
from agent_runtime.llm.tracing import TracingLLMClient
from agent_runtime.memory import (
    AgentMemoryStore,
    MemoryContextDraftRequest,
    MemoryContextDraftResponse,
    MemoryDraftResponse,
    MemoryEntry,
    MemoryEntryCreate,
    MemoryEntryUpdate,
    MemoryFeedbackDraftRequest,
    MemoryFeedbackDraftResponse,
    MemoryFeedbackRequest,
    MemoryOptimizationRequest,
    normalize_model_family,
)
from agent_runtime.monitors import (
    AgentMonitorCreate,
    AgentMonitorDraftRequest,
    AgentMonitorDraftResponse,
    AgentMonitorConflictError,
    AgentMonitorManager,
    AgentMonitorNotFoundError,
    AgentMonitorStore,
)
from agent_runtime.monitors.drafting import draft_monitor_from_prompt
from agent_runtime.parameters import (
    AgentParameterCreate,
    AgentParameterDraftRequest,
    AgentParameterStore,
    AgentParameterUpdate,
    draft_parameter_from_prompt,
    masked_parameter_summary,
)
from agent_runtime.plan_cache import AgentPlanCacheStore
from agent_runtime.lrn_total_tasks import AgentLrnTotalTaskStore
from agent_runtime.command_template_cache import AgentCommandTemplateCacheStore
from agent_runtime.computation_cache import AgentComputationCacheStore
from agent_runtime.prompts import (
    PromptTemplateComparison,
    PromptTemplateRenderError,
    PromptTemplateStore,
    configure_prompt_fetcher,
    extract_template_variables,
    prompt_lines,
)
from agent_runtime.reliability import (
    DEFAULT_RELIABILITY_EVAL_CASES,
    AgentReliabilityStore,
    run_reliability_eval,
)
from agent_runtime.settings_consolidation import (
    PROFILE_REPLACED_INTERNAL_KEYS,
    PUBLIC_RUNTIME_CONTROL_KEYS,
    PUBLIC_UI_PREFERENCE_KEYS,
    REMOVED_PUBLIC_SETTING_KEYS,
    normalize_cardinality_judge_mode,
    normalize_operator_policy_profile,
    normalize_reasoning_profile,
    normalize_repair_profile,
    normalize_shell_input_bindings_mode,
    normalize_workflow_execution_mode,
    repair_settings_from_profile,
    reasoning_settings_from_profile,
    removed_public_settings_in,
    settings_inventory_for_keys,
    strip_public_runtime_overrides,
)
from agent_runtime.observability.agent_trace import (
    AgentRequestTrace,
    AgentTraceEvent,
    AgentTraceSink,
    AgentTraceStore,
)
from agent_runtime.observability.redaction import redact_debug_value, redact_value
from agent_runtime.onlinelinelookup import (
    COMBINED_ONLINE_LOOKUP_PROVIDER,
    ONLINE_LOOKUP_CONTEXT_KEY,
    ONLINE_LOOKUP_CONTEXTS_KEY,
    ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY,
    lookup_online_answer,
    online_lookup_requested_from_context,
    sanitize_online_lookup_query,
)
from agent_runtime.onlineaicheck import (
    ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY,
    online_ai_check_requested_from_context,
)
from agent_runtime.operator.command_exceptions import OperatorCommandAllowlistStore
from agent_runtime.operator.literal_payloads import (
    OPERATOR_LITERAL_PAYLOADS_CONTEXT_KEY,
    extract_literal_payloads,
)
from agent_runtime.operator.user_macros import (
    ADDTOMEMORY_MACRO_KIND,
    AUTOAPPROVE_MACRO_KIND,
    REMIND_MACRO_KIND,
    RUNLATER_MACRO_KIND,
    TODO_MACRO_KIND,
    TYPEIN_INPUT_PREFIX,
    TYPEIN_MACRO_KIND,
    USER_MACRO_PRIVATE_CONTEXT_KEY,
    USER_MACRO_SUMMARY_CONTEXT_KEY,
    UserMacroParseError,
    parse_user_macros,
    prompt_requests_parameter_typein_terminal,
    prompt_macro_registry,
    typein_macro_payloads,
    typein_macro_payloads_from_parameter_record,
)
from agent_runtime.tasks import AgentTaskCreate, AgentTaskStore, AgentTaskUpdate
from agent_runtime.tasks.store import OPEN_TASK_STATUSES, utc_now_iso as task_utc_now_iso



__all__ = [name for name in globals() if not name.startswith("__")]
