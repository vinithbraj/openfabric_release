"""Compatibility runtime engine that routes legacy surfaces into AgentRuntime.

Purpose:
    Preserve older run/session/CLI interfaces while using the current typed
    agent runtime as the real implementation.

Responsibilities:
    Extract prompt text from compatibility payloads, invoke ``AgentRuntime``,
    store in-memory session state, and expose lightweight compatibility events.

Data flow / Interfaces:
    Called by the FastAPI compatibility endpoints and CLI. Inputs are plain
    dictionaries containing fields such as ``task`` or ``prompt``. Outputs are
    JSON-serializable session dictionaries with ``final_output.content`` equal
    to the rendered runtime response.

Boundaries:
    This layer preserves compatibility envelopes, but planning, safety,
    confirmation, execution, and rendering all happen inside ``agent_runtime``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from agent_runtime.capabilities import build_default_registry
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.core.user_errors import user_error_detail, user_error_message
from agent_runtime.execution.engine import ExecutionEngine as AgentExecutionEngine
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.llm import OpenAICompatLLMClient
from agent_runtime.learning_ledger import AgentLearningLedgerStore
from agent_runtime.lrn_total_tasks import AgentLrnTotalTaskStore
from agent_runtime.memory import AgentMemoryStore
from agent_runtime.parameters import AgentParameterStore
from agent_runtime.output_pipeline.orchestrator import OutputPipelineOrchestrator
from agent_runtime.plan_cache import AgentPlanCacheStore
from agent_runtime.command_template_cache import AgentCommandTemplateCacheStore
from agent_runtime.computation_cache import AgentComputationCacheStore
from agent_runtime.api.config import Settings, get_settings
from agent_runtime.operator.command_exceptions import OperatorCommandAllowlistStore
from agent_runtime.prompts import configure_prompt_fetcher
from agent_runtime.reliability import AgentReliabilityStore


def _utc_now() -> str:
    """Return an ISO timestamp for run and event metadata."""

    return datetime.now(UTC).isoformat()


def extract_prompt(input_payload: dict[str, Any] | None) -> str:
    """Extract the user prompt from a compatibility input payload."""

    payload = dict(input_payload or {})
    for key in ("task", "prompt", "query", "message", "text", "input"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in payload.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(payload.get("task") or payload.get("prompt") or "").strip()


def _resolve_agent_gateway_settings(configured_settings: Settings) -> tuple[str, str | None]:
    """Resolve gateway node and URL for the agent runtime bridge."""

    default_node = str(configured_settings.resolved_default_node() or "localhost").strip() or "localhost"
    try:
        gateway_url = configured_settings.resolve_gateway_url(default_node)
    except ValueError:
        gateway_url = "http://127.0.0.1:8787" if default_node == "localhost" else None
    return default_node, gateway_url


def _allow_shell_execution_from_settings(configured_settings: Settings) -> bool:
    """Resolve whether read-only shell capabilities should be enabled."""

    shell_mode = str(configured_settings.shell_mode or "read_only").strip().lower() or "read_only"
    return shell_mode != "disabled"


def build_agent_runtime(settings: Settings) -> AgentRuntime:
    """Build the default typed agent runtime for compatibility surfaces."""

    configure_prompt_fetcher(settings.agent_prompts_db_path)
    default_gateway_node, resolved_gateway_url = _resolve_agent_gateway_settings(settings)
    command_allowlist_store = OperatorCommandAllowlistStore(settings.agent_command_allowlist_db_path)
    registry = build_default_registry()
    if bool(getattr(settings, "agent_learning_ledger_enabled", True)):
        try:
            ledger = AgentLearningLedgerStore(settings.agent_learning_ledger_db_path)
            registry.apply_manifest_overlays(ledger.approved_manifest_overlays())
        except Exception:
            pass
    result_store = InMemoryResultStore()
    execution_engine = AgentExecutionEngine(
        registry,
        {
            "workspace_root": str(settings.workspace_root),
            "allow_shell_execution": _allow_shell_execution_from_settings(settings),
            "allow_network_operations": False,
            "gateway_default_node": default_gateway_node,
            "gateway_url": resolved_gateway_url,
            "gateway_endpoints": dict(settings.gateway_endpoints),
            "gateway_timeout_seconds": settings.gateway_timeout_seconds,
            "max_output_preview_bytes": settings.shell_max_output_chars,
            "max_rows_returned": max(1, int(settings.sql_row_limit or 100)),
            "max_files_listed": 1000,
            "stop_on_error": True,
            "llm_operator_enabled": bool(settings.llm_operator_enabled),
            "llm_operator_requires_approval": bool(settings.llm_operator_requires_approval),
            "llm_operator_max_actions": int(settings.llm_operator_max_actions),
            "operator_policy_profile": str(settings.operator_policy_profile),
            "reasoning_profile": str(settings.reasoning_profile),
            "repair_profile": str(settings.repair_profile),
            "workflow_execution_mode": str(settings.workflow_execution_mode),
            "prompt_rephrase_enabled": bool(settings.prompt_rephrase_enabled),
            "response_streaming_enabled": bool(settings.response_streaming_enabled),
            "shell_input_bindings_mode": str(settings.shell_input_bindings_mode),
            "llm_operator_cardinality_judge_mode": str(
                settings.llm_operator_cardinality_judge_mode
            ),
            "llm_operator_verbose_enabled": bool(settings.llm_operator_verbose_enabled),
            "online_lookup_gemini_api_key": str(settings.online_lookup_gemini_api_key or ""),
            "online_lookup_gemini_model": str(settings.online_lookup_gemini_model or "gemini-2.5-flash"),
            "online_lookup_gemini_api_version": str(settings.online_lookup_gemini_api_version or "v1beta"),
            "online_ai_check_timeout_seconds": float(settings.online_ai_check_timeout_seconds),
            "online_ai_check_reuse_browser": bool(settings.online_ai_check_reuse_browser),
            "online_ai_check_headless": bool(settings.online_ai_check_headless),
            "online_ai_check_profile_dir": str(settings.online_ai_check_profile_dir),
            "llm_operator_step_validation_enabled": bool(
                settings.llm_operator_step_validation_enabled
            ),
            "llm_operator_formatter_source_preview_chars": int(
                settings.llm_operator_formatter_source_preview_chars
            ),
            "agent_clarification_mode": str(settings.agent_clarification_mode),
            "llm_operator_max_clarification_rounds": int(
                settings.llm_operator_max_clarification_rounds
            ),
            "reliability_mode": str(settings.reliability_mode),
            "reliability_db_path": str(settings.agent_reliability_db_path),
            "reliability_max_recovery_probes": int(settings.reliability_max_recovery_probes),
            "reliability_max_autonomous_repair_attempts": int(
                settings.reliability_max_autonomous_repair_attempts
            ),
            "reliability_weak_model_plan_action_cap": int(
                settings.reliability_weak_model_plan_action_cap
            ),
            "reliability_verifier_enforced": bool(settings.reliability_verifier_enforced),
            "reliability_approval_envelope_budget": int(
                settings.reliability_approval_envelope_budget
            ),
            "agent_memory_enabled": bool(settings.agent_memory_enabled),
            "agent_memory_prompt_max_chars": int(settings.agent_memory_prompt_max_chars),
            "agent_parameter_store_enabled": bool(settings.agent_parameter_store_enabled),
            "agent_parameter_prompt_max_chars": int(settings.agent_parameter_prompt_max_chars),
            "sql_agent_enabled": bool(settings.sql_agent_enabled),
            "sql_agent_chat_route_mode": str(settings.sql_agent_chat_route_mode or "agentic"),
            "sql_agent_default_limit": int(settings.sql_agent_default_limit),
            "sql_agent_max_rows": int(settings.sql_agent_max_rows),
            "sql_agent_max_repair_attempts": int(settings.sql_agent_max_repair_attempts),
            "agent_plan_cache_enabled": bool(settings.agent_plan_cache_enabled),
            "agent_plan_cache_prompt_max_chars": int(settings.agent_plan_cache_prompt_max_chars),
            "agent_plan_cache_similarity_threshold": float(
                settings.agent_plan_cache_similarity_threshold
            ),
            "agent_plan_cache_max_entries": int(settings.agent_plan_cache_max_entries),
            "agent_command_template_cache_enabled": bool(settings.agent_command_template_cache_enabled),
            "agent_command_template_cache_prompt_max_chars": int(
                settings.agent_command_template_cache_prompt_max_chars
            ),
            "agent_command_template_cache_similarity_threshold": float(
                settings.agent_command_template_cache_similarity_threshold
            ),
            "agent_command_template_cache_secondary_similarity_threshold": float(
                settings.agent_command_template_cache_secondary_similarity_threshold
            ),
            "agent_command_template_cache_max_entries": int(
                settings.agent_command_template_cache_max_entries
            ),
            "agent_computation_cache_enabled": bool(settings.agent_computation_cache_enabled),
            "agent_computation_cache_prompt_max_chars": int(settings.agent_computation_cache_prompt_max_chars),
            "agent_computation_cache_similarity_threshold": float(
                settings.agent_computation_cache_similarity_threshold
            ),
            "agent_computation_cache_max_entries": int(settings.agent_computation_cache_max_entries),
            "lrnt_enabled": bool(settings.lrnt_enabled),
            "lrnt_similarity_threshold": float(settings.lrnt_similarity_threshold),
            "lrnt_max_entries": int(settings.lrnt_max_entries),
            "lrdirect_enabled": bool(settings.lrdirect_enabled),
            "operator_command_allowlist_hashes": command_allowlist_store.enabled_hashes(),
        },
        result_store,
    )
    llm_client = OpenAICompatLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.default_model,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=settings.default_temperature,
        max_tokens=settings.llm_max_tokens or None,
    )
    return AgentRuntime(
        llm_client=llm_client,
        registry=registry,
        execution_engine=execution_engine,
        output_orchestrator=OutputPipelineOrchestrator(),
        memory_store=(
            AgentMemoryStore(settings.agent_memory_db_path)
            if settings.agent_memory_enabled
            else None
        ),
        parameter_store=(
            AgentParameterStore(settings.agent_parameters_db_path)
            if settings.agent_parameter_store_enabled
            else None
        ),
        plan_cache_store=AgentPlanCacheStore(
            settings.agent_plan_cache_db_path,
            max_entries=settings.agent_plan_cache_max_entries,
        ),
        lrn_total_task_store=AgentLrnTotalTaskStore(
            settings.agent_lrn_total_tasks_db_path,
            max_entries=settings.lrnt_max_entries,
        ),
        command_template_cache_store=AgentCommandTemplateCacheStore(
            settings.agent_command_template_cache_db_path,
            max_entries=settings.agent_command_template_cache_max_entries,
        ),
        computation_cache_store=AgentComputationCacheStore(
            settings.agent_computation_cache_db_path,
            max_entries=settings.agent_computation_cache_max_entries,
        ),
        reliability_store=AgentReliabilityStore(settings.agent_reliability_db_path),
    )


@dataclass
class CompatibilityEventStore:
    """Store compatibility run/session events in memory."""

    _events: list[dict[str, Any]] = field(default_factory=list)
    _next_id: int = 1
    _lock: Lock = field(default_factory=Lock)

    def append_event(self, session_id: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Append an event and return the stored event dictionary."""

        with self._lock:
            event = {
                "id": self._next_id,
                "session_id": session_id,
                "node_name": "agent_runtime",
                "event_type": event_type,
                "payload": dict(payload or {}),
                "created_at": _utc_now(),
            }
            self._next_id += 1
            self._events.append(event)
            return dict(event)

    def get_events_after(self, session_id: str, after_id: int | None = None) -> list[dict[str, Any]]:
        """Return events for a session after an optional event id."""

        after = int(after_id or 0)
        with self._lock:
            return [
                dict(event)
                for event in self._events
                if event["session_id"] == session_id and int(event["id"]) > after
            ]


