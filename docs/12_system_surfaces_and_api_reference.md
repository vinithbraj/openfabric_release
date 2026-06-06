# System Surfaces And API Reference

This reference summarizes the current browser surfaces and the HTTP/WebSocket
routes they use, including the non-streaming Integration Execution API for
external systems. The generated manual route inventory remains the exhaustive
source-inspected list; this page is the curated operator map.


## Browser Surfaces

| Surface | Route | Purpose |
| --- | --- | --- |
| Agent UI | `GET /agent-ui` | Primary desktop workspace with conversation, prompt composer, terminal, trace, visualization, drawers, quick controls, and immersive mode. |
| Client UI | `GET /agent-ui-client` | Lightweight chat/client surface that shares the runtime but hides admin panes and local operator controls. |
| Mobile UI | `GET /agent-ui-mobile` | Touch-first chat, gateway, tasks, monitors, parameters, notifications, and voice workflow. |
| Directory | `GET /directory` | Navigation hub for the local UI surfaces and manual-linked tools. |
| Mission Control Settings | `GET /settings` | Backend-owned settings registry, runtime controls, UI preferences, optimization profiles, and storage status. |
| Prompt Editor | `GET /prompt-editor` | Prompt template inspection, editing, rendering, reset, memory mode, and parameter mode. |
| Parameter Editor | `GET /parameter-editor` | Alias into Prompt Editor parameter mode for full Parameter Store editing. |
| Learning Ledger | `GET /learning-ledger` | Lessons, run evidence, capability insights, and proposal review. |
| Reliability | `GET /reliability` | Capability Reliability Kernel profiles, recovery runs, reports, and deterministic evals. |
| Website | `GET /website` on the website service | Standalone product website for technical evaluators, use cases, resources, and manual deep links. |
| Manual | `GET /manual` on the manual service | Standalone documentation workspace with authored docs, legacy docs, generated route references, generated environment references, and search. |


## Install And Service Startup Map

Docker all-services is the preferred product startup path after source sync:

```bash
./docker-all-services/build.sh
./docker-all-services/start.sh
```

It serves the Agent runtime on `8011`, the manual on `8013`, the website on
`8014`, and the audio runtime inside Docker networking. Gateways and LLM
servers are intentionally outside the compose stack because they own machine
shells, terminals, GPUs, model weights, and workspaces.

Required gateway validation:

```bash
./src/gateway_agent/install.sh
./src/gateway_agent/startup.sh
curl http://127.0.0.1:8787/healthz
```

Required LLM validation:

```bash
curl http://127.0.0.1:8000/v1/models -H 'Authorization: Bearer local'
```

Local vLLM setup uses the checked-in model launch scripts after creating a
`vllm` conda environment:

```bash
conda create -n vllm python=3.12 -y
conda activate vllm
pip install --upgrade uv
uv pip install vllm --torch-backend=auto
./src/llm/start-current-qwen3-coder-30b-a3b-awq.sh
```


## Agent Request Lifecycle Routes

| Route | Purpose |
| --- | --- |
| `POST /api/agent/request` | Submit a prompt plus request context and receive a request id/stream URL. |
| `GET /api/agent/stream/{request_id}` | Server-sent event stream for request progress, trace events, command capsules, and final state. |
| `GET /api/agent/trace/{request_id}` | Full trace payload for Developer Trace, Visualization, and support inspection. |
| `GET /api/agent/raw/{request_id}/{data_ref}` | Retrieve stored raw command/output artifacts by data reference. |
| `GET /api/agent/file` | Serve safe file references produced by display documents or artifacts. |
| `POST /api/agent/confirmation/{request_id}` | Approve or deny a pending confirmation. |
| `POST /api/agent/clarification/{request_id}` | Answer a pending clarification. |
| `POST /api/agent/continue/{request_id}` | Continue a paused/approved request path. |
| `POST /api/agent/stop/{request_id}` | Stop a running request and forward cancellation to runtime/gateway state. |

The Agent UI composer creates a chat-sourced durable task with
`POST /api/agent/tasks` and `start_now: true`. When the runtime is idle, the
backend starts the oldest queued task immediately and returns its request
stream. While active work is present, the task remains queued behind older
queued work.


