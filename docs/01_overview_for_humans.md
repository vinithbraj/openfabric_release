# Runtime Overview For Humans

This document explains the runtime without assuming you already think like a
planner, compiler, or distributed systems engineer.


## What This System Is

The system takes a plain-language request and turns it into safe, structured
local work.

It is not a design where the LLM freely writes commands and the machine blindly
runs them. The LLM helps with meaning, but the runtime owns validation, safety,
approval, terminal routing, and execution boundaries.

OpenFabric is local-first by design. It can use local or OpenAI-compatible
models, and it makes smaller models more useful for agent work by narrowing
model responsibilities and surrounding them with typed contracts,
decomposition, guardrails, validation, approval gates, traceability, recovery,
and durable workflow state. This raises the reliability floor without claiming
that every smaller model is equivalent to a larger model.

The current default execution model is operator-backed:

- shell work is represented as `operator.shell_command`;
- Python programs are represented as `operator.python_action`;
- Python transformations are represented as `operator.python_transform`;
- runtime self-inspection stays deterministic through `runtime.*`.
- saved interval work is represented as persistent runtime events that re-enter
  the same request pipeline later.
- queued composer work is represented as durable tasks that can wait behind an
  active run, survive UI refreshes, and expose attempts/checkpoints.
- reusable runtime values and domain guidance live in the Parameter Store,
  split into executable `value_json` and non-secret `context_json`.
- successful requests can teach local LRN-T total-task structures and LR
  Direct/LR-EX replay shapes for later similar work.

After source sync, the preferred startup path is Docker first, then gateway,
then LLM:

```bash
./docker-all-services/build.sh
./docker-all-services/start.sh
./src/gateway_agent/install.sh
./src/gateway_agent/startup.sh
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8000/v1/models -H 'Authorization: Bearer local'
```

Docker runs the local product surfaces. The gateway is still native because it
is the "hands" of the system and must run on the machine that owns the shell.
The LLM endpoint is also separate because it can be an existing
OpenAI-compatible service or a local vLLM server launched from a conda
environment.

The default execution model is granular and streaming. Non-trivial Agentic
requests are decomposed first, one scoped task is executed at a time, and each
step feeds its evidence into the next decision. Atomic requests may still
naturally become one task and one action, but complex work is not collapsed
into a single generated action.

That granularity is a smaller-model design pressure: each model call emits a
bounded artifact that the runtime can validate, repair, approve, or reject
before the next step.


## The Main Actors

### The LLM

The LLM helps answer questions like:

- What is the user asking for?
- Is this one task or several tasks?
- Is the request clear enough, or should we ask one question?
- Can a proposed clarification be auto-resolved from schema/entity candidates,
  Parameter Store context, and confidence thresholds?
- Should the work be shell, Python action, Python transform, or runtime
  introspection?
- How should one action's output feed the next action?
- Does a prior memory entry apply to this request?
- Did execution prove the goal was met?

The LLM proposes. It does not get final authority over execution.

### The Runtime

The runtime checks proposals before trusting them.

It validates:

- structured JSON shape;
- task ids and action ids;
- known semantic verbs and object types;
- operator manifests;
- command risk and approval requirements;
- Python function shape;
- terminal interaction requirements;
- dependency graph shape;
- input bindings between actions;
- workspace/cwd boundaries;
- final answer and display contracts;
- memory relevance and memory override rules.

### The Gateway

The gateway is the part that touches the machine for shell commands.

It runs bounded local processes, streams stdout and stderr, supports
cancellation, and provides PTY-backed terminal sessions for commands that need
passphrases, credentials, or a controlling terminal.

### Persistent Memory

The memory bank stores user-approved instructions and lessons in SQLite. It can
remember things like "for this model, prefer terminal-backed git push when SSH
may ask for a passphrase" or "for Docker size calculations, preserve source
rows in the final answer."

Memory retrieval is intentionally narrow. The runtime first builds task
context, then retrieves matching entries by model scope, task type, tool type,
intent type, tags, and rare text overlap. Deterministic hints fill in obvious
domains such as Git and Docker when an LLM stage labels the task generically.
Retrieved memories become visible runtime directives, not hidden authority.

