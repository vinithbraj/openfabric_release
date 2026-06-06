# Agent UI User Manual

This manual explains how to use the local OpenFABRIC Agent UI and where its
state is stored.


## Quick Start

After the source is synced down, Docker is the preferred product path:

```bash
./docker-all-services/build.sh
./docker-all-services/start.sh
```

This starts the Agent runtime and Agent UI on `8011`, the manual on `8013`, the
website on `8014`, and the audio runtime inside the Docker network.

Run in the background:

```bash
./docker-all-services/start.sh -d
```

Install and start a gateway before expecting command-backed agent work to
operate. The gateway is the "hands" of the system: it is the process that
touches the shell, terminal, workspace, and local command environment.

```bash
./src/gateway_agent/install.sh
./src/gateway_agent/startup.sh
curl http://127.0.0.1:8787/healthz
```

For a gateway on another trusted machine:

```bash
GATEWAY_NODE_NAME=workstation ./src/gateway_agent/startup.sh --host 0.0.0.0 --port 8787
```

Attach an OpenAI-compatible LLM endpoint:

```bash
curl http://127.0.0.1:8000/v1/models -H 'Authorization: Bearer local'
```

Or create a local vLLM conda environment and launch a checked-in profile:

```bash
conda create -n vllm python=3.12 -y
conda activate vllm
pip install --upgrade uv
uv pip install vllm --torch-backend=auto
./src/llm/start-current-qwen3-coder-30b-a3b-awq.sh
```

For native development instead of Docker, start the runtime:

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
- Events drawer;
- Tasks drawer;
- Monitors drawer;
- Parameters drawer;
- Chat History drawer;
- Notifications drawer.

Standalone surfaces are also available:

| Surface | Route | Use |
| --- | --- | --- |
| Directory | `/directory` | Navigate all local UI and manual surfaces. |
| Mission Control Settings | `/settings` | Edit backend-owned runtime controls and UI preferences. |
| Prompt Editor | `/prompt-editor` | Inspect, render, edit, and reset prompt templates. |
| Parameter Editor | `/parameter-editor` | Full Parameter Store editor, implemented as Prompt Editor parameter mode. |
| Learning Ledger | `/learning-ledger` | Review lessons, run evidence, insights, and capability proposals. |
| Reliability | `/reliability` | Inspect recovery runs, model profiles, reports, and evals. |
| Client UI | `/agent-ui-client` | Minimal client chat surface. |
| Mobile UI | `/agent-ui-mobile` | Touch-first chat, tasks, monitors, parameters, notifications, and voice workflow. |

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

The composer has one action button. When there is no active request or durable
task, the button says Run, saves the prompt as a durable task with
`start_now: true`, and immediately attaches to the oldest queued task. While a
run, queued task, approval, or clarification is active, the same button says
Queue and saves the prompt behind older queued work. When the active work
finishes and no queued/running task remains, the button returns to Run.

Queued composer prompts keep the current conversation, selected model, agent
mode, gateway routing, settings context, terminal context when available, and
request-scoped auto-approve intent. The previous trace stays visible while the
new prompt is queued.

When the queued task starts, the open UI auto-attaches to it: it fetches the
retained trace, renders what already happened, then streams future events. If a
durable task auto-approves a confirmation and continues on a child request, the
UI follows the task to the child request and renders the final result instead
of leaving a stale approval card behind.


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
snippets are terminal-only in this version and do not become operator actions
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

For background durable tasks, auto-approval is owned by the task runner. The UI
may show the saved approval trace while the task is catching up, but it retires
those controls once the task has continued so a second Approve click is not
offered for an already-approved request.


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

Clarification mode is configurable in Settings with a segmented control:

- Auto-pilot: make high-confidence assumptions for most missing details.
- Balanced: default; auto-resolve obvious typos, plurals, casing, acronyms, and
  near-exact entity matches.
- Pedantic: ask whenever a meaningful option is missing or ambiguous.

Before a clarification is shown, the runtime can ask a separate typed
clarification resolver whether the question can be answered from available
schema/entity candidates, Parameter Store context, or prior request context.
When it continues, selected entities and assumptions are attached to the next
agent step. They do not bypass validation, SQL safety, credential flows, or
approvals.


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
- chat pop animation style;
- number animation style;
- final response mode;
- clarification mode and max rounds;
- repair attempt limits;
- LLM endpoint host/base URL/model;
- model selection;
- LLM launch terminal command defaults;
- operator cache controls;
- LRN-T/LR-T toggle and LR-T similarity threshold;
- LR Direct replay toggle and clear controls;
- agent display name;
- restart/reset/apply controls.

The Settings drawer is resizable. Its width is persisted in browser storage.
Sections are collapsible and use the same capsule/card style as the other
drawers.