## Integration Execution Routes

These routes let external systems submit agent prompts and receive blocking,
non-SSE result envelopes. They reuse the Agent UI request lifecycle, so
persisted runtime controls from `/api/agent/runtime-controls` and the Agent UI
settings database stay authoritative over backend-owned request context keys.
The OpenAPI schemas are exposed at `/openapi.json` with the `integration` tag.

| Route | Purpose |
| --- | --- |
| `POST /api/agent/integrations/execute` | Submit a prompt and wait for completion, failure, cancellation, confirmation, clarification, or timeout. |
| `GET /api/agent/integrations/results/{request_id}` | Return the latest result snapshot for a request id. |
| `POST /api/agent/integrations/confirm/{request_id}` | Approve or deny a confirmation-gated request and wait for the child result. |
| `POST /api/agent/integrations/clarify/{request_id}` | Answer a clarification-gated request and wait for the resumed result. |

Integration status values are `completed`, `failed`, `cancelled`, `running`,
`awaiting_confirmation`, and `awaiting_clarification`. Completed, failed,
cancelled, and action-needed results return HTTP `200`; requests still running
after `timeout_seconds` return HTTP `202` with the request id for later lookup.


## Settings And Runtime Control Routes

| Route | Purpose |
| --- | --- |
| `GET /api/agent/settings/registry` | Backend-owned registry for Mission Control sections, settings metadata, defaults, limits, optimization profiles, and setting targets. |
| `GET /api/agent/settings/config` | Defaults and limits used by settings clients. |
| `GET /api/agent/settings/preferences` | Shared persisted UI/runtime preference state. |
| `PUT /api/agent/settings/preferences` | Replace shared persisted preferences after rejecting removed legacy keys. |
| `GET /api/agent/runtime-controls` | Current public runtime controls. |
| `POST /api/agent/runtime-controls` | Update runtime controls such as policy, reasoning, repair, workflow, memory, LRN-T/LR-T, LR Direct, reliability, LLM endpoint, and auto immersive width. |
| `GET /api/agent/model/config` | Resolved model configuration and observed endpoint metadata. |
| `GET /api/agent/health` | Agent UI backend health and connection status. |
| `GET /api/agent/version` | Runtime version metadata. |
| `GET /api/agent/version/check` | Whether the checked-out runtime is newer than the running process. |

Current UI preferences include theme, agent mode, display name, number
animation, chat pop animation, pane visibility, chat bubbles, command capsule
expansion, browser notifications, notification sounds, voice input, capture
preset, and silence timeout. Pane topology is still browser-rendered, but
persistent preference truth is backend-owned.
The desktop composer's square terminal button is an alias for the same
backend-owned terminal visibility preference exposed in Settings. Mobile's
Terminal button opens `/agent-ui?terminal=1`, enables that preference, and
expands the desktop terminal pane.


## Agent UI Feature Routes

