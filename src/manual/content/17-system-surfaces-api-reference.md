---
id: system-surfaces-api-reference
title: System Surfaces And API Reference
kind: manual
tags: [agent-ui, api, routes, settings, manual-service, integration, external-systems, openapi, reference]
summary: A curated map of the current browser surfaces, Agent UI APIs, integration APIs, compatibility routes, manual service APIs, and UI preference behavior.
order: 17
---

# System Surfaces And API Reference

The generated Route Inventory in the manual is the exhaustive source-inspected
route list. This page is the curated operator map: the surfaces users open, the
API groups those surfaces depend on, and the current settings/immersive behavior.
For a page-by-page operator walkthrough, read [UI Pages Tour](19-ui-pages-tour.md).

## Browser Surfaces

| Surface | Route | Purpose |
| --- | --- | --- |
| Agent UI | `/agent-ui` | Primary desktop workspace with conversation, prompt composer, terminal, trace, visualization, drawers, quick controls, and immersive mode. |
| Client UI | `/agent-ui-client` | Lightweight chat/client surface that shares the runtime but hides admin panes and local operator controls. |
| Mobile UI | `/agent-ui-mobile` | Touch-first chat, gateway, tasks, monitors, parameters, notifications, and voice workflow. |
| Directory | `/directory` | Navigation hub for local UI surfaces and manual-linked tools. |
| Mission Control Settings | `/settings` | Backend-owned settings registry, runtime controls, UI preferences, optimization profiles, and storage status. |
| Prompt Editor | `/prompt-editor` | Prompt template inspection, editing, rendering, reset, memory mode, and parameter mode. |
| Parameter Editor | `/parameter-editor` | Alias into Prompt Editor parameter mode for full Parameter Store editing. |
| Learning Ledger | `/learning-ledger` | Lessons, run evidence, capability insights, and proposal review. |
| Reliability | `/reliability` | Capability Reliability Kernel profiles, recovery runs, reports, and deterministic evals. |
| Manual | `/manual` on the manual service | Authored docs, architecture guides, legacy docs, generated source references, generated route references, and search. |

## Request Lifecycle Routes

| Route | Purpose |
| --- | --- |
| `POST /api/agent/request` | Submit a prompt plus request context and receive a request id/stream URL. |
| `GET /api/agent/stream/{request_id}` | Server-sent request progress, trace events, command capsules, and final state. |
| `GET /api/agent/trace/{request_id}` | Full trace payload for Developer Trace, Visualization, and support inspection. |
| `GET /api/agent/raw/{request_id}/{data_ref}` | Stored raw command/output artifact retrieval. |
| `GET /api/agent/file` | Safe file references produced by display documents or artifacts. |
| `POST /api/agent/confirmation/{request_id}` | Approve or deny a pending confirmation. |
| `POST /api/agent/clarification/{request_id}` | Answer a pending clarification. |
| `POST /api/agent/continue/{request_id}` | Continue a paused/approved request path. |
| `POST /api/agent/stop/{request_id}` | Stop a running request and forward cancellation to runtime/gateway state. |

The Agent UI composer creates a chat-sourced durable task with
`POST /api/agent/tasks` and `start_now: true`. When the runtime is idle, the
backend starts the oldest queued task immediately and returns its request
stream. If active work is present, the same Run button changes to Queue and the
task waits behind older queued work.

## Integration Execution Routes

These OpenAPI-documented routes let external systems submit agent prompts and
receive non-streaming result envelopes. They reuse the same Agent UI request
lifecycle, so persisted runtime controls and Agent UI settings remain
authoritative over backend-owned request context keys.

| Route | Purpose |
| --- | --- |
| `POST /api/agent/integrations/execute` | Submit a prompt and block until completion, failure, cancellation, action-needed state, or timeout. |
| `GET /api/agent/integrations/results/{request_id}` | Return the latest non-streaming result snapshot. |
| `POST /api/agent/integrations/confirm/{request_id}` | Approve or deny a confirmation-gated integration request and wait for the child result. |
| `POST /api/agent/integrations/clarify/{request_id}` | Answer a clarification-gated integration request and wait for the resumed result. |

The public status values are `completed`, `failed`, `cancelled`, `running`,
`awaiting_confirmation`, and `awaiting_clarification`. Completed, failed,
cancelled, and action-needed envelopes return HTTP `200`; a still-running
request after `timeout_seconds` returns HTTP `202`. Full schemas are available
in `/openapi.json` under the `integration` tag.

## Settings And Runtime Controls

| Route | Purpose |
| --- | --- |
| `GET /api/agent/settings/registry` | Mission Control sections, setting metadata, defaults, limits, optimization profiles, and setting targets. |
| `GET /api/agent/settings/config` | Defaults and limits for settings clients. |
| `GET /api/agent/settings/preferences` | Shared persisted UI/runtime preference state. |
| `PUT /api/agent/settings/preferences` | Replace persisted preferences after rejecting removed legacy keys. |
| `GET /api/agent/runtime-controls` | Current public runtime controls. |
| `POST /api/agent/runtime-controls` | Update policy, reasoning, repair, workflow, memory, LRN-T/LR-T, LR Direct, reliability, LLM endpoint, and auto immersive width controls. |
| `GET /api/agent/model/config` | Resolved model configuration and observed endpoint metadata. |
| `GET /api/agent/health` | Agent UI backend health. |
| `GET /api/agent/version` | Runtime version metadata. |
| `GET /api/agent/version/check` | Whether the checked-out runtime is newer than the running process. |