Settings are also available from Mission Control at `/settings`. Mission
Control uses the backend registry and shows which values are runtime controls
versus UI preferences. The chat drawer and Mission Control both use the same
backend preference store for theme, pane visibility, chat bubbles, number
animation, chat pop animation, browser notifications, sound, voice input, and
auto immersive width.

In immersive mode the normal topbar and non-chat panes are hidden, but the
conversation header keeps the quick-controls button and a settings burger next
to it. The quick controls, settings burger, backend plug, mirrored run/LLM
badges, and Learned Runtime badge sit in a right-aligned rail underneath the
gateway picker; the model name remains out of that immersive header. The
settings burger opens the same Settings drawer as an overlay, so pane and UI
controls remain reachable without leaving immersive mode.


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


## Parameter Store And Parameter Editor

The Parameter Store saves named runtime values such as database profiles,
service endpoints, project paths, model preferences, tokens, and secrets. Open
the full editor at:

```text
/parameter-editor
/parameter-editor?key=canonical_v1
/prompt-editor?mode=parameters&key=canonical_v1
```

The Settings/Parameter Store drawer still supports quick edits. Use Open
Editor for the full page when you need to edit both executable values and
agent guidance.

Each parameter has two JSON layers:

| Field | Use |
| --- | --- |
| `value_json` | Executable data such as credentials, hosts, ports, tokens, paths, IDs, database names, SQL schemas, and discovery metadata. |
| `context_json` | Non-secret guidance for prompts, clarification, matching, and domain/entity selection. |

`context_json` uses this shape:

```json
{
  "version": 1,
  "user_provided_domain_context": {
    "summary": "",
    "prompt_guidance": "",
    "clarification_guidance": "",
    "concepts": [],
    "metrics": [],
    "relationships": []
  }
}
```

The full editor has separate sections:

- Identity: key, description, aliases, tags, and sensitive toggle.
- Value: `value_json` JSON editor.
- Context: summary, prompt guidance, clarification guidance, concepts, metrics,
  relationships, and advanced raw `context_json`.
- Inspector: masked summary, schemas, environment names, audit/use metadata,
  and validation errors.

Sensitive `value_json` loads masked by default. Reveal uses the existing
audited reveal endpoint. You can edit `context_json` without revealing secrets,
and you can replace `value_json` without seeing the old secret value.


## SQL Database Profiles

`/discoverdb` creates or refreshes Parameter Store database profiles. Every run
discovers the schema and returns reviewable drafts. Commit writes generated SQL
metadata into `value_json`:

- `schema_catalog`;
- `relation_foreign_scheme`;
- `schema_discovery`.

Domain guidance belongs in `context_json`. For example, doctors can add DICOM
hierarchy notes, clinical cohort definitions, anatomic terms, metric
definitions, and clarification rules through the Parameter Editor Context
section.

The SQL agent prefers stored schema metadata from Parameter Store. It falls
back to live discovery only when stored metadata is missing or refresh behavior
is requested. If the database schema changes, run `/discoverdb` again.

SQL validation rejects unknown tables/columns, unsafe SQL, multiple statements,
mutations without confirmation, and invalid limits. Read-only joins that are
not DB-declared foreign keys are allowed with warnings when they are inferred
or supported by domain context. Only database-declared foreign keys count as
approved joins.


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


## Learning Ledger And Capability Proposals

Open `/learning-ledger` to review what the runtime learned from completed
requests.

The page has two lanes:

- Lessons, which preserve the older memory-oriented learning flow.
- Proposals, which are typed capability evolution drafts.

Capability proposals can target:

- `task_memory`;
- `validation_policy`;
- `prompt_patch`;
- `capability_manifest_overlay`;
- `executable_backend_patch`.

Use Remember to approve a proposal. Memory, validation-policy, prompt, and
manifest-overlay proposals are applied to local artifact stores after approval.
Executable backend patch proposals stop at `approved_pending_apply`; they must
be applied manually in code, tested, and activated by restart.

Use Apply Proposal to retry an already approved proposal. Use Reject or Retire
when the proposal is wrong, too broad, unsafe, or no longer useful.

Auto-learning can approve high-confidence safe proposals when
`agent_learning_ledger_auto_learn_enabled` is enabled. Deterministic proposals
need at least `0.72` confidence; LLM-drafted proposals need at least `0.85`.
Approval-bypass language, validation-bypass language, secrets, risk
relaxation, and executable hot-load language block auto-approval.

See [09_capability_evolution_layer.md](09_capability_evolution_layer.md) for
the full proposal lifecycle, safety policy, API routes, and architecture.


## Learned Runtime Caches

OpenFABRIC also keeps private learning caches that are separate from the
reviewable Learning Ledger:

- LRN-T writes successful validated total-task structures.
- LR-T reuses those structures for later similar prompts when classification,
  model family, workflow mode, registry contract hash, and prompt similarity
  are compatible.
