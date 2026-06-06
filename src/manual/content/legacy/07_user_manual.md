# Agent UI User Manual

This manual explains how to use the local OpenFABRIC Agent UI and where its
state is stored.


## Quick Start

Start the runtime:

```bash
./startup.sh
```

Open:

```text
http://127.0.0.1:8011/agent-ui
```

If your LLM server is not already running, open Settings and use the LLM launch
terminal to start a vLLM script from `src/llm/`.


## Main Screen

The Agent UI is split into a few surfaces:

- conversation pane for requests, responses, approvals, clarifications, memory
  edits, feedback, and command capsules;
- prompt composer at the bottom;
- optional terminal pane, shown or hidden from Settings or the square terminal
  button beside Mic;
- optional trace pane;
- optional visualization pane;
- Settings drawer;
- Memory drawer;
- Gateway drawer;
- Events drawer.

The model currently in use is shown under the prompt box. The configured model
may be `auto`; the displayed model should reflect the model discovered from the
OpenAI-compatible LLM endpoint when available.


## Asking For Work

Type requests in plain language:

```text
list all docker images and calculate the total size in GB
```

The runtime will decide whether it can answer directly, needs read-only shell
commands, needs Python, needs approval, or needs one clarification.

Prompt history:

- `Shift+Up` moves to the previous prompt.
- `Shift+Down` moves to the next prompt.


## Agentic, Conversational, And Advisory Modes

The mode toggle controls request context:

- Agentic: each request is treated as a standalone goal.
- Conversational: follow-up requests can use prior turns and prior operator
  outputs.
- Advisory: returns guidance and runnable snippets without executing them.

Conversational state is process-local. It is cleared by New Chat or server
restart.


## Detailed And Simple Final Responses

Quick controls -> Agent behavior -> Answers controls how much work the LLM
spends composing the final answer:

- Detailed: richer final composition.
- Simple: short status/result summary, with raw details left in command
  capsules.

Use Simple when command output already contains the answer or when you want
less final-answer latency.

Scheduled event runs always request detailed final answers so Event History is
useful even when your normal prompt mode is Simple.


## Workflow Execution

Agentic mode decomposes non-trivial tool work into granular steps by default.
The Settings drawer exposes workflow execution controls for Auto, Streaming,
and Full plan behavior. Auto and Streaming keep evidence flowing step by step:
one scoped task/action runs, its real output is recorded, and the next step is
planned with that evidence available.

Advisory mode can explain commands and offer runnable snippets, but those
snippets stay terminal-only and are not converted into operator actions
automatically. The terminal-header **Include terminal in Advisory** switch is
off by default; when enabled, the next Advisory prompt can include terminal
cwd/session metadata and a capped snapshot of the visible terminal output as
context. It still does not execute anything.


## Approvals

Mutating or risky actions pause before execution.

Examples that require approval:

- `git add`, `git commit`, `git push`;
- file writes or deletes;
- removing Docker containers/images;
- removing conda environments;
- Python actions that mutate state.

There is no Agentic fast lane that bypasses approval. Hard safety blockers,
command deny rules, cwd validation, binding validation, and policy checks still
apply to every step.

The approval card shows:

- action labels;
- command or Python code;
- cwd;
- risk;
- reason;
- output flow.

Choose Approve to continue or Deny to discard the pending plan.

The Modify Memory button lets you explain what is wrong with the proposed plan.
That feedback can become a memory proposal.

The conversation header has an Auto-Approve Commands toggle. When it is on, the
UI still renders the confirmation prompt, then submits the same approval action
for you. It does not auto-answer clarifications, schema errors, validation
failures, or hard safety blocks.


## Clarifications

The agent can ask one question when user intent is actually missing.

Example:

```text
create a conda environment
```

The agent may ask which Python version to use.

The agent should not ask for local facts it can discover. For example:

```text
push current branch
```

The agent should inspect the current branch instead of asking you for it.

Clarification mode is configurable in Settings:

- Auto-pilot;
- Balanced;
- Pedantic.


## Terminal Pane

The terminal pane is a trusted PTY session connected through the gateway.

Use it for:

- commands that need interactive input;
- passphrases;
- credential prompts;
- long-running processes you want to watch;
- setting the cwd for agent requests;
- saving per-gateway workspace state.

When the terminal is visible and expanded, agent shell commands may run through
the terminal depending on command type and runtime routing. Commands that may
need a passphrase or credential prompt are forced toward terminal routing when a
trusted terminal session exists.

If the terminal is hidden or collapsed, normal captured commands still work,
but commands that require user input may fail with a terminal-required error or
authentication failure.

