---
id: v1-live-assessment
title: V1 Live Assessment
kind: manual
tags: [v1, assessment, live-eval, readiness, release]
summary: A candid live assessment of the current OpenFabric deployed instance, including V1 feature lock recommendations, blockers, and launch criteria.
order: 20
---

# V1 Live Assessment

Assessment date: June 4, 2026.

Instance tested: the currently running local deployment in this workspace.

Runtime under test:

| Surface | Result |
| --- | --- |
| Agent UI | `https://127.0.0.1:8011/agent-ui` |
| Audio runtime | `http://127.0.0.1:8012/healthz` |
| Manual | `http://127.0.0.1:8013/manual` |
| Website | `http://127.0.0.1:8014/website` |
| Local gateway | `http://127.0.0.1:8787` |
| Runtime version | `1.0 3cfa900`, dirty worktree |
| Active model | `stelterlab/Qwen3-Coder-30B-A3B-Instruct-AWQ` via `http://127.0.0.1:8000/v1` |

## Executive Read

OpenFabric is a credible V1 candidate for local-first agent operations, but the current deployed instance is not ready to present as a frictionless public V1 without cleanup and a tighter launch profile.

The strongest parts are the breadth of product surfaces, gateway execution, traceability, approval gates, manual coverage, audio readiness, and the operator cockpit. The weakest parts are first-run latency, product grounding in Advisory answers, state hygiene, failing tests, and a confirmation-state mismatch after denial.

Recommended V1 posture: ship as an operator-focused local runtime after closing the blockers below. Do not position the current profile as a polished consumer chat agent yet.

## Scorecard

| Criterion | Score | Assessment |
| --- | ---: | --- |
| Service health | 8/10 | Core services are alive. Agent UI is HTTPS-only on `8011`; plain HTTP gives an empty reply, which should be clearer for users. |
| Browser UI | 8/10 | Desktop, mobile, Directory, Settings, Prompt Editor, Learning Ledger, Reliability, Manual, and Website all loaded with no Playwright console or page errors. |
| Agent answer quality | 5/10 | Advisory mode was fast, but gave a generic cloud/edge orchestration answer that conflicts with OpenFabric's local-first product story. |
| Agentic execution | 5/10 | Read-only repo inspection executed six commands, but the synchronous run timed out at 180 seconds and required manual stop. |
| Safety gates | 7/10 | Mutating work paused for confirmation and did not create the probe file. The approval request took 52.7 seconds and only covered the first `mkdir -p` step. |
| Observability | 9/10 | Trace, command capsules, learning, reliability, and route inventory are unusually strong for V1. |
| Onboarding clarity | 6/10 | The website and manual are aligned, but the live settings/state are too power-user-heavy for a first user. |
| Release readiness | 6.5/10 | Broad and promising, but not ready to lock as public V1 until tests pass and defaults/state are cleaned. |

## Live Test Matrix

| Area | What Was Tested | Result |
| --- | --- | --- |
| Service reachability | Health and page probes for Agent UI, audio, manual, website, gateway | Passed. Agent UI requires HTTPS. |
| Browser smoke | Playwright load checks on desktop/mobile Agent UI and support pages | Passed. Screenshots saved under `artifacts/live_eval/screenshots/`. |
| Advisory mode | First-time user explanation, no command execution | Completed in 1.27 seconds, but answer was product-inaccurate. |
| Agentic read-only task | Repo inspection using read-only shell commands | Timed out at 180 seconds while still running; manually stopped. |
| Gateway direct exec | Direct local gateway Python/pwd command | Passed quickly. |
| Mutating safety gate | Request to create `artifacts/live_eval/safety_probe.txt` with auto-approve disabled | Correctly paused for confirmation; file was not created; denial succeeded. |
| Manual/search | Catalog and search for approval docs | Passed; 45 manual docs available. |
| Audio | Transcriber config, model, binary, ffmpeg, service URL | Ready. |
| Test suite | `.venv/bin/pytest -q` | 1,359 passed, 5 skipped, 4 failed. |

## Findings

### What Is Working

The deployed system is real, not a mock. The browser surfaces boot, the local gateway executes commands, the model endpoint is discovered, the audio runtime is ready, and the docs/website/manual ecosystem is coherent.

The UI has strong operator ergonomics: main Agent UI, mobile UI, Directory, Settings, Prompt Editor, Learning Ledger, Reliability, Manual, and Website are all reachable. The live trace and command capsule model are strong differentiators.

The confirmation gate did the right safety thing. With request-level auto-approve disabled, the agent paused before a low-risk mutating command and did not write the test file.

### What Is Not Ready

Advisory mode is not sufficiently grounded in the product. It described OpenFabric as a cloud/edge AI model orchestration platform instead of a local-first typed agent runtime. This is exactly the kind of first answer a new user may ask for, so it needs product grounding before it can be trusted as a front-door assistant.

Agentic latency is too high in the current profile. A normal read-only repo-inspection prompt timed out after 180 seconds, generated 1,242 trace events, and had to be stopped. It did execute read-only commands, but it did not produce the requested answer in a user-acceptable window.

The confirmation flow is safe but slow and fragmented. It took 52.7 seconds and 8 LLM calls to ask approval for `mkdir -p`. After denial, the integration response reported `cancelled`, while the trace still showed `completed`, `confirmation_required: true`, and the old confirmation text plus a later `confirmation.denied` event. That state mismatch should be treated as a V1 blocker.

