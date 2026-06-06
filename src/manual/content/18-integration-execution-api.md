---
id: integration-execution-api
title: Integration Execution API
kind: manual
tags: [api, integration, integrations, external-systems, openapi, non-streaming, agent-runtime, command-output]
summary: A non-streaming OpenAPI-documented API for external systems that submit agent prompts and receive blocking execution envelopes.
order: 18
---

# Integration Execution API

The Integration Execution API lets external systems call the agent runtime
without opening the Agent UI event stream. It accepts agent prompts, waits for
a result or pause state, and returns one JSON envelope. External commands are
plain-language agent prompts, not raw shell commands.

These routes are exposed by the Agent UI server and are documented in
`/openapi.json` under the `integration` tag.

## Endpoints

| Route | Purpose |
| --- | --- |
| `POST /api/agent/integrations/execute` | Submit a prompt and wait for completion, failure, cancellation, confirmation, clarification, or timeout. |
| `GET /api/agent/integrations/results/{request_id}` | Read the latest result snapshot for a request id. |
| `POST /api/agent/integrations/confirm/{request_id}` | Approve or deny a confirmation-gated request and wait for the child result. |
| `POST /api/agent/integrations/clarify/{request_id}` | Answer a clarification-gated request and wait for the resumed result. |

`execute` accepts `prompt`, optional `context`, `agent_mode`, optional
`conversation_id`, optional `llm_model`, and `timeout_seconds`. The default
`agent_mode` is `llm_operator`. The default timeout is `120` seconds, and the
maximum is `1800` seconds.

## Runtime Controls

Integration requests reuse the existing Agent UI request lifecycle. Runtime
controls from `/api/agent/runtime-controls` and the persisted Agent UI settings
database remain authoritative. Callers can pass request context, but
backend-owned runtime control keys are ignored in favor of the saved settings
that the UI and settings APIs already maintain.

The same clarification, confirmation, safety, gateway, trace, display
document, response metrics, and raw-preview rules apply. Detailed stdout and
stderr are included only when existing trace/raw-preview settings make them
available. `final_response` is always included in the response envelope.

## Request Example

```json
{
  "prompt": "Check the repository status and summarize anything risky.",
  "context": {
    "source": "build-system"
  },
  "agent_mode": "llm_operator",
  "conversation_id": "release-2026-06-02",
  "llm_model": "local-coder",
  "timeout_seconds": 120
}
```

## Response Envelope

```json
{
  "request_id": "req_123",
  "conversation_id": "release-2026-06-02",
  "status": "completed",
  "trace_url": "/api/agent/trace/req_123",
  "final_response": "The repository has no high-risk changes.",
  "error": "",
  "outputs": [],
  "display_document": null,
  "response_metrics": {
    "duration_seconds": 4.2
  },
  "needs_action": null
}
```

## Statuses

| Status | Meaning |
| --- | --- |
| `completed` | The final response and any available artifacts are ready. |
| `failed` | Runtime execution failed and `error` describes the failure when available. |
| `cancelled` | The request was stopped or denied. |
| `running` | The timeout elapsed before a terminal or action-needed state. |
| `awaiting_confirmation` | The runtime needs an approval or denial before continuing. |
| `awaiting_clarification` | The runtime needs a clarification answer before continuing. |

Completed, failed, cancelled, and action-needed envelopes return HTTP `200`.
If the request is still running when `timeout_seconds` elapses, the API returns
HTTP `202` with the same envelope shape and `status: "running"`. Call
`GET /api/agent/integrations/results/{request_id}` later to retrieve the
current result.

## Confirmation And Clarification

When `needs_action.type` is `confirmation`, call
`POST /api/agent/integrations/confirm/{request_id}` with:

```json
{
  "action": "approve",
  "timeout_seconds": 120
}
```

Approving starts the approved child request and blocks for that child result.
Denying returns a cancelled envelope for the original request.

When `needs_action.type` is `clarification`, call
`POST /api/agent/integrations/clarify/{request_id}` with:

```json
{
  "answer": "Use Python 3.11",
  "selected_option_id": "py311",
  "timeout_seconds": 120
}
```

Clarification answers resume the saved lifecycle and block for the resumed
child result.

## V1 Boundaries

- The integration API is non-streaming; use `/api/agent/stream/{request_id}`
  only for the interactive Agent UI streaming path.
- No API key or token check is added in v1.
- No raw shell-command endpoint is exposed. External systems submit prompts,
  and the runtime keeps normal validation, approval, and gateway boundaries.
- OpenAPI schemas are generated automatically by FastAPI at `/openapi.json`.