The square terminal button beside Mic toggles the same persisted terminal
visibility preference as the Settings drawer. The terminal header can still
collapse or expand the pane without changing that preference. On Mobile UI, the
Terminal button opens `/agent-ui?terminal=1`, enables the shared terminal
visibility preference, and expands the desktop terminal pane.

Use Save Workspace to persist the terminal's current cwd for the selected
gateway. The UI also attempts to autosave this cwd when the browser closes,
when the page is hidden, and when you switch gateways.


## Command Capsules

Command capsules show execution detail inline in the conversation:

- command text;
- stdout/stderr/result output;
- status;
- exit code;
- Copy;
- Clear.

Capsules are the authoritative place for raw command output. Final responses
may summarize instead of repeating everything.


## Trace Pane

The trace pane is for developer/debug visibility.

It can show:

- stage progress;
- LLM decisions;
- self-brief;
- memory retrieval;
- plan validation;
- approval/clarification events;
- terminal routing;
- execution records;
- repair attempts;
- final formatting and answer judging;
- profiling.

You can disable trace updates in Settings to reduce UI work.


## Visualization Pane

The Visualization pane sits beside the Developer Trace. It builds a Mermaid
flow map from trace events and final trace payloads.

It can show:

- major architecture lanes;
- request stage order;
- loops and repair paths;
- LLM calls and retry groups;
- validation outcomes;
- DAG/action links;
- stage timings and longest calls.

Controls include Live/Freeze, horizontal/vertical layout toggle, Copy Mermaid,
Reset View, zoom buttons, scroll-wheel zoom, click-and-pan, and flow-map theme
invert.

The diagram is deterministic browser rendering. It does not call the LLM.


## Settings Drawer

Open Settings with the gear button.

Common settings:

- show/hide terminal pane, mirrored by the square terminal button beside Mic;
- show/hide trace pane;
- show/hide visualization pane;
- chat visual style;
- final response mode;
- clarification mode and max rounds;
- repair attempt limits;
- LLM endpoint host/base URL/model;
- model selection;
- LLM launch terminal command defaults;
- operator cache controls;
- agent display name;
- restart/reset/apply controls.

The Settings drawer is resizable. Its width is persisted in browser storage.
Sections are collapsible and use the same capsule/card style as the Memory,
Gateway, and Events drawers.


## Gateway Drawer

The Gateway drawer manages the machines that can execute shell commands.

It lets you:

- view active and connected gateways;
- add, edit, test, disable, or delete gateway entries;
- choose the active gateway;
- edit the gateway host, port, scheme, node name, and terminal cwd;
- see health details in table form.

The active gateway is also available from the footer selector. Gateway choice
and drawer width are stored in browser local storage. Gateway records and saved
terminal cwd values live in `artifacts/agent_gateways.db`.

For a gateway running on another machine, start it with a reachable bind host:

```bash
./src/gateway_agent/startup.sh --host 0.0.0.0 --port 8787
```

Only do that on a trusted network or behind a trusted proxy/firewall.


## Name Your Agent

Settings includes a Name Your Agent section.

You can:

- type a display name manually;
- ask the LLM to generate a random name;
- apply the name to UI labels and responses that would otherwise say "Agent".

This is a browser/UI preference sent with requests. It does not change runtime
safety behavior.


## LLM Launch Terminal

Settings includes an xterm-backed launch terminal for starting a local vLLM
server.

Defaults:

- conda env: `vllm`;
- command: `./src/llm/start-current-qwen3-coder-30b-a3b-awq.sh`;
- cwd: workspace root unless overridden.

The launch terminal is intended to be editable and interactive. It uses the
gateway terminal flow, so model-loading logs are visible there.

Closing or refreshing the browser tab should not be treated as a durable
process manager. If the gateway/server restarts, terminal-owned processes may
be terminated. Use a separate shell or process supervisor for long-lived
production serving.


## Memory Drawer

The Memory drawer is separate from Settings.

It lets you:

- browse active/proposed/retired memory;
- view basic memory stats;
- filter by status, scope, model family, task type, tool type, intent type, and
  tags;
- create memory manually;
- edit memory text, summary, scope, tags, and classification;
- retire or restore memory;
- inspect audit history;
- review and apply LLM-generated proposals;
- optimize memory with LLM-suggested cleanup proposals.
- view match diagnostics and applied-memory metadata in traces when memory was
  used.

Manual memory entries become active immediately. LLM-authored feedback or
optimizer entries remain proposed until you apply them.

The Memory drawer is resizable. Its width is persisted in browser storage.
Library, Edit Memory, and Feedback sections are collapsible.


## Memory Scopes

Memory can be scoped as:

- exact model: apply only to one exact model id;
- model family: apply to related variants;
- global: apply to all models when task/tool/intent relevance matches.

Exact model wins over model family, which wins over global.


## When Memory Is Used