- LR Direct reuses exact streaming-step command or Python replay entries.
- LR-EX can reuse equivalent payload-aware replay shapes after a structured
  LLM shape judge approves the match.

The Settings drawer and Mission Control expose LRN-T/LR-T, LR-T threshold, and
LR Direct controls. The cache APIs can clear LRN-T and LR Direct data without
touching approved memory, prompts, or Learning Ledger proposals.

These caches are local runtime accelerators. They do not bypass memory
compliance, validation, approval, gateway routing, or safety policy.

Final responses show a short learning summary instead of raw learned-runtime
event dumps. Examples include:

- `LR-T applied`: a learned total-task structure was reused.
- `LR-T not applied`: candidates were checked, but the best score was below
  the configured threshold.
- `LR-D applied`: LR Direct replayed exact cached execution steps.
- `LRN learned`: reusable task or step actions were saved for future runs.

Internal ids stay out of the default summary and are kept in trace details for
debugging.

Inline learned-runtime notices such as `LR`, `LRD`, `LR-T`, and `LR-EX` render
as compact tags. If a tag is clickable, it opens the correction/review flow for
the associated auto-learnt action; otherwise it is informational.


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
| Parameter Store SQLite database | `artifacts/agent_parameters.db` | Runtime parameters, executable values, and non-secret `context_json`. |
| LLM prompt template database | `artifacts/prompts.db` | Checked-in seed prompt catalog. Override with `AOR_AGENT_PROMPTS_DB_PATH`. |
| Learning Ledger database | `artifacts/agent_learning_ledger.db` | Lessons, run evidence, capability insights, and proposals. Override with `AOR_AGENT_LEARNING_LEDGER_DB_PATH`. |
| LRN-T total-task database | `artifacts/agent_lrn_total_tasks.db` | Private learned task structures. Override with `AOR_LRNT_DB_PATH`. |
| Events SQLite database | `artifacts/agent_events.db` | Saved events and run history. Override with `AOR_AGENT_EVENTS_DB_PATH`. |
| Gateway SQLite database | `artifacts/agent_gateways.db` | Gateway registry and per-gateway terminal cwd. Override with `AOR_AGENT_GATEWAYS_DB_PATH`. |
| Operator plan cache | `artifacts/agent_plan_cache.db` | Private cache, normally not tracked. |
| Command template cache | `artifacts/agent_command_template_cache.db` | Private command template and LR Direct/LR-EX command replay cache, normally not tracked. |
| Computation cache | `artifacts/agent_computation_cache.db` | Private deferred-code cache, normally not tracked. |
| Compatibility/run store | `artifacts/runtime.db` | Used by compatibility/runtime storage paths. |
| vLLM benchmark reports | `src/llm/results/` | Created by `src/llm/run-benchmark.sh`. |
| Generated local artifacts | `outputs/` by default | Controlled by artifact settings. |

Browser-local storage:

| Data | Where | Notes |
| --- | --- | --- |
| Drawer widths | browser local storage | Settings, Memory, Parameters, Gateway, Events, Tasks, and Monitors survive reloads. |
| Selected gateway and terminal cwd hints | browser local storage plus `agent_gateways.db` | The browser remembers the active gateway; durable gateway records and saved cwd values live in SQLite. |
| Prompt history | browser local storage | Used by `Shift+Up`/`Shift+Down`. |
| Transient drawer and panel state | browser local storage | Open/collapsed state, trace or visualization zoom, and similar local layout hints. |
| Random pastel theme seed | browser local storage | Only generated random color seeds are local; the selected theme preference is backend-persisted. |

Backend-persisted UI preferences:

| Data | Where | Notes |
| --- | --- | --- |
| Theme, agent display name, and agent mode | `agent_ui_settings.db` | Shared across browser sessions when backend settings persistence is enabled. |
| Pane visibility and chat bubbles | `agent_ui_settings.db` | Also reflected immediately in the browser layout; terminal visibility is shared by Settings, the composer terminal button, and mobile terminal handoff. |
| Number and chat pop animation | `agent_ui_settings.db` | Chat pop defaults to None. Optional modes are Soft rise, Slide up, Slide side, Scale pop, Spring, Flip, Skew snap, Blur glow, and Drop in. |
| Browser notifications, sound, voice input, capture preset, and silence timeout | `agent_ui_settings.db` | Used by desktop and mobile surfaces. |

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
- `AOR_AGENT_COMMAND_TEMPLATE_CACHE_DB_PATH`
- `AOR_AGENT_COMPUTATION_CACHE_DB_PATH`
- `AOR_LRNT_DB_PATH`
- `AOR_LRNT_ENABLED`
- `AOR_LRNT_SIMILARITY_THRESHOLD`
- `AOR_LRNT_MAX_ENTRIES`
- `AOR_LRDIRECT_ENABLED`
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
- reviewable capability proposals that cannot bypass safety policy.

Do not treat it as an untrusted multi-tenant sandbox.