class ExecutionEngine:
    """Compatibility engine backed by the typed agent runtime."""

    _TERMINAL_FAILURE_CATEGORIES = frozenset(
        {
            "execution_error",
            "runtime_error",
            "safety_block",
            "unexpected_error",
            "validation_error",
        }
    )

    def __init__(
        self,
        settings: Settings | None = None,
        agent_runtime: AgentRuntime | Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.runtime = agent_runtime or build_agent_runtime(self.settings)
        self.store = CompatibilityEventStore()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def _public_session(self, session: dict[str, Any]) -> dict[str, Any]:
        """Return one external-facing session payload without private runtime state."""

        return {key: value for key, value in dict(session).items() if not key.startswith("_")}

    def _runtime_context(
        self,
        *,
        confirmation: bool = False,
        event_callback=None,
    ) -> dict[str, Any]:
        """Build the runtime context used by compatibility routes."""

        context: dict[str, Any] = {
            "workspace_root": str(self.settings.workspace_root),
            "confirmation": confirmation,
            "allow_full_output_access": False,
        }
        if callable(event_callback):
            context["event_callback"] = event_callback
            context["observability"] = {"enabled": True, "debug": False}
        return context

    def _session_status_from_runtime(self, error_detail: dict[str, Any] | None = None) -> str:
        """Map runtime state into one compatibility session status."""

        if isinstance(error_detail, dict):
            return "failed"
        summary = getattr(self.runtime, "last_failure_summary", None)
        if isinstance(summary, dict) and summary.get("category") == "confirmation_required":
            return "awaiting_confirmation"
        return "completed"

    def _runtime_error_detail(
        self,
        *,
        content: str,
        context: dict[str, Any],
        error: Any = None,
    ) -> dict[str, Any] | None:
        if error is not None:
            return user_error_detail(
                error,
                stage="runtime",
                category="unexpected_error",
                context=context,
            )
        summary = getattr(self.runtime, "last_failure_summary", None)
        if not isinstance(summary, dict):
            return None
        category = str(summary.get("category") or "").strip()
        if category in {"confirmation_required", "clarification_required"}:
            return None
        if category not in self._TERMINAL_FAILURE_CATEGORIES and "error" not in category:
            return None
        metadata = summary.get("metadata")
        return user_error_detail(
            str(summary.get("reason") or content or "Request failed."),
            stage=str(summary.get("stage") or "runtime"),
            category=category or "runtime_error",
            context=context,
            metadata=metadata if isinstance(metadata, dict) else None,
            request_id=str(summary.get("request_id") or ""),
        )

    def validate_spec(self, spec_path: str) -> dict[str, Any]:
        """Return a compatibility validation result for a runtime spec path."""

        return {
            "valid": True,
            "spec_path": str(spec_path),
            "mode": "agent_runtime",
            "message": "Compatibility routes are backed by the typed agent runtime.",
        }

    def create_session(self, spec_path: str, input_payload: dict[str, Any] | None, **_: Any) -> dict[str, Any]:
        """Create a pending compatibility session."""

        session_id = uuid4().hex
        prompt = extract_prompt(input_payload)
        session = {
            "id": session_id,
            "spec_path": str(spec_path),
            "status": "pending",
            "input": dict(input_payload or {}),
            "prompt": prompt,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "final_output": None,
            "latest_snapshot": {},
            "_planning_trace": None,
        }
        with self._lock:
            self._sessions[session_id] = session
        self.store.append_event(session_id, "run.created", {"mode": "agent_runtime"})
        return self._public_session(session)

    def resume_session(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        """Resume a compatibility session through the typed runtime."""

        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Session not found: {session_id}")
            session = self._sessions[session_id]
            current_status = str(session.get("status") or "")
            confirmation = bool(kwargs.get("approve_dangerous") or kwargs.get("confirmation"))
            if current_status == "completed":
                return self._public_session(session)
            if current_status == "awaiting_confirmation" and not confirmation:
                return self._public_session(session)
            prompt = str(session.get("prompt") or "")
            planning_trace = session.get("_planning_trace")

        def _event_callback(text: str) -> None:
            if text:
                self.store.append_event(session_id, "agent_runtime.trace", {"content": text})

        runtime_context = self._runtime_context(
            confirmation=confirmation,
            event_callback=_event_callback,
        )
        try:
            if confirmation and planning_trace is not None and hasattr(self.runtime, "replay_from_trace"):
                content = self.runtime.replay_from_trace(
                    planning_trace,
                    context=runtime_context,
                )
            else:
                content = self.runtime.handle_request(
                    prompt,
                    context=runtime_context,
                )
            error_detail = self._runtime_error_detail(
                content=str(content or ""),
                context=runtime_context,
            )
        except Exception as exc:
            content = ""
            error_detail = self._runtime_error_detail(
                content="",
                context=runtime_context,
                error=exc,
            )

        status = self._session_status_from_runtime(error_detail)
        output_content = (
            user_error_message(error_detail, content)
            if isinstance(error_detail, dict)
            else str(content or "")
        )
        final_output = {"kind": "text", "content": output_content}
        latest_snapshot = {
            "session_id": session_id,
            "status": status,
            "final_output": final_output,
            "mode": "agent_runtime",
        }
        if isinstance(error_detail, dict):
            latest_snapshot["error_detail"] = error_detail

        with self._lock:
            session = self._sessions[session_id]
            session["status"] = status
            session["updated_at"] = _utc_now()
            session["final_output"] = final_output
            session["latest_snapshot"] = latest_snapshot
            session["error_detail"] = error_detail if isinstance(error_detail, dict) else None
            session["_planning_trace"] = (
                getattr(self.runtime, "last_planning_trace", None)
                if status == "awaiting_confirmation"
                else None
            )
            public_session = self._public_session(session)

        if status == "awaiting_confirmation":
            self.store.append_event(
                session_id,
                "confirmation.required",
                {"final_output": final_output},
            )
            self.store.append_event(
                session_id,
                "finalize.awaiting_confirmation",
                {"final_output": final_output},
            )
        elif status == "failed":
            self.store.append_event(
                session_id,
                "agent_runtime.failed",
                {"char_count": len(final_output["content"]), "error_detail": error_detail},
            )
            self.store.append_event(
                session_id,
                "finalize.failed",
                {"final_output": final_output, "error_detail": error_detail},
            )
        else:
            self.store.append_event(
                session_id,
                "agent_runtime.completed",
                {"char_count": len(final_output["content"])},
            )
            self.store.append_event(
                session_id,
                "finalize.completed",
                {"final_output": final_output},
            )
        return public_session

    def trigger_session(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        """Compatibility alias for ``resume_session``."""

        return self.resume_session(session_id, **kwargs)

    def run_spec(self, spec_path: str, input_payload: dict[str, Any] | None) -> dict[str, Any]:
        """Create and complete a compatibility run in one call."""

        session = self.create_session(spec_path, input_payload)
        return self.resume_session(str(session["id"]))

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent in-memory compatibility sessions."""

        with self._lock:
            sessions = list(self._sessions.values())[-int(limit or 50) :]
            return [self._public_session(session) for session in reversed(sessions)]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return one compatibility session plus its events."""

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            payload = self._public_session(session)
        payload["events"] = self.store.get_events_after(session_id)
        return {"session": payload, "latest_snapshot": dict(payload.get("latest_snapshot") or {})}

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Compatibility wrapper around ``list_sessions``."""

        return self.list_sessions(limit=limit)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Compatibility wrapper around ``get_session``."""

        return self.get_session(run_id)