| Area | Routes |
| --- | --- |
| Directory | `GET /api/agent/directory` |
| Prompt history and macros | `GET/POST /api/agent/prompt-history`, `GET /api/agent/prompt-macros` |
| Prompt templates | `GET /api/agent/prompt-editor/templates`, `GET/PATCH /api/agent/prompt-editor/templates/{prompt_key}`, `POST /api/agent/prompt-editor/templates/{prompt_key}/reset`, `POST /api/agent/prompt-editor/templates/{prompt_key}/render` |
| Prompt editor modes | `GET /api/agent/prompt-editor/parameters`, `GET /api/agent/prompt-editor/memories`, `POST /api/agent/prompt-editor/memories`, `GET/PATCH /api/agent/prompt-editor/memories/{memory_id}` |
| Chats | `GET /api/agent/chats`, `GET /api/agent/chats/{conversation_id}`, `DELETE /api/agent/chats/{conversation_id}`, `POST /api/agent/conversation/summary` |
| Memory | `GET/POST /api/agent/memory`, `PATCH /api/agent/memory/{memory_id}`, `POST /api/agent/memory/{memory_id}/retire`, `POST /api/agent/memory/{memory_id}/restore`, `GET /api/agent/memory/{memory_id}/audit`, `POST /api/agent/memory/context`, `POST /api/agent/memory/feedback`, `POST /api/agent/memory/feedback/draft`, `POST /api/agent/memory/optimize`, `POST /api/agent/memory/proposals/{proposal_id}/apply` |
| Parameters | `GET/POST /api/agent/parameters`, `GET/PATCH/DELETE /api/agent/parameters/{key}`, `POST /api/agent/parameters/{key}/reveal`, `POST /api/agent/parameters/draft` |
| SQL discovery | `POST /api/agent/databases/discover`, `POST /api/agent/databases/discover/commit`, `POST /api/agent/sql/query` |
| Gateways | `GET/POST /api/agent/gateways`, `PATCH/DELETE /api/agent/gateways/{gateway_id}`, `POST /api/agent/gateways/{gateway_id}/health` |
| Terminal and LLM launch | `WebSocket /api/agent/terminal/ws`, `GET /api/agent/terminal/config`, `POST /api/agent/terminal/cwd`, `GET /api/agent/llm/status`, `GET /api/agent/llm/log`, `POST /api/agent/llm/start`, `POST /api/agent/llm/stop` |
| Events | `POST /api/agent/events/draft`, `POST /api/agent/events/from-prompt`, `GET/POST /api/agent/events`, `PATCH/DELETE /api/agent/events/{event_id}`, `POST /api/agent/events/{event_id}/trigger`, `GET /api/agent/events/{event_id}/runs`, `POST /api/agent/events/{event_id}/runs/{event_run_id}/cancel` |
| Tasks | `GET/POST /api/agent/tasks`, `DELETE /api/agent/tasks`, `GET/DELETE /api/agent/tasks/{task_id}`, `GET /api/agent/tasks/{task_id}/attempts`, `GET /api/agent/tasks/{task_id}/checkpoints`, `POST /api/agent/tasks/{task_id}/start`, `POST /api/agent/tasks/{task_id}/retry`, `POST /api/agent/tasks/{task_id}/cancel`, `POST /api/agent/tasks/{task_id}/archive` |
| Monitors | `POST /api/agent/monitors/draft`, `GET/POST /api/agent/monitors`, `GET /api/agent/monitors/{monitor_id}`, `GET /api/agent/monitors/{monitor_id}/observations`, `GET /api/agent/monitors/{monitor_id}/stream`, `POST /api/agent/monitors/{monitor_id}/start`, `POST /api/agent/monitors/{monitor_id}/pause`, `POST /api/agent/monitors/{monitor_id}/cancel`, `POST /api/agent/monitors/{monitor_id}/archive` |
| Notifications | `GET /api/agent/notifications`, `PATCH /api/agent/notifications/{notification_id}`, `POST /api/agent/notifications/mark-all-read` |
| Learning Ledger | `GET /api/agent/learning-ledger/summary`, `POST /api/agent/learning-ledger/clear`, runs, proposals, lessons, approve/reject/retire/restore/apply routes |
| Reliability | `GET /api/agent/reliability/profile`, `GET /api/agent/reliability/profile/{model_id}`, `GET /api/agent/reliability/runs`, `GET /api/agent/reliability/runs/{request_id}`, `GET /api/agent/reliability/runs/{request_id}/report.json`, `GET /api/agent/reliability/runs/{request_id}/report.md`, `GET /api/agent/reliability/evals`, `POST /api/agent/reliability/evals/run` |
| Cache, LRN-T/LR-T, LR Direct, names, allowlist | `GET /api/agent/cache/stats`, `POST /api/agent/cache/clear`, `GET /api/agent/lrnt/stats`, `POST /api/agent/lrnt/clear`, `POST /api/agent/lrdirect/clear`, `POST /api/agent/name/suggest`, `GET/POST /api/agent/command-allowlist`, `DELETE /api/agent/command-allowlist/{entry_id}` |

Task create payloads support `start_now`, `source`, `conversation_id`,
`agent_mode`, `gateway_id`, `context`, and optional `llm_model`. The selected
task model is persisted in task context and passed into the agent request when
the task starts. When a task starts or continues from a parent approval request
to a child execution request, the task payload's `current_request_id` points at
the request the UI should follow.


