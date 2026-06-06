"""FastAPI API for the typed agent runtime.

Purpose:
    Expose the local Agent UI and preserve older compatibility routes such as
    ``/runs`` and ``/sessions``.

Responsibilities:
    Parse HTTP payloads, bootstrap the typed agent runtime, preserve
    compatibility envelopes, and route Agent UI requests.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent_runtime.api import __version__
from agent_runtime.api.agent_ui import register_agent_ui_routes
from agent_runtime.api.app_config import APP_CONFIG_PATH_ENV
from agent_runtime.api.config import Settings, get_settings
from agent_runtime.api.constants import DEFAULT_COMPAT_SPEC_PATH
from agent_runtime.api.runtime.engine import ExecutionEngine, build_agent_runtime
from agent_runtime.audio_transcriber import register_audio_transcriber_routes
from agent_runtime.capabilities.sql import (
    DatabaseDiscoveryCommitRequest,
    DatabaseDiscoveryRequest,
    SqlAgentRequest,
)
from agent_runtime.core.orchestrator import AgentRuntime


class RunRequest(BaseModel):
    """Compatibility request for run and session endpoints."""

    spec_path: str = DEFAULT_COMPAT_SPEC_PATH
    input: dict[str, Any] = Field(default_factory=dict)


class SessionTriggerRequest(BaseModel):
    """Compatibility request for triggering or resuming a session."""

    trigger: str = "manual"
    max_cycles: int | None = None
    approve_dangerous: bool = False


class ValidateRequest(BaseModel):
    """Compatibility request for the old compile endpoint."""

    spec_path: str


def json_dumps(payload: Any) -> str:
    """Serialize API payloads for JSON and SSE responses.

    Used by:
        Event-stream endpoints.
    """

    return json.dumps(payload, default=str, separators=(",", ":"))


def _format_sse(event_name: str, payload: dict[str, Any] | list[Any] | str) -> str:
    """Format a named server-sent event.

    Used by:
        Session and run streaming compatibility endpoints.
    """

    encoded = payload if isinstance(payload, str) else json_dumps(payload)
    return f"event: {event_name}\ndata: {encoded}\n\n"


def create_app(settings: Settings | None = None, agent_runtime: AgentRuntime | None = None) -> FastAPI:
    """Create the FastAPI application.

    Used by:
        ``aor serve`` and tests.
    """

    configured_settings = settings or get_settings(config_path=os.getenv(APP_CONFIG_PATH_ENV) or None)
    runtime = agent_runtime
    if runtime is None:
        runtime = build_agent_runtime(configured_settings)
    engine = ExecutionEngine(configured_settings, agent_runtime=runtime)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Start and stop optional background runtime workers."""

        scheduler = getattr(app.state, "agent_event_scheduler", None)
        if scheduler is not None and hasattr(scheduler, "start"):
            scheduler.start()
        try:
            yield
        finally:
            scheduler = getattr(app.state, "agent_event_scheduler", None)
            if scheduler is not None and hasattr(scheduler, "stop"):
                scheduler.stop(timeout=float(configured_settings.worker_join_timeout_seconds))

    app = FastAPI(title="OpenFABRIC Agent Runtime", version=__version__, lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = configured_settings
    app.state.agent_runtime = runtime
    register_agent_ui_routes(app, settings=configured_settings, agent_runtime=runtime)
    register_audio_transcriber_routes(app, settings=configured_settings)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Report API health."""

        return {"status": "ok", "mode": "agent_runtime"}

    @app.post("/api/agent/sql/query")
    def agent_sql_query(request: SqlAgentRequest) -> dict[str, Any]:
        """Run a dedicated SQL-agent query/discovery request."""

        handler = getattr(runtime, "handle_sql_query_request", None)
        if not callable(handler):
            raise HTTPException(status_code=503, detail="SQL agent is not available.")
        return handler(request.model_dump(mode="json"), {})

    @app.post("/api/agent/databases/discover")
    def agent_database_discovery(request: DatabaseDiscoveryRequest) -> dict[str, Any]:
        """Discover PostgreSQL databases and return Parameter Store drafts for review."""

        handler = getattr(runtime, "handle_database_discovery_request", None)
        if not callable(handler):
            raise HTTPException(status_code=503, detail="Database discovery is not available.")
        return handler(request.model_dump(mode="json"), {})

    @app.post("/api/agent/databases/discover/commit")
    def agent_database_discovery_commit(
        request: DatabaseDiscoveryCommitRequest,
    ) -> dict[str, Any]:
        """Commit reviewed database discovery drafts into Parameter Store."""

        handler = getattr(runtime, "handle_database_discovery_commit", None)
        if not callable(handler):
            raise HTTPException(status_code=503, detail="Database discovery is not available.")
        return handler(request.model_dump(mode="json"), {})

    @app.post("/compile")
    def compile_spec(request: ValidateRequest) -> dict[str, Any]:
        """Return a compatibility compile/validation response."""

        return engine.validate_spec(request.spec_path)

    @app.post("/sessions")
    def create_session(request: RunRequest, run_immediately: bool = True) -> dict[str, Any]:
        """Create a compatibility session and optionally complete it immediately."""

        session = engine.create_session(request.spec_path, request.input, trigger="manual")
        if not run_immediately:
            return session
        return engine.resume_session(str(session["id"]), trigger="manual")

    @app.get("/sessions")
    def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
        """List compatibility sessions."""

        return engine.list_sessions(limit=limit)

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        """Inspect one compatibility session."""

        payload = engine.get_session(session_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return payload

    @app.get("/sessions/{session_id}/events")
    def get_session_events(session_id: str, after_id: int | None = None) -> list[dict[str, Any]]:
        """Return session events after an optional cursor."""

        if engine.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return engine.store.get_events_after(session_id, after_id=after_id)

    @app.get("/sessions/{session_id}/events/stream")
    def stream_session_events(session_id: str, after_id: int | None = None) -> StreamingResponse:
        """Stream existing session events and stop at finalization."""

        if engine.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Session not found")

        def event_stream():
            cursor = after_id
            for event in engine.store.get_events_after(session_id, after_id=cursor):
                cursor = int(event["id"])
                yield _format_sse(str(event["event_type"]), event)
                if str(event.get("event_type") or "") == "finalize.completed":
                    return

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/sessions/{session_id}/trigger")
    def trigger_session(session_id: str, request: SessionTriggerRequest) -> dict[str, Any]:
        """Trigger a pending compatibility session."""

        try:
            return engine.trigger_session(
                session_id,
                trigger=request.trigger,
                approve_dangerous=request.approve_dangerous,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/sessions/{session_id}/resume")
    def resume_session(session_id: str, request: SessionTriggerRequest) -> dict[str, Any]:
        """Resume a pending compatibility session."""

        try:
            return engine.resume_session(
                session_id,
                trigger=request.trigger,
                approve_dangerous=request.approve_dangerous,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/runs")
    def create_run(request: RunRequest) -> dict[str, Any]:
        """Run one prompt through the typed agent runtime in a compatibility envelope."""

        return engine.run_spec(request.spec_path, request.input)

    @app.post("/runs/stream")
    def create_run_stream(request: RunRequest) -> StreamingResponse:
        """Stream compatibility run events for one typed-runtime run."""

        session = engine.create_session(request.spec_path, request.input)
        final_state = engine.resume_session(str(session["id"]))

        def event_stream():
            for event in engine.store.get_events_after(str(final_state["id"])):
                yield _format_sse(str(event["event_type"]), event)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/runs")
    def list_runs(limit: int = 50) -> list[dict[str, Any]]:
        """List compatibility runs."""

        return engine.list_runs(limit=limit)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        """Inspect one compatibility run."""

        payload = engine.get_run(run_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return payload

    return app