The deployed state is not clean for users. There are 803 active memory records, 7,760 learning-ledger runs, 7,183 reliability events, historical tasks/events, and 50 unread notifications. This may be useful for development, but it should not ship as a default user/demo state.

The automated test suite is not green. Four failures remain:

| Test | Failure Theme |
| --- | --- |
| `tests/test_audio_transcriber.py::test_agent_audio_proxy_uses_runtime_control_endpoint` | Runtime-controls response no longer includes `audio_transcriber_service_url`. |
| `tests/test_execution_engine.py::test_operator_shell_command_trace_events_include_gateway_metadata` | Expected command trace events were not emitted. |
| `tests/test_llm_operator_pipeline.py::test_standard_agent_ordinary_request_routes_to_shared_operator_planner` | Output formatting contract drifted into fenced text. |
| `tests/test_reliability_kernel.py::test_reliability_api_ui_and_trace_summary` | Reliability runtime-control validation rejected expected controls. |

## V1 Feature Lock

Lock these as V1 user-facing features:

| Feature | V1 Status | Notes |
| --- | --- | --- |
| Local Agent UI | Lock | Core desktop cockpit for prompts, approvals, trace, terminal, and evidence. |
| Local gateway execution | Lock | Direct gateway health and exec path are solid. |
| Command capsules and approval gates | Lock | Safety model works; polish latency and denial state. |
| HTTPS local UI | Lock with onboarding note | Needed for browser microphone permissions. Add clearer HTTP-to-HTTPS messaging. |
| Manual and Website | Lock | Messaging is strong and local-first. |
| Directory | Lock | Useful route inventory and navigation hub. |
| Settings/Mission Control | Lock | Keep profiles prominent; hide sharp advanced knobs behind progressive disclosure. |
| Prompt Editor | Lock for operators | Powerful, but should be documented as advanced. |
| Audio dictation | Lock | Runtime config is ready. |
| Integration execution API | Lock | Useful for external systems; needs state consistency fix after confirmation denial. |
| Tasks and scheduled events | Lock as advanced V1 | Useful, but default demo state must be clean and auto-approval defaults should be conservative. |
| Memory and Parameter Store | Lock as advanced V1 | Keep memory enabled, but ship a minimal seed and clear stale learned records. |
| Learning Ledger and Reliability | Lock as operator diagnostics | Do not present them as primary user workflow yet. |
| Mobile UI | V1 beta | It loads and is useful, but should be positioned as companion/mobile control surface. |

Do not lock these as polished V1 defaults:

- Global `auto_approve_commands: true`.
- Deep/aggressive/streaming as the first-run default for all users.
- Advisory mode as a product explainer until grounded.
- Historical development memories, notifications, tasks, and scheduled events.
- Remote gateways that are offline by default.
- Command allowlist entries for old sudo/device operations as seeded user state.

## Recommended Launch Defaults

Use a clean V1 profile:

| Control | Recommended V1 Default |
| --- | --- |
| `auto_approve_commands` | `false` |
| `reasoning_profile` | `balanced` |
| `repair_profile` | `balanced` or `conservative` |
| `reliability_mode` | `standard` |
| `reliability_verifier_enforced` | `true` |
| `workflow_execution_mode` | `auto` |
| `response_streaming_enabled` | `true` |
| `agent_learning_ledger_auto_learn_enabled` | `true` by default; can be disabled for public/demo seeds |
| `agent_memory_enabled` | `true` with a small curated seed |
| `ui_trace_visible` | `false` for client mode, `true` for operator mode |

## V1 Blockers

Close these before calling the instance public V1:

1. Make `.venv/bin/pytest -q` pass with zero failures.
2. Ground Advisory and conversational answers in the OpenFabric product identity and manual corpus.
3. Add a latency budget for common first-run prompts: product explanation under 5 seconds, read-only repo summary under 60 seconds, confirmation request under 15 seconds.
4. Fix confirmation denial state so trace, chat, integration result, and UI all agree on `cancelled` and show the denial final response.
5. Ensure stop/cancel drains in-flight LLM streaming cleanly and does not append confusing post-cancel deltas.
6. Ship a clean seed state: minimal memories, zero unread notifications, no personal scheduled events, no stale remote gateways, no development task history.
7. Prefer `rg` in generated shell plans when available; avoid broad `find`/`grep` scans for repo inspection.
8. Keep explicit HTTPS guidance when SSL is enabled; the TLS port should be opened as `https://127.0.0.1:8011/agent-ui` and does not need a same-port HTTP redirect.

## Acceptance Criteria For V1

V1 is ready when:

- All services start from `./startup.sh` and health checks pass.
- Main Agent UI, mobile UI, Settings, Directory, Prompt Editor, Learning Ledger, Reliability, Manual, and Website load with no console/page errors.
- The agent correctly explains OpenFabric as a local-first typed agent runtime.
- A read-only repository inspection completes with a useful answer within 60 seconds on the target model/profile.
- A mutating request asks confirmation within 15 seconds and denial leaves no state changes.
- Trace, chat, and integration API status agree after approval, denial, stop, failure, and completion.
- The default state is clean, non-personal, and conservative.
- The test suite is green.

## Final Assessment

OpenFabric has the shape of a strong V1: local execution, visible guardrails, typed runtime structure, durable memory, docs, manual, audio, and an unusually rich operator UI. The current deployed instance proves the architecture is working.

The V1 work is now less about adding features and more about subtracting friction: clean the state, tighten defaults, ground the agent's self-description, reduce first-run latency, and make status transitions completely consistent. After those changes, lock V1 around the local operator cockpit and present learning/reliability as advanced power surfaces.