## Compatibility Routes

These remain available for compatibility clients and tests, but the Agent UI is
the primary interactive surface.

| Route | Purpose |
| --- | --- |
| `POST /compile` | Compile a request through compatibility semantics. |
| `POST /runs`, `POST /runs/stream`, `GET /runs`, `GET /runs/{run_id}` | Compatibility run API. |
| `POST /sessions`, `GET /sessions`, `GET /sessions/{session_id}` | Compatibility session API. |
| `GET /sessions/{session_id}/events`, `GET /sessions/{session_id}/events/stream` | Session event history and stream. |
| `POST /sessions/{session_id}/trigger`, `POST /sessions/{session_id}/resume` | Session execution/resume controls. |


## Manual Service Routes

The manual service is standalone and defaults to port `8013`.

| Route | Purpose |
| --- | --- |
| `GET /`, `GET /manual`, `GET /manual/` | Single-page manual workspace. |
| `GET /healthz` | Manual service health. |
| `GET /api/manual/catalog` | Authored, generated, architecture, and legacy document metadata. |
| `GET /api/manual/document/{doc_id}` | Rendered document HTML and metadata. |
| `GET /api/manual/search` | Search by query, tag, and kind. |
| `GET /api/manual/tags` | Tag counts. |
| `GET /api/manual/related/{doc_id}` | Related reading by tag and kind. |


## Website Service Routes

The product website service is standalone and defaults to port `8014`.

| Route | Purpose |
| --- | --- |
| `GET /`, `GET /website`, `GET /website/` | Product homepage for technical evaluators. |
| `GET /website/product` | Product architecture and capability map. |
| `GET /website/use-cases` | Use-case oriented product entry points. |
| `GET /website/resources` | Resource cards that deep-link into the manual. |
| `GET /healthz` | Website service health. |


## Audio Runtime Routes

The main Agent UI server proxies audio transcription routes when the audio
runtime is enabled.

| Route | Purpose |
| --- | --- |
| `GET /api/audio-transcriber/config` | Capture/transcriber configuration surfaced to the UI. |
| `POST /api/audio-transcriber/transcribe` | Audio transcription upload. |


## Gateway Service Routes

Each native gateway runs outside the Agent runtime container and defaults to
port `8787`.

| Route | Purpose |
| --- | --- |
| `GET /healthz` | Gateway health and configured node name. |
| `GET /capabilities` | Node, platform, shell, command profile, terminal support, and LLM runtime support. |
| `POST /exec` | Buffered validated command execution. |
| `POST /exec/stream` | Server-sent-event command execution stream. |
| `POST /exec/cancel` | Cancel an in-flight command by execution id. |
| `WebSocket /terminal/ws` | PTY-backed interactive terminal session. |
| `GET /llm/status`, `GET /llm/log`, `POST /llm/start`, `POST /llm/stop` | Gateway-managed local LLM process controls used by the Agent UI Settings drawer. |


## UI State And Drawer Behavior

The desktop Agent UI keeps pane topology stable: chat, terminal, trace,
visualization, drawers, client mode, mobile, and standalone pages remain
separate surfaces. The current visual system centralizes shared tokens in
`app.css` and aligns standalone pages that load it.

Immersive mode hides the normal topbar and non-chat panes, but keeps the
conversation header controls available. Quick controls, the settings burger,
the backend plug, and mirrored run/LLM/LR badges sit in a right-aligned
rail underneath the gateway picker while the model name stays out of the
immersive header; the settings drawer and backdrop remain
available as overlays in immersive mode. UI preferences include the chat pop
animation mode (`soft-rise`, `slide-up`, `slide-side`, `scale-pop`, `spring`,
`flip`, `skew-snap`, `blur-glow`, `drop-in`, or `none`, which is the default)
and are persisted through the same backend preferences path as other UI
settings.

Advisory terminal context is page-local and off by default. When the
terminal-header switch is enabled, Advisory requests can include terminal
cwd/session metadata and a capped visible terminal-output snapshot. This is
context only; Advisory requests still do not set `execute_in_terminal`.