If memory is applied to a request, the conversation header shows a memory
indicator and count. The final response can also include a small note that
previous memory was applied.

The count near the memory indicator summarizes the usage of applied memories,
not a guarantee that a single memory was responsible for the result.

Memory retrieval happens after self-brief classification. It must match the
current task/tool/intent/tags and request text. This prevents unrelated Git
memory from being applied to Docker tasks, for example.

The runtime also has conservative domain hints. Obvious prompts like "commit
and push this git repo" can retrieve Git memories even if an upstream LLM stage
classified the request generically. A prompt like "stage the presentation"
should not trigger Git memory.

Validation-policy memory is separate from normal task/preference memory. Use
Modify Validation only when you are teaching validator behavior, not ordinary
task strategy.


## Run Feedback

After a run, use Run Feedback to teach the system:

- Right decision;
- Partially right;
- Wrong decision;
- Unclear.

You can type feedback yourself or use Auto Generate to ask the LLM to draft
editable feedback from the operation summary and your selected outcome.

Good feedback is specific:

```text
For git push tasks using SSH, do not wrap git push in python subprocess. Use a
shell_command with interaction_mode may_prompt so the terminal can collect the
passphrase.
```

Bad feedback is too vague:

```text
Do better next time.
```

Submitted feedback becomes a proposed memory. Review it in the Memory drawer
before applying.


## Events Drawer

The Events drawer manages saved interval prompts.

Use it for requests like:

```text
check if there are pending git changes every hour
```

The flow is:

1. The UI detects scheduling language and asks the LLM for editable event
   drafts.
2. You review or edit title, prompt, interval, cwd, status, and auto-approve.
3. Save activates the event.
4. The scheduler runs the event later through the normal agent pipeline.

The drawer has collapsible sections:

- Create Event;
- Scheduled Events;
- Event History.

Scheduled event cards include an on/off toggle for quick pause/resume. Event
History shows each run as collapsible preformatted markdown, with request and
trace links when available.

Event runs use their saved gateway and cwd. Events keep running while the
server process is alive, even if the browser is closed. If the server is down
at a trigger time, it does not backfill every missed interval; overdue events
run once after restart.


## Storage Locations

Persistent files:

| Data | Default Location | Notes |
| --- | --- | --- |
| Memory SQLite database | `artifacts/agent_memory.db` | Override with `AOR_AGENT_MEMORY_DB_PATH`. |
| LLM prompt template database | `artifacts/prompts.db` | Checked-in seed prompt catalog. Override with `AOR_AGENT_PROMPTS_DB_PATH`. |
| Events SQLite database | `artifacts/agent_events.db` | Saved events and run history. Override with `AOR_AGENT_EVENTS_DB_PATH`. |
| Gateway SQLite database | `artifacts/agent_gateways.db` | Gateway registry and per-gateway terminal cwd. Override with `AOR_AGENT_GATEWAYS_DB_PATH`. |
| Operator plan cache | `artifacts/agent_plan_cache.db` | Private cache, normally not tracked. |
| Computation cache | `artifacts/agent_computation_cache.db` | Private deferred-code cache, normally not tracked. |
| Compatibility/run store | `artifacts/runtime.db` | Used by compatibility/runtime storage paths. |
| vLLM benchmark reports | `src/llm/results/` | Created by `src/llm/run-benchmark.sh`. |
| Generated local artifacts | `outputs/` by default | Controlled by artifact settings. |

Browser-local storage:

| Data | Where | Notes |
| --- | --- | --- |
| Theme and random pastel theme | browser local storage | Per browser profile. |
| Settings drawer width | browser local storage | Survives reloads. |
| Memory drawer width | browser local storage | Survives reloads. |
| Gateway drawer width and selected gateway | browser local storage | Survives reloads. |
| Events drawer width | browser local storage | Survives reloads. |
| Prompt history | browser local storage | Used by `Shift+Up`/`Shift+Down`. |
| UI toggles | browser local storage plus `agent_ui_settings.db` | Local layout hints stay in the browser; persisted preferences such as terminal visibility are shared through backend settings. |

Volatile process memory:

| Data | Lifetime |
| --- | --- |
| Active request traces | Until process restart or store eviction. |
| Pending confirmations | Until approval/denial/restart/eviction. |
| Pending clarifications | Until answer/restart/eviction. |
| Conversation turns | Until New Chat or process restart. |
| Gateway terminal sessions | Until idle timeout, close, or gateway restart. |
| Active command process ids | Until command completes/cancels/gateway restart. |
| Runtime-control toggles | Until server restart unless also persisted by UI settings. |


## Important Environment Variables

Common variables:

- `AOR_LLM_PREFLIGHT_ENABLED`
- `AOR_LLM_PREFLIGHT_TIMEOUT_SECONDS`
- `AOR_LLM_CONTEXT_WINDOW_TOKENS`
- `AOR_AGENT_MEMORY_DB_PATH`
- `AOR_AGENT_MEMORY_ENABLED`
- `AOR_AGENT_MEMORY_PROMPT_MAX_CHARS`
- `AOR_AGENT_PROMPTS_DB_PATH`
- `AOR_AGENT_EVENTS_ENABLED`
- `AOR_AGENT_EVENTS_DB_PATH`
- `AOR_AGENT_EVENTS_POLL_SECONDS`
- `AOR_AGENT_EVENTS_MAX_RUN_HISTORY`
- `AOR_AGENT_GATEWAYS_DB_PATH`
- `AOR_AGENT_PLAN_CACHE_DB_PATH`
- `AOR_AGENT_COMPUTATION_CACHE_DB_PATH`
- `AOR_AGENT_UI_LLM_LAUNCH_CONDA_ENV`
- `AOR_AGENT_UI_LLM_LAUNCH_COMMAND`
- `AOR_AGENT_UI_LLM_LAUNCH_CWD`
- `AOR_AGENT_CLARIFICATION_MODE`
- `AOR_LLM_OPERATOR_MAX_CLARIFICATION_ROUNDS`
- `AOR_LLM_OPERATOR_MAX_VALIDATION_REPAIR_ATTEMPTS`
- `AOR_LLM_OPERATOR_MAX_DEFERRED_CODE_REPAIR_ATTEMPTS`
- `AOR_LLM_OPERATOR_MAX_EXECUTION_REPAIR_ATTEMPTS`
- `AOR_GATEWAY_URL`
- `AOR_GATEWAY_TIMEOUT_SECONDS`
- `GATEWAY_BIND_HOST`
- `GATEWAY_BIND_PORT`
- `GATEWAY_NODE_NAME`
- `AOR_SHELL_COMMAND_TIMEOUT_SECONDS`


## Local LLM Models

Launch scripts live under `src/llm/`.

Useful commands:

```bash
./src/llm/start-current-qwen3-coder-30b-a3b-awq.sh
./src/llm/run-benchmark.sh --models qwen3-8b-awq --prompt-limit 3
./src/llm/run-benchmark.sh --no-launch --base-url http://127.0.0.1:8000/v1 --models qwen3-coder-30b-a3b-awq
```

The Agent UI expects an OpenAI-compatible base URL such as:

```text
http://127.0.0.1:8000/v1
```


## Troubleshooting

### The agent asks for the current branch

It should not. Current branch is discoverable. Add feedback such as:

```text
For requests that say current branch, inspect it with git branch --show-current
or git rev-parse --abbrev-ref HEAD instead of asking me.
```

### Git push fails with `ssh_askpass`

This means the command needed a passphrase but was not running in a real
terminal or the terminal was not available. Keep the Agent UI terminal visible
and expanded, or run the push manually in the terminal. Feedback can help the
planner, but terminal routing is the architectural fix.

### A final answer says 0.00 even though source rows exist

This is usually bad parsing or bad final formatting. The runtime has zero-like
result checks and answer judging, but feedback should be specific:

```text
When calculating totals from Docker image sizes, preserve all source rows and
fail/repair if the computed total is zero while non-empty MB/GB inputs exist.
```

### Memory appears unrelated

Open the Memory drawer and inspect active entries. Retire overly broad entries
or edit their task/tool/intent/tags to make them narrower.

### The memory icon did not light up

Check that the live backend has been restarted after memory-retrieval changes,
that memory is enabled, and that the run reached the `memory_check` trace
stage. In operator mode memory usually appears after self-briefing, not at the
instant the prompt is submitted.

### Event run says awaiting confirmation

Open Event History and follow the request/trace link. If the event has
auto-approve enabled, the scheduler should submit the generated confirmation
automatically. If it still waits, check whether the event was saved before
auto-approve was enabled or whether the run hit a clarification or validation
failure instead of a confirmation.

### Gateway cwd is wrong after refresh

Open the Gateway drawer and check the selected gateway's terminal cwd. Use Save
Workspace in the terminal to persist the current cwd for that gateway.

### A drawer is too small

Drag the drawer edge to resize it. The width is persisted in browser storage.

### The trace pane is noisy

Disable trace in Settings. The runtime still executes normally; the browser
just stops updating the trace pane.


## Safety Expectations

The runtime is a local trusted tool. It can execute shell and Python when
approved. Safety comes from:

- clear action display;
- typed validation;
- read-only vs mutating risk;
- approval before mutation;
- terminal routing for interactive prompts;
- audit/trace visibility;
- user-controlled persistent memory.

Do not treat it as an untrusted multi-tenant sandbox.
