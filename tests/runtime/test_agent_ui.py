from __future__ import annotations

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any
from urllib import error as urllib_error
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from agent_runtime.api import agent_ui
from agent_runtime.api.app import create_app
from agent_runtime.api.agent_ui_support.runtime_flow import (
    LEARNED_COMMAND_FAILURE_NOTICE,
    _append_learned_command_failure_notice,
)
from agent_runtime.api.chat_store import AgentConversationStore
from agent_runtime.api.config import Settings, get_settings
from agent_runtime.capabilities import build_default_registry
from agent_runtime.command_template_cache import AgentCommandTemplateCacheStore, CommandTemplateWrite, exact_step_key
from agent_runtime.computation_cache import AgentComputationCacheStore, ComputationCacheWrite
from agent_runtime.events import AgentEventStore, AgentNotificationCreate, AgentNotificationUpdate
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.gateways import AgentGatewayStore
from agent_runtime.gateways import store as gateway_store_module
from agent_runtime.monitors import AgentMonitorCreate, AgentMonitorStore, AgentMonitorUpdate
from agent_runtime.tasks import AgentTaskCreate, AgentTaskStore, AgentTaskUpdate
from agent_runtime.learning_ledger import AgentLearningLedgerStore, CapabilityProposalWrite, LearningLessonWrite
from agent_runtime.llm.client import LLMClientError
from agent_runtime.llm.tracing import TracingLLMClient, infer_stage_from_prompt
from agent_runtime.memory import AgentMemoryStore, MemoryRetrievalContext
from agent_runtime.observability import (
    AgentTraceEvent,
    AgentTraceSink,
    AgentTraceStore,
    PipelineEvent,
    build_observability_context,
)
from agent_runtime.onlinelinelookup import (
    ONLINE_LOOKUP_CONTEXT_KEY,
    ONLINE_LOOKUP_CONTEXTS_KEY,
    ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY,
    OnlineLookupResult,
)
from agent_runtime.onlineaicheck import ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY
from agent_runtime.operator.command_exceptions import OperatorCommandAllowlistStore, operator_command_hash
from agent_runtime.operator.literal_payloads import OPERATOR_LITERAL_PAYLOADS_CONTEXT_KEY
from agent_runtime.operator.user_macros import (
    USER_MACRO_PRIVATE_CONTEXT_KEY,
    USER_MACRO_SUMMARY_CONTEXT_KEY,
)
from agent_runtime.parameters import AgentParameterCreate, AgentParameterStore
from agent_runtime.settings_consolidation import PUBLIC_RUNTIME_CONTROL_KEYS, PUBLIC_UI_PREFERENCE_KEYS


class FakeAgentRuntime:
    """Tiny runtime for local agent UI route tests."""

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.last_context = dict(context or {})
        store = InMemoryResultStore()
        data_ref = store.put(
            "node-test",
            {"rows": [{"path": "README.txt"}], "truncated": False},
            "table",
            {"capability_id": "filesystem.search_files"},
        )
        self.execution_engine = SimpleNamespace(result_store=store)
        self.last_display_document = {
            "document_id": "display-doc-test",
            "request_id": "runtime-request",
            "target_ui": "agent_ui",
            "summary": "fake display document",
            "raw_available": True,
            "trace_refs": [],
            "sections": [
                {
                    "section_id": "section-test",
                    "title": "Fake Rows",
                    "primitive_id": "record_list",
                    "display_type": "table",
                    "shape_type": "record_list",
                    "source_node_id": "node-test",
                    "content": None,
                    "rows": [{"path": "README.txt"}],
                    "columns": ["path"],
                    "metadata": {},
                    "language": None,
                    "truncated": False,
                    "preview_count": 1,
                    "total_count": 1,
                    "data_ref": data_ref.ref_id,
                    "raw_available": True,
                }
            ],
        }
        payload = dict(context or {})
        observability_config = dict(payload.get("observability") or {})
        for sink in observability_config.get("sinks", []):
            sink.emit(
                PipelineEvent(
                    request_id="runtime-request",
                    level="info",
                    stage="prompt_classification",
                    event_type="stage.completed",
                    title="Prompt classification completed",
                    summary="The fake runtime classified the prompt.",
                    details={"prompt_type": "simple_tool_task"},
                )
            )
        return f"handled: {raw_prompt}"


class CountingAgentRuntime(FakeAgentRuntime):
    def __init__(self) -> None:
        self.calls = 0

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.calls += 1
        return super().handle_request(raw_prompt, context)


class BlockingFirstRequestRuntime(FakeAgentRuntime):
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self._lock = threading.RLock()

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        with self._lock:
            self.calls += 1
            call_number = self.calls
            self.prompts.append(raw_prompt)
        if call_number == 1:
            self.first_started.set()
            if not self.release_first.wait(3):
                raise TimeoutError("Timed out waiting to release the first request")
        return super().handle_request(raw_prompt, context)


class FailingAgentRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.calls += 1
        raise RuntimeError(f"boom: {raw_prompt}")


class FailingLlmRuntime:
    def __init__(self) -> None:
        self.calls = 0
        self.last_failure_summary: dict[str, Any] | None = None

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        _ = raw_prompt, context
        self.calls += 1
        raise LLMClientError(
            error_kind="transport_error",
            error_message="LLM request failed: connection refused",
        )


class FakeGenericEventOutputRuntime:
    """Runtime double that returns the generic terminal message but stores command output."""

    last_display_document: dict[str, Any] | None = None

    def __init__(
        self,
        stdout: str = " M app.py\n",
        final_response: str = "Task completed. Detailed output is available in the terminal or command output capsule.",
    ) -> None:
        self.stdout = stdout
        self.final_response = final_response
        self.last_context: dict[str, Any] = {}

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        _ = raw_prompt
        self.last_context = dict(context or {})
        store = InMemoryResultStore()
        store.put(
            "action_1",
            {
                "action_id": "action_1",
                "task_id": "task_1",
                "kind": "shell_command",
                "status": "success",
                "stdout": self.stdout,
                "stderr": "",
                "exit_code": 0,
                "output": None,
                "error": None,
                "metadata": {"operator_mode": True},
            },
            "shell_command",
            {"operator_mode": True, "status": "success"},
        )
        self.execution_engine = SimpleNamespace(result_store=store)
        return self.final_response


class FailingOperatorRecordRuntime(FakeAgentRuntime):
    """Runtime double that stores one failed operator record."""

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.last_context = dict(context or {})
        store = InMemoryResultStore()
        store.put(
            "action_1",
            {
                "action_id": "action_1",
                "task_id": "task_1",
                "kind": "shell_command",
                "status": "error",
                "stdout": "",
                "stderr": "x" * 5000 + "\nCONDA_HINT: Run 'conda init' before 'conda activate'\n",
                "exit_code": 1,
                "error": "Command failed.",
                "metadata": {"operator_mode": True},
            },
            "shell_command",
            {"operator_mode": True, "status": "error"},
        )
        self.execution_engine = SimpleNamespace(result_store=store)
        self.last_display_document = None
        return f"failed: {raw_prompt}"


class FakeLLMClient:
    model = "fake-model"
    temperature = 0.25

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {"ok": True, "schema": schema.get("title")}


def _event_llm_user_prompt(prompt: str) -> str:
    marker = "User prompt:\n"
    if marker not in prompt:
        return ""
    return prompt.split(marker, 1)[1].split("\nRequest context:", 1)[0].strip()


def _event_llm_draft(
    *,
    title: str,
    prompt: str,
    schedule_type: str = "interval",
    event_kind: str = "scheduled",
    interval_seconds: int = 3600,
    timezone: str = "UTC",
    next_run_at: str = "",
    schedule_summary: str = "",
    action_type: str = "agent_prompt",
    notification_message: str = "",
    context: dict[str, Any] | None = None,
    auto_approve_confirmations: bool = True,
) -> dict[str, Any]:
    return {
        "title": title,
        "prompt": prompt,
        "schedule_type": schedule_type,
        "event_kind": event_kind,
        "interval_seconds": interval_seconds,
        "timezone": timezone,
        "next_run_at": next_run_at,
        "schedule_summary": schedule_summary,
        "confidence": 0.9,
        "missing_details": [],
        "auto_approve_confirmations": auto_approve_confirmations,
        "context": dict(context or {}),
        "action_type": action_type,
        "notification_message": notification_message,
    }


def _event_llm_response(*drafts: dict[str, Any], rationale: str = "typed event fixture") -> dict[str, Any]:
    return {
        "is_schedule_request": bool(drafts),
        "drafts": list(drafts),
        "missing_details": [],
        "rationale": rationale,
    }


def _default_event_llm_response(user_prompt: str) -> dict[str, Any]:
    prompt = " ".join(str(user_prompt or "").split()).strip()
    if prompt == "check disk usage every hour":
        return _event_llm_response(
            _event_llm_draft(
                title="Check disk usage",
                prompt="check disk usage",
                interval_seconds=3600,
                schedule_summary="Every 1 hour",
                action_type="agent_prompt",
            )
        )
    if prompt == "check disk every hour and rotate logs every 6 hours":
        return _event_llm_response(
            _event_llm_draft(
                title="Check disk",
                prompt="check disk",
                interval_seconds=3600,
                schedule_summary="Every 1 hour",
                action_type="agent_prompt",
            ),
            _event_llm_draft(
                title="Rotate logs",
                prompt="rotate logs",
                interval_seconds=21600,
                schedule_summary="Every 6 hours",
                action_type="agent_prompt",
            ),
        )
    if prompt == "remind me to stretch after 15 mins":
        return _event_llm_response(
            _event_llm_draft(
                title="Stretch",
                prompt="stretch",
                schedule_type="once",
                interval_seconds=900,
                schedule_summary="After 15 minutes",
                action_type="notification",
                notification_message="stretch",
            )
        )
    if prompt in {"todo to stretch after 15 mins", "to stretch after 15 mins", "stretch after 15 mins"}:
        return _event_llm_response(
            _event_llm_draft(
                title="Stretch",
                prompt="stretch",
                schedule_type="once",
                interval_seconds=900,
                schedule_summary="After 15 minutes",
                action_type="notification",
                notification_message="stretch",
            )
        )
    if prompt == "Remind me on 6/01/2099 to pay Aegis insurance":
        return _event_llm_response(
            _event_llm_draft(
                title="Pay Aegis insurance",
                prompt="pay Aegis insurance",
                schedule_type="once",
                interval_seconds=60,
                timezone="UTC",
                next_run_at="2099-06-01T09:00:00+00:00",
                schedule_summary="On June 1, 2099 at 9:00 AM UTC",
                action_type="notification",
                notification_message="pay Aegis insurance",
            )
        )
    if prompt == "remind me to eat after 10 seconds":
        return _event_llm_response(
            _event_llm_draft(
                title="Eat",
                prompt="eat",
                schedule_type="once",
                interval_seconds=10,
                schedule_summary="After 10 seconds",
                action_type="notification",
                notification_message="eat",
            )
        )
    if prompt == "Remind me on 6/01/2099 at 5:30 pm to pay Aegis insurance":
        return _event_llm_response(
            _event_llm_draft(
                title="Pay Aegis insurance",
                prompt="pay Aegis insurance",
                schedule_type="once",
                interval_seconds=60,
                timezone="UTC",
                next_run_at="2099-06-01T17:30:00+00:00",
                schedule_summary="On June 1, 2099 at 5:30 PM UTC",
                action_type="notification",
                notification_message="pay Aegis insurance",
            )
        )
    if prompt == "Remind me MRI appointment on June 5th 10.00 AM 2099":
        return _event_llm_response(
            _event_llm_draft(
                title="MRI appointment",
                prompt="MRI appointment",
                schedule_type="once",
                interval_seconds=60,
                timezone="UTC",
                next_run_at="2099-06-05T10:00:00+00:00",
                schedule_summary="On June 5, 2099 at 10:00 AM UTC",
                action_type="notification",
                notification_message="MRI appointment",
            )
        )
    if prompt == "remind me on June 5th at 10.00 AM that I have an MRI appointment":
        return _event_llm_response(
            _event_llm_draft(
                title="MRI appointment",
                prompt="I have an MRI appointment",
                schedule_type="once",
                interval_seconds=60,
                timezone="UTC",
                next_run_at="2099-06-05T10:00:00+00:00",
                schedule_summary="On June 5, 2099 at 10:00 AM UTC",
                action_type="notification",
                notification_message="I have an MRI appointment",
            )
        )
    if prompt == "Rmeind me for Phyio therapy at June 9 2099 11 aM":
        return _event_llm_response(
            _event_llm_draft(
                title="Phyio therapy",
                prompt="Phyio therapy",
                schedule_type="once",
                interval_seconds=60,
                timezone="UTC",
                next_run_at="2099-06-09T11:00:00+00:00",
                schedule_summary="On June 9, 2099 at 11:00 AM UTC",
                action_type="notification",
                notification_message="Phyio therapy",
            )
        )
    if prompt in {
        "after 25 mins git stage and commit with msg \"x\"",
        "after 25 mins git stage and commit with msg \"x\" scheduled action",
    }:
        return _event_llm_response(
            _event_llm_draft(
                title="Git commit",
                prompt='git stage and commit with msg "x"',
                schedule_type="once",
                interval_seconds=1500,
                schedule_summary="After 25 minutes",
                action_type="agent_prompt",
            )
        )
    if prompt == "on 6/01/2099 check disk usage":
        return _event_llm_response(
            _event_llm_draft(
                title="Check disk usage",
                prompt="check disk usage",
                schedule_type="once",
                interval_seconds=60,
                timezone="UTC",
                next_run_at="2099-06-01T09:00:00+00:00",
                schedule_summary="On June 1, 2099 at 9:00 AM UTC",
                action_type="agent_prompt",
            )
        )
    if prompt == "in 10 seconds check disk usage":
        return _event_llm_response(
            _event_llm_draft(
                title="Check disk usage",
                prompt="check disk usage",
                schedule_type="once",
                interval_seconds=60,
                schedule_summary="After 10 seconds",
                action_type="agent_prompt",
            )
        )
    if prompt == "remind me every hour to drink water":
        return _event_llm_response(
            _event_llm_draft(
                title="Drink water",
                prompt="drink water",
                interval_seconds=3600,
                schedule_summary="Every 1 hour",
                action_type="notification",
                notification_message="drink water",
            )
        )
    if prompt in {"todo every hour to drink water", "every hour to drink water"}:
        return _event_llm_response(
            _event_llm_draft(
                title="Drink water",
                prompt="drink water",
                interval_seconds=3600,
                schedule_summary="Every 1 hour",
                action_type="notification",
                notification_message="drink water",
            )
        )
    if prompt in {"todo pay insurance", "pay insurance"}:
        return _event_llm_response(
            _event_llm_draft(
                title="Pay insurance",
                prompt="pay insurance",
                event_kind="todo",
                action_type="notification",
                notification_message="pay insurance",
            )
        )
    if prompt == "every hour remind me to drink a glass of water":
        return _event_llm_response(
            _event_llm_draft(
                title="Drink a glass of water",
                prompt="drink a glass of water",
                interval_seconds=3600,
                schedule_summary="Every 1 hour",
                action_type="notification",
                notification_message="drink a glass of water",
            )
        )
    if prompt == 'every 10 mins push all changes to origin typein "[redacted]" for ssh':
        return _event_llm_response(
            _event_llm_draft(
                title="Push all changes",
                prompt='push all changes to origin typein "[redacted]" for ssh',
                interval_seconds=600,
                schedule_summary="Every 10 minutes",
                action_type="agent_prompt",
            )
        )
    return {"is_schedule_request": False, "drafts": [], "missing_details": [], "rationale": "not an event"}


class EventRecognitionLLMClient(FakeLLMClient):
    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return _default_event_llm_response(_event_llm_user_prompt(prompt))


class FakeAdvisoryLLMClient(FakeLLMClient):
    def __init__(self) -> None:
        super().__init__()
        self.schemas: list[dict[str, Any]] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return {
            "answer_markdown": "Use the macOS clipboard tools from the terminal.",
            "snippets": [
                {
                    "snippet_id": "snippet_pbpaste",
                    "title": "Inspect the clipboard",
                    "language": "shell",
                    "code": "pbpaste | sed -n '1,20p'",
                    "runnable": True,
                    "explanation": "Shows the first clipboard lines.",
                    "cwd_note": "/Users/vinith/project",
                },
                {
                    "snippet_id": "snippet_notes",
                    "title": "Manual note",
                    "language": "text",
                    "code": "Confirm the app has the needed permissions.",
                    "runnable": False,
                    "explanation": "This is explanatory text, not a terminal command.",
                },
            ],
        }


class FakeAdvisoryRuntime:
    def __init__(self) -> None:
        self.llm_client = FakeAdvisoryLLMClient()
        self.handle_calls = 0

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.handle_calls += 1
        return f"should not run: {raw_prompt}"


class FakeSummaryLLMClient(FakeLLMClient):
    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {"summary": "Docker images need approval"}


class FakeStreamingLLMClient(FakeLLMClient):
    def complete_json_stream(self, prompt: str, schema: dict[str, Any], on_delta) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        self.prompts.append(prompt)
        on_delta('{"answer":"hel')
        on_delta('lo", "confidence": 0.9, "reason": "test"}')
        return {"answer": "hello", "confidence": 0.9, "reason": "test"}


class FakeNameLLMClient(FakeLLMClient):
    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {"name": "Coda"}


class FakeSequenceNameLLMClient(FakeLLMClient):
    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.names = list(names)

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {"name": self.names.pop(0) if self.names else "Coda"}


class FakeMemoryContextLLMClient(FakeLLMClient):
    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {
            "instruction": "Verify git staging with the exact requested postcondition.",
            "summary": "Git staging verification",
            "scope": "global",
            "model_name": "qwen-test",
            "model_family": "qwen-test",
            "task_type": "git",
            "tool_type": "shell",
            "intent_type": "verify_state",
            "tags": ["git", "staging"],
            "rationale": "The request and response are about staging git changes.",
            "confidence": 0.9,
        }


class FakeMemoryFeedbackLLMClient(FakeLLMClient):
    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        if schema.get("title") == "MemoryFeedbackDraftResponse":
            return {
                "feedback": (
                    "The agent made the wrong decision for git staging verification. "
                    "Next time, verify staged state directly."
                ),
                "rationale": "Draft feedback from the selected run outcome.",
            }
        return {
            "drafts": [
                {
                    "proposal_type": "create",
                    "memory_id": "",
                    "instruction": "When staging git changes, verify the staged postcondition directly.",
                    "summary": "Git staging verification",
                    "scope": "global",
                    "model_name": "",
                    "model_family": "",
                    "task_type": "git",
                    "tool_type": "shell",
                    "intent_type": "verify_state",
                    "tags": ["Git", "Staging"],
                    "rationale": "The feedback concerns git staging verification.",
                    "confidence": 0.9,
                }
            ],
            "rationale": "Create one targeted git memory.",
        }


class FakeValidationPolicyFeedbackLLMClient(FakeLLMClient):
    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return {
            "drafts": [
                {
                    "proposal_type": "create",
                    "memory_id": "",
                    "instruction": "Allow <iostream> only when it is literal C++ source inside a file-writing heredoc.",
                    "summary": "C++ include validation policy",
                    "memory_kind": "validation_policy",
                    "scope": "global",
                    "model_name": "",
                    "model_family": "",
                    "task_type": "cpp",
                    "tool_type": "shell_command",
                    "intent_type": "create_file",
                    "validator_error_type": "unresolved_shell_placeholder",
                    "safe_examples": ["#include <iostream> in a quoted heredoc that writes main.cpp"],
                    "blocked_examples": ["echo '<calculated_value>'"],
                    "tags": ["C++", "Literal payload"],
                    "rationale": "The feedback corrects validator behavior.",
                    "confidence": 0.9,
                }
            ],
            "rationale": "Create one targeted validation policy.",
        }


class FakeConfirmationRuntime:
    """Runtime double that pauses once, then replays from the saved trace on approval."""

    def __init__(self) -> None:
        self.last_failure_summary: dict[str, Any] | None = None
        self.last_planning_trace: dict[str, Any] | None = None
        self.last_display_document: dict[str, Any] | None = None
        self.replay_contexts: list[dict[str, Any]] = []

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.last_failure_summary = {"category": "confirmation_required"}
        self.last_planning_trace = {"raw_prompt": raw_prompt, "validated_dag": {"fake": True}}
        return "## Confirmation Required\n\nExecution requires confirmation before proceeding."

    def replay_from_trace(self, trace: dict[str, Any], context: dict | None = None) -> str:
        payload = dict(context or {})
        self.replay_contexts.append(payload)
        self.last_failure_summary = None
        self.last_planning_trace = trace
        self.last_display_document = None
        return f"approved: {trace['raw_prompt']}"


class OneConfirmationThenCountingRuntime(FakeConfirmationRuntime):
    """Pause the first chat request, then handle later fresh requests normally."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.prompts: list[str] = []

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.calls += 1
        self.prompts.append(raw_prompt)
        if self.calls == 1:
            return super().handle_request(raw_prompt, context)
        self.last_failure_summary = None
        self.last_planning_trace = None
        self.last_display_document = None
        return f"handled: {raw_prompt}"


class FakeTerminalConfirmationRuntime(FakeConfirmationRuntime):
    """Runtime double that marks its pending approval as terminal-capable."""

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.last_failure_summary = {"category": "confirmation_required"}
        self.last_planning_trace = SimpleNamespace(
            raw_prompt=raw_prompt,
            metadata={
                "operator_confirmation_actions": [
                    {
                        "node_id": "action_1",
                        "task_id": "Push changes",
                        "capability_id": "llm_operator.shell_command",
                        "operation_id": "shell_command",
                        "description": "Push changes to the remote.",
                        "requires_terminal_context": True,
                        "arguments": {
                            "kind": "shell_command",
                            "command": "git push origin HEAD",
                            "cwd": ".",
                            "risk": "high",
                            "interaction_mode": "may_prompt",
                            "requires_terminal_context": True,
                        },
                    }
                ]
            },
        )
        return "## Confirmation Required\n\nExecution requires confirmation before proceeding."

    def replay_from_trace(self, trace: Any, context: dict | None = None) -> str:
        payload = dict(context or {})
        self.replay_contexts.append(payload)
        self.last_failure_summary = None
        self.last_planning_trace = trace
        self.last_display_document = None
        raw_prompt = str(getattr(trace, "raw_prompt", "") or "")
        return f"approved: {raw_prompt}"


class FakeImplicitTerminalConfirmationRuntime(FakeConfirmationRuntime):
    """Runtime double whose approval action implies terminal use through interaction_mode."""

    def __init__(self, command: str = "git push origin HEAD") -> None:
        self.command = command
        self.last_failure_summary: dict[str, Any] | None = None
        self.last_planning_trace: Any = None
        self.last_display_document: dict[str, Any] | None = None
        self.replay_contexts: list[dict[str, Any]] = []

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        _ = context
        self.last_failure_summary = {"category": "confirmation_required"}
        self.last_planning_trace = SimpleNamespace(
            raw_prompt=raw_prompt,
            metadata={
                "operator_confirmation_actions": [
                    {
                        "node_id": "action_1",
                        "task_id": "Terminal command",
                        "capability_id": "llm_operator.shell_command",
                        "operation_id": "shell_command",
                        "description": "Run an interactive terminal-capable command.",
                        "arguments": {
                            "kind": "shell_command",
                            "command": self.command,
                            "cwd": ".",
                            "risk": "high",
                            "interaction_mode": "may_prompt",
                        },
                    }
                ]
            },
        )
        return "## Confirmation Required\n\nExecution requires confirmation before proceeding."

    def replay_from_trace(self, trace: Any, context: dict | None = None) -> str:
        payload = dict(context or {})
        self.replay_contexts.append(payload)
        self.last_failure_summary = None
        self.last_planning_trace = trace
        self.last_display_document = None
        raw_prompt = str(getattr(trace, "raw_prompt", "") or "")
        return f"approved: {raw_prompt}"


class FakeMultiConfirmationRuntime:
    """Runtime double that pauses again after the first confirmation approval."""

    def __init__(self) -> None:
        self.last_failure_summary: dict[str, Any] | None = None
        self.last_planning_trace: dict[str, Any] | None = None
        self.last_display_document: dict[str, Any] | None = None
        self.replay_contexts: list[dict[str, Any]] = []

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.last_failure_summary = {"category": "confirmation_required"}
        self.last_planning_trace = {"raw_prompt": raw_prompt, "validated_dag": {"stage": "stage"}}
        return "## Confirmation Required\n\nStage changes before continuing."

    def replay_from_trace(self, trace: dict[str, Any], context: dict | None = None) -> str:
        payload = dict(context or {})
        self.replay_contexts.append(payload)
        self.last_display_document = None
        if len(self.replay_contexts) == 1:
            self.last_failure_summary = {"category": "confirmation_required"}
            self.last_planning_trace = {
                "raw_prompt": trace["raw_prompt"],
                "validated_dag": {"stage": "commit"},
            }
            return "## Confirmation Required\n\nCommit staged changes before continuing."
        self.last_failure_summary = None
        self.last_planning_trace = trace
        return f"approved twice: {trace['raw_prompt']}"


class FakeContinuationRuntime:
    """Runtime double that exposes a failed operator plan and can continue it."""

    def __init__(
        self,
        agent_mode: str = "llm_operator",
        *,
        include_continuation_metadata: bool = True,
    ) -> None:
        self.agent_mode = agent_mode
        self.include_continuation_metadata = include_continuation_metadata
        self.last_failure_summary: dict[str, Any] | None = None
        self.last_planning_trace: Any = None
        self.last_display_document: dict[str, Any] | None = None
        self.continuation_contexts: list[dict[str, Any]] = []

    def _planning_trace(self, raw_prompt: str) -> SimpleNamespace:
        metadata = {
            "agent_mode": self.agent_mode,
            "operator_plan": {
                "summary": "Saved failed plan.",
                "tasks": [{"task_id": "task_1", "goal": "commit changes", "semantic_verb": "update"}],
                "actions": [
                    {
                        "action_id": "action_1",
                        "task_id": "task_1",
                        "kind": "shell_command",
                        "command": "git rev-parse --is-inside-work-tree",
                        "cwd": ".",
                        "execution_mode": "captured",
                        "interaction_mode": "non_interactive",
                        "declared_output_shape": "text",
                        "risk": "medium",
                        "reason": "Verify repository.",
                    },
                    {
                        "action_id": "action_2",
                        "task_id": "task_1",
                        "kind": "shell_command",
                        "command": "git commit -m bad",
                        "cwd": ".",
                        "execution_mode": "captured",
                        "interaction_mode": "non_interactive",
                        "declared_output_shape": "text",
                        "risk": "high",
                        "reason": "Commit changes.",
                    },
                ],
                "dependencies": [],
                "expected_outputs": ["changes committed"],
                "assumptions": [],
            },
            "operator_execution_records": [
                {
                    "action_id": "action_1",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "status": "success",
                    "stdout": "true\n",
                    "stderr": "",
                    "exit_code": 0,
                    "output": None,
                    "error": None,
                    "metadata": {},
                },
                {
                    "action_id": "action_2",
                    "task_id": "task_1",
                    "kind": "shell_command",
                    "status": "error",
                    "stdout": "",
                    "stderr": "bad quoting",
                    "exit_code": 1,
                    "output": None,
                    "error": "Command failed.",
                    "metadata": {},
                },
            ],
        }
        if self.include_continuation_metadata:
            metadata["operator_failure_continuation"] = {
                "resumable": True,
                "agent_mode": self.agent_mode,
                "successful_action_ids": ["action_1"],
                "failed_action_id": "action_2",
                "failed_action_kind": "shell_command",
                "failed_exit_code": 1,
                "failed_error": "Command failed.",
                "record_count": 2,
            }
        return SimpleNamespace(raw_prompt=raw_prompt, metadata=metadata)

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        _ = context
        self.last_planning_trace = self._planning_trace(raw_prompt)
        continuation = self.last_planning_trace.metadata.get(
            "operator_failure_continuation",
            {"resumable": True},
        )
        self.last_failure_summary = {
            "category": "runtime_error",
            "metadata": {"operator_failure_continuation": continuation},
        }
        return "failed after partial execution"

    def continue_from_trace(self, trace: Any, context: dict | None = None) -> str:
        self.continuation_contexts.append(dict(context or {}))
        self.last_planning_trace = trace
        self.last_failure_summary = None
        self.last_display_document = None
        return f"continued: {getattr(trace, 'raw_prompt', '')}"


class FakeClarificationRuntime:
    """Runtime double that pauses once, then resumes with clarification context."""

    def __init__(self) -> None:
        self.last_failure_summary: dict[str, Any] | None = None
        self.last_planning_trace: Any = None
        self.last_display_document: dict[str, Any] | None = None
        self.contexts: list[dict[str, Any]] = []

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        payload = dict(context or {})
        self.contexts.append(payload)
        clarifications = payload.get("clarifications")
        if isinstance(clarifications, list) and clarifications:
            self.last_failure_summary = None
            self.last_display_document = None
            return f"answered with {clarifications[-1]['answer']}: {raw_prompt}"
        request = {
            "question": "Which Python version should the environment use?",
            "reason": "The version changes the created environment.",
            "missing_information": "Python version",
            "options": [
                {"option_id": "py311", "label": "Python 3.11", "description": "Stable default"},
                {"option_id": "py312", "label": "Python 3.12", "description": "Newer runtime"},
                {"option_id": "py313", "label": "Python 3.13", "description": "Latest local line"},
            ],
            "allow_freeform": True,
            "confidence": 0.86,
        }
        self.last_failure_summary = {
            "category": "clarification_required",
            "metadata": {"clarification_request": request},
        }
        self.last_planning_trace = SimpleNamespace(metadata={"operator_clarification_request": request})
        self.last_display_document = None
        return "## Clarification Required\n\nWhich Python version should the environment use?"


class FakeCredentialClarificationRuntime(FakeClarificationRuntime):
    """Runtime double that asks for credential input once."""

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        payload = dict(context or {})
        self.contexts.append(payload)
        clarifications = payload.get("clarifications")
        if isinstance(clarifications, list) and clarifications:
            self.last_failure_summary = None
            self.last_display_document = None
            return f"credential answer {clarifications[-1]['answer']}: {raw_prompt}"
        request = {
            "question": "Which credential should I use for the SSH prompt?",
            "reason": "The terminal is asking for a private credential.",
            "missing_information": "SSH password or key credential",
            "options": [],
            "input_kind": "password",
            "parameter_choices": [
                {
                    "choice_id": "param:sshgit_key",
                    "key": "sshgit_key",
                    "normalized_key": "sshgit_key",
                    "label": "sshgit_key",
                    "description": "Git SSH password",
                    "aliases": ["git ssh key"],
                    "tags": ["git", "ssh"],
                    "sensitive": True,
                    "field_paths": ["password"],
                    "env_names": ["OF_PARAM_SSHGIT_KEY_PASSWORD"],
                    "exact": True,
                    "score": 1000,
                    "match_reasons": ["explicit_key"],
                    "group": "relevant",
                }
            ],
            "secret_input": True,
            "allow_freeform": True,
            "confidence": 0.9,
        }
        self.last_failure_summary = {
            "category": "clarification_required",
            "metadata": {"clarification_request": request},
        }
        self.last_planning_trace = SimpleNamespace(metadata={"operator_clarification_request": request})
        self.last_display_document = None
        return "## Clarification Required\n\nWhich credential should I use?"


class FakeSudoClarificationRuntime(FakeClarificationRuntime):
    """Runtime double that asks for sudo retry credentials."""

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        payload = dict(context or {})
        self.contexts.append(payload)
        clarifications = payload.get("clarifications")
        if isinstance(clarifications, list) and clarifications:
            self.last_failure_summary = None
            self.last_display_document = None
            return f"sudo answer {clarifications[-1]['answer']}: {raw_prompt}"
        request = {
            "question": (
                "This step previously failed and needs sudo to continue. "
                "Enter a sudo password or choose a saved credential to retry this exact command with sudo."
            ),
            "reason": "The failed command output indicates sudo is needed.",
            "missing_information": "Sudo password or saved credential to retry this command",
            "options": [
                {
                    "option_id": "retry_with_sudo",
                    "label": "Retry with sudo in terminal",
                    "description": "Run the same command with sudo.",
                },
                {
                    "option_id": "do_not_use_sudo",
                    "label": "Do not use sudo",
                    "description": "Continue without elevation.",
                },
            ],
            "input_kind": "password",
            "parameter_choices": [
                {
                    "choice_id": "param:local_sudo_pass",
                    "key": "local_sudo_pass",
                    "normalized_key": "local_sudo_pass",
                    "label": "local_sudo_pass",
                    "description": "Local sudo password",
                    "field_paths": ["password"],
                    "sensitive": True,
                    "group": "relevant",
                }
            ],
            "secret_input": True,
            "allow_freeform": True,
            "metadata": {
                "kind": "sudo_retry",
                "original_command": "dumpe2fs /dev/sdc2",
                "proposed_sudo_command": "sudo dumpe2fs /dev/sdc2",
            },
        }
        self.last_failure_summary = {
            "category": "clarification_required",
            "metadata": {"clarification_request": request},
        }
        self.last_planning_trace = SimpleNamespace(metadata={"operator_clarification_request": request})
        self.last_display_document = None
        return "## Clarification Required\n\nRetry this command with sudo?"


class FakeCommitMessageClarificationRuntime(FakeClarificationRuntime):
    """Runtime double with noisy credential metadata around a plain commit message gap."""

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        payload = dict(context or {})
        self.contexts.append(payload)
        clarifications = payload.get("clarifications")
        if isinstance(clarifications, list) and clarifications:
            self.last_failure_summary = None
            self.last_display_document = None
            return f"commit message answer {clarifications[-1]['answer']}: {raw_prompt}"
        request = {
            "question": "What Git commit message should I use before continuing?",
            "reason": "The request mentions ssh key sshgit_key, but the missing information is the commit message.",
            "missing_information": "Git commit message",
            "options": [],
            "input_kind": "password",
            "parameter_choices": [
                {
                    "choice_id": "param:sshgit_key",
                    "key": "sshgit_key",
                    "normalized_key": "sshgit_key",
                    "label": "sshgit_key",
                    "description": "Git SSH password",
                    "field_paths": ["password"],
                    "group": "relevant",
                }
            ],
            "secret_input": True,
            "allow_freeform": True,
            "confidence": 0.9,
        }
        self.last_failure_summary = {
            "category": "clarification_required",
            "metadata": {"clarification_request": request},
        }
        self.last_planning_trace = SimpleNamespace(metadata={"operator_clarification_request": request})
        self.last_display_document = None
        return "## Clarification Required\n\nWhat Git commit message should I use before continuing?"


class FakeOperatorLoopClarificationRuntime:
    """Runtime double that pauses from inside an operator loop, then replays."""

    def __init__(self) -> None:
        self.last_failure_summary: dict[str, Any] | None = None
        self.last_planning_trace: Any = None
        self.last_display_document: dict[str, Any] | None = None
        self.replay_contexts: list[dict[str, Any]] = []

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        request = {
            "question": "Which target should be repaired?",
            "reason": "The failed action needs user intent before repair.",
            "missing_information": "Target choice",
            "options": [
                {"option_id": "a", "label": "Target A", "description": "Repair A"},
                {"option_id": "b", "label": "Target B", "description": "Repair B"},
                {"option_id": "all", "label": "All targets", "description": "Repair all"},
            ],
            "allow_freeform": True,
            "confidence": 0.9,
        }
        self.last_failure_summary = {
            "category": "clarification_required",
            "metadata": {"clarification_request": request},
        }
        self.last_planning_trace = SimpleNamespace(
            raw_prompt=raw_prompt,
            metadata={
                "agent_mode": "llm_operator",
                "operator_plan": {
                    "summary": "Saved operator plan.",
                    "tasks": [],
                    "actions": [],
                    "dependencies": [],
                    "expected_outputs": [],
                    "assumptions": [],
                    "confidence": 0.8,
                },
                "operator_execution_records": [
                    {
                        "action_id": "action_1",
                        "task_id": "task_1",
                        "kind": "shell_command",
                        "status": "error",
                        "stdout": "",
                        "stderr": "ambiguous target",
                        "exit_code": 2,
                        "output": None,
                        "error": None,
                        "metadata": {},
                    }
                ],
                "operator_clarification_pending": True,
                "operator_clarification_phase": "execution_repair",
                "operator_clarification_request": request,
            },
        )
        self.last_display_document = None
        return "## Clarification Required\n\nWhich target should be repaired?"

    def replay_from_trace(self, trace: Any, context: dict | None = None) -> str:
        self.replay_contexts.append(dict(context or {}))
        self.last_failure_summary = None
        self.last_planning_trace = trace
        self.last_display_document = None
        return "resumed from saved operator state"


class FakeStreamingClarificationRuntime:
    """Runtime double that pauses before a streaming step plan, then replays streaming state."""

    def __init__(self) -> None:
        self.last_failure_summary: dict[str, Any] | None = None
        self.last_planning_trace: Any = None
        self.last_display_document: dict[str, Any] | None = None
        self.replay_contexts: list[dict[str, Any]] = []

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        request = {
            "question": "Which compose file should be used?",
            "reason": "The streaming step needs the selected target before launching services.",
            "missing_information": "Compose file",
            "options": [
                {
                    "option_id": "webui",
                    "label": "./webui/docker-compose.yml",
                    "description": "Use the webui compose file",
                }
            ],
            "allow_freeform": True,
            "confidence": 0.9,
        }
        self.last_failure_summary = {
            "category": "clarification_required",
            "metadata": {"clarification_request": request},
        }
        self.last_planning_trace = SimpleNamespace(
            raw_prompt=raw_prompt,
            metadata={
                "agent_mode": "standard_operator",
                "operator_execution_mode": "streaming",
                "operator_streaming_state": {
                    "current_index": 1,
                    "completed_task_ids": ["task_1"],
                    "prior_results": [
                        {
                            "task_id": "task_1",
                            "status": "success",
                            "records": [
                                {
                                    "action_id": "action_1",
                                    "task_id": "task_1",
                                    "kind": "shell_command",
                                    "status": "success",
                                    "stdout": "./webui/docker-compose.yml\n",
                                    "stderr": "",
                                    "exit_code": 0,
                                    "output": None,
                                    "error": None,
                                    "metadata": {},
                                }
                            ],
                        }
                    ],
                },
                "operator_clarification_pending": True,
                "operator_clarification_phase": "pre_planning",
                "operator_clarification_request": request,
            },
        )
        self.last_display_document = None
        return "## Clarification Required\n\nWhich compose file should be used?"

    def replay_from_trace(self, trace: Any, context: dict | None = None) -> str:
        self.replay_contexts.append(dict(context or {}))
        self.last_failure_summary = None
        self.last_planning_trace = trace
        self.last_display_document = None
        return "resumed streaming step"


class FakeStreamingFailureContinuationRuntime:
    """Runtime double with failed streaming-state records and no flat continuation plan."""

    def __init__(self) -> None:
        self.last_failure_summary: dict[str, Any] | None = None
        self.last_planning_trace: Any = None
        self.last_display_document: dict[str, Any] | None = None
        self.continuation_contexts: list[dict[str, Any]] = []

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        _ = context
        prior_success = {
            "action_id": "action_1",
            "task_id": "task_1",
            "kind": "shell_command",
            "status": "success",
            "stdout": "main\nfeature\n",
            "stderr": "",
            "exit_code": 0,
            "output": None,
            "error": None,
            "metadata": {
                "streaming_task_id": "task_1",
                "streaming_step_id": "stream-step-1-task-1",
                "streaming_step_index": 0,
            },
        }
        failed_attempt = {
            "action_id": "action_1",
            "task_id": "task_2",
            "kind": "python_action",
            "status": "error",
            "stdout": "",
            "stderr": "fatal: failed to stat 'main\\nfeature': File name too long",
            "exit_code": 1,
            "output": None,
            "error": "The prior branch list was used as one branch name.",
            "metadata": {
                "streaming_task_id": "task_2",
                "streaming_step_id": "stream-step-2-task-2",
                "streaming_step_index": 1,
            },
        }
        self.last_planning_trace = SimpleNamespace(
            raw_prompt=raw_prompt,
            metadata={
                "agent_mode": "llm_operator",
                "workflow_execution_mode": "streaming",
                "operator_streaming_state": {
                    "original_prompt": raw_prompt,
                    "agent_mode": "llm_operator",
                    "tasks": [
                        {
                            "task_id": "task_1",
                            "description": "List all git branches",
                            "semantic_verb": "list",
                            "object_type": "git.branch",
                            "dependencies": [],
                            "streaming_step_id": "stream-step-1-task-1",
                        },
                        {
                            "task_id": "task_2",
                            "description": "Extract commit times for each listed branch",
                            "semantic_verb": "read",
                            "object_type": "git.branch",
                            "dependencies": ["task_1"],
                            "streaming_step_id": "stream-step-2-task-2",
                        },
                    ],
                    "current_index": 1,
                    "completed_task_ids": ["task_1"],
                    "prior_results": [
                        {
                            "task_id": "task_1",
                            "description": "List all git branches",
                            "status": "success",
                            "final_response": "Branches listed.",
                            "records": [prior_success],
                        },
                        {
                            "task_id": "task_2",
                            "description": "Extract commit times for each listed branch",
                            "status": "error",
                            "final_response": "The branch list was consumed as one value.",
                            "records": [failed_attempt],
                        },
                    ],
                },
            },
        )
        self.last_failure_summary = {
            "category": "runtime_error",
            "metadata": {},
        }
        self.last_display_document = None
        return "streaming step failed"

    def continue_from_trace(self, trace: Any, context: dict | None = None) -> str:
        self.continuation_contexts.append(dict(context or {}))
        self.last_failure_summary = None
        self.last_planning_trace = trace
        self.last_display_document = None
        return "streaming continuation resumed"


class SlowAgentRuntime:
    """Runtime double that stays busy long enough for the stop route test."""

    last_display_document: dict[str, Any] | None = None

    def __init__(self) -> None:
        self.gateway_cancel_calls: list[dict[str, Any]] = []
        gateway = SimpleNamespace(
            cancel_raw_command=lambda **kwargs: self.gateway_cancel_calls.append(dict(kwargs))
            or {"cancelled": True}
        )
        self.execution_engine = SimpleNamespace(gateway_client=gateway)

    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        _ = raw_prompt, context
        time.sleep(1.0)
        return "late response"


class FakeMonitorGateway:
    def __init__(self, chunks: list[dict[str, Any]] | None = None) -> None:
        self.sessions: list[dict[str, Any]] = []
        self.commands: list[dict[str, Any]] = []
        self.cancellations: list[dict[str, Any]] = []
        self.chunks = chunks or [
            {"type": "stdout", "text": "ready 1024\n"},
            {"type": "completed", "exit_code": 0},
        ]

    def create_terminal_session(self, **kwargs: Any) -> dict[str, Any]:
        self.sessions.append(dict(kwargs))
        return {"session_id": kwargs.get("session_id"), "status": "created"}

    def stream_terminal_command(self, **kwargs: Any) -> Any:
        self.commands.append(dict(kwargs))
        for chunk in self.chunks:
            yield dict(chunk)

    def cancel_raw_command(self, **kwargs: Any) -> dict[str, Any]:
        self.cancellations.append(dict(kwargs))
        return {"cancelled": True}


class MonitorAgentRuntime(FakeAgentRuntime):
    def __init__(self, gateway: FakeMonitorGateway | None = None) -> None:
        self.gateway = gateway or FakeMonitorGateway()
        self.execution_engine = SimpleNamespace(gateway_client=self.gateway)


def _client() -> TestClient:
    return TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=FakeAgentRuntime(),
        )
    )


def _event_client(tmp_path, runtime: Any | None = None) -> TestClient:
    runtime = runtime or FakeAgentRuntime()
    if not hasattr(runtime, "llm_client"):
        runtime.llm_client = EventRecognitionLLMClient()
    return TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_events_db_path=tmp_path / "agent_events.db",
                agent_tasks_db_path=tmp_path / "agent_tasks.db",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
                agent_events_poll_seconds=60,
            ),
            agent_runtime=runtime,
        )
    )


def _task_client(tmp_path, runtime: Any | None = None, *, default_model: str = "") -> TestClient:
    return TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                default_model=default_model,
                agent_chats_db_path=tmp_path / "chats.db",
                agent_events_db_path=tmp_path / "agent_events.db",
                agent_tasks_db_path=tmp_path / "agent_tasks.db",
                agent_gateways_db_path=tmp_path / "agent_gateways.db",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
                agent_events_poll_seconds=60,
            ),
            agent_runtime=runtime or FakeAgentRuntime(),
        )
    )


def _capture_terminal_session_requests(monkeypatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __init__(self, body: dict[str, Any] | None = None) -> None:
            self._body = body

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            if self._body is not None:
                return json.dumps(self._body).encode("utf-8")
            payload = json.loads(captured["data"].decode("utf-8"))
            return json.dumps(
                {
                    "ok": True,
                    "session_id": payload["session_id"],
                    "cwd": payload["initial_cwd"],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        if str(request.full_url).endswith("/models"):
            return FakeHTTPResponse({"data": []})
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)
    return captured


def _monitor_client(tmp_path, runtime: Any | None = None) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_events_db_path=tmp_path / "agent_events.db",
                agent_tasks_db_path=tmp_path / "agent_tasks.db",
                agent_monitors_db_path=tmp_path / "agent_monitors.db",
                agent_gateways_db_path=tmp_path / "agent_gateways.db",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
                agent_events_poll_seconds=60,
            ),
            agent_runtime=runtime or FakeAgentRuntime(),
        )
    )


def _prompt_editor_client(tmp_path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_prompts_db_path=tmp_path / "prompts.db",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_gateways_db_path=tmp_path / "gateways.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_command_allowlist_db_path=tmp_path / "command_allowlist.db",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
                agent_memory_db_path=tmp_path / "agent_memory.db",
                agent_plan_cache_db_path=tmp_path / "agent_plan_cache.db",
                agent_computation_cache_db_path=tmp_path / "agent_computation_cache.db",
                agent_learning_ledger_db_path=tmp_path / "agent_learning_ledger.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )


def test_command_allowlist_store_persists_exact_commands(tmp_path) -> None:
    db_path = tmp_path / "command_allowlist.db"
    store = OperatorCommandAllowlistStore(db_path)

    entry = store.upsert("sudo apt update -y && sudo apt upgrade -y", block_reason="sudo")

    reopened = OperatorCommandAllowlistStore(db_path)
    entries = reopened.list(include_disabled=False)
    assert [item.entry_id for item in entries] == [entry.entry_id]
    assert reopened.enabled_hashes() == [
        operator_command_hash("sudo apt update -y && sudo apt upgrade -y")
    ]

    assert reopened.delete(entry.entry_id) is True
    assert reopened.enabled_hashes() == []


def test_agent_ui_command_allowlist_api_and_request_context(tmp_path) -> None:
    runtime = CountingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_command_allowlist_db_path=tmp_path / "agent_command_allowlist.db",
            ),
            agent_runtime=runtime,
        )
    )

    listed = client.get("/api/agent/command-allowlist")
    assert listed.status_code == 200
    assert listed.json()["entries"] == []

    created = client.post(
        "/api/agent/command-allowlist",
        json={"command": "sudo apt update -y && sudo apt upgrade -y", "reason": "sudo"},
    )
    assert created.status_code == 200
    entry = created.json()["entry"]
    expected_hash = operator_command_hash("sudo apt update -y && sudo apt upgrade -y")
    assert entry["command_hash"] == expected_hash

    submitted = client.post("/api/agent/request", json={"prompt": "list files"})
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    assert runtime.last_context["operator_command_allowlist_hashes"] == [expected_hash]

    deleted = client.delete(f"/api/agent/command-allowlist/{entry['entry_id']}")
    assert deleted.status_code == 200
    assert client.get("/api/agent/command-allowlist").json()["entries"] == []


def test_agent_ui_renders_command_exception_controls() -> None:
    client = _client()
    response = client.get("/agent-ui")
    assert response.status_code == 200
    html = response.text
    assert "Command Exceptions" in html
    assert 'id="command-exception-input"' in html
    assert 'id="command-exception-list"' in html
    script = client.get("/agent-ui/static/app.js?v=test")
    assert "/api/agent/command-allowlist" in script.text
    assert "Allow this action in the future" in script.text


def test_agent_ui_suppresses_inline_command_exception_for_sudo() -> None:
    client = _client()
    script = client.get("/agent-ui/static/app.js?v=test")

    assert 'if (/(^|[\\s;&|()])sudo(?:\\s|$)/i.test(normalizedCommand))' in script.text
    assert "continue;" in script.text


def test_directory_is_default_landing_and_lists_dynamic_routes() -> None:
    client = _client()

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "/directory"

    page = client.get("/directory")
    assert page.status_code == 200
    html = page.text
    assert "OpenFabric Directory" in html
    assert 'href="/agent-ui/static/directory.css?v=20260530-coherent-ui"' in html
    assert 'src="/agent-ui/static/agent_ui_shared.js?v=20260602-text-odometer"' in html
    assert 'src="/agent-ui/static/directory.js?v=20260531-website-service"' in html
    assert 'data-manual-doc="manual-home"' in html

    payload = client.get("/api/agent/directory").json()
    page_paths = {page["path"] for page in payload["pages"]}
    assert {
        "/directory",
        "/agent-ui",
        "/agent-ui-client",
        "/agent-ui-mobile",
        "/settings",
        "/prompt-editor",
        "/learning-ledger",
        "/website",
        "/manual",
        "/healthz",
    }.issubset(page_paths)
    client_page = next(page for page in payload["pages"] if page["path"] == "/agent-ui-client")
    assert client_page["title"] == "Agent UI Client"
    assert client_page["summary"] == "Client-facing chat and notification surface."
    assert payload["manual"]["port"] == "8013"
    assert payload["website"]["port"] == "8014"

    route_paths = {route["path"] for route in payload["routes"]}
    assert "/" in route_paths
    assert "/api/agent/directory" in route_paths
    assert "/api/agent/request" in route_paths
    assert "/sessions/{session_id}" in route_paths
    assert "/openapi.json" in route_paths
    assert "/agent-ui/static" not in route_paths

    group_names = {group["group"] for group in payload["route_groups"]}
    assert {"api", "compatibility", "openapi", "utility"}.issubset(group_names)

    manual_page = next(page for page in payload["pages"] if page["path"] == "/manual")
    assert manual_page["external"] is True
    assert any(link["doc_id"] == "generated-route-inventory" for link in manual_page["manual_links"])
    website_page = next(page for page in payload["pages"] if page["path"] == "/website")
    assert website_page["external"] is True
    assert website_page["title"] == "Website"

    directory_script = client.get("/agent-ui/static/directory.js?v=test")
    assert directory_script.status_code == 200
    assert 'websitePort: "8014"' in directory_script.text
    assert "function websiteUrl" in directory_script.text
    assert 'path === "/website"' in directory_script.text


def test_companion_pages_link_directory_and_relevant_manual_pages(tmp_path) -> None:
    client = _prompt_editor_client(tmp_path)

    agent_html = client.get("/agent-ui").text
    assert 'href="/directory"' in agent_html
    assert 'data-manual-doc="first-run-agent-ui"' in agent_html
    assert 'href="http://127.0.0.1:8013/manual#doc=first-run-agent-ui"' in agent_html

    settings_html = client.get("/settings").text
    assert 'href="/directory"' in settings_html
    assert 'data-manual-doc="configuration-storage"' in settings_html

    prompt_html = client.get("/prompt-editor").text
    assert 'href="/directory"' in prompt_html
    assert 'data-manual-doc="tasks-parameters-prompts-learning"' in prompt_html
    assert 'src="/agent-ui/static/agent_ui_shared.js?v=20260602-text-odometer"' in prompt_html

    ledger_html = client.get("/learning-ledger").text
    assert 'href="/directory"' in ledger_html
    assert 'data-manual-doc="capability-evolution-learning-ledger"' in ledger_html
    assert 'src="/agent-ui/static/agent_ui_shared.js?v=20260602-text-odometer"' in ledger_html

    mobile_html = client.get("/agent-ui-mobile").text
    assert 'href="/directory"' in mobile_html
    assert 'data-manual-doc="first-run-agent-ui"' in mobile_html


def test_agent_ui_renders_notification_center_assets() -> None:
    client = _client()

    html = client.get("/agent-ui").text
    shared = client.get("/agent-ui/static/agent_ui_shared.js?v=test").text
    script = client.get("/agent-ui/static/app.js?v=test").text
    css = client.get("/agent-ui/static/app.css?v=test").text

    assert 'id="notifications-toggle"' in html
    assert 'id="notification-overlay"' in html
    assert 'id="notifications-drawer"' in html
    assert "/api/agent/notifications" in script
    assert "function showNotification" in script
    assert "function notifyUiError" in shared
    assert "ui-error-toast-region" in shared
    assert "function showUiActionError" in script
    assert "localOnly: true" in script
    assert "markUiActionFieldInvalid(field)" in script
    assert "target.focus({ preventScroll: false })" in script
    assert "Gateway nickname required" in script
    assert "field: gatewayLabel" in script
    assert "Gateway host required" in script
    assert "field: gatewayHost" in script
    assert "Event prompt required" in script
    assert "field: eventPrompt || eventNotificationMessage" in script
    assert "Parameter key required" in script
    assert "field: parameterKeyInput" in script
    assert "Memory instruction required" in script
    assert "field: memoryInstruction" in script
    assert "Settings apply failed" in script
    assert "function loadNotifications" in script
    assert "function notificationRequestId" in script
    assert "function notificationTaskId" in script
    assert "function shouldSuppressLiveTaskNotificationDelivery" in script
    assert "function markSuppressedLiveTaskNotificationRead" in script
    assert "function notificationToastDedupKey" in script
    assert "notificationToastDedup" in script
    assert "updateNotificationDuplicateCount(existing.card, count)" in script
    assert "function notificationDetailChips" in script
    assert "function notificationRawMarkdownSections" in script
    assert "function notificationMarkdownDedupKey" in script
    assert "function notificationRawSectionPriority" in script
    assert "messageDuplicatedByRaw" in script
    assert "function appendNotificationMarkdownSection" in script
    assert "renderMarkdown(text, { skipStreamingCompletion: true })" in script
    assert "notification-card-raw-markdown" in script
    assert "Completed successfully" in script
    assert "Auto-approved" in script
    assert "function openNotificationRequest" in script
    assert "function renderTraceEventsFromTracePayload" in script
    assert "function showRequestLoadProgress" in script
    assert "function clearRequestLoadProgress" in script
    assert "Loading notification chat" in script
    assert "Loading notification trace" in script
    assert "Rendering notification chat..." in script
    assert "Rendering notification trace..." in script
    assert "renderTraceEvents: fullTrace" in script
    assert 'void openNotificationRequest(notification, null, { view: "trace" })' in script
    assert "Chat unavailable" in script
    assert "function notificationChatUnavailableMessage" in script
    assert "onTraceUnavailable" in script
    assert "Final answer" in script
    assert "Full trace" in script
    assert "notification-open-trace" in script
    assert "function playNotificationSound" in script
    assert "function showBrowserNotification" in script
    assert "function requestBrowserNotificationPermission" in script
    assert "browser_notifications_enabled" in script
    assert "notification_sound_variant" in script
    assert "notification_sound_volume" in script
    assert 'new window.Notification' in script
    assert "unlockNotificationAudio" in script
    assert 'id="setting-browser-notifications-enabled"' in html
    assert 'id="browser-notifications-permission-button"' in html
    assert 'id="setting-notification-sound-enabled"' in html
    assert 'id="setting-notification-sound-variant"' in html
    assert 'id="setting-notification-sound-volume"' in html
    assert 'id="notification-sound-test-button"' in html
    assert 'sourceType === "scheduled_event_notification"' in script
    assert 'sourceType === "scheduled_event_auto_approved"' in script
    assert ".notification-overlay" in css
    assert "body.immersive-mode .notification-overlay" in css
    assert ".notification-card" in css
    assert ".notification-duplicate-count" in css
    assert ".notification-card-markdown" in css
    assert ".notification-card-raw-markdown" in css
    assert ".notification-card-markdown-body .table-wrap" in css
    assert ".notification-card-details" in css
    assert ".notification-open-request" in css


def test_agent_ui_static_js_formats_api_error_payloads() -> None:
    client = _client()

    script = client.get("/agent-ui/static/app.js?v=test").text

    assert "function apiErrorMessageFromText" in script
    assert "function responseErrorMessage" in script
    assert "const reason = apiErrorMessageFromText(detail);" in script
    assert "message = apiErrorMessageFromText(await response.text());" in script
    assert 'addMessage("assistant", "Confirmation error", message)' in script
    assert 'addMessage("assistant", "Clarification error", message)' in script
    assert 'addMessage("assistant", "Confirmation error", await response.text())' not in script
    assert 'addMessage("assistant", "Clarification error", await response.text())' not in script


def test_agent_ui_static_backend_connection_indicator() -> None:
    client = _client()

    html = client.get("/agent-ui").text
    script = client.get("/agent-ui/static/app.js?v=test").text
    css = client.get("/agent-ui/static/app.css?v=test").text

    assert 'id="backend-connection-status"' in html
    assert 'id="immersive-backend-connection-status"' in html
    assert 'backend-connection-icon-plug' in html
    assert 'backend-connection-icon-disconnect' in html
    assert "function setBackendConnectionStatus" in script
    assert "function startBackendConnectionPolling" in script
    assert 'document.querySelectorAll(".backend-connection-status")' in script
    assert "/api/agent/health?connection_check=" in script
    assert 'setBackendConnectionStatus("connected")' in script
    assert 'setBackendConnectionStatus("disconnected"' in script
    assert '.backend-connection-status[data-state="connected"]' in css
    assert '.backend-connection-status[data-state="disconnected"]' in css
    assert ".immersive-backend-connection-status" in css
    assert 'class="immersive-header-controls"' in html
    assert ":not(.immersive-header-controls)" in css
    assert "body.immersive-mode .quick-controls-toggle" in css


def test_agent_ui_static_uses_generic_clarification_rendering() -> None:
    client = _client()

    script = client.get("/agent-ui/static/app.js?v=test").text
    css = client.get("/agent-ui/static/app.css?v=test").text

    assert "generatedCommitMessageApprovalText" not in script
    assert "safe.metadata?.generated_commit_message" not in script
    assert "generatedCommitMessageApprovalHeading" not in script
    assert "safe.metadata?.generated_commit_message_heading" not in script
    assert "optionButton.textContent = labelText" in script
    assert "optionButton.append(optionDescription)" not in script
    assert ".clarification-preformatted" in css
    assert "white-space: pre-wrap" in css


def test_agent_ui_static_llm_indicator_marks_unreachable() -> None:
    client = _client()

    script = client.get("/agent-ui/static/app.js?v=test").text
    css = client.get("/agent-ui/static/app.css?v=test").text

    assert "state.llmReachable = active.available !== false;" in script
    assert 'state.llmReachable = false;' in script
    assert 'modelStatus.textContent = "LLM service unreachable";' in script
    assert 'modelStatus.dataset.reachable = String(!unreachable);' in script
    assert 'const immersiveModelStatus = document.querySelector("#immersive-model-status")' in script
    assert 'syncBadgeMirror(modelStatus, immersiveModelStatus, "immersive-model-status")' in script
    assert 'llmActivityIndicator.classList.toggle("unreachable", unreachable)' in script
    assert 'llmActivityIndicator.dataset.reachable = String(!unreachable);' in script
    assert 'const immersiveLlmActivityIndicator = document.querySelector("#immersive-llm-activity-indicator")' in script
    assert 'syncBadgeMirror(llmActivityIndicator, immersiveLlmActivityIndicator, "immersive-llm-activity-indicator")' in script
    assert 'const immersiveLearningRuntimeIndicator = document.querySelector("#immersive-learning-runtime-indicator")' in script
    assert '"immersive-learning-runtime-indicator"' in script
    assert "LLM service unreachable" in script
    assert ".llm-activity-indicator.unreachable" in css
    assert '.llm-activity-indicator[data-reachable="false"]' in css
    assert '.model-status[data-reachable="false"]' in css
    assert "text-decoration-line: line-through" in css


def test_agent_ui_renders_prompt_slash_macro_palette() -> None:
    client = _client()

    html = client.get("/agent-ui").text
    script = client.get("/agent-ui/static/app.js?v=test").text
    css = client.get("/agent-ui/static/app.css?v=test").text
    registry = client.get("/api/agent/prompt-macros").json()

    assert 'id="prompt-macro-menu"' in html
    assert 'role="listbox"' in html
    assert 'aria-controls="prompt-macro-menu"' in html
    assert "let promptMacroRegistry = []" in script
    assert 'fetch("/api/agent/prompt-macros"' in script
    assert "function loadPromptMacroRegistry" in script
    assert "function normalizePromptMacroRegistry" in script
    assert "function promptMacroSyntaxMask" in script
    assert "mask[slashIndex]" in script
    assert "function promptParameterShortcutTriggerAtCursor" in script
    assert "function promptShortcutTriggerAtCursor" in script
    assert 'fetch("/api/agent/parameters?limit=1000"' in script
    assert "promptParameterShortcutMatches" in script
    assert "insertPromptShortcutOption" in script
    assert "promptInput.value = value.slice(0, trigger.start) + key + value.slice(trigger.end)" in script
    assert "function updatePromptMacroMenuFromInput" in script
    assert 'event?.inputType === "insertFromPaste"' in script
    assert registry["macros"][0]["id"] == "typein"
    assert registry["macros"][0]["template"] == '/typein "<your text>"'
    assert registry["macros"][0]["placeholder"] == "<your text>"
    assert any(item["id"] == "checkonline" and item["template"] == "/checkonline" for item in registry["macros"])
    assert any(
        item["id"] == "checkonlineai" and item["template"] == '/checkonlineai "<your search query>"'
        for item in registry["macros"]
    )
    assert any(item["id"] == "autoapprove" and item["template"] == "/autoapprove" for item in registry["macros"])
    assert any(item["id"] == "addtomemory" and item["template"] == "/addtomemory" for item in registry["macros"])
    assert any(item["id"] == "runlater" and item["template"] == "/runlater" for item in registry["macros"])
    assert any(item["id"] == "remind" and item["template"] == "/remind" for item in registry["macros"])
    assert any(item["id"] == "todo" and item["template"] == "/todo" for item in registry["macros"])
    assert any(item["id"] == "restart" and item["template"] == "/restart" for item in registry["macros"])
    assert "promptInput.selectionStart" in script
    assert "promptInput.selectionEnd" in script
    assert "promptInput.setSelectionRange(selectionStart, selectionEnd)" in script
    assert "promptMacroSelectionArmed" in script
    assert 'event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)' in script
    assert 'event.key === "Escape"' in script
    assert "function insertPromptMacro" in script
    assert ".prompt-macro-menu" in css
    assert ".prompt-macro-option.active" in css
    assert '.prompt-macro-option[data-shortcut-kind="parameter"]' in css


def test_prompt_editor_page_serves_themed_assets(tmp_path) -> None:
    client = _prompt_editor_client(tmp_path)

    response = client.get("/prompt-editor")

    assert response.status_code == 200
    html = response.text
    assert "OpenFabric Prompt Editor" in html
    assert '<html lang="en" class="app-booting">' in html
    assert 'id="app-boot-screen"' in html
    assert "Loading editor" in html
    assert 'href="/agent-ui/static/app.css?v=20260606-prompt-gutters"' in html
    assert 'href="/agent-ui/static/prompt_editor.css?v=20260530-coherent-ui"' in html
    assert 'src="/agent-ui/static/prompt_editor.js?v=20260527-list-pills-v11"' in html
    assert 'id="prompts-mode-button"' in html
    assert 'id="memories-mode-button"' in html
    assert 'id="parameters-mode-button"' in html
    assert 'id="new-memory-button"' in html
    assert 'id="new-parameter-button"' in html
    assert 'id="memory-fields"' in html
    assert 'id="parameter-fields"' in html
    assert 'id="parameter-value-json"' in html
    assert 'id="parameter-context-json"' in html
    assert "How Parameter Context Is Used" in html
    assert "bounded summary and prompt guidance" in html
    assert "do not become DB-approved foreign keys" in html
    assert 'id="template-search"' in html
    assert 'id="template-body"' in html
    assert 'id="save-button"' in html
    assert 'id="reset-button"' in html
    assert 'id="render-button"' in html
    assert 'class="inspector-panel default-panel"' in html
    assert 'class="prompt-editor-footer"' in html
    assert 'id="prompt-editor-footer-version"' in html
    assert 'id="prompt-editor-footer-runtime"' in html
    assert 'id="prompt-editor-footer-git"' in html
    assert 'id="prompt-editor-footer-count"' in html
    assert '<option value="aurora">Aurora</option>' in html
    script = client.get("/agent-ui/static/prompt_editor.js?v=test").text
    css = client.get("/agent-ui/static/prompt_editor.css?v=test").text
    assert "/api/agent/prompt-editor" in script
    assert "const Core = window.OpenFabricAgentUi" in script
    assert "function notifyUiError" in script
    assert "function saveTemplate" in script
    assert "Prompt save failed" in script
    assert "async function saveMemory" in script
    assert "Memory instruction required" in script
    assert "async function loadMemories" in script
    assert "function createNewMemory" in script
    assert "async function loadParameters" in script
    assert "async function saveParameter" in script
    assert "Parameter key required" in script
    assert "function buildParameterContextFromFields" in script
    assert 'fetch(url, { ...options, headers, cache: "no-store" })' in script
    assert "function resetTemplate" in script
    assert "function renderTemplate" in script
    assert "async function loadEditorVersion" in script
    assert 'requestJson("/api/agent/version")' in script
    assert "Promise.allSettled([templatesStartup, versionStartup]).finally(finishAppBoot)" in script
    assert "function finishAppBoot" in script
    assert "openfabric.agentUi.theme" not in script
    assert "setEditorMode(\"memories\")" in script
    assert "setEditorMode(\"parameters\")" in script
    assert 'window.location.pathname.includes("parameter-editor")' in script
    assert '`${apiBase}/memories`' in script
    assert '`${apiBase}/memories/${encodeURIComponent(state.selectedMemoryId)}`' in script

    parameter_response = client.get("/parameter-editor?key=canonical_v1")
    assert parameter_response.status_code == 200
    assert 'id="parameters-mode-button"' in parameter_response.text
    assert 'createTemplatePill(templateScope(template), "scope")' in script
    assert '`state ${template.edited ? "edited" : "default"}`' in script
    assert 'createTemplatePill(`v${template.version || 1}`, "version")' in script
    assert ".prompt-editor-layout" in css
    assert ".editor-resource-tabs" in css
    assert ".memory-fields" in css
    assert ".parameter-section-hint code" in css
    assert "--prompt-editor-footer-height: 34px" in css
    assert "grid-template-rows: auto minmax(0, 1fr) auto" in css
    assert "grid-template-columns: minmax(340px, 420px) minmax(420px, 1fr) minmax(300px, 380px)" in css
    assert "grid-template-rows: minmax(0, 1fr)" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert "grid-template-rows: auto minmax(24px, auto)" in css
    assert "align-items: stretch" in css
    assert "align-self: stretch" in css
    assert "flex-direction: column" in css
    assert "height: calc(100dvh - var(--prompt-editor-topbar-height) - var(--prompt-editor-footer-height))" in css
    assert "flex: 1 1 100%" in css
    assert "var(--prompt-editor-footer-height) - 180px" in css
    assert "grid-template-rows: minmax(0, 1fr) minmax(180px, 34%)" in css
    assert ".template-body:disabled" in css
    assert "background: rgba(0, 0, 0, 0.18)" in css
    assert "cursor: not-allowed" in css
    assert ".default-body,\n.render-output" in css
    assert "height: 100%" in css
    assert ".default-panel,\n.render-panel" in css
    assert "align-self: stretch" in css
    assert "justify-self: stretch" in css
    assert "block-size: 100%" in css
    assert ".prompt-editor-footer" in css
    assert 'body[data-editor-mode="memories"] .template-body' in css
    assert ".template-row-meta .template-pill.state" in css
    assert ".template-row-meta .template-pill.version" in css
    assert "text-overflow: ellipsis" in css
    assert "var(--accent)" in css


def test_mission_control_settings_page_serves_registry_assets(tmp_path) -> None:
    client = _prompt_editor_client(tmp_path)

    response = client.get("/settings")

    assert response.status_code == 200
    html = response.text
    assert "OpenFabric Settings" in html
    assert "Mission Control" in html
    assert 'id="settings-search"' in html
    assert 'id="settings-optimization-control"' in html
    assert 'id="settings-optimization-heat"' in html
    assert "Optimize agent for" in html
    assert ">Accuracy</button>" in html
    assert ">Custom</button>" in html
    assert ">Speed</button>" in html
    assert 'id="settings-presets"' in html
    assert 'id="settings-section-nav"' in html
    assert 'id="settings-sections"' in html
    assert 'id="settings-sync-status"' in html
    assert 'href="/agent-ui/static/app.css?v=20260606-prompt-gutters"' in html
    assert 'href="/agent-ui/static/settings.css?v=20260530-coherent-ui"' in html
    assert 'src="/agent-ui/static/agent_ui_shared.js?v=20260602-text-odometer"' in html
    assert 'src="/agent-ui/static/settings.js?v=20260527-clarification-heat"' in html
    assert 'id="settings-clarification-control"' in html
    assert 'id="settings-clarification-heat"' in html
    assert "50% Auto-pilot" in html
    assert html.index("agent_ui_shared.js") < html.index("settings.js")
    assert 'id="settings-drawer"' not in html

    css = client.get("/agent-ui/static/settings.css?v=test")
    script = client.get("/agent-ui/static/settings.js?v=test")

    assert css.status_code == 200
    assert script.status_code == 200
    assert ".mission-settings-layout" in css.text
    assert ".mission-settings-section" in css.text
    assert ".mission-optimization-control" in css.text
    assert ".mission-optimization-heat" in css.text
    assert ".mission-settings-clarification" in css.text
    assert "MISSION_CONTROL_PRESETS" in script.text
    assert "MISSION_CONTROL_OPTIMIZATION_PROFILES" in script.text
    assert "function applyOptimizationProfile" in script.text
    assert "function currentOptimizationProfile" in script.text
    assert "function renderClarificationControl" in script.text
    assert "settings-clarification-control" in script.text
    assert 'apiJson("/settings/registry"' in script.text
    assert 'apiJson("/runtime-controls"' in script.text
    assert "writeSettingsBackendPreferences" in script.text
    assert "scheduleAutosave" in script.text
    assert "requires_confirmation" in script.text
    assert "mission-readonly-value" in script.text
    assert "settingsDrawer" not in script.text
    assert "function notifyUiError" in script.text
    assert "Settings autosave failed" in script.text
    assert "Core?.notifyUiError" in script.text


def test_agent_settings_registry_drives_config_and_mission_control(tmp_path) -> None:
    client = _prompt_editor_client(tmp_path)

    registry_response = client.get("/api/agent/settings/registry")
    config_response = client.get("/api/agent/settings/config")

    assert registry_response.status_code == 200
    assert config_response.status_code == 200
    registry = registry_response.json()
    config = config_response.json()
    assert registry["version"] == "20260602-text-odometer"
    assert config == {
        "defaults": registry["defaults"],
        "limits": registry["limits"],
    }
    assert set(registry["runtime_control_keys"]) == set(PUBLIC_RUNTIME_CONTROL_KEYS)
    assert {"agent_behavior", "runtime_controls", "ui_preferences"} == {
        section["id"] for section in registry["sections"]
    }
    assert set(registry["optimization_profiles"]) == {"accuracy", "speed"}
    assert registry["presets"] == []

    removed_keys = {
        "operator_execution_mode",
        "operator_effect_policy_mode",
        "llm_response_streaming_enabled",
        "guided_deliberation_mode",
        "operator_auto_rephrase_retry_enabled",
        "backend_persistence_enabled",
    }
    assert removed_keys.isdisjoint(registry["runtime_control_keys"])
    assert removed_keys.isdisjoint(registry["optimization_controlled_keys"])
    assert removed_keys.isdisjoint(registry["defaults"])
    for profile in registry["optimization_profiles"].values():
        assert removed_keys.isdisjoint(profile["values"])

    settings_by_key = {
        setting["key"]: setting
        for section in registry["sections"]
        for setting in section["settings"]
    }
    assert set(registry["runtime_control_keys"]) <= set(settings_by_key)
    assert {
        "operator_policy_profile",
        "reasoning_profile",
        "repair_profile",
        "workflow_execution_mode",
        "prompt_rephrase_enabled",
        "response_streaming_enabled",
        "agent_clarification_mode",
        "ui_theme",
        "ui_chat_pop_animation",
        "ui_thinking_text_animation",
        "agent_display_name",
        "lrnt_similarity_threshold",
    } <= set(settings_by_key)
    assert settings_by_key["operator_policy_profile"]["target"] == "runtime"
    assert settings_by_key["workflow_execution_mode"]["target"] == "runtime"
    assert settings_by_key["prompt_rephrase_enabled"]["target"] == "runtime"
    assert settings_by_key["prompt_rephrase_enabled"]["label"] == "Rephrase prompts"
    assert settings_by_key["response_streaming_enabled"]["target"] == "runtime"
    assert settings_by_key["lrnt_similarity_threshold"]["target"] == "runtime"
    assert settings_by_key["lrnt_similarity_threshold"]["label"] == "LR-T threshold"
    assert settings_by_key["ui_theme"]["target"] == "ui"
    assert settings_by_key["ui_chat_pop_animation"]["target"] == "ui"
    assert settings_by_key["ui_thinking_text_animation"]["target"] == "ui"
    assert {option["value"] for option in settings_by_key["ui_chat_pop_animation"]["options"]} == {
        "soft-rise",
        "slide-up",
        "slide-side",
        "scale-pop",
        "spring",
        "flip",
        "skew-snap",
        "blur-glow",
        "drop-in",
        "stream-roll",
        "odometer",
        "none",
    }
    assert {option["value"] for option in settings_by_key["ui_thinking_text_animation"]["options"]} == {
        "roll-up",
        "soft-rise",
        "slide-left",
        "snap-down",
        "fade",
        "pop",
        "flip",
        "blur",
        "skew",
        "bounce",
        "swing",
        "odometer",
        "none",
    }
    assert registry["defaults"]["workflow_execution_mode"] == "streaming"
    assert registry["defaults"]["operator_policy_profile"] == "assisted"
    assert registry["defaults"]["reasoning_profile"] == "balanced"
    assert registry["defaults"]["repair_profile"] == "balanced"
    assert registry["optimization_profiles"]["accuracy"]["values"]["operator_policy_profile"] == "assisted"
    assert registry["optimization_profiles"]["accuracy"]["values"]["reasoning_profile"] == "deep"
    assert registry["optimization_profiles"]["speed"]["values"]["workflow_execution_mode"] == "streaming"
    assert registry["optimization_profiles"]["speed"]["values"]["repair_profile"] == "conservative"
    assert "llm_endpoint_port" not in registry["limits"]


def test_learning_ledger_page_serves_assets_and_api(tmp_path) -> None:
    client = _prompt_editor_client(tmp_path)
    ledger = AgentLearningLedgerStore(tmp_path / "agent_learning_ledger.db")
    lesson = ledger.create_lesson(
        LearningLessonWrite(
            title="Use provided literals",
            instruction="When the user provides a literal value, preserve it exactly.",
            summary="Preserve literal user input.",
            source_request_id="req-learning",
            tags=["literal"],
        )
    )
    assert lesson is not None
    proposal = ledger.create_proposal(
        CapabilityProposalWrite(
            target_kind="capability_manifest_overlay",
            title="Add planner hint",
            summary="Recognize literal-preservation requests.",
            confidence=0.74,
            target_id="operator.shell_command",
            draft={
                "capability_id": "operator.shell_command",
                "overlay": {"semantic_tags": ["literal-preservation"]},
            },
            dedupe_key="ui-proposal",
        )
    )
    assert proposal is not None

    response = client.get("/learning-ledger")

    assert response.status_code == 200
    html = response.text
    assert "OpenFabric Learning Ledger" in html
    assert 'href="/agent-ui/static/learning_ledger.css?v=20260530-coherent-ui"' in html
    assert 'src="/agent-ui/static/learning_ledger.js?v=20260521-learning-ledger"' in html
    assert 'id="lesson-approve"' in html
    assert 'id="lesson-digest-note"' in html
    assert 'id="proposal-list"' in html
    assert 'id="proposal-apply"' in html
    script = client.get("/agent-ui/static/learning_ledger.js?v=test").text
    css = client.get("/agent-ui/static/learning_ledger.css?v=test").text
    assert "/api/agent/learning-ledger/summary" in script
    assert "/api/agent/learning-ledger/lessons" in script
    assert "/api/agent/learning-ledger/proposals" in script
    assert "/api/agent/learning-ledger/runs" in script
    assert "function notifyUiError" in script
    assert "No lesson selected" in script
    assert "Lesson note required" in script
    assert "Proposal apply failed" in script
    assert "Digest Note" in html
    assert ".learning-ledger-layout" in css
    assert ".compact-list" in css
    assert "auto-learned" in script

    listed = client.get("/api/agent/learning-ledger/lessons")
    assert listed.status_code == 200
    assert listed.json()["lessons"][0]["lesson_id"] == lesson.lesson_id
    listed_proposals = client.get("/api/agent/learning-ledger/proposals")
    assert listed_proposals.status_code == 200
    assert listed_proposals.json()["proposals"][0]["proposal_id"] == proposal.proposal_id

    approved = client.post(f"/api/agent/learning-ledger/lessons/{lesson.lesson_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["lesson"]["status"] == "approved"
    memories = client.get("/api/agent/prompt-editor/memories?tag=learning-ledger")
    assert memories.status_code == 200
    assert memories.json()["count"] == 1

    cleared = client.post("/api/agent/learning-ledger/clear")

    assert cleared.status_code == 200
    assert cleared.json()["cleared"] is True
    assert cleared.json()["summary"]["approved_lessons"] == 0
    assert client.get("/api/agent/learning-ledger/lessons").json()["lessons"] == []
    assert client.get("/api/agent/learning-ledger/proposals").json()["proposals"] == []


def test_learning_ledger_auto_learn_approves_safe_run_lessons(tmp_path) -> None:
    class CorrectedCommandRuntime(FakeAgentRuntime):
        def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
            self.last_context = dict(context or {})
            self.last_display_document = None
            self.last_planning_trace = SimpleNamespace(
                metadata={
                    "agent_mode": "llm_operator",
                    "operator_execution_records": [
                        {
                            "action_id": "action_1",
                            "task_id": "task_1",
                            "kind": "shell_command",
                            "status": "error",
                            "command": "bad command",
                            "stderr": "unknown option",
                            "exit_code": 1,
                        },
                        {
                            "action_id": "action_2",
                            "task_id": "task_1",
                            "kind": "shell_command",
                            "status": "success",
                            "command": "good command",
                            "stdout": "ok",
                            "exit_code": 0,
                        },
                    ],
                }
            )
            return f"handled: {raw_prompt}"

    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_learning_ledger_db_path=tmp_path / "agent_learning_ledger.db",
                agent_memory_db_path=tmp_path / "agent_memory.db",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
                agent_learning_ledger_auto_learn_enabled=True,
            ),
            agent_runtime=CorrectedCommandRuntime(),
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "recover from the command failure", "agent_mode": "llm_operator"},
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    lessons = client.get("/api/agent/learning-ledger/lessons")
    for _ in range(20):
        if lessons.json().get("lessons"):
            break
        time.sleep(0.05)
        lessons = client.get("/api/agent/learning-ledger/lessons")
    trace = client.get(submitted.json()["trace_url"]).json()
    memories = client.get("/api/agent/prompt-editor/memories?tag=learning-ledger")
    summary = client.get("/api/agent/learning-ledger/summary")

    assert lessons.status_code == 200
    lesson = lessons.json()["lessons"][0]
    assert lesson["status"] == "approved"
    assert lesson["auto_approved"] is True
    assert memories.json()["count"] == 1
    assert summary.json()["auto_learn_enabled"] is True
    assert summary.json()["summary"]["auto_approved_lessons"] == 1
    assert any(
        event["event_type"] == "learning.lesson.learned"
        for event in trace["events"]
    )


def test_learning_ledger_auto_learn_can_be_disabled(tmp_path) -> None:
    class CorrectedCommandRuntime(FakeAgentRuntime):
        def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
            self.last_context = dict(context or {})
            self.last_display_document = None
            self.last_planning_trace = SimpleNamespace(
                metadata={
                    "operator_execution_records": [
                        {
                            "action_id": "action_1",
                            "task_id": "task_1",
                            "kind": "shell_command",
                            "status": "error",
                            "command": "bad command",
                            "stderr": "unknown option",
                            "exit_code": 1,
                        },
                        {
                            "action_id": "action_2",
                            "task_id": "task_1",
                            "kind": "shell_command",
                            "status": "success",
                            "command": "good command",
                            "stdout": "ok",
                            "exit_code": 0,
                        },
                    ],
                }
            )
            return "handled"

    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_learning_ledger_auto_learn_enabled=False,
                agent_learning_ledger_db_path=tmp_path / "agent_learning_ledger.db",
                agent_memory_db_path=tmp_path / "agent_memory.db",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
            ),
            agent_runtime=CorrectedCommandRuntime(),
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "fix command"})
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    lessons = client.get("/api/agent/learning-ledger/lessons")
    for _ in range(20):
        if lessons.json().get("lessons"):
            break
        time.sleep(0.05)
        lessons = client.get("/api/agent/learning-ledger/lessons")
    lesson = lessons.json()["lessons"][0]
    assert lesson["status"] == "draft"
    assert lesson["auto_approved"] is False
    assert client.get("/api/agent/prompt-editor/memories?tag=learning-ledger").json()["count"] == 0


def test_prompt_editor_api_updates_renders_and_resets_templates(tmp_path) -> None:
    client = _prompt_editor_client(tmp_path)

    listed = client.get("/api/agent/prompt-editor/templates")
    assert listed.status_code == 200
    list_payload = listed.json()
    assert list_payload["count"] > 0
    assert list_payload["db_path"].endswith("prompts.db")
    assert any(item["prompt_key"] == "direct_answer" for item in list_payload["templates"])

    detail = client.get("/api/agent/prompt-editor/templates/direct_answer")
    assert detail.status_code == 200
    template = detail.json()["template"]
    original_body = template["body"]
    original_version = template["version"]
    assert template["has_default"] is True
    assert template["edited"] is False

    updated = client.patch(
        "/api/agent/prompt-editor/templates/direct_answer",
        json={"body": f"{original_body}\nEditor sentinel for ${{name}}."},
    )
    assert updated.status_code == 200
    updated_template = updated.json()["template"]
    assert updated_template["version"] == original_version + 1
    assert updated_template["edited"] is True
    assert "name" in updated_template["variables"]

    missing_variable = client.post(
        "/api/agent/prompt-editor/templates/direct_answer/render",
        json={"variables": {}},
    )
    assert missing_variable.status_code == 400

    rendered = client.post(
        "/api/agent/prompt-editor/templates/direct_answer/render",
        json={"variables": {"name": "Ada"}},
    )
    assert rendered.status_code == 200
    assert "Editor sentinel for Ada." in rendered.json()["rendered"]

    reset = client.post("/api/agent/prompt-editor/templates/direct_answer/reset")
    assert reset.status_code == 200
    reset_template = reset.json()["template"]
    assert reset_template["body"] == original_body
    assert reset_template["edited"] is False


def test_prompt_editor_api_reports_unknown_and_invalid_templates(tmp_path) -> None:
    client = _prompt_editor_client(tmp_path)

    missing = client.get("/api/agent/prompt-editor/templates/no.such.prompt")
    assert missing.status_code == 404

    invalid = client.patch(
        "/api/agent/prompt-editor/templates/direct_answer",
        json={"body": "Cost is $5"},
    )
    assert invalid.status_code == 400
    assert "placeholder syntax" in invalid.json()["detail"]


def test_prompt_editor_memory_api_creates_lists_updates_and_reports_errors(tmp_path) -> None:
    client = _prompt_editor_client(tmp_path)

    empty = client.get("/api/agent/prompt-editor/memories")
    assert empty.status_code == 200
    assert empty.json()["count"] == 0
    assert empty.json()["db_path"].endswith("agent_memory.db")

    created = client.post(
        "/api/agent/prompt-editor/memories",
        json={
            "instruction": "On macOS, launch apps with open -a when the app name is known.",
            "summary": "macOS app launching",
            "memory_kind": "task_memory",
            "scope": "global",
            "task_type": "macos",
            "tool_type": "shell_command",
            "tags": ["macos", "open"],
            "safe_examples": ["open -a Safari"],
        },
    )
    assert created.status_code == 200
    memory = created.json()["memory"]
    memory_id = memory["memory_id"]
    assert memory["summary"] == "macOS app launching"
    assert memory["tags"] == ["macos", "open"]

    listed = client.get("/api/agent/prompt-editor/memories")
    assert listed.status_code == 200
    list_payload = listed.json()
    assert list_payload["count"] == 1
    assert list_payload["stats"]["active"] == 1
    assert list_payload["memories"][0]["memory_id"] == memory_id
    assert list_payload["memories"][0]["preview"]
    assert "instruction" not in list_payload["memories"][0]

    detail = client.get(f"/api/agent/prompt-editor/memories/{memory_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["memory"]["instruction"].startswith("On macOS")
    assert detail_payload["audit_events"][0]["actor"] == "prompt_editor"

    updated = client.patch(
        f"/api/agent/prompt-editor/memories/{memory_id}",
        json={
            "instruction": "On macOS, use open -a Safari to launch Safari.",
            "summary": "macOS Safari launch",
            "status": "retired",
            "tags": ["macos", "safari"],
            "blocked_examples": ["xdg-open Safari"],
        },
    )
    assert updated.status_code == 200
    updated_memory = updated.json()["memory"]
    assert updated_memory["status"] == "retired"
    assert updated_memory["summary"] == "macOS Safari launch"
    assert updated_memory["tags"] == ["macos", "safari"]
    assert updated_memory["blocked_examples"] == ["xdg-open Safari"]

    active_list = client.get("/api/agent/prompt-editor/memories?status=active")
    assert active_list.status_code == 200
    assert active_list.json()["count"] == 0

    missing = client.get("/api/agent/prompt-editor/memories/no-such-memory")
    assert missing.status_code == 404

    invalid = client.patch(
        f"/api/agent/prompt-editor/memories/{memory_id}",
        json={"instruction": "   "},
    )
    assert invalid.status_code == 400
    assert "cannot be empty" in invalid.json()["detail"]


def _wait_for_event_run_status(
    client: TestClient,
    event_id: str,
    expected_status: str,
    *,
    timeout: float = 3.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/api/agent/events/{event_id}/runs")
        assert response.status_code == 200
        runs = response.json()["runs"]
        if runs:
            latest = runs[0]
            if latest["status"] == expected_status:
                return latest
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {expected_status}; latest run: {latest}")


def _wait_for_task_status(
    client: TestClient,
    task_id: str,
    expected_status: str,
    *,
    timeout: float = 3.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/api/agent/tasks/{task_id}")
        assert response.status_code == 200
        latest = response.json()["task"]
        if latest["status"] == expected_status:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for task {expected_status}; latest task: {latest}")


def _wait_for_trace_status(
    client: TestClient,
    request_id: str,
    expected_status: str,
    *,
    timeout: float = 3.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/api/agent/trace/{request_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["status"] == expected_status:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for trace {expected_status}; latest trace: {latest}")


class _GatewayDiscoveryResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> "_GatewayDiscoveryResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_agent_ui_html_serves_light_theme_and_versioned_assets() -> None:
    client = _client()

    response = client.get("/agent-ui")

    assert response.status_code == 200
    html = response.text
    assert '<option value="light">Light</option>' in html
    assert '<option value="daylight">Daylight</option>' in html
    assert '<option value="mint">Mint</option>' in html
    assert '<option value="citrus">Citrus</option>' in html
    assert '<option value="rosewater">Rosewater</option>' in html
    assert '<option value="steel">Steel</option>' in html
    assert '<option value="aurora">Aurora</option>' in html
    assert '<option value="ember">Ember</option>' in html
    assert '<option value="ubuntu">Ubuntu</option>' in html
    assert '<option value="ubuntu-dark">Ubuntu Dark</option>' in html
    assert '<option value="dracula">Dracula</option>' in html
    assert '<option value="nord">Nord</option>' in html
    assert '<option value="tokyo-night">Tokyo Night</option>' in html
    assert '<option value="catppuccin-mocha">Catppuccin Mocha</option>' in html
    assert '<option value="solarized-dark">Solarized Dark</option>' in html
    assert '<option value="gruvbox-dark">Gruvbox Dark</option>' in html
    assert '<option value="nebula">Nebula</option>' in html
    assert '<option value="moss">Moss</option>' in html
    assert '<option value="retro">Retro</option>' in html
    assert '<option value="lagoon">Lagoon</option>' in html
    assert '<option value="sakura">Sakura</option>' in html
    assert '<option value="harbor">Harbor</option>' in html
    assert '<option value="circuit">Circuit</option>' in html
    assert '<option value="random-pastel">Random Pastel</option>' in html
    assert 'id="theme-randomize-button"' in html
    assert 'id="engine-version"' in html
    assert '<html lang="en" class="app-booting">' in html
    assert 'id="app-boot-screen"' in html
    assert "OpenFabric Agent" in html
    assert '"ubuntu-dark"' in html
    assert '"dracula"' in html
    assert '"nord"' in html
    assert '"tokyo-night"' in html
    assert '"catppuccin-mocha"' in html
    assert '"solarized-dark"' in html
    assert '"gruvbox-dark"' in html
    assert 'href="/agent-ui/static/app.css?v=20260606-prompt-gutters"' in html
    assert 'href="/agent-ui/static/vendor/xterm/xterm.css?v=5.5.0"' in html
    assert 'src="/agent-ui/static/agent_ui_shared.js?v=20260602-text-odometer"' in html
    assert 'src="/agent-ui/static/app.js?v=20260606-run-queue"' in html
    assert html.index('src="/agent-ui/static/agent_ui_shared.js?v=20260602-text-odometer"') < html.index(
        'src="/agent-ui/static/app.js?v=20260606-run-queue"'
    )
    assert 'class="manual-footer-link"' in html
    assert 'href="http://127.0.0.1:8013/manual#doc=first-run-agent-ui"' in html
    assert 'data-manual-link' in html
    assert 'data-manual-doc="first-run-agent-ui"' in html
    assert 'data-manual-port="8013"' in html
    assert "Service manual" in html
    assert 'href="/directory"' in html
    assert 'id="model-status"' in html
    assert "model auto" in html
    assert html.index('id="engine-version"') < html.index('id="model-status"')
    assert html.index('id="model-status"') < html.index(
        'class="manual-footer-link directory-footer-link"'
    )
    assert html.index('class="manual-footer-link directory-footer-link"') < html.index(
        'class="prompt-gateway-picker footer-gateway-picker"'
    )
    assert 'id="topbar-menu-toggle"' in html
    assert 'aria-controls="topbar-actions"' in html
    assert 'id="topbar-actions"' in html
    assert 'id="backend-connection-status"' in html
    assert 'aria-label="Checking backend connection"' in html
    assert 'class="settings-toggle-icon"' in html
    assert 'class="settings-toggle-text">Settings</span>' in html
    assert ">Settings Storage<" in html
    assert 'id="settings-backend-status"' in html
    assert "Settings are owned by the backend." in html
    assert 'id="wall-clock"' in html
    assert 'id="immersive-wall-clock"' in html
    assert 'data-task-filter="archived">Archived' in html
    assert 'id="task-delete-button"' in html
    assert 'id="tasks-clear-archived-button"' in html
    assert "Operator Cache" not in html
    assert 'id="setting-plan-cache-enabled"' not in html
    assert 'id="setting-computation-cache-enabled"' not in html
    assert "Auto-learning" in html
    assert 'id="setting-auto-learn-enabled"' in html
    assert 'id="learning-ledger-status"' in html
    assert ">Audio<" in html
    assert 'id="setting-audio-voice-input-enabled"' in html
    assert 'id="setting-audio-service-enabled" type="checkbox" disabled' in html
    assert 'id="setting-audio-service-host"' in html
    assert 'id="setting-audio-service-port"' in html
    assert 'id="setting-audio-service-url"' in html
    assert 'id="setting-audio-model-path"' in html
    assert 'id="setting-audio-binary-path"' in html
    assert 'id="audio-transcriber-test-button"' in html
    assert 'id="setting-workspace-cwd-guard"' in html
    assert 'id="setting-lrnt-enabled"' in html
    assert 'id="setting-lrt-threshold"' in html
    assert 'id="setting-lrdirect-enabled"' in html
    assert 'id="setting-lr-threshold"' in html
    assert 'id="setting-lr-secondary-threshold"' in html
    assert 'id="lrdirect-clear-button"' in html
    assert 'id="auto-learn-clear-button"' in html
    assert 'id="setting-shell-input-bindings-mode"' not in html
    assert 'data-mode="confirm_bound"' not in html
    assert 'class="settings-status-card operator-cache-cards"' in html
    assert 'class="operator-cache-card"' not in html
    assert 'id="operator-cache-clear-button"' not in html
    script = client.get("/agent-ui/static/app.js?v=test").text
    assert "agent_learning_ledger_auto_learn_enabled" in script
    assert "agent_command_template_cache_similarity_threshold" in script
    assert "agent_command_template_cache_secondary_similarity_threshold" in script
    assert "lrnt_enabled" in script
    assert "lrnt_similarity_threshold" in script
    assert "lrdirect_enabled" in script
    assert "learning.lesson.learned" in script
    assert "function updateLearningRuntimeActivityFromEvent" in script
    assert "learningRuntimeRetrievedCount" in script
    assert "learningRuntimeUsedCount" in script
    assert "learningRuntimeLearnedCount" in script
    assert "learningRuntimeAppliedNoticeRendered: new Set()" in script
    assert "learningRuntimeNoticeRendered: new Set()" in script
    assert 'return { kind: "learning", count: 1, label: "learned runtime cache entry" }' in script
    assert "function appendMessageInlineBadge" in script
    assert "function inlineNoticeTargetForRequest" in script
    assert "function appendLearningRuntimeAppliedConversationMessage" in script
    assert "function learningRuntimeAppliedSignal" in script
    assert "function learningRuntimeIsLrEx" in script
    assert "label: badgeLabel" in script
    assert "LR applied: ${applied}" in script
    assert "Modify auto-learnt action." in script
    assert 'className: "learning-runtime-applied-inline-badge"' in script
    assert "appendLearningRuntimeAppliedConversationMessage(event)" in script
    assert "function appendLearningRuntimeLearnedConversationMessage" in script
    assert "function learningRuntimeLearnedLabel" in script
    assert "LRN learned" in script
    assert "LR-EX payload-aware command template" in script
    assert "title: `Learned this: ${learned}" in script
    assert 'className: "learning-runtime-learned-inline-badge"' in script
    assert "appendLearningRuntimeLearnedConversationMessage(event)" in script
    assert 'const badgePrefix = status === "direct" ? "LRD" : "LR"' in script
    assert 'return { kind: "direct", count: 1, label: "used LR Direct exact-step cache" }' in script
    assert 'return { kind: "used", count: 1, label: "used LR-T task structure" }' in script
    assert "operator.cache.lookup" in script
    assert "operator.lrnt.write" in script
    assert "operator.lrt.lookup" in script
    assert "operator.lrt.hit" in script
    assert "operator.command_template_cache.write" in script
    assert "operator.command_template_cache.hit" in script
    assert "operator.lrdirect.hit" in script
    assert "/api/agent/lrdirect/clear" in script
    assert "/api/agent/learning-ledger/clear" in script
    assert "Delete this learnt step" in script
    assert "/api/agent/corrections/step/delete" in script
    assert "Delete only this learnt step? Other learnt actions in this plan or request will stay." in script
    assert "Deleted learnt step" in script
    assert "/api/audio-transcriber/transcribe" in script
    assert "refreshAudioPermissionState" in script
    assert "navigator.permissions.query({ name: \"microphone\" })" in script
    assert "audio_transcriber_service_host" in script
    assert "audio_transcriber_service_port" in script
    assert "const AUDIO_DEFAULT_SILENCE_TIMEOUT_SECONDS = 2;" in script
    assert "const AUDIO_MIN_SILENCE_TIMEOUT_SECONDS = 1;" in script
    assert "rmsThreshold: 0.016" in script
    assert "rmsThreshold: 0.022" in script
    assert "function audioDynamicSpeechThreshold" in script
    assert "function audioDynamicRecoveryThreshold" in script
    assert "audioShouldSampleAmbientRms" in script
    assert "AUDIO_SILENCE_RECOVERY_HOLD_MS" in script
    assert "usingRecoveryThreshold" in script
    assert "recoveringFromWarning" in script
    assert "aboveThresholdFrames >= 2" in script
    assert "function migrateAudioSilenceTimeoutSetting" in script
    assert "openfabric.agentUi.audioSilenceTimeoutV2" in script
    assert 'min="1"' in html
    assert 'value="2"' in html
    assert "Mic auto-stops after 2 seconds of detected silence." in html
    assert 'fetch(settingsBackendPreferencesUrl()' in script
    assert '"/api/agent/settings/preferences"' in script
    assert "settingsBackendPersistenceEnabled" in script
    assert 'settingsSaveButton?.addEventListener("click", async () =>' in script
    assert "await persistAgentSettingsToBackend(state.agentSettings);" in script
    assert "window.clearTimeout(state.settingsBackendSaveTimer);" in script
    assert "setSettingsBackendPersistenceEnabled" not in script
    assert 'const THEME_STORAGE_KEY = ""' in script
    assert "const SUPPORTED_THEME_NAMES = Object.freeze" in script
    assert "function storedThemePreference" in script
    assert "function withStoredThemePreference" in script
    assert "loadedBackendTheme = backendHasTheme" not in script
    assert "if (storedTheme && !loadedBackendTheme)" not in script
    assert "ui_theme: normalizeThemeName(merged.ui_theme)" in script
    assert "ui_theme: themeSelect?.value || state.agentSettings?.ui_theme" in script
    assert "ui_chat_pop_animation: normalizeChatPopAnimationMode(merged.ui_chat_pop_animation)" in script
    assert "ui_chat_pop_animation:" in script
    assert "ui_terminal_visible: merged.ui_terminal_visible !== false" in script
    assert "ui_chat_bubbles_enabled: merged.ui_chat_bubbles_enabled === true" in script
    assert "ui_terminal_visible: settingTerminalVisible?.checked !== false" in script
    assert "ui_chat_bubbles_enabled: settingChatBubbles?.checked === true" in script
    assert "persistString(themes.storageKey, state.agentSettings.ui_theme)" not in script
    assert "ui_agent_mode: normalizeAgentMode(merged.ui_agent_mode)" in script
    assert "insertPromptTextAtCursor" in script
    assert "function insertTextAtCursor" in script
    assert "setAudioVoiceTarget" in script
    assert "handleVoiceInputButtonClick(event" in script
    assert "Dictate clarification response" in script


def test_reliability_page_serves_shared_error_notifier_assets(tmp_path) -> None:
    client = _prompt_editor_client(tmp_path)

    response = client.get("/reliability")

    assert response.status_code == 200
    html = response.text
    assert "OpenFabric Reliability" in html
    assert 'src="/agent-ui/static/agent_ui_shared.js?v=20260602-text-odometer"' in html
    assert 'src="/agent-ui/static/reliability.js?v=20260525-reliability"' in html
    assert html.index("agent_ui_shared.js") < html.index("reliability.js")
    script = client.get("/agent-ui/static/reliability.js?v=test").text
    assert "const Core = window.OpenFabricAgentUi" in script
    assert "function notifyUiError" in script
    assert "Reliability refresh failed" in script
    assert "Reliability controls save failed" in script
    assert "Reliability eval failed" in script


def test_agent_ui_mobile_route_serves_touch_first_shell() -> None:
    client = _client()

    response = client.get("/agent-ui-mobile")

    assert response.status_code == 200
    html = response.text
    assert "OpenFabric Agent Mobile" in html
    assert 'href="/agent-ui/static/mobile.css?v=20260602-text-odometer"' in html
    assert 'src="/agent-ui/static/agent_ui_shared.js?v=20260602-text-odometer"' in html
    assert 'src="/agent-ui/static/mobile.js?v=20260602-text-odometer"' in html
    assert html.index('src="/agent-ui/static/agent_ui_shared.js?v=20260602-text-odometer"') < html.index(
        'src="/agent-ui/static/mobile.js?v=20260602-text-odometer"'
    )
    assert 'id="mobile-root"' in html
    assert 'id="mobile-topbar"' in html

    assert 'id="mobile-main"' in html
    assert 'id="mobile-chat-panel"' in html
    assert 'id="mobile-chat-form"' in html
    assert 'id="mobile-prompt-input"' in html
    assert 'id="mobile-parameter-shortcut-menu"' in html
    assert 'id="mobile-send-button"' in html
    assert 'id="mobile-stop-button"' in html
    assert 'id="mobile-new-chat-button"' in html
    assert 'id="mobile-mic-button"' in html
    assert 'id="mobile-terminal-button"' in html
    assert 'id="mobile-gateway-select"' in html
    assert 'aria-label="Gateway terminal"' in html
    assert 'id="mobile-theme-select"' in html
    assert '<option value="github">GitHub</option>' in html
    assert '<option value="midnight">Midnight</option>' in html
    assert '<option value="contrast">Contrast</option>' in html
    assert 'id="notification-sheet"' in html
    assert 'id="mobile-notifications-button"' in html
    assert 'id="mobile-notification-list"' in html
    assert 'id="mobile-notifications-mark-read"' in html
    assert 'id="mobile-task-sheet"' in html
    assert 'id="mobile-tasks-button"' in html
    assert 'id="mobile-task-list"' in html
    assert 'id="mobile-task-create-start"' in html
    assert 'id="mobile-monitor-sheet"' in html
    assert 'id="mobile-monitors-button"' in html
    assert 'id="mobile-monitor-list"' in html
    assert 'id="mobile-monitor-create-start"' in html
    assert 'id="mobile-browser-notifications-enabled"' in html
    assert 'id="mobile-browser-notifications-permission"' in html
    assert 'id="mobile-notification-sound-select"' in html
    assert 'id="mobile-notification-sound-test"' in html
    assert 'href="/agent-ui">Desktop</a>' in html
    assert 'id="mobile-activity-panel"' not in html
    assert 'id="mobile-events-panel"' not in html
    assert 'id="mobile-tools-panel"' not in html
    assert 'id="mobile-settings-panel"' not in html
    assert 'id="mobile-bottom-nav"' not in html
    assert 'data-mobile-tab=' not in html
    assert 'id="memory-sheet"' not in html
    assert 'id="gateway-sheet"' not in html
    assert 'id="event-sheet"' not in html
    assert 'id="history-sheet"' not in html
    assert 'id="trace-sheet"' not in html
    assert ">Settings<" not in html


def test_agent_ui_client_route_serves_client_chat_shell() -> None:
    client = _client()

    response = client.get("/agent-ui-client")
    desktop = client.get("/agent-ui")
    script = client.get("/agent-ui/static/app.js?v=test").text
    css = client.get("/agent-ui/static/app.css?v=test").text

    assert response.status_code == 200
    html = response.text
    assert "OpenFabric Agent Client" in html
    assert 'body class="agent-ui-client"' in html
    assert 'data-agent-ui-surface="client"' in html
    assert 'id="chat-log"' in html
    assert 'id="prompt-input"' in html
    assert 'id="new-chat-button"' in html
    assert 'id="stop-button"' in html
    assert 'id="submit-button"' in html
    assert 'id="queue-button"' in html
    assert 'id="notifications-drawer"' in html
    assert 'id="notification-overlay"' in html
    assert 'id="client-trace-close-button"' in html
    assert 'id="run-task-button"' not in html
    assert 'id="settings-toggle"' in html
    assert 'id="learning-runtime-indicator"' in html
    assert 'id="gateway-toggle"' in html
    assert 'id="terminal-panel"' in html
    assert 'id="tasks-drawer"' in html
    assert desktop.status_code == 200
    assert 'body class="agent-ui-client"' not in desktop.text
    assert "function isAgentUiClientSurface" in script
    assert 'window.location?.pathname === "/agent-ui-client"' in script
    assert "agentUiClientMode" in script
    assert "function setClientTraceOpen" in script
    assert "client-trace-open" in script
    assert "clientModeAdminOpen" in script
    assert "setClientTraceOpen(fullTrace)" in script
    assert "clientTraceCloseButton?.addEventListener" in script
    assert "body.agent-ui-client #settings-toggle" in css
    assert "body.agent-ui-client #immersive-settings-toggle" in css
    assert "body.agent-ui-client #run-task-button" not in css
    assert "body.agent-ui-client #queue-button" not in css
    assert "body.agent-ui-client #terminal-panel" in css
    assert "body.agent-ui-client.client-trace-open .trace-pane" in css
    assert "body.agent-ui-client .client-trace-close-button" in css


def test_agent_ui_mobile_static_assets_are_served_and_wired() -> None:
    client = _client()

    shared = client.get("/agent-ui/static/agent_ui_shared.js?v=test")
    css = client.get("/agent-ui/static/mobile.css?v=test")
    script = client.get("/agent-ui/static/mobile.js?v=test")

    assert shared.status_code == 200
    assert css.status_code == 200
    assert script.status_code == 200
    shared_text = shared.text
    css_text = css.text
    script_text = script.text
    assert "function apiFetch" in shared_text
    assert "function normalizeAgentSettings" in shared_text
    assert "function normalizeThemeName" in shared_text
    assert "function storedThemePreference" in shared_text
    assert 'const settingsKey = ""' in shared_text
    assert 'const themeKey = ""' in shared_text
    assert "openfabric.agentUi.promptHistory" not in shared_text
    assert "volatileStorage" in shared_text
    assert "auto_approve_commands" in shared_text
    assert "ui_chat_pop_animation" in shared_text
    assert "ui_thinking_text_animation" in shared_text
    assert "chatPopAnimationModes" in shared_text
    assert "normalizeChatPopAnimationMode" in shared_text
    assert "thinkingTextAnimationModes" in shared_text
    assert "normalizeThinkingTextAnimationMode" in shared_text
    assert "normalizeNotificationSoundVariant" in shared_text
    assert "browser_notifications_enabled" in shared_text
    assert "function normalizeEvent" in shared_text
    assert "function normalizeNotification" in shared_text
    assert "function normalizeTask" in shared_text
    assert "function createTask" in shared_text
    assert "function listTasks" in shared_text
    assert "function normalizeMonitor" in shared_text
    assert "function createMonitor" in shared_text
    assert "function listMonitors" in shared_text
    assert "function submitPrompt" in shared_text
    assert "function streamUrlForRequest" in shared_text
    assert "function loadSharedAgentSettings" in shared_text
    assert "function saveSharedAgentSettings" in shared_text
    assert "function notifyUiError" in shared_text
    assert "ui-error-toast-region" in shared_text
    assert ".mobile-chat-panel" in css_text
    assert ".mobile-notification-panel" in css_text
    assert ".mobile-notification-options" in css_text
    assert ".mobile-task-create" in css_text
    assert ".mobile-monitor-grid" in css_text
    assert ".mobile-confirmation-action-gateway" in css_text
    assert ".mobile-message-actions select" in css_text
    assert '.mobile-chat-log[data-chat-pop-animation="soft-rise"] .mobile-message' in css_text
    assert '.mobile-chat-log[data-chat-pop-animation="stream-roll"] .mobile-message' in css_text
    assert '.mobile-chat-log[data-chat-pop-animation="odometer"] .mobile-message' in css_text
    assert ".mobile-text-odometer-reel" in css_text
    assert "@keyframes mobileChatStreamRollText" in css_text
    assert "@keyframes mobileTextOdometerRoll" in css_text
    assert "@keyframes mobileChatPopSpring" in css_text
    assert "@keyframes mobileChatPopBlurGlow" in css_text
    assert ".mobile-theme-picker" in css_text
    assert ".mobile-gateway-picker" in css_text
    assert ".mobile-terminal-button" in css_text
    assert ".mobile-parameter-shortcut-menu" in css_text
    assert ".mobile-parameter-shortcut-option.active" in css_text
    assert ':root[data-theme="contrast"]' in css_text
    assert ".mobile-bottom-nav" not in css_text
    assert "@media (min-width: 700px)" in css_text
    assert "function handleChatSubmit" in script_text
    assert "function showActionError" in script_text
    assert "function markActionFieldInvalid" in script_text
    assert "Parameter key required" in script_text
    assert "No parameter selected" in script_text
    assert "Correct the JSON object in the value field, then save again." in script_text
    assert "function maybeCreateMonitorFromChat" in script_text
    assert "function loadMonitors" in script_text
    assert "function renderMonitors" in script_text
    assert 'Core.apiJson("/monitors/draft"' in script_text
    assert 'Core.apiJson("/monitors"' in script_text
    assert "function loadGateways" in script_text
    assert "function renderGatewaySelector" in script_text
    assert "function selectedGatewayContext" in script_text
    assert "function openMobileTerminal" in script_text
    assert 'window.localStorage?.setItem("openfabric.agentUi.terminalVisible", "true")' in script_text
    assert 'window.localStorage?.setItem("openfabric.agentUi.terminalCollapsed", "false")' in script_text
    assert 'window.location.assign("/agent-ui?terminal=1")' in script_text
    assert 'elements.terminalButton?.addEventListener("click", openMobileTerminal)' in script_text
    assert "function gatewayLabelFromMetadata" in script_text
    assert "function confirmationActionGatewayText" in script_text
    assert "mobile-confirmation-action-gateway" in script_text
    assert "Core.keys.selectedGateway" in script_text
    assert 'Core.apiJson("/gateways"' in script_text
    assert "gateway_id: gateway.gateway_id" in script_text
    assert "function applyMobileTheme" in script_text
    assert "function applyMobileChatPopAnimation" in script_text
    assert "Core.normalizeChatPopAnimationMode" in script_text
    assert "Core.keys.theme" not in script_text
    assert "function loadMobileSharedSettings" in script_text
    assert "Core.loadSharedAgentSettings" in script_text
    assert "Core.saveSharedAgentSettings" in script_text
    assert "function loadNotifications" in script_text
    assert "function loadTasks" in script_text
    assert "function createMobileTask" in script_text
    assert "function handleTaskAction" in script_text
    assert "function showBrowserNotification" in script_text
    assert "function requestBrowserNotificationPermission" in script_text
    assert "function playNotificationSound" in script_text
    assert "function deliverUnreadNotifications" in script_text
    assert "function shouldSuppressLiveTaskNotificationDelivery" in script_text
    assert "function markSuppressedLiveTaskNotificationRead" in script_text
    assert 'new window.Notification' in script_text
    assert "function startMobileTraceStream" in script_text
    assert "function startVoiceRecording" in script_text
    assert "function stopVoiceRecording" in script_text
    assert "function handleVoiceRecordingSubmitIntent" in script_text
    assert "new EventSource" in script_text
    assert "submitConfirmation" in script_text
    assert "submitClarification" in script_text
    assert "parameter_choices" in script_text
    assert "parameter_choice_id" in script_text
    assert "function mobileParameterShortcutTriggerAtCursor" in script_text
    assert "function loadMobilePromptParameterShortcuts" in script_text
    assert "function insertMobileParameterShortcut" in script_text
    assert 'params: { limit: 1000 }' in script_text
    assert "answer_is_secret" in script_text
    assert "/api/audio-transcriber/config" in script_text
    assert "/api/audio-transcriber/transcribe" in script_text
    assert "MediaRecorder" in script_text
    assert "getUserMedia" in script_text
    assert "audioSubmitAfterTranscription" in script_text
    assert "SpeechRecognition" not in script_text
    assert "webkitSpeechRecognition" not in script_text
    assert "function bindBottomTabs" not in script_text
    assert "function loadEvents" not in script_text
    assert "function saveMobileSettings" not in script_text


def test_agent_ui_version_endpoint_exposes_engine_version() -> None:
    client = _client()

    response = client.get("/api/agent/version")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "1.0"
    assert payload["display"].startswith("1.0")
    assert "runtime_version" in payload
    assert "git_hash" in payload
    check = client.get("/api/agent/version/check")
    assert check.status_code == 200
    check_payload = check.json()
    assert check_payload["comparison_key"] == "git_hash"
    assert "running" in check_payload
    assert "latest" in check_payload
    assert "new_version_available" in check_payload
    assert check_payload["restart_endpoint"] == "/api/agent/restart"
    html = client.get("/agent-ui").text
    assert 'src="/agent-ui/static/vendor/xterm/xterm.js?v=5.5.0"' in html
    assert 'src="/agent-ui/static/app.js?v=20260606-run-queue"' in html
    assert 'class="topbar-title"' in html
    assert 'class="brand-title"' in html
    assert "<span>OpenFabric</span>" in html
    assert '<span id="brand-agent-name">Agent</span>' in html
    assert '<span id="immersive-brand-agent-name" class="immersive-brand-agent-name">Agent</span>' in html
    assert ">Name Your Agent<" in html
    assert 'id="setting-agent-name"' in html
    assert 'id="agent-name-random-button"' in html
    assert 'class="topbar-status-row"' not in html
    assert 'id="restart-button"' in html
    assert 'id="tasks-toggle"' in html
    assert 'id="tasks-drawer"' in html
    assert 'id="tasks-drawer-resizer"' in html
    assert 'id="run-task-button"' not in html
    assert 'id="queue-button"' in html
    assert 'id="task-create-start-button"' in html
    assert 'class="memory-stats events-status-summary tasks-status-summary"' in html
    assert '<details id="task-editor-card"' in html
    assert '<details id="task-list-card"' in html
    assert '<details id="task-details-card"' in html
    assert 'class="memory-card event-section-card task-editor-card"' in html
    assert 'class="settings-drawer-footer event-controls-bar task-controls-bar"' in html
    assert html.index('id="task-create-start-button"') < html.index('id="task-list-card"')
    assert html.index('id="task-list-card"') < html.index('id="task-details-card"')
    assert 'id="monitors-toggle"' in html
    assert 'id="monitors-drawer"' in html
    assert 'id="monitors-drawer-resizer"' in html
    assert 'id="monitor-create-start-button"' in html
    assert 'class="memory-stats events-status-summary monitors-status-summary"' in html
    assert '<details id="monitor-editor-card"' in html
    assert '<details id="monitor-list-card"' in html
    assert '<details id="monitor-details-card"' in html
    assert 'class="settings-drawer-footer event-controls-bar monitor-controls-bar"' in html
    assert html.index('id="monitor-create-start-button"') < html.index('id="monitor-list-card"')
    assert html.index('id="monitor-list-card"') < html.index('id="monitor-details-card"')
    assert 'id="setting-auto-immersive-min-width"' in html
    assert "Auto immersive width" in html
    assert html.index('class="settings-drawer-footer"') < html.index('id="restart-button"')
    assert html.index('id="settings-reset-button"') < html.index('id="restart-button"')
    assert html.index('id="restart-button"') < html.index('id="settings-save-button"')
    assert html.index('aria-label="Displays the interactive terminal') < html.index('id="setting-terminal-visible"')
    assert 'id="setting-chat-bubbles"' in html
    assert "Use chat bubbles" in html
    assert 'id="setting-chat-pop-animation"' in html
    assert "Chat pop animation" in html
    assert '<option value="blur-glow">Blur glow</option>' in html
    assert '<option value="odometer">Odometer</option>' in html
    assert 'id="setting-thinking-text-animation"' in html
    assert "Thinking text animation" in html
    assert '<option value="roll-up">Roll up</option>' in html
    assert '<option value="swing">Swing</option>' in html
    assert "Controls deliberation depth, plan review, answer judging, and self-brief behavior through one backend-owned profile." in html
    assert "Keeps human-readable planning and review contracts." in html
    assert 'id="setting-operator-verbose"' in html
    assert ">Verbose</span>" in html
    assert 'id="agent-optimization-control"' in html
    assert 'id="setting-agent-optimization-control"' in html
    assert 'id="quick-clarification-mode"' in html
    assert "Optimize agent for" in html
    assert ">Accuracy</button>" in html
    assert ">Custom</button>" in html
    assert ">Speed</button>" in html
    assert ">Auto-pilot</button>" in html
    assert ">Balanced</button>" in html
    assert ">Pedantic</button>" in html
    assert 'data-agent-optimization-heat' in html
    assert 'id="fast-mode-toggle"' not in html
    assert "Fast / Thinking" not in html
    assert 'id="quick-audio-toggle"' in html
    assert 'id="prompt-voice-input-anchor"' in html
    assert 'id="voice-input-button"' in html
    assert 'class="voice-input-button"' in html
    assert 'id="quick-terminal-toggle-button"' in html
    assert 'class="quick-terminal-toggle-button"' in html
    assert 'id="footer-voice-input-anchor"' not in html
    assert ">Thinking</span>" not in html
    assert "Checks the final response against real outputs" not in html
    assert "Restrict cwd to workspace" in html
    assert "operator action cwd values must stay inside the repo workspace" in html
    assert ">Policy Ownership<" not in html
    assert 'id="setting-policy-preset"' not in html
    assert 'id="setting-policy-effect"' not in html
    assert 'id="setting-policy-streaming-scope"' not in html
    assert 'id="setting-policy-python-code-review"' not in html
    assert "LLM-owned" not in html
    assert ">Animation<" in html
    assert 'id="setting-number-animation"' in html
    assert '<option value="odometer">Odometer</option>' in html
    assert '<option value="fade">Fade</option>' in html
    assert '<option value="slide">Slide</option>' in html
    assert '<option value="pop">Pop</option>' in html
    assert '<option value="flip">Flip</option>' in html
    assert "Displays the interactive terminal in the main workspace" in html
    assert "Shows planning, prompt, and execution trace events" in html
    assert "Switches the conversation to left/right aligned chat styling" in html
    assert "Retrieves targeted active memories as guidance" in html
    assert 'class="settings-info-icon"' in html
    assert 'class="settings-info-tooltip"' in html
    assert ">Restart</button>" in html
    assert 'class="settings-model-picker"' in html
    assert 'id="model-select"' in html
    assert html.index('id="settings-drawer"') < html.index('id="model-select"')
    assert html.index(">LLM Endpoint<") < html.index('id="model-select"')
    assert html.index('id="model-select"') < html.index('id="setting-llm-base-scheme"')
    assert 'id="theme-select"' in html
    assert 'id="memory-toggle"' in html
    assert 'id="gateway-toggle"' in html
    assert 'id="events-toggle"' in html
    assert 'id="conversation-collapse-all-button"' in html
    assert 'id="terminal-collapse-all-button"' in html
    assert 'id="trace-collapse-all-button"' in html
    assert 'id="llm-stream-collapse-all-button"' in html
    assert 'id="progress-collapse-all-button"' in html
    assert 'id="visualization-collapse-all-button"' in html
    assert 'id="visualization-detail-collapse-all-button"' in html
    assert 'id="settings-drawer-resizer"' in html
    assert 'aria-label="Resize settings drawer"' in html
    assert 'id="settings-collapse-all-button"' in html
    assert 'aria-label="Collapse all settings sections"' in html
    assert 'class="settings-section settings-collapsible-section"' in html
    assert 'class="settings-section-summary"' in html
    assert 'class="settings-section-body"' in html
    assert 'id="memory-drawer"' in html
    assert 'id="memory-drawer-resizer"' in html
    assert 'aria-label="Resize memory drawer"' in html
    assert 'id="memory-collapse-all-button"' in html
    assert 'id="memory-close-button"' in html
    assert 'id="settings-tab-settings"' not in html
    assert 'id="settings-tab-memory"' not in html
    assert html.index('id="settings-drawer"') < html.index('id="memory-drawer"')
    assert 'id="gateway-drawer"' in html
    assert 'id="gateway-drawer-resizer"' in html
    assert 'aria-label="Resize gateways drawer"' in html
    assert 'id="gateway-collapse-all-button"' in html
    assert 'id="gateway-close-button"' in html
    assert 'id="gateway-select"' in html
    assert 'id="immersive-gateway-select"' in html
    assert 'class="prompt-gateway-picker immersive-gateway-picker"' in html
    assert 'id="gateway-active-summary"' in html
    assert 'class="memory-stats gateway-status-summary"' in html
    assert 'class="memory-card gateway-section-card gateway-active-card"' in html
    assert 'class="memory-card-header gateway-section-summary"' in html
    assert 'class="gateway-section-body"' in html
    assert 'class="gateway-os-badge"' not in html
    assert 'id="gateway-list"' in html
    assert 'id="gateway-registry-select"' in html
    assert 'id="gateway-details-body"' in html
    assert 'id="gateway-save-button"' in html
    assert 'id="gateway-test-button"' in html
    assert 'id="gateway-delete-button"' in html
    assert "Gateway Registry" in html
    assert "Nickname" in html
    assert 'id="gateway-label"' in html
    assert html.index('id="memory-drawer"') < html.index('id="gateway-drawer"')
    assert 'id="events-drawer"' in html
    assert 'id="events-drawer-resizer"' in html
    assert 'aria-label="Resize events drawer"' in html
    assert 'id="events-collapse-all-button"' in html
    assert 'id="event-draft-list"' not in html
    assert 'id="events-list"' in html
    assert 'id="event-runs"' in html
    assert 'id="event-selected-summary"' not in html
    assert 'id="events-status-summary"' in html
    assert 'class="memory-stats events-status-summary"' in html
    assert '<details id="event-drafts-card"' not in html
    assert '<details id="event-editor-card"' in html
    assert '<details id="event-list-card"' in html
    assert '<details id="event-runs-card"' in html
    assert 'class="memory-card event-section-card event-editor-card"' in html
    assert 'id="event-gateway"' in html
    assert 'id="event-terminal-cwd"' in html
    assert 'class="event-title-field"' in html
    assert 'id="event-edit-button"' in html
    assert 'id="event-save-button"' in html
    assert 'id="event-run-now-button"' in html
    assert 'class="settings-drawer-footer event-controls-bar"' in html
    assert html.index('class="event-editor-actions"') < html.index('id="event-save-button"')
    assert html.index('id="event-auto-approve"') < html.index('id="event-save-button"')
    assert html.index('id="event-save-button"') < html.index('id="event-list-card"')
    assert "Event History" in html
    assert "Runtime Events" in html
    assert html.index('id="gateway-drawer"') < html.index('id="events-drawer"')
    assert 'id="setting-clarification-mode"' in html
    assert 'id="setting-clarification-rounds"' in html
    assert 'id="setting-memory-enabled"' in html
    assert 'id="setting-events-enabled"' in html
    assert 'id="setting-memory-prompt-chars"' in html
    assert "Memory Library" in html
    assert 'class="settings-section memory-settings memory-dashboard"' in html
    assert 'id="memory-stats"' in html
    assert 'id="memory-entry-picker"' in html
    assert 'class="memory-card memory-library-card memory-section-card"' in html
    assert 'class="memory-card memory-editor-card memory-section-card"' in html
    assert 'class="memory-card memory-feedback-card memory-section-card"' in html
    assert 'class="memory-card-header memory-section-summary"' in html
    assert 'class="memory-section-body"' in html
    assert 'class="memory-advanced-filters"' in html
    assert 'class="memory-association-panel"' in html
    assert 'id="memory-list"' not in html
    assert 'id="memory-scope-filter"' in html
    assert 'id="memory-kind-filter"' in html
    assert 'id="memory-model-family-filter"' in html
    assert 'id="memory-task-filter"' in html
    assert 'id="memory-tool-filter"' in html
    assert 'id="memory-intent-filter"' in html
    assert 'id="memory-validator-error-filter"' in html
    assert 'id="memory-tag-filter"' in html
    assert 'id="memory-kind"' in html
    assert 'id="memory-validator-error-type"' in html
    assert 'id="memory-safe-examples"' in html
    assert 'id="memory-blocked-examples"' in html
    assert 'id="memory-instruction"' in html
    assert 'id="memory-feedback-button"' in html
    assert 'id="memory-validation-feedback-button"' in html
    assert 'id="memory-optimize-button"' in html
    assert '<option value="auto">Auto</option>' in html
    assert 'id="agent-mode-control"' in html
    assert 'data-agent-mode="llm_operator"' in html
    assert 'data-agent-mode="standard"' in html
    assert 'data-agent-mode="advisory"' in html
    assert 'class="agent-mode-control"' in html
    assert "Agentic" in html
    assert "Conversational" in html
    assert "Advisory" in html
    assert "Advisory explains and offers runnable snippets" in html
    assert ">Answers<" in html
    assert 'id="output-composition-toggle"' in html
    assert 'class="mode-switch response-mode-switch"' in html
    assert ">Detailed<" in html
    assert ">Simple<" in html
    assert 'class="prompt-info-icon"' in html
    assert 'class="prompt-info-tooltip"' in html
    assert "Detailed composes a fuller final answer" in html
    assert ">Validation<" in html
    assert 'id="verification-enforced-toggle"' in html
    assert ">Relaxed<" in html
    assert ">Enforced<" in html
    assert "Enforced asks the agent to verify work when useful" in html
    assert 'class="prompt-context-row"' in html
    assert 'id="context-meter"' in html
    assert 'data-context-percent="--"' in html
    assert html.index('id="verification-enforced-toggle"') < html.index(
        'id="context-meter"'
    )
    assert html.index('id="context-meter"') < html.index('id="voice-input-button"')
    assert html.index('id="voice-input-button"') < html.index('id="quick-terminal-toggle-button"')
    assert html.index('id="quick-terminal-toggle-button"') < html.index('id="new-chat-button"')
    assert html.index('id="voice-input-button"') < html.index('id="new-chat-button"')
    assert html.index('id="context-meter"') < html.index('id="prompt-input"')
    assert "20260602-text-odometer" in html
    assert 'id="terminal-exec-toggle"' not in html
    assert ">Run in Terminal<" not in html
    assert 'id="terminal-toggle"' not in html
    assert 'class="composer-split"' in html
    assert 'id="terminal-panel"' in html
    assert 'id="terminal-header"' in html
    assert 'aria-expanded="true"' in html
    assert 'id="advisory-terminal-context-toggle"' in html
    assert 'class="mode-switch terminal-advisory-context-toggle"' in html
    assert 'role="switch"' in html
    assert "Include terminal in Advisory" in html
    assert "Include terminal context in Advisory" in html
    assert 'class="mode-track"' in html
    assert 'class="mode-thumb"' in html
    assert "Adds cwd and visible terminal output to Advisory prompts." in html
    assert 'id="terminal-screen"' in html
    assert 'id="terminal-copy-button"' in html
    assert 'class="composer-footer"' in html
    assert html.index('id="prompt-form"') < html.index('id="run-status"')
    assert html.index('id="run-status"') < html.index('id="request-id"')
    assert html.index('id="request-id"') < html.index('id="engine-version"')
    assert html.index('id="engine-version"') < html.index('id="model-status"')
    assert html.index('id="prompt-form"') < html.index('id="engine-version"')
    assert 'id="model-status"' in html
    assert 'id="quick-controls-toggle"' in html
    assert 'aria-controls="quick-controls-panel"' in html
    assert 'id="immersive-settings-toggle"' in html
    assert 'aria-controls="settings-drawer"' in html
    assert 'class="immersive-header-controls" aria-label="Immersive header controls"' in html
    assert 'class="immersive-header-badges" aria-label="Runtime status badges"' in html
    assert 'id="immersive-run-status"' in html
    assert 'id="immersive-model-status"' in html
    assert 'id="immersive-llm-activity-indicator"' in html
    assert 'id="immersive-learning-runtime-indicator"' in html
    assert 'id="quick-controls-panel"' in html
    assert 'aria-label="Conversation quick controls"' in html
    assert 'id="quick-controls-close-button"' in html
    assert 'class="quick-controls-footer"' in html
    assert 'id="quick-restart-button"' in html
    assert "Restart agent + gateways" in html
    assert 'class="quick-controls-section-heading"' in html
    assert html.count('class="prompt-info-icon quick-controls-section-info"') == 5
    assert "Choose whether the agent executes tasks" in html
    assert "Approval controls command confirmation" in html
    assert "Shows the next five active scheduled events" in html
    assert 'id="quick-upcoming-events-list"' in html
    assert "Pick the interface theme" in html
    assert html.index('id="conversation-header"') < html.index('id="quick-controls-toggle"')
    assert html.index('id="quick-controls-toggle"') < html.index('id="immersive-settings-toggle"')
    assert html.index('id="immersive-settings-toggle"') < html.index('class="immersive-header-badges"')
    assert html.index('class="immersive-header-badges"') < html.index('class="immersive-brand-clock"')
    assert html.index('id="conversation-clear-button"') < html.index(
        'id="conversation-history-button"'
    )
    assert html.index('id="quick-controls-panel"') < html.index(
        'id="conversation-history-button"'
    )
    assert html.index(
        'class="conversation-actions conversation-actions-secondary"'
    ) < html.index('id="quick-controls-panel"')
    assert html.index('id="quick-controls-panel"') < html.index(
        'id="agent-mode-control"'
    )
    assert html.index('id="agent-mode-control"') < html.index(">Answers<")
    assert html.index(">Answers<") < html.index(
        'id="output-composition-toggle"'
    )
    assert html.index('id="output-composition-toggle"') < html.index(">Validation<")
    assert html.index(">Validation<") < html.index(
        'id="verification-enforced-toggle"'
    )
    assert html.index('id="verification-enforced-toggle"') < html.index(
        'id="auto-approve-toggle"'
    )
    assert html.index(">Approval<") < html.index('id="auto-approve-toggle"')
    assert html.index('id="auto-approve-toggle"') < html.index(">Optimize agent for<")
    assert html.index(">Optimize agent for<") < html.index('id="agent-optimization-control"')
    assert html.index('id="auto-approve-toggle"') < html.index('id="agent-optimization-control"')
    assert html.index('id="agent-optimization-control"') < html.index(">Clarification<")
    assert html.index(">Clarification<") < html.index('id="quick-clarification-mode"')
    assert html.index('id="quick-clarification-mode"') < html.index('data-agent-clarification-heat')
    assert html.index('id="quick-clarification-mode"') < html.index(">Voice<")
    assert html.index(">Voice<") < html.index('id="quick-audio-toggle"')
    assert html.index('id="quick-upcoming-events-list"') < html.index('id="quick-audio-toggle"')
    assert html.index('id="quick-upcoming-events-list"') < html.index('id="theme-select"')
    assert html.index('id="theme-select"') < html.index('id="quick-restart-button"')
    assert html.index('id="quick-restart-button"') < html.index('id="chat-log"')
    assert html.index('id="theme-select"') < html.index('id="gateway-select"')
    assert 'id="model-status"' in html
    assert html.index('class="composer-footer"') < html.index('id="gateway-select"')
    assert html.index('id="gateway-select"') < html.index('class="manual-footer-link"')
    assert html.index('id="terminal-status"') < html.index('id="gateway-select"')
    assert 'id="terminal-gateway-badge" class="terminal-gateway-badge" hidden' in html
    assert html.count('id="run-status"') == 1
    assert html.count('id="engine-version"') == 1
    assert html.count('id="model-status"') == 1
    assert html.count('id="theme-select"') == 1
    assert html.count('id="gateway-select"') == 1
    assert html.count('id="immersive-gateway-select"') == 1
    assert 'id="terminal-status" class="terminal-status" hidden' in html
    assert html.index('id="terminal-panel"') < html.index('id="prompt-form"')
    assert '<button id="run-task-button" class="run-task-button" type="button">Queue</button>' not in html
    assert "Run as Task" not in html
    assert 'id="openfabric-idle-art"' in html
    assert 'class="openfabric-banner-art"' in html
    assert "██████" in html
    assert "local agent orbiting quietly" in html
    assert 'class="conversation-panel"' in html
    assert 'id="conversation-header"' in html
    assert 'id="conversation-immersive-toggle"' in html
    assert 'role="switch"' in html
    assert 'aria-label="Immersive mode"' in html
    assert 'id="conversation-summary-label"' in html
    assert 'id="conversation-header-confirmation"' in html
    assert 'id="agent-optimization-control"' in html
    assert 'class="tri-state-toggle agent-optimization-control"' in html
    assert 'class="tri-state-toggle agent-optimization-control agent-clarification-control quick-clarification-control"' in html
    assert 'data-agent-clarification-heat' in html
    assert 'class="mode-switch auto-approve-switch conversation-auto-approve-switch"' in html
    assert 'id="conversation-deep-reasoning-badge"' in html
    assert "Reasoning profile: balanced" in html
    assert "BAL" in html
    assert 'id="conversation-streaming-badge"' in html
    assert "Streaming operator mode is enabled" in html
    assert 'id="conversation-memory-applied-badge"' in html
    assert "🧠" in html
    assert "Previous memory applied to this request" in html
    assert 'id="conversation-cache-applied-badge"' in html
    assert "cachehit" in html
    assert "Cache used for this request" in html
    assert 'id="conversation-header-memory-button"' in html
    assert 'id="conversation-header-approve-button"' in html
    assert 'id="conversation-header-deny-button"' in html
    assert "Thinking" in html
    assert 'id="conversation-copy-button"' in html
    assert 'id="conversation-history-button"' in html
    assert 'id="conversation-memory-button"' in html
    assert 'id="conversation-clear-button"' in html
    assert 'id="chat-history-drawer"' in html
    assert 'id="chat-history-list"' in html
    assert html.index('id="conversation-clear-button"') < html.index('id="conversation-copy-button"')
    assert 'id="conversation-activity-label"' in html
    assert 'id="conversation-collapse-label"' not in html
    assert 'id="terminal-split-resizer"' in html
    assert 'aria-label="Resize conversation and terminal"' in html
    assert 'id="progress-header"' in html
    assert 'id="progress-title"' in html
    assert 'id="progress-summary"' in html
    assert 'id="progress-response-metrics"' not in html
    assert 'id="progress-copy-button"' in html
    assert 'id="progress-collapse-label"' not in html
    assert html.index('class="pane trace-pane"') < html.index('id="trace-events-panel"')
    assert html.index('id="trace-events-panel"') < html.index('id="trace-list"')
    assert html.index('id="trace-list"') < html.index('id="llm-stream-panel"')
    assert html.index('id="llm-stream-panel"') < html.index('id="progress-header"')
    assert 'id="setting-terminal-visible"' in html
    assert "Show terminal pane" in html
    assert 'id="setting-trace-visible"' in html
    assert "Show developer trace" in html
    assert 'id="setting-llm-base-host"' in html
    assert 'id="setting-llm-base-port"' in html
    assert 'id="setting-llm-base-path"' in html
    assert 'id="setting-llm-timeout-seconds"' in html
    assert 'id="setting-llm-max-tokens"' in html
    assert 'id="setting-llm-service-status"' in html
    assert 'id="llm-endpoint-test-button"' in html
    assert "Test endpoint" in html
    assert 'id="setting-self-brief-max-tokens"' not in html
    assert 'id="terminal-save-workspace-button"' in html
    assert ">LLM Endpoint<" in html
    assert ">LLM Server<" not in html
    assert 'id="setting-llm-launch-conda-env"' not in html
    assert 'id="setting-llm-launch-cwd"' not in html
    assert 'id="gateway-terminal-cwd"' in html
    assert 'id="setting-llm-launch-command"' not in html
    assert 'id="llm-start-button"' not in html
    assert 'id="llm-restart-button"' not in html
    assert 'id="llm-stop-button"' not in html
    assert 'id="llm-runtime-status"' not in html
    assert 'id="llm-runtime-terminal"' not in html
    assert 'id="llm-runtime-terminal-header"' not in html
    assert 'aria-controls="llm-runtime-log"' not in html
    assert 'id="llm-runtime-log"' not in html
    assert 'id="llm-log-copy-button"' not in html
    assert 'id="llm-log-clear-button"' not in html
    assert 'id="terminal-close-button"' not in html
    assert 'id="stop-button"' in html
    assert 'id="trace-toggle"' in html
    assert 'id="trace-copy-button"' in html
    assert 'id="visualization-toggle"' in html
    assert 'id="visualization-resizer"' in html
    assert 'class="pane visualization-pane"' in html
    assert 'id="visualization-copy-button"' in html
    assert 'id="visualization-live-button"' in html
    assert 'class="mode-switch visualization-direction-switch"' in html
    assert 'id="visualization-direction-toggle"' in html
    assert ">Horizontal<" in html
    assert ">Vertical<" in html
    assert 'class="visualization-zoom-controls"' in html
    assert 'id="visualization-zoom-out-button"' in html
    assert 'id="visualization-zoom-label"' in html
    assert 'id="visualization-zoom-in-button"' in html
    assert ">100%<" in html
    assert 'id="visualization-invert-button"' in html
    assert "Invert" in html
    assert 'class="visualization-map-footer"' in html
    assert 'id="visualization-reset-button"' in html
    assert 'id="visualization-detail-header"' in html
    assert 'id="visualization-detail-copy-button"' in html
    assert 'id="visualization-detail-toggle"' not in html
    assert ">Hide<" not in html
    assert 'aria-controls="visualization-detail-list"' in html
    assert html.index('id="visualization-invert-button"') < html.index('id="visualization-canvas"')
    assert html.index('id="visualization-invert-button"') < html.index('id="visualization-copy-button"')
    assert html.index('id="visualization-copy-button"') < html.index('id="visualization-canvas"')
    assert html.index('id="visualization-canvas"') < html.index('class="visualization-map-footer"')
    assert html.index('class="visualization-map-footer"') < html.index('id="visualization-zoom-out-button"')
    assert html.index('id="visualization-zoom-in-button"') < html.index('id="visualization-reset-button"')
    assert html.index('id="visualization-reset-button"') < html.index('id="visualization-detail-header"')
    assert html.index('id="visualization-detail-title"') < html.index('id="visualization-detail-copy-button"')
    assert 'id="setting-visualization-visible"' in html
    assert "Show visualization pane" in html
    assert "/agent-ui/static/vendor/mermaid/mermaid.min.js" in html
    assert 'id="new-chat-button"' in html
    assert 'class="copy-button pane-copy-button"' in html
    assert 'class="prompt-actions"' in html
    assert html.index('id="agent-mode-control"') < html.index('id="prompt-input"')
    assert html.index('id="agent-mode-control"') < html.index(
        'aria-label="Agentic plans and executes tool actions'
    )
    assert html.index('id="output-composition-toggle"') < html.index(
        'aria-label="Detailed composes a fuller final answer'
    )
    assert html.index('id="verification-enforced-toggle"') < html.index(
        'aria-label="Enforced asks the agent to verify work'
    )
    assert html.index('id="prompt-form"') < html.index('id="new-chat-button"')
    assert html.index('id="context-meter"') < html.index('id="voice-input-button"')
    assert html.index('id="voice-input-button"') < html.index('id="new-chat-button"')
    assert html.index('id="new-chat-button"') < html.index('id="submit-button"')
    assert html.index('id="submit-button"') < html.index('id="queue-button"')
    assert html.index('id="queue-button"') < html.index('id="stop-button"')
    assert html.index('id="stop-button"') < html.index('id="gateway-select"')
    assert html.index('id="terminal-reset-button"') < html.index('id="terminal-copy-button"')
    assert html.index('id="clear-button"') < html.index('id="trace-copy-button"')
    assert html.index('id="llm-stream-clear-button"') < html.index('id="llm-stream-copy-button"')
    assert ">New Chat<" in html
    assert ">Clear<" in html
    assert "Conversational" in html
    assert ">Run in Terminal<" not in html
    assert 'id="setting-command-output-expanded"' in html
    assert "Expand SSE capsules by default" in html
    assert 'id="command-output-panel"' not in html
    assert 'id="command-output-list"' not in html


def test_agent_ui_static_js_routes_command_output_to_streaming_step() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "function finishAppBoot()" in response.text
    assert 'root.classList.remove("app-booting")' in response.text
    assert 'appBootScreen?.remove()' in response.text
    assert "streamingStepMessagesByTaskId: new Map()" in response.text
    assert "streamingStepConfirmationMessagesByTaskId: new Map()" in response.text
    assert "streamingStepOutputPanelsByCommandKey: new Map()" in response.text
    assert "commandOutputPanelsByKey: new Map()" in response.text
    assert "function streamingStepMessageForCommandEvent" in response.text
    assert "function commandStreamStepScope" in response.text
    assert "function ensureStreamingStepCommandOutputContainer" in response.text
    assert "function ensureGlobalCommandOutputContainer" in response.text
    assert "function commandOutputContainerForEvent" in response.text
    assert "function commandOutputExecutionSnippetText" in response.text
    assert "function commandOutputOperationDescriptionText" in response.text
    assert "function normalizeTerminalOutputText" in response.text
    assert "function isRawTerminalOutput" in response.text
    assert "chunk.textContent = normalizeTerminalOutputText(item.text)" in response.text
    assert "if (isRawTerminalOutput(markdown))" in response.text
    assert "function reparentCommandOutputRecord" in response.text
    assert "function removeEmptyCommandOutputPanel" in response.text
    assert "function replayCommandOutputRecordsFromTracePayload" in response.text
    assert "function replayTraceConversationArtifacts" in response.text
    replay_artifacts = response.text.split("function replayTraceConversationArtifacts", 1)[1].split(
        "function normalizeTerminalOutputText",
        1,
    )[0]
    assert "handleUserFacingLlmDelta(event)" in replay_artifacts
    assert "function operatorPlanActionsByIdFromTracePayload" in response.text
    assert "function streamingStepIndexForTraceRecord" in response.text
    assert "function operatorExecutionRecordsFromTracePayload" in response.text
    assert "function confirmationActionTaskIds" in response.text
    assert "function cancelStreamingStepTask" in response.text
    assert "function ensureStreamingStepActionsNode" in response.text
    assert "function ensureStreamingStepCancelButton" in response.text
    assert 'actions.className = "streaming-step-actions"' in response.text
    assert ':scope > .streaming-step-status .streaming-step-cancel-button' in response.text
    assert ':scope > .streaming-step-actions .streaming-step-cancel-button' not in response.text
    assert 'button.className = "streaming-step-cancel-button"' in response.text
    assert 'button.textContent = "cancel"' in response.text
    assert "actions.append(button)" in response.text
    assert "status.append(actions)" in response.text
    assert "status.append(button)" not in response.text
    assert 'message.dataset.durableTaskId = durableTaskId' in response.text
    assert '`/api/agent/tasks/${encodeURIComponent(taskId)}/cancel`' in response.text
    assert '`/api/agent/stop/${encodeURIComponent(requestId)}`' in response.text
    assert 'setStreamingStepMessageStatus(message, "cancelled")' in response.text
    assert 'state.streamingStepMessagesByTaskId.set(taskId, message)' in response.text
    assert 'state.streamingStepConfirmationMessagesByTaskId.set(taskId, message)' in response.text
    assert "state.streamingStepOutputPanelsByCommandKey.set(key, panel)" in response.text
    assert "state.commandOutputPanelsByKey.set(key, panel)" in response.text
    assert "const scope = commandStreamStepScope(event);" in response.text
    assert "parts.push(scope);" in response.text
    assert 'return `task:${taskId}`;' in response.text
    assert 'return `step:${rawIndex}`;' in response.text
    assert "reparentCommandOutputRecord(record, event)" in response.text
    assert "function insertStreamingStepCommandOutputPanel" in response.text
    assert "cursor.nextSibling.classList.contains(\"streaming-step-command-output-panel\")" in response.text
    assert "insertStreamingStepCommandOutputPanel(panel, anchor, taskId)" in response.text
    assert "replayCommandOutputRecordsFromTracePayload(payload)" in response.text
    assert "replayTraceConversationArtifacts(payload)" in response.text
    assert '["shell_command", "python_action", "python_transform"]' in response.text
    assert "const actionsById = operatorPlanActionsByIdFromTracePayload(payload)" in response.text
    assert "const streamingStepIndex = streamingStepIndexForTraceRecord(payload, record)" in response.text
    assert (
        "updateVisualizationFromTracePayload(payload);\n"
        "  if (options.prioritizeAutoApproved === true) {\n"
        "    appendAutoApprovedConversationMessagesFromTracePayload(payload);\n"
        "  }\n"
        "  replayTraceConversationArtifacts(payload);\n"
        "  if (options.renderTraceEvents) {\n"
        "    renderTraceEventsFromTracePayload(payload, { focusTrace: options.focusTrace === true });\n"
        "  }\n"
        "  appendResponseMetricsToRenderedRequest(requestId, responseMetrics);\n"
        "  appendLearningSummaryToRenderedRequest(requestId, learningSummary);\n"
        '  const isCancelled = String(payload.status || "") === "cancelled";\n'
        "  const terminalTraceResult = tracePayloadHasTerminalResult(payload, { inputNeeded, isCancelled });\n"
        "  if (\n"
        "    state.finalTraceRendered.has(requestId) &&"
    ) in response.text
    assert "metadata.operator_execution_records" in response.text
    assert "execution_snippet" in response.text
    assert "execution_snippet_language" in response.text
    assert "operation_description" in response.text
    assert 'descriptionText.className = "command-output-description-body"' in response.text
    assert 'description.className = "command-output-description"' in response.text
    assert 'descriptionSummary.textContent = "More details..."' in response.text
    assert 'description.append(descriptionSummary, descriptionText)' in response.text
    assert 'language === "python" ? "Python code"' in response.text
    assert 'panel.className = "message assistant command-output-panel streaming-step-command-output-panel"' in response.text
    assert 'panel.className = "message assistant command-output-panel command-output-record-panel"' in response.text
    assert 'if (!panel.parentNode && anchor)' in response.text
    assert 'card.dataset.outputScope = target.scope' in response.text
    assert "target.container.append(card)" in response.text
    assert "target.container.append(record.card)" in response.text
    assert "mountCommandOutputPanel" not in response.text
    assert "commandOutputListForEvent" not in response.text
    assert "markAgentResponseMessage(commandOutputPanel, requestId)" not in response.text


def test_agent_ui_static_js_links_file_references() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "function linkifyFileReferences" in response.text
    assert "/api/agent/file?path=" in response.text
    assert "class=\"file-link\"" in response.text
    assert "pipeline-label" in response.text
    assert "pipeline-duration" in response.text
    assert '["memory_check", "memory check"]' in response.text
    assert "function formatDuration" in response.text
    assert "function buildVisualizationModel" in response.text
    assert "function buildMermaidSource" in response.text
    assert "function visualizationMermaidConfig" in response.text
    assert "function responseMetricsFromTracePayload" in response.text
    assert "function learningSummaryFromTracePayload" in response.text
    assert "function learningSummaryFromEvents" in response.text
    assert "function mergeLearningSummaries" in response.text
    assert "function learningSummaryFromTraceArtifacts" in response.text
    assert "function learningSummaryArtifactRow" in response.text
    assert "function rememberLearningTracePayload" in response.text
    assert "function rememberLearningEvent" in response.text
    assert "function learningSummaryForRequestChain" in response.text
    assert "function appendLearningSummaryFooter" in response.text
    assert "function appendLearningSummaryToRenderedRequest" in response.text
    assert "function scheduleLearningSummaryFooterRefresh" in response.text
    assert "function setProgressResponseMetrics" not in response.text
    assert "function markAgentResponseMessage" in response.text
    assert "function appendResponseMetricsFooter" in response.text
    assert "function responseFooterForMessage" in response.text
    assert "function setResponseFooterLine" in response.text
    assert "function appendResponseMetricsToRenderedRequest" in response.text
    assert "function scheduleResponseMetricsFooterRefresh" in response.text
    assert "responseMetricsRefreshTimers: new Map()" in response.text
    assert "learningSummaryRefreshTimers: new Map()" in response.text
    assert "value.input_tokens_estimate ?? value.inputTokens" in response.text
    assert "value.duration_ms ?? value.durationMs" in response.text
    assert 'footer.dataset.footerPosition = "bottom"' in response.text
    assert 'setResponseFooterLine(message, "memory", noteText, "memory-applied-note")' in response.text
    assert 'setResponseFooterLine(message, "metrics", text, "response-metrics-line")' in response.text
    assert 'details.dataset.footerLine = "learning-summary"' in response.text
    assert 'appendLearningSummaryFooter(message, memoryContext?.learning_summary)' in response.text
    assert "const learningSummary = learningSummaryForRequestChain(" in response.text
    assert "rememberLearningTracePayload(payload, requestId)" in response.text
    assert "appendLearningSummaryToRenderedRequest(requestId, learningSummary)" in response.text
    assert "scheduleLearningSummaryFooterRefresh(requestId)" in response.text
    assert "rememberLearningEvent(event)" in response.text
    assert "rememberLearningRequestLink(payload.request_id, requestId)" in response.text
    assert "rememberLearningTracePayload(payload, id)" in response.text
    assert "learningSummaryForRequestChain(id, summary)" in response.text
    assert "LR-EX payload-aware command template" in response.text
    assert 'const cacheType = String(detail?.cache_type || "").trim();' in response.text
    assert "Applied LR Direct exact-step replay evidence from execution metadata." in response.text
    assert "Learnings applied - " in response.text
    assert "Learning summary - no learning reused" in response.text
    assert "LR-T applied" in response.text
    assert "LR-T not applied" in response.text
    assert "best ${bestScore} was below the ${threshold} threshold" in response.text
    assert "LR-D applied" in response.text
    assert "LR-D not applied" in response.text
    assert "LRN learned" in response.text
    assert "response-learning-summary-row-ids" not in response.text
    assert 'eventType === "operator.lrt.hit"' in response.text
    assert 'eventType === "operator.lrnt.write"' in response.text
    assert 'eventType === "operator.lrdirect.hit"' in response.text
    assert 'eventType.includes("auto_approved")' in response.text
    assert 'eventType.startsWith("learning.lesson.")' in response.text
    assert "No learning system was reused for this run." in response.text
    assert 'message.querySelector(":scope > .response-metrics-footer")' in response.text
    assert "markAgentResponseMessage(commandOutputPanel, requestId)" not in response.text
    assert "markAgentResponseMessage(panel, event?.request_id || state.requestId)" not in response.text
    assert 'const progressResponseMetrics = document.querySelector("#progress-response-metrics")' not in response.text
    assert 'message.dataset.agentResponse = "true"' in response.text
    assert ".message.streaming-step-message" in response.text
    assert 'appendResponseMetricsFooter(message, memoryContext?.response_metrics)' in response.text
    assert "appendResponseMetricsToRenderedRequest(requestId, responseMetrics)" in response.text
    assert "responseMetricsFooterText(normalizeResponseMetrics(chat.response_metrics))" in response.text
    assert "Tokens in/out:" in response.text
    assert "function updateVisualizationFromEvent" in response.text
    assert "function updateVisualizationFromTracePayload" in response.text
    assert "function setVisualizationVisible" in response.text
    assert "function setVisualizationCollapsed" in response.text
    assert "function setVisualizationLive" in response.text
    assert "function setVisualizationDirection" in response.text
    assert "function toggleVisualizationDirection" in response.text
    assert "function normalizeVisualizationDirection" in response.text
    assert "function setVisualizationZoom" in response.text
    assert "function adjustVisualizationZoom" in response.text
    assert "function handleVisualizationWheelZoom" in response.text
    assert "function setVisualizationInverted" in response.text
    assert "function setVisualizationDetailsCollapsed" in response.text
    assert "function startVisualizationPan" in response.text
    assert "function moveVisualizationPan" in response.text
    assert "function endVisualizationPan" in response.text
    assert 'grid.className = "visualization-detail-grid"' in response.text
    assert 'item.className = "visualization-detail-pair"' in response.text
    assert "openfabric.agentUi.visualizationVisible" in response.text
    assert "openfabric.agentUi.visualizationDirection" in response.text
    assert "openfabric.agentUi.visualizationZoom" in response.text
    assert "openfabric.agentUi.visualizationInverted" in response.text
    assert "openfabric.agentUi.visualizationDetailsCollapsed" in response.text
    assert "visualizationZoom: storedNumber(uiPreferences.visualizationZoom, 1)" in response.text
    assert "visualizationInverted: storedBoolean(uiPreferences.visualizationInverted, false)" in response.text
    assert "visualizationDetailsCollapsed: storedBoolean(uiPreferences.visualizationDetailsCollapsed, false)" in response.text
    assert 'visualizationDirection: storedString(uiPreferences.visualizationDirection, "TD")' in response.text
    assert "openfabric.agentUi.tracePaneWidthPx" in response.text
    assert 'const mapBg = rootCssVariable("--trace-panel", "#f6f8fa")' in response.text
    assert 'const panelBg = rootCssVariable("--panel", "#ffffff")' in response.text
    assert "background: mapBg" in response.text
    assert "mainBkg: panelBg" in response.text
    assert "edgeLabelBackground: panelBg" in response.text
    assert 'const headerBg = rootCssVariable("--command-output-header-bg", "#f6f8fa")' in response.text
    assert "clusterBkg: headerBg" in response.text
    assert "clusterBorder: headerBorder" in response.text
    assert "clusterTextColor: text" in response.text
    assert "titleColor: text" in response.text
    assert "window.mermaid.__openfabricThemeSignature" in response.text
    assert "scheduleVisualizationRender({ force: true })" in response.text
    assert 'wirePaneCopyButton(visualizationCopyButton, "visualization")' in response.text
    assert 'wirePaneCopyButton(visualizationDetailCopyButton, "visualization-details")' in response.text
    assert 'visualizationCanvas?.addEventListener("wheel", handleVisualizationWheelZoom, { passive: false })' in response.text
    assert "setVisualizationInverted(!state.visualizationInverted)" in response.text
    assert 'visualizationCanvas?.addEventListener("pointerdown", startVisualizationPan)' in response.text
    assert 'visualizationCanvas?.addEventListener("pointermove", moveVisualizationPan)' in response.text
    assert 'visualizationCanvas?.addEventListener("pointerup", endVisualizationPan)' in response.text
    assert 'visualizationDetailHeader?.addEventListener("click"' in response.text
    assert 'visualizationDetailHeader?.addEventListener("keydown"' in response.text
    assert "setVisualizationDetailsCollapsed(!state.visualizationDetailsCollapsed)" in response.text
    assert "setVisualizationZoom(state.visualizationZoom, { persist: false })" in response.text
    assert "const flowDirection = visualizationFlowDirection()" in response.text
    assert 'flowchart ${flowDirection}' in response.text
    assert 'return String(direction || "").toUpperCase() === "LR" ? "LR" : "TD"' in response.text
    assert 'const subgraphDirection = flowDirection === "LR" ? "LR" : "TB"' in response.text
    assert "window.mermaid.render" in response.text
    assert "Mermaid renderer unavailable; showing source." in response.text
    assert "clarification resume" in response.text
    assert "approval resume" in response.text
    assert 'chromeOnlyTitles = new Set(["Request", "Follow-up", "Response", "Final response", "Agent", "Agent Response"])' in response.text
    assert "function appendFinalResultCopyAction" in response.text
    assert "Copy final result" in response.text
    assert "appendFinalResultCopyAction(message, body)" in response.text
    assert "function stripAssistantResponseChrome" in response.text
    assert "stripAssistantResponseChrome(content)" in response.text
    assert "prefixPattern" in response.text
    assert "buttonCanCarryMemory" not in response.text
    assert 'conversationMemoryButton.textContent = `${badgeText} Memory`' in response.text
    assert 'heading.textContent = "Response"' not in response.text
    assert 'heading.textContent = "Final response"' not in response.text
    assert 'const conversationMemoryButton = document.querySelector("#conversation-memory-button")' in response.text
    assert 'const conversationMemoryAppliedBadge = document.querySelector("#conversation-memory-applied-badge")' in response.text
    assert 'const conversationCacheAppliedBadge = document.querySelector("#conversation-cache-applied-badge")' in response.text
    assert 'const conversationStreamingBadge = document.querySelector("#conversation-streaming-badge")' in response.text
    assert 'const settingWorkspaceCwdGuard = document.querySelector("#setting-workspace-cwd-guard")' in response.text
    assert "streamingStepRendered: new Set()" in response.text
    assert "streamingStepMessages: new Map()" in response.text
    assert "promptRephraseRendered: new Set()" in response.text
    assert "autoRephraseRetryRendered: new Set()" in response.text
    assert "memoryDigestRendered: new Set()" in response.text
    assert "onlineAiCheckMessages: new Map()" in response.text
    assert "function appendStreamingStepConversationMessage" in response.text
    assert "function updateStreamingStepConversationStatus" in response.text
    assert "function setStreamingStepMessageStatus" in response.text
    assert "function streamingStepTitle" in response.text
    assert "`Streaming step ${rawIndex + 1} / ${stepCount}`" in response.text
    assert "function appendAutoRephraseRetryConversationMessage" in response.text
    assert "function appendPromptRephraseConversationMessage" in response.text
    assert "function appendPromptRephraseMessagesFromTracePayload" in response.text
    assert "function appendAutoRephraseRetryMessagesFromTracePayload" in response.text
    assert "function appendConversationArtifactNode" in response.text
    assert "const replayedCommandOutputKeys = new Set(state.commandOutputs.keys())" in response.text
    replay_start = response.text.index("function replayTraceConversationArtifacts")
    assert response.text.index("appendPromptRephraseConversationMessage(event)", replay_start) < response.text.index(
        "appendAutoRephraseRetryConversationMessage(event)",
        replay_start,
    )
    assert response.text.index("appendAutoRephraseRetryConversationMessage(event)", replay_start) < response.text.index(
        "replayCommandOutputRecordsFromTracePayload(payload)",
        replay_start,
    )
    assert "replayed_from_trace: true" in response.text
    assert "function appendMemoryDigestConversationMessage" in response.text
    assert "function appendMemoryDigestMessagesFromTracePayload" in response.text
    assert "function appendLearningLessonConversationMessage" in response.text
    assert "function appendLearningLessonMessagesFromTracePayload" in response.text
    assert "function appendOrUpdateOnlineAiCheckConversationMessage" in response.text
    assert "function appendOnlineAiCheckMessagesFromTracePayload" in response.text
    assert "function onlineLookupMemoryFeedback" in response.text
    assert "function ensureOnlineLookupMemoryActions" in response.text
    assert '"operator.auto_rephrase_retry"' in response.text
    assert '"operator.memory_digest.proposed"' in response.text
    assert '"learning.lesson.proposed"' in response.text
    assert '"operator.online_ai_lookup."' in response.text
    assert '"operator.online_lookup."' in response.text
    assert '"prompt_rephrase.applied"' in response.text
    assert '"Operator auto rephrase retry started"' in response.text
    assert '"Prompt rephrased"' in response.text
    assert "detail.rephrased_prompt" in response.text
    assert 'message.className = "message user rephrase-retry-message"' in response.text
    assert 'message.className = "message assistant rephrase-retry-message"' in response.text
    assert 'message.className = "message assistant memory-digest-message"' in response.text
    assert 'message.className = "message assistant learning-lesson-message"' in response.text
    assert 'message.className = "message assistant online-ai-check-message"' in response.text
    assert 'heading.append(document.createTextNode("Rewritten request"))' in response.text
    assert 'heading.append(document.createTextNode("Rephrased retry"))' in response.text
    assert 'appendRephraseRetryBadge(heading, "Rewritten request")' in response.text
    assert 'heading.append(document.createTextNode("Memory digest proposed"))' in response.text
    assert "onlineLookupTitle(eventType, detail)" in response.text
    assert 'badge.textContent = "R"' in response.text
    assert 'badge.textContent = "M"' in response.text
    assert "function onlineLookupBadgeText" in response.text
    assert "badge.textContent = label" in response.text
    assert 'review.textContent = "Review memory"' in response.text
    assert 'remember.textContent = "Remember"' in response.text
    assert 'save.textContent = "Save to memory"' in response.text
    assert "void commitInlineMemoryFeedback(feedback, context, status, actions)" in response.text
    assert "appendPromptRephraseMessagesFromTracePayload(payload)" in response.text
    assert "appendAutoRephraseRetryMessagesFromTracePayload(payload)" in response.text
    assert "appendMemoryDigestMessagesFromTracePayload(payload)" in response.text
    assert "appendLearningLessonMessagesFromTracePayload(payload)" in response.text
    assert "appendOnlineAiCheckMessagesFromTracePayload(payload)" in response.text
    assert "appendOrUpdateOnlineAiCheckConversationMessage(event)" in response.text
    assert "appendMemoryDigestConversationMessage(event)" in response.text
    assert "appendLearningLessonConversationMessage(event)" in response.text
    assert "function renderStreamingCompletionMarkdown" in response.text
    assert "function stripStreamingStepNumberLabels" in response.text
    assert "function streamingCompletionRows" in response.text
    assert "function dedupeStreamingCompletionRows" in response.text
    assert "function streamingCompletionResultChunks" in response.text
    assert "function streamingCompletionCanonicalJsonKey" in response.text
    assert "function streamingCompletionChunksSimilar" in response.text
    assert "function streamingCompletionDiceSimilarity" in response.text
    assert "function streamingCompletionRawPreferenceScore" in response.text
    assert "function normalizeStreamingCompletionAdjacentJson" in response.text
    assert "function scheduleChatAutoScroll" in response.text
    assert "function installChatAutoScrollObserver" in response.text
    assert "new MutationObserver(() => scheduleChatAutoScroll())" in response.text
    assert "function removeFlattenedDuplicateStreamingLines" in response.text
    assert "function preferStreamingCompletionChunkText" in response.text
    assert "function renderStreamingCompletionRow" in response.text
    assert 'class="streaming-complete-grid"' in response.text
    assert 'role="columnheader">Step' in response.text
    assert 'role="columnheader">Status' in response.text
    assert 'role="columnheader">Result' in response.text
    assert '"Streaming operator step started"' in response.text
    assert '"Streaming operator step completed"' in response.text
    assert '"Streaming operator step failed"' in response.text
    assert 'addMessage("user", title, description)' in response.text
    assert "function renderOperatorCacheStats" in response.text
    assert "operator-cache-card" in response.text
    assert "Accepted uses" in response.text
    assert "function setLatestMemoryContext" in response.text
    assert "function appliedMemoryFromTracePayload" in response.text
    assert "function appliedCacheFromTracePayload" in response.text
    assert "function setConversationCacheApplied" in response.text
    assert "function updateAppliedCacheFromEvent" in response.text
    assert "`cachehit ${hitCount}x`" in response.text
    assert "💾" not in response.text
    assert "function appendAppliedMemoryNote" in response.text
    assert "appliedMemoryNoticeRendered: new Set()" in response.text
    assert "function appendAppliedMemoryConversationMessage" in response.text
    assert "function appendMessageInlineBadge" in response.text
    assert 'label: itemCount > 1 ? `M${itemCount}` : "M"' in response.text
    assert "title: `Memory applied: ${parts.join" in response.text
    assert 'className: "memory-applied-inline-badge"' in response.text
    assert "function updateAppliedMemoryFromEvent" in response.text
    assert "memory_use_count" in response.text
    assert "conversationMemoryAppliedBadge.hidden = false" not in response.text
    assert 'conversationMemoryAppliedBadge.setAttribute("aria-hidden", "false")' not in response.text
    assert "conversationMemoryAppliedBadge.hidden = true" in response.text
    assert "`🧠 ${useCountValue}x`" in response.text
    assert "updateAppliedMemoryFromEvent(event)" in response.text
    assert "appendAppliedMemoryConversationMessage(event)" in response.text
    assert "function appendMemoryModifyAction" not in response.text
    assert 'fetchWithLlmActivity("/api/agent/memory/context"' in response.text
    assert "function renderLlmActivityIndicator" in response.text
    assert "function updateLlmActivityFromEvent" in response.text
    assert "function fetchWithLlmActivity" in response.text
    assert 'eventType === "llm.request"' in response.text
    assert 'eventType === "llm.response" || eventType === "llm.error"' in response.text
    assert "function setMemoryDrawerOpen" in response.text
    assert "function renderMemoryStats" in response.text
    assert "function renderMemoryPicker" in response.text
    assert "memoryPickerEntries" in response.text
    assert 'const memoryStats = document.querySelector("#memory-stats")' in response.text
    assert 'const memoryEntryPicker = document.querySelector("#memory-entry-picker")' in response.text
    assert "function setupDrawerResize" in response.text
    assert "openfabric.agentUi.settingsDrawerWidthPx" in response.text
    assert "openfabric.agentUi.memoryDrawerWidthPx" in response.text
    assert "function llmRuntimeTerminalCellWidth" not in response.text
    assert "llmRuntimeTerminalCollapsed" not in response.text
    assert "function setLlmRuntimeTerminalCollapsed" not in response.text
    assert 'llmRuntimeTerminalHeader?.addEventListener("click"' not in response.text
    assert "function showInlineMemoryEditor" in response.text
    assert "function commitInlineMemoryFeedback" in response.text
    assert "memoryFeedbackRequestTimeoutMs = 30000" in response.text
    assert "function memoryFeedbackEndpointContext" in response.text
    assert "llm_base_url: normalizeAgentSettings(state.agentSettings).llm_base_url" in response.text
    assert "function postMemoryFeedbackJson" in response.text
    assert "feedback request timed out" in response.text
    assert "function setInlineMemoryEditorDisabled" in response.text
    assert "function refreshMemoryAfterFeedback" in response.text
    assert "Memory save failed: ${shortStatusError(error)}" in response.text
    assert "proposal${failedApplyCount === 1 ? \"\" : \"s\"} failed" in response.text
    assert "function appendRunFeedbackAction" in response.text
    assert "function buildRunFeedbackObject" in response.text
    assert "function draftInlineMemoryFeedback" in response.text
    assert "Auto draft failed: ${shortStatusError(error)}" in response.text
    assert "run_feedback: context?.run_feedback || null" in response.text
    assert 'postMemoryFeedbackJson(\n      "/api/agent/memory/feedback/draft"' in response.text
    assert "Run feedback" in response.text
    assert "Continue from failure" in response.text
    assert "/api/agent/continue/" in response.text
    assert "metadata.operator_failure_continuation" in response.text
    assert "metadata.operator_streaming_state" in response.text
    assert "streamingState.prior_results" in response.text
    assert "Auto generate" in response.text
    assert "Modify Validation" in response.text
    assert "Tell the agent how this validation should behave next time." in response.text
    assert 'postMemoryFeedbackJson(\n      "/api/agent/memory/feedback"' in response.text
    assert 'const memoryToggle = document.querySelector("#memory-toggle")' in response.text
    assert 'const memoryDrawer = document.querySelector("#memory-drawer")' in response.text
    assert 'const gatewayToggle = document.querySelector("#gateway-toggle")' in response.text
    assert 'const gatewayDrawer = document.querySelector("#gateway-drawer")' in response.text
    assert 'const immersiveGatewaySelect = document.querySelector("#immersive-gateway-select")' in response.text
    assert "function setSelectedGatewayFromPicker" in response.text
    assert 'const conversationCollapseAllButton = document.querySelector("#conversation-collapse-all-button")' in response.text
    assert 'const conversationImmersiveToggle = document.querySelector("#conversation-immersive-toggle")' in response.text
    assert 'immersiveMode: "openfabric.agentUi.immersiveMode"' in response.text
    assert "requestAutoApproveIds: new Set()" in response.text
    assert "function parseRequestIntents" in response.text
    assert "requestContext.auto_approve_scope = \"request\"" in response.text
    assert "state.requestAutoApproveIds.add(payload.request_id)" in response.text
    assert "state.requestAutoApproveIds.has(requestId)" in response.text
    assert "function appendAutoApprovedConversationMessage" in response.text
    assert "Auto approved" in response.text
    assert "if (requestScopedAutoApprove && payload.request_id)" in response.text
    assert "if (requestScopedAutoApprove && responsePayload.request_id)" in response.text
    assert "function setImmersiveMode" in response.text
    assert "const DEFAULT_AUTO_IMMERSIVE_MIN_WIDTH_PX = 550" in response.text
    assert 'const settingAutoImmersiveMinWidth = document.querySelector("#setting-auto-immersive-min-width")' in response.text
    assert "ui_auto_immersive_min_width_px" in response.text
    assert "function syncResponsiveImmersiveMode" in response.text
    assert "function installResponsiveImmersiveObserver" in response.text
    assert "setImmersiveMode(true, { persist: false, automatic: true })" in response.text
    assert "state.autoImmersiveSuppressed = true" in response.text
    assert 'document.body.classList.toggle("immersive-mode", state.immersiveMode)' in response.text
    assert 'conversationImmersiveToggle?.addEventListener("change"' in response.text
    assert 'const terminalCollapseAllButton = document.querySelector("#terminal-collapse-all-button")' in response.text
    assert 'const traceCollapseAllButton = document.querySelector("#trace-collapse-all-button")' in response.text
    assert 'const visualizationCollapseAllButton = document.querySelector("#visualization-collapse-all-button")' in response.text
    assert "function collapseAllPanes" in response.text
    assert "setLlmStreamCollapsed(true)" in response.text
    assert "function loadGateways" in response.text
    assert "function selectedGatewayContext" in response.text
    assert "function gatewayRoutingPreview" in response.text
    assert "function gatewayPlatformLabel" in response.text
    assert "function gatewayPlatformSummary" in response.text
    assert "function appendGatewayRoutingFooter" in response.text
    assert "gateway_routing: routingPreview.metadata" in response.text
    assert '["gateway_platform", gateway.platform]' in response.text
    assert 'metadata.gateway_platform_label = gatewayPlatformLabel(gateway);' in response.text
    assert 'return `Gateway: ${[displayName, platformLabel].filter(Boolean).join(" · ")}`' in response.text
    assert "Gateway selected for this request." in response.text
    assert 'fetch("/api/agent/gateways"' in response.text
    assert 'const eventsToggle = document.querySelector("#events-toggle")' in response.text
    assert 'const eventsDrawer = document.querySelector("#events-drawer")' in response.text
    assert 'const quickUpcomingEventsList = document.querySelector("#quick-upcoming-events-list")' in response.text
    assert "function setEventsDrawerOpen" in response.text
    assert "openfabric.agentUi.chatHistoryDrawerOpen" in response.text
    assert "persistBoolean(uiPreferences.chatHistoryDrawerOpen, state.chatHistoryOpen)" in response.text
    assert "openfabric.agentUi.detailsOpen" in response.text
    assert "function restorePersistentDetailsState" in response.text
    assert "function syncDrawerCollapseButtons" in response.text
    assert "function setDrawerSectionsCollapsed" in response.text
    assert "wireDrawerCollapseButton(settingsCollapseAllButton, settingsDrawer)" in response.text
    assert "wireDrawerCollapseButton(tasksCollapseAllButton, tasksDrawer)" in response.text
    assert "wireDrawerCollapseButton(monitorsCollapseAllButton, monitorsDrawer)" in response.text
    assert "details.settings-collapsible-section" in response.text
    assert 'details.addEventListener("toggle"' in response.text
    assert "function setAgentEventsEnabled" in response.text
    assert "function maybeDraftScheduledEvents" in response.text
    assert "function appendEventDraftMessage" in response.text
    assert "function defaultEventWorkingDirectory" in response.text
    assert 'const eventSelectedSummary = document.querySelector("#event-selected-summary")' not in response.text
    assert 'const eventEditorCard = document.querySelector("#event-editor-card")' in response.text
    assert 'const eventListCard = document.querySelector("#event-list-card")' in response.text
    assert 'const eventRunsCard = document.querySelector("#event-runs-card")' in response.text
    assert 'const tasksDrawerResizer = document.querySelector("#tasks-drawer-resizer")' in response.text
    assert 'const taskDetails = document.querySelector("#task-details")' in response.text
    assert 'state.taskFilter === "archived"' in response.text
    assert "function selectTask" in response.text
    assert "function renderTaskDetails" in response.text
    assert "function loadSelectedTaskDetails" in response.text
    assert "function openTaskRequestTrace" in response.text
    assert "function taskSourceLabel" in response.text
    assert 'if (status === "deleted") return "event deleted"' in response.text
    assert 'if (sourceEvent.exists === false || status === "missing") return "event missing"' in response.text
    assert "function clarificationExpectsCredential" in response.text
    assert "const credentialClarification = clarificationExpectsCredential(request)" in response.text
    assert "const parameterChoices = credentialClarification && Array.isArray(request.parameter_choices)" in response.text
    assert 'input.type = credentialClarification ? "password" : "text"' in response.text
    assert 'input.placeholder = parameterChoices.length' in response.text
    assert '"Type your answer"' in response.text
    assert 'title: "Loading task trace"' in response.text
    assert 'detail: "Fetching full saved task trace..."' in response.text
    assert "renderTraceEvents = true" in response.text
    assert "focusTrace = true" in response.text
    assert "renderTraceEvents ? \"Rendering task trace...\" : \"Rendering task chat...\"" in response.text
    assert 'traceLink.className = "event-row-trace event-row-control"' in response.text
    assert 'traceLink.dataset.taskAction = "open"' in response.text
    assert 'followButton.className = "event-row-live event-row-control"' in response.text
    assert 'followButton.dataset.taskAction = "follow"' in response.text
    assert '["Request", latestRequestId || "-", { requestId: latestRequestId }]' in response.text
    assert 'link.className = "event-row-value-link event-row-control"' in response.text
    assert 'cancelButton.className = "event-row-cancel event-row-control"' in response.text
    assert 'cancelButton.dataset.taskAction = "cancel"' in response.text
    assert 'cancelButton.disabled = !taskCanCancel(task)' in response.text
    assert 'void taskAction(String(task?.task_id || ""), "cancel")' in response.text
    assert 'archiveButton.className = "event-row-archive event-row-control"' in response.text
    assert 'archiveButton.dataset.taskAction = "archive"' in response.text
    assert 'void taskAction(String(task?.task_id || ""), "archive")' in response.text
    assert 'const taskDeleteButton = document.querySelector("#task-delete-button")' in response.text
    assert 'const tasksClearArchivedButton = document.querySelector("#tasks-clear-archived-button")' in response.text
    assert 'await deleteTask(id);' in response.text
    assert 'fetch(`/api/agent/tasks/${encodeURIComponent(id)}`, { method: "DELETE" })' in response.text
    assert 'fetch("/api/agent/tasks?status=archived&limit=500&include_archived=true"' in response.text
    assert "details.task-detail-row" in response.text
    assert "details.monitor-row" in response.text
    assert "details.monitor-detail-row" in response.text
    assert "state.taskDetailsExpanded = !collapsed" in response.text


def test_agent_ui_static_js_auto_attaches_open_durable_tasks() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    script = response.text
    assert "autoAttachedTaskRequestIds: new Set()" in script
    assert 'attachedTaskId: ""' in script
    assert "durableApprovalContinuingParentRequestIds: new Set()" in script
    assert "durableApprovalAttachingRequestIds: new Set()" in script
    assert "function notifyQueuedTaskForAutoAttach" in script
    assert "function autoAttachOpenDurableTask" in script
    assert "function syncAttachedDurableTaskRequest" in script
    assert "function attachDurableTaskRequest" in script
    assert "function fetchTaskTracePayload" in script
    assert "async function fetchDurableTaskRecord" in script
    assert "function durableTaskIdFromTracePayload" in script
    assert "function tracePayloadParentRequestId" in script
    assert "function tracePayloadHasAutoApprovalEvent" in script
    assert "function appendAutoApprovedConversationMessagesFromTracePayload" in script
    assert "async function renderParentTraceBeforeContinuation" in script
    assert "async function restoreDurableTaskContinuationFromTracePayload" in script
    assert "async function attachDurableApprovalContinuation" in script
    assert "function startDurableTaskTraceStream" in script
    assert "function scheduleDurableTaskStreamSync" in script
    assert "function canAutoAttachTaskRequest" in script
    assert "function composerHasActiveRun" in script
    assert "function composerCanSubmit" in script
    assert "function syncComposerSubmitButton" in script
    assert "function setComposerPreparing" in script
    assert "function setRunStatusFromTracePayload" in script
    assert "function composerCanSubmit()" in script
    assert "return state.composerPreparing !== true;" in script
    assert "const activeWork = composerHasActiveWork();" in script
    assert "submitButton.disabled = preparing" in script
    assert "function composerHasActiveDurableTask" in script
    assert "function composerHasActiveWork" in script
    assert "function notifyRunBlockedByActiveWork" in script
    assert 'submitButton.setAttribute("aria-label", "Run request")' in script
    assert "Run live only. A task is already running or queued; use Queue to add this prompt to the queue." in script
    assert "A task is already running. Please hit Queue to queue this prompt, or wait until it completes." in script
    assert "queueButton.disabled = preparing" in script
    assert "const queueSubmission = Boolean(queue);" in script
    assert "if (!queue && composerHasActiveWork())" in script
    assert "if (!composerCanSubmit())" in script
    assert 'submitButtonLabel.textContent = "Run"' in script
    assert 'queueButtonLabel.textContent = "Queue"' in script
    assert 'queueButton.setAttribute("aria-label", "Queue request")' in script
    assert "findRenderedRequestAction(state.requestId)" not in script
    load_tasks = script.split("async function loadTasks()", 1)[1].split("async function openTaskRequestTrace", 1)[0]
    assert "const attached = await syncAttachedDurableTaskRequest(state.tasks);" in load_tasks
    assert "await autoAttachOpenDurableTask(state.tasks);" in load_tasks
    assert "syncComposerSubmitButton();" in load_tasks
    can_auto_attach = script.split("function canAutoAttachTaskRequest", 1)[1].split(
        "async function attachDurableTaskRequest",
        1,
    )[0]
    assert "const replaceableWaitingRequest = Boolean(" in can_auto_attach
    assert "const replaceableWaitingSnapshot = Boolean(" in can_auto_attach
    assert "composerHasActiveRun() && !replaceableWaitingRequest && !replaceableWaitingSnapshot" in can_auto_attach
    sync_attached = script.split("async function syncAttachedDurableTaskRequest", 1)[1].split(
        "async function autoAttachOpenDurableTask",
        1,
    )[0]
    assert "!state.terminalTraceRendered.has(requestId)" in sync_attached
    assert "state.durableApprovalContinuingParentRequestIds.has(activeRequestId)" in sync_attached
    assert "state.durableApprovalAttachingRequestIds.has(requestId)" in sync_attached
    assert "if (state.source) {\n        state.source.close();\n        state.source = null;" in sync_attached
    assert "setStopEnabled(false);" in sync_attached
    assert "async function queueComposerPromptAsTask" in script
    assert "async function submitComposerPromptLive" in script
    assert "function followLiveTaskRequest" in script
    assert "followLiveTaskRequest(requestId, taskStreamUrl(task));" in script
    assert 'title: "Task queued"' in script
    assert 'title: "Task started"' in script
    assert 'follow.textContent = "Follow live"' in script
    assert "await fetch(`/api/agent/trace/${encodeURIComponent(requestId)}`" in script
    assert "afterId: lastTraceEventId(payload)" in script
    assert "suppressAutoApprove: taskAutoApprove" in script
    assert "function taskTraceLoadOptions" in script
    assert "function attachedTaskAutoApproveExpectedForRequest" in script
    assert "retireTaskAutoApproval: taskAutoApprove" in script
    assert "appendAutoApprovedConversationMessage(event)" in script
    assert 'eventType === "confirmation.approved" && detail.auto_approved === true' in script
    assert "responseStatus === 409 && /already handled/i.test(message)" in script
    assert "Task auto-approval is continuing this run in the background." in script
    assert "scheduleDurableTaskStreamSync(requestId, { immediate: true })" in script
    assert "restoreDurableTaskContinuationFromTracePayload(requestId, payload, {" in script
    assert "preserveConversation: true" in script
    assert "scheduleDurableTaskStreamSync(event.request_id, { immediate: true })" in script
    stream_helper = script.split("function startDurableTaskTraceStream", 1)[1].split(
        "function taskAutoApproveExpected",
        1,
    )[0]
    assert "startTraceStream(safeRequestId, taskStreamUrlForRequest(task, safeRequestId), {" in stream_helper
    assert "afterId: lastTraceEventId(payload)" in stream_helper
    assert "resetActivity: false" in stream_helper
    attach_task = script.split("async function attachDurableTaskRequest", 1)[1].split(
        "async function syncAttachedDurableTaskRequest",
        1,
    )[0]
    assert "preserveConversation = false" in attach_task
    assert "const shouldPreserveConversation =" in attach_task
    assert "durableTaskAttachShouldPreserveConversation(task, safeRequestId, payload, effectiveParentRequestId)" in attach_task
    assert "if (!shouldPreserveConversation) {\n    resetView({ clearChat: true, resetConversation: false });" in attach_task
    assert "payload?.prompt && !shouldPreserveConversation" in attach_task
    assert "const continuationReplay = Boolean(tracePayloadParentRequestId(payload));" in attach_task
    assert "await renderParentTraceBeforeContinuation(task, safeRequestId, payload);" in attach_task
    assert "let loadedTracePayload = payload;" in attach_task
    assert "prioritizeAutoApproved: continuationReplay || tracePayloadHasAutoApprovalEvent(payload)" in attach_task
    assert "loadedTracePayload = tracePayload || payload;" in attach_task
    assert "await loadFinalTrace(\n      safeRequestId" in attach_task
    assert "startDurableTaskTraceStream(task, safeRequestId, loadedTracePayload)" in attach_task
    assert "replayRetainedRunningTraceEvents(payload)" not in attach_task
    assert "attachDurableTaskRequest(task, requestId, payload, { preserveConversation: true })" in sync_attached
    load_final_trace = script.split("async function loadFinalTrace", 1)[1].split(
        "function startTraceStream",
        1,
    )[0]
    assert "attachedTaskAutoApproveExpectedForRequest(requestId)" in load_final_trace
    assert "!taskAutoApprovalForTrace" in load_final_trace
    assert "const terminalTraceResult = tracePayloadHasTerminalResult(payload, { inputNeeded, isCancelled });" in load_final_trace
    assert "(!terminalTraceResult || state.terminalTraceRendered.has(requestId))" in load_final_trace
    assert "state.terminalTraceRendered.add(requestId)" in load_final_trace
    assert "clearActiveRequestSnapshot();" in load_final_trace
    assert "options.suppressTaskContinuationFollow !== true" in load_final_trace
    assert "options.prioritizeAutoApproved === true" in load_final_trace
    assert "appendAutoApprovedConversationMessagesFromTracePayload(payload);" in load_final_trace
    assert "{ requestId }" in load_final_trace
    assert "await loadFinalTrace(requestId)" in script
    restore_active_request = script.split("async function restoreActiveRequestAfterReload", 1)[1].split(
        "function resetView",
        1,
    )[0]
    assert "restoreDurableTaskContinuationFromTracePayload(requestId, payload)" in restore_active_request
    restore_continuation = script.split("async function restoreDurableTaskContinuationFromTracePayload", 1)[1].split(
        "function notifyQueuedTaskForAutoAttach",
        1,
    )[0]
    assert "const taskId = durableTaskIdFromTracePayload(payload)" in restore_continuation
    assert "const task = await fetchDurableTaskRecord(taskId)" in restore_continuation
    assert "options = {}" in restore_continuation
    assert 'parentRequestId: String(parentRequestId || "").trim()' in restore_continuation
    parent_handoff = script.split("async function renderParentTraceBeforeContinuation", 1)[1].split(
        "async function restoreDurableTaskContinuationFromTracePayload",
        1,
    )[0]
    assert "tracePayloadParentRequestId(payload)" in parent_handoff
    assert "await loadFinalTrace(parentRequestId, {" in parent_handoff
    assert "renderTraceEvents: false" in parent_handoff
    assert "suppressTaskContinuationFollow: true" in parent_handoff
    approval_click = script.split("async function handleConfirmationAction", 1)[1].split(
        "async function handleClarificationAnswer",
        1,
    )[0]
    assert "if (await attachDurableApprovalContinuation(requestId, payload, { autoApproved }))" in approval_click
    assert "startTraceStream(payload.request_id, payload.stream_url);" in approval_click
    assert "setComposerPreparing(true)" not in approval_click
    assert "state.durableApprovalContinuingParentRequestIds.add(requestId)" in approval_click
    assert "state.durableApprovalContinuingParentRequestIds.delete(requestId)" in approval_click
    assert "let response = null;" in approval_click
    durable_approval = script.split("async function attachDurableApprovalContinuation", 1)[1].split(
        "function notifyQueuedTaskForAutoAttach",
        1,
    )[0]
    assert "state.durableApprovalAttachingRequestIds.add(childRequestId)" in durable_approval
    assert "state.durableApprovalAttachingRequestIds.delete(childRequestId)" in durable_approval
    assert "if (!childRequestId || !state.attachedTaskId)" in durable_approval
    assert "task = rememberTaskRecord({" in durable_approval
    assert "await fetchTaskTracePayload(childRequestId)" in durable_approval
    assert 'setRunStatus("following task", "running");' in durable_approval
    assert "return attachDurableTaskRequest(task, childRequestId, tracePayload, {" in durable_approval
    assert "preserveConversation: true" in durable_approval
    assert "parentRequestId" in durable_approval
    assert "const tasksStartup = loadTasks();" in script
    assert "state.taskPollTimer = window.setInterval(() => {\n  void loadTasks();" in script
    assert "notifyQueuedTaskForAutoAttach(payload.task, { source: \"chat\" })" in script
    assert "notifyQueuedTaskForAutoAttach(payload.task, { source: \"task\" })" in script
    submit_handler = script.split("async function handleComposerSubmit", 1)[1].split(
        'promptInput.addEventListener("keydown"',
        1,
    )[0]
    assert "queueComposerPromptAsTask" in submit_handler
    assert "submitComposerPromptLive" in submit_handler
    queue_handler = script.split("async function queueComposerPromptAsTask", 1)[1].split(
        "async function submitComposerPromptLive",
        1,
    )[0]
    assert "start_now: false" in queue_handler
    assert 'fetch("/api/agent/request"' in script
    assert "pendingTaskFollowIds" not in script
    assert "state.monitorDetailsExpanded = !collapsed" in response.text
    assert "function openEventEditorPanel" in response.text
    assert "function selectEvent" in response.text
    assert "function editSelectedEvent" in response.text
    assert "function updateEventControls" in response.text
    assert 'eventSaveButton.textContent = "Save"' in response.text
    assert 'const eventGateway = document.querySelector("#event-gateway")' in response.text
    assert 'const eventTerminalCwd = document.querySelector("#event-terminal-cwd")' in response.text
    assert "function renderEventGatewaySelect" in response.text
    assert "function eventGatewayContext" in response.text
    assert '["Gateway", eventGatewayLabelFromContext(event.context)]' in response.text
    assert "function eventTimeUntilInfo" in response.text
    assert "function eventNextInvocationBadgeText" in response.text
    assert "function refreshEventCountdowns" in response.text
    assert "function upcomingEvents" in response.text
    assert "function renderQuickUpcomingEvents" in response.text
    assert "upcomingEvents(5)" in response.text
    assert "delete requestPayload.schedule_type" in response.text
    assert "expandedEventIds: new Set()" in response.text
    assert "eventCountdownTimer: null" in response.text
    assert "hasTypeinSummary" in response.text
    assert '`${draftPrompt} /typein "[redacted]"`' in response.text
    assert 'const row = document.createElement("details")' in response.text
    assert "row.open = state.expandedEventIds.has(eventId)" in response.text
    assert 'const header = document.createElement("summary")' in response.text
    assert 'header.className = "event-row-header"' in response.text
    assert 'clickEvent.target.closest(".event-row-control")' in response.text
    assert 'titleGroup.className = "event-row-title-group"' in response.text
    assert 'nextBadge.className = "event-row-next-badge"' in response.text
    assert 'nextBadge.dataset.eventCountdownMode = "badge"' in response.text
    assert 'actions.className = "event-row-actions"' in response.text
    assert 'toggleLabel.className = "event-row-toggle event-row-control"' in response.text
    assert 'toggle.type = "checkbox"' in response.text
    assert 'toggleText.className = "event-row-toggle-text"' in response.text
    assert 'void updateEventStatus(event.event_id, toggle.checked ? "active" : "paused")' in response.text
    assert 'deleteButton.className = "event-row-delete event-row-control"' in response.text
    assert 'void deleteEvent(event.event_id)' in response.text
    assert '["Due in", eventTimeUntilInfo(event.next_run_at).label, { countdown: true }]' in response.text
    assert 'value.dataset.eventCountdownMode = "detail"' in response.text
    assert 'countdown.dataset.eventCountdownMode = "detail"' in response.text
    assert "state.eventCountdownTimer = window.setInterval" in response.text
    assert "if (state.eventsDrawerOpen || state.quickControlsOpen)" in response.text
    assert 'content.className = "event-row-content"' in response.text
    assert 'label.className = "event-row-label"' in response.text
    assert 'value.className = "event-row-value"' in response.text
    assert 'lines.className = "event-row-lines"' in response.text
    assert 'line.className = "event-row-line"' in response.text
    assert 'prompt.className = "event-row-prompt"' in response.text
    assert 'const row = document.createElement("details")' in response.text
    assert 'title.className = "event-run-summary"' in response.text
    assert 'content.className = "event-run-content"' in response.text
    assert 'const body = document.createElement("pre")' in response.text
    assert 'body.className = "event-run-markdown"' in response.text
    assert "body.textContent = stripAssistantResponseChrome(markdown)" in response.text
    assert "function openEventRunTrace" in response.text
    assert "function openBlockingEventRunTrace" in response.text
    assert "function eventRunCanCancel" in response.text
    assert "function cancelEventRun" in response.text
    assert "blocked_by_event_run_id" in response.text
    assert "Open blocking run" in response.text
    assert "Cancel run" in response.text
    assert "/runs/${encodeURIComponent(runId)}/cancel" in response.text
    assert 'link.href = `/api/agent/trace/${encodeURIComponent(run.request_id)}`' in response.text
    assert "void openEventRunTrace(run)" in response.text
    assert "setEventsDrawerOpen(false)" in response.text
    assert "state.eventEditorContext" in response.text
    assert "state.eventEditorNextRunAt" in response.text
    assert "delete context.terminal_session_id" in response.text
    assert 'fetchWithLlmActivity("/api/agent/events/from-prompt"' in response.text
    assert "browser_timezone: browserTimezone" in response.text
    assert 'addMessage("assistant", "Event saved"' in response.text
    assert "function playNotificationSound" in response.text
    assert "function unlockNotificationAudio" in response.text
    assert 'fetch("/api/agent/events"' in response.text
    assert 'setupDrawerResize(eventsDrawerResizer, eventsDrawer, "events")' in response.text
    assert "openfabric.agentUi.eventsDrawerOpen" in response.text
    assert "/api/agent/terminal/config${suffix}" in response.text
    assert 'const settingChatBubbles = document.querySelector("#setting-chat-bubbles")' in response.text
    assert 'const settingChatPopAnimation = document.querySelector("#setting-chat-pop-animation")' in response.text
    assert 'const settingThinkingTextAnimation = document.querySelector("#setting-thinking-text-animation")' in response.text
    assert "function normalizeChatPopAnimationMode" in response.text
    assert "function normalizeThinkingTextAnimationMode" in response.text
    assert "CHAT_POP_ANIMATION_MODES" in response.text
    assert "THINKING_TEXT_ANIMATION_MODES" in response.text
    assert "function renderTextOdometerInto" in response.text
    assert "function applyChatTextOdometer" in response.text
    assert 'animationMode === "odometer"' in response.text
    assert 'querySelectorAll("[data-copy-text]")' in response.text
    assert "ui_chat_pop_animation: normalizeChatPopAnimationMode(merged.ui_chat_pop_animation)" in response.text
    assert "ui_thinking_text_animation: normalizeThinkingTextAnimationMode(" in response.text
    assert "chatLog.dataset.chatPopAnimation = safe.ui_chat_pop_animation" in response.text
    assert "document.documentElement.dataset.thinkingTextAnimation = safe.ui_thinking_text_animation" in response.text
    assert "ui_chat_pop_animation:" in response.text
    assert "ui_thinking_text_animation:" in response.text
    assert (
        'const settingCommandOutputExpanded = document.querySelector("#setting-command-output-expanded")'
        in response.text
    )
    assert "function setChatBubbleLayout" in response.text
    assert 'chatLog?.classList.toggle("chat-bubbles", state.chatBubbleLayout)' in response.text
    assert "ui_command_output_expanded_by_default" in response.text
    assert "function commandOutputCollapsedDefaultFromSettings" in response.text
    assert "setCommandOutputCollapsed(commandOutputCollapsedDefaultFromSettings())" in response.text
    assert 'const randomPastelThemeName = "random-pastel"' in response.text
    assert "function applyRandomPastelTheme" in response.text
    assert "function clearRandomPastelTheme" in response.text
    assert 'const isDark = Math.random() < 0.5' in response.text
    assert 'root.style.colorScheme = isDark ? "dark" : "light"' in response.text
    assert 'const themeRandomizeButton = document.querySelector("#theme-randomize-button")' in response.text
    assert "function refreshRandomPastelTheme" in response.text
    assert "function setThemeRandomizeVisible" in response.text
    assert "event.shiftKey &&\n    !event.ctrlKey &&" in response.text
    assert 'fetch("/api/agent/prompt-history", { cache: "no-store" })' in response.text
    assert 'fetch("/api/agent/prompt-history", {' in response.text
    assert "void navigatePromptHistory(direction)" in response.text
    assert "openfabric.agentUi.promptHistory" not in response.text


def test_agent_ui_prompt_history_is_backend_persisted(tmp_path) -> None:
    db_path = tmp_path / "agent_ui_settings.db"
    first_client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    assert first_client.get("/api/agent/prompt-history").json()["items"] == []

    first_client.post("/api/agent/prompt-history", json={"prompt": "first request"})
    first_client.post("/api/agent/prompt-history", json={"prompt": "second request"})
    updated = first_client.post("/api/agent/prompt-history", json={"prompt": "first request"})

    assert updated.status_code == 200
    assert updated.json()["items"] == ["second request", "first request"]
    assert updated.json()["max_items"] == 100

    second_client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    assert second_client.get("/api/agent/prompt-history").json()["items"] == [
        "second request",
        "first request",
    ]


def test_agent_ui_prompt_home_end_keys_jump_to_entry_edges() -> None:
    client = _client()

    desktop = client.get("/agent-ui/static/app.js?v=20260606-run-queue")
    mobile = client.get("/agent-ui/static/mobile.js?v=20260602-text-odometer")

    assert desktop.status_code == 200
    assert mobile.status_code == 200
    assert "function movePromptCaretToEntryEdge(event)" in desktop.text
    assert 'event.key !== "Home" && event.key !== "End"' in desktop.text
    assert 'promptInput.setSelectionRange(0, anchor, "backward")' in desktop.text
    assert 'promptInput.setSelectionRange(anchor, valueEnd, "forward")' in desktop.text
    prompt_keydown = desktop.text.split('promptInput.addEventListener("keydown"', 1)[1].split(
        'submitButton?.addEventListener("click"',
        1,
    )[0]
    assert "if (movePromptCaretToEntryEdge(event))" in prompt_keydown
    assert "updatePromptMacroMenu({ allowOpen: promptMacroMenuIsOpen() });" in prompt_keydown
    assert "function moveMobilePromptCaretToEntryEdge(event)" in mobile.text
    assert 'input.setSelectionRange(0, anchor, "backward")' in mobile.text
    assert 'input.setSelectionRange(anchor, valueEnd, "forward")' in mobile.text
    assert "if (moveMobilePromptCaretToEntryEdge(event))" in mobile.text


def test_agent_ui_static_js_uses_llm_owned_event_recognition() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "function looksLikeSchedulePrompt" not in response.text
    assert "if (!looksLikeSchedulePrompt(prompt))" not in response.text
    assert 'fetchWithLlmActivity("/api/agent/events/from-prompt"' in response.text
    assert 'addMessage("assistant", "Event save failed"' not in response.text
    assert 'console.warn("Event recognition skipped:", error)' in response.text


def test_agent_ui_static_js_developer_trace_clear_is_trace_only() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "function clearDeveloperTracePane" in response.text
    assert "clearButton.addEventListener(\"click\", clearDeveloperTracePane)" in response.text
    assert "clearButton.addEventListener(\"click\", resetView)" not in response.text
    clear_function = response.text.split("function clearDeveloperTracePane", 1)[1].split(
        "function empty",
        1,
    )[0]
    assert "state.groups.clear()" in clear_function
    assert "state.llmInteractions.clear()" in clear_function
    assert "state.llmStageCounts.clear()" in clear_function
    assert "traceList.replaceChildren(empty(\"No trace events yet.\"))" in clear_function
    assert "state.source.close()" not in clear_function
    assert "chatLog.replaceChildren" not in clear_function
    assert "state.requestId = null" not in clear_function


def test_agent_ui_static_js_stopped_runs_show_feedback_panel() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert '["Final response", "Failure", "Stopped"].includes(visibleTitle)' in response.text
    assert "stoppedFeedbackRendered: new Set()" in response.text
    assert "state.stoppedFeedbackRendered.clear()" in response.text
    assert "terminalTraceRendered: new Set()" in response.text
    assert "state.terminalTraceRendered.clear()" in response.text
    assert "autoExpandedTerminalRequestIds: new Set()" in response.text
    assert "autoExpandedTerminalRestoreByRequest: new Map()" in response.text
    assert "state.autoExpandedTerminalRequestIds.clear()" in response.text
    assert "state.autoExpandedTerminalRestoreByRequest.clear()" in response.text

    load_final_trace = response.text.split("async function loadFinalTrace", 1)[1].split(
        "function startTraceStream",
        1,
    )[0]
    assert 'const isCancelled = String(payload.status || "") === "cancelled";' in load_final_trace
    assert "state.stoppedFeedbackRendered.has(requestId)" in load_final_trace
    assert "const responseMetrics = responseMetricsFromTracePayload(payload);" in load_final_trace
    assert "const liveTrace =" in load_final_trace
    assert "if (!liveTrace) {\n    clearStreamingAnswerMessage();" in load_final_trace
    assert "appendResponseMetricsToRenderedRequest(requestId, responseMetrics)" in load_final_trace
    assert "scheduleResponseMetricsFooterRefresh(requestId)" in load_final_trace
    assert 'const stoppedMessage = "The active run was stopped."' in load_final_trace
    assert 'message = addMessage("assistant", "Stopped", stoppedMessage, memoryContext)' in load_final_trace
    assert "state.stoppedFeedbackRendered.add(requestId)" in load_final_trace
    assert "maybeCollapseAutoExpandedTerminal(requestId)" in load_final_trace
    terminal_restore_helper = response.text.split("function restoreAutoExpandedTerminalState", 1)[1].split(
        "function maybeCollapseAutoExpandedTerminal",
        1,
    )[0]
    assert "void setTerminalVisible(false);" in terminal_restore_helper
    assert "setTerminalCollapsed(true);" in terminal_restore_helper
    terminal_restore = response.text.split("function maybeCollapseAutoExpandedTerminal", 1)[1].split(
        "function waitForTerminalRequestContext",
        1,
    )[0]
    assert "const restoreState = state.autoExpandedTerminalRestoreByRequest.get(id) || null;" in terminal_restore
    assert "state.autoExpandedTerminalRestoreByRequest.delete(id);" in terminal_restore
    assert "restoreAutoExpandedTerminalState(restoreState);" in terminal_restore

    stop_request_helper = response.text.split("async function activeRunStopRequestId", 1)[1].split(
        "async function fetchTaskTracePayload",
        1,
    )[0]
    assert '/api/agent/tasks/${encodeURIComponent(attachedTaskId)}' in stop_request_helper
    assert "taskActiveRequestIdForStop(rememberTaskRecord(payload?.task))" in stop_request_helper
    assert "taskActiveRequestIdForStop(attachedTaskForActiveRun())" in stop_request_helper
    assert "function visibleRequestIdForStop()" in response.text
    assert 'label === "Idle"' in response.text
    assert 'const visibleRequestId = String(state.requestId || "").trim() || visibleRequestIdForStop();' in stop_request_helper
    assert "if (visibleRequestId) {\n    return visibleRequestId;" in stop_request_helper

    stop_function = response.text.split("async function stopCurrentRun", 1)[1].split(
        "function startNewChat",
        1,
    )[0]
    assert "const requestId = await activeRunStopRequestId();" in stop_function
    assert "const requestId = state.requestId;" not in stop_function
    assert "taskTraceLoadOptions(stoppedTask, requestId" in stop_function
    assert "renderTraceEvents: true" in stop_function
    assert "await loadFinalTrace(requestId);" in stop_function
    assert "scheduleDurableTaskStreamSync(requestId, { immediate: true });" in stop_function

    handle_event = response.text.split("function handleEvent", 1)[1].split(
        "function normalizePromptHistoryItems",
        1,
    )[0]
    assert 'event.event_type === "request.cancelled"' in handle_event
    assert "loadFinalTrace(\n        event.request_id" in handle_event


def test_agent_ui_static_js_validation_feedback_applies_non_policy_memory() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "pendingValidationPolicyCount" in response.text
    assert 'proposal?.draft?.memory_kind' in response.text
    assert 'proposalKind === "validation_policy"' in response.text
    assert "`Saved ${appliedCount} memory item${appliedCount === 1 ? \"\" : \"s\"}.`" in response.text
    assert "`Proposed ${pendingValidationPolicyCount} validation polic${pendingValidationPolicyCount === 1 ? \"y\" : \"ies\"}.`" in response.text
    assert 'eventType !== "operator.validation.adjudicated"' in response.text


def test_agent_ui_static_js_hides_modify_validation_for_schema_failures() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "function isStructuredSchemaFailure" in response.text
    assert "structured llm response did not match" in response.text
    assert "promptclassificationproposal" in response.text
    append_function = response.text.split("function appendRunFeedbackAction", 1)[1].split(
        "async function optimizeMemory",
        1,
    )[0]
    assert "if (!isStructuredSchemaFailure(context))" in append_function
    assert 'validationButton.textContent = "Modify Validation"' in append_function


def test_agent_ui_version_check_endpoint_detects_new_checkout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                gateway_url="http://127.0.0.1:8787",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    monkeypatch.setattr(
        agent_ui,
        "get_runtime_version_update_status",
        lambda: {
            "status": "update_available",
            "check_available": True,
            "new_version_available": True,
            "restart_recommended": True,
            "gateway_restart_recommended": True,
            "comparison_key": "git_hash",
            "running": {
                "version": "1.0",
                "git_hash": "old1234",
                "dirty": False,
                "runtime_version": "1.0+old1234",
                "display": "1.0 old1234",
            },
            "latest": {
                "version": "1.0",
                "git_hash": "new5678",
                "dirty": False,
                "runtime_version": "1.0+new5678",
                "display": "1.0 new5678",
            },
        },
    )

    response = client.get("/api/agent/version/check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "update_available"
    assert payload["new_version_available"] is True
    assert payload["running"]["git_hash"] == "old1234"
    assert payload["latest"]["git_hash"] == "new5678"
    assert payload["gateway_restart_supported"] is True
    assert payload["restart_endpoint"] == "/api/agent/restart"


def test_agent_ui_static_js_shows_model_introspection_in_trace() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "function buildModelIntrospectionMarkdown" in response.text
    assert "Model self-brief" in response.text
    assert "appendModelIntrospectionBlock(record.blocks, latest)" in response.text


def test_agent_ui_static_js_posts_restart_request() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "function restartServer" in response.text
    assert "async function restartServer(options = {})" in response.text
    assert "function promptRestartMacroRequested" in response.text
    assert "promptRestartMacroRequested(rawPrompt)" in response.text
    assert "Restart server and all attached gateways ?" in response.text
    assert "function checkVersionUpdateOnRefresh" in response.text
    assert "function versionUpdateRestartMessage" in response.text
    assert 'fetch("/api/agent/version/check", { cache: "no-store" })' in response.text
    assert "payload?.new_version_available !== true" in response.text
    assert "await restartServer({ skipConfirm: true })" in response.text
    assert "void refreshEngineVersion().finally(() => checkVersionUpdateOnRefresh())" in response.text
    assert 'const quickRestartButton = document.querySelector("#quick-restart-button")' in response.text
    assert "const restartButtons = [restartButton, quickRestartButton].filter(Boolean)" in response.text
    assert "function setRestartButtonsDisabled" in response.text
    assert 'quickRestartButton?.addEventListener("click", restartServer)' in response.text
    assert 'fetch("/api/agent/restart", { method: "POST" })' in response.text


def test_agent_ui_static_js_allows_left_pane_to_fill_remaining_width() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "maxPercent = ((rect.width - visibleRightPaneMinWidth() - resizerWidth) / rect.width) * 100" in response.text
    assert "persistPaneWidth(setPaneWidth(100))" in response.text


def test_agent_ui_static_js_task_submit_has_desktop_prompt_resize_helper() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    script = response.text
    assert "function resizePromptInput()" in script
    assert "resetTextareaHeight(promptInput);" in script
    assert 'input.style.height = "";' in script
    submit_handler = script.split("async function handleComposerSubmit", 1)[1].split(
        'promptInput.addEventListener("keydown"',
        1,
    )[0]
    assert 'promptInput.value = "";' in submit_handler
    assert "resizePromptInput();" in submit_handler
    clear_editor = script.split("function clearTaskEditor()", 1)[1].split("function taskRow", 1)[0]
    assert "resetTextareaHeight(taskPrompt);" in clear_editor
    assert "resizePromptInput();" not in clear_editor


def test_agent_ui_static_js_wires_monitor_controls() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    script = response.text
    assert 'const monitorsToggle = document.querySelector("#monitors-toggle")' in script
    assert 'const monitorsDrawer = document.querySelector("#monitors-drawer")' in script
    assert "function maybeDraftMonitor" in script
    assert 'fetch("/api/agent/monitors/draft"' in script
    assert 'fetch("/api/agent/monitors"' in script
    assert "function openMonitorStream" in script
    assert "new EventSource(`/api/agent/monitors/${encodeURIComponent(id)}/stream`)" in script
    assert "function renderMonitors" in script
    assert "function monitorAction" in script


def test_agent_ui_static_css_animates_active_pipeline_step() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.css?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert ".pipeline-item.active::before" in response.text
    assert ".pipeline-duration" in response.text
    assert ".response-metrics-footer" in response.text
    assert ".command-output-description" in response.text
    assert ".command-output-description summary" in response.text
    assert ".command-output-description-body" in response.text
    assert "border-top: 1px solid var(--border-soft)" in response.text
    assert ".app-boot-screen" in response.text
    assert "appBootSpin" in response.text
    assert "html:not(.app-booting) .app-boot-screen" in response.text
    assert "content: none" in response.text
    assert "stageBreathing" not in response.text
    assert "animation: stageDotBreathing" in response.text
    assert ".mode-switch" in response.text
    assert ".prompt-mode-row" in response.text
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 280px)" in response.text
    assert "grid-template-rows: 28px 28px minmax(28px, auto)" in response.text
    assert "grid-row: 1 / span 3" in response.text
    assert ".rolling-digit-reel" in response.text
    assert ".animated-number.number-animating[data-number-animation=\"fade\"]" in response.text
    assert ".animated-number.number-animating[data-number-animation=\"slide\"]" in response.text
    assert ".animated-number.number-animating[data-number-animation=\"pop\"]" in response.text
    assert ".animated-number.number-animating[data-number-animation=\"flip\"]" in response.text
    assert ".wall-clock .rolling-static" in response.text
    assert "@keyframes numberFlip" in response.text
    assert "prefers-reduced-motion: reduce" in response.text
    assert "align-self: end" in response.text
    assert "min-height: 108px" in response.text
    assert ':root[data-theme="daylight"]' in response.text
    assert ':root[data-theme="mint"]' in response.text
    assert ':root[data-theme="citrus"]' in response.text
    assert ':root[data-theme="rosewater"]' in response.text
    assert ':root[data-theme="steel"]' in response.text
    assert ':root[data-theme="aurora"]' in response.text
    assert ':root[data-theme="ember"]' in response.text
    assert ':root[data-theme="ubuntu"]' in response.text
    assert "--accent: #e95420" in response.text
    assert ':root[data-theme="ubuntu-dark"]' in response.text
    assert ':root[data-theme="dracula"]' in response.text
    assert ':root[data-theme="nord"]' in response.text
    assert ':root[data-theme="tokyo-night"]' in response.text
    assert ':root[data-theme="catppuccin-mocha"]' in response.text
    assert ':root[data-theme="solarized-dark"]' in response.text
    assert ':root[data-theme="gruvbox-dark"]' in response.text
    assert ':root[data-theme="nebula"]' in response.text
    assert ':root[data-theme="moss"]' in response.text
    assert ':root[data-theme="retro"]' in response.text
    assert ':root[data-theme="lagoon"]' in response.text
    assert ':root[data-theme="sakura"]' in response.text
    assert ':root[data-theme="harbor"]' in response.text
    assert ':root[data-theme="circuit"]' in response.text
    assert ':root[data-theme="random-pastel"]' in response.text
    assert "color-scheme: light dark" in response.text
    assert ".theme-randomize-button" in response.text
    assert ".trace-topbar .topbar-actions button" in response.text
    assert ".settings-toggle-text" in response.text
    assert ".topbar.topbar-compact .settings-toggle-text" in response.text
    assert ".llm-activity-indicator" in response.text
    assert '.llm-activity-indicator[data-active="true"]' in response.text
    assert '.llm-activity-indicator[data-reachable="false"]' in response.text
    assert ".learning-runtime-indicator" in response.text
    assert '.learning-runtime-indicator[data-active="true"]' in response.text
    assert '.learning-runtime-indicator[data-active="true"][data-status="retrieved"]' in response.text
    assert '.learning-runtime-indicator[data-active="true"][data-status="learning"]' in response.text
    assert '.learning-runtime-indicator[data-active="true"][data-status="used"]' in response.text
    assert '.learning-runtime-indicator[data-active="true"][data-status="direct"]' in response.text
    assert "0 0 16px rgba(var(--accent-rgb), 0.34)" in response.text
    assert "0 0 16px rgba(var(--warning-rgb), 0.32)" in response.text
    assert "0 0 16px rgba(var(--success-rgb), 0.34)" in response.text
    assert "max-height: 28px" in response.text
    assert ".settings-action-button" in response.text
    assert ".pane-collapse-all-button" in response.text
    assert ".settings-drawer-header > .settings-drawer-header-actions" in response.text
    assert ".settings-drawer-collapse-button" in response.text
    assert "--drawer-width" in response.text
    assert ".settings-drawer-resizer" in response.text
    assert ".settings-drawer.resizing" in response.text
    assert ".settings-drawer button" in response.text
    assert ".settings-collapsible-section" in response.text
    assert "margin: 14px 14px 0" in response.text
    assert "box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08)" in response.text
    assert "display: flex" in response.text
    assert "white-space: nowrap" in response.text
    assert ".settings-section-summary" in response.text
    assert ".settings-section-body" in response.text
    assert ".operator-cache-cards" in response.text
    assert ".operator-cache-card" in response.text
    assert "grid-template-columns: repeat(auto-fit, minmax(128px, 1fr))" in response.text
    assert ".settings-section > .settings-action-button" in response.text
    assert "justify-content: flex-end" in response.text
    assert ".memory-dashboard" in response.text
    assert ".memory-drawer" in response.text
    assert ".gateway-drawer" in response.text
    assert ".gateway-status-summary" in response.text
    assert ".gateway-stat-card" in response.text
    assert ".gateway-section-card" in response.text
    assert ".gateway-section-summary" in response.text
    assert ".gateway-section-body" in response.text
    assert ".gateway-active-card" in response.text
    assert ".gateway-registry-card" in response.text
    assert ".gateway-active-summary" in response.text
    assert ".gateway-details-table" in response.text
    assert ".gateway-active-summary .gateway-os-badge" in response.text
    assert ".gateway-routing-badge" not in response.text
    assert ".gateway-routing-message" not in response.text
    assert ".response-gateway-line" in response.text
    assert ".prompt-gateway-picker" in response.text
    assert ".immersive-gateway-picker" in response.text
    assert ".conversation-theme-picker" in response.text
    assert ".footer-gateway-picker" in response.text
    assert "margin-left: auto" in response.text
    assert ".memory-stats" in response.text
    assert ".memory-stat-card" in response.text
    assert ".events-status-summary" in response.text
    assert ".memory-card-header" in response.text
    assert ".memory-section-card" in response.text
    assert ".memory-section-summary" in response.text
    assert ".memory-section-body" in response.text
    assert "overflow: visible" in response.text
    assert ".event-section-card" in response.text
    assert ".event-section-summary" in response.text
    assert ".event-editor-body" in response.text
    assert ".event-editor-actions" in response.text
    assert ".event-title-field" in response.text
    assert ".event-section-body" in response.text
    assert ".tasks-list .task-row" in response.text
    assert ".task-details-card" in response.text
    assert ".task-details" in response.text
    assert ".monitors-list .monitor-row" in response.text
    assert ".monitor-details-card" in response.text
    assert ".monitor-details" in response.text
    assert ".tasks-hero," in response.text
    assert ".task-editor-card," in response.text
    assert ".monitors-hero," in response.text
    assert ".monitor-editor-card," in response.text
    assert ".tasks-status-summary," in response.text
    assert ".monitors-status-summary {" in response.text
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in response.text
    assert ".tasks-status-summary .memory-stat-card" in response.text
    assert ".monitors-status-summary .memory-stat-card" in response.text
    assert "max-height: none" in response.text
    assert "grid-template-columns: minmax(0, 1fr)" in response.text
    assert "grid-auto-rows: max-content" in response.text
    assert "clear: both" in response.text
    assert "content: \"Show\"" in response.text
    assert "content: \"Hide\"" in response.text
    assert ".memory-advanced-filters" in response.text
    assert ".memory-association-panel" in response.text
    assert ".memory-actions" in response.text
    assert "min-height: 100px" in response.text
    assert "max-height: 480px" not in response.text
    assert "max-height: 452px" not in response.text
    assert ".events-list .event-row" in response.text
    assert ".event-row-header" in response.text
    assert ".event-row-title-group" in response.text
    assert ".event-row-next-badge" in response.text
    assert "padding: 7px 13px" in response.text
    assert "min-height: 35px" in response.text
    assert "width: 35px" in response.text
    assert "white-space: nowrap" in response.text
    assert '.event-row-next-badge[data-countdown-state="overdue"]' in response.text
    assert ".event-row-actions" in response.text
    assert ".event-row-toggle" in response.text
    assert ".event-row-toggle-text" in response.text
    assert ".event-row-delete" in response.text
    assert ".event-row-trace" in response.text
    assert ".event-row-cancel" in response.text
    assert ".event-row-archive" in response.text
    assert ".event-row-value-link" in response.text
    assert ".event-row-content" in response.text
    assert ".event-row-lines" in response.text
    assert ".event-row-line" in response.text
    assert ".event-row-label" in response.text
    assert ".event-row-value" in response.text
    assert ".event-row-prompt" in response.text
    assert "word-break: break-word" in response.text
    assert ".event-run-markdown" in response.text
    assert ".event-run-summary" in response.text
    assert ".event-run-content" in response.text
    assert ".event-run-blocked-hint" in response.text
    assert ".event-run-button" in response.text
    assert ".event-run-cancel-button" in response.text
    assert ".event-runs-card {" in response.text
    assert ".event-runs-card > .memory-card-header" in response.text
    assert "background: var(--command-output-header-bg)" in response.text
    assert "max-height: min(72vh, 820px)" not in response.text
    assert "background: transparent" in response.text
    assert ".event-runs {" in response.text
    assert "grid-auto-rows: max-content" in response.text
    assert "max-height: 240px" in response.text
    assert "box-sizing: border-box" in response.text
    assert "min-width: 0" in response.text
    assert "max-width: 100%" in response.text
    assert "min-height: max-content" in response.text
    assert "max-height: none" in response.text
    assert "justify-self: stretch" in response.text
    assert "padding: 12px" in response.text
    assert "padding: 4px" in response.text
    assert "overflow: auto" in response.text
    assert "grid-template-columns: minmax(0, 1fr)" in response.text
    assert ".event-status-badge" in response.text
    assert ".event-selected-card" not in response.text
    assert ".event-controls-bar" in response.text
    assert "margin-top: auto" in response.text
    assert "border-radius: 22px" not in response.text
    assert "padding: 32px 20px" not in response.text
    assert ".trace-list .event-row" in response.text
    assert 'grid-template-areas:\n    "trace-topbar"\n    "trace-events"\n    "llm-stream-resizer"\n    "llm-stream"\n    "progress"' in response.text
    assert ".trace-events-panel" in response.text
    assert ".trace-events-header" in response.text
    assert ".trace-events-search" in response.text
    assert ".chat-history-search" in response.text
    assert ".chat-history-tag" in response.text
    assert ".trace-search-row" in response.text
    assert ".trace-events-panel.trace-events-collapsed .trace-search-row" in response.text
    assert ".trace-search-highlight" in response.text
    assert "grid-area: trace-events" in response.text
    assert ".trace-pane.trace-events-collapsed" in response.text
    assert ".trace-pane.trace-events-collapsed.llm-stream-collapsed.trace-progress-collapsed" in response.text
    assert '"."' in response.text
    assert ".llm-stream-panel" in response.text
    assert ".llm-stream-resizer" in response.text
    assert ".llm-stream-panel.llm-stream-collapsed .trace-search-row" in response.text
    assert ".llm-stream-search" in response.text
    assert ".llm-stream-header-main" in response.text
    assert '"llm-stream-title llm-stream-actions"' in response.text
    assert '"llm-stream-counter llm-stream-counter"' in response.text
    assert "#llm-stream-token-rate" in response.text
    assert "grid-area: llm-stream-counter" in response.text
    assert "@container (max-width: 420px)" not in response.text
    assert "grid-area: llm-stream" in response.text
    assert "grid-area: llm-stream-resizer" in response.text
    assert "grid-area: progress" in response.text
    assert ".event-draft-row > div" in response.text
    assert "white-space: normal" in response.text
    assert "text-overflow: ellipsis" in response.text
    assert "scrollbar-gutter: stable both-edges" not in response.text
    assert "overflow-wrap: anywhere" in response.text
    assert "grid-template-columns: 18px 38px minmax(0, 1fr)" in response.text
    assert ".settings-section input[type=\"checkbox\"]::before" in response.text
    assert ".settings-section input[type=\"checkbox\"]:checked" in response.text
    assert ".settings-section .settings-info-icon" in response.text
    assert ".settings-section .settings-info-tooltip" in response.text
    assert "cursor: default" in response.text
    assert "background: var(--panel-alt)" in response.text
    assert "text-align: left" in response.text
    assert "transform: translate(0, 4px)" in response.text
    assert "transform: translateX(16px)" in response.text
    assert "grid-template-rows: auto minmax(0, 1fr) auto" in response.text
    assert "#run-status" in response.text
    assert ".composer-footer #request-id" in response.text
    assert ".model-status" in response.text
    assert ".topbar-status-row" not in response.text
    assert ".prompt-output-row" in response.text
    assert ".prompt-context-row" in response.text
    assert ".prompt-context-row .context-meter" in response.text
    assert ".prompt-info-icon" in response.text
    assert ".prompt-info-tooltip" in response.text
    assert ".response-mode-switch" in response.text
    assert ".visualization-direction-switch" in response.text
    assert ".visualization-direction-switch:has(input:checked)" in response.text
    assert ".visualization-toggle-button.visualization-hidden" in response.text
    assert ".visualization-toggle-button:hover" in response.text
    assert ".visualization-map-footer" in response.text
    assert ".visualization-zoom-controls" in response.text
    assert ".visualization-zoom-button" in response.text
    assert ".visualization-zoom-label" in response.text
    assert ".visualization-map-footer .visualization-reset-button" in response.text
    assert ".visualization-invert-button[aria-pressed=\"true\"]" in response.text
    assert ".visualization-canvas" in response.text
    assert "--visualization-zoom: 1" in response.text
    assert "cursor: grab" in response.text
    assert ".visualization-canvas.panning" in response.text
    assert ".visualization-canvas.flow-map-inverted .visualization-mermaid" in response.text
    assert "filter: invert(1) hue-rotate(180deg)" in response.text
    assert "cursor: grabbing" in response.text
    assert "background: var(--trace-panel)" in response.text
    assert "background: var(--panel)" in response.text
    assert ".visualization-detail-panel.details-collapsed" in response.text
    assert ".visualization-detail-heading" in response.text
    assert ".visualization-detail-actions" in response.text
    assert ".visualization-detail-toggle" not in response.text
    assert ".visualization-detail-grid" in response.text
    assert "grid-template-columns: repeat(auto-fit, minmax(190px, 1fr))" in response.text
    assert ".visualization-detail-pair" in response.text
    assert ".visualization-detail-table" not in response.text
    assert ".visualization-detail-item" not in response.text
    assert ".visualization-mermaid svg" in response.text
    assert "zoom: var(--visualization-zoom)" in response.text
    assert "user-select: text" in response.text
    assert ".visualization-mermaid svg .node rect" in response.text
    assert ".visualization-mermaid svg .cluster rect" in response.text
    assert ".visualization-mermaid svg .cluster-label *" in response.text
    assert ".visualization-mermaid svg .cluster-label .nodeLabel" in response.text
    assert "filter: drop-shadow(0 5px 8px rgba(31, 35, 40, 0.18))" in response.text
    assert "box-shadow: 0 12px 28px var(--shadow)" in response.text
    assert "color: var(--text)" in response.text
    assert "grid-template-columns: 58px 38px 92px" in response.text
    assert "width: 198px" in response.text
    assert "justify-content: flex-start" in response.text
    assert ".prompt-voice-input-anchor" in response.text
    assert ".prompt-voice-input-anchor .voice-input-button" in response.text
    assert ".prompt-voice-input-anchor .quick-terminal-toggle-button" in response.text
    assert ".quick-terminal-toggle-button[aria-pressed=\"true\"]" in response.text
    assert ".composer-footer .voice-input-button" not in response.text
    assert ".prompt-actions .stop-button" in response.text
    assert ".prompt-actions .run-task-button" not in response.text
    assert ".prompt-actions #queue-button" in response.text
    assert "grid-template-columns: repeat(3, minmax(0, 1fr)) 62px" in response.text
    assert "grid-row: 2 / span 2" in response.text
    assert "grid-template-rows: repeat(2, 28px)" in response.text
    assert "grid-template-rows: 28px 28px minmax(28px, auto)" in response.text
    assert "grid-auto-rows: 28px" in response.text
    assert "width: 62px" in response.text
    assert "width: 100%" in response.text
    assert "gap: 6px" in response.text
    assert "grid-column: 1 / 4" in response.text
    assert "grid-column: 1 / -1" in response.text
    assert "grid-column: 3 / -1" in response.text
    assert "grid-column: 3" in response.text
    assert "body:not(.immersive-mode) .prompt-actions .voice-input-button" not in response.text
    assert "flex: 1 1 auto" in response.text
    assert "justify-content: flex-end" in response.text
    assert ".chat-log:not(.chat-bubbles) .message" in response.text
    assert '.chat-log[data-chat-pop-animation="soft-rise"] .message' in response.text
    assert '.chat-log[data-chat-pop-animation="slide-side"] .message.assistant' in response.text
    assert '.chat-log[data-chat-pop-animation="stream-roll"] .message .message-body' in response.text
    assert '.chat-log[data-chat-pop-animation="odometer"] .message' in response.text
    assert ".chat-text-odometer" in response.text
    assert '.chat-log[data-chat-pop-animation="none"] .message' in response.text
    assert "@keyframes chatPopSoftRise" in response.text
    assert "@keyframes chatPopSpring" in response.text
    assert "@keyframes chatPopBlurGlow" in response.text
    assert "@keyframes chatStreamRollText" in response.text
    assert "@keyframes textOdometerRoll" in response.text
    assert "@media (prefers-reduced-motion: reduce)" in response.text
    assert "padding-top: 14px" in response.text
    assert "padding-bottom: 14px" in response.text
    assert ".chat-log.chat-bubbles .message.user" in response.text
    assert ".message.user.streaming-step-message" in response.text
    assert ".streaming-step-running .streaming-step-dots span" in response.text
    assert ".streaming-step-cancelled .streaming-step-status" in response.text
    assert ".streaming-step-actions" in response.text
    assert "grid-template-columns: auto auto minmax(0, 1fr)" in response.text
    assert "justify-self: end" in response.text
    assert "margin-left: auto" in response.text
    assert ".streaming-step-cancel-button" in response.text
    assert "text-decoration: underline" in response.text
    assert "@keyframes streamingStepDot" in response.text
    assert ".chat-log.chat-bubbles .message.user.streaming-step-message" in response.text
    assert ".chat-log.chat-bubbles .message.assistant" in response.text
    assert ".message.assistant.final-response-message" in response.text
    assert ".conversation-deep-reasoning-badge" in response.text
    assert ".conversation-streaming-badge" in response.text
    assert ".conversation-memory-applied-badge" in response.text
    assert ".conversation-cache-applied-badge" in response.text
    assert ".rephrase-retry-message" in response.text
    assert ".message.user.rephrase-retry-message" in response.text
    assert ".chat-log.chat-bubbles .message.user.rephrase-retry-message" in response.text
    assert ".rephrase-retry-badge" in response.text
    assert ".memory-digest-message" in response.text
    assert ".memory-digest-badge" in response.text
    assert ".memory-digest-actions" in response.text
    assert ".learning-lesson-message" in response.text
    assert ".learning-lesson-badge" in response.text
    assert ".learning-lesson-actions" in response.text
    assert ".message-inline-badges" in response.text
    assert "min-height: 16px" in response.text
    assert "max-height: 16px" in response.text
    assert ".memory-applied-inline-badge" in response.text
    assert ".learning-runtime-applied-inline-badge" in response.text
    assert ".learning-runtime-learned-inline-badge" in response.text
    assert ".memory-applied-note" in response.text
    assert ".response-learning-summary" in response.text
    assert ".response-learning-summary-header" in response.text
    assert ".response-learning-summary-counts" in response.text
    assert ".response-learning-summary-row" in response.text
    assert ".response-learning-summary-empty" in response.text
    assert "#conversation-memory-button.memory-applied" in response.text
    assert "border: 1px solid rgba(var(--accent-rgb), 0.42)" in response.text
    assert "background: rgba(var(--accent-rgb), 0.1)" in response.text
    assert "border-left: 2px solid var(--accent)" in response.text
    assert "border-right: 2px solid var(--success)" in response.text
    assert "border-right-color: var(--debug)" in response.text
    assert "background: rgba(var(--accent-rgb), 0.08)" in response.text
    assert "background: rgba(var(--success-rgb), 0.08)" in response.text
    assert ".chat-log.chat-bubbles .thinking-message" in response.text
    assert ".chat-log.chat-bubbles .message.assistant.command-output-panel" in response.text
    assert "width: min(78%, 720px)" in response.text
    assert "max-width: min(78%, 720px)" in response.text
    assert ".chat-log.chat-bubbles .message.assistant.command-output-panel + .message" in response.text
    assert ".streaming-complete-grid" in response.text
    assert ".streaming-complete-row" in response.text
    assert ".streaming-complete-cell" in response.text
    assert ".streaming-complete-result" in response.text
    assert ".streaming-complete-result .markdown-code" in response.text
    assert ".streaming-complete-result .markdown-code code" in response.text
    assert "grid-template-columns: minmax(120px, 0.9fr) minmax(86px, 0.45fr) minmax(0, 1.8fr)" in response.text
    assert ".streaming-step-command-output-panel" in response.text
    assert ".command-output-record-panel" in response.text
    assert ".command-output-card.collapsed .command-output-terminal" in response.text
    assert ".streaming-step-message .command-output-button" in response.text
    assert "gap: 10px" in response.text
    assert "box-shadow: none" in response.text
    assert "font-size: 10px" in response.text
    assert "text-align: left" in response.text
    assert "height: 28px" in response.text
    assert "max-width: none" in response.text
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in response.text
    assert "border-radius: 4px" in response.text
    assert "border: 1px solid var(--border-soft)" in response.text
    assert ".command-output-panel" in response.text
    assert ".command-output-panel.collapsed" in response.text
    assert "--command-output-header-bg" in response.text
    assert "--command-output-card-head-bg" in response.text
    assert "background: var(--command-output-header-bg)" in response.text
    assert "border-radius: 8px" in response.text
    assert "border-left-color: var(--accent)" in response.text
    assert ".command-output-panel.collapsed .command-output-terminal" in response.text
    assert ".llm-runtime-terminal" not in response.text
    assert ".command-output-command" in response.text
    assert ".command-output-terminal" in response.text
    assert ".command-output-button" in response.text
    assert "grid-template-columns: minmax(0, 1fr) max-content" in response.text
    assert "min-width: max-content" in response.text
    assert "width: 46px" in response.text
    assert ".shell.trace-collapsed" in response.text
    assert ".shell.visualization-collapsed" in response.text
    assert ".visualization-pane" in response.text
    assert ".visualization-topbar" in response.text
    assert "border-bottom-color: var(--command-output-header-border)" in response.text
    assert "background: var(--command-output-header-bg)" in response.text
    assert ".visualization-canvas" in response.text
    assert ".visualization-mermaid" in response.text
    assert "fill: var(--flow-map-header-bg, var(--command-output-header-bg)) !important" in response.text
    assert "stroke: var(--flow-map-header-border, var(--command-output-header-border)) !important" in response.text
    assert ".visualization-mermaid svg .cluster-label" in response.text
    assert ".visualization-detail-panel" in response.text
    assert ".visualization-detail-header" in response.text
    assert ".visualization-detail-grid" in response.text
    assert ".visualization-toggle-button" in response.text
    assert ".memory-toggle-button[aria-expanded=\"true\"]" in response.text
    assert ".trace-toggle-button.trace-hidden" in response.text
    assert ".trace-toggle-button:hover" in response.text
    assert ".progress-header" in response.text
    assert ".settings-model-picker" in response.text
    assert ".model-picker" not in response.text
    assert "padding-bottom: 10px" in response.text
    assert ".progress-title" in response.text
    assert ".progress-summary" in response.text
    assert ".progress-summary[hidden]" in response.text
    assert ".progress-summary-stage" in response.text
    assert ".progress-summary-duration" in response.text
    assert ".progress-response-metrics" not in response.text
    assert ".progress-response-metrics[hidden]" not in response.text
    assert ".conversation-header" in response.text
    assert ".terminal-advisory-context-row" in response.text
    assert ".terminal-advisory-context-label" in response.text
    assert ".terminal-advisory-context-toggle" in response.text
    assert ".terminal-advisory-context-toggle .mode-track" in response.text
    assert ".terminal-advisory-context-toggle:has(input:checked)" in response.text
    assert "@media (max-width: 620px)" in response.text
    assert "flex-wrap: wrap" in response.text
    assert "grid-template-rows: 26px minmax(38px, auto)" in response.text
    assert (
        ".terminal-header,\n.conversation-header {\n"
        "  border-bottom-color: var(--command-output-header-border);\n"
        "  background: var(--command-output-header-bg);\n}"
    ) in response.text
    assert ".conversation-actions" in response.text
    assert ".chat-history-drawer" in response.text
    assert ".chat-history-item" in response.text
    assert ".conversation-auto-approve-switch" in response.text
    assert ".agent-optimization-control" in response.text
    assert ".agent-clarification-control" in response.text
    assert ".agent-optimization-heat" in response.text
    assert ".conversation-immersive-toggle" in response.text
    assert ".quick-controls-toggle" in response.text
    assert ".immersive-settings-toggle" in response.text
    assert ".quick-controls-panel" in response.text
    assert ".quick-controls-panel.open" in response.text
    assert "border-radius: 0 var(--pane-radius) var(--pane-radius) 0" in response.text
    assert "box-shadow: 6px 0 18px rgba(0, 0, 0, 0.08)" in response.text
    assert "transform: translateX(-102%)" in response.text
    assert "height: 100dvh" in response.text
    assert "border-right: 1px solid var(--border)" in response.text
    assert ".quick-controls-close-button" in response.text
    assert ".quick-controls-footer" in response.text
    assert ".quick-controls-restart-button" in response.text
    assert ".quick-controls-section" in response.text
    assert ".quick-controls-section-heading" in response.text
    assert ".quick-controls-section-info" in response.text
    assert ".quick-controls-section-heading .quick-controls-section-label" in response.text
    assert ".quick-upcoming-events-section" in response.text
    assert ".quick-upcoming-events-list" in response.text
    assert ".quick-upcoming-event" in response.text
    assert ".quick-upcoming-event-countdown" in response.text
    assert "--section-card-radius: 12px" in response.text
    assert "background: var(--command-output-header-bg)" in response.text
    assert "border-radius: calc(var(--section-card-radius) - 1px)" in response.text
    assert "text-transform: none" in response.text
    assert ".quick-controls-panel .prompt-info-tooltip" in response.text
    assert "left: calc(100% + 10px)" in response.text
    assert "max-width: calc(100vw - 380px)" in response.text
    assert "@media (max-width: 700px)" in response.text
    assert ".quick-control-row" in response.text
    assert ".quick-control-row-label" in response.text
    assert "flex-direction: column" in response.text
    assert "text-align: center" in response.text
    assert ".quick-controls-panel .prompt-mode-row + .quick-control-row" in response.text
    assert ".quick-control-row + .quick-control-row" in response.text
    assert "margin-top: 8px" in response.text
    assert ".quick-controls-panel .quick-control-row .prompt-output-row" in response.text
    assert ".quick-controls-panel .quick-control-row .prompt-verification-row" in response.text
    assert '.agent-clarification-control button[data-mode="auto_pilot"]' in response.text
    assert "grid-template-columns: minmax(0, 78px) 38px minmax(0, 78px)" in response.text
    assert "padding-inline: 22px" in response.text
    assert ".quick-controls-panel .prompt-mode-row > .prompt-info-icon" in response.text
    assert "justify-content: center" in response.text
    assert "flex: 1 1 auto" in response.text
    assert ".conversation-summary-label" in response.text
    assert ".conversation-header-confirmation" in response.text
    assert ".conversation-actions-primary" in response.text
    assert ".conversation-actions-secondary" in response.text
    assert ".conversation-panel.conversation-collapsed:has(.conversation-header-confirmation:not([hidden]))" in response.text
    assert "container: conversation-panel / inline-size" in response.text
    assert "grid-template-columns: auto auto auto minmax(0, 1fr) 84px auto" in response.text
    assert "grid-template-rows: 28px 28px" in response.text
    assert "@container conversation-panel (max-width: 760px)" in response.text
    assert "grid-template-rows: 28px 28px 28px" in response.text
    assert "@container conversation-panel (max-width: 620px)" in response.text
    assert "grid-column: 1 / 5" in response.text
    assert "grid-column: 5 / -1" in response.text
    assert "body.immersive-mode .shell" in response.text
    assert "body.immersive-mode .settings-drawer," not in response.text
    assert "body.immersive-mode .settings-backdrop," not in response.text
    assert "flex-wrap: wrap" in response.text
    assert "max-height: none" in response.text
    assert (
        "body.immersive-mode .conversation-header > :not(.immersive-header-controls):not(.immersive-gateway-picker):not(.conversation-header-confirmation):not(.immersive-brand-clock)"
        in response.text
    )
    assert "body.immersive-mode .immersive-gateway-picker" in response.text
    assert "flex: 0 1 280px" in response.text
    assert "min-width: 128px" in response.text
    assert "body.immersive-mode .immersive-header-controls" in response.text
    assert "flex: 1 1 100%" in response.text
    assert "justify-content: flex-end" in response.text
    assert "body.immersive-mode .immersive-header-badges" in response.text
    assert "display: contents" in response.text
    assert "body.immersive-mode .immersive-header-badges .immersive-model-status" in response.text
    assert "body.immersive-mode .immersive-header-badges .immersive-run-status" in response.text
    assert "body.immersive-mode .quick-controls-toggle" in response.text
    assert "body.immersive-mode .immersive-settings-toggle" in response.text
    assert "flex: 0 0 26px" in response.text
    assert "order: 5" in response.text
    assert "order: 6" in response.text
    assert "order: 7" in response.text
    assert "order: 8" in response.text
    assert "order: 6" in response.text
    assert "body.immersive-mode .prompt-form" in response.text
    assert "grid-template-columns: minmax(52px, auto) 94px minmax(0, 1fr)" in response.text
    assert "grid-template-rows: minmax(64px, auto) auto" in response.text
    assert "body.immersive-mode .prompt-actions" in response.text
    assert "grid-column: 3" in response.text
    assert "grid-row: 2" in response.text
    assert "minmax(52px, 0.8fr)" in response.text
    assert "minmax(42px, 0.65fr)" in response.text
    assert "minmax(64px, 1fr)" in response.text
    assert "grid-template-rows: 32px" in response.text
    assert "minmax(46px, 0.8fr)" in response.text
    assert "minmax(38px, 0.6fr)" in response.text
    assert "minmax(56px, 1fr)" in response.text
    assert "justify-content: stretch" in response.text
    assert "body.immersive-mode .prompt-form > .prompt-mode-row" in response.text
    assert "body.immersive-mode .quick-controls-panel .prompt-mode-row" in response.text
    assert "body.immersive-mode .prompt-context-row" in response.text
    assert "body.immersive-mode .prompt-context-row .context-meter::before" in response.text
    assert "content: attr(data-context-percent)" in response.text
    assert "body.immersive-mode .prompt-voice-input-anchor" in response.text
    assert "body.immersive-mode .prompt-voice-input-anchor .quick-terminal-toggle-button" in response.text
    assert "body.immersive-mode .prompt-voice-input-anchor .voice-input-label" in response.text
    assert "body.immersive-mode #prompt-input" in response.text
    assert "body.immersive-mode .prompt-actions .new-chat-button" in response.text
    assert "body.immersive-mode .prompt-actions .stop-button" in response.text
    assert "body.immersive-mode .prompt-actions .voice-input-button" not in response.text
    assert "body.immersive-mode .prompt-actions .run-task-button" not in response.text
    assert "body.immersive-mode .prompt-actions #queue-button" in response.text
    assert "grid-column: 3" in response.text
    assert "body.immersive-mode .prompt-actions #submit-button" in response.text
    assert "body.immersive-mode .terminal-panel," not in response.text
    assert "body.immersive-mode .terminal-split-resizer," not in response.text
    assert ".progress-actions" in response.text
    assert ".pane-copy-button" in response.text
    assert ".final-result-copy-button" in response.text
    assert ".conversation-activity-label" in response.text
    assert ".conversation-panel.conversation-collapsed.conversation-thinking" in response.text
    assert ".conversation-panel.conversation-collapsed.conversation-clarification-requested" in response.text
    assert ".conversation-panel.conversation-clarification-requested .conversation-header" in response.text
    assert ".conversation-panel.conversation-collapsed" in response.text
    assert ".openfabric-idle-art" in response.text
    assert ".openfabric-banner-art" in response.text
    assert ".user-pane.all-panes-collapsed::after" in response.text
    assert "openfabricBannerBreathe" in response.text
    assert ".user-pane.conversation-collapsed-layout" in response.text
    assert ".user-pane.conversation-collapsed-layout.terminal-expanded-layout" in response.text
    assert ".user-pane.bottom-stack-layout::after" in response.text
    assert ".progress-panel.progress-collapsed" in response.text
    assert "height: 32px" in response.text
    assert "padding: 0 12px" in response.text
    assert ".stop-button" in response.text
    assert ".new-chat-button:not(:disabled):hover" in response.text
    assert "#submit-button:not(:disabled):hover" in response.text
    assert ".settings-drawer button" in response.text
    assert ".events-drawer button:not(:disabled):hover" in response.text
    assert ".events-dashboard,\n.tasks-dashboard,\n.monitors-dashboard" in response.text
    assert ".events-status-summary {\n  grid-template-columns: repeat(5, minmax(0, 1fr));" in response.text
    assert ".events-status-summary {\n  grid-template-columns: repeat(4" not in response.text
    assert ".events-status-summary,\n.tasks-status-summary,\n.monitors-status-summary" in response.text
    assert ".events-status-summary .memory-stat-card strong,\n.events-status-summary .memory-stat-card span" in response.text
    assert ".visualization-pane button:not(:disabled):hover" in response.text
    assert ".trace-pane .live-button.live-active" in response.text
    assert ".trace-toggle-button" in response.text
    assert ".terminal-toggle-button" not in response.text
    assert ".prompt-actions" in response.text
    assert ".composer-split" in response.text
    assert ".terminal-panel" in response.text
    assert ".terminal-split-resizer" in response.text
    assert ".terminal-panel.terminal-collapsed" in response.text
    assert "margin: 0" in response.text
    assert "border-radius: 0" in response.text
    assert "box-shadow: none" in response.text
    assert ".terminal-screen" in response.text
    assert ".terminal-gateway-badge" in response.text
    assert "height: 22px" in response.text
    assert "padding: 0 10px" in response.text
    assert "max-width: clamp(96px, 24vw, 320px)" in response.text
    assert "text-overflow: ellipsis" in response.text
    assert "background: var(--terminal-bg)" in response.text
    assert ".terminal-gateway-badge.connected" in response.text
    assert "color: var(--success)" in response.text
    assert '.terminal-gateway-badge[data-connected="false"]' in response.text
    assert "color: var(--muted)" in response.text
    assert "text-decoration: line-through" in response.text
    assert ".brand-title span:first-child::after" in response.text
    assert ".topbar-title > .wall-clock::before" in response.text
    assert "#brand-agent-name" in response.text
    assert ".immersive-brand-agent-name" in response.text
    assert "font-size: 12px" in response.text
    assert (
        "grid-template-columns: auto auto auto minmax(72px, 1fr) auto auto auto"
        in response.text
    )
    assert ".directory-footer-link" in response.text
    assert ".terminal-panel:not([hidden]) + .prompt-form" in response.text
    assert "--terminal-bg" in response.text
    assert ".thinking-indicator" in response.text
    assert ".thinking-indicator-text" in response.text
    assert ".thinking-indicator-text.thinking-indicator-text-animate" in response.text
    assert 'html[data-thinking-text-animation="roll-up"]' in response.text
    assert "animation: thinkingIndicatorTextRollUp 240ms" in response.text
    assert 'html[data-thinking-text-animation="swing"]' in response.text
    assert 'html[data-thinking-text-animation="odometer"]' in response.text
    assert ".thinking-indicator-text.thinking-text-odometer" in response.text
    assert ".text-odometer-reel" in response.text
    assert ".thinking-indicator-dots" in response.text
    assert "animation: thinkingBounceDot 0.9s ease-in-out infinite" in response.text
    assert "@keyframes thinkingBounceDot" in response.text
    assert "@keyframes thinkingIndicatorTextRollUp" in response.text
    assert "@keyframes thinkingIndicatorTextSwing" in response.text
    assert "@keyframes textOdometerRoll" in response.text
    assert "animation: thinkingRectangleSweep" not in response.text
    assert "@keyframes thinkingRectangleSweep" not in response.text
    assert "background-clip: text" not in response.text
    assert "-webkit-text-fill-color: transparent" not in response.text
    assert "animation: thinkingTextSweep" not in response.text
    assert "@keyframes thinkingTextSweep" not in response.text
    assert ".confirmation-actions.retired" in response.text
    assert ".confirmation-actions-header" in response.text
    assert ".confirmation-action-card" in response.text
    assert ".confirmation-action-mini-header" in response.text
    assert ".confirmation-action-footer" in response.text
    assert ".confirmation-action-command pre" in response.text
    assert ".restore-progress-state" in response.text
    assert ".restore-progress-fill" in response.text
    assert "@keyframes restoreProgressSweep" in response.text
    assert ".confirmation-actions-buttons" in response.text
    assert ".confirmation-chip" in response.text
    assert ".command-output-gateway" in response.text
    assert ".command-output-title-stack" in response.text
    assert ".confirmation-button.memory" in response.text
    assert ".inline-memory-editor" in response.text
    assert ".clarification-other button" in response.text
    assert ".clarification-voice-input" in response.text
    assert ".clarification-parameter-select" in response.text
    assert ".confirmation-actions:not(.retired) .confirmation-button:not(:disabled)" in response.text
    assert ".confirmation-button:disabled" in response.text
    assert "animation: thinkingPulse 1.55s ease-in-out infinite" in response.text
    assert "justify-content: flex-start" in response.text
    assert "flex: 0 0 100%" in response.text
    assert "font-size: 11px" in response.text


def test_agent_ui_file_endpoint_opens_workspace_file(tmp_path) -> None:
    target = tmp_path / "README.txt"
    target.write_text("hello from workspace", encoding="utf-8")
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    response = client.get("/api/agent/file", params={"path": "README.txt"})

    assert response.status_code == 200
    assert response.text == "hello from workspace"
    assert "inline" in response.headers["content-disposition"]


def test_agent_ui_file_endpoint_blocks_workspace_escape(tmp_path) -> None:
    outside = tmp_path.parent / "outside-agent-ui-link.txt"
    outside.write_text("outside", encoding="utf-8")
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    response = client.get("/api/agent/file", params={"path": str(outside)})

    assert response.status_code == 403


def test_agent_ui_restart_endpoint_uses_injected_callback() -> None:
    client = _client()
    calls: list[str] = []
    client.app.state.agent_ui_restart_callback = lambda: calls.append("restart")

    response = client.post("/api/agent/restart")

    assert response.status_code == 200
    assert response.json()["status"] == "restarting"
    assert response.json()["mode"] == "callback"
    assert response.json()["gateway"]["status"] == "skipped"
    assert calls == ["restart"]


def test_agent_ui_restart_endpoint_requests_gateway_restart(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = CountingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                gateway_url="http://127.0.0.1:8787",
                gateway_timeout_seconds=30,
            ),
            agent_runtime=runtime,
        )
    )
    client.app.state.agent_ui_restart_callback = lambda: None
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"status":"restarting","mode":"callback"}'

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        calls.append({"url": request.full_url, "data": request.data, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)

    response = client.post("/api/agent/restart")

    assert response.status_code == 200
    assert response.json()["gateway"] == {"status": "restarting", "mode": "callback"}
    assert calls == [
        {
            "url": "http://127.0.0.1:8787/restart",
            "data": b'{"node": "localhost"}',
            "timeout": 3.0,
        }
    ]


def test_agent_ui_llm_runtime_start_proxies_to_gateway(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                gateway_url="http://127.0.0.1:8787",
                gateway_timeout_seconds=30,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"status":"starting","running":true,"pid":1234,"message":"Managed LLM runtime started."}'

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        calls.append({"url": request.full_url, "data": request.data, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)

    response = client.post(
        "/api/agent/llm/start",
        json={
            "command": "./src/llm/start-test.sh",
            "conda_env": "vllm",
            "cwd": "/tmp",
            "restart": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["running"] is True
    assert calls == [
        {
            "url": "http://127.0.0.1:8787/llm/start",
            "data": (
                b'{"command": "./src/llm/start-test.sh", '
                b'"conda_env": "vllm", "cwd": "/tmp", "restart": true, "node": "localhost"}'
            ),
            "timeout": 10.0,
        }
    ]


def test_agent_ui_llm_runtime_log_proxies_offset_to_gateway(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                gateway_url="http://127.0.0.1:8787",
                gateway_timeout_seconds=30,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"status":"running","running":true,"offset":12,"next_offset":18,"text":"loading"}'

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        calls.append({"url": request.full_url, "data": request.data, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)

    response = client.get("/api/agent/llm/log", params={"offset": 12})

    assert response.status_code == 200
    assert response.json()["text"] == "loading"
    assert calls == [
        {
            "url": "http://127.0.0.1:8787/llm/log?offset=12&node=localhost",
            "data": None,
            "timeout": 5.0,
        }
    ]


def test_agent_ui_restart_uses_module_invocation_for_uvicorn_main(monkeypatch, tmp_path) -> None:
    uvicorn_main = tmp_path / "site-packages" / "uvicorn" / "__main__.py"
    uvicorn_main.parent.mkdir(parents=True)
    uvicorn_main.write_text("", encoding="utf-8")
    monkeypatch.setattr(agent_ui.sys, "executable", "/tmp/python")
    monkeypatch.setattr(
        agent_ui.sys,
        "argv",
        [
            str(uvicorn_main),
            "--app-dir",
            "src",
            "agent_runtime.api.app:create_app",
            "--factory",
        ],
    )

    assert agent_ui._restart_argv() == [
        "/tmp/python",
        "-m",
        "uvicorn",
        "--app-dir",
        "src",
        "agent_runtime.api.app:create_app",
        "--factory",
    ]


def test_agent_ui_request_stream_and_trace_round_trip() -> None:
    client = _client()

    submitted = client.post("/api/agent/request", json={"prompt": "list files"})

    assert submitted.status_code == 200
    payload = submitted.json()
    assert payload["request_id"]
    assert payload["stream_url"] == f"/api/agent/stream/{payload['request_id']}"
    assert payload["trace_url"] == f"/api/agent/trace/{payload['request_id']}"
    assert payload["llm_context_window_tokens"] > 0

    with client.stream("GET", payload["stream_url"]) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: trace" in body
    assert "Request completed" in body

    trace = client.get(payload["trace_url"])

    assert trace.status_code == 200
    trace_payload = trace.json()
    assert trace_payload["status"] == "completed"
    assert trace_payload["final_response"] == "handled: list files"
    assert trace_payload["response_metrics"]["input_tokens_estimate"] == 0
    assert trace_payload["response_metrics"]["output_tokens_estimate"] == 0
    assert trace_payload["response_metrics"]["duration_seconds"] >= 0
    assert trace_payload["response_metrics"]["source"] == "trace_timestamps"
    assert trace_payload["display_document"]["sections"][0]["rows"][0] == {"path": "README.txt"}
    data_ref = trace_payload["display_document"]["sections"][0]["data_ref"]
    raw = client.get(f"/api/agent/raw/{payload['request_id']}/{data_ref}")
    assert raw.status_code == 200
    assert raw.json()["preview"]["rows"] == [{"path": "README.txt"}]
    assert raw.json()["full_payload_available"] is False
    assert any(event["stage"] == "prompt_classification" for event in trace_payload["events"])


def test_agent_ui_runtime_failure_trace_includes_specific_error_detail() -> None:
    runtime = FailingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "fail please"})
    request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    trace = client.get(submitted.json()["trace_url"]).json()

    assert trace["status"] == "failed"
    assert "Runtime error while handling the request" in trace["error"]
    assert "RuntimeError: boom: fail please" in trace["error"]
    assert trace["error_detail"]["category"] == "unexpected_error"
    assert trace["error_detail"]["metadata"]["exception_type"] == "RuntimeError"
    assert any(
        event["event_type"] == "request.failed"
        and event["detail"]["error_detail"]["category"] == "unexpected_error"
        for event in trace["events"]
    )
    assert request_id


def test_agent_ui_llm_failure_trace_includes_specific_error_detail() -> None:
    runtime = FailingLlmRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "ask model",
            "context": {"llm_base_url": "http://127.0.0.1:8000/v1"},
        },
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    trace = client.get(submitted.json()["trace_url"]).json()

    assert trace["status"] == "failed"
    assert trace["error_detail"]["category"] == "llm_transport_error"
    assert "LLM service unavailable" in trace["error_detail"]["title"]
    assert "model server is running" in trace["error_detail"]["fix_hint"]
    assert "connection refused" in trace["error"]


def test_agent_integration_execute_returns_blocking_result_without_sse() -> None:
    client = _client()

    response = client.post(
        "/api/agent/integrations/execute",
        json={"prompt": "list files"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"]
    assert payload["status"] == "completed"
    assert payload["trace_url"] == f"/api/agent/trace/{payload['request_id']}"
    assert payload["final_response"] == "handled: list files"
    assert payload["display_document"]["sections"][0]["rows"] == [{"path": "README.txt"}]
    assert payload["outputs"][0]["preview"]["rows"] == [{"path": "README.txt"}]
    assert payload["response_metrics"]["source"] == "trace_timestamps"


def test_agent_integration_execute_returns_specific_failure_detail() -> None:
    runtime = FailingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/integrations/execute",
        json={"prompt": "fail please"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert "RuntimeError: boom: fail please" in payload["error"]
    assert payload["error_detail"]["category"] == "unexpected_error"
    assert payload["error_detail"]["message_markdown"] == payload["error"]


def test_agent_integration_execute_uses_persisted_runtime_controls(tmp_path) -> None:
    runtime = CountingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=tmp_path / "settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    updated = client.post(
        "/api/agent/runtime-controls",
        json={
            "operator_policy_profile": "assisted",
            "reasoning_profile": "deep",
            "repair_profile": "aggressive",
            "workflow_execution_mode": "streaming",
            "prompt_rephrase_enabled": False,
            "response_streaming_enabled": True,
        },
    )
    response = client.post(
        "/api/agent/integrations/execute",
        json={
            "prompt": "list files",
            "context": {
                "operator_execution_mode": "full_plan",
                "operator_effect_policy_mode": "deterministic",
                "guided_deliberation_mode": "off",
            },
        },
    )

    assert updated.status_code == 200
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert runtime.last_context["workflow_execution_mode"] == "streaming"
    assert runtime.last_context["operator_policy_profile"] == "assisted"
    assert runtime.last_context["reasoning_profile"] == "deep"
    assert runtime.last_context["repair_profile"] == "aggressive"
    assert runtime.last_context["prompt_rephrase_enabled"] is False
    assert runtime.last_context["response_streaming_enabled"] is True
    assert "operator_execution_mode" not in runtime.last_context
    assert "operator_effect_policy_mode" not in runtime.last_context
    assert "guided_deliberation_mode" not in runtime.last_context


def test_agent_integration_execute_timeout_returns_accepted_then_result() -> None:
    runtime = BlockingFirstRequestRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/integrations/execute",
        json={"prompt": "slow list files", "timeout_seconds": 0.01},
    )

    assert response.status_code == 202
    accepted = response.json()
    assert accepted["status"] == "running"
    request_id = accepted["request_id"]
    assert runtime.first_started.wait(1.0)

    runtime.release_first.set()
    completed_trace = _wait_for_trace_status(client, request_id, "completed")
    result = client.get(f"/api/agent/integrations/results/{request_id}")

    assert completed_trace["final_response"] == "handled: slow list files"
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["final_response"] == "handled: slow list files"


def test_agent_integration_confirmation_blocks_on_approved_child_result() -> None:
    runtime = FakeConfirmationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/integrations/execute",
        json={"prompt": "calculate memory and write mem.txt"},
    )
    parent = submitted.json()

    assert submitted.status_code == 200
    assert parent["status"] == "awaiting_confirmation"
    assert parent["needs_action"]["type"] == "confirmation"

    approved = client.post(f"/api/agent/integrations/confirm/{parent['request_id']}", json={})

    assert approved.status_code == 200
    approved_payload = approved.json()
    assert approved_payload["request_id"] != parent["request_id"]
    assert approved_payload["status"] == "completed"
    assert approved_payload["final_response"] == "approved: calculate memory and write mem.txt"
    assert runtime.replay_contexts[-1]["confirmation"] is True


def test_agent_integration_confirmation_deny_keeps_trace_cancelled() -> None:
    runtime = FakeConfirmationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/integrations/execute",
        json={"prompt": "calculate memory and write mem.txt"},
    )
    parent = submitted.json()

    denied = client.post(
        f"/api/agent/integrations/confirm/{parent['request_id']}",
        json={"action": "deny"},
    )
    trace = client.get(f"/api/agent/trace/{parent['request_id']}").json()

    assert denied.status_code == 200
    assert denied.json()["status"] == "cancelled"
    assert denied.json()["final_response"].startswith("## Confirmation Denied")
    assert trace["status"] == "cancelled"
    assert trace["final_response"].startswith("## Confirmation Denied")
    assert trace["confirmation_required"] is False
    assert trace["confirmation_actions"] == []
    assert trace["display_document"] is None
    assert [event["event_type"] for event in trace["events"]][-1] == "confirmation.denied"
    assert runtime.replay_contexts == []


def test_agent_integration_clarification_blocks_on_answered_child_result() -> None:
    runtime = FakeClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/integrations/execute",
        json={"prompt": "create a conda environment"},
    )
    parent = submitted.json()

    assert submitted.status_code == 200
    assert parent["status"] == "awaiting_clarification"
    assert parent["needs_action"]["type"] == "clarification"
    assert parent["needs_action"]["clarification_request"]["options"][0]["option_id"] == "py311"

    answered = client.post(
        f"/api/agent/integrations/clarify/{parent['request_id']}",
        json={"answer": "Python 3.11", "selected_option_id": "py311"},
    )

    assert answered.status_code == 200
    answered_payload = answered.json()
    assert answered_payload["request_id"] != parent["request_id"]
    assert answered_payload["status"] == "completed"
    assert answered_payload["final_response"] == (
        "answered with Python 3.11: create a conda environment"
    )
    assert runtime.contexts[-1]["clarifications"][-1]["selected_option_id"] == "py311"


def test_agent_integration_routes_are_in_openapi() -> None:
    client = _client()

    schema = client.get("/openapi.json").json()

    for path in (
        "/api/agent/integrations/execute",
        "/api/agent/integrations/results/{request_id}",
        "/api/agent/integrations/confirm/{request_id}",
        "/api/agent/integrations/clarify/{request_id}",
    ):
        assert path in schema["paths"]
    assert schema["paths"]["/api/agent/integrations/execute"]["post"]["tags"] == [
        "integration"
    ]
    component_names = set(schema["components"]["schemas"])
    assert {
        "AgentIntegrationExecutePayload",
        "AgentIntegrationExecutionResponse",
        "AgentIntegrationConfirmationPayload",
        "AgentIntegrationClarificationPayload",
    }.issubset(component_names)


def test_agent_ui_autoapprove_macro_is_request_scoped_and_stripped() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "/autoapprove list files"})
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(submitted.json()["trace_url"]).json()

    assert submitted.status_code == 200
    assert trace["prompt"] == "list files"
    assert trace["final_response"] == "handled: list files"
    assert runtime.last_context["auto_approve_commands"] is True
    assert runtime.last_context["auto_approve_scope"] == "request"
    summaries = runtime.last_context["operator_user_macro_summaries"]
    assert summaries[0]["kind"] == "autoapprove"


def test_agent_ui_autoapprove_macro_can_be_trailing() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": 'stage all changes and commit with msg = "ship it" /autoapprove'},
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(submitted.json()["trace_url"]).json()

    assert submitted.status_code == 200
    assert trace["prompt"] == 'stage all changes and commit with msg = "ship it"'
    assert trace["final_response"] == 'handled: stage all changes and commit with msg = "ship it"'
    assert runtime.last_context["auto_approve_commands"] is True
    assert runtime.last_context["auto_approve_scope"] == "request"
    summaries = runtime.last_context["operator_user_macro_summaries"]
    assert summaries[0]["kind"] == "autoapprove"


def test_agent_ui_autoapprove_regression_for_pasted_git_commit_prompt() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )
    raw_prompt = (
        'git stage all changes and commit with msg '
        '"add more context to the event ui to resolve any ambiguity" /autoapprove'
    )
    expected_prompt = (
        'git stage all changes and commit with msg '
        '"add more context to the event ui to resolve any ambiguity"'
    )

    submitted = client.post("/api/agent/request", json={"prompt": raw_prompt})
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(submitted.json()["trace_url"]).json()

    assert submitted.status_code == 200
    assert trace["prompt"] == expected_prompt
    assert trace["final_response"] == f"handled: {expected_prompt}"
    assert runtime.last_context["auto_approve_commands"] is True
    summaries = runtime.last_context["operator_user_macro_summaries"]
    assert [item["kind"] for item in summaries] == ["autoapprove"]


def test_agent_ui_runtime_controls_are_server_global(tmp_path) -> None:
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    initial = client.get("/api/agent/runtime-controls")
    updated = client.post(
        "/api/agent/runtime-controls",
        json={
            "auto_approve_commands": True,
            "agent_events_enabled": False,
            "agent_learning_ledger_auto_learn_enabled": False,
            "lrnt_enabled": False,
            "lrdirect_enabled": True,
            "operator_policy_profile": "assisted",
            "reasoning_profile": "balanced",
            "repair_profile": "aggressive",
            "workflow_execution_mode": "streaming",
            "prompt_rephrase_enabled": False,
            "response_streaming_enabled": True,
            "sql_agent_chat_route_mode": "direct",
            "operator_workspace_cwd_guard_enabled": True,
            "llm_operator_verbose_enabled": False,
            "llm_operator_final_response_mode": "simple",
            "llm_operator_cardinality_judge_mode": "on",
            "llm_operator_verification_enforced": False,
            "llm_operator_max_clarification_rounds": 7,
            "agent_memory_enabled": False,
            "agent_memory_prompt_max_chars": 4567,
            "agent_command_template_cache_similarity_threshold": 0.74,
            "agent_command_template_cache_secondary_similarity_threshold": 0.31,
            "lrnt_similarity_threshold": 0.93,
            "reliability_max_recovery_probes": 6,
            "reliability_max_autonomous_repair_attempts": 4,
            "reliability_weak_model_plan_action_cap": 9,
            "reliability_approval_envelope_budget": 3,
            "llm_base_scheme": "https",
            "llm_base_host": "llm.local",
            "llm_base_port": 9443,
            "llm_base_path": "/openai/v1",
            "llm_timeout_seconds": 240,
            "llm_max_tokens": 4096,
            "audio_transcriber_service_host": "audio.local",
            "audio_transcriber_service_port": 9012,
            "ui_auto_immersive_min_width_px": 640,
        },
    )
    current = client.get("/api/agent/runtime-controls")

    assert initial.status_code == 200
    assert set(initial.json()) == set(PUBLIC_RUNTIME_CONTROL_KEYS)
    assert initial.json()["auto_approve_commands"] is False
    assert initial.json()["operator_policy_profile"] == "assisted"
    assert initial.json()["reasoning_profile"] == "balanced"
    assert initial.json()["repair_profile"] == "balanced"
    assert initial.json()["workflow_execution_mode"] == "streaming"
    assert initial.json()["prompt_rephrase_enabled"] is True
    assert initial.json()["response_streaming_enabled"] is True
    assert initial.json()["llm_operator_final_response_mode"] == "simple"
    assert initial.json()["agent_learning_ledger_auto_learn_enabled"] is True
    assert initial.json()["lrnt_enabled"] is True
    assert initial.json()["lrdirect_enabled"] is True
    assert initial.json()["agent_memory_enabled"] is True
    assert initial.json()["reliability_mode"] == "standard"
    assert initial.json()["reliability_verifier_enforced"] is True
    assert initial.json()["audio_transcriber_service_url"] == "http://localhost:8012"
    assert "operator_execution_mode" not in initial.json()
    assert "operator_effect_policy_mode" not in initial.json()
    assert "operator_auto_rephrase_retry_enabled" not in initial.json()
    assert updated.status_code == 200
    assert set(updated.json()) == set(PUBLIC_RUNTIME_CONTROL_KEYS)
    assert updated.json()["auto_approve_commands"] is True
    assert updated.json()["agent_events_enabled"] is False
    assert updated.json()["agent_learning_ledger_auto_learn_enabled"] is False
    assert updated.json()["lrnt_enabled"] is False
    assert updated.json()["lrnt_similarity_threshold"] == 0.93
    assert updated.json()["lrdirect_enabled"] is True
    assert updated.json()["operator_policy_profile"] == "assisted"
    assert updated.json()["reasoning_profile"] == "balanced"
    assert updated.json()["repair_profile"] == "aggressive"
    assert updated.json()["workflow_execution_mode"] == "streaming"
    assert updated.json()["prompt_rephrase_enabled"] is False
    assert updated.json()["response_streaming_enabled"] is True
    assert updated.json()["sql_agent_chat_route_mode"] == "direct"
    assert updated.json()["operator_workspace_cwd_guard_enabled"] is True
    assert updated.json()["llm_operator_verbose_enabled"] is False
    assert updated.json()["llm_operator_final_response_mode"] == "simple"
    assert updated.json()["llm_operator_cardinality_judge_mode"] == "on"
    assert updated.json()["llm_operator_verification_enforced"] is False
    assert updated.json()["llm_operator_max_clarification_rounds"] == 7
    assert updated.json()["agent_memory_enabled"] is False
    assert updated.json()["agent_memory_prompt_max_chars"] == 4567
    assert updated.json()["agent_command_template_cache_similarity_threshold"] == 0.74
    assert (
        updated.json()["agent_command_template_cache_secondary_similarity_threshold"]
        == 0.31
    )
    assert updated.json()["reliability_max_recovery_probes"] == 6
    assert updated.json()["reliability_max_autonomous_repair_attempts"] == 4
    assert updated.json()["reliability_weak_model_plan_action_cap"] == 9
    assert updated.json()["reliability_approval_envelope_budget"] == 3
    assert updated.json()["llm_base_scheme"] == "https"
    assert updated.json()["llm_base_host"] == "llm.local"
    assert updated.json()["llm_base_port"] == 9443
    assert updated.json()["llm_base_path"] == "/openai/v1"
    assert updated.json()["llm_base_url"] == "https://llm.local:9443/openai/v1"
    assert updated.json()["llm_timeout_seconds"] == 240
    assert updated.json()["llm_max_tokens"] == 4096
    assert updated.json()["audio_transcriber_service_host"] == "audio.local"
    assert updated.json()["audio_transcriber_service_port"] == 9012
    assert updated.json()["audio_transcriber_service_url"] == "http://audio.local:9012"
    assert updated.json()["ui_auto_immersive_min_width_px"] == 640
    assert current.json() == updated.json()

    partial = client.post("/api/agent/runtime-controls", json={"auto_approve_commands": False})
    assert partial.status_code == 200
    assert partial.json()["auto_approve_commands"] is False
    assert partial.json()["workflow_execution_mode"] == "streaming"
    assert partial.json()["lrnt_enabled"] is False
    assert partial.json()["lrnt_similarity_threshold"] == 0.93
    assert partial.json()["lrdirect_enabled"] is True
    assert partial.json()["agent_command_template_cache_similarity_threshold"] == 0.74
    assert (
        partial.json()["agent_command_template_cache_secondary_similarity_threshold"]
        == 0.31
    )
    assert partial.json()["operator_policy_profile"] == "assisted"

    invalid_policy = client.post(
        "/api/agent/runtime-controls",
        json={"operator_effect_policy_mode": "surprise"},
    )
    assert invalid_policy.status_code == 422
    assert "Removed runtime settings" in invalid_policy.text


def test_agent_ui_settings_preferences_persist_backend_mode_across_clients(tmp_path) -> None:
    db_path = tmp_path / "agent_ui_settings.db"
    first_client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    initial = first_client.get("/api/agent/settings/preferences")
    updated = first_client.put(
        "/api/agent/settings/preferences",
        json={
            "settings": {
                "agent_display_name": "Shared Agent",
                "auto_approve_commands": True,
                "ui_agent_mode": "advisory",
                "ui_theme": "midnight",
                "llm_operator_verbose_enabled": False,
                "operator_policy_profile": "assisted",
                "reasoning_profile": "balanced",
                "repair_profile": "conservative",
                "workflow_execution_mode": "streaming",
                "response_streaming_enabled": True,
                "agent_clarification_mode": "pedantic",
                "llm_operator_final_response_mode": "simple",
                "llm_operator_verification_enforced": False,
                "llm_operator_max_clarification_rounds": 6,
                "agent_memory_enabled": False,
                "agent_memory_prompt_max_chars": 6789,
                "agent_events_enabled": False,
                "agent_learning_ledger_auto_learn_enabled": False,
                "sql_agent_chat_route_mode": "direct",
                "operator_workspace_cwd_guard_enabled": True,
                "ui_auto_immersive_min_width_px": 640,
                "ui_terminal_visible": False,
                "ui_trace_visible": False,
                "ui_visualization_visible": False,
                "ui_chat_bubbles_enabled": True,
                "ui_number_animation": "flip",
                "ui_chat_pop_animation": "spring",
                "ui_thinking_text_animation": "bounce",
                "ui_command_output_expanded_by_default": True,
                "browser_notifications_enabled": False,
                "notification_sound_enabled": False,
                "notification_sound_variant": "alert",
                "notification_sound_volume": 0.25,
                "audio_voice_input_enabled": False,
                "audio_transcriber_service_host": "audio.local",
                "audio_transcriber_service_port": 9012,
                "audio_capture_preset": "low_latency",
                "audio_silence_timeout_seconds": 4,
                "llm_base_scheme": "https",
                "llm_base_host": "llm.local",
                "llm_base_port": 9443,
                "llm_base_path": "/openai/v1",
                "llm_timeout_seconds": 240,
                "llm_max_tokens": 4096,
            },
        },
    )
    second_client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    restarted = second_client.get("/api/agent/settings/preferences")
    removed = second_client.put(
        "/api/agent/settings/preferences",
        json={"backend_persistence_enabled": False},
    )

    assert initial.status_code == 200
    assert initial.json() == {"settings": {}}
    assert updated.status_code == 200
    assert updated.json()["settings"]["agent_display_name"] == "Shared Agent"
    assert updated.json()["settings"]["auto_approve_commands"] is True
    assert updated.json()["settings"]["ui_agent_mode"] == "advisory"
    assert updated.json()["settings"]["ui_theme"] == "midnight"
    assert updated.json()["settings"]["llm_operator_verbose_enabled"] is False
    assert updated.json()["settings"]["operator_policy_profile"] == "assisted"
    assert updated.json()["settings"]["reasoning_profile"] == "balanced"
    assert updated.json()["settings"]["repair_profile"] == "conservative"
    assert updated.json()["settings"]["workflow_execution_mode"] == "streaming"
    assert updated.json()["settings"]["response_streaming_enabled"] is True
    assert updated.json()["settings"]["agent_clarification_mode"] == "pedantic"
    assert updated.json()["settings"]["llm_operator_final_response_mode"] == "simple"
    assert updated.json()["settings"]["llm_operator_verification_enforced"] is False
    assert updated.json()["settings"]["llm_operator_max_clarification_rounds"] == 6
    assert updated.json()["settings"]["agent_memory_enabled"] is False
    assert updated.json()["settings"]["agent_memory_prompt_max_chars"] == 6789
    assert updated.json()["settings"]["agent_events_enabled"] is False
    assert updated.json()["settings"]["agent_learning_ledger_auto_learn_enabled"] is False
    assert updated.json()["settings"]["sql_agent_chat_route_mode"] == "direct"
    assert updated.json()["settings"]["operator_workspace_cwd_guard_enabled"] is True
    assert updated.json()["settings"]["ui_auto_immersive_min_width_px"] == 640
    assert updated.json()["settings"]["ui_terminal_visible"] is False
    assert updated.json()["settings"]["ui_trace_visible"] is False
    assert updated.json()["settings"]["ui_visualization_visible"] is False
    assert updated.json()["settings"]["ui_chat_bubbles_enabled"] is True
    assert updated.json()["settings"]["ui_number_animation"] == "flip"
    assert updated.json()["settings"]["ui_chat_pop_animation"] == "spring"
    assert updated.json()["settings"]["ui_thinking_text_animation"] == "bounce"
    assert updated.json()["settings"]["ui_command_output_expanded_by_default"] is True
    assert updated.json()["settings"]["browser_notifications_enabled"] is False
    assert updated.json()["settings"]["notification_sound_enabled"] is False
    assert updated.json()["settings"]["notification_sound_variant"] == "alert"
    assert updated.json()["settings"]["notification_sound_volume"] == 0.25
    assert updated.json()["settings"]["audio_voice_input_enabled"] is False
    assert updated.json()["settings"]["audio_transcriber_service_host"] == "audio.local"
    assert updated.json()["settings"]["audio_transcriber_service_port"] == 9012
    assert updated.json()["settings"]["audio_capture_preset"] == "low_latency"
    assert updated.json()["settings"]["audio_silence_timeout_seconds"] == 4
    assert updated.json()["settings"]["llm_base_scheme"] == "https"
    assert updated.json()["settings"]["llm_base_host"] == "llm.local"
    assert updated.json()["settings"]["llm_base_port"] == 9443
    assert updated.json()["settings"]["llm_base_path"] == "/openai/v1"
    assert updated.json()["settings"]["llm_timeout_seconds"] == 240
    assert updated.json()["settings"]["llm_max_tokens"] == 4096
    assert restarted.status_code == 200
    assert restarted.json() == updated.json()
    assert removed.status_code == 422


def test_agent_ui_settings_preferences_update_live_runtime_controls(tmp_path) -> None:
    db_path = tmp_path / "agent_ui_settings.db"
    first_client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    initial_runtime = first_client.get("/api/agent/runtime-controls")
    updated_preferences = first_client.put(
        "/api/agent/settings/preferences",
        json={
            "settings": {
                "operator_policy_profile": "assisted",
                "reasoning_profile": "deep",
                "repair_profile": "aggressive",
                "workflow_execution_mode": "streaming",
                "response_streaming_enabled": True,
                "sql_agent_chat_route_mode": "direct",
                "operator_workspace_cwd_guard_enabled": True,
                "agent_clarification_mode": "pedantic",
                "llm_operator_verbose_enabled": False,
                "llm_operator_final_response_mode": "simple",
                "llm_operator_verification_enforced": False,
                "llm_operator_max_clarification_rounds": 6,
                "agent_memory_enabled": False,
                "agent_memory_prompt_max_chars": 6789,
                "agent_events_enabled": False,
                "llm_base_scheme": "https",
                "llm_base_host": "llm.local",
                "llm_base_port": 9443,
                "llm_base_path": "/openai/v1",
                "llm_timeout_seconds": 240,
                "llm_max_tokens": 4096,
            },
        },
    )
    live_runtime = first_client.get("/api/agent/runtime-controls")

    restarted_client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    restarted_runtime = restarted_client.get("/api/agent/runtime-controls")

    assert initial_runtime.status_code == 200
    assert initial_runtime.json()["workflow_execution_mode"] == "streaming"
    assert updated_preferences.status_code == 200
    assert live_runtime.status_code == 200
    assert live_runtime.json()["operator_policy_profile"] == "assisted"
    assert live_runtime.json()["reasoning_profile"] == "deep"
    assert live_runtime.json()["repair_profile"] == "aggressive"
    assert live_runtime.json()["workflow_execution_mode"] == "streaming"
    assert live_runtime.json()["response_streaming_enabled"] is True
    assert live_runtime.json()["sql_agent_chat_route_mode"] == "direct"
    assert live_runtime.json()["operator_workspace_cwd_guard_enabled"] is True
    assert live_runtime.json()["agent_clarification_mode"] == "pedantic"
    assert live_runtime.json()["llm_operator_verbose_enabled"] is False
    assert live_runtime.json()["agent_events_enabled"] is False
    assert live_runtime.json()["llm_operator_final_response_mode"] == "simple"
    assert live_runtime.json()["llm_operator_verification_enforced"] is False
    assert live_runtime.json()["llm_operator_max_clarification_rounds"] == 6
    assert live_runtime.json()["agent_memory_enabled"] is False
    assert live_runtime.json()["agent_memory_prompt_max_chars"] == 6789
    assert live_runtime.json()["llm_base_scheme"] == "https"
    assert live_runtime.json()["llm_base_host"] == "llm.local"
    assert live_runtime.json()["llm_base_port"] == 9443
    assert live_runtime.json()["llm_base_path"] == "/openai/v1"
    assert live_runtime.json()["llm_base_url"] == "https://llm.local:9443/openai/v1"
    assert live_runtime.json()["llm_timeout_seconds"] == 240
    assert live_runtime.json()["llm_max_tokens"] == 4096
    assert restarted_runtime.status_code == 200
    assert restarted_runtime.json()["sql_agent_chat_route_mode"] == "direct"
    assert restarted_runtime.json()["operator_policy_profile"] == "assisted"
    assert restarted_runtime.json()["reasoning_profile"] == "deep"
    assert restarted_runtime.json()["repair_profile"] == "aggressive"
    assert restarted_runtime.json()["workflow_execution_mode"] == "streaming"
    assert restarted_runtime.json()["response_streaming_enabled"] is True
    assert restarted_runtime.json()["operator_workspace_cwd_guard_enabled"] is True
    assert restarted_runtime.json()["llm_operator_verbose_enabled"] is False
    assert restarted_runtime.json()["agent_events_enabled"] is False
    assert restarted_runtime.json()["agent_clarification_mode"] == "pedantic"
    assert restarted_runtime.json()["llm_operator_final_response_mode"] == "simple"
    assert restarted_runtime.json()["llm_operator_verification_enforced"] is False
    assert restarted_runtime.json()["llm_operator_max_clarification_rounds"] == 6
    assert restarted_runtime.json()["agent_memory_enabled"] is False
    assert restarted_runtime.json()["agent_memory_prompt_max_chars"] == 6789
    assert restarted_runtime.json()["llm_base_scheme"] == "https"
    assert restarted_runtime.json()["llm_base_host"] == "llm.local"
    assert restarted_runtime.json()["llm_base_port"] == 9443
    assert restarted_runtime.json()["llm_base_path"] == "/openai/v1"
    assert restarted_runtime.json()["llm_base_url"] == "https://llm.local:9443/openai/v1"
    assert restarted_runtime.json()["llm_timeout_seconds"] == 240
    assert restarted_runtime.json()["llm_max_tokens"] == 4096


def test_agent_ui_runtime_controls_supply_repair_attempts_to_requests(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
            ),
            agent_runtime=runtime,
        )
    )
    controls = client.post(
        "/api/agent/runtime-controls",
        json={
            "repair_profile": "aggressive",
            "operator_policy_profile": "assisted",
            "sql_agent_chat_route_mode": "direct",
            "agent_command_template_cache_similarity_threshold": 0.73,
            "agent_command_template_cache_secondary_similarity_threshold": 0.29,
            "lrnt_similarity_threshold": 0.94,
            "lrdirect_enabled": True,
        },
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "list files", "context": {}, "agent_mode": "llm_operator"},
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    assert controls.status_code == 200
    assert controls.json()["repair_profile"] == "aggressive"
    assert controls.json()["agent_command_template_cache_secondary_similarity_threshold"] == 0.29
    assert runtime.last_context["repair_profile"] == "aggressive"
    assert runtime.last_context["operator_policy_profile"] == "assisted"
    assert runtime.last_context["agent_command_template_cache_secondary_similarity_threshold"] == 0.29
    assert controls.json()["lrnt_similarity_threshold"] == 0.94
    assert runtime.last_context["lrnt_similarity_threshold"] == 0.94
    assert "llm_operator_max_validation_repair_attempts" not in runtime.last_context
    assert "operator_effect_policy_mode" not in runtime.last_context
    assert controls.json()["sql_agent_chat_route_mode"] == "direct"
    assert runtime.last_context["sql_agent_chat_route_mode"] == "direct"
    assert controls.json()["agent_command_template_cache_similarity_threshold"] == 0.73
    assert runtime.last_context["agent_command_template_cache_similarity_threshold"] == 0.73
    assert runtime.last_context["lrdirect_enabled"] is True


def test_agent_ui_runtime_controls_persist_repair_attempts_across_restart(tmp_path) -> None:
    db_path = tmp_path / "agent_ui_settings.db"
    first_client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    updated = first_client.post(
        "/api/agent/runtime-controls",
        json={
            "auto_approve_commands": True,
            "agent_learning_ledger_auto_learn_enabled": False,
            "lrdirect_enabled": True,
            "repair_profile": "aggressive",
            "operator_policy_profile": "assisted",
            "workflow_execution_mode": "streaming",
            "response_streaming_enabled": True,
            "agent_events_enabled": False,
            "operator_workspace_cwd_guard_enabled": True,
            "llm_operator_verbose_enabled": False,
            "llm_operator_step_validation_enabled": False,
            "llm_operator_final_response_mode": "simple",
            "llm_operator_verification_enforced": False,
            "llm_operator_max_clarification_rounds": 8,
            "agent_memory_enabled": False,
            "agent_memory_prompt_max_chars": 8765,
            "agent_command_template_cache_similarity_threshold": 0.66,
            "agent_command_template_cache_secondary_similarity_threshold": 0.28,
            "lrnt_similarity_threshold": 0.95,
            "ui_auto_immersive_min_width_px": 720,
            "llm_base_scheme": "https",
            "llm_base_host": "llm.local",
            "llm_base_port": 9443,
            "llm_base_path": "/openai/v1",
            "llm_timeout_seconds": 240,
            "llm_max_tokens": 4096,
            "audio_transcriber_service_host": "127.0.0.2",
            "audio_transcriber_service_port": 9912,
        },
    )

    restarted_client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=db_path,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    restarted = restarted_client.get("/api/agent/runtime-controls")

    assert updated.status_code == 200
    assert restarted.status_code == 200
    assert restarted.json()["auto_approve_commands"] is True
    assert restarted.json()["agent_learning_ledger_auto_learn_enabled"] is False
    assert restarted.json()["lrdirect_enabled"] is True
    assert restarted.json()["repair_profile"] == "aggressive"
    assert restarted.json()["operator_policy_profile"] == "assisted"
    assert restarted.json()["workflow_execution_mode"] == "streaming"
    assert restarted.json()["response_streaming_enabled"] is True
    assert restarted.json()["agent_events_enabled"] is False
    assert restarted.json()["operator_workspace_cwd_guard_enabled"] is True
    assert restarted.json()["llm_operator_verbose_enabled"] is False
    assert restarted.json()["llm_operator_step_validation_enabled"] is False
    assert restarted.json()["llm_operator_final_response_mode"] == "simple"
    assert restarted.json()["llm_operator_verification_enforced"] is False
    assert restarted.json()["llm_operator_max_clarification_rounds"] == 8
    assert restarted.json()["agent_memory_enabled"] is False
    assert restarted.json()["agent_memory_prompt_max_chars"] == 8765
    assert restarted.json()["agent_command_template_cache_similarity_threshold"] == 0.66
    assert (
        restarted.json()["agent_command_template_cache_secondary_similarity_threshold"]
        == 0.28
    )
    assert restarted.json()["lrnt_similarity_threshold"] == 0.95
    assert restarted.json()["ui_auto_immersive_min_width_px"] == 720
    assert restarted.json()["llm_base_scheme"] == "https"
    assert restarted.json()["llm_base_host"] == "llm.local"
    assert restarted.json()["llm_base_port"] == 9443
    assert restarted.json()["llm_base_path"] == "/openai/v1"
    assert restarted.json()["llm_base_url"] == "https://llm.local:9443/openai/v1"
    assert restarted.json()["llm_timeout_seconds"] == 240
    assert restarted.json()["llm_max_tokens"] == 4096
    assert restarted.json()["audio_transcriber_service_host"] == "127.0.0.2"
    assert restarted.json()["audio_transcriber_service_port"] == 9912


def test_agent_ui_events_draft_interval_prompts(tmp_path) -> None:
    client = _event_client(tmp_path)

    single = client.post(
        "/api/agent/events/draft",
        json={
            "prompt": "check disk usage every hour",
            "agent_mode": "llm_operator",
            "context": {"terminal_cwd": "/tmp/draft-space"},
        },
    )
    multiple = client.post(
        "/api/agent/events/draft",
        json={"prompt": "check disk every hour and rotate logs every 6 hours"},
    )
    ordinary = client.post(
        "/api/agent/events/draft",
        json={"prompt": "list files in this directory"},
    )

    assert single.status_code == 200
    single_draft = single.json()["draft"]
    assert single_draft["is_schedule_request"] is True
    assert single_draft["drafts"][0]["prompt"] == "check disk usage"
    assert single_draft["drafts"][0]["interval_seconds"] == 3600
    assert single_draft["drafts"][0]["auto_approve_confirmations"] is True
    assert single_draft["drafts"][0]["context"]["agent_mode"] == "llm_operator"
    assert single_draft["drafts"][0]["context"]["terminal_cwd"] == "/tmp/draft-space"

    assert multiple.status_code == 200
    multiple_draft = multiple.json()["draft"]
    assert [draft["prompt"] for draft in multiple_draft["drafts"]] == [
        "check disk",
        "rotate logs",
    ]
    assert [draft["interval_seconds"] for draft in multiple_draft["drafts"]] == [3600, 21600]

    assert ordinary.status_code == 200
    assert ordinary.json()["draft"]["is_schedule_request"] is False


def test_agent_event_store_notifications_persist_and_update(tmp_path) -> None:
    store = AgentEventStore(tmp_path / "events.db", max_run_history=3)

    created = store.create_notification(
        AgentNotificationCreate(
            level="warning",
            title="Disk",
            message="Disk usage is high",
            source_type="test",
            source_id="source-1",
        )
    )

    assert created.status == "unread"
    assert store.notification_counts()["unread"] == 1
    assert store.notification_exists(source_type="test", source_id="source-1") is True
    updated = store.update_notification(created.notification_id, AgentNotificationUpdate(status="read"))
    assert updated is not None
    assert updated.status == "read"
    assert store.mark_all_notifications_read() == 0


def test_agent_ui_notification_api_read_and_dismiss(tmp_path) -> None:
    client = _event_client(tmp_path)
    notification = client.app.state.agent_event_store.create_notification(
        AgentNotificationCreate(
            level="info",
            title="Reminder",
            message="Stretch",
            source_type="test",
            source_id="source-api",
        )
    )

    listed = client.get("/api/agent/notifications")
    assert listed.status_code == 200
    assert listed.json()["counts"]["unread"] == 1
    assert listed.json()["notifications"][0]["notification_id"] == notification.notification_id

    read = client.patch(
        f"/api/agent/notifications/{notification.notification_id}",
        json={"status": "read"},
    )
    assert read.status_code == 200
    assert read.json()["notification"]["status"] == "read"

    dismissed = client.patch(
        f"/api/agent/notifications/{notification.notification_id}",
        json={"status": "dismissed"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["counts"]["dismissed"] == 1
    assert client.get("/api/agent/notifications").json()["notifications"] == []


def test_agent_monitor_store_persists_observations_triggers_and_interrupts(tmp_path) -> None:
    store = AgentMonitorStore(tmp_path / "monitors.db")
    monitor = store.create_monitor(
        AgentMonitorCreate(
            title="RAM watch",
            prompt="monitor free ram",
            command="free -m",
            condition="number_lt:2048",
            context={"gateway_id": "gw-1"},
        )
    )
    updated = store.update_monitor(
        monitor.monitor_id,
        AgentMonitorUpdate(status="running", terminal_session_id="mon-term-1"),
    )
    assert updated.status == "running"
    observation = store.add_observation(
        monitor_id=monitor.monitor_id,
        sequence=1,
        kind="sample",
        stdout="1024\n",
        matched=True,
        match_reason="Observed 1024, below 2048.",
    )
    trigger = store.add_trigger(
        monitor_id=monitor.monitor_id,
        observation_id=observation.observation_id,
        reason=observation.match_reason,
        notification_id="note-1",
        task_id="task-1",
    )

    assert store.get_monitor(monitor.monitor_id).latest_observation_id == observation.observation_id
    assert store.list_observations(monitor.monitor_id)[0].stdout == "1024"
    assert store.list_triggers(monitor.monitor_id)[0].trigger_id == trigger.trigger_id
    assert store.counts()["running"] == 1
    assert store.interrupt_active_monitors() == 1
    interrupted = store.get_monitor(monitor.monitor_id)
    assert interrupted.status == "interrupted"
    assert "server restarted" in interrupted.error_preview
    archived = store.archive_monitor(monitor.monitor_id)
    assert archived.status == "archived"
    assert archived.archived_at


def _wait_for_monitor_status(
    client: TestClient,
    monitor_id: str,
    expected_status: str,
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/agent/monitors/{monitor_id}")
        assert response.status_code == 200
        latest = response.json()["monitor"]
        if latest["status"] == expected_status:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for monitor {expected_status}; latest monitor: {latest}")


def test_agent_ui_monitor_api_create_stream_cancel_archive(tmp_path) -> None:
    gateway = FakeMonitorGateway()
    client = _monitor_client(tmp_path, runtime=MonitorAgentRuntime(gateway))

    draft = client.post(
        "/api/agent/monitors/draft",
        json={"prompt": "/monitor free RAM for 1 minute and tell me if below 2GB"},
    )
    assert draft.status_code == 200
    assert draft.json()["draft"]["is_monitor_request"] is True
    assert draft.json()["draft"]["drafts"][0]["command"]

    created = client.post(
        "/api/agent/monitors",
        json={
            "prompt": "monitor sample",
            "title": "Sample monitor",
            "command": "printf ready",
            "condition": "contains:ready",
            "duration_seconds": 1,
            "start_now": True,
        },
    )
    assert created.status_code == 200
    monitor = created.json()["monitor"]
    assert monitor["status"] in {"queued", "running", "triggered"}
    triggered = _wait_for_monitor_status(client, monitor["monitor_id"], "triggered")
    assert triggered["terminal_session_id"].startswith("mon-term")
    assert triggered["trigger_reason"]
    assert gateway.sessions
    assert gateway.commands[0]["execution_context"]["monitor_id"] == monitor["monitor_id"]

    observations = client.get(f"/api/agent/monitors/{monitor['monitor_id']}/observations").json()
    assert observations["observations"][0]["matched"] is True
    assert observations["triggers"][0]["reason"] == triggered["trigger_reason"]

    with client.stream("GET", f"/api/agent/monitors/{monitor['monitor_id']}/stream") as response:
      body = response.read().decode("utf-8")
    assert "event: status" in body
    assert "event: observation" in body

    listed = client.get("/api/agent/monitors").json()
    assert listed["counts"]["triggered"] >= 1
    assert any(item["monitor_id"] == monitor["monitor_id"] for item in listed["monitors"])

    queued = client.post(
        "/api/agent/monitors",
        json={"title": "Queued", "command": "sleep 60", "start_now": False},
    ).json()["monitor"]
    cancelled = client.post(f"/api/agent/monitors/{queued['monitor_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["monitor"]["status"] == "cancelled"
    archived = client.post(f"/api/agent/monitors/{queued['monitor_id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["monitor"]["status"] == "archived"


def test_agent_monitor_runtime_capabilities_create_inspect_and_stop(tmp_path) -> None:
    gateway = FakeMonitorGateway(
        chunks=[
            {"type": "stdout", "text": "available_mb=4096\n"},
            {"type": "completed", "exit_code": 0},
        ]
    )
    client = _monitor_client(tmp_path, runtime=MonitorAgentRuntime(gateway))
    manager = client.app.state.agent_monitor_manager
    registry = build_default_registry()

    start_manifest = registry.get("runtime.start_monitor").manifest
    inspect_manifest = registry.get("runtime.inspect_monitors").manifest
    stop_manifest = registry.get("runtime.stop_monitor").manifest
    assert start_manifest.read_only is False
    assert start_manifest.requires_confirmation is True
    assert stop_manifest.read_only is False
    assert inspect_manifest.read_only is True
    assert inspect_manifest.requires_confirmation is False

    start = registry.get("runtime.start_monitor").execute(
        {
            "prompt": "monitor sample",
            "title": "Runtime monitor",
            "command": "printf available_mb=4096",
            "duration_seconds": 1,
            "condition": "contains:available_mb",
            "start_now": True,
        },
        {"node_id": "node-start-monitor", "execution_context": {"monitor_manager": manager}},
    )
    assert start.status == "success"
    monitor_id = start.data_preview["monitor_id"]
    assert monitor_id
    assert start.data_preview["stream_url"].endswith(f"/{monitor_id}/stream")
    _wait_for_monitor_status(client, monitor_id, "triggered")

    inspect = registry.get("runtime.inspect_monitors").execute(
        {"operation": "observations", "monitor_id": monitor_id},
        {"node_id": "node-inspect-monitor", "execution_context": {"monitor_manager": manager}},
    )
    assert inspect.status == "success"
    assert inspect.data_preview["observations"][0]["matched"] is True

    stop = registry.get("runtime.stop_monitor").execute(
        {"monitor_id": monitor_id, "action": "archive"},
        {"node_id": "node-stop-monitor", "execution_context": {"monitor_manager": manager}},
    )
    assert stop.status == "success"
    assert stop.data_preview["status"] == "archived"


def test_agent_monitor_runtime_capability_requests_missing_details(tmp_path) -> None:
    client = _monitor_client(tmp_path, runtime=MonitorAgentRuntime(FakeMonitorGateway()))
    result = build_default_registry().get("runtime.start_monitor").execute(
        {"prompt": "monitor this thing for five minutes"},
        {
            "node_id": "node-start-monitor",
            "execution_context": {"monitor_manager": client.app.state.agent_monitor_manager},
        },
    )

    assert result.status == "success"
    assert result.data_preview["needs_clarification"] is True
    assert "command" in result.data_preview["missing_details"]


def test_agent_task_store_persists_attempts_checkpoints_and_interrupts(tmp_path) -> None:
    store = AgentTaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        AgentTaskCreate(
            prompt="build durable task mode",
            title="Durable task",
            context={"gateway_id": "gw-1"},
        )
    )
    attempt = store.create_attempt(task_id=task.task_id, request_id="req-1", status="running")
    store.update_task(
        task.task_id,
        AgentTaskUpdate(
            status="running",
            current_request_id="req-1",
            latest_request_id="req-1",
            current_attempt_id=attempt.attempt_id,
        ),
    )
    checkpoint = store.add_checkpoint(
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        request_id="req-1",
        trace_event_id=1,
        stage="execution",
        event_type="stage.completed",
        title="Executed",
        summary="The task made progress.",
        detail={"secret": "redacted"},
    )

    assert checkpoint is not None
    assert store.add_checkpoint(
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        request_id="req-1",
        trace_event_id=1,
    ) is None
    assert store.get_task_by_request_id("req-1").task_id == task.task_id
    assert store.latest_checkpoint(task.task_id).summary == "The task made progress."
    assert store.interrupt_active_tasks() == 1
    interrupted = store.get_task(task.task_id)
    assert interrupted.status == "interrupted"
    assert "server restarted" in interrupted.blocker_reason
    archived = store.archive_task(task.task_id)
    assert archived.archived_at
    assert store.list_tasks(status="archived")[0].task_id == task.task_id
    assert store.delete_task(task.task_id) is True
    assert store.get_task(task.task_id) is None
    assert store.list_attempts(task.task_id) == []
    assert store.list_checkpoints(task.task_id) == []


def test_agent_ui_task_api_create_start_retry_cancel_archive(tmp_path) -> None:
    runtime = CountingAgentRuntime()
    client = _task_client(tmp_path, runtime=runtime, default_model="configured-model")

    created = client.post(
        "/api/agent/tasks",
        json={
            "prompt": "summarize task mode",
            "title": "Task mode",
            "start_now": True,
            "llm_model": "configured-model",
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["request_id"]
    task = payload["task"]
    assert task["status"] == "running"
    assert task["current_request_id"] == payload["request_id"]

    with client.stream("GET", payload["stream_url"]) as response:
        _ = response.read()

    completed = _wait_for_task_status(client, task["task_id"], "completed")
    listed = client.get("/api/agent/tasks").json()
    assert listed["tasks"][0]["task_id"] == task["task_id"]
    assert completed["latest_checkpoint"]
    assert runtime.calls == 1
    assert task["context"]["llm_model"] == "configured-model"
    assert runtime.last_context["llm_model"] == "configured-model"

    attempts = client.get(f"/api/agent/tasks/{task['task_id']}/attempts").json()["attempts"]
    assert attempts[0]["request_id"] == payload["request_id"]
    checkpoints = client.get(f"/api/agent/tasks/{task['task_id']}/checkpoints").json()["checkpoints"]
    assert checkpoints
    assert any(checkpoint["event_type"] == "durable_task.started" for checkpoint in checkpoints)

    retried = client.post(f"/api/agent/tasks/{task['task_id']}/retry")
    assert retried.status_code == 200
    with client.stream("GET", retried.json()["stream_url"]) as response:
        _ = response.read()
    _wait_for_task_status(client, task["task_id"], "completed")
    assert runtime.calls == 2

    cancelled_task = client.post(
        "/api/agent/tasks",
        json={"prompt": "queued only", "title": "Queued", "start_now": False},
    ).json()["task"]
    cancelled = client.post(f"/api/agent/tasks/{cancelled_task['task_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["task"]["status"] == "cancelled"
    archived = client.post(f"/api/agent/tasks/{cancelled_task['task_id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["task"]["archived_at"]
    archived_list = client.get("/api/agent/tasks", params={"status": "archived"})
    assert archived_list.status_code == 200
    assert archived_list.json()["tasks"][0]["task_id"] == cancelled_task["task_id"]
    deleted = client.delete(f"/api/agent/tasks/{cancelled_task['task_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/agent/tasks/{cancelled_task['task_id']}").status_code == 404

    chat_task = client.post(
        "/api/agent/tasks",
        json={
            "prompt": "run this from chat",
            "start_now": False,
            "context": {"durable_task_create_source": "chat"},
        },
    ).json()["task"]
    assert chat_task["source"] == "chat"
    assert "durable_task_create_source" not in chat_task["context"]

    cleanup_task = client.post(
        "/api/agent/tasks",
        json={"prompt": "cleanup archived", "title": "Cleanup", "start_now": False},
    ).json()["task"]
    assert client.post(f"/api/agent/tasks/{cleanup_task['task_id']}/archive").status_code == 200
    cleared = client.delete("/api/agent/tasks", params={"status": "archived"})
    assert cleared.status_code == 200
    assert cleared.json()["deleted"] == 1


def test_agent_ui_task_start_now_starts_oldest_queued_task_first(tmp_path) -> None:
    runtime = BlockingFirstRequestRuntime()
    client = _task_client(tmp_path, runtime=runtime)

    first = client.post(
        "/api/agent/tasks",
        json={
            "prompt": "first submitted task",
            "title": "First",
            "start_now": False,
        },
    )
    assert first.status_code == 200
    first_task = first.json()["task"]
    assert first_task["status"] == "queued"

    second = client.post(
        "/api/agent/tasks",
        json={
            "prompt": "second submitted task",
            "title": "Second",
            "start_now": True,
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    second_task = second_payload["task"]
    assert second_payload["status"] == "queued"
    assert second_task["status"] == "queued"
    assert second_payload["started_task"]["task_id"] == first_task["task_id"]
    assert second_payload["started_request_id"]
    assert runtime.first_started.wait(2)

    running_first = client.get(f"/api/agent/tasks/{first_task['task_id']}").json()["task"]
    assert running_first["status"] == "running"
    assert running_first["current_request_id"] == second_payload["started_request_id"]
    assert client.get(f"/api/agent/tasks/{second_task['task_id']}").json()["task"]["status"] == "queued"
    assert runtime.prompts == ["first submitted task"]

    runtime.release_first.set()
    with client.stream("GET", second_payload["started_stream_url"]) as response:
        _ = response.read()

    _wait_for_task_status(client, first_task["task_id"], "completed", timeout=5.0)
    completed_second = _wait_for_task_status(client, second_task["task_id"], "completed", timeout=5.0)
    assert completed_second["latest_request_id"]
    assert runtime.prompts == ["first submitted task", "second submitted task"]


def test_agent_ui_task_auto_approves_confirmation_without_opening_chat(tmp_path) -> None:
    runtime = FakeConfirmationRuntime()
    client = _task_client(tmp_path, runtime=runtime)

    controls = client.post("/api/agent/runtime-controls", json={"auto_approve_commands": True})
    assert controls.status_code == 200
    assert controls.json()["auto_approve_commands"] is True

    created = client.post(
        "/api/agent/tasks",
        json={
            "prompt": "write mem.txt",
            "title": "Confirmed task",
            "start_now": True,
        },
    )

    assert created.status_code == 200
    task_id = created.json()["task"]["task_id"]
    completed = _wait_for_task_status(client, task_id, "completed")
    assert runtime.replay_contexts[-1]["confirmation"] is True
    assert runtime.replay_contexts[-1]["auto_approved_confirmation"] is True
    assert runtime.replay_contexts[-1]["durable_task_id"] == task_id
    assert completed["latest_request_id"] != created.json()["request_id"]
    latest_trace = client.get(f"/api/agent/trace/{completed['latest_request_id']}").json()
    assert latest_trace["final_response"] == "approved: write mem.txt"
    assert any(
        event["event_type"] == "confirmation.auto_approved"
        for event in latest_trace["events"]
    )
    assert any(
        event["event_type"] == "durable_task.auto_approved"
        for event in latest_trace["events"]
    )

    notifications = client.get("/api/agent/notifications").json()["notifications"]
    assert not [
        item
        for item in notifications
        if item["metadata"].get("task_id") == task_id
        and item["metadata"].get("task_status") == "awaiting_confirmation"
    ]
    assert any(
        item["metadata"].get("task_id") == task_id
        and item["metadata"].get("task_status") == "completed"
        for item in notifications
    )


def test_agent_ui_task_auto_approval_uses_background_terminal_for_terminal_actions(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakeTerminalConfirmationRuntime()
    client = _task_client(tmp_path, runtime=runtime)
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __init__(self, body: dict[str, Any] | None = None) -> None:
            self._body = body

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            if self._body is not None:
                return json.dumps(self._body).encode("utf-8")
            payload = json.loads(captured["data"].decode("utf-8"))
            return json.dumps(
                {
                    "ok": True,
                    "session_id": payload["session_id"],
                    "cwd": payload["initial_cwd"],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        if str(request.full_url).endswith("/models"):
            return FakeHTTPResponse({"data": []})
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)
    controls = client.post("/api/agent/runtime-controls", json={"auto_approve_commands": True})
    assert controls.status_code == 200

    created = client.post(
        "/api/agent/tasks",
        json={
            "prompt": "push git changes",
            "title": "Push",
            "start_now": True,
            "context": {
                "terminal_cwd": str(tmp_path),
            },
        },
    )

    assert created.status_code == 200
    task_id = created.json()["task"]["task_id"]
    completed = _wait_for_task_status(client, task_id, "completed")
    request_payload = json.loads(captured["data"].decode("utf-8"))
    assert captured["url"] == "http://127.0.0.1:8787/terminal/session"
    assert request_payload["node"] == "localhost"
    assert request_payload["initial_cwd"] == str(tmp_path)
    assert request_payload["session_id"].startswith("term-")
    assert runtime.replay_contexts[-1]["terminal_session_id"] == request_payload["session_id"]
    assert runtime.replay_contexts[-1]["terminal_cwd"] == str(tmp_path)
    assert runtime.replay_contexts[-1]["execute_in_terminal"] is True
    assert runtime.replay_contexts[-1]["durable_task_background_terminal"] is True
    assert runtime.replay_contexts[-1]["background_terminal"] is True
    assert completed["status"] == "completed"


def test_agent_ui_start_now_task_preloads_background_terminal_for_gateway_context(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakeAgentRuntime()
    client = _task_client(tmp_path, runtime=runtime)
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __init__(self, body: dict[str, Any] | None = None) -> None:
            self._body = body

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            if self._body is not None:
                return json.dumps(self._body).encode("utf-8")
            payload = json.loads(captured["data"].decode("utf-8"))
            return json.dumps(
                {
                    "ok": True,
                    "session_id": payload["session_id"],
                    "cwd": payload["initial_cwd"],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        if str(request.full_url).endswith("/models"):
            return FakeHTTPResponse({"data": []})
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)

    created = client.post(
        "/api/agent/tasks",
        json={
            "prompt": "pull latest changes",
            "title": "Pull",
            "start_now": True,
            "context": {
                "gateway_id": "",
                "terminal_cwd": str(tmp_path),
            },
        },
    )

    assert created.status_code == 200
    task_id = created.json()["task"]["task_id"]
    _wait_for_task_status(client, task_id, "completed")
    request_payload = json.loads(captured["data"].decode("utf-8"))
    assert captured["url"] == "http://127.0.0.1:8787/terminal/session"
    assert request_payload["initial_cwd"] == str(tmp_path)
    assert runtime.last_context["terminal_session_id"] == request_payload["session_id"]
    assert runtime.last_context["terminal_cwd"] == str(tmp_path)
    assert runtime.last_context["execute_in_terminal"] is True
    assert runtime.last_context["durable_task_background_terminal"] is True
    assert runtime.last_context["background_terminal"] is True


def test_agent_ui_task_api_queues_start_now_while_chat_running(tmp_path) -> None:
    runtime = BlockingFirstRequestRuntime()
    client = _task_client(tmp_path, runtime=runtime, default_model="configured-model")

    chat = client.post("/api/agent/request", json={"prompt": "long running chat"})
    assert chat.status_code == 200
    chat_payload = chat.json()
    assert runtime.first_started.wait(2)
    _wait_for_trace_status(client, chat_payload["request_id"], "running")

    created = client.post(
        "/api/agent/tasks",
        json={
            "prompt": "run after chat",
            "title": "Queued behind chat",
            "start_now": True,
            "llm_model": "configured-model",
            "context": {
                "durable_task_create_source": "chat",
                "auto_approve_commands": True,
                "auto_approve_scope": "request",
            },
        },
    )
    assert created.status_code == 200
    payload = created.json()
    task = payload["task"]
    assert payload["status"] == "queued"
    assert not payload.get("request_id")
    assert task["status"] == "queued"
    assert task["source"] == "chat"
    assert task["context"]["llm_model"] == "configured-model"
    assert task["context"]["auto_approve_commands"] is True
    assert "durable_task_create_source" not in task["context"]
    assert task["current_request_id"] == ""
    assert task["blocker_reason"] == "Waiting for the active chat request to finish."
    assert runtime.calls == 1

    runtime.release_first.set()
    with client.stream("GET", chat_payload["stream_url"]) as response:
        _ = response.read()
    _wait_for_trace_status(client, chat_payload["request_id"], "completed")

    completed = _wait_for_task_status(client, task["task_id"], "completed", timeout=5.0)
    assert completed["latest_request_id"]
    assert completed["current_request_id"] == ""
    assert runtime.calls == 2
    assert runtime.prompts == ["long running chat", "run after chat"]
    assert runtime.last_context["llm_model"] == "configured-model"
    assert runtime.last_context["auto_approve_commands"] is True


def test_agent_ui_task_queue_waits_for_confirmation_followup(tmp_path) -> None:
    runtime = OneConfirmationThenCountingRuntime()
    client = _task_client(tmp_path, runtime=runtime)

    chat = client.post("/api/agent/request", json={"prompt": "needs approval first"})
    assert chat.status_code == 200
    first_request_id = chat.json()["request_id"]
    with client.stream("GET", chat.json()["stream_url"]) as response:
        _ = response.read()
    trace = client.get(f"/api/agent/trace/{first_request_id}").json()
    assert trace["confirmation_required"] is True

    created = client.post(
        "/api/agent/tasks",
        json={"prompt": "run only after approval", "title": "Queued behind approval", "start_now": True},
    )

    assert created.status_code == 200
    payload = created.json()
    task = payload["task"]
    assert payload["status"] == "queued"
    assert task["status"] == "queued"
    assert runtime.calls == 1

    approved = client.post(
        f"/api/agent/confirmation/{first_request_id}",
        json={"action": "approve"},
    )
    assert approved.status_code == 200
    with client.stream("GET", approved.json()["stream_url"]) as response:
        _ = response.read()

    completed = _wait_for_task_status(client, task["task_id"], "completed", timeout=5.0)
    assert completed["latest_request_id"]
    assert runtime.calls == 2
    assert runtime.prompts == ["needs approval first", "run only after approval"]
    assert runtime.replay_contexts[-1]["confirmation"] is True


def test_agent_ui_task_queue_releases_after_confirmation_denial(tmp_path) -> None:
    runtime = OneConfirmationThenCountingRuntime()
    client = _task_client(tmp_path, runtime=runtime)

    chat = client.post("/api/agent/request", json={"prompt": "deny this first"})
    assert chat.status_code == 200
    first_request_id = chat.json()["request_id"]
    with client.stream("GET", chat.json()["stream_url"]) as response:
        _ = response.read()

    created = client.post(
        "/api/agent/tasks",
        json={"prompt": "run after denial", "title": "Queued behind denial", "start_now": True},
    )
    assert created.status_code == 200
    task = created.json()["task"]
    assert task["status"] == "queued"
    assert runtime.calls == 1

    denied = client.post(
        f"/api/agent/confirmation/{first_request_id}",
        json={"action": "deny"},
    )
    assert denied.status_code == 200

    completed = _wait_for_task_status(client, task["task_id"], "completed", timeout=5.0)
    assert completed["latest_request_id"]
    assert runtime.calls == 2
    assert runtime.prompts == ["deny this first", "run after denial"]


def test_agent_ui_scheduled_agent_event_creates_linked_task(tmp_path) -> None:
    runtime = CountingAgentRuntime()
    client = _event_client(tmp_path, runtime=runtime)

    event = client.post(
        "/api/agent/events",
        json={
            "title": "Check disk",
            "prompt": "check disk usage",
            "schedule_type": "once",
            "interval_seconds": 60,
            "action_type": "agent_prompt",
        },
    ).json()["event"]
    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger").json()

    assert triggered["task_id"]
    assert triggered["request_id"]
    with client.stream("GET", triggered["stream_url"]) as response:
        _ = response.read()

    tasks = client.get("/api/agent/tasks").json()["tasks"]
    assert tasks[0]["task_id"] == triggered["task_id"]
    assert tasks[0]["event_id"] == event["event_id"]
    assert tasks[0]["source_event"]["exists"] is True
    assert tasks[0]["source_event"]["title"] == "Check disk"
    assert tasks[0]["source_event"]["status"] == "active"
    assert tasks[0]["status"] == "completed"
    runs = client.get(f"/api/agent/events/{event['event_id']}/runs").json()["runs"]
    assert runs[0]["task_id"] == triggered["task_id"]
    assert runs[0]["status"] == "completed"
    assert runtime.calls == 1


def test_agent_ui_scheduled_task_marks_missing_source_event(tmp_path) -> None:
    client = _task_client(tmp_path)
    task = client.app.state.agent_task_store.create_task(
        AgentTaskCreate(
            prompt="check disk usage",
            title="Check disk",
            source="scheduled_event",
            event_id="evt-missing",
            event_run_id="evt-run-missing",
        )
    )

    tasks = client.get("/api/agent/tasks").json()["tasks"]

    assert tasks[0]["task_id"] == task.task_id
    assert tasks[0]["source_event"] == {
        "exists": False,
        "event_id": "evt-missing",
        "title": "",
        "status": "missing",
        "event_kind": "scheduled",
    }


def test_agent_ui_events_draft_one_shot_reminder_notification(tmp_path) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/draft",
        json={"prompt": "remind me to stretch after 15 mins"},
    )

    assert response.status_code == 200
    draft = response.json()["draft"]["drafts"][0]
    assert draft["schedule_type"] == "once"
    assert draft["action_type"] == "notification"
    assert draft["interval_seconds"] == 900
    assert draft["notification_message"] == "stretch"


def test_agent_ui_events_draft_one_shot_todo_notification(tmp_path) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/draft",
        json={"prompt": "todo to stretch after 15 mins"},
    )

    assert response.status_code == 200
    draft = response.json()["draft"]["drafts"][0]
    assert draft["schedule_type"] == "once"
    assert draft["action_type"] == "notification"
    assert draft["interval_seconds"] == 900
    assert draft["notification_message"] == "stretch"


def test_agent_ui_events_draft_date_based_reminder_notification(tmp_path) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/draft",
        json={
            "prompt": "Remind me on 6/01/2099 to pay Aegis insurance",
            "context": {"timezone": "UTC"},
        },
    )

    assert response.status_code == 200
    payload = response.json()["draft"]
    assert payload["is_schedule_request"] is True
    draft = payload["drafts"][0]
    assert draft["schedule_type"] == "once"
    assert draft["action_type"] == "notification"
    assert draft["notification_message"] == "pay Aegis insurance"
    assert draft["timezone"] == "UTC"
    assert draft["next_run_at"] == "2099-06-01T09:00:00+00:00"
    assert draft["schedule_summary"] == "On June 1, 2099 at 9:00 AM UTC"


def test_agent_ui_events_from_prompt_creates_one_shot_seconds_reminder(tmp_path) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "remind me to eat after 10 seconds"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_schedule_request"] is True
    assert payload["created_count"] == 1
    event = payload["events"][0]
    assert event["schedule_type"] == "once"
    assert event["action_type"] == "notification"
    assert event["interval_seconds"] == 10
    assert event["notification_message"] == "eat"
    assert "Saved reminder: eat after 10 seconds." == payload["message"]


def test_agent_ui_events_from_prompt_creates_date_based_reminder(tmp_path) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/from-prompt",
        json={
            "prompt": "Remind me on 6/01/2099 at 5:30 pm to pay Aegis insurance",
            "context": {"timezone": "UTC"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_schedule_request"] is True
    assert payload["created_count"] == 1
    event = payload["events"][0]
    assert event["schedule_type"] == "once"
    assert event["action_type"] == "notification"
    assert event["notification_message"] == "pay Aegis insurance"
    assert event["next_run_at"] == "2099-06-01T17:30:00+00:00"
    assert event["context"]["_scheduled_event_schedule_summary"] == (
        "On June 1, 2099 at 5:30 PM UTC"
    )
    assert payload["message"] == (
        "Saved reminder: pay Aegis insurance on June 1, 2099 at 5:30 PM UTC."
    )


def test_agent_ui_events_from_prompt_timed_appointments_are_not_todos(tmp_path) -> None:
    client = _event_client(tmp_path)

    appointment = client.post(
        "/api/agent/events/from-prompt",
        json={
            "prompt": "Remind me MRI appointment on June 5th 10.00 AM 2099",
            "context": {"timezone": "UTC"},
        },
    )
    inferred_year = client.post(
        "/api/agent/events/from-prompt",
        json={
            "prompt": "remind me on June 5th at 10.00 AM that I have an MRI appointment",
            "context": {"timezone": "UTC"},
        },
    )
    typo = client.post(
        "/api/agent/events/from-prompt",
        json={
            "prompt": "Rmeind me for Phyio therapy at June 9 2099 11 aM",
            "context": {"timezone": "UTC"},
        },
    )

    assert appointment.status_code == 200
    appointment_payload = appointment.json()
    appointment_event = appointment_payload["events"][0]
    assert appointment_event["event_kind"] == "scheduled"
    assert appointment_event["action_type"] == "notification"
    assert appointment_event["notification_message"] == "MRI appointment"
    assert appointment_event["next_run_at"] == "2099-06-05T10:00:00+00:00"
    assert appointment_payload["message"].startswith("Saved reminder: MRI appointment on ")

    assert inferred_year.status_code == 200
    inferred_year_event = inferred_year.json()["events"][0]
    assert inferred_year_event["event_kind"] == "scheduled"
    assert inferred_year_event["next_run_at"][4:] == "-06-05T10:00:00+00:00"
    assert inferred_year_event["notification_message"] == "I have an MRI appointment"

    assert typo.status_code == 200
    typo_event = typo.json()["events"][0]
    assert typo_event["event_kind"] == "scheduled"
    assert typo_event["action_type"] == "notification"
    assert typo_event["notification_message"] == "Phyio therapy"
    assert typo_event["next_run_at"] == "2099-06-09T11:00:00+00:00"


def test_agent_ui_events_from_prompt_scheduled_llm_todo_is_promoted(tmp_path) -> None:
    class EventLLMClient(FakeLLMClient):
        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(prompt)
            return {
                "is_schedule_request": True,
                "drafts": [
                    {
                        "title": "MRI appointment",
                        "prompt": "MRI appointment",
                        "schedule_type": "once",
                        "event_kind": "todo",
                        "interval_seconds": 60,
                        "timezone": "UTC",
                        "next_run_at": "2099-06-05T10:00:00+00:00",
                        "schedule_summary": "On June 5, 2099 at 10:00 AM UTC",
                        "confidence": 0.9,
                        "missing_details": [],
                        "auto_approve_confirmations": True,
                        "context": {},
                        "action_type": "notification",
                        "notification_message": "MRI appointment",
                    }
                ],
                "missing_details": [],
                "rationale": "structured",
            }

    llm = EventLLMClient()
    client = _event_client(tmp_path, runtime=SimpleNamespace(llm_client=llm))

    response = client.post(
        "/api/agent/events/from-prompt",
        json={
            "prompt": "Remind me MRI appointment on June 5th 10.00 AM 2099",
            "context": {"timezone": "UTC"},
        },
    )

    assert response.status_code == 200
    assert llm.prompts
    event = response.json()["events"][0]
    assert event["event_kind"] == "scheduled"
    assert event["next_run_at"] == "2099-06-05T10:00:00+00:00"
    assert response.json()["message"].startswith("Saved reminder: MRI appointment on ")


def test_agent_ui_events_from_prompt_date_needs_schedule_intent(tmp_path) -> None:
    client = _event_client(tmp_path)

    ordinary = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "what happened on 6/01/2099"},
    )
    runlater = client.post(
        "/api/agent/events/from-prompt",
        json={
            "prompt": "/runlater on 6/01/2099 check disk usage",
            "context": {"timezone": "UTC"},
        },
    )

    assert ordinary.status_code == 200
    assert ordinary.json()["is_schedule_request"] is False
    assert ordinary.json()["created_count"] == 0

    assert runlater.status_code == 200
    event = runlater.json()["events"][0]
    assert event["action_type"] == "agent_prompt"
    assert event["prompt"] == "check disk usage"
    assert event["next_run_at"] == "2099-06-01T09:00:00+00:00"


def test_agent_ui_events_from_prompt_creates_one_shot_agent_action(tmp_path) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/from-prompt",
        json={
            "prompt": 'after 25 mins git stage and commit with msg "x"',
            "agent_mode": "llm_operator",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_schedule_request"] is True
    assert payload["created_count"] == 1
    event = payload["events"][0]
    assert event["schedule_type"] == "once"
    assert event["action_type"] == "agent_prompt"
    assert event["interval_seconds"] == 1500
    assert event["prompt"] == 'git stage and commit with msg "x"'
    assert event["notification_message"] == ""
    assert event["context"]["agent_mode"] == "llm_operator"

    quick_action = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "in 10 seconds check disk usage"},
    )
    assert quick_action.status_code == 200
    quick_event = quick_action.json()["events"][0]
    assert quick_event["action_type"] == "agent_prompt"
    assert quick_event["interval_seconds"] == 60
    assert quick_event["prompt"] == "check disk usage"


def test_agent_ui_triggered_scheduled_event_persists_chat_turn(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_events_db_path=tmp_path / "agent_events.db",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_tasks_db_path=tmp_path / "agent_tasks.db",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
                agent_events_poll_seconds=60,
            ),
            agent_runtime=runtime,
        )
    )

    created = client.post(
        "/api/agent/events",
        json={
            "title": "Check disk",
            "prompt": "check disk usage",
            "schedule_type": "once",
            "interval_seconds": 60,
            "action_type": "agent_prompt",
        },
    ).json()["event"]
    triggered = client.post(f"/api/agent/events/{created['event_id']}/trigger").json()
    with client.stream("GET", triggered["stream_url"]) as response:
        _ = response.read()

    with client.app.state.agent_trace_store._lock:
        client.app.state.agent_trace_store._traces.clear()

    restored = client.get(f"/api/agent/trace/{triggered['request_id']}")
    listing = client.get("/api/agent/chats").json()

    assert restored.status_code == 200
    assert restored.json()["trace_source"] == "chat_history"
    assert restored.json()["final_response"] == "handled: check disk usage"
    assert listing["chats"][0]["last_request_id"] == triggered["request_id"]


def test_agent_ui_event_macros_force_one_shot_action_type(tmp_path) -> None:
    client = _event_client(tmp_path)

    runlater = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": '/runlater after 25 mins git stage and commit with msg "x"'},
    )
    remind = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": '/remind after 25 mins git stage and commit with msg "x"'},
    )
    todo = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": '/todo after 25 mins git stage and commit with msg "x"'},
    )

    assert runlater.status_code == 200
    runlater_event = runlater.json()["events"][0]
    assert runlater_event["action_type"] == "agent_prompt"
    assert runlater_event["schedule_type"] == "once"
    assert runlater_event["prompt"] == 'git stage and commit with msg "x"'
    assert runlater_event["notification_message"] == ""
    assert runlater_event["context"]["_scheduled_event_extraction_hint"] == "agent_prompt"
    assert USER_MACRO_SUMMARY_CONTEXT_KEY not in runlater_event["context"]

    assert remind.status_code == 200
    remind_event = remind.json()["events"][0]
    assert remind_event["action_type"] == "notification"
    assert remind_event["schedule_type"] == "once"
    assert remind_event["notification_message"] == 'git stage and commit with msg "x"'
    assert remind_event["context"]["_scheduled_event_extraction_hint"] == "notification"

    assert todo.status_code == 200
    todo_event = todo.json()["events"][0]
    assert todo_event["action_type"] == "notification"
    assert todo_event["schedule_type"] == "once"
    assert todo_event["notification_message"] == 'git stage and commit with msg "x"'
    assert todo_event["context"]["_scheduled_event_extraction_hint"] == "notification"


def test_agent_ui_events_from_prompt_creates_recurring_notification_and_agent_task(tmp_path) -> None:
    client = _event_client(tmp_path)

    reminder = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "remind me every hour to drink water"},
    )
    task = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "check disk usage every hour", "agent_mode": "llm_operator"},
    )

    assert reminder.status_code == 200
    reminder_event = reminder.json()["events"][0]
    assert reminder_event["schedule_type"] == "interval"
    assert reminder_event["action_type"] == "notification"
    assert reminder_event["interval_seconds"] == 3600
    assert reminder_event["notification_message"] == "drink water"

    assert task.status_code == 200
    task_event = task.json()["events"][0]
    assert task_event["schedule_type"] == "interval"
    assert task_event["action_type"] == "agent_prompt"
    assert task_event["prompt"] == "check disk usage"
    assert task_event["context"]["agent_mode"] == "llm_operator"


def test_agent_ui_events_from_prompt_creates_recurring_todo_notification(tmp_path) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "todo every hour to drink water"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_schedule_request"] is True
    assert payload["created_count"] == 1
    event = payload["events"][0]
    assert event["schedule_type"] == "interval"
    assert event["action_type"] == "notification"
    assert event["interval_seconds"] == 3600
    assert event["notification_message"] == "drink water"


def test_agent_ui_events_from_prompt_creates_unscheduled_todo(tmp_path) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "todo pay insurance"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_schedule_request"] is True
    assert payload["created_count"] == 1
    event = payload["events"][0]
    assert event["event_kind"] == "todo"
    assert event["action_type"] == "notification"
    assert event["notification_message"] == "pay insurance"
    assert event["next_run_at"] == ""
    assert payload["message"] == "Saved todo: pay insurance."


def test_agent_ui_events_from_prompt_creates_recurring_notification_from_leading_schedule(
    tmp_path,
) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "every hour remind me to drink a glass of water"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_schedule_request"] is True
    assert payload["created_count"] == 1
    event = payload["events"][0]
    assert event["schedule_type"] == "interval"
    assert event["action_type"] == "notification"
    assert event["interval_seconds"] == 3600
    assert event["notification_message"] == "drink a glass of water"


def test_agent_ui_events_from_prompt_creates_multiple_events(tmp_path) -> None:
    client = _event_client(tmp_path)

    response = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "check disk every hour and rotate logs every 6 hours"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created_count"] == 2
    assert [event["prompt"] for event in payload["events"]] == ["check disk", "rotate logs"]
    assert [event["interval_seconds"] for event in payload["events"]] == [3600, 21600]


def test_agent_ui_events_from_prompt_uses_llm_structured_payload_first(tmp_path) -> None:
    class EventLLMClient(FakeLLMClient):
        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(prompt)
            return {
                "is_schedule_request": True,
                "drafts": [
                    {
                        "title": "Stand",
                        "prompt": "stand up",
                        "schedule_type": "once",
                        "interval_seconds": 7,
                        "timezone": "UTC",
                        "schedule_summary": "After 7 seconds",
                        "confidence": 0.9,
                        "missing_details": [],
                        "auto_approve_confirmations": True,
                        "context": {"gateway_id": "llm-should-not-win", "gateway_node": "wrong"},
                        "action_type": "notification",
                        "notification_message": "stand up",
                    }
                ],
                "missing_details": [],
                "rationale": "structured",
            }

    llm = EventLLMClient()
    client = _event_client(tmp_path, runtime=SimpleNamespace(llm_client=llm))

    response = client.post(
        "/api/agent/events/from-prompt",
        json={
            "prompt": "remind me to stand after 10 seconds",
            "context": {"gateway_id": "gateway-good", "gateway_node": "worker-a"},
        },
    )

    assert response.status_code == 200
    assert llm.prompts
    event = response.json()["events"][0]
    assert event["title"] == "Stand"
    assert event["interval_seconds"] == 7
    assert event["notification_message"] == "stand up"
    assert event["context"]["gateway_id"] == "gateway-good"
    assert event["context"]["gateway_node"] == "worker-a"


def test_agent_ui_events_from_prompt_preserves_autoapprove_from_request_context(tmp_path) -> None:
    class EventLLMClient(FakeLLMClient):
        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(prompt)
            return {
                "is_schedule_request": True,
                "drafts": [
                    {
                        "title": "Commit changes",
                        "prompt": 'stage and commit with message "literal extraction bug fix"',
                        "schedule_type": "once",
                        "interval_seconds": 60,
                        "timezone": "UTC",
                        "schedule_summary": "After 1 minute",
                        "confidence": 0.9,
                        "missing_details": [],
                        "auto_approve_confirmations": False,
                        "context": {
                            "auto_approve_commands": False,
                            "auto_approve_scope": "draft",
                        },
                        "action_type": "agent_prompt",
                        "notification_message": "",
                    }
                ],
                "missing_details": [],
                "rationale": "structured",
            }

    llm = EventLLMClient()
    client = _event_client(tmp_path, runtime=SimpleNamespace(llm_client=llm))

    response = client.post(
        "/api/agent/events/from-prompt",
        json={
            "prompt": (
                '/runlater after 1 mins, stage all changes in this branch and commit with '
                'message "literal extraction bug fix"'
            ),
            "context": {
                "auto_approve_commands": True,
                "auto_approve_scope": "request",
            },
            "agent_mode": "llm_operator",
        },
    )

    assert response.status_code == 200
    event = response.json()["events"][0]
    assert event["auto_approve_confirmations"] is True
    assert event["context"]["auto_approve_commands"] is True
    assert event["context"]["auto_approve_scope"] == "request"


def test_agent_ui_runlater_macro_overrides_llm_notification_draft(tmp_path) -> None:
    class EventLLMClient(FakeLLMClient):
        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(prompt)
            return {
                "is_schedule_request": True,
                "drafts": [
                    {
                        "title": "Git commit",
                        "prompt": 'git stage and commit with msg "x"',
                        "schedule_type": "once",
                        "interval_seconds": 1500,
                        "timezone": "UTC",
                        "schedule_summary": "After 25 minutes",
                        "confidence": 0.9,
                        "missing_details": [],
                        "auto_approve_confirmations": True,
                        "context": {},
                        "action_type": "notification",
                        "notification_message": 'git stage and commit with msg "x"',
                    }
                ],
                "missing_details": [],
                "rationale": "structured",
            }

    llm = EventLLMClient()
    client = _event_client(tmp_path, runtime=SimpleNamespace(llm_client=llm))

    response = client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": '/runlater after 25 mins git stage and commit with msg "x"'},
    )

    assert response.status_code == 200
    assert llm.prompts
    event = response.json()["events"][0]
    assert event["action_type"] == "agent_prompt"
    assert event["prompt"] == 'git stage and commit with msg "x"'
    assert event["notification_message"] == ""
    assert event["context"]["_scheduled_event_extraction_hint"] == "agent_prompt"


def test_agent_ui_events_from_prompt_llm_false_and_failure_fall_through(tmp_path) -> None:
    class RaisingEventLLMClient(FakeLLMClient):
        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(prompt)
            raise RuntimeError("event recognition failed")

    llm_false_client = _event_client(tmp_path)
    malformed_llm = FakeLLMClient()
    malformed_client = _event_client(tmp_path, runtime=SimpleNamespace(llm_client=malformed_llm))
    raising_client = _event_client(tmp_path, runtime=SimpleNamespace(llm_client=RaisingEventLLMClient()))
    unavailable_client = _event_client(tmp_path, runtime=SimpleNamespace(llm_client=None))

    ordinary = llm_false_client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "list files in this directory"},
    )
    malformed = malformed_client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "remind me to eat after 10 seconds"},
    )
    raised = raising_client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "remind me to eat after 10 seconds"},
    )
    unavailable = unavailable_client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "remind me to eat after 10 seconds"},
    )
    macro_failure = malformed_client.post(
        "/api/agent/events/from-prompt",
        json={"prompt": "/remind after 10 seconds eat"},
    )

    assert ordinary.status_code == 200
    assert ordinary.json()["is_schedule_request"] is False
    assert ordinary.json()["created_count"] == 0
    for response in (malformed, raised, unavailable, macro_failure):
        assert response.status_code == 200
        assert response.json()["is_schedule_request"] is False
        assert response.json()["created_count"] == 0
        assert response.json()["events"] == []
    assert "_scheduled_event_extraction_hint" in malformed_llm.prompts[-1]


def test_agent_ui_due_notification_event_does_not_call_runtime(tmp_path) -> None:
    runtime = CountingAgentRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Stretch",
            "prompt": "stretch",
            "schedule_type": "once",
            "action_type": "notification",
            "notification_message": "Time to stretch",
            "interval_seconds": 900,
            "next_run_at": "2000-01-01T00:00:00+00:00",
        },
    ).json()["event"]

    assert client.app.state.agent_event_scheduler.tick() == 1

    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    notifications = client.get("/api/agent/notifications").json()["notifications"]
    listed_event = client.get("/api/agent/events").json()["events"][0]
    assert runtime.calls == 0
    assert run["request_id"] == ""
    assert run["final_response_preview"] == "Time to stretch"
    assert notifications[0]["message"] == "Time to stretch"
    assert notifications[0]["event_run_id"] == run["event_run_id"]
    assert listed_event["status"] == "completed"
    assert listed_event["next_run_at"] == ""


def test_agent_ui_due_one_shot_agent_event_invokes_runtime_and_completes(tmp_path) -> None:
    runtime = CountingAgentRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Disk",
            "prompt": "check disk usage",
            "schedule_type": "once",
            "action_type": "agent_prompt",
            "interval_seconds": 60,
            "next_run_at": "2000-01-01T00:00:00+00:00",
        },
    ).json()["event"]

    assert client.app.state.agent_event_scheduler.tick() == 1

    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    listed_event = client.get("/api/agent/events").json()["events"][0]
    assert runtime.calls == 1
    assert runtime.last_context["scheduled_event_id"] == event["event_id"]
    assert runtime.last_context["scheduled_event_title"] == "Disk"
    assert run["request_id"]
    assert listed_event["status"] == "completed"
    assert listed_event["next_run_at"] == ""


def test_agent_ui_failed_scheduled_event_creates_notification(tmp_path) -> None:
    runtime = FailingAgentRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Broken",
            "prompt": "fail please",
            "interval_seconds": 3600,
            "next_run_at": "2000-01-01T00:00:00+00:00",
        },
    ).json()["event"]

    assert client.app.state.agent_event_scheduler.tick() == 1
    run = _wait_for_event_run_status(client, event["event_id"], "failed")
    notifications = client.get("/api/agent/notifications").json()["notifications"]

    assert runtime.calls == 1
    assert "RuntimeError: boom: fail please" in run["error_preview"]
    assert notifications[0]["level"] == "error"
    assert notifications[0]["event_run_id"] == run["event_run_id"]
    assert "boom: fail please" in notifications[0]["message"]
    assert "Runtime error while handling the request" in notifications[0]["message"]


def test_agent_ui_completed_scheduled_event_with_action_error_warns_in_notifications(
    tmp_path,
) -> None:
    runtime = FailingOperatorRecordRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Conda",
            "prompt": "activate conda env",
            "interval_seconds": 3600,
            "notify_on": ["completed"],
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    task_id = triggered.json()["task_id"]
    task = _wait_for_task_status(client, task_id, "completed")
    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    notifications = client.get("/api/agent/notifications").json()["notifications"]

    task_notification = next(
        item
        for item in notifications
        if item["source_type"] == "durable_task"
        and item["metadata"].get("task_id") == task_id
    )
    event_notification = next(
        item
        for item in notifications
        if item["source_type"] == "scheduled_event_run"
        and item["metadata"].get("event_status") == "completed"
    )
    assert task["status"] == "completed"
    assert run["status"] == "completed"
    assert task["error_preview"].startswith("operator_execution_error:")
    assert run["error_preview"].startswith("operator_execution_error:")
    assert task_notification["level"] == "warning"
    assert event_notification["level"] == "warning"
    assert task_notification["metadata"]["execution_error"] is True
    assert event_notification["metadata"]["execution_error"] is True
    assert task_notification["metadata"]["execution_error_action_id"] == "action_1"
    assert event_notification["metadata"]["execution_error_code"] == "operator_execution_error"
    assert "completed with one or more action errors" in task_notification["message"]
    assert "completed with one or more action errors" in event_notification["message"]


def test_agent_ui_scheduled_event_preserves_typein_macro_privately(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = _event_client(tmp_path, runtime=runtime)

    drafted = client.post(
        "/api/agent/events/draft",
        json={
            "prompt": 'every 10 mins push all changes to origin typein "event-secret" for ssh',
            "agent_mode": "llm_operator",
        },
    )

    assert drafted.status_code == 200
    draft_payload = drafted.json()
    assert "event-secret" not in json.dumps(draft_payload)
    draft = draft_payload["draft"]["drafts"][0]
    assert draft["prompt"] == 'push all changes to origin typein "[redacted]" for ssh'
    assert draft["context"][USER_MACRO_SUMMARY_CONTEXT_KEY][0]["kind"] == "typein"
    assert USER_MACRO_PRIVATE_CONTEXT_KEY not in draft["context"]

    created = client.post(
        "/api/agent/events",
        json={
            "title": draft["title"],
            "prompt": draft["prompt"],
            "interval_seconds": draft["interval_seconds"],
            "context": draft["context"],
        },
    )

    assert created.status_code == 200
    event = created.json()["event"]
    assert "event-secret" not in json.dumps(event)
    assert USER_MACRO_PRIVATE_CONTEXT_KEY not in event["context"]
    stored_event = client.app.state.agent_event_store.get_event(event["event_id"])
    assert stored_event is not None
    assert stored_event.prompt == 'push all changes to origin typein "[redacted]" for ssh'
    assert stored_event.context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "event-secret"

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    _wait_for_event_run_status(client, event["event_id"], "completed")
    assert runtime.last_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "event-secret"
    trace = client.get(triggered.json()["trace_url"]).json()
    assert trace["prompt"] == 'push all changes to origin typein "[redacted]" for ssh'


def test_agent_ui_scheduled_event_typein_creates_background_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakeAgentRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __init__(self, body: dict[str, Any] | None = None) -> None:
            self._body = body

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            if self._body is not None:
                return json.dumps(self._body).encode("utf-8")
            payload = json.loads(captured["data"].decode("utf-8"))
            return json.dumps(
                {
                    "ok": True,
                    "session_id": payload["session_id"],
                    "cwd": payload["initial_cwd"],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        if str(request.full_url).endswith("/models"):
            return FakeHTTPResponse({"data": []})
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Push",
            "prompt": 'push git changes typein "event-secret" for ssh',
            "interval_seconds": 600,
            "context": {
                "agent_mode": "llm_operator",
                "gateway_node": "localhost",
                "terminal_cwd": str(tmp_path),
            },
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    _wait_for_event_run_status(client, event["event_id"], "completed")

    assert captured["url"] == "http://127.0.0.1:8787/terminal/session"
    request_payload = json.loads(captured["data"].decode("utf-8"))
    assert request_payload["node"] == "localhost"
    assert request_payload["initial_cwd"] == str(tmp_path)
    assert request_payload["session_id"].startswith("term-")
    assert runtime.last_context["terminal_session_id"] == request_payload["session_id"]
    assert runtime.last_context["terminal_cwd"] == str(tmp_path)
    assert runtime.last_context["execute_in_terminal"] is True
    assert runtime.last_context["scheduled_event_background_terminal"] is True
    trace = client.get(triggered.json()["trace_url"]).json()
    assert any(
        item["event_type"] == "scheduled_event.background_terminal.created"
        for item in trace["events"]
    )


def test_agent_ui_scheduled_git_may_prompt_auto_approval_creates_background_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakeImplicitTerminalConfirmationRuntime("git push origin HEAD")
    client = _event_client(tmp_path, runtime=runtime)
    captured = _capture_terminal_session_requests(monkeypatch)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Push",
            "prompt": "push git changes",
            "interval_seconds": 600,
            "auto_approve_confirmations": True,
            "context": {
                "agent_mode": "llm_operator",
                "gateway_node": "localhost",
                "terminal_cwd": str(tmp_path),
            },
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    _wait_for_event_run_status(client, event["event_id"], "completed")

    request_payload = json.loads(captured["data"].decode("utf-8"))
    assert captured["url"] == "http://127.0.0.1:8787/terminal/session"
    assert request_payload["node"] == "localhost"
    assert request_payload["initial_cwd"] == str(tmp_path)
    assert request_payload["session_id"].startswith("term-")
    assert runtime.replay_contexts[-1]["terminal_session_id"] == request_payload["session_id"]
    assert runtime.replay_contexts[-1]["terminal_cwd"] == str(tmp_path)
    assert runtime.replay_contexts[-1]["execute_in_terminal"] is True
    assert runtime.replay_contexts[-1]["scheduled_event_background_terminal"] is True


def test_agent_ui_scheduled_non_git_may_prompt_auto_approval_creates_background_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakeImplicitTerminalConfirmationRuntime("ssh deploy@example.com uptime")
    client = _event_client(tmp_path, runtime=runtime)
    captured = _capture_terminal_session_requests(monkeypatch)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Remote uptime",
            "prompt": "check remote uptime",
            "interval_seconds": 600,
            "auto_approve_confirmations": True,
            "context": {
                "agent_mode": "llm_operator",
                "gateway_node": "localhost",
                "terminal_cwd": str(tmp_path),
            },
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    _wait_for_event_run_status(client, event["event_id"], "completed")

    request_payload = json.loads(captured["data"].decode("utf-8"))
    assert request_payload["initial_cwd"] == str(tmp_path)
    assert runtime.replay_contexts[-1]["terminal_session_id"] == request_payload["session_id"]
    assert runtime.replay_contexts[-1]["execute_in_terminal"] is True
    assert runtime.replay_contexts[-1]["scheduled_event_background_terminal"] is True


def test_agent_ui_event_draft_llm_does_not_receive_typein_secret(tmp_path) -> None:
    llm = FakeLLMClient()
    client = _event_client(tmp_path, runtime=SimpleNamespace(llm_client=llm))

    response = client.post(
        "/api/agent/events/draft",
        json={
            "prompt": 'every 10 mins push all changes to origin typein "event-secret" for ssh',
            "agent_mode": "llm_operator",
        },
    )

    assert response.status_code == 200
    assert llm.prompts
    assert "event-secret" not in llm.prompts[-1]
    assert 'typein "[redacted]"' in llm.prompts[-1]


def test_agent_ui_scheduled_event_preserves_typein_when_draft_prompt_is_shortened(tmp_path) -> None:
    class ShorteningEventLLMClient(FakeLLMClient):
        def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(prompt)
            return {
                "is_schedule_request": True,
                "drafts": [
                    {
                        "title": "Push",
                        "prompt": "do a git push",
                        "interval_seconds": 600,
                        "timezone": "UTC",
                        "schedule_summary": "Every 10 minutes",
                        "confidence": 0.9,
                        "context": {},
                        "auto_approve_confirmations": True,
                    }
                ],
                "missing_details": [],
                "rationale": "shortened",
            }

    client = _event_client(tmp_path, runtime=SimpleNamespace(llm_client=ShorteningEventLLMClient()))

    drafted = client.post(
        "/api/agent/events/draft",
        json={
            "prompt": 'every 10 mins do a git push and use ssh password typein "event-secret"',
            "agent_mode": "llm_operator",
        },
    )

    assert drafted.status_code == 200
    draft_payload = drafted.json()
    assert "event-secret" not in json.dumps(draft_payload)
    draft = draft_payload["draft"]["drafts"][0]
    assert draft["prompt"] == "do a git push"
    assert draft["context"][USER_MACRO_SUMMARY_CONTEXT_KEY][0]["kind"] == "typein"

    created = client.post(
        "/api/agent/events",
        json={
            "title": draft["title"],
            "prompt": 'do a git push typein "[redacted]"',
            "interval_seconds": draft["interval_seconds"],
            "context": draft["context"],
        },
    )

    assert created.status_code == 200
    stored_event = client.app.state.agent_event_store.get_event(created.json()["event"]["event_id"])
    assert stored_event is not None
    assert stored_event.prompt == 'do a git push typein "[redacted]"'
    assert stored_event.context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "event-secret"


def test_agent_ui_events_draft_falls_through_when_disabled(tmp_path) -> None:
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_events_enabled=False,
                agent_events_db_path=tmp_path / "agent_events.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    response = client.post(
        "/api/agent/events/draft",
        json={"prompt": "check disk every hour"},
    )

    assert response.status_code == 200
    assert response.json()["draft"]["is_schedule_request"] is False


def test_agent_ui_events_runtime_toggle_disables_event_apis(tmp_path) -> None:
    client = _event_client(tmp_path)

    disabled = client.post("/api/agent/runtime-controls", json={"agent_events_enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["agent_events_enabled"] is False

    listed = client.get("/api/agent/events")
    assert listed.status_code == 200
    assert listed.json()["enabled"] is False
    assert listed.json()["events"] == []

    draft = client.post("/api/agent/events/draft", json={"prompt": "check disk every hour"})
    assert draft.status_code == 200
    assert draft.json()["draft"]["is_schedule_request"] is False

    direct = client.post("/api/agent/events/from-prompt", json={"prompt": "check disk every hour"})
    assert direct.status_code == 200
    assert direct.json()["created_count"] == 0
    assert direct.json()["is_schedule_request"] is False

    created = client.post(
        "/api/agent/events",
        json={"title": "Disk", "prompt": "check disk", "interval_seconds": 3600},
    )
    assert created.status_code == 404


def test_agent_ui_events_crud_and_manual_trigger(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = _event_client(tmp_path, runtime=runtime)

    created = client.post(
        "/api/agent/events",
        json={
            "title": "Disk check",
            "prompt": "check disk usage",
            "interval_seconds": 3600,
            "context": {"agent_mode": "standard", "terminal_cwd": "/tmp/event-space"},
        },
    )
    assert created.status_code == 200
    event = created.json()["event"]
    event_id = event["event_id"]
    assert event["status"] == "active"
    assert event["auto_approve_confirmations"] is True

    listed = client.get("/api/agent/events")
    assert listed.status_code == 200
    assert [item["event_id"] for item in listed.json()["events"]] == [event_id]

    updated = client.patch(
        f"/api/agent/events/{event_id}",
        json={"status": "paused", "schedule_type": "interval", "auto_approve_confirmations": False},
    )
    assert updated.status_code == 200
    assert updated.json()["event"]["status"] == "paused"
    assert updated.json()["event"]["auto_approve_confirmations"] is False

    triggered = client.post(f"/api/agent/events/{event_id}/trigger")
    assert triggered.status_code == 200
    with client.stream("GET", triggered.json()["stream_url"]) as response:
        _ = response.read()
    run = _wait_for_event_run_status(client, event_id, "completed")
    assert run["request_id"] == triggered.json()["request_id"]
    assert run["trace_url"].endswith(triggered.json()["request_id"])
    trace = client.get(triggered.json()["trace_url"]).json()
    assert trace["final_response"] == "handled: check disk usage"
    assert any(event["event_type"] == "scheduled_event.started" for event in trace["events"])
    assert runtime.last_context["terminal_cwd"] == "/tmp/event-space"
    assert "terminal_session_id" not in runtime.last_context
    assert "execute_in_terminal" not in runtime.last_context
    assert runtime.last_context["llm_operator_final_response_mode"] == "simple"

    deleted = client.delete(f"/api/agent/events/{event_id}")
    assert deleted.status_code == 200
    assert deleted.json()["event"]["status"] == "deleted"
    assert client.get("/api/agent/events").json()["events"] == []


def test_agent_ui_scheduled_event_run_history_summarizes_command_output(tmp_path) -> None:
    runtime = FakeGenericEventOutputRuntime(
        stdout=" M src/app.py\n",
        final_response=(
            "## Results\n\n"
            "Execution completed.\n\n"
            "### Determine if there are uncommitted changes in the git repository.\n\n"
            "- Status: `success`\n"
            "- Exit code: `0`"
        ),
    )
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Git status",
            "prompt": "check if there are pending git changes",
            "interval_seconds": 3600,
            "context": {
                "terminal_cwd": "/tmp/repo",
                "terminal_session_id": "browser-terminal-stale",
                "execute_in_terminal": True,
                "llm_operator_final_response_mode": "simple",
            },
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200

    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    assert "M src/app.py" in run["final_response_preview"]
    assert "## Event Output" in run["final_response_preview"]
    trace = client.get(run["trace_url"]).json()
    assert "## Event Output" in trace["final_response"]
    assert "M src/app.py" in trace["final_response"]
    assert any(
        event["event_type"] == "request.final_response.updated"
        for event in trace["events"]
    )
    assert "terminal_session_id" not in runtime.last_context
    assert "execute_in_terminal" not in runtime.last_context
    assert runtime.last_context["terminal_cwd"] == "/tmp/repo"
    assert runtime.last_context["llm_operator_final_response_mode"] == "simple"


def test_agent_ui_event_scheduler_claims_due_event_once(tmp_path) -> None:
    client = _event_client(tmp_path)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Immediate check",
            "prompt": "list files",
            "interval_seconds": 3600,
            "next_run_at": "2000-01-01T00:00:00+00:00",
        },
    ).json()["event"]

    scheduler = client.app.state.agent_event_scheduler
    assert scheduler.tick() == 1
    assert scheduler.tick() == 0

    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    assert run["status"] == "completed"
    listed = client.get("/api/agent/events").json()["events"][0]
    assert listed["last_run_at"]
    assert listed["next_run_at"] > listed["last_run_at"]


def test_agent_ui_scheduled_event_auto_approves_confirmation(tmp_path) -> None:
    runtime = FakeConfirmationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Confirmed write",
            "prompt": "write mem.txt",
            "interval_seconds": 3600,
            "auto_approve_confirmations": True,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200

    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    assert runtime.replay_contexts[-1]["confirmation"] is True
    assert runtime.replay_contexts[-1]["auto_approved_confirmation"] is True
    assert runtime.replay_contexts[-1]["scheduled_event_id"] == event["event_id"]
    assert run["parent_request_id"] == triggered.json()["request_id"]
    assert run["request_id"] != triggered.json()["request_id"]

    approved_trace = client.get(run["trace_url"]).json()
    assert approved_trace["final_response"] == "approved: write mem.txt"
    assert any(
        trace_event["event_type"] == "scheduled_event.auto_approved"
        for trace_event in approved_trace["events"]
    )
    notifications = client.get("/api/agent/notifications").json()["notifications"]
    auto_approved_notification = next(
        item for item in notifications if item["source_type"] == "scheduled_event_auto_approved"
    )
    completed_notification = next(
        item
        for item in notifications
        if item["metadata"].get("event_status") == "completed"
    )
    assert auto_approved_notification["event_run_id"] == run["event_run_id"]
    assert auto_approved_notification["request_id"] == run["request_id"]
    assert auto_approved_notification["metadata"]["auto_approved_confirmation"] is True
    assert auto_approved_notification["metadata"]["confirmation_action_count"] == 1
    assert "auto-approved 1 confirmation" in auto_approved_notification["message"]
    assert completed_notification["level"] == "success"
    assert completed_notification["event_run_id"] == run["event_run_id"]
    assert completed_notification["metadata"]["completed_successfully"] is True
    assert completed_notification["metadata"]["auto_approved_confirmation"] is True
    assert completed_notification["metadata"]["confirmation_action_count"] == 1
    assert "completed successfully" in completed_notification["message"]
    assert "Auto-approved 1 confirmation" in completed_notification["message"]


def test_agent_ui_scheduled_event_auto_approval_carries_across_multiple_confirmations(tmp_path) -> None:
    runtime = FakeMultiConfirmationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Stage and commit",
            "prompt": "stage all changes and commit",
            "interval_seconds": 3600,
            "auto_approve_confirmations": True,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200

    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    assert len(runtime.replay_contexts) == 2
    assert all(context["auto_approved_confirmation"] is True for context in runtime.replay_contexts)
    assert [context["scheduled_event_auto_approve_count"] for context in runtime.replay_contexts] == [1, 2]
    assert run["request_id"] != triggered.json()["request_id"]

    approved_trace = client.get(run["trace_url"]).json()
    assert approved_trace["final_response"] == "approved twice: stage all changes and commit"
    assert any(
        trace_event["event_type"] == "scheduled_event.auto_approved"
        for trace_event in approved_trace["events"]
    )


def test_agent_ui_scheduled_event_reconciles_stale_running_run(tmp_path) -> None:
    client = _event_client(tmp_path)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Stale run",
            "prompt": "push git changes",
            "interval_seconds": 600,
        },
    ).json()["event"]
    run = client.app.state.agent_event_store.create_run(
        event_id=event["event_id"],
        scheduled_for="2026-01-01T00:00:00+00:00",
        request_id="req-missing-trace",
        status="running",
    )

    response = client.get(f"/api/agent/events/{event['event_id']}/runs")

    assert response.status_code == 200
    stale = next(item for item in response.json()["runs"] if item["event_run_id"] == run.event_run_id)
    assert stale["status"] == "cancelled"
    assert "stale run record was closed" in stale["final_response_preview"]


def test_agent_ui_scheduled_event_manual_confirmation_closes_event_run(tmp_path) -> None:
    runtime = FakeConfirmationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Manual confirmed write",
            "prompt": "write mem.txt",
            "interval_seconds": 3600,
            "auto_approve_confirmations": False,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    parent_request_id = triggered.json()["request_id"]

    waiting_run = _wait_for_event_run_status(client, event["event_id"], "awaiting_confirmation")
    assert waiting_run["request_id"] == parent_request_id
    assert client.app.state.agent_event_store.can_start_run(event["event_id"]) is False

    approved = client.post(
        f"/api/agent/confirmation/{parent_request_id}",
        json={"action": "approve"},
    )

    assert approved.status_code == 200
    approved_payload = approved.json()
    with client.stream("GET", approved_payload["stream_url"]) as response:
        _ = response.read()

    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    assert run["request_id"] == approved_payload["request_id"]
    assert run["parent_request_id"] == parent_request_id
    assert client.app.state.agent_event_store.can_start_run(event["event_id"]) is True
    approved_trace = client.get(run["trace_url"]).json()
    assert approved_trace["final_response"] == "approved: write mem.txt"
    assert any(
        trace_event["event_type"] == "scheduled_event.completed"
        for trace_event in approved_trace["events"]
    )

    updated = client.patch(
        f"/api/agent/events/{event['event_id']}",
        json={"next_run_at": "2000-01-01T00:00:00+00:00"},
    )
    assert updated.status_code == 200
    assert client.app.state.agent_event_scheduler.tick() == 1
    next_run = _wait_for_event_run_status(client, event["event_id"], "awaiting_confirmation")
    assert next_run["status"] == "awaiting_confirmation"
    assert next_run["request_id"] != approved_payload["request_id"]


def test_agent_ui_scheduled_event_reconciles_handled_confirmation_to_child_request(tmp_path) -> None:
    runtime = FakeConfirmationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Manual confirmed write",
            "prompt": "write mem.txt",
            "interval_seconds": 3600,
            "auto_approve_confirmations": False,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    parent_request_id = triggered.json()["request_id"]
    waiting_run = _wait_for_event_run_status(client, event["event_id"], "awaiting_confirmation")

    approved = client.post(
        f"/api/agent/confirmation/{parent_request_id}",
        json={"action": "approve"},
    )
    assert approved.status_code == 200
    approved_payload = approved.json()
    with client.stream("GET", approved_payload["stream_url"]) as response:
        _ = response.read()

    client.app.state.agent_event_store.update_run(
        waiting_run["event_run_id"],
        request_id=parent_request_id,
        parent_request_id="",
        status="awaiting_confirmation",
        completed_at="",
    )

    reconciled = _wait_for_event_run_status(client, event["event_id"], "completed")
    assert reconciled["request_id"] == approved_payload["request_id"]
    assert reconciled["parent_request_id"] == parent_request_id


def test_agent_ui_scheduled_event_leaves_clarification_awaiting_user(tmp_path) -> None:
    runtime = FakeClarificationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Env check",
            "prompt": "create a conda environment",
            "interval_seconds": 3600,
            "auto_approve_confirmations": True,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200

    run = _wait_for_event_run_status(client, event["event_id"], "awaiting_clarification")
    assert run["request_id"] == triggered.json()["request_id"]
    notifications = client.get("/api/agent/notifications").json()["notifications"]
    assert notifications[0]["event_run_id"] == run["event_run_id"]
    assert notifications[0]["request_id"] == run["request_id"]
    assert notifications[0]["metadata"]["event_status"] == "awaiting_clarification"
    trace = client.get(run["trace_url"]).json()
    assert trace["clarification_required"] is True
    assert runtime.contexts[-1]["scheduled_event_id"] == event["event_id"]


def test_agent_ui_scheduled_event_skipped_run_links_blocking_run(tmp_path) -> None:
    runtime = FakeClarificationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Push",
            "prompt": "push git changes",
            "interval_seconds": 600,
            "auto_approve_confirmations": True,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    blocking_run = _wait_for_event_run_status(
        client,
        event["event_id"],
        "awaiting_clarification",
    )

    updated = client.patch(
        f"/api/agent/events/{event['event_id']}",
        json={"next_run_at": "2000-01-01T00:00:00+00:00"},
    )
    assert updated.status_code == 200
    assert client.app.state.agent_event_scheduler.tick() == 0

    runs_response = client.get(f"/api/agent/events/{event['event_id']}/runs")
    assert runs_response.status_code == 200
    skipped_run = next(item for item in runs_response.json()["runs"] if item["status"] == "skipped")
    assert blocking_run["event_run_id"] in skipped_run["error_preview"]
    assert skipped_run["blocked_by_event_run_id"] == blocking_run["event_run_id"]
    assert skipped_run["blocked_by_request_id"] == triggered.json()["request_id"]
    assert skipped_run["blocked_by_status"] == "awaiting_clarification"

    listed_runs = client.get("/api/agent/events").json()["runs"][event["event_id"]]
    listed_skip = next(item for item in listed_runs if item["status"] == "skipped")
    assert listed_skip["blocked_by_event_run_id"] == blocking_run["event_run_id"]


def test_agent_ui_scheduled_event_run_can_be_cancelled_from_history(tmp_path) -> None:
    runtime = FakeClarificationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Env check",
            "prompt": "create a conda environment",
            "interval_seconds": 600,
            "auto_approve_confirmations": True,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    waiting_run = _wait_for_event_run_status(
        client,
        event["event_id"],
        "awaiting_clarification",
    )
    assert client.app.state.agent_event_store.can_start_run(event["event_id"]) is False

    cancelled = client.post(
        f"/api/agent/events/{event['event_id']}/runs/{waiting_run['event_run_id']}/cancel"
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["run"]["status"] == "cancelled"
    assert "Future scheduled ticks are unblocked" in cancelled.json()["run"]["final_response_preview"]
    assert client.app.state.agent_event_store.can_start_run(event["event_id"]) is True
    listed_run = next(
        item
        for item in client.get(f"/api/agent/events/{event['event_id']}/runs").json()["runs"]
        if item["event_run_id"] == waiting_run["event_run_id"]
    )
    assert listed_run["status"] == "cancelled"

    retriggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert retriggered.status_code == 200
    assert retriggered.json()["request_id"] != triggered.json()["request_id"]


def test_agent_ui_scheduled_event_clarification_resume_closes_event_run(tmp_path) -> None:
    runtime = FakeClarificationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Env check",
            "prompt": "create a conda environment",
            "interval_seconds": 3600,
            "auto_approve_confirmations": True,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    parent_request_id = triggered.json()["request_id"]

    waiting_run = _wait_for_event_run_status(client, event["event_id"], "awaiting_clarification")
    assert waiting_run["request_id"] == parent_request_id
    assert client.app.state.agent_event_store.can_start_run(event["event_id"]) is False

    answered = client.post(
        f"/api/agent/clarification/{parent_request_id}",
        json={"answer": "Python 3.12", "selected_option_id": "py312"},
    )
    assert answered.status_code == 200
    answered_payload = answered.json()
    with client.stream("GET", answered_payload["stream_url"]) as response:
        _ = response.read()

    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    assert run["request_id"] == answered_payload["request_id"]
    assert run["parent_request_id"] == parent_request_id
    assert client.app.state.agent_event_store.can_start_run(event["event_id"]) is True
    assert "answered with Python 3.12" in run["final_response_preview"]
    assert runtime.contexts[-1]["clarifications"][-1]["answer"] == "Python 3.12"
    trace = client.get(run["trace_url"]).json()
    assert trace["final_response"] == "answered with Python 3.12: create a conda environment"
    assert any(
        trace_event["event_type"] == "scheduled_event.completed"
        for trace_event in trace["events"]
    )

    updated = client.patch(
        f"/api/agent/events/{event['event_id']}",
        json={"next_run_at": "2000-01-01T00:00:00+00:00"},
    )
    assert updated.status_code == 200
    assert client.app.state.agent_event_scheduler.tick() == 1
    next_run = _wait_for_event_run_status(client, event["event_id"], "awaiting_clarification")
    assert next_run["request_id"] != answered_payload["request_id"]


def test_agent_ui_scheduled_event_reconciles_handled_clarification_to_child_request(tmp_path) -> None:
    runtime = FakeClarificationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Env check",
            "prompt": "create a conda environment",
            "interval_seconds": 3600,
            "auto_approve_confirmations": True,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    parent_request_id = triggered.json()["request_id"]
    waiting_run = _wait_for_event_run_status(client, event["event_id"], "awaiting_clarification")

    answered = client.post(
        f"/api/agent/clarification/{parent_request_id}",
        json={"answer": "Python 3.12", "selected_option_id": "py312"},
    )
    assert answered.status_code == 200
    answered_payload = answered.json()
    with client.stream("GET", answered_payload["stream_url"]) as response:
        _ = response.read()

    client.app.state.agent_event_store.update_run(
        waiting_run["event_run_id"],
        request_id=parent_request_id,
        parent_request_id="",
        status="awaiting_clarification",
        completed_at="",
    )

    reconciled = _wait_for_event_run_status(client, event["event_id"], "completed")
    assert reconciled["request_id"] == answered_payload["request_id"]
    assert reconciled["parent_request_id"] == parent_request_id


def test_agent_ui_scheduled_event_can_persist_clarification_for_future_runs(tmp_path) -> None:
    runtime = FakeClarificationRuntime()
    client = _event_client(tmp_path, runtime=runtime)
    event = client.post(
        "/api/agent/events",
        json={
            "title": "Env check",
            "prompt": "create a conda environment",
            "interval_seconds": 3600,
            "auto_approve_confirmations": True,
        },
    ).json()["event"]

    triggered = client.post(f"/api/agent/events/{event['event_id']}/trigger")
    assert triggered.status_code == 200
    parent_request_id = triggered.json()["request_id"]
    _wait_for_event_run_status(client, event["event_id"], "awaiting_clarification")

    answered = client.post(
        f"/api/agent/clarification/{parent_request_id}",
        json={
            "answer": "Python 3.12",
            "selected_option_id": "py312",
            "persist_for_event": True,
        },
    )
    assert answered.status_code == 200
    assert answered.json()["persisted_for_event"] is True
    with client.stream("GET", answered.json()["stream_url"]) as response:
        _ = response.read()

    run = _wait_for_event_run_status(client, event["event_id"], "completed")
    trace = client.get(run["trace_url"]).json()
    assert any(
        trace_event["event_type"] == "scheduled_event.clarification_saved"
        for trace_event in trace["events"]
    )
    saved_event = client.get("/api/agent/events").json()["events"][0]
    saved_clarifications = saved_event["context"]["clarifications"]
    assert saved_clarifications[-1]["answer"] == "Python 3.12"
    assert saved_clarifications[-1]["persisted_for_event"] is True

    updated = client.patch(
        f"/api/agent/events/{event['event_id']}",
        json={"next_run_at": "2000-01-01T00:00:00+00:00"},
    )
    assert updated.status_code == 200
    assert client.app.state.agent_event_scheduler.tick() == 1

    next_run = _wait_for_event_run_status(client, event["event_id"], "completed")
    assert next_run["request_id"] != answered.json()["request_id"]
    next_trace = client.get(next_run["trace_url"]).json()
    assert next_trace["clarification_required"] is False
    assert "answered with Python 3.12" in next_trace["final_response"]
    assert runtime.contexts[-1]["clarifications"][-1]["answer"] == "Python 3.12"


def test_agent_ui_operator_mode_posts_request_flag() -> None:
    client = _client()

    response = client.get("/agent-ui/static/app.js?v=20260509-hide-command-trace")

    assert response.status_code == 200
    assert "function currentAgentMode()" in response.text
    assert "function setAgentMode" in response.text
    assert "agent_mode: agentMode" in response.text
    assert "agent_mode: currentAgentMode()" in response.text
    assert "ui_agent_mode: currentAgentMode()" in response.text
    assert "agentModePreservesConversation(agentMode)" in response.text
    assert "requestBody.conversation_id = state.conversationId" in response.text
    assert "function appendAdvisorySnippetCards" in response.text
    assert "await ensureAdvisorySnippetTerminal(gateway)" in response.text
    assert "sendTerminalInput(`${snippet.code}\\r`)" in response.text
    assert "sendTerminalInput(snippet.code)" in response.text
    assert "/api/agent/chats" in response.text
    assert "function openChatHistoryItem" in response.text
    assert "function restoreChatHistory" in response.text
    assert "state.conversationId = String(chat.conversation_id || \"\") || null" in response.text
    assert "function currentThinkingText" in response.text
    assert "function setThinkingIndicatorText" in response.text
    assert "function updateThinkingIndicatorText" in response.text
    assert "function chatIsScrolledToBottom" in response.text
    assert "function updateChatAutoScrollPinned" in response.text
    assert 'chatLog.addEventListener("scroll", updateChatAutoScrollPinned, { passive: true })' in response.text
    assert "if (!force && !chatAutoScrollPinned)" in response.text
    assert 'indicator.querySelector(".thinking-indicator-text")' in response.text
    assert 'textNode.classList.add("thinking-indicator-text-animate")' in response.text
    assert 'indicatorText.className = "thinking-indicator-text"' in response.text
    assert 'indicatorDots.className = "thinking-indicator-dots"' in response.text
    assert 'submitting: "Submitting"' in response.text
    assert 'if (label === "execution") return "Working"' in response.text
    assert "indicator.textContent = label" not in response.text
    assert 'promptInput.value = ""' in response.text
    assert "function navigatePromptHistory" in response.text
    assert "function loadPromptHistory" in response.text
    assert "function persistPromptHistoryEntry" in response.text
    assert 'fetch("/api/agent/prompt-history", { cache: "no-store" })' in response.text
    assert 'body: JSON.stringify({ prompt: value })' in response.text
    assert "void loadPromptHistory()" in response.text
    assert "openfabric.agentUi.promptHistory" not in response.text
    assert "openfabric.agentUi.activeRequest" in response.text
    assert "function durableUiStorage" in response.text
    assert "durableUiStorage()?.getItem(key)" in response.text
    assert "durableUiStorage()?.setItem(key, normalized)" in response.text
    assert "durableUiStorage()?.removeItem(key)" in response.text
    assert "function restoreActiveRequestAfterReload(snapshot = readActiveRequestSnapshot())" in response.text
    assert "function persistActiveRequestSnapshot" in response.text
    assert "function streamUrlWithAfterId(streamUrl, afterId)" in response.text
    assert "new EventSource(streamUrlWithAfterId" in response.text
    assert "rememberActiveRequestEvent(event)" in response.text
    assert "syncActiveRequestFromTracePayload(payload, requestId)" in response.text
    assert "finishAppBoot()" in response.text
    assert "void restoreActiveRequestAfterReload(activeRequestRestoreSnapshot)" in response.text
    assert "event.shiftKey &&\n    !event.ctrlKey &&" in response.text
    assert 'event.key === "ArrowUp"' in response.text
    assert 'event.key === "ArrowDown"' in response.text
    assert 'form.requestSubmit(submitButton)' in response.text
    assert "function updateCommandOutput" in response.text
    assert "function scheduleCommandOutputFlush" in response.text
    assert "function flushCommandOutputChunks" in response.text
    assert "function commandOutputScrollSnapshot" in response.text
    assert "function restoreCommandOutputScrollPosition" in response.text
    assert "const terminalScroll = commandOutputScrollSnapshot(record.terminal)" in response.text
    assert "restoreCommandOutputScrollPosition(chatLog, chatLogScroll)" in response.text
    assert "record.stepMessage.scrollIntoView" not in response.text
    assert "function setCommandOutputCollapsed" in response.text
    assert "openfabric.agentUi.commandOutputCollapsed" in response.text
    assert "openfabric.agentUi.progressCollapsed" in response.text
    assert "openfabric.agentUi.terminalVisible" in response.text
    assert "openfabric.agentUi.terminalCollapsed" in response.text
    assert "openfabric.agentUi.traceVisible" in response.text
    assert "openfabric.agentUi.traceCollapsed" in response.text
    assert "pendingTerminalInput" in response.text
    assert "terminalConnectToken" in response.text
    assert "terminalInputReconnectGatewayId" in response.text
    assert 'const terminalGatewayBadge = document.querySelector("#terminal-gateway-badge")' in response.text
    assert "function updateTerminalGatewayBadge" in response.text
    assert 'terminalGatewayBadge.classList.toggle("connected"' in response.text
    assert 'terminalGatewayBadge.dataset.connected = String(Boolean(text && state.terminalConnected));' in response.text
    assert "function gatewayAddressText" in response.text
    assert 'join(" @ ")' in response.text
    assert "function sendTerminalInput" in response.text
    assert "function ensureTerminalInputConnection" in response.text
    assert "function reconnectTerminalFromKeystroke" in response.text
    assert "function ensureTerminalConnectedToGateway" in response.text
    assert "await ensureTerminalConnectedToGateway(routingPreview.gateway)" in response.text
    assert 'params.set("gateway_id", targetGatewayId)' in response.text
    assert "terminalConfigMatchesGateway(targetGatewayId)" in response.text
    assert "const socketReady = [WebSocket.OPEN, WebSocket.CONNECTING].includes" in response.text
    assert "connectToken !== null && state.terminalConnectToken !== connectToken" in response.text
    assert "closeTerminalSocketQuietly()" in response.text
    assert "ensureTerminalInputConnection({ refreshConfig: true })" in response.text
    assert "connectTerminal({" in response.text
    assert "gatewayId: targetGatewayId" in response.text
    assert "ensureTerminalConfig({" in response.text
    assert "terminal.onKey?.(" in response.text
    assert 'terminalScreen?.addEventListener("keydown"' in response.text
    assert "flushPendingTerminalInput(socket)" in response.text
    assert "openfabric.agentUi.visualizationVisible" in response.text
    assert "openfabric.agentUi.visualizationCollapsed" in response.text
    assert "openfabric.agentUi.visualizationDirection" in response.text
    assert 'const visualizationToggle = document.querySelector("#visualization-toggle")' in response.text
    assert 'const visualizationDirectionToggle = document.querySelector("#visualization-direction-toggle")' in response.text
    assert 'const settingVisualizationVisible = document.querySelector("#setting-visualization-visible")' in response.text
    assert "function storedBoolean" in response.text
    assert "function persistBoolean" in response.text
    assert "function ensureGlobalCommandOutputContainer" in response.text
    assert "appendConversationArtifactNode(panel)" in response.text
    assert "function mountCommandOutputPanel" not in response.text
    assert "chatLog.insertBefore(commandOutputPanel, anchor)" not in response.text
    assert "actions.append(clear, copy)" in response.text
    assert "function copyTextToClipboard" in response.text
    assert "function paneCopyText" in response.text
    assert "function wirePaneCopyButton" in response.text
    assert 'const quickTerminalToggleButton = document.querySelector("#quick-terminal-toggle-button")' in response.text
    assert "function syncQuickTerminalToggleButton" in response.text
    assert "const visible = Boolean(state.terminalVisible && !state.agentUiClientMode)" in response.text
    assert "function terminalOpenRequestedFromUrl" in response.text
    assert "function toggleQuickTerminalPane" in response.text
    assert "const nextVisible = !state.terminalVisible" in response.text
    assert "const visibilityUpdate = setTerminalVisible(nextVisible)" in response.text
    assert "saveAgentSettings(readSettingsFromControls())" in response.text
    assert "const openTerminalFromUrl = terminalOpenRequestedFromUrl()" in response.text
    assert "syncSettings: openTerminalFromUrl" in response.text
    assert "saveAgentSettings({ ...readSettingsFromControls(), ui_terminal_visible: true })" in response.text
    assert "quickTerminalToggleButton?.addEventListener(\"click\"" in response.text
    assert "ADVISORY_TERMINAL_CONTEXT_MAX_CHARS = 8000" in response.text
    assert "advisoryTerminalContextEnabled: false" in response.text
    assert "function terminalVisibleSnapshotText" in response.text
    assert "function advisoryTerminalRequestContext" in response.text
    assert "advisory_terminal_context_enabled" in response.text
    assert "advisory_terminal_output" in response.text
    assert 'wirePaneCopyButton(conversationCopyButton, "conversation")' in response.text
    assert 'wirePaneCopyButton(progressCopyButton, "progress")' in response.text
    assert 'wirePaneCopyButton(terminalCopyButton, "terminal")' in response.text
    assert 'wirePaneCopyButton(traceCopyButton, "trace")' in response.text
    assert 'wirePaneCopyButton(visualizationDetailCopyButton, "visualization-details")' in response.text
    assert "function requestConversationHeaderSummary" in response.text
    assert "function clearConversationHeaderSummary" in response.text
    assert "function tracePayloadTextForConversationSummary" in response.text
    assert "function displayDocumentTextForSummary" in response.text
    assert "function rawPayloadTextForSummary" in response.text
    assert "function removeStreamingCompletionDuplicateRawTextChunks" in response.text
    assert "streamingCompletionFindSimilarChunk(record, rawRecords)" in response.text
    assert "Execution payload previews:" in response.text
    assert "state.conversationLatestSummaryText = tracePayloadTextForConversationSummary(payload)" in response.text
    assert "requestConversationHeaderSummary(state.conversationLatestSummaryText)" in response.text
    assert 'fetchWithLlmActivity("/api/agent/conversation/summary"' in response.text
    assert "function modelConfigUrl" in response.text
    assert "fetch(modelConfigUrl()" in response.text
    assert "observeEngineModel(event.detail.model" in response.text
    assert 'state.activeModelSource === "discovered"' in response.text
    assert 'window.addEventListener("pageshow"' in response.text
    assert "function initModelSelector" in response.text
    assert "llm_model: selectedModelValue()" in response.text
    assert "function getCommandOutputText" in response.text
    assert "function clearCommandOutput" in response.text
    assert "function retirePendingConfirmationActions" in response.text
    assert "function appendClarificationRequest" in response.text
    assert "function clarificationNoticeText" in response.text
    assert "state.finalTraceRendered.has(requestId)" in response.text
    assert '"Input needed"' in response.text
    assert "payload.final_response || \"I need one answer before continuing.\"" not in response.text
    assert "detail.command_stream_id" in response.text
    assert "metadata.command_stream_id" in response.text
    assert "record.command_stream_id" in response.text
    assert "actionId ||\n      `record:${index}`" in response.text
    assert "const key = `${requestId}:${streamId}`" in response.text
    assert 'fetch(`/api/agent/clarification/${requestId}`' in response.text
    assert "persist_for_event" in response.text
    assert "persistInput.checked = true" in response.text
    assert "parameter_choices" in response.text
    assert "parameter_choice_id" in response.text
    assert "answer_is_secret" in response.text
    assert "handleClarificationAnswer(" in response.text
    assert "function scheduledEventInfoFromTracePayload" in response.text
    assert "Use this answer for future runs of this event" in response.text
    assert "retirePendingConfirmationActions()" in response.text
    assert "retirePendingClarificationActions()" in response.text
    assert "actions.dataset.requestId = requestId" in response.text
    assert "function terminalRequestContext" in response.text
    assert "function waitForTerminalRequestContext" in response.text
    assert "function ensureTerminalContextForApproval" in response.text
    assert "function terminalWouldAutoExpandForApproval" in response.text
    assert "function terminalRestoreStateForApproval" in response.text
    assert "function rememberAutoExpandedTerminalRequest" in response.text
    assert "function restoreAutoExpandedTerminalState" in response.text
    assert "function maybeCollapseAutoExpandedTerminal" in response.text
    assert "autoExpandedTerminalRestoreForApproval = terminalRestoreStateForApproval()" in response.text
    assert "autoExpandedTerminalForApproval = terminalWouldAutoExpandForApproval()" in response.text
    assert "rememberAutoExpandedTerminalRequest(payload.request_id, autoExpandedTerminalRestoreForApproval)" in response.text
    assert "restoreAutoExpandedTerminalState(autoExpandedTerminalRestoreForApproval)" in response.text
    assert 'const buttonRow = node.querySelector(".confirmation-actions-buttons")' in response.text
    assert "buttonRow.hidden = true" in response.text
    assert 'buttonRow.setAttribute("aria-hidden", "true")' in response.text
    assert "function confirmationActionsRequireTerminal" in response.text
    assert "function confirmationNoticeText" in response.text
    assert "function renderConfirmationActionCard" in response.text
    assert "function updateCommandOutputGateway" in response.text
    assert "function confirmationActionGatewayText" in response.text
    assert "confirmation-gateway-chip" in response.text
    assert "confirmation-action-card" in response.text
    assert "confirmation-action-mini-header" in response.text
    assert "confirmation-action-footer" in response.text
    assert "confirmation-action-command" in response.text
    assert "confirmation-actions-buttons" in response.text
    assert "function prepareActiveRequestRestore" in response.text
    assert "function createActiveRequestRestoreProgress" in response.text
    assert "function updateActiveRequestRestoreProgress" in response.text
    assert "Restoring pending approval" in response.text
    assert "Fetching saved trace and approval details..." in response.text
    assert "Rendering pending conversation..." in response.text
    assert "const activeRequestRestoreSnapshot = readActiveRequestSnapshot()" in response.text
    assert "void restoreActiveRequestAfterReload(activeRequestRestoreSnapshot)" in response.text
    assert "requiresTerminalContext" in response.text
    assert "Terminal-capable actions will run in the Agent UI terminal" in response.text
    assert "terminal input needed" in response.text
    assert "detail.terminal_input_satisfied_by_macro === true" in response.text
    assert "The command is waiting for input in the Agent UI terminal" in response.text
    assert "function terminalExecutionEnabled" in response.text
    assert "async function refreshAgentSettingsBeforeRequest()" in response.text
    assert "await refreshAgentSettingsBeforeRequest();" in response.text
    assert "const terminalExecution = terminalExecutionEnabled()" in response.text
    assert "terminalContext.execute_in_terminal = true" in response.text
    assert "const confirmationPayload = { action, context: agentSettingsContext() }" in response.text
    assert "confirmationPayload.context = {" in response.text
    assert "JSON.stringify(confirmationPayload)" in response.text
    assert 'const settingsToggle = document.querySelector("#settings-toggle")' in response.text
    assert 'const immersiveBrandAgentName = document.querySelector("#immersive-brand-agent-name")' in response.text
    assert "immersiveBrandAgentName.textContent = name;" in response.text
    assert 'const immersiveSettingsToggle = document.querySelector("#immersive-settings-toggle")' in response.text
    assert 'immersiveSettingsToggle?.addEventListener("click"' in response.text
    assert 'immersiveSettingsToggle?.setAttribute(' in response.text
    assert 'const settingsDrawer = document.querySelector("#settings-drawer")' in response.text
    assert 'const quickRestartButton = document.querySelector("#quick-restart-button")' in response.text
    assert "function initSettingsDrawer" in response.text
    assert "function agentSettingsContext" in response.text
    assert "function generateRandomAgentName" in response.text
    assert 'fetchWithLlmActivity("/api/agent/name/suggest"' in response.text
    assert "recent_names: recentAgentNamesForSuggestion()" in response.text
    assert "function rememberAgentNameSuggestion" in response.text
    assert "agent_display_name: currentAgentName()" in response.text
    assert 'const quickControlsToggle = document.querySelector("#quick-controls-toggle")' in response.text
    assert 'const quickControlsPanel = document.querySelector("#quick-controls-panel")' in response.text
    assert "function setQuickControlsOpen" in response.text
    assert 'quickControlsToggle?.addEventListener("click"' in response.text
    assert "const agentOptimizationControls = Array.from" in response.text
    assert "function applyAgentOptimizationProfile" in response.text
    assert "function agentOptimizationProfileFor" in response.text
    assert "function agentOptimizationScore" in response.text
    assert "agentOptimizationProfiles" in response.text
    assert "llm_operator_verbose_enabled: false" in response.text
    assert "llm_operator_step_validation_enabled: false" in response.text
    assert "function finalResponseMode" in response.text
    assert "llm_operator_final_response_mode: finalResponseMode()" in response.text
    assert 'const outputCompositionToggle = document.querySelector("#output-composition-toggle")' in response.text
    assert "function verificationEnforced" in response.text
    assert "llm_operator_verification_enforced: verificationEnforced()" in response.text
    assert 'const verificationEnforcedToggle = document.querySelector("#verification-enforced-toggle")' in response.text
    assert 'const settingStepValidation = document.querySelector("#setting-step-validation")' in response.text
    assert "llm_operator_step_validation_enabled: settingStepValidation?.checked === true" in response.text
    assert 'const settingCompletionRetries = document.querySelector("#setting-completion-retries")' not in response.text
    assert 'const settingClarificationMode = document.querySelector("#setting-clarification-mode")' in response.text
    assert 'const quickClarificationMode = document.querySelector("#quick-clarification-mode")' in response.text
    assert "const agentClarificationControls = Array.from" in response.text
    assert "const agentClarificationHeatBars = Array.from" in response.text
    assert "function applyClarificationModeToControls" in response.text
    assert "function clarificationModeScore" in response.text
    assert 'quickClarificationMode?.addEventListener("click", handleClarificationModeClick)' in response.text
    assert 'const settingClarificationRounds = document.querySelector("#setting-clarification-rounds")' in response.text
    assert 'const settingOperatorVerbose = document.querySelector("#setting-operator-verbose")' in response.text
    assert 'const settingPromptRephraseEnabled = document.querySelector("#setting-prompt-rephrase-enabled")' in response.text
    assert 'const settingLlmBaseHost = document.querySelector("#setting-llm-base-host")' in response.text
    assert 'const settingLlmBasePort = document.querySelector("#setting-llm-base-port")' in response.text
    assert 'const settingLlmTimeoutSeconds = document.querySelector("#setting-llm-timeout-seconds")' in response.text
    assert 'const settingLlmMaxTokens = document.querySelector("#setting-llm-max-tokens")' in response.text
    assert 'const settingLlmServiceStatus = document.querySelector("#setting-llm-service-status")' in response.text
    assert 'const llmEndpointTestButton = document.querySelector("#llm-endpoint-test-button")' in response.text
    assert 'llmEndpointTestButton?.addEventListener("click"' in response.text
    assert 'refreshModelConfig({ announce: true })' in response.text
    assert 'const settingResponseStreamingEnabled = document.querySelector("#setting-response-streaming-enabled")' in response.text
    assert 'const settingAutoRephraseRetry = document.querySelector("#setting-auto-rephrase-retry")' not in response.text
    assert 'const settingOnlineMode = document.querySelector("#setting-online-mode")' not in response.text
    assert "#setting-online-mode" not in response.text
    assert 'const settingNumberAnimation = document.querySelector("#setting-number-animation")' in response.text
    assert 'const traceEventsPanel = document.querySelector("#trace-events-panel")' in response.text
    assert 'const traceEventsHeader = document.querySelector("#trace-events-header")' in response.text
    assert 'const traceEventsSearch = document.querySelector("#trace-events-search")' in response.text
    assert 'const traceEventsSearchClear = document.querySelector("#trace-events-search-clear")' in response.text
    assert 'const traceEventsSearchCount = document.querySelector("#trace-events-search-count")' in response.text
    assert 'const chatHistorySearch = document.querySelector("#chat-history-search")' in response.text
    assert 'const chatHistorySearchClear = document.querySelector("#chat-history-search-clear")' in response.text
    assert 'const chatHistorySearchCount = document.querySelector("#chat-history-search-count")' in response.text
    assert 'const llmStreamPanel = document.querySelector("#llm-stream-panel")' in response.text
    assert 'const llmStreamResizer = document.querySelector("#llm-stream-resizer")' in response.text
    assert 'const llmStreamTokenRate = document.querySelector("#llm-stream-token-rate")' in response.text
    assert 'const llmStreamOutput = document.querySelector("#llm-stream-output")' in response.text
    assert 'const llmStreamSearch = document.querySelector("#llm-stream-search")' in response.text
    assert 'const llmStreamSearchClear = document.querySelector("#llm-stream-search-clear")' in response.text
    assert 'const llmStreamSearchCount = document.querySelector("#llm-stream-search-count")' in response.text
    assert "function searchMatchCountLabel(matched)" in response.text
    assert 'traceEventsSearchCount.textContent = active ? searchMatchCountLabel(matched) : ""' in response.text
    assert 'llmStreamSearchCount.textContent = active ? searchMatchCountLabel(matched) : ""' in response.text
    assert "`${matched} / ${total}`" not in response.text
    assert 'const settingSelfBriefMode = document.querySelector("#setting-self-brief-mode")' not in response.text
    assert 'const settingSelfBriefMaxTokens = document.querySelector("#setting-self-brief-max-tokens")' not in response.text
    assert 'const settingRepairConfidenceThreshold = document.querySelector("#setting-repair-confidence-threshold")' not in response.text
    assert 'const settingLlmLaunchCommand = document.querySelector("#setting-llm-launch-command")' not in response.text
    assert "start-current-qwen3-coder-30b-a3b-awq.sh" not in response.text
    assert "llm_base_url:" in response.text
    assert "llm_timeout_seconds:" in response.text
    assert "llm_max_tokens:" in response.text
    assert "llm_operator_verbose_enabled:" in response.text
    assert "prompt_rephrase_enabled:" in response.text
    assert "response_streaming_enabled:" in response.text
    assert "operator_auto_rephrase_retry_enabled:" not in response.text
    assert "operator_online_mode_enabled:" not in response.text
    assert "function runtimeRepairAttemptsValue" not in response.text
    assert "llm_operator_max_validation_repair_attempts:" not in response.text
    assert "llm_operator_max_deferred_code_repair_attempts:" not in response.text
    assert "llm_operator_max_answer_judge_repair_attempts:" not in response.text
    assert "llm_operator_max_execution_repair_attempts:" not in response.text
    assert "llm_operator_max_completion_repair_attempts:" not in response.text
    assert "llm_operator_repair_confidence_threshold:" not in response.text
    assert "function normalizedUnitFloat" in response.text
    assert 'settingAutoRephraseRetry?.addEventListener("change"' not in response.text
    assert 'settingOnlineMode?.addEventListener("change"' not in response.text
    assert "function appendLlmStreamDelta" in response.text
    assert "function setTraceEventsCollapsed" in response.text
    assert "function wildcardSearchToRegex" in response.text
    assert "function chatHistoryTags(chat)" in response.text
    assert "function clearChatHistorySearch" in response.text
    assert "function applyTraceEventSearch" in response.text
    assert "function renderLlmStreamOutput" in response.text
    assert "function clearLlmStreamSearch" in response.text
    assert "chatHistorySearch?.addEventListener(\"input\"" in response.text
    assert "traceEventsSearch?.addEventListener(\"input\"" in response.text
    assert "llmStreamSearch?.addEventListener(\"input\"" in response.text
    assert "event.key !== \"Escape\"" in response.text
    assert ".llm-stream-search, .trace-search-row" in response.text
    assert "row.hidden = !visible" in response.text
    assert "function setLlmResponseStreamingEnabled" in response.text
    assert "function setLlmStreamCollapsed" in response.text
    assert "openfabric.agentUi.llmStreamCollapsed" in response.text
    assert "setLlmStreamCollapsed(!state.llmStreamCollapsed)" in response.text
    assert "Collapse and disable LLM response streaming" not in response.text
    assert "function setLlmStreamHeightPx" in response.text
    assert "function formatLlmTokenRate" in response.text
    assert "function llmTokenRateParts(inputTokens, totalTokens, outputTokens, durationMs)" in response.text
    assert "function updateRollingNumber" in response.text
    assert "function updateAnimatedNumber" in response.text
    assert "function normalizeNumberAnimationMode" in response.text
    assert "function currentRequestDuration" in response.text
    assert "Request duration:" in response.text
    assert "ui_number_animation:" in response.text
    assert "document.documentElement.dataset.numberAnimation" in response.text
    assert 'const wallClockMeridiemSpacer = "\\u2009"' in response.text
    assert "function formatWallClockTime(now)" in response.text
    assert "const text = formatWallClockTime(now)" in response.text
    assert "updateAnimatedNumber(clock, text)" in response.text
    assert "renderWallClock();" in response.text
    assert "function updateRollingEventLabel" in response.text
    assert "function updateRollingLlmTokenRate" in response.text
    assert "rolling-token-input" in response.text
    assert "inputTokens: Number(source.input_tokens_estimate" in response.text
    assert "in/out ${parts.inputText} / ${parts.outputText}" in response.text
    assert "progress-summary-duration" in response.text
    assert "progressResponseMetrics" not in response.text
    assert 'progressSummary.querySelector(":scope > .progress-summary-duration")' in response.text
    assert "rolling-digit-reel" in response.text
    assert "dataset.copyText" in response.text
    assert "function updateLlmStreamTokenRate" in response.text
    assert "trace-progress-collapsed" in response.text
    assert 'wirePaneCopyButton(llmStreamCopyButton, "llm-stream")' in response.text
    assert "function handleUserFacingLlmDelta" in response.text
    assert "llm.response.delta" in response.text
    assert 'if (event?.event_type === "llm.response.delta") {' in response.text
    assert "llm_operator_self_brief_mode:" not in response.text
    assert "llm_operator_self_brief_max_tokens:" not in response.text
    assert "function normalizeSelfBriefMode" not in response.text
    assert 'fetch("/api/agent/llm/start"' not in response.text
    assert 'fetch("/api/agent/llm/stop"' not in response.text
    assert 'fetch("/api/agent/llm/status"' not in response.text
    assert "/api/agent/llm/terminal/config" not in response.text
    assert "function connectLlmRuntimeTerminal" not in response.text
    assert "function ensureLlmRuntimeTerminal" not in response.text
    assert "llmRuntimeTerminalSessionId" not in response.text
    assert "llmRuntimeTerminalScrollback" not in response.text
    assert 'const settingTerminalVisible = document.querySelector("#setting-terminal-visible")' in response.text
    assert 'const settingTraceVisible = document.querySelector("#setting-trace-visible")' in response.text
    assert 'fetch("/api/agent/settings/config"' in response.text
    assert "detail.terminal_dispatch === true" in response.text
    assert "function setTerminalCollapsed" in response.text
    assert "function setTerminalHeightPx" in response.text
    assert "terminalSplitResizer?.addEventListener(\"pointerdown\"" in response.text
    assert "terminalSplitResizer?.addEventListener(\"keydown\"" in response.text
    assert "function setProgressCollapsed" in response.text
    assert "function updateProgressSummary" in response.text
    assert "function updateProgressHeaderState" in response.text
    assert "progressSummary.replaceChildren(label, duration)" in response.text
    assert "state.stageStartedAtMs.set(normalized, Date.now())" in response.text
    assert "progressTitle.hidden = state.progressCollapsed" in response.text
    assert "progressSummary.hidden = !state.progressCollapsed" in response.text
    assert "function setConversationCollapsed" in response.text
    assert "function clearConversationPane" in response.text
    assert "function clearTerminalPane" in response.text
    assert "async function saveTerminalWorkspace" in response.text
    assert "lastOperatedGatewayId" in response.text
    assert "function rememberOperatedGateway" in response.text
    assert "function restoreLastOperatedGatewaySelection" in response.text
    assert "restoreLastOperatedGatewaySelection()" in response.text
    assert "rememberOperatedGateway(state.terminalConfig.gateway_id)" in response.text
    assert "const gatewayStartup = state.agentUiClientMode" in response.text
    assert ": loadGateways().then(() => refreshVisibleGateways())" in response.text
    assert "gatewayStartup.finally" in response.text
    assert "function scheduleTerminalWorkspaceAutosave" in response.text
    assert "function flushTerminalWorkspaceAutosave" in response.text
    assert "scheduleTerminalWorkspaceAutosave()" in response.text
    assert 'window.addEventListener("pagehide"' in response.text
    assert 'window.addEventListener("beforeunload"' in response.text
    assert 'document.visibilityState === "hidden"' in response.text
    assert "void flushTerminalWorkspaceAutosave({ keepalive: true })" in response.text
    assert 'terminalSaveWorkspaceButton?.addEventListener("click"' in response.text
    assert "body: JSON.stringify({ terminal_cwd: cwd })" in response.text
    assert "conversationClearButton?.addEventListener" in response.text
    assert "conversationHistoryButton?.addEventListener" in response.text
    assert "toggleChatHistoryDrawer()" in response.text
    assert "terminalResetButton?.addEventListener(\"click\", clearTerminalPane)" in response.text
    assert "function setConversationThinking" in response.text
    assert "setConversationThinking(true)" in response.text
    assert "function updateAdaptivePaneLayout" in response.text
    assert "allPanesCollapsed" in response.text
    assert "openfabricIdleArt.hidden = !allPanesCollapsed" in response.text
    assert "\"bottom-stack-layout\"" in response.text
    assert "function writeTerminalOutput" in response.text
    assert "function flushTerminalWriteBuffer" in response.text
    assert "function terminalOutputNeedsUserInput" in response.text
    assert "function revealTerminalForUserInput" in response.text
    assert "function revealTerminalForInputRequiredEvent" in response.text
    assert "setTerminalVisible(true).then" in response.text
    assert "revealTerminalForUserInput(output)" in response.text
    assert "enter passphrase" in response.text
    assert "async function syncTerminalCwd" in response.text
    assert "await syncTerminalCwd()" in response.text
    assert "/api/agent/terminal/config" in response.text
    assert "/api/agent/terminal/cwd" in response.text
    assert "terminalHeader?.addEventListener(\"click\"" in response.text
    assert "progressHeader?.addEventListener(\"click\"" in response.text
    assert "conversationHeader?.addEventListener(\"click\"" in response.text
    assert 'closest("button, input, label, select, textarea, a")' in response.text
    assert "setTraceCollapsed(state.traceCollapsed)" in response.text
    assert "setConversationCollapsed(state.conversationCollapsed)" in response.text
    assert "setLlmStreamCollapsed(state.llmStreamCollapsed)" in response.text
    assert "setProgressCollapsed(state.progressCollapsed)" in response.text
    assert "const nextTerminalVisible = state.terminalVisible || openTerminalFromUrl" in response.text
    assert "const visibilityUpdate = setTerminalVisible(nextTerminalVisible" in response.text
    assert "context: agentSettingsContext()" in response.text
    assert "requestBody.context = {" in response.text
    assert "title.textContent = `Running: ${label}`" in response.text
    assert "record.title.textContent = `${statusText}: ${record.label}`" in response.text
    assert "execution.command." in response.text
    assert "if (!isCommandStreamEvent(event))" in response.text
    assert "function stopCurrentRun" in response.text
    assert "/api/agent/stop/" in response.text
    assert 'channel === "cancelled"' in response.text
    assert "Stopped: ${record.label}" in response.text
    assert "} else if (state.requestId) {" in response.text
    assert "setStopEnabled(true);" in response.text
    assert "function setTraceCollapsed" in response.text
    assert "function setTraceVisible" in response.text
    assert "function syncVisualizationPaneVisibility" in response.text
    assert "function initVisualizationResizer" in response.text
    assert "function shouldRenderTraceUi" in response.text
    assert "traceToggle.hidden = !state.traceVisible" in response.text
    assert "if (shouldRenderTraceUi())" in response.text
    assert "function startNewChat" in response.text
    assert 'const contextMeter = document.querySelector("#context-meter")' in response.text
    assert "function updateContextMeter" in response.text
    assert "function applyContextWindowTokens" in response.text
    assert "llm_context_usage" in response.text
    assert "applyContextWindowTokens(payload?.llm_context_window_tokens || active.context_window_tokens)" in response.text
    assert "applyContextWindowTokens(payload.llm_context_window_tokens)" in response.text
    assert "state.contextWindowTokens" in response.text
    assert "ctx available ${safeRemaining.toFixed(0)}%" in response.text
    assert 'contextMeter.dataset.contextPercent = "--"' in response.text
    assert "contextMeter.dataset.contextPercent = `${safeRemaining.toFixed(0)}%`" in response.text
    assert "resetView({ clearChat: true, resetConversation: true })" in response.text
    assert 'newChatButton?.addEventListener("click", startNewChat)' in response.text
    assert "function handleConfirmationShortcut" in response.text
    assert "function syncConversationHeaderConfirmation" in response.text
    assert "function triggerHeaderConfirmationAction" in response.text
    assert 'const autoApproveToggle = document.querySelector("#auto-approve-toggle")' in response.text
    assert 'const deepReasoningToggle = document.querySelector("#deep-reasoning-toggle")' in response.text
    assert 'const conversationDeepReasoningBadge = document.querySelector("#conversation-deep-reasoning-badge")' in response.text
    assert 'const conversationStreamingBadge = document.querySelector("#conversation-streaming-badge")' in response.text
    assert 'const settingWorkflowExecutionMode = document.querySelector("#setting-workflow-execution-mode")' in response.text
    assert 'const settingOperatorExecutionMode = document.querySelector("#setting-operator-execution-mode")' not in response.text
    assert 'const settingOperatorTryoutMode = document.querySelector("#setting-operator-tryout-mode")' not in response.text
    assert 'const settingWorkspaceCwdGuard = document.querySelector("#setting-workspace-cwd-guard")' in response.text
    assert "function appendStreamingStepConversationMessage" in response.text
    assert "function appendStreamingStepMessagesFromTracePayload" in response.text
    assert 'const settingShellInputBindingsMode = document.querySelector("#setting-shell-input-bindings-mode")' not in response.text
    assert "OPERATOR_POLICY_MODE_KEYS" not in response.text
    assert "OPERATOR_POLICY_PRESETS" not in response.text
    assert "function normalizePolicyMode" not in response.text
    assert 'const settingPolicyPreset = document.querySelector("#setting-policy-preset")' not in response.text
    assert "operator_policy_profile" in response.text
    assert "operator_effect_policy_mode" not in response.text
    assert "operator_memory_question_policy_mode" not in response.text
    assert "setting-policy-memory-question" not in response.text
    assert "operator_streaming_scope_policy_mode" not in response.text
    assert "operator_python_code_review_policy_mode" not in response.text
    assert "guided_deliberation_mode" not in response.text
    assert "operator_execution_mode" not in response.text
    assert "sql_agent_chat_route_mode" in response.text
    assert "operator_tryout_mode" not in response.text
    assert "fast_trout" not in response.text
    assert 'settingOperatorTryoutMode?.addEventListener("click"' not in response.text
    assert "operator_workspace_cwd_guard_enabled" in response.text
    assert "shell_input_bindings_mode" not in response.text
    assert "function setGuidedDeliberationMode" not in response.text
    assert "function normalizeOperatorExecutionMode" not in response.text
    assert "function normalizeSqlAgentChatRouteMode" in response.text
    assert "function normalizeOperatorTryoutMode" not in response.text
    assert "function updateWorkflowExecutionModeBadge" in response.text
    assert "function normalizeShellInputBindingsMode" not in response.text
    assert "guided_deliberation_mode: normalizeGuidedDeliberationMode(" not in response.text
    assert "operator_execution_mode: normalizeOperatorExecutionMode(" not in response.text
    assert "sql_agent_chat_route_mode: normalizeSqlAgentChatRouteMode(" in response.text
    assert "operator_tryout_mode: normalizeOperatorTryoutMode(" not in response.text
    assert "operator_workspace_cwd_guard_enabled: settingWorkspaceCwdGuard?.checked === true" in response.text
    assert "guided_deliberation_mode: normalizeGuidedDeliberationMode(safe.guided_deliberation_mode)" not in response.text
    assert "payload?.guided_deliberation_mode ||" not in response.text
    assert "payload?.operator_execution_mode ||" not in response.text
    assert "payload?.sql_agent_chat_route_mode ||" in response.text
    assert "payload?.operator_tryout_mode ||" not in response.text
    assert 'fetch("/api/agent/runtime-controls"' in response.text
    assert "async function loadRuntimeControls({" in response.text
    assert "collapsePanelsWhenUnavailable = false" in response.text
    assert "payload.operator_execution_mode = safe.operator_execution_mode" not in response.text
    assert "sql_agent_chat_route_mode: safe.sql_agent_chat_route_mode" in response.text
    assert "payload.operator_tryout_mode = safe.operator_tryout_mode" not in response.text
    assert "loadRuntimeControls({ preservePersistentSettings: true, collapsePanelsWhenUnavailable: true })" in response.text
    assert "function maybeAutoApproveConfirmation" in response.text
    assert "auto_approved_confirmation: true" in response.text
    assert 'triggerHeaderConfirmationAction("approve")' in response.text
    assert 'triggerHeaderConfirmationAction("deny")' in response.text
    assert 'triggerHeaderConfirmationAction("memory")' in response.text
    assert "openMemoryForResponseContext(state.latestMemoryContext)" in response.text
    assert 'showInlineMemoryEditor(actions, memoryContextForConfirmation(message, requestId))' in response.text
    assert 'key !== "a" && key !== "d"' in response.text
    assert 'document.addEventListener("keydown", handleConfirmationShortcut)' in response.text


def test_agent_ui_conversation_summary_endpoint_uses_llm() -> None:
    llm = FakeSummaryLLMClient()
    runtime = SimpleNamespace(llm_client=llm)
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/conversation/summary",
        json={"text": "Request\nlist docker images\nFinal response\nConfirmation Required"},
    )

    assert response.status_code == 200
    assert response.json() == {"summary": "Docker images need approval"}
    assert llm.prompts
    assert "12 words or fewer" in llm.prompts[-1]
    assert "concrete answer, number, count, size, or yes/no outcome" in llm.prompts[-1]


def test_agent_ui_name_suggestion_endpoint_uses_llm() -> None:
    llm = FakeNameLLMClient()
    runtime = SimpleNamespace(llm_client=llm)
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/name/suggest",
        json={"current_name": "Agent", "model_name": "qwen-test"},
    )

    assert response.status_code == 200
    assert response.json() == {"name": "Coda"}
    assert llm.prompts
    assert "Generate one short, friendly display name" in llm.prompts[-1]
    assert "Creative lane:" in llm.prompts[-1]
    assert "Creative seed:" in llm.prompts[-1]


def test_agent_ui_name_suggestion_retries_overused_llm_name() -> None:
    llm = FakeSequenceNameLLMClient(["CodeWhisper", "Bright Loom"])
    runtime = SimpleNamespace(llm_client=llm)
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/name/suggest",
        json={
            "current_name": "Agent",
            "model_name": "qwen-test",
            "recent_names": ["CodeNook", "Coda"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"name": "Bright Loom"}
    assert len(llm.prompts) == 2
    assert "Avoid exact names:" in llm.prompts[0]
    assert "CodeWhisper" in llm.prompts[0]
    assert "CodeNook" in llm.prompts[0]


def test_agent_ui_prompt_contains_context_meter() -> None:
    client = _client()

    response = client.get("/agent-ui")

    assert response.status_code == 200
    assert 'id="context-meter"' in response.text
    assert 'class="prompt-context-row"' in response.text
    assert 'data-context-percent="--"' in response.text
    assert "ctx -" in response.text
    assert response.text.index('id="verification-enforced-toggle"') < response.text.index(
        'id="context-meter"'
    )
    assert response.text.index('id="context-meter"') < response.text.index('id="voice-input-button"')
    assert response.text.index('id="voice-input-button"') < response.text.index('id="quick-terminal-toggle-button"')
    assert response.text.index('id="quick-terminal-toggle-button"') < response.text.index('id="new-chat-button"')
    assert response.text.index('id="voice-input-button"') < response.text.index('id="new-chat-button"')
    assert response.text.index('id="context-meter"') < response.text.index('id="prompt-input"')
    assert 'id="footer-voice-input-anchor"' not in response.text
    assert "promptActions.appendChild(voiceInputButton)" not in response.text
    assert 'id="settings-toggle"' in response.text
    assert 'id="llm-activity-indicator"' in response.text
    assert 'id="learning-runtime-indicator"' in response.text
    assert response.text.index('id="llm-activity-indicator"') < response.text.index(
        'id="learning-runtime-indicator"'
    )
    assert response.text.index('id="learning-runtime-indicator"') < response.text.index(
        'id="settings-toggle"'
    )
    assert response.text.index('id="settings-toggle"') < response.text.index(
        'id="memory-toggle"'
    )
    assert 'id="auto-approve-toggle"' in response.text
    assert 'aria-label="Auto-Approve Commands"' in response.text
    assert 'id="deep-reasoning-toggle"' in response.text
    assert 'aria-label="Reasoning profile"' in response.text
    assert 'id="setting-response-streaming-enabled"' in response.text
    assert 'id="setting-prompt-rephrase-enabled"' in response.text
    assert 'id="setting-auto-rephrase-retry"' not in response.text
    assert "Auto Retry By Rephrasing" not in response.text
    assert "Retry query by rephrasing automatically" not in response.text
    assert 'id="llm-stream-panel"' in response.text
    assert 'id="llm-stream-resizer"' in response.text
    assert 'id="llm-stream-token-rate"' in response.text
    assert 'Reasoning input tokens, output tokens, total tokens, and tokens per second' in response.text
    assert 'in/out 0 / 0 · 0 t · - t/s' in response.text
    assert 'id="llm-stream-output"' in response.text
    assert 'id="llm-stream-search"' in response.text
    assert 'placeholder="Search reasoning..."' in response.text
    assert 'id="llm-stream-search-clear"' in response.text
    assert 'id="llm-stream-search-count"' in response.text
    assert 'id="trace-events-panel"' in response.text
    assert 'id="trace-events-header"' in response.text
    assert 'id="trace-events-search"' in response.text
    assert 'placeholder="Search events..."' in response.text
    assert 'id="trace-events-search-clear"' in response.text
    assert 'id="trace-events-search-count"' in response.text
    assert 'id="chat-history-search"' in response.text
    assert 'placeholder="Search chats or tags..."' in response.text
    assert 'id="chat-history-search-clear"' in response.text
    assert 'id="chat-history-search-count"' in response.text
    assert 'id="setting-workflow-execution-mode"' in response.text
    assert 'aria-label="Workflow execution mode"' in response.text
    assert 'id="setting-operator-execution-mode"' not in response.text
    assert 'id="setting-operator-tryout-mode"' not in response.text
    assert 'settings-wide-mode-row' in response.text
    assert 'data-mode="fast_trout"' not in response.text
    assert 'id="setting-workspace-cwd-guard"' in response.text
    assert 'data-mode="off"' not in response.text
    assert 'data-mode="auto"' in response.text
    assert 'data-mode="always"' not in response.text
    assert response.text.index('id="conversation-header"') < response.text.index(
        'id="quick-controls-toggle"'
    )
    assert response.text.index('id="quick-controls-toggle"') < response.text.index(
        'id="conversation-immersive-toggle"'
    )
    assert response.text.index('id="conversation-immersive-toggle"') < response.text.index(
        'id="conversation-summary-label"'
    )
    assert response.text.index('id="conversation-summary-label"') < response.text.index(
        'class="conversation-actions conversation-actions-primary"'
    )
    assert response.text.index('id="conversation-clear-button"') < response.text.index(
        'id="conversation-history-button"'
    )
    assert response.text.index('id="quick-controls-panel"') < response.text.index(
        'id="conversation-history-button"'
    )
    assert response.text.index('id="conversation-copy-button"') < response.text.index(
        'class="conversation-actions conversation-actions-secondary"'
    )
    assert response.text.index(
        'class="conversation-actions conversation-actions-secondary"'
    ) < response.text.index('id="quick-controls-panel"')
    assert response.text.index('id="quick-controls-panel"') < response.text.index(
        'id="agent-mode-control"'
    )
    assert response.text.index('id="agent-mode-control"') < response.text.index(
        ">Answers<"
    )
    assert response.text.index(">Answers<") < response.text.index(
        'id="output-composition-toggle"'
    )
    assert response.text.index('id="output-composition-toggle"') < response.text.index(
        ">Validation<"
    )
    assert response.text.index(">Validation<") < response.text.index(
        'id="verification-enforced-toggle"'
    )
    assert response.text.index('id="verification-enforced-toggle"') < response.text.index(
        'id="auto-approve-toggle"'
    )
    assert response.text.index(">Approval<") < response.text.index('id="auto-approve-toggle"')
    assert response.text.index('id="auto-approve-toggle"') < response.text.index(
        ">Optimize agent for<"
    )
    assert response.text.index(">Optimize agent for<") < response.text.index(
        'id="agent-optimization-control"'
    )
    assert response.text.index('id="agent-optimization-control"') < response.text.index(
        'id="quick-clarification-mode"'
    )
    assert response.text.index('id="quick-clarification-mode"') < response.text.index(
        'id="theme-select"'
    )
    assert 'id="settings-drawer"' in response.text
    assert response.text.index('id="settings-drawer"') < response.text.index(
        'id="deep-reasoning-toggle"'
    )
    assert response.text.index('id="deep-reasoning-toggle"') < response.text.index(
        'id="setting-repair-profile"'
    )
    assert response.text.index('id="setting-repair-profile"') < response.text.index(
        'id="setting-workflow-execution-mode"'
    )
    assert response.text.index('id="setting-workflow-execution-mode"') < response.text.index(
        'id="setting-prompt-rephrase-enabled"'
    )
    assert response.text.index('id="setting-prompt-rephrase-enabled"') < response.text.index(
        'id="setting-response-streaming-enabled"'
    )
    assert response.text.index('id="setting-response-streaming-enabled"') < response.text.index(
        'id="setting-workspace-cwd-guard"'
    )
    assert "app.css?v=20260606-prompt-gutters" in response.text
    assert "app.js?v=20260606-run-queue" in response.text


def test_agent_ui_model_config_lists_auto_and_configured_model(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                default_model="configured-model",
                llm_base_url="http://10.0.0.12:8123/v1",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    monkeypatch.setattr(
        agent_ui.urllib_request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(agent_ui.urllib_error.URLError("offline")),
    )

    response = client.get("/api/agent/model/config")

    assert response.status_code == 200
    assert response.json() == {
        "configured_model": "configured-model",
        "active_model": {"name": "configured-model", "source": "configured", "available": False},
        "default_selection": "auto",
        "llm_endpoint": {
            "llm_base_scheme": "http",
            "llm_base_host": "10.0.0.12",
            "llm_base_port": 8123,
            "llm_base_path": "/v1",
            "llm_base_url": "http://10.0.0.12:8123/v1",
        },
        "llm_context_window_tokens": 32768,
        "llm_context_window_source": "settings",
        "options": [
            {"value": "auto", "label": "Auto"},
            {"value": "configured-model", "label": "configured-model"},
        ],
    }


def test_agent_ui_model_config_prefers_live_discovered_model(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                default_model="configured-model",
                llm_base_url="http://10.0.0.12:8123/v1",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return (
                b'{"data":[{"id":"QuantTrio/Qwen3-30B-A3B-Thinking-2507-AWQ",'
                b'"max_model_len":8192}]}'
            )

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        calls.append({"url": request.full_url, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)

    response = client.get("/api/agent/model/config")

    assert response.status_code == 200
    assert response.json()["configured_model"] == "configured-model"
    assert response.json()["active_model"] == {
        "name": "QuantTrio/Qwen3-30B-A3B-Thinking-2507-AWQ",
        "source": "discovered",
        "available": True,
        "context_window_tokens": 8192,
    }
    assert response.json()["llm_context_window_tokens"] == 8192
    assert response.json()["llm_context_window_source"] == "model_api"
    assert calls == [{"url": "http://10.0.0.12:8123/v1/models", "timeout": 1.2}]


def test_agent_ui_model_config_uses_runtime_controls_with_preview_overrides(
    monkeypatch,
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                default_model="configured-model",
                llm_base_url="http://bootstrap.local:8123/v1",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    calls: list[dict[str, Any]] = []

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        calls.append({"url": request.full_url, "timeout": timeout})
        raise agent_ui.urllib_error.URLError("offline")

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)

    updated = client.post(
        "/api/agent/runtime-controls",
        json={"llm_base_url": "https://persisted.local:9443/openai/v1"},
    )
    default_response = client.get("/api/agent/model/config")
    preview_response = client.get(
        "/api/agent/model/config",
        params={
            "llm_base_scheme": "http",
            "llm_base_host": "preview.local",
            "llm_base_port": "8124",
            "llm_base_path": "/preview/v1",
        },
    )
    runtime_controls = client.get("/api/agent/runtime-controls")

    assert updated.status_code == 200
    assert default_response.status_code == 200
    assert default_response.json()["llm_endpoint"] == {
        "llm_base_scheme": "https",
        "llm_base_host": "persisted.local",
        "llm_base_port": 9443,
        "llm_base_path": "/openai/v1",
        "llm_base_url": "https://persisted.local:9443/openai/v1",
    }
    assert preview_response.status_code == 200
    assert preview_response.json()["llm_endpoint"] == {
        "llm_base_scheme": "http",
        "llm_base_host": "preview.local",
        "llm_base_port": 8124,
        "llm_base_path": "/preview/v1",
        "llm_base_url": "http://preview.local:8124/preview/v1",
    }
    assert runtime_controls.json()["llm_base_url"] == (
        "https://persisted.local:9443/openai/v1"
    )
    assert calls == [
        {"url": "https://persisted.local:9443/openai/v1/models", "timeout": 1.2},
        {"url": "http://preview.local:8124/preview/v1/models", "timeout": 1.2},
    ]


def test_agent_ui_settings_config_exposes_operator_retry_defaults(tmp_path) -> None:
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                operator_policy_profile="assisted",
                reasoning_profile="deep",
                repair_profile="aggressive",
                workflow_execution_mode="streaming",
                prompt_rephrase_enabled=False,
                response_streaming_enabled=True,
                agent_clarification_mode="pedantic",
                llm_operator_max_clarification_rounds=8,
                agent_memory_enabled=True,
                agent_memory_prompt_max_chars=4321,
                agent_command_template_cache_similarity_threshold=0.77,
                agent_command_template_cache_secondary_similarity_threshold=0.33,
                lrnt_similarity_threshold=0.91,
                sql_agent_chat_route_mode="direct",
                operator_workspace_cwd_guard_enabled=True,
                llm_operator_step_validation_enabled=False,
                llm_operator_verbose_enabled=False,
                audio_transcriber_enabled=False,
                audio_transcriber_service_url="http://audio-box.local:9913",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
                agent_ui_number_animation="flip",
                agent_ui_auto_immersive_min_width_px=640,
                llm_base_url="https://192.168.1.25:9443/openai/v1",
                llm_timeout_seconds=321,
                llm_max_tokens=7777,
                agent_ui_llm_launch_conda_env="vllm-test",
                agent_ui_llm_launch_command="./src/llm/start-test.sh",
                agent_ui_llm_launch_cwd="/tmp/models",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    response = client.get("/api/agent/settings/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["defaults"]["agent_display_name"] == "Agent"
    assert payload["defaults"]["auto_approve_commands"] is False
    assert payload["defaults"]["operator_policy_profile"] == "assisted"
    assert payload["defaults"]["reasoning_profile"] == "deep"
    assert payload["defaults"]["repair_profile"] == "aggressive"
    assert payload["defaults"]["workflow_execution_mode"] == "streaming"
    assert payload["defaults"]["prompt_rephrase_enabled"] is False
    assert payload["defaults"]["response_streaming_enabled"] is True
    assert payload["defaults"]["ui_terminal_visible"] is True
    assert payload["defaults"]["ui_trace_visible"] is True
    assert payload["defaults"]["ui_visualization_visible"] is True
    assert payload["defaults"]["ui_chat_bubbles_enabled"] is False
    assert set(payload["defaults"]) <= (PUBLIC_RUNTIME_CONTROL_KEYS | PUBLIC_UI_PREFERENCE_KEYS)
    assert "llm_operator_max_validation_repair_attempts" not in payload["defaults"]
    assert "llm_operator_max_deferred_code_repair_attempts" not in payload["defaults"]
    assert "llm_operator_max_answer_judge_repair_attempts" not in payload["defaults"]
    assert "llm_operator_max_execution_repair_attempts" not in payload["defaults"]
    assert "llm_operator_max_completion_repair_attempts" not in payload["defaults"]
    assert payload["defaults"]["llm_operator_final_response_mode"] == "simple"
    assert payload["defaults"]["llm_operator_cardinality_judge_mode"] == "auto"
    assert payload["defaults"]["llm_operator_verification_enforced"] is True
    assert "guided_deliberation_mode" not in payload["defaults"]
    assert "llm_operator_repair_confidence_threshold" not in payload["defaults"]
    assert "operator_execution_mode" not in payload["defaults"]
    assert payload["defaults"]["sql_agent_chat_route_mode"] == "direct"
    assert payload["defaults"]["operator_workspace_cwd_guard_enabled"] is True
    assert "shell_input_bindings_mode" not in payload["defaults"]
    assert "operator_effect_policy_mode" not in payload["defaults"]
    assert "operator_interaction_policy_mode" not in payload["defaults"]
    assert "operator_stdout_policy_mode" not in payload["defaults"]
    assert "operator_failure_policy_mode" not in payload["defaults"]
    assert "operator_memory_policy_mode" not in payload["defaults"]
    assert "operator_memory_question_policy_mode" not in payload["defaults"]
    assert "operator_streaming_scope_policy_mode" not in payload["defaults"]
    assert "operator_verb_policy_mode" not in payload["defaults"]
    assert "operator_python_code_review_policy_mode" not in payload["defaults"]
    assert payload["defaults"]["llm_operator_step_validation_enabled"] is False
    assert "llm_response_streaming_enabled" not in payload["defaults"]
    assert payload["defaults"]["llm_operator_verbose_enabled"] is False
    assert "operator_auto_rephrase_retry_enabled" not in payload["defaults"]
    assert "operator_online_mode_enabled" not in payload["defaults"]
    assert "llm_operator_self_brief_mode" not in payload["defaults"]
    assert payload["defaults"]["ui_number_animation"] == "flip"
    assert payload["defaults"]["ui_chat_pop_animation"] == "none"
    assert payload["defaults"]["ui_thinking_text_animation"] == "roll-up"
    assert payload["defaults"]["ui_auto_immersive_min_width_px"] == 640
    assert "llm_operator_self_brief_max_tokens" not in payload["defaults"]
    assert payload["defaults"]["agent_clarification_mode"] == "pedantic"
    assert "llm_operator_clarification_strategy" not in payload["defaults"]
    assert payload["defaults"]["llm_operator_max_clarification_rounds"] == 8
    assert payload["defaults"]["agent_memory_enabled"] is True
    assert payload["defaults"]["agent_memory_prompt_max_chars"] == 4321
    assert payload["defaults"]["agent_command_template_cache_similarity_threshold"] == 0.77
    assert payload["defaults"]["agent_command_template_cache_secondary_similarity_threshold"] == 0.33
    assert payload["defaults"]["lrnt_similarity_threshold"] == 0.91
    assert payload["defaults"]["agent_events_enabled"] is True
    assert "agent_plan_cache_enabled" not in payload["defaults"]
    assert "agent_plan_cache_prompt_max_chars" not in payload["defaults"]
    assert "agent_plan_cache_similarity_threshold" not in payload["defaults"]
    assert "agent_plan_cache_max_entries" not in payload["defaults"]
    assert "agent_computation_cache_enabled" not in payload["defaults"]
    assert "agent_computation_cache_prompt_max_chars" not in payload["defaults"]
    assert "agent_computation_cache_similarity_threshold" not in payload["defaults"]
    assert "agent_computation_cache_max_entries" not in payload["defaults"]
    assert payload["defaults"]["agent_learning_ledger_auto_learn_enabled"] is True
    assert payload["defaults"]["lrnt_enabled"] is True
    assert payload["defaults"]["lrdirect_enabled"] is True
    assert payload["defaults"]["browser_notifications_enabled"] is True
    assert payload["defaults"]["notification_sound_enabled"] is True
    assert payload["defaults"]["notification_sound_variant"] == "chime"
    assert payload["defaults"]["notification_sound_volume"] == 0.55
    assert payload["defaults"]["audio_voice_input_enabled"] is False
    assert payload["defaults"]["audio_transcriber_service_host"] == "audio-box.local"
    assert payload["defaults"]["audio_transcriber_service_port"] == 9913
    assert payload["defaults"]["audio_transcriber_service_url"] == "http://audio-box.local:9913"
    assert payload["defaults"]["audio_capture_preset"] == "balanced"
    assert payload["defaults"]["audio_silence_timeout_seconds"] == 2
    assert payload["defaults"]["llm_base_scheme"] == "https"
    assert payload["defaults"]["llm_base_host"] == "192.168.1.25"
    assert payload["defaults"]["llm_base_port"] == 9443
    assert payload["defaults"]["llm_base_path"] == "/openai/v1"
    assert payload["defaults"]["llm_base_url"] == "https://192.168.1.25:9443/openai/v1"
    assert payload["defaults"]["llm_timeout_seconds"] == 321
    assert payload["defaults"]["llm_max_tokens"] == 7777
    assert "llm_launch_conda_env" not in payload["defaults"]
    assert "llm_launch_command" not in payload["defaults"]
    assert "llm_launch_cwd" not in payload["defaults"]
    assert payload["limits"]["llm_operator_max_clarification_rounds"] == {"min": 0, "max": 10}
    assert payload["limits"]["agent_memory_prompt_max_chars"] == {"min": 200, "max": 20000}
    assert payload["limits"]["ui_auto_immersive_min_width_px"] == {"min": 0, "max": 4000}
    assert "repair_attempts" not in payload["limits"]
    assert "agent_plan_cache_prompt_max_chars" not in payload["limits"]
    assert "agent_computation_cache_prompt_max_chars" not in payload["limits"]
    assert "llm_operator_self_brief_max_tokens" not in payload["limits"]
    assert "llm_operator_repair_confidence_threshold" not in payload["limits"]
    assert payload["limits"]["audio_transcriber_service_port"] == {"min": 1, "max": 65535}
    assert "llm_endpoint_port" not in payload["limits"]
    assert payload["limits"]["llm_base_port"] == {"min": 1, "max": 65535}
    assert payload["limits"]["llm_timeout_seconds"] == {"min": 1, "max": 1800}
    assert payload["limits"]["llm_max_tokens"] == {"min": 0, "max": 65536}
    assert payload["limits"]["audio_silence_timeout_seconds"] == {"min": 1, "max": 10}


def test_agent_ui_settings_config_default_llm_launch_command_is_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("AOR_AGENT_UI_LLM_LAUNCH_COMMAND", raising=False)
    client = TestClient(create_app(Settings(), agent_runtime=FakeAgentRuntime()))

    response = client.get("/api/agent/settings/config")

    assert response.status_code == 200
    assert "llm_launch_command" not in response.json()["defaults"]


def test_agent_ui_settings_config_honors_auto_learn_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AOR_AGENT_LEARNING_LEDGER_AUTO_LEARN_ENABLED", "false")
    settings = get_settings(cwd=tmp_path).model_copy(
        update={"agent_ui_settings_db_path": tmp_path / "agent_ui_settings.db"}
    )
    client = TestClient(create_app(settings, agent_runtime=FakeAgentRuntime()))

    response = client.get("/api/agent/settings/config")

    assert response.status_code == 200
    assert response.json()["defaults"]["agent_learning_ledger_auto_learn_enabled"] is False


def test_agent_ui_memory_api_crud_and_audit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=tmp_path / "agent_memory.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    created = client.post(
        "/api/agent/memory",
        json={
            "instruction": "Use machine-readable command output before parsing.",
            "summary": "Prefer parseable output.",
            "scope": "global",
            "tags": ["git", "parsing"],
        },
    )

    assert created.status_code == 200
    entry = created.json()["entry"]
    memory_id = entry["memory_id"]
    assert entry["status"] == "active"
    assert entry["provenance"] == "manual"

    listed = client.get("/api/agent/memory", params={"status": "active"})
    assert listed.status_code == 200
    assert [item["memory_id"] for item in listed.json()["entries"]] == [memory_id]
    assert [item["memory_id"] for item in listed.json()["picker_entries"]] == [memory_id]
    assert listed.json()["stats"]["active"] == 1
    assert listed.json()["stats"]["total"] == 1
    assert listed.json()["stats"]["filtered"] == 1
    assert listed.json()["stats"]["picker"] == 1

    updated = client.patch(
        f"/api/agent/memory/{memory_id}",
        json={
            "instruction": "Prefer machine-readable output, then verify exact postconditions.",
            "summary": "Parseable output and verification.",
            "tags": ["git", "verification"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["entry"]["summary"] == "Parseable output and verification."

    tag_filtered = client.get("/api/agent/memory", params={"status": "active", "tag": "verification"})
    assert [item["memory_id"] for item in tag_filtered.json()["entries"]] == [memory_id]
    assert [item["memory_id"] for item in tag_filtered.json()["picker_entries"]] == [memory_id]

    retired = client.post(f"/api/agent/memory/{memory_id}/retire")
    assert retired.status_code == 200
    assert retired.json()["entry"]["status"] == "retired"

    active_after_retire = client.get("/api/agent/memory", params={"status": "active"})
    assert active_after_retire.json()["entries"] == []
    assert active_after_retire.json()["picker_entries"] == []
    assert active_after_retire.json()["stats"]["active"] == 0
    assert active_after_retire.json()["stats"]["retired"] == 1

    audit = client.get(f"/api/agent/memory/{memory_id}/audit")
    assert audit.status_code == 200
    assert [event["event_type"] for event in audit.json()["events"]] == [
        "memory.created",
        "memory.updated",
        "memory.retired",
    ]

    restored = client.post(f"/api/agent/memory/{memory_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["entry"]["status"] == "active"


def test_agent_ui_addtomemory_shortcut_commits_categorized_memory(tmp_path) -> None:
    runtime = CountingAgentRuntime()
    db_path = tmp_path / "agent_memory.db"
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=db_path,
                agent_chats_db_path=tmp_path / "chats.db",
            ),
            agent_runtime=runtime,
        )
    )
    note = "\n".join(
        [
            "Patient.PatientID",
            "  -> Study.PatientID",
            "RT objects:",
            "RTPlan.SOPInstanceUID",
            "  -> BeamSequence.SOPInstanceUID",
            "RTSTRUCT objects:",
            "RTSTRUCT.SOPInstanceUID",
            "  -> ROIContourSequence.SOPInstanceUID",
            "ROI joins:",
            "StructureSetROISequence.ROINumber",
            "  -> ROIContourSequence.ReferencedROINumber",
            "RTDOSE links:",
            "RTDOSE.ReferencedRTPlanUID",
            "  -> RTPlan.SOPInstanceUID",
            "Orthanc public hierarchy:",
            "resources.internalid",
            "  -> resources.parenti",
        ]
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": f"/addtomemory {note}"},
    )

    assert submitted.status_code == 200
    payload = submitted.json()
    assert runtime.calls == 0
    assert payload["memory_entries"][0]["status"] == "active"
    assert {"dicom", "sql", "orthanc", "relationship_map"} <= set(
        payload["memory_entries"][0]["tags"]
    )

    with client.stream("GET", payload["stream_url"]) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "Memory saved" in body
    trace = client.get(payload["trace_url"]).json()
    assert trace["status"] == "completed"
    assert "Saved 1 memory entry" in trace["final_response"]
    assert any(event["event_type"] == "memory.addtomemory.saved" for event in trace["events"])

    entry = AgentMemoryStore(db_path).list_entries(status="active")[0]
    assert entry.provenance == "manual"
    assert entry.task_type == ""
    assert "Patient.PatientID -> Study.PatientID" in entry.instruction
    assert "RTPlan.SOPInstanceUID -> BeamSequence.SOPInstanceUID" in entry.instruction
    assert "resources.parenti" in entry.instruction

    retrieved = AgentMemoryStore(db_path).retrieve(
        MemoryRetrievalContext(
            prompt=(
                "Which SQL joins connect RTPlan to BeamSequence in Orthanc DICOM?"
            ),
            tags=["dicom", "sql", "orthanc"],
            max_chars=5000,
        ),
        record_use=False,
    )
    assert [item.memory_id for item in retrieved] == [entry.memory_id]


def test_agent_ui_plan_cache_stats_and_clear_api(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_plan_cache_db_path=tmp_path / "agent_plan_cache.db",
                agent_computation_cache_db_path=tmp_path / "agent_computation_cache.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    stats = client.get("/api/agent/cache/stats")
    cleared = client.post("/api/agent/cache/clear")

    assert stats.status_code == 200
    assert stats.json()["enabled"] is True
    assert stats.json()["stats"]["total_entries"] == 0
    assert stats.json()["computation_enabled"] is True
    assert stats.json()["computation_stats"]["total_entries"] == 0
    assert cleared.status_code == 200
    assert cleared.json()["stats"]["total_entries"] == 0
    assert cleared.json()["computation_stats"]["total_entries"] == 0
    assert cleared.json()["stats"]["last_cleared_at"]
    assert cleared.json()["computation_stats"]["last_cleared_at"]


def test_agent_ui_lrnt_stats_and_clear_api(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_lrn_total_tasks_db_path=tmp_path / "agent_lrn_total_tasks.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    stats = client.get("/api/agent/lrnt/stats")
    cleared = client.post("/api/agent/lrnt/clear")

    assert stats.status_code == 200
    assert stats.json()["enabled"] is True
    assert stats.json()["stats"]["total_entries"] == 0
    assert cleared.status_code == 200
    assert cleared.json()["stats"]["total_entries"] == 0
    assert cleared.json()["stats"]["last_cleared_at"]


def test_agent_ui_lrdirect_clear_api_preserves_lr_templates(tmp_path) -> None:  # type: ignore[no-untyped-def]
    command_db = tmp_path / "agent_command_template_cache.db"
    computation_db = tmp_path / "agent_computation_cache.db"
    key = exact_step_key("list docker images")
    command_store = AgentCommandTemplateCacheStore(command_db)
    command_entry = command_store.upsert_entry(
        CommandTemplateWrite(
            prompt="list docker images",
            command_template="docker images",
            observed_command="docker images",
            exact_step_key=key,
            direct_action={
                "action_id": "action_cached",
                "task_id": "task_cached",
                "kind": "shell_command",
                "command": "docker images",
                "reason": "List images.",
            },
            status="success",
        )
    )
    computation_store = AgentComputationCacheStore(computation_db)
    computation_store.upsert_entry(
        ComputationCacheWrite(
            prompt="list docker images",
            action_kind="python_action",
            input_signature="shape",
            exact_step_key=key,
            code_template="def main(inputs):\n    return 1",
            direct_action={
                "action_id": "action_cached",
                "task_id": "task_cached",
                "kind": "python_action",
                "code": "def main(inputs):\n    return 1",
                "reason": "Calculate.",
            },
            status="success",
        )
    )
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_command_template_cache_db_path=command_db,
                agent_computation_cache_db_path=computation_db,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    cleared = client.post("/api/agent/lrdirect/clear")

    assert cleared.status_code == 200
    assert cleared.json()["cleared"] is True
    assert command_store.get_entry(command_entry.template_id) is not None
    assert AgentCommandTemplateCacheStore(command_db).retrieve_exact_step(key) == []
    assert AgentComputationCacheStore(computation_db).retrieve_exact_step(
        key,
        action_kind="python_action",
        input_signature="shape",
    ) == []


def test_agent_ui_memory_picker_entries_ignore_search_filters(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=tmp_path / "agent_memory.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    first = client.post(
        "/api/agent/memory",
        json={
            "instruction": "Use git porcelain for staging checks.",
            "summary": "Git staging checks.",
            "scope": "global",
            "tags": ["git"],
        },
    ).json()["entry"]["memory_id"]
    second = client.post(
        "/api/agent/memory",
        json={
            "instruction": "Use machine-readable docker output for image sizes.",
            "summary": "Docker image size parsing.",
            "scope": "global",
            "tags": ["docker"],
        },
    ).json()["entry"]["memory_id"]

    filtered = client.get("/api/agent/memory", params={"status": "active", "q": "docker"})

    assert filtered.status_code == 200
    payload = filtered.json()
    assert [item["memory_id"] for item in payload["entries"]] == [second]
    assert {item["memory_id"] for item in payload["picker_entries"]} == {first, second}
    assert payload["stats"]["active"] == 2
    assert payload["stats"]["filtered"] == 1
    assert payload["stats"]["picker"] == 2


def test_agent_ui_memory_context_endpoint_uses_llm_metadata(tmp_path) -> None:  # type: ignore[no-untyped-def]
    llm = FakeMemoryContextLLMClient()
    runtime = SimpleNamespace(llm_client=llm)
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=tmp_path / "agent_memory.db",
            ),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/memory/context",
        json={
            "prompt": "stage all git changes",
            "final_response": "Staged all pending changes.",
            "request_id": "req-test",
            "model_name": "qwen-test",
        },
    )

    assert response.status_code == 200
    draft = response.json()["draft"]
    assert draft["task_type"] == "git"
    assert draft["tool_type"] == "shell"
    assert draft["intent_type"] == "verify_state"
    assert draft["tags"] == ["git", "staging"]
    assert "stage all git changes" in llm.prompts[-1]
    assert "MemoryContextDraftResponse schema" in llm.prompts[-1]


def test_agent_ui_memory_feedback_proposals_are_categorized(tmp_path) -> None:  # type: ignore[no-untyped-def]
    llm = FakeMemoryFeedbackLLMClient()
    runtime = SimpleNamespace(llm_client=llm)
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=tmp_path / "agent_memory.db",
            ),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/memory/feedback",
        json={
            "feedback": "The plan verified git staging incorrectly; check the staged state directly.",
            "prompt": "stage all git changes",
            "model_name": "qwen-test",
            "task_type": "git",
            "tool_type": "shell",
            "intent_type": "verify_state",
            "run_feedback": {
                "feedback_kind": "post_run",
                "outcome": "wrong_decision",
                "request_id": "req-feedback",
                "run_status": "completed",
                "prompt": "stage all git changes",
                "final_response": "The run staged files but verified the wrong state.",
                "error": "",
                "model_name": "qwen-test",
                "applied_memory_ids": [],
                "applied_memory_count": 0,
                "applied_memory_use_count": 0,
            },
        },
    )

    assert response.status_code == 200
    draft = response.json()["proposals"][0]["draft"]
    assert draft["task_type"] == "git"
    assert draft["tool_type"] == "shell"
    assert draft["intent_type"] == "verify_state"
    assert draft["model_name"] == "qwen-test"
    assert draft["model_family"] == "qwen-test"
    assert draft["tags"] == ["git", "staging", "shell", "verify_state"]
    assert "Categorize every create/update draft for targeted retrieval" in llm.prompts[-1]
    assert "Typed post-run feedback object" in llm.prompts[-1]
    assert '"outcome": "wrong_decision"' in llm.prompts[-1]


def test_agent_ui_memory_feedback_can_propose_validation_policy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    llm = FakeValidationPolicyFeedbackLLMClient()
    runtime = SimpleNamespace(llm_client=llm)
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=tmp_path / "agent_memory.db",
            ),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/memory/feedback",
        json={
            "feedback": "Allow #include <iostream> when it is file content in a C++ heredoc.",
            "feedback_target": "validation_policy",
            "prompt": "create main.cpp",
            "model_name": "qwen-test",
            "task_type": "cpp",
            "tool_type": "shell_command",
            "intent_type": "create_file",
            "validator_error_type": "unresolved_shell_placeholder",
        },
    )

    assert response.status_code == 200
    proposal = response.json()["proposals"][0]
    draft = proposal["draft"]
    assert draft["memory_kind"] == "validation_policy"
    assert draft["validator_error_type"] == "unresolved_shell_placeholder"
    assert draft["safe_examples"] == ["#include <iostream> in a quoted heredoc that writes main.cpp"]
    assert draft["blocked_examples"] == ["echo '<calculated_value>'"]
    assert "validation_policy" in draft["tags"]
    assert proposal["status"] == "proposed"
    assert "Feedback target:" in llm.prompts[-1]
    assert "validation_policy" in llm.prompts[-1]


def test_agent_ui_validation_feedback_can_be_rerouted_to_task_memory(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runtime = SimpleNamespace(llm_client=object())
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=tmp_path / "agent_memory.db",
            ),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/memory/feedback",
        json={
            "feedback": (
                "When checking for Docker containers, remember docker ps has empty output "
                "when no containers are running; do not treat that as failed validation."
            ),
            "feedback_target": "validation_policy",
            "prompt": "check running docker containers",
            "model_name": "qwen-test",
        },
    )

    assert response.status_code == 200
    proposal = response.json()["proposals"][0]
    draft = proposal["draft"]
    assert proposal["status"] == "proposed"
    assert draft["memory_kind"] == "task_memory"
    assert draft["task_type"] == "docker"
    assert draft["tool_type"] == "shell"
    assert draft["intent_type"] == "verify_state"
    assert draft["validator_error_type"] == ""
    assert draft["safe_examples"] == [
        "docker ps may produce no stdout when no containers are running; treat empty output as valid evidence for an empty list."
    ]
    assert "Do not fail or retry merely because docker ps stdout is empty." in draft["blocked_examples"]


def test_agent_ui_memory_feedback_draft_uses_run_outcome(tmp_path) -> None:  # type: ignore[no-untyped-def]
    llm = FakeMemoryFeedbackLLMClient()
    runtime = SimpleNamespace(llm_client=llm)
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=tmp_path / "agent_memory.db",
            ),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/memory/feedback/draft",
        json={
            "outcome": "wrong_decision",
            "prompt": "stage all git changes",
            "model_name": "qwen-test",
            "run_feedback": {
                "feedback_kind": "post_run",
                "outcome": "wrong_decision",
                "request_id": "req-feedback",
                "run_status": "completed",
                "prompt": "stage all git changes",
                "final_response": "The run staged files but verified the wrong state.",
                "error": "",
                "model_name": "qwen-test",
                "applied_memory_ids": [],
                "applied_memory_count": 0,
                "applied_memory_use_count": 0,
            },
        },
    )

    assert response.status_code == 200
    assert "wrong decision" in response.json()["feedback"]
    assert "MemoryFeedbackDraftResponse schema" in llm.prompts[-1]
    assert "Selected outcome:" in llm.prompts[-1]
    assert "wrong_decision" in llm.prompts[-1]


def test_agent_ui_memory_feedback_falls_back_without_llm(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runtime = SimpleNamespace(llm_client=object())
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=tmp_path / "agent_memory.db",
            ),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/memory/feedback",
        json={
            "feedback": "For git tasks, verify staged files with git diff --cached.",
            "prompt": "stage git changes",
            "model_name": "qwen-test",
            "task_type": "git",
            "tool_type": "shell",
            "intent_type": "verify_state",
        },
    )

    assert response.status_code == 200
    proposal = response.json()["proposals"][0]
    draft = proposal["draft"]
    assert proposal["status"] == "proposed"
    assert draft["instruction"] == "For git tasks, verify staged files with git diff --cached."
    assert draft["memory_kind"] == "task_memory"
    assert draft["task_type"] == "git"
    assert draft["tool_type"] == "shell"
    assert draft["intent_type"] == "verify_state"


def test_agent_ui_memory_feedback_draft_falls_back_without_llm(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runtime = SimpleNamespace(llm_client=object())
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_memory_db_path=tmp_path / "agent_memory.db",
            ),
            agent_runtime=runtime,
        )
    )

    response = client.post(
        "/api/agent/memory/feedback/draft",
        json={
            "outcome": "wrong_decision",
            "prompt": "stage all git changes",
            "model_name": "qwen-test",
            "run_feedback": {
                "feedback_kind": "post_run",
                "outcome": "wrong_decision",
                "request_id": "req-feedback",
                "run_status": "completed",
                "prompt": "stage all git changes",
                "final_response": "The run staged files but verified the wrong state.",
                "error": "",
                "model_name": "qwen-test",
                "applied_memory_ids": [],
                "applied_memory_count": 0,
                "applied_memory_use_count": 0,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "wrong decision" in payload["feedback"]
    assert "stage all git changes" in payload["feedback"]
    assert payload["rationale"] == "Fallback draft created from run outcome."


def test_agent_ui_memory_feedback_uses_request_llm_endpoint(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8")
            calls.append({"path": self.path, "body": json.loads(body)})
            content = json.dumps(
                {
                    "feedback": "The agent should verify staged files directly next time.",
                    "rationale": "Live endpoint draft.",
                }
            )
            payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = TestClient(
            create_app(
                Settings(
                    openai_compat_model_name="OpenFABRIC Echo",
                    agent_memory_db_path=tmp_path / "agent_memory.db",
                    llm_timeout_seconds=5,
                ),
                agent_runtime=SimpleNamespace(llm_client=object()),
            )
        )

        response = client.post(
            "/api/agent/memory/feedback/draft",
            json={
                "outcome": "wrong_decision",
                "prompt": "stage all git changes",
                "model_name": "qwen-test",
                "llm_model": "feedback-live-model",
                "llm_base_url": f"http://127.0.0.1:{server.server_port}/v1",
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status_code == 200
    assert response.json()["feedback"] == "The agent should verify staged files directly next time."
    assert [call["path"] for call in calls] == ["/v1/chat/completions"]
    assert calls[0]["body"]["model"] == "feedback-live-model"


def test_agent_ui_request_accepts_llm_operator_mode(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = FakeAgentRuntime()

    def offline_urlopen(*args: Any, **kwargs: Any) -> None:
        raise OSError("model API unavailable in this test")

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", offline_urlopen)
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo", llm_context_window_tokens=12345),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "operator test", "agent_mode": "llm_operator"},
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    assert runtime.last_context["agent_mode"] == "llm_operator"
    assert runtime.last_context["llm_context_window_tokens"] == 12345


def test_agent_ui_advisory_mode_returns_structured_snippets(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = FakeAdvisoryRuntime()

    def offline_urlopen(*args: Any, **kwargs: Any) -> None:
        raise OSError("model API unavailable in this test")

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", offline_urlopen)
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_gateways_db_path=tmp_path / "gateways.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_command_allowlist_db_path=tmp_path / "command_allowlist.db",
                agent_ui_settings_db_path=tmp_path / "settings.db",
                agent_prompts_db_path=tmp_path / "prompts.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "How can I inspect clipboard text on the Mac?",
            "agent_mode": "advisory",
            "context": {
                "gateway_platform": "macos",
                "gateway_platform_label": "macOS",
                "gateway_shell": "/bin/bash",
                "gateway_command_profile": "posix-bash-macos",
                "terminal_cwd": "/Users/vinith/project",
                "advisory_terminal_context_enabled": True,
                "advisory_terminal_output": "git status shows one modified file in src/agent_runtime/api/static/agent_ui/app.js",
            },
        },
    )

    assert submitted.status_code == 200
    payload = submitted.json()
    with client.stream("GET", payload["stream_url"]) as response:
        _ = response.read()

    assert runtime.handle_calls == 0
    assert runtime.llm_client.schemas[-1]["title"] == "AdvisoryResponse"
    prompt = runtime.llm_client.prompts[-1]
    assert "OpenFabric Advisory mode" in prompt
    assert "macOS" in prompt
    assert "/bin/bash" in prompt
    assert "/Users/vinith/project" in prompt
    assert "advisory_terminal_output" in prompt
    assert "git status shows one modified file" in prompt
    assert "How can I inspect clipboard text on the Mac?" in prompt

    trace = client.get(payload["trace_url"]).json()
    assert trace["status"] == "completed"
    assert trace["final_response"] == "Use the macOS clipboard tools from the terminal."
    metadata = trace["display_document"]["metadata"]
    assert metadata["advisory"] is True
    assert metadata["snippets"][0]["runnable"] is True
    assert metadata["snippets"][0]["code"] == "pbpaste | sed -n '1,20p'"
    assert metadata["snippets"][1]["runnable"] is False


def test_agent_ui_advisory_followup_includes_prior_conversation_context(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = FakeAdvisoryRuntime()

    def offline_urlopen(*args: Any, **kwargs: Any) -> None:
        raise OSError("model API unavailable in this test")

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", offline_urlopen)
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_gateways_db_path=tmp_path / "gateways.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_command_allowlist_db_path=tmp_path / "command_allowlist.db",
                agent_ui_settings_db_path=tmp_path / "settings.db",
                agent_prompts_db_path=tmp_path / "prompts.db",
            ),
            agent_runtime=runtime,
        )
    )

    first = client.post(
        "/api/agent/request",
        json={"prompt": "How do I use pbpaste?", "agent_mode": "advisory"},
    ).json()
    with client.stream("GET", first["stream_url"]) as response:
        _ = response.read()

    second = client.post(
        "/api/agent/request",
        json={
            "prompt": "Can I run that from any folder?",
            "agent_mode": "advisory",
            "conversation_id": first["conversation_id"],
        },
    ).json()
    with client.stream("GET", second["stream_url"]) as response:
        _ = response.read()

    prompt = runtime.llm_client.prompts[-1]
    assert "How do I use pbpaste?" in prompt
    assert "Use the macOS clipboard tools from the terminal." in prompt
    assert "Can I run that from any folder?" in prompt


def test_agent_ui_request_uses_live_model_context_window(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    runtime = CountingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                llm_context_window_tokens=32768,
                llm_base_url="http://10.0.0.12:8123/v1",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
            ),
            agent_runtime=runtime,
        )
    )
    calls: list[dict[str, Any]] = []

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"data":[{"id":"nemotron-awq","max_model_len":8192}]}'

    def fake_urlopen(request, timeout: float):  # type: ignore[no-untyped-def]
        calls.append({"url": request.full_url, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "operator test", "agent_mode": "llm_operator"},
    )
    payload = submitted.json()
    with client.stream("GET", payload["stream_url"]) as response:
        _ = response.read()

    assert payload["llm_context_window_tokens"] == 8192
    assert payload["llm_context_window_source"] == "model_api"
    assert runtime.last_context["llm_context_window_tokens"] == 8192
    assert calls == [{"url": "http://10.0.0.12:8123/v1/models", "timeout": 1.2}]


def test_agent_ui_request_accepts_available_llm_model_selection() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                default_model="configured-model",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "list files", "llm_model": "configured-model"},
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    assert runtime.last_context["llm_model"] == "configured-model"
    assert submitted.json()["llm_model"] == "configured-model"


def test_agent_ui_request_accepts_operator_settings_context() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )
    controls = client.post(
        "/api/agent/runtime-controls",
        json={
            "operator_policy_profile": "assisted",
            "reasoning_profile": "deep",
            "repair_profile": "aggressive",
            "workflow_execution_mode": "streaming",
            "prompt_rephrase_enabled": False,
            "response_streaming_enabled": True,
            "operator_workspace_cwd_guard_enabled": True,
            "llm_operator_verbose_enabled": False,
            "llm_operator_step_validation_enabled": True,
        },
    )
    assert controls.status_code == 200

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "list files",
            "context": {
                "llm_operator_verbose_enabled": True,
                "llm_operator_plan_review_enabled": False,
                "agent_display_name": "Coda",
                "llm_operator_answer_judge_enabled": True,
                "llm_operator_formatter_source_preview_chars": 5000,
                "llm_operator_max_validation_repair_attempts": 3,
                "llm_operator_max_deferred_code_repair_attempts": 4,
                "llm_operator_max_answer_judge_repair_attempts": 5,
                "llm_operator_max_execution_repair_attempts": 6,
                "llm_operator_max_completion_repair_attempts": 7,
                "llm_operator_repair_confidence_threshold": 0.42,
                "llm_operator_verification_enforced": False,
                "llm_operator_clarification_strategy": "material_gaps",
                "llm_operator_final_response_mode": "simple",
                "llm_operator_max_clarification_rounds": 8,
                "operator_workspace_cwd_guard_enabled": True,
                "shell_input_bindings_mode": "allow",
                "operator_effect_policy_mode": "llm",
                "operator_interaction_policy_mode": "llm",
                "operator_stdout_policy_mode": "llm",
                "operator_failure_policy_mode": "llm",
                "operator_memory_policy_mode": "llm",
                "operator_memory_question_policy_mode": "llm",
                "operator_streaming_scope_policy_mode": "llm",
                "operator_verb_policy_mode": "llm",
                "operator_python_code_review_policy_mode": "llm",
                "llm_operator_step_validation_enabled": True,
                "prompt_rephrase_enabled": True,
                "llm_response_streaming_enabled": True,
                "operator_auto_rephrase_retry_enabled": False,
                "operator_online_mode_enabled": True,
                "llm_operator_self_brief_mode": "off",
                "llm_operator_self_brief_max_tokens": 1024,
                "agent_memory_enabled": False,
                "agent_memory_prompt_max_chars": 2345,
                "agent_plan_cache_enabled": False,
                "agent_plan_cache_prompt_max_chars": 3456,
                "agent_plan_cache_similarity_threshold": 0.71,
                "agent_plan_cache_max_entries": 4321,
                "agent_computation_cache_enabled": False,
                "agent_computation_cache_prompt_max_chars": 4567,
                "agent_computation_cache_similarity_threshold": 0.62,
                "agent_computation_cache_max_entries": 5432,
                "llm_base_scheme": "http",
                "llm_base_host": "127.0.0.2",
                "llm_base_port": 8999,
                "llm_base_path": "v1",
                "llm_timeout_seconds": 240,
                "llm_max_tokens": 4096,
                "ignored": "not forwarded",
            },
        },
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    assert runtime.last_context["llm_operator_verbose_enabled"] is False
    assert "llm_operator_plan_review_enabled" not in runtime.last_context
    assert runtime.last_context["agent_display_name"] == "Coda"
    assert "llm_operator_answer_judge_enabled" not in runtime.last_context
    assert "llm_operator_formatter_source_preview_chars" not in runtime.last_context
    assert "llm_operator_max_validation_repair_attempts" not in runtime.last_context
    assert "llm_operator_max_deferred_code_repair_attempts" not in runtime.last_context
    assert "llm_operator_max_answer_judge_repair_attempts" not in runtime.last_context
    assert "llm_operator_max_execution_repair_attempts" not in runtime.last_context
    assert "llm_operator_max_completion_repair_attempts" not in runtime.last_context
    assert "llm_operator_repair_confidence_threshold" not in runtime.last_context
    assert runtime.last_context["llm_operator_verification_enforced"] is True
    assert runtime.last_context["agent_clarification_mode"] == "balanced"
    assert runtime.last_context["llm_operator_final_response_mode"] == "detailed"
    assert "guided_deliberation_mode" not in runtime.last_context
    assert runtime.last_context["llm_operator_max_clarification_rounds"] == 3
    assert runtime.last_context["operator_workspace_cwd_guard_enabled"] is True
    assert runtime.last_context["shell_input_bindings_mode"] == "allow"
    assert runtime.last_context["operator_policy_profile"] == "assisted"
    assert runtime.last_context["reasoning_profile"] == "deep"
    assert runtime.last_context["repair_profile"] == "aggressive"
    assert runtime.last_context["workflow_execution_mode"] == "streaming"
    assert "operator_effect_policy_mode" not in runtime.last_context
    assert "operator_interaction_policy_mode" not in runtime.last_context
    assert "operator_stdout_policy_mode" not in runtime.last_context
    assert "operator_failure_policy_mode" not in runtime.last_context
    assert "operator_memory_policy_mode" not in runtime.last_context
    assert "operator_memory_question_policy_mode" not in runtime.last_context
    assert "operator_streaming_scope_policy_mode" not in runtime.last_context
    assert "operator_verb_policy_mode" not in runtime.last_context
    assert "operator_python_code_review_policy_mode" not in runtime.last_context
    assert runtime.last_context["llm_operator_step_validation_enabled"] is True
    assert runtime.last_context["prompt_rephrase_enabled"] is False
    assert runtime.last_context["response_streaming_enabled"] is True
    assert "llm_response_streaming_enabled" not in runtime.last_context
    assert "operator_auto_rephrase_retry_enabled" not in runtime.last_context
    assert "operator_online_mode_enabled" not in runtime.last_context
    assert "llm_operator_self_brief_mode" not in runtime.last_context
    assert "llm_operator_self_brief_max_tokens" not in runtime.last_context
    assert runtime.last_context["agent_memory_enabled"] is True
    assert runtime.last_context["agent_memory_prompt_max_chars"] == 3000
    assert "agent_plan_cache_enabled" not in runtime.last_context
    assert "agent_computation_cache_enabled" not in runtime.last_context
    assert runtime.last_context["llm_base_host"] != "127.0.0.2"
    assert runtime.last_context["llm_base_port"] != 8999
    assert runtime.last_context["llm_base_url"] != "http://127.0.0.2:8999/v1"
    assert runtime.last_context["llm_base_host"] == "127.0.0.1"
    assert runtime.last_context["llm_base_port"] == 8000
    assert runtime.last_context["llm_base_path"] == "/v1"
    assert runtime.last_context["llm_base_url"] == "http://127.0.0.1:8000/v1"
    assert runtime.last_context["llm_timeout_seconds"] == 120
    assert runtime.last_context["llm_max_tokens"] == 0
    assert "ignored" in runtime.last_context


def test_agent_ui_request_runtime_context_does_not_override_backend_settings(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_settings_db_path=tmp_path / "agent_ui_settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "list files",
            "context": {
                "guided_deliberation_mode": "always",
                "operator_execution_mode": "streaming",
            },
        },
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    assert "guided_deliberation_mode" not in runtime.last_context
    assert "operator_execution_mode" not in runtime.last_context
    assert runtime.last_context["workflow_execution_mode"] == "streaming"


def test_agent_ui_request_rejects_unknown_llm_model_selection() -> None:
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                default_model="configured-model",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    response = client.post(
        "/api/agent/request",
        json={"prompt": "list files", "llm_model": "not-configured"},
    )

    assert response.status_code == 400
    assert "not available" in response.json()["detail"]


def test_agent_ui_terminal_config_and_cwd_context_are_trusted() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    config = client.get("/api/agent/terminal/config")
    assert config.status_code == 200
    terminal_payload = config.json()
    assert terminal_payload["session_id"].startswith("term-")
    assert terminal_payload["websocket_url"].startswith("ws://")
    assert "/api/agent/terminal/ws?" in terminal_payload["websocket_url"]
    assert "/terminal/ws?" in terminal_payload["gateway_websocket_url"]

    updated = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": terminal_payload["session_id"], "cwd": "/tmp"},
    )
    assert updated.status_code == 200

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "pwd",
            "context": {
                "terminal_session_id": terminal_payload["session_id"],
                "terminal_cwd": "/tmp",
                "execute_in_terminal": True,
            },
        },
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    assert runtime.last_context["terminal_session_id"] == terminal_payload["session_id"]
    assert runtime.last_context["terminal_cwd"] == "/tmp"
    assert runtime.last_context["execute_in_terminal"] is True


def test_agent_ui_terminal_websocket_uses_backend_proxy(monkeypatch) -> None:
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    config = client.get("/api/agent/terminal/config").json()
    target = urlsplit(config["websocket_url"])
    proxy_path = f"{target.path}?{target.query}"
    captured: dict[str, str] = {}

    class FakeGatewaySocket:
        def __init__(self) -> None:
            self.messages = [
                json.dumps({"type": "cwd", "cwd": "/tmp"}),
                json.dumps({"type": "output", "data": "ready"}),
            ]

        async def __aenter__(self) -> "FakeGatewaySocket":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def send(self, _data: str) -> None:
            return None

        def __aiter__(self) -> "FakeGatewaySocket":
            return self

        async def __anext__(self) -> str:
            if not self.messages:
                raise StopAsyncIteration
            return self.messages.pop(0)

    def fake_connect(url: str) -> FakeGatewaySocket:
        captured["url"] = url
        return FakeGatewaySocket()

    monkeypatch.setattr(agent_ui.websockets, "connect", fake_connect)

    with client.websocket_connect(proxy_path) as websocket:
        assert websocket.receive_json() == {"type": "cwd", "cwd": "/tmp"}
        assert websocket.receive_json() == {"type": "output", "data": "ready"}

    assert captured["url"].startswith("ws://127.0.0.1:8787/terminal/ws?")
    assert "node=localhost" in captured["url"]
    assert f"session_id={config['session_id']}" in captured["url"]
    assert "gateway_id=" not in captured["url"]


def test_agent_ui_gateway_registry_crud_and_selection_context(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
                agent_gateways_db_path=tmp_path / "gateways.db",
                default_node="worker-a",
                available_nodes_raw="worker-a",
                gateway_endpoints={"worker-a": "http://127.0.0.1:8787"},
            ),
            agent_runtime=runtime,
        )
    )

    listed = client.get("/api/agent/gateways")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["default_gateway_node"] == "worker-a"
    assert payload["gateways"][0]["node"] == "worker-a"
    assert payload["gateways"][0]["label"] == "worker-a"
    assert payload["gateways"][0]["base_url"] == "http://127.0.0.1:8787"
    assert payload["gateways"][0]["terminal_cwd"] == "/tmp"

    missing_nickname = client.post(
        "/api/agent/gateways",
        json={"scheme": "http", "host": "10.0.0.6", "port": 8788, "node": "missing"},
    )
    assert missing_nickname.status_code == 422

    ip_nickname = client.post(
        "/api/agent/gateways",
        json={"label": "10.0.0.6", "scheme": "http", "host": "10.0.0.6", "port": 8788, "node": "ip-name"},
    )
    assert ip_nickname.status_code == 400
    assert "nickname cannot be an IP address" in ip_nickname.text

    url_nickname = client.post(
        "/api/agent/gateways",
        json={"label": "http://bench", "scheme": "http", "host": "10.0.0.6", "port": 8788, "node": "url-name"},
    )
    assert url_nickname.status_code == 400
    assert "nickname cannot be a URL" in url_nickname.text

    created = client.post(
        "/api/agent/gateways",
        json={
            "label": "Bench",
            "scheme": "http",
            "host": "10.0.0.5",
            "port": 8788,
            "node": "bench",
            "terminal_cwd": str(tmp_path / "bench"),
        },
    )
    assert created.status_code == 200
    created_gateway = created.json()["gateway"]
    assert created_gateway["label"] == "Bench"
    assert created_gateway["base_url"] == "http://10.0.0.5:8788"
    assert created_gateway["terminal_cwd"] == str(tmp_path / "bench")

    updated = client.patch(
        f"/api/agent/gateways/{created_gateway['gateway_id']}",
        json={"label": "Bench GPU", "terminal_cwd": str(tmp_path), "enabled": True},
    )
    assert updated.status_code == 200
    assert updated.json()["gateway"]["label"] == "Bench GPU"
    assert updated.json()["gateway"]["terminal_cwd"] == str(tmp_path)

    duplicate = client.post(
        "/api/agent/gateways",
        json={
            "label": "bench gpu",
            "scheme": "http",
            "host": "10.0.0.7",
            "port": 8789,
            "node": "duplicate",
        },
    )
    assert duplicate.status_code == 400
    assert "nickname must be unique" in duplicate.text

    blank_update = client.patch(
        f"/api/agent/gateways/{created_gateway['gateway_id']}",
        json={"label": "   "},
    )
    assert blank_update.status_code == 400
    assert "nickname is required" in blank_update.text

    terminal = client.get(
        "/api/agent/terminal/config",
        params={"gateway_id": created_gateway["gateway_id"]},
    )
    assert terminal.status_code == 200
    terminal_payload = terminal.json()
    assert terminal_payload["initial_cwd"] == str(tmp_path)
    assert "/api/agent/terminal/ws?" in terminal_payload["websocket_url"]
    assert f"gateway_id={created_gateway['gateway_id']}" in terminal_payload["websocket_url"]
    assert "/terminal/ws?" in terminal_payload["gateway_websocket_url"]

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "pwd",
            "context": {"gateway_id": created_gateway["gateway_id"]},
        },
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    assert runtime.last_context["gateway_id"] == created_gateway["gateway_id"]
    assert runtime.last_context["gateway_node"] == "bench"
    assert runtime.last_context["gateway_url"] == "http://10.0.0.5:8788"
    assert runtime.last_context["gateway_endpoints"] == {"bench": "http://10.0.0.5:8788"}
    assert runtime.last_context["gateway_default_cwd"] == str(tmp_path)
    assert runtime.last_context["gateway_routing"]["mode"] == "selected"
    assert runtime.last_context["gateway_routing"]["gateway_nickname"] == "Bench GPU"

    deleted = client.delete(f"/api/agent/gateways/{created_gateway['gateway_id']}")
    assert deleted.status_code == 200


def test_agent_ui_default_gateway_cwd_is_request_execution_cwd(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    db_path = tmp_path / "gateways.db"
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path / "container-data",
                agent_gateways_db_path=db_path,
                default_node="worker-a",
                available_nodes_raw="worker-a",
                gateway_endpoints={"worker-a": "http://127.0.0.1:8787"},
            ),
            agent_runtime=runtime,
        )
    )
    gateway = client.get("/api/agent/gateways").json()["gateways"][0]
    host_cwd = tmp_path / "host-workspace"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE gateways SET terminal_cwd = ? WHERE gateway_id = ?",
            (str(host_cwd), gateway["gateway_id"]),
        )

    submitted = client.post("/api/agent/request", json={"prompt": "pwd"})
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    assert runtime.last_context["gateway_id"] == gateway["gateway_id"]
    assert runtime.last_context["gateway_node"] == "worker-a"
    assert runtime.last_context["gateway_default_cwd"] == str(host_cwd)
    assert runtime.last_context["gateway_routing"]["mode"] == "default"


def test_agent_ui_gateway_startup_repairs_invalid_nicknames(tmp_path) -> None:
    db_path = tmp_path / "gateways.db"
    store = AgentGatewayStore(db_path)
    record = store.create(
        label="Bench",
        scheme="http",
        host="10.0.0.5",
        port=8788,
        node="bench",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE gateways SET label = ? WHERE gateway_id = ?",
            ("127.0.0.1", record.gateway_id),
        )

    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
                agent_gateways_db_path=db_path,
                default_node="worker-a",
                available_nodes_raw="worker-a",
                gateway_endpoints={"worker-a": "http://127.0.0.1:8787"},
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    gateways = client.get("/api/agent/gateways").json()["gateways"]
    repaired = next(gateway for gateway in gateways if gateway["gateway_id"] == record.gateway_id)
    assert repaired["label"] == "bench"
    seeded = next(gateway for gateway in gateways if gateway["node"] == "worker-a")
    assert seeded["label"] == "worker-a"


def test_gateway_store_migrates_platform_metadata_columns(tmp_path) -> None:
    db_path = tmp_path / "gateways.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE gateways (
                gateway_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                scheme TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                base_url TEXT NOT NULL,
                node TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'unknown',
                last_checked_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'user'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO gateways (
                gateway_id, label, scheme, host, port, base_url, node,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "gw-old",
                "old-gateway",
                "http",
                "127.0.0.1",
                8787,
                "http://127.0.0.1:8787",
                "old-node",
                "2026-05-20T00:00:00Z",
                "2026-05-20T00:00:00Z",
            ),
        )

    store = AgentGatewayStore(db_path)
    record = store.get("gw-old")

    assert record is not None
    assert record.terminal_cwd == ""
    assert record.platform == "unknown"
    assert record.platform_label == "Unknown"
    assert record.capability_tags == []


def test_gateway_store_health_check_discovers_macos_metadata(tmp_path, monkeypatch) -> None:
    store = AgentGatewayStore(tmp_path / "gateways.db")
    record = store.create(
        label="Bench Mac",
        scheme="http",
        host="10.0.0.9",
        port=8787,
        node="bench-mac",
    )

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        assert request.full_url == "http://10.0.0.9:8787/capabilities"
        assert timeout >= 0.25
        return _GatewayDiscoveryResponse(
            {
                "node": "mac-node",
                "version": "0.4.0",
                "platform": "macos",
                "platform_label": "macOS",
                "platform_version": "15.5",
                "architecture": "arm64",
                "shell": "/bin/bash",
                "command_profile": "posix-bash-macos",
                "capability_tags": ["exec", "python", "terminal", "posix_shell", "macos_cli"],
                "capabilities": [],
            }
        )

    monkeypatch.setattr(gateway_store_module.urllib_request, "urlopen", fake_urlopen)

    checked = store.health_check(record.gateway_id)

    assert checked is not None
    assert checked.status == "connected"
    assert checked.node == "mac-node"
    assert checked.platform == "macos"
    assert checked.platform_label == "macOS"
    assert checked.platform_version == "15.5"
    assert checked.architecture == "arm64"
    assert checked.shell == "/bin/bash"
    assert checked.command_profile == "posix-bash-macos"
    assert "macos_cli" in checked.capability_tags


def test_gateway_store_health_check_falls_back_for_legacy_gateways(tmp_path, monkeypatch) -> None:
    store = AgentGatewayStore(tmp_path / "gateways.db")
    record = store.create(
        label="Legacy",
        scheme="http",
        host="10.0.0.10",
        port=8787,
        node="legacy",
    )
    urls: list[str] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        _ = timeout
        urls.append(request.full_url)
        if request.full_url.endswith("/capabilities"):
            raise urllib_error.HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)
        return _GatewayDiscoveryResponse({"status": "ok", "node": "legacy-node"})

    monkeypatch.setattr(gateway_store_module.urllib_request, "urlopen", fake_urlopen)

    checked = store.health_check(record.gateway_id)

    assert checked is not None
    assert urls == [
        "http://10.0.0.10:8787/capabilities",
        "http://10.0.0.10:8787/healthz",
    ]
    assert checked.status == "connected"
    assert checked.node == "legacy-node"
    assert checked.platform == "unknown"
    assert checked.platform_label == "Unknown"
    assert checked.capability_tags == []


def test_agent_ui_auto_gateway_routing_by_nickname(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    db_path = tmp_path / "gateways.db"
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
                agent_gateways_db_path=db_path,
                default_node="worker-a",
                available_nodes_raw="worker-a,worker-b",
                gateway_endpoints={
                    "worker-a": "http://127.0.0.1:8787",
                    "worker-b": "http://127.0.0.1:8788",
                },
            ),
            agent_runtime=runtime,
        )
    )
    gateways = client.get("/api/agent/gateways").json()["gateways"]
    gateway_a = next(gateway for gateway in gateways if gateway["node"] == "worker-a")
    gateway_b = next(gateway for gateway in gateways if gateway["node"] == "worker-b")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE gateways
            SET platform = ?, platform_label = ?, platform_version = ?, architecture = ?,
                shell = ?, command_profile = ?, capability_tags_json = ?
            WHERE gateway_id = ?
            """,
            (
                "macos",
                "macOS",
                "15.5",
                "arm64",
                "/bin/bash",
                "posix-bash-macos",
                json.dumps(["exec", "terminal", "macos_cli"]),
                gateway_b["gateway_id"],
            ),
        )
    gateways = client.get("/api/agent/gateways").json()["gateways"]
    gateway_b = next(gateway for gateway in gateways if gateway["node"] == "worker-b")
    assert gateway_b["platform"] == "macos"
    assert gateway_b["platform_label"] == "macOS"

    routed = client.post(
        "/api/agent/request",
        json={
            "prompt": "@worker-b run pwd",
            "context": {"gateway_id": gateway_a["gateway_id"]},
        },
    )
    assert routed.status_code == 200
    routed_payload = routed.json()
    assert routed_payload["gateway_routing"]["mode"] == "nickname"
    assert routed_payload["gateway_routing"]["gateway_nickname"] == "worker-b"
    assert routed_payload["gateway_routing"]["matched_text"] == "worker-b"
    assert routed_payload["gateway_routing"]["gateway_platform"] == "macos"
    assert routed_payload["gateway_routing"]["gateway_platform_label"] == "macOS"
    with client.stream("GET", routed_payload["stream_url"]) as response:
        _ = response.read()
    assert runtime.last_context["gateway_id"] == gateway_b["gateway_id"]
    assert runtime.last_context["gateway_node"] == "worker-b"
    assert runtime.last_context["gateway_url"] == "http://127.0.0.1:8788"
    assert runtime.last_context["gateway_platform"] == "macos"
    assert runtime.last_context["gateway_platform_label"] == "macOS"
    assert runtime.last_context["gateway_shell"] == "/bin/bash"
    assert runtime.last_context["gateway_command_profile"] == "posix-bash-macos"
    assert runtime.last_context["gateway_capability_tags"] == ["exec", "terminal", "macos_cli"]
    assert runtime.last_context["gateway_routing"]["mode"] == "nickname"

    trace = client.get(routed_payload["trace_url"]).json()
    routing_events = [
        event
        for event in trace["events"]
        if event["event_type"] == "agent.gateway.routing"
    ]
    assert routing_events
    assert routing_events[0]["detail"]["gateway_nickname"] == "worker-b"
    assert routing_events[0]["detail"]["gateway_platform_label"] == "macOS"

    selected = client.post(
        "/api/agent/request",
        json={
            "prompt": "run pwd without a nickname",
            "context": {"gateway_id": gateway_a["gateway_id"]},
        },
    )
    assert selected.status_code == 200
    selected_payload = selected.json()
    assert selected_payload["gateway_routing"]["mode"] == "selected"
    with client.stream("GET", selected_payload["stream_url"]) as response:
        _ = response.read()
    assert runtime.last_context["gateway_id"] == gateway_a["gateway_id"]
    assert runtime.last_context["gateway_routing"]["mode"] == "selected"

    ambiguous = client.post(
        "/api/agent/request",
        json={"prompt": "compare worker-a and worker-b", "context": {"gateway_id": gateway_a["gateway_id"]}},
    )
    assert ambiguous.status_code == 400
    assert "Multiple gateway nicknames were mentioned" in ambiguous.text

    disabled_update = client.patch(
        f"/api/agent/gateways/{gateway_b['gateway_id']}",
        json={"enabled": False},
    )
    assert disabled_update.status_code == 200
    disabled = client.post(
        "/api/agent/request",
        json={"prompt": "worker-b run pwd", "context": {"gateway_id": gateway_a["gateway_id"]}},
    )
    assert disabled.status_code == 400
    assert "Gateway nickname is disabled" in disabled.text


def test_agent_ui_confirmation_actions_include_gateway_metadata(tmp_path) -> None:
    class GatewayConfirmationRuntime:
        def __init__(self) -> None:
            self.last_failure_summary: dict[str, Any] | None = None
            self.last_planning_trace: Any = None
            self.last_display_document = None

        def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
            _ = raw_prompt
            self.last_failure_summary = {"category": "confirmation_required"}
            self.last_planning_trace = SimpleNamespace(
                metadata={
                    "operator_confirmation_actions": [
                        {
                            "action_id": "action_1",
                            "kind": "shell_command",
                            "command": "pwd",
                            "cwd": "/tmp",
                            "arguments": {},
                        },
                        {
                            "action_id": "action_2",
                            "kind": "shell_command",
                            "command": "hostname",
                            "gateway_node": "override-node",
                            "arguments": {},
                        },
                    ]
                }
            )
            return "## Confirmation Required\n\nExecution requires confirmation before proceeding."

    db_path = tmp_path / "gateways.db"
    store = AgentGatewayStore(db_path)
    record = store.create(
        label="Bench GPU",
        scheme="http",
        host="10.0.0.5",
        port=8788,
        node="bench",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE gateways SET platform = ?, platform_label = ? WHERE gateway_id = ?",
            ("linux", "Linux", record.gateway_id),
        )
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
                agent_gateways_db_path=db_path,
            ),
            agent_runtime=GatewayConfirmationRuntime(),
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "run two commands", "context": {"gateway_id": record.gateway_id}},
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()
    trace = client.get(submitted["trace_url"]).json()

    first, second = trace["confirmation_actions"]
    assert first["gateway_display_name"] == "Bench GPU (bench)"
    assert first["gateway_nickname"] == "Bench GPU"
    assert first["gateway_node"] == "bench"
    assert first["gateway_platform_label"] == "Linux"
    assert first["arguments"]["gateway_routing"]["gateway_display_name"] == "Bench GPU (bench)"
    assert second["gateway_display_name"] == "override-node"
    assert second["gateway_node"] == "override-node"


def test_agent_ui_terminal_sessions_are_gateway_scoped(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
                agent_gateways_db_path=tmp_path / "gateways.db",
                default_node="worker-a",
                available_nodes_raw="worker-a,worker-b",
                gateway_endpoints={
                    "worker-a": "http://127.0.0.1:8787",
                    "worker-b": "http://127.0.0.1:8788",
                },
            ),
            agent_runtime=runtime,
        )
    )
    gateways = client.get("/api/agent/gateways").json()["gateways"]
    gateway_a = next(gateway for gateway in gateways if gateway["node"] == "worker-a")
    gateway_b = next(gateway for gateway in gateways if gateway["node"] == "worker-b")

    terminal_a = client.get(
        "/api/agent/terminal/config",
        params={"gateway_id": gateway_a["gateway_id"]},
    ).json()
    assert terminal_a["node"] == "worker-a"
    assert terminal_a["gateway_id"] == gateway_a["gateway_id"]

    updated = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": terminal_a["session_id"], "cwd": str(tmp_path)},
    )
    assert updated.status_code == 200

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "pwd",
            "context": {
                "gateway_id": gateway_b["gateway_id"],
                "terminal_session_id": terminal_a["session_id"],
                "terminal_cwd": str(tmp_path),
                "execute_in_terminal": True,
            },
        },
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    assert runtime.last_context["gateway_node"] == "worker-b"
    assert runtime.last_context["gateway_url"] == "http://127.0.0.1:8788"
    assert "terminal_session_id" not in runtime.last_context
    assert "terminal_cwd" not in runtime.last_context
    assert "execute_in_terminal" not in runtime.last_context

    terminal_b = client.get(
        "/api/agent/terminal/config",
        params={"gateway_id": gateway_b["gateway_id"]},
    ).json()
    assert terminal_b["node"] == "worker-b"
    updated_b = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": terminal_b["session_id"], "cwd": str(tmp_path)},
    )
    assert updated_b.status_code == 200

    routed_terminal = client.post(
        "/api/agent/request",
        json={
            "prompt": "@worker-b pwd",
            "context": {
                "gateway_id": gateway_a["gateway_id"],
                "terminal_session_id": terminal_b["session_id"],
                "terminal_cwd": str(tmp_path),
                "execute_in_terminal": True,
            },
        },
    )
    with client.stream("GET", routed_terminal.json()["stream_url"]) as response:
        _ = response.read()

    assert runtime.last_context["gateway_node"] == "worker-b"
    assert runtime.last_context["terminal_session_id"] == terminal_b["session_id"]
    assert runtime.last_context["terminal_cwd"] == str(tmp_path)
    assert runtime.last_context["execute_in_terminal"] is True


def test_agent_ui_rejects_untrusted_terminal_context() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "pwd",
            "context": {
                "terminal_session_id": "term-missing",
                "terminal_cwd": "/tmp",
                "execute_in_terminal": True,
            },
        },
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    assert "terminal_session_id" not in runtime.last_context
    assert "terminal_cwd" not in runtime.last_context
    assert "execute_in_terminal" not in runtime.last_context


def test_agent_ui_operator_request_returns_conversation_id() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "operator test", "agent_mode": "llm_operator"},
    )
    payload = submitted.json()
    with client.stream("GET", payload["stream_url"]) as response:
        _ = response.read()

    assert payload["conversation_id"].startswith("conv-")
    assert runtime.last_context["conversation_id"] == payload["conversation_id"]


def test_agent_conversation_store_persists_chat_history(tmp_path) -> None:
    db_path = tmp_path / "chats.db"
    store = AgentConversationStore(db_path)
    conversation_id = store.create("first prompt")

    store.upsert_turn(
        conversation_id,
        request_id="req-one",
        prompt="first prompt",
        final_response="first response",
        status="completed",
        display_document={"sections": [{"title": "Rows", "rows": [{"x": 1}]}]},
        response_metrics={
            "input_tokens_estimate": 120,
            "output_tokens_estimate": 30,
            "total_tokens_estimate": 150,
            "duration_seconds": 1.25,
        },
    )

    reopened = AgentConversationStore(db_path)
    chats = reopened.list()
    detail = reopened.get(conversation_id)

    assert chats[0].conversation_id == conversation_id
    assert chats[0].title == "first prompt"
    assert chats[0].last_response_metrics["duration_seconds"] == 1.25
    assert detail is not None
    assert detail.turns[0].final_response == "first response"
    assert detail.turns[0].display_document["sections"][0]["rows"][0]["x"] == 1
    assert detail.turns[0].response_metrics["input_tokens_estimate"] == 120
    assert reopened.get_turn("req-one").final_response == "first response"

    assert reopened.delete(conversation_id) is True
    assert reopened.get(conversation_id) is None
    assert reopened.get_turn("req-one") is None


def test_agent_conversation_store_persists_learning_summary_from_metadata(tmp_path) -> None:
    db_path = tmp_path / "chats.db"
    conversation_store = AgentConversationStore(db_path)
    conversation_id = conversation_store.create("commit staged files")
    trace_store = AgentTraceStore()
    trace = trace_store.create_request("commit staged files")
    trace_store.append_event(
        AgentTraceEvent(
            request_id=trace.request_id,
            stage="execution",
            event_type="execution.command.completed",
            title="Command completed",
            summary="Done: Reused LR-EX payload-aware command template cmdtpl-7482e031eefb.",
            detail={
                "metadata": {
                    "operation_description": "Reused LR-EX payload-aware command template cmdtpl-7482e031eefb.",
                    "command_template_id": "cmdtpl-7482e031eefb",
                    "cache_type": "payload_command_template",
                }
            },
        )
    )
    trace_store.complete_request(trace.request_id, "committed")

    conversation_store.append_turn(conversation_id, trace_store.get_trace(trace.request_id))
    reopened = AgentConversationStore(db_path)
    turn = reopened.get_turn(trace.request_id)

    assert turn is not None
    assert turn.learning_summary is not None
    assert turn.learning_summary["counts"]["applied"] == 1
    assert turn.learning_summary["event_count"] == 1
    row = turn.learning_summary["rows"][0]
    assert row["label"] == "LR-EX payload-aware command template"
    assert row["ids"] == ["cmdtpl-7482e031eefb"]
    assert row["benefited_steps"] == "command preparation; validation"


def test_agent_conversation_store_persists_grouped_learning_summary_rows(tmp_path) -> None:
    db_path = tmp_path / "chats.db"
    conversation_store = AgentConversationStore(db_path)
    conversation_id = conversation_store.create("use learning summary rows")
    trace_store = AgentTraceStore()
    trace = trace_store.create_request("use learning summary rows")
    trace_store.append_event(
        AgentTraceEvent(
            request_id=trace.request_id,
            stage="decomposition",
            event_type="operator.lrt.lookup",
            title="LR-T lookup",
            summary="Checked learned total-task structures.",
            detail={
                "candidate_count": 0,
                "rejected_candidate_count": 3,
                "best_rejected_score": 0.80,
                "similarity_threshold": 0.92,
            },
        )
    )
    trace_store.append_event(
        AgentTraceEvent(
            request_id=trace.request_id,
            stage="execution",
            event_type="operator.lrdirect.hit",
            title="LR Direct hit",
            summary="Replayed exact step.",
            detail={"exact_step_key": "exact-step-1"},
        )
    )
    trace_store.append_event(
        AgentTraceEvent(
            request_id=trace.request_id,
            stage="learning",
            event_type="operator.lrnt.write",
            title="LRN write",
            summary="Saved reusable task actions.",
            detail={"entry_id": "lrnt-entry-1"},
        )
    )
    trace_store.complete_request(trace.request_id, "done")

    conversation_store.append_turn(conversation_id, trace_store.get_trace(trace.request_id))
    turn = AgentConversationStore(db_path).get_turn(trace.request_id)

    assert turn is not None
    assert turn.learning_summary is not None
    rows = {row["label"]: row for row in turn.learning_summary["rows"]}
    assert turn.learning_summary["counts"]["not_applied"] == 1
    assert turn.learning_summary["counts"]["applied"] == 1
    assert turn.learning_summary["counts"]["learned"] == 1
    assert rows["LR-T not applied"]["learned"] == "3 candidates checked; best 80% was below the 92% threshold."
    assert rows["LR-D applied"]["learned"] == "Replayed exact-step cache for this execution step."
    assert rows["LRN learned"]["learned"] == "Saved reusable task/step actions for future runs."
    assert "LR-T lookup" not in rows


def test_learned_command_failure_notice_appends_for_failed_learned_command() -> None:
    trace_store = AgentTraceStore()
    trace = trace_store.create_request("create cleanup branch")
    trace_store.append_event(
        AgentTraceEvent(
            request_id=trace.request_id,
            stage="validation",
            level="info",
            event_type="operator.validation.accepted",
            title="Template accepted",
            summary="Accepted learned command template.",
            detail={
                "command_template_id": "cmdtpl-bad",
                "cache_type": "payload_command_template",
                "lr_mode": "lr_ex",
            },
        )
    )
    trace_store.append_event(
        AgentTraceEvent(
            request_id=trace.request_id,
            stage="execution",
            level="error",
            event_type="operator.execution.failed",
            title="Command failed",
            summary="The learned command failed.",
            detail={"action_id": "action_1", "stderr": "bad transform"},
        )
    )
    planning_trace = SimpleNamespace(
        metadata={
            "operator_command_template_cache_applied": {
                "template_id": "cmdtpl-bad",
                "cache_type": "payload_command_template",
                "lr_mode": "lr_ex",
            },
            "operator_execution_records": [{"action_id": "action_1", "status": "error"}],
        }
    )

    result = _append_learned_command_failure_notice(
        "The run failed.",
        planning_trace=planning_trace,
        trace=trace_store.get_trace(trace.request_id),
    )

    assert LEARNED_COMMAND_FAILURE_NOTICE in result
    assert "LR-EX `cmdtpl-bad`" in result


def test_learned_command_failure_notice_skips_normal_command_failure() -> None:
    trace_store = AgentTraceStore()
    trace = trace_store.create_request("run normal command")
    trace_store.append_event(
        AgentTraceEvent(
            request_id=trace.request_id,
            stage="execution",
            level="error",
            event_type="operator.execution.failed",
            title="Command failed",
            summary="A normal command failed.",
            detail={"action_id": "action_1", "stderr": "bad flag"},
        )
    )
    planning_trace = SimpleNamespace(
        metadata={"operator_execution_records": [{"action_id": "action_1", "status": "error"}]}
    )

    result = _append_learned_command_failure_notice(
        "The run failed.",
        planning_trace=planning_trace,
        trace=trace_store.get_trace(trace.request_id),
    )

    assert result == "The run failed."


def test_agent_ui_standard_request_returns_and_persists_conversation_id(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_chats_db_path=tmp_path / "chats.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "standard chat"}).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    assert submitted["conversation_id"].startswith("conv-")
    listing = client.get("/api/agent/chats").json()
    assert listing["chats"][0]["conversation_id"] == submitted["conversation_id"]
    assert listing["chats"][0]["response_metrics"]["duration_seconds"] >= 0
    assert {"completed", "single turn", "has response"}.issubset(set(listing["chats"][0]["tags"]))
    detail = client.get(f"/api/agent/chats/{submitted['conversation_id']}").json()["chat"]
    assert detail["turns"][0]["prompt"] == "standard chat"
    assert detail["turns"][0]["final_response"] == "handled: standard chat"
    assert detail["turns"][0]["response_metrics"]["duration_seconds"] >= 0
    assert detail["turns"][0]["display_document"]["sections"][0]["title"] == "Fake Rows"


def test_agent_ui_trace_endpoint_falls_back_to_durable_chat_turn(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_chats_db_path=tmp_path / "chats.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "durable fallback"}).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    with client.app.state.agent_trace_store._lock:
        client.app.state.agent_trace_store._traces.clear()

    restored = client.get(f"/api/agent/trace/{submitted['request_id']}")

    assert restored.status_code == 200
    payload = restored.json()
    assert payload["trace_source"] == "chat_history"
    assert payload["request_id"] == submitted["request_id"]
    assert payload["prompt"] == "durable fallback"
    assert payload["final_response"] == "handled: durable fallback"
    assert payload["display_document"]["sections"][0]["title"] == "Fake Rows"


def test_agent_ui_chat_history_fallback_returns_learning_summary(tmp_path) -> None:
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_chats_db_path=tmp_path / "chats.db",
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )
    conversation_id = client.app.state.agent_conversation_store.create("commit staged files")
    trace_store = client.app.state.agent_trace_store
    trace = trace_store.create_request("commit staged files")
    trace_store.append_event(
        AgentTraceEvent(
            request_id=trace.request_id,
            stage="execution",
            event_type="execution.command.completed",
            title="Command completed",
            summary="Done: Reused LR-EX payload-aware command template cmdtpl-7482e031eefb.",
            detail={
                "metadata": {
                    "operation_description": "Reused LR-EX payload-aware command template cmdtpl-7482e031eefb.",
                    "command_template_id": "cmdtpl-7482e031eefb",
                    "cache_type": "payload_command_template",
                }
            },
        )
    )
    trace_store.complete_request(trace.request_id, "committed")
    client.app.state.agent_conversation_store.append_turn(
        conversation_id,
        trace_store.get_trace(trace.request_id),
    )

    with trace_store._lock:
        trace_store._traces.clear()

    restored = client.get(f"/api/agent/trace/{trace.request_id}")
    detail = client.get(f"/api/agent/chats/{conversation_id}").json()["chat"]

    assert restored.status_code == 200
    assert restored.json()["trace_source"] == "chat_history"
    assert restored.json()["learning_summary"]["counts"]["applied"] == 1
    assert restored.json()["learning_summary"]["rows"][0]["label"] == "LR-EX payload-aware command template"
    assert detail["turns"][0]["learning_summary"]["counts"]["applied"] == 1


def test_agent_ui_chat_history_delete_removes_followup_conversation(tmp_path) -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_chats_db_path=tmp_path / "chats.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "list files", "agent_mode": "llm_operator"},
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    deleted = client.delete(f"/api/agent/chats/{submitted['conversation_id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/agent/chats/{submitted['conversation_id']}").status_code == 404

    followup = client.post(
        "/api/agent/request",
        json={
            "prompt": "continue",
            "agent_mode": "llm_operator",
            "conversation_id": submitted["conversation_id"],
        },
    )
    assert followup.status_code == 404


def test_agent_ui_operator_followup_receives_prior_context() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    first = client.post(
        "/api/agent/request",
        json={"prompt": "list files", "agent_mode": "llm_operator"},
    ).json()
    with client.stream("GET", first["stream_url"]) as response:
        _ = response.read()

    second = client.post(
        "/api/agent/request",
        json={
            "prompt": "which file did you list?",
            "agent_mode": "llm_operator",
            "conversation_id": first["conversation_id"],
        },
    ).json()
    with client.stream("GET", second["stream_url"]) as response:
        _ = response.read()

    context = runtime.last_context["operator_conversation_context"]
    assert second["conversation_id"] == first["conversation_id"]
    assert context["conversation_id"] == first["conversation_id"]
    assert context["turns"][0]["prompt"] == "list files"
    assert context["turns"][0]["final_response"] == "handled: list files"
    assert context["turns"][0]["raw_payloads"][0]["value_source"] == "preview"


def test_agent_ui_operator_followup_can_include_full_payload_context() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_allow_full_payloads=True,
            ),
            agent_runtime=runtime,
        )
    )

    first = client.post(
        "/api/agent/request",
        json={"prompt": "list files", "agent_mode": "llm_operator"},
    ).json()
    with client.stream("GET", first["stream_url"]) as response:
        _ = response.read()

    second = client.post(
        "/api/agent/request",
        json={
            "prompt": "what did the raw output contain?",
            "agent_mode": "llm_operator",
            "conversation_id": first["conversation_id"],
        },
    ).json()
    with client.stream("GET", second["stream_url"]) as response:
        _ = response.read()

    raw_payload = runtime.last_context["operator_conversation_context"]["turns"][0]["raw_payloads"][0]
    assert raw_payload["value_source"] == "full_payload"
    assert raw_payload["value"]["rows"] == [{"path": "README.txt"}]


def test_agent_ui_operator_followup_context_includes_failed_operator_stderr() -> None:
    runtime = FailingOperatorRecordRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    first = client.post(
        "/api/agent/request",
        json={"prompt": "activate conda env", "agent_mode": "llm_operator"},
    ).json()
    with client.stream("GET", first["stream_url"]) as response:
        _ = response.read()

    second = client.post(
        "/api/agent/request",
        json={
            "prompt": "try again using the error",
            "agent_mode": "llm_operator",
            "conversation_id": first["conversation_id"],
        },
    ).json()
    with client.stream("GET", second["stream_url"]) as response:
        _ = response.read()

    raw_payload = runtime.last_context["operator_conversation_context"]["turns"][0]["raw_payloads"][0]
    assert second["conversation_id"] == first["conversation_id"]
    assert raw_payload["value_source"] == "preview"
    assert raw_payload["diagnostics"]["status"] == "error"
    assert "CONDA_HINT" in raw_payload["diagnostics"]["stderr"]


def test_agent_ui_operator_followup_rejects_missing_conversation() -> None:
    client = _client()

    response = client.post(
        "/api/agent/request",
        json={
            "prompt": "follow up",
            "agent_mode": "llm_operator",
            "conversation_id": "conv-missing",
        },
    )

    assert response.status_code == 404
    assert "Conversation not found" in response.text


def test_agent_ui_stop_route_marks_request_cancelled() -> None:
    runtime = SlowAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "slow test", "agent_mode": "standard"},
    )
    request_id = submitted.json()["request_id"]

    stopped = client.post(f"/api/agent/stop/{request_id}")

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"
    trace = client.get(f"/api/agent/trace/{request_id}").json()
    assert trace["status"] == "cancelled"
    assert any(event["event_type"] == "request.cancelled" for event in trace["events"])
    assert runtime.gateway_cancel_calls
    assert runtime.gateway_cancel_calls[0]["execution_id"] == request_id


def test_agent_ui_health_reports_retained_requests() -> None:
    client = _client()

    response = client.get("/api/agent/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["mode"] == "agent_ui"
    assert response.json()["retained_requests"] == 0
    assert response.json()["raw_previews_enabled"] is True


def test_agent_ui_raw_payload_endpoint_can_include_full_payload_when_enabled() -> None:
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_ui_allow_full_payloads=True,
            ),
            agent_runtime=FakeAgentRuntime(),
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "list files"})
    request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    trace = client.get(f"/api/agent/trace/{request_id}").json()
    data_ref = trace["display_document"]["sections"][0]["data_ref"]

    raw = client.get(f"/api/agent/raw/{request_id}/{data_ref}")

    assert raw.status_code == 200
    assert raw.json()["full_payload_available"] is True
    assert raw.json()["full_payload"]["rows"] == [{"path": "README.txt"}]


def test_agent_ui_confirmation_approval_replays_saved_prompt() -> None:
    runtime = FakeConfirmationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "calculate memory and write mem.txt"},
    )
    first_request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(f"/api/agent/trace/{first_request_id}").json()
    assert trace["confirmation_required"] is True
    assert trace["final_response"].startswith("## Confirmation Required")

    approved = client.post(
        f"/api/agent/confirmation/{first_request_id}",
        json={"action": "approve"},
    )

    assert approved.status_code == 200
    approved_payload = approved.json()
    assert approved_payload["status"] == "approved"
    assert approved_payload["request_id"] != first_request_id

    with client.stream("GET", approved_payload["stream_url"]) as response:
        _ = response.read()
    approved_trace = client.get(approved_payload["trace_url"]).json()

    assert approved_trace["final_response"] == "approved: calculate memory and write mem.txt"
    assert runtime.replay_contexts[-1]["confirmation"] is True


def test_agent_ui_typein_macro_sanitizes_trace_and_survives_confirmation() -> None:
    runtime = FakeConfirmationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": 'use sudo and typein "macro-secret-value"'},
    )
    first_request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(f"/api/agent/trace/{first_request_id}").json()
    assert trace["prompt"] == 'use sudo and typein "[redacted]"'
    assert "macro-secret-value" not in json.dumps(trace)

    approved = client.post(
        f"/api/agent/confirmation/{first_request_id}",
        json={"action": "approve"},
    )
    assert approved.status_code == 200
    with client.stream("GET", approved.json()["stream_url"]) as response:
        _ = response.read()

    assert runtime.replay_contexts[-1][USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "macro-secret-value"
    approved_trace = client.get(approved.json()["trace_url"]).json()
    assert "macro-secret-value" not in json.dumps(approved_trace)


def test_agent_ui_typein_macro_creates_background_terminal_without_visible_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __init__(self, body: dict[str, Any] | None = None) -> None:
            self._body = body

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            if self._body is not None:
                return json.dumps(self._body).encode("utf-8")
            payload = json.loads(captured["data"].decode("utf-8"))
            return json.dumps(
                {
                    "ok": True,
                    "session_id": payload["session_id"],
                    "cwd": payload["initial_cwd"],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        if str(request.full_url).endswith("/models"):
            return FakeHTTPResponse({"data": []})
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": 'push changes to origin typein "macro-secret-value" for ssh',
            "context": {"terminal_cwd": str(tmp_path)},
        },
    )
    assert submitted.status_code == 200
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    request_payload = json.loads(captured["data"].decode("utf-8"))
    assert captured["url"] == "http://127.0.0.1:8787/terminal/session"
    assert request_payload["node"] == "localhost"
    assert request_payload["initial_cwd"] == str(tmp_path)
    assert runtime.last_context["terminal_session_id"] == request_payload["session_id"]
    assert runtime.last_context["terminal_cwd"] == str(tmp_path)
    assert runtime.last_context["execute_in_terminal"] is True
    assert runtime.last_context["request_background_terminal"] is True
    assert runtime.last_context["background_terminal"] is True
    assert runtime.last_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "macro-secret-value"


def test_agent_ui_exact_credential_parameter_creates_terminal_context_without_visible_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    parameters_db_path = tmp_path / "parameters.db"
    store = AgentParameterStore(parameters_db_path)
    store.create(
        AgentParameterCreate(
            key="sshgit_key",
            value_json={"password": "ssh-secret"},
            description="Git SSH password",
            sensitive=True,
        )
    )
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_parameters_db_path=parameters_db_path,
            ),
            agent_runtime=runtime,
        )
    )
    captured: dict[str, Any] = {}

    class FakeHTTPResponse:
        def __init__(self, body: dict[str, Any] | None = None) -> None:
            self._body = body

        def __enter__(self) -> "FakeHTTPResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            if self._body is not None:
                return json.dumps(self._body).encode("utf-8")
            payload = json.loads(captured["data"].decode("utf-8"))
            return json.dumps(
                {
                    "ok": True,
                    "session_id": payload["session_id"],
                    "cwd": payload["initial_cwd"],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        if str(request.full_url).endswith("/models"):
            return FakeHTTPResponse({"data": []})
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr(agent_ui.urllib_request, "urlopen", fake_urlopen)

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "git push using sshgit_key",
            "context": {"terminal_cwd": str(tmp_path)},
        },
    )
    assert submitted.status_code == 200
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    request_payload = json.loads(captured["data"].decode("utf-8"))
    assert captured["url"] == "http://127.0.0.1:8787/terminal/session"
    assert request_payload["initial_cwd"] == str(tmp_path)
    assert runtime.last_context["terminal_session_id"] == request_payload["session_id"]
    assert runtime.last_context["execute_in_terminal"] is True
    assert runtime.last_context["parameter_typein_terminal_required"] is True
    assert runtime.last_context["request_parameter_typein_terminal"] is True
    assert runtime.last_context["request_background_terminal"] is True


def test_agent_ui_checkonline_runs_lookup_without_leaking_typein(monkeypatch) -> None:
    runtime = FakeAgentRuntime()
    lookup_queries: list[str] = []

    def fake_lookup(query: str, **_: object) -> OnlineLookupResult:
        lookup_queries.append(query)
        return OnlineLookupResult(
            provider="duck_ai",
            query=query,
            available=True,
            answer_text="Use nvidia-smi --help for command options.",
            source_title="NVIDIA docs",
            source_url="https://docs.nvidia.com/deploy/nvidia-smi/",
            fetched_at="2026-05-15T00:00:00Z",
        )

    monkeypatch.setattr(agent_ui, "lookup_online_answer", fake_lookup)
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": '/checkonline how to use nvidia-smi and typein "secret-value" for password'},
    )
    assert submitted.status_code == 200
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(submitted.json()["trace_url"]).json()
    trace_json = json.dumps(trace)
    assert trace["prompt"] == 'check online how to use nvidia-smi and typein "[redacted]" for password'
    assert "secret-value" not in trace_json
    assert lookup_queries == []
    assert runtime.last_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] is True
    assert "operator.online_lookup.completed" not in trace_json


def test_agent_ui_checkonlineai_sanitizes_without_leaking_typein() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": '/checkonlineai how to use nvidia-smi and typein "secret-value" for password'},
    )
    assert submitted.status_code == 200
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(submitted.json()["trace_url"]).json()
    trace_json = json.dumps(trace)
    assert trace["prompt"] == 'using online AI context how to use nvidia-smi and typein "[redacted]" for password'
    assert "secret-value" not in trace_json
    assert runtime.last_context[ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY] is True
    assert runtime.last_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "secret-value"
    assert any(
        item["kind"] == "checkonlineai"
        for item in runtime.last_context["operator_user_macro_summaries"]
    )


def test_agent_ui_literal_payload_shields_decomposition_prompt_and_preserves_value() -> None:
    runtime = FakeAgentRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )
    commit_message = (
        "Add explicit `/checkonline` online lookup support\n\n"
        "- Add `onlinelinelookup` module for compact Duck.ai-based lookup results\n"
        "- Register `/checkonline` in the server-backed prompt macro registry"
    )

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": (
                f'stage all changes and commit using description "{commit_message}" '
                'and push changes typein "ssh-secret"'
            )
        },
    )
    assert submitted.status_code == 200
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(submitted.json()["trace_url"]).json()
    trace_json = json.dumps(trace)
    assert trace["prompt"] == (
        'stage all changes and commit using description [provided description_payload payload] '
        'and push changes typein "[redacted]"'
    )
    assert "onlinelinelookup` module" not in trace["prompt"]
    assert "operator.literal_payload.detected" in trace_json
    assert "operator.online_lookup.started" not in trace_json
    payloads = runtime.last_context[OPERATOR_LITERAL_PAYLOADS_CONTEXT_KEY]
    assert payloads[0]["input_name"] == "description_payload"
    assert payloads[0]["value"] == commit_message
    assert runtime.last_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "ssh-secret"


def test_agent_ui_continue_from_failure_preserves_conversation_id() -> None:
    runtime = FakeContinuationRuntime(agent_mode="llm_operator")
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "commit changes",
            "agent_mode": "llm_operator",
            "context": {
                "guided_deliberation_mode": "always",
                "shell_input_bindings_mode": "allow",
                "llm_operator_max_validation_repair_attempts": 4,
            },
        },
    )
    parent_request_id = submitted.json()["request_id"]
    conversation_id = submitted.json()["conversation_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    continued = client.post(
        f"/api/agent/continue/{parent_request_id}",
        json={"context": {"llm_operator_final_response_mode": "detailed"}},
    )

    assert continued.status_code == 200
    payload = continued.json()
    assert payload["parent_request_id"] == parent_request_id
    assert payload["request_id"] != parent_request_id
    assert payload["conversation_id"] == conversation_id

    with client.stream("GET", payload["stream_url"]) as response:
        _ = response.read()
    continued_trace = client.get(payload["trace_url"]).json()

    assert continued_trace["final_response"] == "continued: commit changes"
    assert runtime.continuation_contexts[-1]["parent_request_id"] == parent_request_id
    assert runtime.continuation_contexts[-1]["agent_mode"] == "llm_operator"
    assert runtime.continuation_contexts[-1]["conversation_id"] == conversation_id
    assert "guided_deliberation_mode" not in runtime.continuation_contexts[-1]
    assert runtime.continuation_contexts[-1]["shell_input_bindings_mode"] == "allow"
    assert "llm_operator_max_validation_repair_attempts" not in runtime.continuation_contexts[-1]
    assert runtime.continuation_contexts[-1]["llm_operator_final_response_mode"] == "detailed"


def test_agent_ui_continue_from_failure_uses_record_driven_fallback() -> None:
    runtime = FakeContinuationRuntime(
        agent_mode="llm_operator",
        include_continuation_metadata=False,
    )
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "commit changes", "agent_mode": "llm_operator"},
    )
    parent_request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    continued = client.post(f"/api/agent/continue/{parent_request_id}", json={})

    assert continued.status_code == 200
    with client.stream("GET", continued.json()["stream_url"]) as response:
        _ = response.read()
    assert runtime.continuation_contexts[-1]["parent_request_id"] == parent_request_id
    assert runtime.continuation_contexts[-1]["agent_mode"] == "llm_operator"


def test_agent_ui_continue_from_failure_uses_streaming_state_record_fallback() -> None:
    runtime = FakeStreamingFailureContinuationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "list branches then extract commit times",
            "agent_mode": "llm_operator",
            "workflow_execution_mode": "streaming",
        },
    )
    parent_request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    continued = client.post(f"/api/agent/continue/{parent_request_id}", json={})

    assert continued.status_code == 200
    with client.stream("GET", continued.json()["stream_url"]) as response:
        _ = response.read()
    continued_trace = client.get(continued.json()["trace_url"]).json()

    assert continued_trace["final_response"] == "streaming continuation resumed"
    assert runtime.continuation_contexts[-1]["parent_request_id"] == parent_request_id
    assert runtime.continuation_contexts[-1]["agent_mode"] == "llm_operator"


def test_agent_ui_continue_from_failure_preserves_terminal_binding_across_gateway_switch(tmp_path) -> None:
    runtime = FakeContinuationRuntime(agent_mode="llm_operator")
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
                agent_gateways_db_path=tmp_path / "gateways.db",
                default_node="worker-a",
                available_nodes_raw="worker-a,worker-b",
                gateway_endpoints={
                    "worker-a": "http://127.0.0.1:8787",
                    "worker-b": "http://127.0.0.1:8788",
                },
            ),
            agent_runtime=runtime,
        )
    )
    gateways = client.get("/api/agent/gateways").json()["gateways"]
    gateway_a = next(gateway for gateway in gateways if gateway["node"] == "worker-a")
    gateway_b = next(gateway for gateway in gateways if gateway["node"] == "worker-b")

    terminal_a = client.get(
        "/api/agent/terminal/config",
        params={"gateway_id": gateway_a["gateway_id"]},
    ).json()
    updated_a = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": terminal_a["session_id"], "cwd": "/tmp/worker-a"},
    )
    assert updated_a.status_code == 200

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "commit changes",
            "agent_mode": "llm_operator",
            "context": {
                "gateway_id": gateway_a["gateway_id"],
                "terminal_session_id": terminal_a["session_id"],
                "terminal_cwd": "/tmp/worker-a",
                "execute_in_terminal": True,
            },
        },
    )
    parent_request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    terminal_b = client.get(
        "/api/agent/terminal/config",
        params={"gateway_id": gateway_b["gateway_id"]},
    ).json()
    updated_b = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": terminal_b["session_id"], "cwd": "/tmp/worker-b"},
    )
    assert updated_b.status_code == 200

    continued = client.post(
        f"/api/agent/continue/{parent_request_id}",
        json={
            "context": {
                "gateway_id": gateway_b["gateway_id"],
                "terminal_session_id": terminal_b["session_id"],
                "terminal_cwd": "/tmp/worker-b",
                "execute_in_terminal": True,
            },
        },
    )
    assert continued.status_code == 200
    with client.stream("GET", continued.json()["stream_url"]) as response:
        _ = response.read()

    continuation_context = runtime.continuation_contexts[-1]
    assert continuation_context["terminal_session_id"] == terminal_a["session_id"]
    assert continuation_context["terminal_cwd"] == "/tmp/worker-a"
    assert continuation_context["gateway_id"] == gateway_a["gateway_id"]
    assert continuation_context["gateway_node"] == "worker-a"
    assert continuation_context["execute_in_terminal"] is True


def test_agent_ui_continue_from_failure_preserves_agentic_mode() -> None:
    runtime = FakeContinuationRuntime(agent_mode="standard_operator")
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "commit changes"})
    parent_request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    continued = client.post(f"/api/agent/continue/{parent_request_id}", json={})

    assert continued.status_code == 200
    with client.stream("GET", continued.json()["stream_url"]) as response:
        _ = response.read()

    assert runtime.continuation_contexts[-1]["agent_mode"] == "standard_operator"
    assert runtime.continuation_contexts[-1]["parent_request_id"] == parent_request_id


def test_agent_ui_auto_confirmation_approval_uses_existing_confirmation_route() -> None:
    runtime = FakeConfirmationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    controls = client.post("/api/agent/runtime-controls", json={"auto_approve_commands": True})
    assert controls.status_code == 200
    assert controls.json()["auto_approve_commands"] is True

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "calculate memory and write mem.txt"},
    )
    first_request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(f"/api/agent/trace/{first_request_id}").json()
    assert trace["confirmation_required"] is True

    approved = client.post(
        f"/api/agent/confirmation/{first_request_id}",
        json={
            "action": "approve",
            "context": {
                "auto_approve_commands": True,
                "auto_approved_confirmation": True,
            },
        },
    )

    assert approved.status_code == 200
    approved_payload = approved.json()
    with client.stream("GET", approved_payload["stream_url"]) as response:
        _ = response.read()
    approved_trace = client.get(approved_payload["trace_url"]).json()

    assert approved_trace["final_response"] == "approved: calculate memory and write mem.txt"
    assert runtime.replay_contexts[-1]["confirmation"] is True
    assert runtime.replay_contexts[-1]["auto_approved_confirmation"] is True
    assert any(
        event["event_type"] == "confirmation.auto_approved"
        for event in approved_trace["events"]
    )


def test_agent_ui_confirmation_approval_refreshes_terminal_context() -> None:
    runtime = FakeConfirmationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    config = client.get("/api/agent/terminal/config").json()
    updated = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": config["session_id"], "cwd": "/tmp"},
    )
    assert updated.status_code == 200

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "git push origin HEAD"},
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    approved = client.post(
        f"/api/agent/confirmation/{submitted['request_id']}",
        json={
            "action": "approve",
            "context": {
                "terminal_session_id": config["session_id"],
                "terminal_cwd": "/tmp",
                "execute_in_terminal": True,
                "llm_operator_max_validation_repair_attempts": 7,
                "ignored": "not merged",
            },
        },
    )
    assert approved.status_code == 200
    with client.stream("GET", approved.json()["stream_url"]) as response:
        _ = response.read()

    replay_context = runtime.replay_contexts[-1]
    assert replay_context["confirmation"] is True
    assert replay_context["terminal_session_id"] == config["session_id"]
    assert replay_context["terminal_cwd"] == "/tmp"
    assert replay_context["execute_in_terminal"] is True
    assert "llm_operator_max_validation_repair_attempts" not in replay_context
    assert "ignored" not in replay_context


def test_agent_ui_confirmation_approval_preserves_terminal_binding_across_gateway_switch(tmp_path) -> None:
    runtime = FakeConfirmationRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
                agent_gateways_db_path=tmp_path / "gateways.db",
                default_node="worker-a",
                available_nodes_raw="worker-a,worker-b",
                gateway_endpoints={
                    "worker-a": "http://127.0.0.1:8787",
                    "worker-b": "http://127.0.0.1:8788",
                },
            ),
            agent_runtime=runtime,
        )
    )
    gateways = client.get("/api/agent/gateways").json()["gateways"]
    gateway_a = next(gateway for gateway in gateways if gateway["node"] == "worker-a")
    gateway_b = next(gateway for gateway in gateways if gateway["node"] == "worker-b")

    terminal_a = client.get(
        "/api/agent/terminal/config",
        params={"gateway_id": gateway_a["gateway_id"]},
    ).json()
    updated_a = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": terminal_a["session_id"], "cwd": "/tmp/worker-a"},
    )
    assert updated_a.status_code == 200

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "calculate memory and write mem.txt",
            "context": {
                "gateway_id": gateway_a["gateway_id"],
                "terminal_session_id": terminal_a["session_id"],
                "terminal_cwd": "/tmp/worker-a",
                "execute_in_terminal": True,
            },
        },
    )
    request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    terminal_b = client.get(
        "/api/agent/terminal/config",
        params={"gateway_id": gateway_b["gateway_id"]},
    ).json()
    updated_b = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": terminal_b["session_id"], "cwd": "/tmp/worker-b"},
    )
    assert updated_b.status_code == 200

    approved = client.post(
        f"/api/agent/confirmation/{request_id}",
        json={
            "action": "approve",
            "context": {
                "gateway_id": gateway_b["gateway_id"],
                "terminal_session_id": terminal_b["session_id"],
                "terminal_cwd": "/tmp/worker-b",
                "execute_in_terminal": True,
            },
        },
    )
    assert approved.status_code == 200
    with client.stream("GET", approved.json()["stream_url"]) as response:
        _ = response.read()

    replay_context = runtime.replay_contexts[-1]
    assert replay_context["terminal_session_id"] == terminal_a["session_id"]
    assert replay_context["terminal_cwd"] == "/tmp/worker-a"
    assert replay_context["gateway_id"] == gateway_a["gateway_id"]
    assert replay_context["gateway_node"] == "worker-a"
    assert replay_context["execute_in_terminal"] is True


def test_agent_ui_operator_confirmation_preserves_conversation_id() -> None:
    runtime = FakeConfirmationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "write mem.txt", "agent_mode": "llm_operator"},
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    approved = client.post(
        f"/api/agent/confirmation/{submitted['request_id']}",
        json={"action": "approve"},
    )

    assert approved.status_code == 200
    assert approved.json()["conversation_id"] == submitted["conversation_id"]


def test_agent_ui_confirmation_deny_does_not_replay() -> None:
    runtime = FakeConfirmationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "write mem.txt"})
    request_id = submitted.json()["request_id"]
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    denied = client.post(f"/api/agent/confirmation/{request_id}", json={"action": "deny"})
    trace = client.get(f"/api/agent/trace/{request_id}").json()

    assert denied.status_code == 200
    assert denied.json()["status"] == "denied"
    assert "No confirmation-gated actions were executed" in denied.json()["final_response"]
    assert trace["status"] == "cancelled"
    assert trace["final_response"].startswith("## Confirmation Denied")
    assert trace["confirmation_required"] is False
    assert trace["confirmation_actions"] == []
    assert trace["display_document"] is None
    assert [event["event_type"] for event in trace["events"]][-1] == "confirmation.denied"
    assert runtime.replay_contexts == []


def test_agent_ui_repo_summary_fast_path_uses_read_only_capsule(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# OpenFabric\n\nLocal agent runtime.\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[project]\nname = \"openfabric-test\"\n\n[project.scripts]\nopenfabric = \"agent_runtime.api.app:main\"\n",
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    before = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*"))
    runtime = CountingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=repo,
                agent_ui_settings_db_path=tmp_path / "settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "What is this repo and what are the entrypoints?"},
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    trace = client.get(submitted.json()["trace_url"]).json()
    after = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*"))

    assert trace["status"] == "completed"
    assert "## Repo Summary" in trace["final_response"]
    assert "openfabric-test" in trace["final_response"]
    assert "entrypoints" in trace["final_response"].lower()
    assert before == after
    assert runtime.calls == 0
    command_events = [
        event
        for event in trace["events"]
        if str(event["event_type"]).startswith("execution.command.")
    ]
    assert command_events
    assert "rg --files" in command_events[0]["detail"]["command"]


def test_agent_ui_exact_file_write_fast_path_requires_confirmation_and_denial_leaves_no_file(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = CountingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=repo,
                agent_ui_settings_db_path=tmp_path / "settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": 'create file at notes/v1.txt containing exactly "hello V1"'},
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    trace = client.get(submitted.json()["trace_url"]).json()

    assert trace["status"] == "completed"
    assert trace["confirmation_required"] is True
    assert len(trace["confirmation_actions"]) == 1
    assert trace["confirmation_actions"][0]["relative_path"] == "notes/v1.txt"
    assert trace["confirmation_actions"][0]["bound_inputs"]["OF_INPUT_CONTENT"] == "hello V1"
    assert not (repo / "notes" / "v1.txt").exists()

    denied = client.post(
        f"/api/agent/confirmation/{submitted.json()['request_id']}",
        json={"action": "deny"},
    )

    assert denied.status_code == 200
    assert not (repo / "notes" / "v1.txt").exists()
    assert runtime.calls == 0


def test_agent_ui_exact_file_write_fast_path_approval_writes_exact_content(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = CountingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=repo,
                agent_ui_settings_db_path=tmp_path / "settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": 'write file at notes/v1.txt containing exactly "hello V1"'},
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    approved = client.post(
        f"/api/agent/confirmation/{submitted.json()['request_id']}",
        json={"action": "approve"},
    )
    with client.stream("GET", approved.json()["stream_url"]) as response:
        _ = response.read()
    child_trace = client.get(approved.json()["trace_url"]).json()

    assert approved.status_code == 200
    assert (repo / "notes" / "v1.txt").read_text(encoding="utf-8") == "hello V1"
    assert child_trace["status"] == "completed"
    assert "Created `notes/v1.txt`" in child_trace["final_response"]
    assert any(event["event_type"] == "execution.command.completed" for event in child_trace["events"])
    assert runtime.calls == 0


def test_agent_ui_exact_file_write_fast_path_rejects_absolute_paths(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.txt"
    runtime = CountingAgentRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=repo,
                agent_ui_settings_db_path=tmp_path / "settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": f'create file at {outside} containing exactly "nope"'},
    )
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    trace = client.get(submitted.json()["trace_url"]).json()

    assert trace["status"] == "completed"
    assert trace["confirmation_required"] is False
    assert trace["final_response"].startswith("## File Write Rejected")
    assert not outside.exists()
    assert runtime.calls == 0


def test_agent_ui_clarification_resume_replays_saved_prompt_with_answer() -> None:
    runtime = FakeClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "create a conda environment"},
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    trace = client.get(f"/api/agent/trace/{submitted['request_id']}").json()
    assert trace["clarification_required"] is True
    assert trace["clarification_request"]["options"][0]["option_id"] == "py311"

    resumed = client.post(
        f"/api/agent/clarification/{submitted['request_id']}",
        json={
            "answer": "Python 3.11",
            "selected_option_id": "py311",
            "context": {
                "llm_operator_clarification_strategy": "ask_any_missing",
                "llm_operator_max_clarification_rounds": 5,
            },
        },
    )

    assert resumed.status_code == 200
    payload = resumed.json()
    assert payload["status"] == "answered"
    assert payload["request_id"] != submitted["request_id"]

    with client.stream("GET", payload["stream_url"]) as response:
        _ = response.read()
    resumed_trace = client.get(payload["trace_url"]).json()

    assert resumed_trace["final_response"] == "answered with Python 3.11: create a conda environment"
    assert runtime.contexts[-1]["clarifications"][-1]["answer"] == "Python 3.11"
    assert runtime.contexts[-1]["clarifications"][-1]["selected_option_id"] == "py311"
    assert runtime.contexts[-1]["agent_clarification_mode"] == "balanced"
    assert runtime.contexts[-1]["llm_operator_max_clarification_rounds"] == 3


def test_agent_ui_clarification_resume_preserves_terminal_binding_across_gateway_switch(tmp_path) -> None:
    runtime = FakeClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                workspace_root=tmp_path,
                agent_gateways_db_path=tmp_path / "gateways.db",
                default_node="worker-a",
                available_nodes_raw="worker-a,worker-b",
                gateway_endpoints={
                    "worker-a": "http://127.0.0.1:8787",
                    "worker-b": "http://127.0.0.1:8788",
                },
            ),
            agent_runtime=runtime,
        )
    )

    gateways = client.get("/api/agent/gateways").json()["gateways"]
    gateway_a = next(gateway for gateway in gateways if gateway["node"] == "worker-a")
    gateway_b = next(gateway for gateway in gateways if gateway["node"] == "worker-b")

    terminal_a = client.get(
        "/api/agent/terminal/config",
        params={"gateway_id": gateway_a["gateway_id"]},
    ).json()
    updated_a = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": terminal_a["session_id"], "cwd": "/tmp/worker-a"},
    )
    assert updated_a.status_code == 200

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "create a conda environment",
            "context": {
                "gateway_id": gateway_a["gateway_id"],
                "terminal_session_id": terminal_a["session_id"],
                "terminal_cwd": "/tmp/worker-a",
                "execute_in_terminal": True,
            },
        },
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    trace = client.get(f"/api/agent/trace/{submitted['request_id']}").json()
    assert trace["clarification_required"] is True
    assert trace["clarification_request"]["options"][0]["option_id"] == "py311"

    terminal_b = client.get(
        "/api/agent/terminal/config",
        params={"gateway_id": gateway_b["gateway_id"]},
    ).json()
    updated_b = client.post(
        "/api/agent/terminal/cwd",
        json={"session_id": terminal_b["session_id"], "cwd": "/tmp/worker-b"},
    )
    assert updated_b.status_code == 200

    resumed = client.post(
        f"/api/agent/clarification/{submitted['request_id']}",
        json={
            "answer": "Python 3.11",
            "selected_option_id": "py311",
            "context": {
                "gateway_id": gateway_b["gateway_id"],
                "terminal_session_id": terminal_b["session_id"],
                "terminal_cwd": "/tmp/worker-b",
                "execute_in_terminal": True,
            },
        },
    )

    assert resumed.status_code == 200
    with client.stream("GET", resumed.json()["stream_url"]) as response:
        _ = response.read()

    resumed_context = runtime.contexts[-1]
    assert resumed_context["terminal_session_id"] == terminal_a["session_id"]
    assert resumed_context["terminal_cwd"] == "/tmp/worker-a"
    assert resumed_context["gateway_id"] == gateway_a["gateway_id"]
    assert resumed_context["gateway_node"] == "worker-a"


def test_agent_ui_clarification_resume_preserves_answer_whitespace() -> None:
    runtime = FakeClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "create a conda environment"},
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    answer = "Python 3.12\n  include cuda"
    resumed = client.post(
        f"/api/agent/clarification/{submitted['request_id']}",
        json={"answer": f"  {answer}  ", "selected_option_id": "custom"},
    )

    assert resumed.status_code == 200
    with client.stream("GET", resumed.json()["stream_url"]) as response:
        _ = response.read()
    assert runtime.contexts[-1]["clarifications"][-1]["answer"] == answer


def test_agent_ui_credential_clarification_selected_parameter_adds_private_macro(tmp_path) -> None:
    runtime = FakeCredentialClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_parameters_db_path=tmp_path / "parameters.db",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_tasks_db_path=tmp_path / "tasks.db",
                agent_monitors_db_path=tmp_path / "monitors.db",
                agent_ui_settings_db_path=tmp_path / "ui_settings.db",
            ),
            agent_runtime=runtime,
        )
    )
    created = client.post(
        "/api/agent/parameters",
        json={
            "key": "sshgit_key",
            "value_json": {"password": "raw-password"},
            "description": "Git SSH password",
            "aliases": ["git ssh key"],
            "tags": ["git", "ssh"],
            "sensitive": True,
        },
    )
    assert created.status_code == 200

    submitted = client.post("/api/agent/request", json={"prompt": "git push"})
    assert submitted.status_code == 200
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    trace = client.get(f"/api/agent/trace/{submitted.json()['request_id']}").json()
    assert trace["clarification_request"]["parameter_choices"][0]["key"] == "sshgit_key"

    resumed = client.post(
        f"/api/agent/clarification/{submitted.json()['request_id']}",
        json={
            "answer": "Use parameter sshgit_key",
            "selected_option_id": "param:sshgit_key",
            "parameter_choice_id": "param:sshgit_key",
            "parameter_key": "sshgit_key",
            "answer_is_secret": True,
        },
    )
    assert resumed.status_code == 200
    with client.stream("GET", resumed.json()["stream_url"]) as response:
        _ = response.read()

    resumed_context = runtime.contexts[-1]
    assert resumed_context["clarifications"][-1]["answer"] == "Use parameter sshgit_key"
    assert resumed_context["clarifications"][-1]["parameter_key"] == "sshgit_key"
    macro = resumed_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]
    assert macro["value"] == "raw-password"
    assert macro["source"] == "parameter_store"
    assert macro["parameter_field"] == "password"
    assert "raw-password" not in str(resumed_context["clarifications"])
    assert "raw-password" not in client.get(resumed.json()["trace_url"]).text


def test_agent_ui_credential_clarification_freeform_secret_is_redacted(tmp_path) -> None:
    runtime = FakeCredentialClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_parameters_db_path=tmp_path / "parameters.db",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_tasks_db_path=tmp_path / "tasks.db",
                agent_monitors_db_path=tmp_path / "monitors.db",
                agent_ui_settings_db_path=tmp_path / "ui_settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "git push"})
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    resumed = client.post(
        f"/api/agent/clarification/{submitted.json()['request_id']}",
        json={"answer": "typed-secret", "answer_is_secret": True},
    )
    assert resumed.status_code == 200
    with client.stream("GET", resumed.json()["stream_url"]) as response:
        _ = response.read()

    resumed_context = runtime.contexts[-1]
    assert resumed_context["clarifications"][-1]["answer"] == "[redacted]"
    assert resumed_context["clarifications"][-1]["answer_is_secret"] is True
    assert resumed_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "typed-secret"
    assert "typed-secret" not in str(resumed_context["clarifications"])


def test_agent_ui_sudo_clarification_is_credential_capable(tmp_path) -> None:
    runtime = FakeSudoClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_tasks_db_path=tmp_path / "tasks.db",
                agent_monitors_db_path=tmp_path / "monitors.db",
                agent_ui_settings_db_path=tmp_path / "ui_settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "inspect the usb filesystem"})
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    trace = client.get(f"/api/agent/trace/{submitted.json()['request_id']}").json()
    request = trace["clarification_request"]
    assert request["question"] == (
        "This step previously failed and needs sudo to continue. "
        "Enter a sudo password or choose a saved credential to retry this exact command with sudo."
    )
    assert request["input_kind"] == "password"
    assert request["secret_input"] is True
    assert request["parameter_choices"][0]["key"] == "local_sudo_pass"
    assert request["metadata"]["proposed_sudo_command"] == "sudo dumpe2fs /dev/sdc2"
    script = client.get("/agent-ui/static/app.js?v=test")
    assert 'input.type = credentialClarification ? "password" : "text"' in script.text
    assert "submitCredentialAwareAnswer(optionId, labelText)" in script.text
    assert "Enter a password or choose a saved credential to continue." in script.text
    assert 'credentialValidation.className = "clarification-validation-message"' in script.text


def test_agent_ui_sudo_retry_option_requires_credential(tmp_path) -> None:
    runtime = FakeSudoClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_tasks_db_path=tmp_path / "tasks.db",
                agent_monitors_db_path=tmp_path / "monitors.db",
                agent_ui_settings_db_path=tmp_path / "ui_settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "inspect the usb filesystem"})
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()

    invalid = client.post(
        f"/api/agent/clarification/{submitted.json()['request_id']}",
        json={
            "answer": "Retry with sudo in terminal",
            "selected_option_id": "retry_with_sudo",
            "answer_is_secret": False,
        },
    )
    assert invalid.status_code == 400
    assert "password or choose a saved credential" in invalid.text

    resumed = client.post(
        f"/api/agent/clarification/{submitted.json()['request_id']}",
        json={
            "answer": "typed-sudo-password",
            "selected_option_id": "retry_with_sudo",
            "answer_is_secret": True,
        },
    )
    assert resumed.status_code == 200
    with client.stream("GET", resumed.json()["stream_url"]) as response:
        _ = response.read()

    resumed_context = runtime.contexts[-1]
    clarification = resumed_context["clarifications"][-1]
    assert clarification["answer"] == "[redacted]"
    assert clarification["selected_option_id"] == "retry_with_sudo"
    assert clarification["answer_is_secret"] is True
    assert resumed_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "typed-sudo-password"
    assert "typed-sudo-password" not in str(clarification)


def test_agent_ui_commit_message_clarification_ignores_parameter_noise(tmp_path) -> None:
    runtime = FakeCommitMessageClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_parameters_db_path=tmp_path / "parameters.db",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_tasks_db_path=tmp_path / "tasks.db",
                agent_monitors_db_path=tmp_path / "monitors.db",
                agent_ui_settings_db_path=tmp_path / "ui_settings.db",
            ),
            agent_runtime=runtime,
        )
    )

    submitted = client.post("/api/agent/request", json={"prompt": "push all git changes using ssh key sshgit_key"})
    assert submitted.status_code == 200
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    trace = client.get(f"/api/agent/trace/{submitted.json()['request_id']}").json()
    assert trace["clarification_request"]["missing_information"] == "Git commit message"
    assert trace["clarification_request"]["parameter_choices"][0]["key"] == "sshgit_key"

    resumed = client.post(
        f"/api/agent/clarification/{submitted.json()['request_id']}",
        json={"answer": "add various bug fixes", "answer_is_secret": False},
    )
    assert resumed.status_code == 200
    with client.stream("GET", resumed.json()["stream_url"]) as response:
        _ = response.read()

    resumed_context = runtime.contexts[-1]
    clarification = resumed_context["clarifications"][-1]
    assert clarification["answer"] == "add various bug fixes"
    assert clarification["answer_is_secret"] is False
    assert clarification["answer_redacted"] is False
    assert clarification["source"] == "freeform"
    assert USER_MACRO_PRIVATE_CONTEXT_KEY not in resumed_context


def test_agent_ui_clarification_resume_preserves_operator_conversation_id() -> None:
    runtime = FakeClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "create a conda environment", "agent_mode": "llm_operator"},
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    resumed = client.post(
        f"/api/agent/clarification/{submitted['request_id']}",
        json={"answer": "3.12", "selected_option_id": "py312"},
    )

    assert resumed.status_code == 200
    assert resumed.json()["conversation_id"] == submitted["conversation_id"]
    with client.stream("GET", resumed.json()["stream_url"]) as response:
        _ = response.read()
    assert runtime.contexts[-1]["agent_mode"] == "llm_operator"
    assert runtime.contexts[-1]["conversation_id"] == submitted["conversation_id"]


def test_agent_ui_clarification_resume_replays_saved_operator_loop_state() -> None:
    runtime = FakeOperatorLoopClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "repair the ambiguous target", "agent_mode": "llm_operator"},
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    trace = client.get(f"/api/agent/trace/{submitted['request_id']}").json()
    assert trace["clarification_required"] is True

    resumed = client.post(
        f"/api/agent/clarification/{submitted['request_id']}",
        json={"answer": "Target A", "selected_option_id": "a"},
    )

    assert resumed.status_code == 200
    with client.stream("GET", resumed.json()["stream_url"]) as response:
        _ = response.read()
    resumed_trace = client.get(resumed.json()["trace_url"]).json()

    assert resumed_trace["final_response"] == "resumed from saved operator state"
    assert runtime.replay_contexts
    assert runtime.replay_contexts[-1]["operator_resume_after_clarification"] is True
    assert runtime.replay_contexts[-1]["confirmation"] is True
    assert runtime.replay_contexts[-1]["clarifications"][-1]["answer"] == "Target A"


def test_agent_ui_clarification_resume_replays_saved_streaming_state_without_plan() -> None:
    runtime = FakeStreamingClarificationRuntime()
    client = TestClient(
        create_app(
            Settings(openai_compat_model_name="OpenFABRIC Echo"),
            agent_runtime=runtime,
        )
    )

    submitted = client.post(
        "/api/agent/request",
        json={
            "prompt": "search for docker-compose files and start services",
            "operator_execution_mode": "streaming",
        },
    ).json()
    with client.stream("GET", submitted["stream_url"]) as response:
        _ = response.read()

    trace = client.get(f"/api/agent/trace/{submitted['request_id']}").json()
    assert trace["clarification_required"] is True

    resumed = client.post(
        f"/api/agent/clarification/{submitted['request_id']}",
        json={"answer": "./webui/docker-compose.yml", "selected_option_id": "webui"},
    )

    assert resumed.status_code == 200
    with client.stream("GET", resumed.json()["stream_url"]) as response:
        _ = response.read()
    resumed_trace = client.get(resumed.json()["trace_url"]).json()

    assert resumed_trace["final_response"] == "resumed streaming step"
    assert runtime.replay_contexts
    assert runtime.replay_contexts[-1]["operator_resume_after_clarification"] is True
    assert runtime.replay_contexts[-1]["confirmation"] is True
    assert runtime.replay_contexts[-1]["clarifications"][-1]["answer"] == "./webui/docker-compose.yml"


def test_agent_trace_sink_receives_redacted_pipeline_events() -> None:
    store = AgentTraceStore()
    trace = store.create_request("hello")
    sink = AgentTraceSink(store, trace.request_id)
    observability = build_observability_context(
        trace.request_id,
        {
            "observability": {
                "enabled": True,
                "debug": True,
                "sinks": [sink],
            }
        },
    )

    observability.info(
        "execution",
        "execution.tool.input_resolved",
        "Tool input resolved",
        "Arguments were resolved.",
        {
            "api_key": "sk-testsecret0000000000",
            "tool_input": {"path": "."},
            "llm_context_usage": {
                "context_window_tokens": 32768,
                "used_tokens_estimate": 4096,
                "remaining_tokens_estimate": 28672,
            },
        },
        debug_only=True,
    )

    stored = store.get_trace(trace.request_id)

    assert stored is not None
    event = stored.events[-1]
    assert event.detail["api_key"] == "[redacted]"
    assert event.detail["llm_context_usage"]["context_window_tokens"] == 32768
    assert event.detail["llm_context_usage"]["used_tokens_estimate"] == 4096
    assert event.detail["llm_context_usage"]["remaining_tokens_estimate"] == 28672
    assert event.tool_input == {"path": "."}


def test_tracing_llm_client_records_prompt_response_and_model() -> None:
    store = AgentTraceStore()
    trace = store.create_request("classify")
    llm = TracingLLMClient(FakeLLMClient(), store, trace.request_id)

    response = llm.complete_json(
        "You are classifying a user prompt for an intelligent agent runtime.",
        {"title": "FakeSchema"},
    )

    assert response == {"ok": True, "schema": "FakeSchema"}
    stored = store.get_trace(trace.request_id)
    assert stored is not None
    llm_events = [event for event in stored.events if event.event_type.startswith("llm.")]
    assert [event.event_type for event in llm_events] == ["llm.request", "llm.response"]
    assert llm_events[0].stage == "prompt_classification"
    assert llm_events[0].detail["model"] == "fake-model"
    assert llm_events[0].detail["input_tokens_estimate"] > 0
    assert llm_events[0].detail["llm_stats_markdown"].startswith("| metric | value |")
    assert llm_events[0].llm_prompt
    assert llm_events[1].detail["output_tokens_estimate"] > 0
    assert llm_events[1].detail["total_tokens_estimate"] >= llm_events[1].detail["input_tokens_estimate"]
    assert llm_events[1].detail["duration_ms"] >= 0
    assert "| time |" in llm_events[1].detail["llm_stats_markdown"]
    assert llm_events[1].parsed_output == {"ok": True, "schema": "FakeSchema"}


def test_agent_trace_response_metrics_roll_up_llm_usage() -> None:
    store = AgentTraceStore()
    trace = store.create_request("classify")
    llm = TracingLLMClient(FakeLLMClient(), store, trace.request_id)

    llm.complete_json(
        "You are classifying a user prompt for an intelligent agent runtime.",
        {"title": "FakeSchema"},
    )
    store.complete_request(trace.request_id, "done")

    stored = store.get_trace(trace.request_id)
    assert stored is not None
    metrics = stored.response_metrics
    assert metrics is not None
    assert metrics["source"] == "llm_trace_events"
    assert metrics["input_tokens_estimate"] > 0
    assert metrics["output_tokens_estimate"] > 0
    assert metrics["total_tokens_estimate"] >= metrics["input_tokens_estimate"]
    assert metrics["llm_call_count"] == 1


def test_agent_trace_response_metrics_backfills_profile_tokens_from_llm_events() -> None:
    store = AgentTraceStore()
    trace = store.create_request("classify")
    request_id = trace.request_id
    store.append_event(
        AgentTraceEvent(
            request_id=request_id,
            stage="llm",
            event_type="llm.response",
            title="LLM response",
            detail={
                "call_id": "call-1",
                "input_tokens_estimate": 320,
                "output_tokens_estimate": 44,
                "duration_ms": 250.0,
            },
        )
    )
    store.append_event(
        AgentTraceEvent(
            request_id=request_id,
            stage="completed",
            event_type="profiling.completed",
            title="Runtime profile completed",
            detail={
                "runtime_profile": {
                    "total_duration_ms": 1000.0,
                    "llm_call_count": 1,
                    "llm_calls_by_stage": {"llm": 1},
                    "longest_llm_call": {
                        "input_tokens_estimate": 320,
                        "output_tokens_estimate": 44,
                    },
                }
            },
        )
    )
    store.complete_request(request_id, "done")

    stored = store.get_trace(request_id)
    assert stored is not None
    metrics = stored.response_metrics
    assert metrics is not None
    assert metrics["source"] == "runtime_profile"
    assert metrics["input_tokens_estimate"] == 320
    assert metrics["output_tokens_estimate"] == 44
    assert metrics["total_tokens_estimate"] == 364
    assert metrics["duration_ms"] == 1000.0
    assert metrics["duration_seconds"] >= 0


def test_tracing_llm_client_streams_delta_events_for_direct_answers() -> None:
    store = AgentTraceStore()
    trace = store.create_request("direct")
    llm = TracingLLMClient(
        FakeStreamingLLMClient(),
        store,
        trace.request_id,
        streaming_enabled=True,
    )

    response = llm.complete_json(
        "You are directly answering a user prompt for the OpenFabric agent runtime.",
        {"title": "DirectAnswerProposal"},
    )

    assert response == {"answer": "hello", "confidence": 0.9, "reason": "test"}
    stored = store.get_trace(trace.request_id)
    assert stored is not None
    llm_events = [event for event in stored.events if event.event_type.startswith("llm.")]
    assert [event.event_type for event in llm_events] == [
        "llm.request",
        "llm.response.delta",
        "llm.response",
    ]
    assert llm_events[1].stage == "direct_answer"
    assert llm_events[1].detail["user_facing_stream"] is True
    assert llm_events[1].detail["user_facing_text"] == "hello"
    assert llm_events[1].llm_response.endswith('"reason": "test"}')
    assert llm_events[2].parsed_output == response
    assert llm_events[0].detail["call_id"] == llm_events[1].detail["call_id"]
    assert llm_events[1].detail["call_id"] == llm_events[2].detail["call_id"]


def test_agent_trace_cancelled_request_ignores_late_llm_deltas() -> None:
    store = AgentTraceStore()
    trace = store.create_request("write mem.txt")
    final_response = "## Confirmation Denied\n\nNo confirmation-gated actions were executed."

    store.deny_confirmation(trace.request_id, final_response)
    before = store.get_trace(trace.request_id)
    assert before is not None

    store.append_event(
        AgentTraceEvent(
            request_id=trace.request_id,
            stage="llm",
            event_type="llm.response.delta",
            title="Late streamed delta",
            detail={"user_facing_text": "late text"},
            llm_response="late text",
        )
    )
    store.complete_request(trace.request_id, "late completion")

    after = store.get_trace(trace.request_id)
    assert after is not None
    assert after.status == "cancelled"
    assert after.final_response == final_response
    assert len(after.events) == len(before.events)
    assert all(event.event_type != "llm.response.delta" for event in after.events)


def test_tracing_labels_output_contract_overlap_separately() -> None:
    stage = infer_stage_from_prompt(
        "You are reviewing whether a downstream task is already satisfied by upstream declared outputs."
    )

    assert stage == "output_contract_overlap_review"


def test_tracing_labels_dag_review_before_dataflow_mentions() -> None:
    stage = infer_stage_from_prompt(
        "You are reviewing a sanitized action DAG for an intelligent agent runtime. "
        "The review schema includes dataflow_warnings."
    )

    assert stage == "dag_review"


def test_agent_debug_trace_keeps_full_llm_prompt_text() -> None:
    store = AgentTraceStore()
    trace = store.create_request("classify")
    llm = TracingLLMClient(FakeLLMClient(), store, trace.request_id)
    long_prompt = (
        "You are classifying a user prompt for an intelligent agent runtime.\n"
        + "keep-this-prompt-visible " * 120
        + "tail-marker"
    )

    llm.complete_json(long_prompt, {"title": "FakeSchema"})

    stored = store.get_trace(trace.request_id)
    assert stored is not None
    llm_request = next(event for event in stored.events if event.event_type == "llm.request")
    assert llm_request.llm_prompt == long_prompt
    assert "tail-marker" in llm_request.llm_prompt
    assert "[truncated]" not in llm_request.llm_prompt


def test_openai_compatible_chat_completion_endpoint_is_removed() -> None:
    client = _client()

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 404
