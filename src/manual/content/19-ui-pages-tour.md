---
id: ui-pages-tour
title: UI Pages Tour
kind: manual
tags: [agent-ui, ui-pages, operator-tour, product]
summary: A page-by-page operator tour of every OpenFabric Agent UI browser surface and what each page is for.
order: 19
---

# UI Pages Tour

OpenFabric has several browser pages because local agent work has more than one
job. The main workspace is for prompts, traces, terminal evidence, and active
runs. Companion pages separate mobile operation, directory navigation, runtime
settings, prompt and parameter editing, learning review, and reliability
inspection.

Use this page as the operator tour for every Agent UI surface. For the API and
route-level map, read [System Surfaces And API Reference](17-system-surfaces-api-reference.md).

## Page Map

| Page | Route | Primary Job |
| --- | --- | --- |
| Agent UI | `/agent-ui` | Main desktop workspace for prompts, runs, terminal evidence, trace, visualization, and drawers. |
| Agent UI Client | `/agent-ui-client` | Lighter client-facing chat surface that shares the runtime while hiding heavier operator controls. |
| Mobile Agent UI | `/agent-ui-mobile` | Touch-first agent workflow for chat, voice, gateway, tasks, monitors, parameters, and notifications. |
| Directory | `/directory` | Navigation hub for primary runtime paths and route inventory groups. |
| Mission Control | `/settings` | Backend-owned settings registry, runtime controls, optimization profiles, preferences, and storage status. |
| Prompt Editor | `/prompt-editor` | Prompt template inspection, editing, rendering, reset, and memory editing modes. |
| Parameter Editor | `/parameter-editor` | Full Parameter Store editing, served through Prompt Editor parameter mode. |
| Learning Ledger | `/learning-ledger` | Review lessons, run evidence, capability insights, and capability proposals. |
| Reliability | `/reliability` | Inspect recovery timelines, model profiles, verifier settings, reports, and deterministic evals. |

## Agent UI

Open:

```text
/agent-ui
```

The Agent UI is the primary desktop cockpit. It combines conversation,
composer, terminal, trace, visualization, runtime indicators, gateway selection,
quick controls, and drawers for Memory, Parameters, Events, Tasks, Monitors,
Notifications, Settings, and evidence inspection.

Use this page when you want the full local operator experience: submit a prompt,
queue work, approve or deny confirmations, answer clarifications, inspect
command capsules, watch terminal output, follow live trace events, and review
the final answer in the same browser surface.

Main controls include:

- the prompt composer, Run or Queue button, Stop, New Chat, model and gateway
  status, voice input, the square terminal visibility button, and prompt
  macros;
- quick controls for agent mode, clarification behavior, auto-approval,
  optimization profile, appearance, audio, and restart;
- trace and visualization toggles for developer evidence;
- terminal controls for interactive gateway sessions;
- the terminal-header **Include terminal in Advisory** switch, which is
  off-by-default and attaches cwd/session metadata plus visible terminal output
  to Advisory prompts without executing anything;
- task, monitor, event, memory, parameter, notification, and settings drawers.

A typical workflow starts with a prompt, watches the status badge and trace
events, reviews command capsules or terminal output, handles any approval or
clarification card, then inspects the final answer and retained evidence.

Related docs:

- [First Run And Agent UI](03-first-run-agent-ui.md)
- [Command Capsules, Terminal, And Gateway](05-command-capsules-terminal-gateway.md)
- [Modes, Approvals, And Clarifications](04-modes-approvals-clarifications.md)

## Agent UI Client

Open:

```text
/agent-ui-client
```

The Agent UI Client is a lighter chat-oriented page. It shares the same runtime
and request lifecycle as the desktop Agent UI, but it is meant for situations
where the user needs the agent conversation without the full admin/operator
surface always in view.

Use this page when the agent should feel closer to a client chat while still
retaining OpenFabric runtime behavior: queued requests, confirmations,
clarifications, retained trace state, final responses, and notifications.

Main controls include the conversation, composer, status indicators,
notifications, gateway selection, and compact access to runtime side panels.
Operator-only inspection remains available through related pages when deeper
debugging is needed.

Related docs:

- [First Run And Agent UI](03-first-run-agent-ui.md)
- [System Surfaces And API Reference](17-system-surfaces-api-reference.md)

## Mobile Agent UI

Open:

```text
/agent-ui-mobile
```

The Mobile Agent UI is the touch-first surface. It keeps chat and common
operator actions usable on narrow screens without asking the user to manipulate
the full desktop layout.

Use this page for mobile prompts, voice-assisted input, quick gateway
selection, task creation, monitor creation, notification review, and Parameter
Store updates. The page is especially useful when scheduled or background work
needs quick attention away from the desktop workspace.

Main controls include:

- mobile chat log, prompt input, send, stop, new chat, microphone, and gateway
  selector;
- a Terminal button that opens the desktop Agent UI with the shared terminal
  visibility preference enabled;
- notification, task, monitor, and parameter sheets;
- quick links to Directory, Manual, and Desktop;
- theme controls and browser notification controls.

Related docs:

- [Audio Dictation](08-audio-dictation.md)
- [Memory, Events, And Monitors](06-memory-events-monitors.md)
- [Tasks, Parameters, Prompt Editor, And Learning Ledger](07-tasks-parameters-prompts-learning.md)

## Directory

Open:

```text
/directory
```

The Directory page is the local navigation hub. It lists primary browser paths,
manual links, external companion services, and generated route inventory groups.