UI preferences currently include theme, agent mode, display name, number
animation, chat pop animation, pane visibility, chat bubbles, command capsule
expansion, browser notifications, notification sounds, voice input, capture
preset, and silence timeout.
The desktop composer's square terminal button is an alias for the same
backend-persisted terminal visibility preference exposed in Settings. Mobile's
Terminal button opens `/agent-ui?terminal=1`, enables that preference, and
expands the desktop terminal pane.

## Feature API Groups

| Area | Routes |
| --- | --- |
| Directory | `GET /api/agent/directory` |
| Prompt history and macros | `GET/POST /api/agent/prompt-history`, `GET /api/agent/prompt-macros` |
| Prompt templates | `GET /api/agent/prompt-editor/templates`, `GET/PATCH /api/agent/prompt-editor/templates/{prompt_key}`, reset, render |
| Prompt editor modes | prompt-editor parameters and memories routes |
| Chats | `GET /api/agent/chats`, `GET /api/agent/chats/{conversation_id}`, `DELETE /api/agent/chats/{conversation_id}`, `POST /api/agent/conversation/summary` |
| Memory | list/create/update, retire/restore, audit, context draft, feedback draft, optimize, proposal apply |
| Parameters | list/create/read/update/delete, reveal, draft |
| SQL discovery | `/api/agent/databases/discover`, `/api/agent/databases/discover/commit`, `/api/agent/sql/query` |
| Gateways | list/create/update/delete/health |
| Terminal and LLM launch | `WebSocket /api/agent/terminal/ws`, terminal config/cwd, LLM status/log/start/stop |
| Events | draft/from-prompt, list/create/update/delete, trigger, run history, run cancel |
| Tasks | list/create/delete, read, attempts, checkpoints, start, retry, cancel, archive |
| Monitors | draft, list/create/read, observations, stream, start, pause, cancel, archive |
| Notifications | list, update, mark all read |
| Learning Ledger | summary, clear, runs, proposals, lessons, approve/reject/retire/restore/apply |
| Reliability | profiles, runs, reports, eval list, eval run |
| Cache, LRN-T/LR-T, LR Direct, names, allowlist | cache stats/clear, LRN-T stats/clear, LR Direct clear, name suggestions, command allowlist list/create/delete |

Task create payloads support `start_now`, `source`, `conversation_id`,
`agent_mode`, gateway/routing context, terminal context in `context`, and
optional `llm_model`. The selected task model is persisted and passed into the
request payload when the task starts. Task payloads expose `current_request_id`
so attached UIs can follow background approval continuations to child requests.

## Compatibility Routes

Compatibility routes remain available for older clients and tests:

- `/compile`
- `/runs`, `/runs/stream`, `/runs/{run_id}`
- `/sessions`, `/sessions/{session_id}`
- `/sessions/{session_id}/events`
- `/sessions/{session_id}/events/stream`
- `/sessions/{session_id}/trigger`
- `/sessions/{session_id}/resume`

## Manual Service Routes

The manual service is standalone and defaults to port `8013`.

| Route | Purpose |
| --- | --- |
| `GET /`, `GET /manual`, `GET /manual/` | Single-page manual workspace. |
| `GET /healthz` | Manual service health. |
| `GET /api/manual/catalog` | Authored, generated, architecture, and legacy metadata. |
| `GET /api/manual/document/{doc_id}` | Rendered document HTML and metadata. |
| `GET /api/manual/search` | Search by query, tag, and kind. |
| `GET /api/manual/tags` | Tag counts. |
| `GET /api/manual/related/{doc_id}` | Related reading by tag and kind. |

## Audio Routes

The main Agent UI server proxies audio transcription routes when enabled:

- `GET /api/audio-transcriber/config`
- `POST /api/audio-transcriber/transcribe`

## Current UI State Rules

The desktop Agent UI keeps pane topology stable: chat, terminal, trace,
visualization, drawers, client mode, mobile, and standalone pages remain
separate surfaces. Shared desktop styling is centralized in `app.css`; mobile
keeps touch-first rules in `mobile.css`.

Immersive mode hides the normal topbar and non-chat panes, but keeps the
conversation header controls available. Quick controls, the settings burger,
the backend plug, and mirrored run/LLM/LR badges sit in a right-aligned rail
underneath the gateway picker. The model name stays out of that immersive
header, and the settings drawer and backdrop remain available as overlays in
immersive mode.

Chat pop animation is a backend-persisted UI preference with these modes:
`soft-rise`, `slide-up`, `slide-side`, `scale-pop`, `spring`, `flip`,
`skew-snap`, `blur-glow`, `drop-in`, and `none`. The default is `none`, so
chat messages render as rectangular cards unless motion is selected.

Advisory terminal context is page-local and off by default. When the
terminal-header switch is enabled, Advisory requests can include terminal
cwd/session metadata and a capped visible terminal-output snapshot. This is
context only; Advisory requests still do not set `execute_in_terminal`.
