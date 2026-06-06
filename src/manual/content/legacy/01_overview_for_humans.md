# Runtime Overview For Humans

This document explains the runtime without assuming you already think like a
planner, compiler, or distributed systems engineer.


## What This System Is

The system takes a plain-language request and turns it into safe, structured
local work.

It is not a design where the LLM freely writes commands and the machine blindly
runs them. The LLM helps with meaning, but the runtime owns validation, safety,
approval, terminal routing, and execution boundaries.

The current default execution model is operator-backed:

- shell work is represented as `operator.shell_command`;
- Python programs are represented as `operator.python_action`;
- Python transformations are represented as `operator.python_transform`;
- runtime self-inspection stays deterministic through `runtime.*`.
- saved interval work is represented as persistent runtime events that re-enter
  the same request pipeline later.

The default execution model is granular and streaming. Non-trivial Agentic
requests are decomposed first, one scoped task is executed at a time, and each
step feeds its evidence into the next decision. Atomic requests may still
naturally become one task and one action, but complex work is not collapsed
into a single generated action.


## The Four Main Actors

### The LLM

The LLM helps answer questions like:

- What is the user asking for?
- Is this one task or several tasks?
- Is the request clear enough, or should we ask one question?
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

### Runtime Events

Runtime events are saved prompts with interval schedules. They let the server
run requests such as "check git status every hour" without the browser staying
open.

Events persist in SQLite, remember their gateway and working directory, and run
through the same agent path as normal prompts. If an event is configured to
auto-approve, it only clicks the confirmation prompt that the runtime already
generated; it does not bypass validation or answer clarifications.


## What Happens When You Type A Prompt

For a request like:

```text
list all docker images and calculate the total size in GB
```

the rough flow is:

1. The UI sends the prompt and current settings to the runtime.
2. The LLM may ask a clarification only if user intent is genuinely missing.
3. The runtime creates a self-brief and classifies task/tool/intent.
4. The memory bank retrieves only relevant memories for that specific request
   and turns them into planning/final-answer directives.
5. The LLM authors a plan, usually with a machine-readable shell command and a
   Python calculation step.
6. If Python depends on shell output, code generation can wait until the real
   stdout shape exists.
7. The runtime checks command/code shape, safety, dataflow, and approval needs.
8. Read-only work runs; mutating work pauses for approval.
9. Results stream into command capsules.
10. Observation review can repair failures or ask for user intent if needed.
11. The final response is rendered in Detailed or Simple mode.
12. The user can give run feedback, which can become a proposed memory.
13. Trace events also feed the Developer Trace and Visualization panes so the
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