Memory is guidance, not law. Current user instructions, live command output,
typed validation, and safety policy always win.

### Learning Runtime Caches

The runtime also keeps private learned caches for repeatable request structure
and execution shapes.

LRN-T learns the validated total-task structure from successful requests.
LR-T is the reuse path: it can match a later similar prompt by normalized
prompt shape, classification, model family, workflow mode, and registry
contract hash, then reuse the typed task frames instead of asking the LLM to
decompose the prompt again.

LR Direct learns exact streaming-step replay entries for command templates and
Python computations. LR-EX adds a structured LLM shape judge for cases where a
new step is semantically equivalent to a cached payload-aware command or Python
shape even though the words or input names changed.

These caches are private runtime accelerators, not authority. Reused entries
are still checked against current memory, validation, safety, approval,
gateway, and execution policy. Failed or invalid total-task entries are
quarantined instead of reused.

### Parameter Store

The Parameter Store saves named values and domain guidance in SQLite. Examples
include database profiles, service endpoints, project paths, model preferences,
tokens, and credentials.

Each entry has two JSON areas:

- `value_json` for executable data such as hosts, ports, credentials, paths,
  IDs, and generated SQL discovery metadata;
- `context_json` for non-secret summary, prompt guidance, clarification
  guidance, concepts, metrics, and relationships.

The runtime can use bounded `context_json` to choose a database, table, metric,
file, or concept more intelligently. It cannot use context to reveal secrets,
lower risk, approve joins, skip confirmations, or override validators.

### Runtime Events

Runtime events are saved prompts with interval schedules. They let the server
run requests such as "check git status every hour" without the browser staying
open.

Events persist in SQLite, remember their gateway and working directory, and run
through the same agent path as normal prompts. If an event is configured to
auto-approve, it only clicks the confirmation prompt that the runtime already
generated; it does not bypass validation or answer clarifications.

Durable tasks are saved prompts with attempt history. The composer uses them
automatically when the user presses Run while another request, task, approval,
or clarification is active: the button changes to Queue, the prompt is saved as
a chat-sourced task, and the backend starts it when active work clears. If the
UI is open when the task starts, it replays the retained trace first and then
continues streaming live events. If task auto-approval creates a child request,
the UI follows the task to that child and shows the final result there.


## What Happens When You Type A Prompt

For a request like:

```text
list all docker images and calculate the total size in GB
```

the rough flow is:

1. The UI sends the prompt and current settings to the runtime.
2. The LLM may ask or auto-resolve a clarification only if user intent is
   genuinely missing.
3. The runtime creates a self-brief and classifies task/tool/intent.
4. Memory and Parameter Store matching retrieve only relevant guidance for that
   specific request and turn it into planning/final-answer directives.
5. The runtime checks LR-T for a validated learned total-task structure; on a
   hit, it reuses typed tasks and still continues through the normal operator
   path.
6. On a miss, the LLM decomposes non-trivial work into granular subtasks before
   execution.
7. The LLM authors the next scoped plan, usually with a machine-readable shell
   command and a Python calculation step.
8. If Python depends on shell output, code generation can wait until the real
   stdout shape exists.
9. LR Direct or LR-EX may reuse a safe exact/equivalent current-step replay
   shape, still subject to validation and policy.
10. The runtime checks command/code shape, safety, dataflow, and approval
    needs.
11. Read-only work runs; mutating work pauses for approval.
12. Results stream into command capsules.
13. Observation review can repair failures or ask for user intent if needed.
14. The final response is rendered in Detailed or Simple mode.
15. The user can give run feedback, which can become a proposed memory.
16. Successful runs may update LRN-T and LR Direct/LR-EX caches for future
    local reuse.
17. The final answer shows a concise grouped learning summary when LRN-T,
    LR Direct, or LRN writes applied or were rejected by thresholds.
18. Trace events also feed the Developer Trace and Visualization panes so the
    request flow, timings, loops, and retries are inspectable.


## Conversational Mode

The Agent UI has Agentic, Conversational, and Advisory modes.