Use this page when you need to orient yourself, jump between UI surfaces, or
confirm which local paths are available in the running process. It is also a
good first stop after startup because it links the Agent UI, manual, website,
settings, editors, learning, reliability, health, and route references.

Main controls include search, primary path cards, route inventory groups, and
manual-linked actions. Directory data is backed by `/api/agent/directory`, so it
reflects the runtime's registered route metadata rather than a hand-maintained
static list.

Related docs:

- [Install And Startup](02-install-startup.md)
- [System Surfaces And API Reference](17-system-surfaces-api-reference.md)

## Mission Control

Open:

```text
/settings
```

Mission Control is the standalone settings page. It exposes the backend-owned
settings registry, runtime controls, persisted UI preferences, optimization
profiles, clarification profiles, presets, and storage state.

Use this page when you want to tune how the agent behaves before or between
runs. This includes runtime pacing, reasoning, repair, memory, LRN-T/LR-T, LR
Direct, reliability, LLM endpoint settings, audio endpoint settings, display
preferences, and profile-based defaults.

Main controls include the settings search field, optimization profile control,
clarification control, preset list, section navigation, save/sync indicators,
and individual controls generated from the settings registry. The terminal
visibility setting is mirrored by the square terminal button in the main Agent
UI composer, so changing either control updates the same preference.

Related docs:

- [Configuration And Storage](09-configuration-storage.md)
- [Modes, Approvals, And Clarifications](04-modes-approvals-clarifications.md)
- [Capability Reliability Kernel](15-capability-reliability-kernel.md)

## Prompt Editor

Open:

```text
/prompt-editor
```

Prompt Editor is the full prompt template and reusable memory editing surface.
It lets maintainers inspect registered prompt keys, compare current template
body to defaults, edit templates, render previews with variables, reset to
defaults, and switch into memory editing mode.

Use this page when adjusting how the runtime prompts LLM stages or when
reviewing the exact template text behind a behavior. It is not the normal place
to submit agent work; it is for maintaining the language contracts that support
agent work.

Main controls include mode tabs, template search, scope filter, template list,
Save, Render, Reset, dirty state, placeholder chips, template body editor,
default body panel, variable fields, preview panel, and memory editing fields.

Related docs:

- [Tasks, Parameters, Prompt Editor, And Learning Ledger](07-tasks-parameters-prompts-learning.md)
- [Configuration And Storage](09-configuration-storage.md)
- [Capability Evolution And Learning Ledger](14-capability-evolution-learning-ledger.md)

## Parameter Editor

Open:

```text
/parameter-editor
```

Parameter Editor is a dedicated route into Prompt Editor's parameter mode. It
uses the same underlying editor page as `/prompt-editor`, but opens the full
Parameter Store workflow directly.

Use this page when parameter metadata needs more room than the Agent UI drawer
provides. It supports identity fields, aliases, tags, sensitive state,
`value_json`, context summary, prompt guidance, clarification guidance,
concepts, metrics, relationships, and advanced `context_json`.

Main controls include parameter search, New, Save, Reveal, Reset or delete
actions where available, JSON editors for executable values and non-secret
context, and structured metadata sections used by parameter matching,
clarification resolution, SQL/entity selection, and prompt construction.

Related docs:

- [Adding Parameter Metadata For The Agent](16-parameter-store-context-sql.md)
- [Tasks, Parameters, Prompt Editor, And Learning Ledger](07-tasks-parameters-prompts-learning.md)
- [Configuration And Storage](09-configuration-storage.md)

## Learning Ledger

Open:

```text
/learning-ledger
```

Learning Ledger is the review surface for turning run evidence into reusable
lessons and capability proposals. It separates learned improvements from normal
chat so memory, prompt guidance, validation policies, planner overlays, and
backend patch proposals remain reviewable.

Use this page after a run produces feedback, a lesson draft, a suspect cache
entry, or a capability proposal. Operators can inspect evidence, edit summary
text, approve safe changes, apply already approved proposals, reject proposals,
retire stale entries, and add digest notes.

Main controls include summary counts, lesson status filters, proposal status
filters, lesson and proposal lists, recent run list, editable lesson/proposal
detail pane, evidence pane, Save, Remember, Reject, Retire, Restore, Apply
Proposal, and Digest Note.

Related docs:

- [Capability Evolution And Learning Ledger](14-capability-evolution-learning-ledger.md)
- [Memory, Events, And Monitors](06-memory-events-monitors.md)
- [Tasks, Parameters, Prompt Editor, And Learning Ledger](07-tasks-parameters-prompts-learning.md)

## Reliability

Open:

```text
/reliability
```

Reliability is the Capability Reliability Kernel dashboard. It shows how the
runtime detects, recovers from, verifies, reports, and evaluates weak-model
failures while preserving approval envelopes and trace evidence.

Use this page when a run needs support inspection, model capability comparison,
recovery timeline review, or deterministic eval checks. It is the best page for
understanding malformed JSON, bad plans, wrong cwd values, command failures,
Python failures, verification failures, repeated repair loops, or final-answer
loss.

Main controls include reliability mode, verifier enforcement, probe and repair
budgets, weak-model action cap, approval-envelope budget, model profile list,
failure taxonomy, run timeline, evidence pane, JSON and Markdown report links,
recent runs, eval list, and Run for deterministic evals.

Related docs:

- [Capability Reliability Kernel](15-capability-reliability-kernel.md)
- [Modes, Approvals, And Clarifications](04-modes-approvals-clarifications.md)
- [Command Capsules, Terminal, And Gateway](05-command-capsules-terminal-gateway.md)
