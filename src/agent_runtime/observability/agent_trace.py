"""Structured trace storage for the local agent debug UI."""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.core.ids import new_id
from agent_runtime.observability.events import PipelineEvent
from agent_runtime.observability.redaction import redact_debug_value
from agent_runtime.observability.sinks import EventSink


TraceLevel = Literal["debug", "info", "warning", "error"]
TraceStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


def utc_now_iso() -> str:
    """Return a UTC ISO timestamp for trace records."""

    return datetime.now(UTC).isoformat()


class AgentTraceEvent(BaseModel):
    """One operational event emitted for the local agent debug UI."""

    model_config = ConfigDict(extra="forbid")

    id: int = 0
    event_id: str = Field(default_factory=lambda: new_id("trace_evt"))
    request_id: str
    timestamp: str = Field(default_factory=utc_now_iso)
    stage: str
    level: TraceLevel = "info"
    event_type: str = "trace.event"
    title: str
    summary: str = ""
    detail: Any = None
    llm_prompt: str | None = None
    llm_response: Any = None
    parsed_output: Any = None
    tool_name: str | None = None
    tool_input: Any = None
    tool_output: Any = None
    validation_result: Any = None
    error: str | None = None


class AgentRequestTrace(BaseModel):
    """In-memory trace state for one submitted agent request."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    prompt: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    status: TraceStatus = "pending"
    final_response: str | None = None
    display_document: dict[str, Any] | None = None
    confirmation_required: bool = False
    confirmation_actions: list[dict[str, Any]] = Field(default_factory=list)
    clarification_required: bool = False
    clarification_request: dict[str, Any] | None = None
    raw_payloads: dict[str, dict[str, Any]] = Field(default_factory=dict)
    response_metrics: dict[str, Any] | None = None
    error: str | None = None
    error_detail: dict[str, Any] | None = None
    events: list[AgentTraceEvent] = Field(default_factory=list)


class AgentTraceStore:
    """Bounded in-memory trace store with blocking reads for SSE streams."""

    def __init__(self, max_requests: int = 100) -> None:
        self.max_requests = max(1, int(max_requests))
        self._traces: OrderedDict[str, AgentRequestTrace] = OrderedDict()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._next_event_id = 1

    def create_request(self, prompt: str) -> AgentRequestTrace:
        """Create one request trace and emit the initial request event."""

        request_id = new_id("req")
        with self._condition:
            trace = AgentRequestTrace(request_id=request_id, prompt=str(prompt or ""))
            self._traces[request_id] = trace
            self._evict_locked()
            self._append_locked(
                request_id,
                AgentTraceEvent(
                    request_id=request_id,
                    stage="request_received",
                    level="info",
                    event_type="request.received",
                    title="Request received",
                    summary="The local agent UI accepted the request.",
                    detail={"prompt_preview": str(prompt or "")[:240]},
                ),
            )
            self._condition.notify_all()
            return trace.model_copy(deep=True)

    def retained_request_count(self) -> int:
        """Return the number of request traces currently retained."""

        with self._lock:
            return len(self._traces)

    def get_trace(self, request_id: str) -> AgentRequestTrace | None:
        """Return one trace snapshot, if it is still retained."""

        with self._lock:
            trace = self._traces.get(request_id)
            return trace.model_copy(deep=True) if trace is not None else None

    def find_child_request_id(self, parent_request_id: str) -> str:
        """Return the newest retained request that records the given parent."""

        normalized = str(parent_request_id or "").strip()
        if not normalized:
            return ""
        with self._lock:
            for request_id, trace in reversed(self._traces.items()):
                for event in list(trace.events or []):
                    detail = event.detail if isinstance(event.detail, dict) else {}
                    if str(detail.get("parent_request_id") or "").strip() == normalized:
                        return str(request_id)
        return ""

    def mark_running(self, request_id: str) -> None:
        """Mark one request as actively running."""

        self.update_status(request_id, "running")
        self.append_event(
            AgentTraceEvent(
                request_id=request_id,
                stage="request_received",
                level="info",
                event_type="request.running",
                title="Request running",
                summary="The runtime worker started processing the request.",
            )
        )

    def complete_request(
        self,
        request_id: str,
        final_response: str,
        display_document: dict[str, Any] | None = None,
        *,
        confirmation_required: bool = False,
        confirmation_actions: list[dict[str, Any]] | None = None,
        clarification_required: bool = False,
        clarification_request: dict[str, Any] | None = None,
    ) -> None:
        """Mark one request as completed and store the final response."""

        with self._condition:
            trace = self._require_locked(request_id)
            if trace.status == "cancelled":
                return
            trace.status = "completed"
            trace.final_response = str(final_response or "")
            trace.display_document = display_document
            trace.confirmation_required = bool(confirmation_required)
            trace.confirmation_actions = [
                dict(action)
                for action in list(confirmation_actions or [])
                if isinstance(action, dict)
            ]
            trace.clarification_required = bool(clarification_required)
            trace.clarification_request = (
                dict(clarification_request)
                if isinstance(clarification_request, dict)
                else None
            )
            trace.updated_at = utc_now_iso()
            self._append_locked(
                request_id,
                AgentTraceEvent(
                    request_id=request_id,
                    stage="completed",
                    level="info",
                    event_type="request.completed",
                    title="Request completed",
                    summary="The runtime produced the final response.",
                    detail={
                        "content_length": len(trace.final_response or ""),
                        "display_document_available": display_document is not None,
                        "confirmation_required": trace.confirmation_required,
                        "confirmation_action_count": len(trace.confirmation_actions),
                        "clarification_required": trace.clarification_required,
                    },
                    parsed_output={
                        "final_response": trace.final_response,
                        "display_document": display_document,
                        "confirmation_required": trace.confirmation_required,
                        "confirmation_actions": trace.confirmation_actions,
                        "clarification_required": trace.clarification_required,
                        "clarification_request": trace.clarification_request,
                    },
                ),
            )
            trace.response_metrics = self._build_response_metrics(trace)
            self._condition.notify_all()

    def attach_raw_payloads(self, request_id: str, raw_payloads: dict[str, dict[str, Any]]) -> None:
        """Attach raw preview/full-payload records for the debug UI."""

        with self._condition:
            trace = self._require_locked(request_id)
            trace.raw_payloads = {
                str(key): dict(value)
                for key, value in dict(raw_payloads or {}).items()
                if isinstance(value, dict)
            }
            trace.updated_at = utc_now_iso()
            self._condition.notify_all()

    def update_final_response(self, request_id: str, final_response: str) -> None:
        """Replace the stored final response for a completed request trace."""

        with self._condition:
            trace = self._require_locked(request_id)
            trace.final_response = str(final_response or "")
            trace.updated_at = utc_now_iso()
            self._append_locked(
                request_id,
                AgentTraceEvent(
                    request_id=request_id,
                    stage="completed",
                    level="info",
                    event_type="request.final_response.updated",
                    title="Final response updated",
                    summary="The stored final response was enriched with captured runtime output.",
                    detail={"content_length": len(trace.final_response or "")},
                    parsed_output={"final_response": trace.final_response},
                ),
            )
            trace.response_metrics = self._build_response_metrics(trace)
            self._condition.notify_all()

    def get_raw_payload(self, request_id: str, data_ref: str) -> dict[str, Any] | None:
        """Return one stored raw payload record."""

        with self._lock:
            trace = self._traces.get(str(request_id or ""))
            if trace is None:
                return None
            payload = trace.raw_payloads.get(str(data_ref or ""))
            return dict(payload) if payload is not None else None

    def fail_request(
        self,
        request_id: str,
        error: str,
        *,
        error_detail: dict[str, Any] | None = None,
    ) -> None:
        """Mark one request as failed and store the safe error text."""

        with self._condition:
            trace = self._require_locked(request_id)
            if trace.status == "cancelled":
                return
            trace.status = "failed"
            trace.error = str(error or "Request failed.")
            trace.error_detail = dict(error_detail) if isinstance(error_detail, dict) else None
            trace.updated_at = utc_now_iso()
            self._append_locked(
                request_id,
                AgentTraceEvent(
                    request_id=request_id,
                    stage="completed",
                    level="error",
                    event_type="request.failed",
                    title="Request failed",
                    summary=(
                        str(trace.error_detail.get("likely_cause"))
                        if isinstance(trace.error_detail, dict)
                        and trace.error_detail.get("likely_cause")
                        else "The runtime worker failed before producing a final response."
                    ),
                    detail={"error_detail": trace.error_detail} if trace.error_detail else None,
                    error=trace.error,
                ),
            )
            trace.response_metrics = self._build_response_metrics(trace)
            self._condition.notify_all()

    def cancel_request(self, request_id: str, reason: str = "Stopped by user.") -> None:
        """Mark one request as cancelled by the local UI."""

        with self._condition:
            trace = self._require_locked(request_id)
            if trace.status in {"completed", "failed", "cancelled"}:
                return
            trace.status = "cancelled"
            trace.error = str(reason or "Stopped by user.")
            trace.updated_at = utc_now_iso()
            self._append_locked(
                request_id,
                AgentTraceEvent(
                    request_id=request_id,
                    stage="completed",
                    level="warning",
                    event_type="request.cancelled",
                    title="Request stopped",
                    summary="The local agent UI stopped this request.",
                    error=trace.error,
                ),
            )
            trace.response_metrics = self._build_response_metrics(trace)
            self._condition.notify_all()

    def deny_confirmation(
        self,
        request_id: str,
        final_response: str,
        *,
        partial_results_denied: bool = False,
    ) -> None:
        """Mark one confirmation-gated request as denied and terminal."""

        with self._condition:
            trace = self._require_locked(request_id)
            trace.status = "cancelled"
            trace.final_response = str(final_response or "")
            trace.display_document = None
            trace.confirmation_required = False
            trace.confirmation_actions = []
            trace.clarification_required = False
            trace.clarification_request = None
            trace.error = "Confirmation was denied."
            trace.updated_at = utc_now_iso()
            self._append_locked(
                request_id,
                AgentTraceEvent(
                    request_id=request_id,
                    stage="completed",
                    level="info",
                    event_type="confirmation.denied",
                    title="Confirmation denied",
                    summary=(
                        "The user denied continuing with partial command output."
                        if partial_results_denied
                        else "The user denied the pending confirmation-gated actions."
                    ),
                    detail={
                        "status": "cancelled",
                        "confirmation_required": False,
                        "confirmation_action_count": 0,
                        "partial_results_denied": bool(partial_results_denied),
                    },
                    parsed_output={
                        "status": "cancelled",
                        "final_response": trace.final_response,
                        "display_document": None,
                        "confirmation_required": False,
                        "confirmation_actions": [],
                        "clarification_required": False,
                        "clarification_request": None,
                    },
                ),
            )
            trace.response_metrics = self._build_response_metrics(trace)
            self._condition.notify_all()

    def update_status(self, request_id: str, status: TraceStatus) -> None:
        """Update one trace status without appending an event."""

        with self._condition:
            trace = self._require_locked(request_id)
            trace.status = status
            trace.updated_at = utc_now_iso()
            self._condition.notify_all()

    def append_event(self, event: AgentTraceEvent) -> AgentTraceEvent:
        """Append one event and return the stored event snapshot."""

        with self._condition:
            stored = self._append_locked(event.request_id, event)
            self._condition.notify_all()
            return stored.model_copy(deep=True)

    def events_after(self, request_id: str, after_id: int = 0) -> list[AgentTraceEvent]:
        """Return events for a request with numeric ids greater than ``after_id``."""

        with self._lock:
            trace = self._require_locked(request_id)
            return [
                event.model_copy(deep=True)
                for event in trace.events
                if int(event.id) > int(after_id or 0)
            ]

    def wait_for_events(
        self,
        request_id: str,
        after_id: int = 0,
        timeout_seconds: float = 5.0,
    ) -> list[AgentTraceEvent]:
        """Wait until new events are available, then return them."""

        with self._condition:
            self._require_locked(request_id)
            self._condition.wait_for(
                lambda: bool(self._events_after_locked(request_id, after_id))
                or self._is_terminal_locked(request_id),
                timeout=max(0.1, float(timeout_seconds)),
            )
            return [
                event.model_copy(deep=True)
                for event in self._events_after_locked(request_id, after_id)
            ]

    def _append_locked(self, request_id: str, event: AgentTraceEvent) -> AgentTraceEvent:
        trace = self._require_locked(request_id)
        if trace.status == "cancelled" and event.event_type == "llm.response.delta":
            return event.model_copy(update={"id": self._next_event_id, "request_id": request_id}, deep=True)
        sanitized = self._redact_event(event)
        stored = event.model_copy(
            update={
                "id": self._next_event_id,
                "request_id": request_id,
                "timestamp": sanitized.timestamp or utc_now_iso(),
                "summary": sanitized.summary,
                "detail": sanitized.detail,
                "llm_prompt": sanitized.llm_prompt,
                "llm_response": sanitized.llm_response,
                "parsed_output": sanitized.parsed_output,
                "tool_input": sanitized.tool_input,
                "tool_output": sanitized.tool_output,
                "validation_result": sanitized.validation_result,
                "error": sanitized.error,
            },
            deep=True,
        )
        self._next_event_id += 1
        trace.events.append(stored)
        trace.updated_at = utc_now_iso()
        self._traces.move_to_end(request_id)
        return stored

    def _events_after_locked(self, request_id: str, after_id: int) -> list[AgentTraceEvent]:
        trace = self._require_locked(request_id)
        return [event for event in trace.events if int(event.id) > int(after_id or 0)]

    def _is_terminal_locked(self, request_id: str) -> bool:
        trace = self._require_locked(request_id)
        return trace.status in {"completed", "failed", "cancelled"}

    def _require_locked(self, request_id: str) -> AgentRequestTrace:
        trace = self._traces.get(str(request_id or ""))
        if trace is None:
            raise KeyError(f"Trace not found: {request_id}")
        return trace

    def _evict_locked(self) -> None:
        while len(self._traces) > self.max_requests:
            self._traces.popitem(last=False)

    @staticmethod
    def _metric_number(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if number != number or number < 0:
            return 0.0
        return number

    @classmethod
    def _metric_int(cls, value: Any) -> int:
        return int(round(cls._metric_number(value)))

    @staticmethod
    def _timestamp_ms(value: Any) -> float | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp() * 1000.0

    @classmethod
    def _trace_duration_ms(cls, trace: AgentRequestTrace) -> float:
        started = cls._timestamp_ms(trace.created_at)
        finished = cls._timestamp_ms(trace.updated_at)
        if started is None or finished is None:
            return 0.0
        return max(0.0, finished - started)

    @staticmethod
    def _runtime_profile_from_trace(trace: AgentRequestTrace) -> dict[str, Any] | None:
        for event in reversed(trace.events):
            detail = event.detail if isinstance(event.detail, dict) else {}
            profile = detail.get("runtime_profile")
            if isinstance(profile, dict):
                return dict(profile)
        return None

    @classmethod
    def _llm_trace_event_metrics(cls, trace: AgentRequestTrace) -> dict[str, Any] | None:
        completed_calls: dict[str, dict[str, Any]] = {}
        sent_calls: dict[str, dict[str, Any]] = {}
        for event in trace.events:
            detail = event.detail if isinstance(event.detail, dict) else {}
            if not detail:
                continue
            call_id = str(detail.get("call_id") or event.event_id or "").strip()
            if not call_id:
                continue
            if event.event_type == "llm.request":
                sent_calls[call_id] = detail
            elif event.event_type in {"llm.response", "llm.error"}:
                completed_calls[call_id] = detail
        calls = completed_calls or sent_calls
        if not calls:
            return None
        input_tokens = sum(
            cls._metric_int(call.get("input_tokens_estimate"))
            for call in calls.values()
        )
        output_tokens = sum(
            cls._metric_int(call.get("output_tokens_estimate"))
            for call in calls.values()
        )
        duration_ms = sum(cls._metric_number(call.get("duration_ms")) for call in calls.values())
        return {
            "source": "llm_trace_events",
            "input_tokens_estimate": input_tokens,
            "output_tokens_estimate": output_tokens,
            "total_tokens_estimate": input_tokens + output_tokens,
            "duration_ms": round(duration_ms, 2) if duration_ms > 0 else None,
            "llm_call_count": len(calls),
            "estimated": True,
        }

    @classmethod
    def _runtime_profile_metrics(cls, trace: AgentRequestTrace) -> dict[str, Any] | None:
        profile = cls._runtime_profile_from_trace(trace)
        if not profile:
            return None
        llm_calls = [
            call
            for call in list(profile.get("llm_calls") or [])
            if isinstance(call, dict)
        ]
        input_tokens = cls._metric_int(profile.get("input_tokens_estimate_total"))
        output_tokens = cls._metric_int(profile.get("output_tokens_estimate_total"))
        if not input_tokens and llm_calls:
            input_tokens = sum(
                cls._metric_int(call.get("input_tokens_estimate"))
                for call in llm_calls
            )
        if not output_tokens and llm_calls:
            output_tokens = sum(
                cls._metric_int(call.get("output_tokens_estimate"))
                for call in llm_calls
            )
        llm_call_count = cls._metric_int(profile.get("llm_call_count")) or len(llm_calls)
        return {
            "source": "runtime_profile",
            "input_tokens_estimate": input_tokens,
            "output_tokens_estimate": output_tokens,
            "total_tokens_estimate": input_tokens + output_tokens,
            "duration_ms": round(cls._metric_number(profile.get("total_duration_ms")), 2),
            "llm_call_count": llm_call_count,
            "estimated": True,
        }

    @classmethod
    def _build_response_metrics(cls, trace: AgentRequestTrace) -> dict[str, Any]:
        profile_metrics = cls._runtime_profile_metrics(trace)
        event_metrics = cls._llm_trace_event_metrics(trace)
        if profile_metrics and event_metrics:
            profile_tokens = (
                cls._metric_int(profile_metrics.get("input_tokens_estimate")) +
                cls._metric_int(profile_metrics.get("output_tokens_estimate"))
            )
            event_tokens = (
                cls._metric_int(event_metrics.get("input_tokens_estimate")) +
                cls._metric_int(event_metrics.get("output_tokens_estimate"))
            )
            if not profile_tokens and event_tokens:
                profile_metrics = {
                    **profile_metrics,
                    "input_tokens_estimate": event_metrics.get("input_tokens_estimate"),
                    "output_tokens_estimate": event_metrics.get("output_tokens_estimate"),
                    "total_tokens_estimate": event_metrics.get("total_tokens_estimate"),
                    "llm_call_count": max(
                        cls._metric_int(profile_metrics.get("llm_call_count")),
                        cls._metric_int(event_metrics.get("llm_call_count")),
                    ),
                }
        metrics = profile_metrics or event_metrics or {
            "source": "trace_timestamps",
            "input_tokens_estimate": 0,
            "output_tokens_estimate": 0,
            "total_tokens_estimate": 0,
            "duration_ms": None,
            "llm_call_count": 0,
            "estimated": True,
        }
        duration_ms = (
            cls._metric_number(metrics.get("duration_ms"))
            or cls._trace_duration_ms(trace)
        )
        input_tokens = cls._metric_int(metrics.get("input_tokens_estimate"))
        output_tokens = cls._metric_int(metrics.get("output_tokens_estimate"))
        total_tokens = (
            cls._metric_int(metrics.get("total_tokens_estimate"))
            or input_tokens + output_tokens
        )
        return {
            "source": str(metrics.get("source") or "trace_timestamps"),
            "input_tokens_estimate": input_tokens,
            "output_tokens_estimate": output_tokens,
            "total_tokens_estimate": total_tokens,
            "duration_ms": round(duration_ms, 2),
            "duration_seconds": round(duration_ms / 1000.0, 3),
            "llm_call_count": cls._metric_int(metrics.get("llm_call_count")),
            "estimated": True,
        }

    @staticmethod
    def _redact_event(event: AgentTraceEvent) -> AgentTraceEvent:
        """Return an event sanitized with the standard observability policy."""

        return event.model_copy(
            update={
                "summary": redact_debug_value(event.summary),
                "detail": redact_debug_value(event.detail),
                "llm_prompt": redact_debug_value(event.llm_prompt),
                "llm_response": redact_debug_value(event.llm_response),
                "parsed_output": redact_debug_value(event.parsed_output),
                "tool_input": redact_debug_value(event.tool_input),
                "tool_output": redact_debug_value(event.tool_output),
                "validation_result": redact_debug_value(event.validation_result),
                "error": redact_debug_value(event.error),
            },
            deep=True,
        )


class AgentTraceSink(EventSink):
    """Observability sink that records pipeline events as agent trace events."""

    def __init__(self, store: AgentTraceStore, request_id: str) -> None:
        self.store = store
        self.request_id = request_id

    def emit(self, event: PipelineEvent) -> None:
        """Convert and store one sanitized pipeline event."""

        details = dict(event.details or {})
        capability_id = details.get("capability_id")
        operation_id = details.get("operation_id")
        tool_name = None
        if capability_id or operation_id:
            tool_name = ".".join(str(item) for item in (capability_id, operation_id) if item)

        validation_result = None
        if str(event.event_type).startswith("validation."):
            validation_result = {
                "event_type": event.event_type,
                "accepted": event.event_type.endswith(".accepted"),
                "details": details,
            }

        self.store.append_event(
            AgentTraceEvent(
                request_id=self.request_id,
                stage=event.stage,
                level=event.level,
                event_type=event.event_type,
                title=event.title,
                summary=event.summary,
                detail=details,
                parsed_output=details if event.event_type.startswith("llm.") else None,
                tool_name=tool_name,
                tool_input=details.get("arguments") or details.get("tool_input"),
                tool_output=(
                    details.get("result")
                    or details.get("tool_output")
                    or details.get("data_preview")
                ),
                validation_result=validation_result,
                error=details.get("error") if event.level == "error" else None,
            )
        )


__all__ = [
    "AgentRequestTrace",
    "AgentTraceEvent",
    "AgentTraceSink",
    "AgentTraceStore",
]