In Conversational mode, follow-up prompts can refer to previous command output:

```text
Which image is the largest?
```

The runtime builds bounded conversation context. The LLM then chooses one of
two paths:

- answer from prior output, with no new approval;
- propose new operator actions, which are validated and approval-gated normally.

Conversation state is process-local and can be cleared with New Chat.


## Clarifications

The runtime lets the LLM ask the user one question at a time when user intent is
really required.

Good clarification:

```text
create a conda environment
```

The LLM may ask which Python version to use because that value affects the
created environment.

Bad clarification:

```text
push current branch
```

The LLM should not ask which branch is current. It should inspect the repository
with a read-only command because the branch is discoverable local state.

Clarification behavior has three modes:

- `auto_pilot`: make high-confidence assumptions for most gaps;
- `balanced`: default; auto-resolve obvious typos, plurals, casing, acronyms,
  and near-exact entity matches;
- `pedantic`: ask whenever a meaningful option is missing or ambiguous.

Auto-resolution never bypasses validation, SQL safety, credential flows, or
approval prompts.


## Why This Is Safer Than Blind Command Execution

The system adds guardrails between meaning and action:

- strict structured-call schemas;
- bounded operator manifests;
- deterministic validation;
- read-only vs mutating command classification;
- approval before mutation;
- terminal routing for prompt-shaped commands;
- auto-approval limited to already-generated confirmation prompts;
- gateway execution instead of direct arbitrary shell access from the planner;
- Python function-shape contracts;
- memory relevance and compliance checks;
- LRN-T/LR-T and LR Direct/LR-EX reuse only after scoped matching and current
  validation;
- Parameter Store context kept separate from raw execution values;
- non-overlapping scheduled event runs;
- final answer checks;
- structured rendering instead of hidden ad hoc formatting.


## Important Terms

### Prompt

The sentence or question the user types.

### Task

One semantic unit of work extracted from the prompt.

### Operator Action

The executable action compiled from a task. The ordinary operator actions are:

- `operator.shell_command`
- `operator.python_action`
- `operator.python_transform`

### Streaming Decomposition

The default operator pacing model for non-trivial tool work. The runtime asks
for granular subtasks, executes one scoped task/action with the existing
validator and approval gates, records evidence, then carries that evidence into
the next step.

### LRN-T / LR-T

LRN-T is the write path that stores successful validated total-task structures.
LR-T is the lookup path that reuses one when the current prompt and runtime
context match closely enough.

### LR Direct / LR-EX

LR Direct replays exact validated streaming-step command or Python actions.
LR-EX can reuse an equivalent payload-aware shape after a structured LLM judge
approves the match and maps prior outputs safely.

### DAG

A directed acyclic graph of actions and dependencies. If action 2 needs action
1's output, the DAG records that.

### Gateway

The controlled process-execution service used for shell commands and terminal
sessions. The Agent UI stores a gateway registry and a separate saved terminal
cwd per gateway.

### Runtime Event

A persisted scheduled prompt. It stores title, prompt, interval, status, saved
runtime context, gateway/cwd, and run history.

### Result Bundle

The collected execution results for a request.

### Confirmation

A pause before mutating or risky work. Read-only actions can run without
confirmation; mutation requires approval.

### Clarification

A pause because the LLM needs one user answer before it can plan safely.

### Memory Entry

A durable user-approved instruction or lesson stored in SQLite and retrieved
when relevant to a future request.

### Validation Policy Memory

A separate memory kind used only for context-sensitive validator behavior, such
as allowing literal source payloads that look like placeholders. It cannot
override hard safety checks.

### Visualization Pane

The live Mermaid diagram beside the Developer Trace. It is built from trace
events and final trace payloads, not from an extra LLM call.

### DisplayDocument

The structured document sent to the Agent UI so tables, markdown, code blocks,
errors, and sections can be rendered without scraping text.


## What To Read Next

- [02_request_lifecycle.md](02_request_lifecycle.md) for the precise flow
- [07_user_manual.md](07_user_manual.md) for UI and storage details
- [06_worked_example_memory_report.md](06_worked_example_memory_report.md) for
  one concrete example
