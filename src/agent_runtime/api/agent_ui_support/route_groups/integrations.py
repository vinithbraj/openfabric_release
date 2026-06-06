"""Non-streaming integration API routes for external agent callers."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from agent_runtime.api.agent_ui_support.route_groups.shared import *


_INTEGRATION_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "awaiting_confirmation",
    "awaiting_clarification",
}


def _integration_status(trace: AgentRequestTrace) -> str:
    """Return the public integration status for one internal trace."""

    if trace.status == "completed":
        if trace.confirmation_required:
            return "awaiting_confirmation"
        if trace.clarification_required:
            return "awaiting_clarification"
        return "completed"
    if trace.status in {"failed", "cancelled"}:
        return str(trace.status)
    return "running"


def _integration_text(value: Any, *, limit: int = 12000) -> str | None:
    """Return a bounded text value for integration command output fields."""

    text = _event_text_preview(value, limit=limit)
    return text or None


def _integration_outputs(trace: AgentRequestTrace) -> list[AgentIntegrationOutput]:
    """Return compact raw payload previews and command diagnostics for integrations."""

    outputs: list[AgentIntegrationOutput] = []
    for data_ref, payload in dict(trace.raw_payloads or {}).items():
        if not isinstance(payload, dict):
            continue
        diagnostics = payload.get("diagnostics")
        preview = payload.get("preview")
        source = diagnostics if isinstance(diagnostics, dict) else preview
        source_payload = source if isinstance(source, dict) else {}
        exit_code: int | None = None
        if source_payload.get("exit_code") is not None:
            try:
                exit_code = int(source_payload.get("exit_code"))
            except (TypeError, ValueError):
                exit_code = None
        outputs.append(
            AgentIntegrationOutput(
                data_ref=str(payload.get("data_ref") or data_ref),
                source_node_id=(
                    str(payload.get("source_node_id"))
                    if payload.get("source_node_id") is not None
                    else None
                ),
                data_type=(
                    str(payload.get("data_type"))
                    if payload.get("data_type") is not None
                    else None
                ),
                status=(
                    str(source_payload.get("status"))
                    if source_payload.get("status") is not None
                    else None
                ),
                exit_code=exit_code,
                stdout=_integration_text(source_payload.get("stdout")),
                stderr=_integration_text(source_payload.get("stderr")),
                output=source_payload.get("output"),
                error=_integration_text(source_payload.get("error")),
                preview=preview,
                metadata=payload.get("metadata"),
                full_payload_available=bool(payload.get("full_payload_available")),
            )
        )
    return outputs


def _integration_needs_action(trace: AgentRequestTrace) -> AgentIntegrationNeedsAction | None:
    """Return confirmation or clarification metadata for gated integration requests."""

    if trace.confirmation_required:
        return AgentIntegrationNeedsAction(
            type="confirmation",
            confirmation_actions=[
                dict(action)
                for action in list(trace.confirmation_actions or [])
                if isinstance(action, dict)
            ],
        )
    if trace.clarification_required:
        return AgentIntegrationNeedsAction(
            type="clarification",
            clarification_request=(
                dict(trace.clarification_request)
                if isinstance(trace.clarification_request, dict)
                else None
            ),
        )
    return None


def register_integration_routes(ctx: AgentUiRouteContext) -> None:
    """Register synchronous integration routes on the Agent UI app."""

    app = ctx.app
    trace_store = ctx.trace_store
    state_store = ctx.state_store

    def _conversation_id(request_id: str, fallback: str = "") -> str:
        state = state_store.get(request_id)
        if state is not None and state.conversation_id:
            return str(state.conversation_id)
        return str(fallback or "")

    def _response_for_trace(
        trace: AgentRequestTrace,
        *,
        fallback_conversation_id: str = "",
    ) -> AgentIntegrationExecutionResponse:
        status = _integration_status(trace)
        return AgentIntegrationExecutionResponse(
            request_id=trace.request_id,
            conversation_id=_conversation_id(
                trace.request_id,
                fallback=fallback_conversation_id,
            ),
            status=status,  # type: ignore[arg-type]
            trace_url=f"/api/agent/trace/{trace.request_id}",
            final_response=str(trace.final_response or ""),
            error=str(trace.error or ""),
            error_detail=(
                dict(trace.error_detail)
                if isinstance(trace.error_detail, dict)
                else None
            ),
            outputs=_integration_outputs(trace),
            display_document=(
                dict(trace.display_document)
                if isinstance(trace.display_document, dict)
                else None
            ),
            response_metrics=(
                dict(trace.response_metrics)
                if isinstance(trace.response_metrics, dict)
                else None
            ),
            needs_action=_integration_needs_action(trace),
        )

    def _get_trace_or_404(request_id: str) -> AgentRequestTrace:
        trace = trace_store.get_trace(request_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="Integration result not found")
        return trace

    def _wait_for_result(
        request_id: str,
        *,
        timeout_seconds: float,
        fallback_conversation_id: str = "",
    ) -> tuple[AgentIntegrationExecutionResponse, bool]:
        timeout = max(0.0, min(1800.0, float(timeout_seconds)))
        deadline = time.monotonic() + timeout
        cursor = 0
        while True:
            trace = _get_trace_or_404(request_id)
            response = _response_for_trace(
                trace,
                fallback_conversation_id=fallback_conversation_id,
            )
            if response.status in _INTEGRATION_TERMINAL_STATUSES:
                return response, False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return response, True
            try:
                events = trace_store.wait_for_events(
                    request_id,
                    after_id=cursor,
                    timeout_seconds=min(remaining, 1.0),
                )
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail="Integration result not found",
                ) from exc
            for event in events:
                cursor = max(cursor, int(event.id))

    def _accepted_or_complete(
        response: AgentIntegrationExecutionResponse,
        timed_out: bool,
    ) -> AgentIntegrationExecutionResponse | JSONResponse:
        if timed_out:
            return JSONResponse(
                status_code=202,
                content=response.model_dump(mode="json"),
            )
        return response

    @app.post(
        "/api/agent/integrations/execute",
        response_model=AgentIntegrationExecutionResponse,
        responses={202: {"model": AgentIntegrationExecutionResponse}},
        tags=["integration"],
    )
    def execute_agent_integration(
        payload: AgentIntegrationExecutePayload,
    ) -> AgentIntegrationExecutionResponse | JSONResponse:
        """Submit one agent prompt and wait for a non-streaming integration result."""

        submitter = getattr(ctx, "submit_agent_request", None)
        if not callable(submitter):
            raise HTTPException(
                status_code=503,
                detail="Agent request handler is not available.",
            )
        submitted = submitter(
            AgentRequestPayload(
                prompt=payload.prompt,
                context=dict(payload.context or {}),
                agent_mode=payload.agent_mode,
                conversation_id=payload.conversation_id,
                llm_model=payload.llm_model,
            )
        )
        request_id = str(dict(submitted or {}).get("request_id") or "").strip()
        if not request_id:
            raise HTTPException(
                status_code=500,
                detail="Agent request did not return a request id.",
            )
        response, timed_out = _wait_for_result(
            request_id,
            timeout_seconds=payload.timeout_seconds,
            fallback_conversation_id=str(dict(submitted or {}).get("conversation_id") or ""),
        )
        return _accepted_or_complete(response, timed_out)

    @app.get(
        "/api/agent/integrations/results/{request_id}",
        response_model=AgentIntegrationExecutionResponse,
        tags=["integration"],
    )
    def get_agent_integration_result(request_id: str) -> AgentIntegrationExecutionResponse:
        """Return the current non-streaming integration result snapshot."""

        return _response_for_trace(_get_trace_or_404(request_id))

    @app.post(
        "/api/agent/integrations/confirm/{request_id}",
        response_model=AgentIntegrationExecutionResponse,
        responses={202: {"model": AgentIntegrationExecutionResponse}},
        tags=["integration"],
    )
    def confirm_agent_integration(
        request_id: str,
        payload: AgentIntegrationConfirmationPayload,
    ) -> AgentIntegrationExecutionResponse | JSONResponse:
        """Approve or deny a confirmation-gated integration request and wait for the result."""

        submitter = getattr(ctx, "submit_agent_confirmation", None)
        if not callable(submitter):
            raise HTTPException(
                status_code=503,
                detail="Agent confirmation handler is not available.",
            )
        submitted = submitter(
            request_id,
            AgentConfirmationPayload(
                action=payload.action,
                context=dict(payload.context or {}),
            ),
        )
        submitted_payload = dict(submitted or {})
        if payload.action == "deny":
            return AgentIntegrationExecutionResponse(
                request_id=request_id,
                conversation_id=str(submitted_payload.get("conversation_id") or ""),
                status="cancelled",
                trace_url=f"/api/agent/trace/{request_id}",
                final_response=str(submitted_payload.get("final_response") or ""),
                error="",
            )
        child_request_id = str(submitted_payload.get("request_id") or "").strip()
        if not child_request_id:
            raise HTTPException(
                status_code=500,
                detail="Confirmation did not return a request id.",
            )
        response, timed_out = _wait_for_result(
            child_request_id,
            timeout_seconds=payload.timeout_seconds,
            fallback_conversation_id=str(submitted_payload.get("conversation_id") or ""),
        )
        return _accepted_or_complete(response, timed_out)

    @app.post(
        "/api/agent/integrations/clarify/{request_id}",
        response_model=AgentIntegrationExecutionResponse,
        responses={202: {"model": AgentIntegrationExecutionResponse}},
        tags=["integration"],
    )
    def clarify_agent_integration(
        request_id: str,
        payload: AgentIntegrationClarificationPayload,
    ) -> AgentIntegrationExecutionResponse | JSONResponse:
        """Answer a clarification-gated integration request and wait for the result."""

        submitter = getattr(ctx, "submit_agent_clarification", None)
        if not callable(submitter):
            raise HTTPException(
                status_code=503,
                detail="Agent clarification handler is not available.",
            )
        submitted = submitter(
            request_id,
            AgentClarificationPayload(
                answer=payload.answer,
                selected_option_id=payload.selected_option_id,
                parameter_choice_id=payload.parameter_choice_id,
                parameter_key=payload.parameter_key,
                parameter_field=payload.parameter_field,
                answer_is_secret=payload.answer_is_secret,
                persist_for_event=payload.persist_for_event,
                context=dict(payload.context or {}),
            ),
        )
        submitted_payload = dict(submitted or {})
        child_request_id = str(submitted_payload.get("request_id") or "").strip()
        if not child_request_id:
            raise HTTPException(
                status_code=500,
                detail="Clarification did not return a request id.",
            )
        response, timed_out = _wait_for_result(
            child_request_id,
            timeout_seconds=payload.timeout_seconds,
            fallback_conversation_id=str(submitted_payload.get("conversation_id") or ""),
        )
        return _accepted_or_complete(response, timed_out)


__all__ = ["register_integration_routes"]
