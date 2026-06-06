# LLM Stages And Contracts

This document explains where the LLM is involved, what it may decide, and what
the runtime checks before trusting the result.

The core contract is:

> The LLM proposes typed structure.
> The runtime validates it.
> Execution only sees trusted actions.


## Where Prompt Builders Live

There is no active `prompts/` directory driving the runtime.

Prompt builders live in the stage modules that call the LLM. Structured calls
are routed through:

- `src/agent_runtime/llm/client.py`
- `src/agent_runtime/llm/structured_call.py`

Memory guidance is injected through:

- `src/agent_runtime/memory/prompting.py`


## Shared Structured-Call Contract

LLM-assisted stages generally:

1. build a compact stage-specific prompt;
2. include clarification answers and relevant memory when available;
3. ask for JSON only, or one strict Python function body;
4. validate against a Pydantic schema or structural code contract;
5. on schema validation failure only, ask for one schema-only repair when the
   invalid payload is JSON-like and close enough to repair;
6. reject, repair, or accept;
7. emit trace diagnostics.

Common structured-call diagnostics include:

- `transport_error`;
- `invalid_json`;
- `schema_validation_error`;
- `empty_response`;
- `parsing_error`;
- `python_action_contract_shape`;
- `python_transform_contract_shape`;
- `plan_review_revision_validation_failed`.

Transport errors, empty responses, and invalid JSON extraction failures do not
trigger structured-output repair. If the repair payload still fails validation,
the runtime reports a structured-output failure instead of weakening the
schema.


## Stage Inventory

| Stage | File | Why LLM Is Used | Runtime Guardrail |
| --- | --- | --- | --- |
| Clarification decision | `operator/pipeline.py` | ask only when user intent is missing | typed `clarification_required`, max rounds |
| Self brief | `operator/pipeline.py` | classify task/tool/intent and risks | schema validation |
| Event draft | `api/agent_ui.py`, `events/` | extract interval events from scheduling prompts | draft only, user saves |
| Memory context draft | `api/agent_ui.py`, `memory/` | prefill memory fields from request/response | proposal only, user can edit |
| Memory feedback draft | `api/agent_ui.py`, `memory/` | draft editable feedback text | proposal only |
| Memory optimization | `memory/` | suggest merges/retirements/rewrites | never mutates automatically |
| Memory compliance review | `operator/pipeline.py` | check plan/cache against applied directives | repair once or explain ignore |
| Operator planning | `operator/pipeline.py` | author shell/Python action plans | action schema and graph validation |
| Plan review | `operator/pipeline.py` | catch logical plan gaps | revised plan revalidated |
| Validation adjudication | `operator/pipeline.py`, `memory/` | adjudicate context-sensitive validator failures | hard safety non-overridable |
| Deferred Python generation | `operator/pipeline.py` | write code after upstream shape is known | strict function shape |
| Code-only repair | `operator/pipeline.py` | fix malformed/failing Python body | same action revalidated |
| Observation review | `operator/pipeline.py` | decide finalize/repair/ask/continue/fail | bounded loop and approval unchanged |
| Final formatter | `output_pipeline/`, `operator/final_formatter.py` | compose final answer | source artifact checks and fallback |
| Name suggestion | `api/agent_ui.py` | suggest an agent display name | UI preference only |
| Conversational follow-up | `operator/pipeline.py` | answer from context or plan actions | bounded context and normal validation |


## Clarification Contract

A clarification decision may return:

- continue; or
- ask the user one question.

When asking, the result must include:

- `status: "clarification_required"`;
- question;
- reason;
- missing information;
- exactly three suggested options;
- `allow_freeform: true`;
- confidence.

Clarifications are for user intent, preferences, irreversible choices,
credentials, or ambiguous targets. They are not for discoverable local facts.


## Granular Decomposition Contract

For complex Agentic tool work, the LLM should prefer small independent
subtasks over a single monolithic action. Each step must be executable and
verifiable on its own, with outputs and evidence made available to the next
step. If the request is already atomic, the decomposer may emit one task and
the operator may execute one action.

Every decomposed task still uses ordinary validation, policy, cwd, binding,
and approval checks before execution.


## Event Draft Contract

The event draft stage may return:

- not a schedule request; or
- one or more editable interval event drafts.

Drafts include title, prompt to run, interval seconds, schedule summary,
timezone, missing details, confidence, saved runtime context, and the
auto-approve confirmation preference.

The draft stage never executes the requested task. The user must save the draft
before it becomes an active event.


## Memory Contract

Relevant memory is injected as request-specific constraints:

- memory id;
- summary;
- instruction;
- scope;
- model name/family;
- task/tool/intent;
- tags.
- match reasons;
- directive strength;
- safe and blocked examples where available.

Prompt rule:

> Follow user-approved memories unless contradicted by the current user
> request, live runtime evidence, validation contracts, or safety policy.

Memory retrieval is not a blob dump. It happens after self-brief
classification, and entries must pass relevance checks before they are used.
The retrieval context may be enriched by deterministic domain hints for obvious
Git and Docker prompts so generic self-brief labels do not hide relevant
memories.

Applied entries become `MemoryDirective` objects with applies-to targets such
as direct answer, operator plan, plan review, final answer, and cache. When
relevant directives are present, the operator can run a
`MemoryComplianceReview` before deterministic validation.

Validation-policy memories are a separate `memory_kind`. They are retrieved
only for matching context-sensitive validator errors and never override hard
safety rules.


## Operator Planning Contract

The LLM may return actions of these kinds:

- `shell_command`;
- `python_action`;
- `python_transform`.

Each action includes:

- id;
- label;
- kind;
- dependencies;
- input bindings;
- cwd where applicable;
- risk;
- timeout;
- reason;
- declared output shape;
- interaction mode and execution mode for shell commands.

The runtime rejects:

- invented action kinds;
- cycles;
- duplicate ids;
- missing dependencies;
- unresolved placeholders;
- invalid input bindings;
- terminal-detached actions with downstream dependencies;
- Python code that does not start with the required function signature.

If retrieved memory requires a planning shape, such as "prove this is a git
work tree before `git add` or `git commit`", a memory compliance review can
request one repaired plan before deterministic validation continues.


## Dynamic Validation Adjudication Contract

The deterministic validator always runs first. For marked context-sensitive
errors, the LLM can be asked to adjudicate whether the failure is actually
literal payload content, a real repair need, a block, or a user question.

The adjudication payload includes:

- decision;
- validation error;
- affected action id;
- literal payload evidence;
- why safe;
- repair guidance;
- confidence.

Low-confidence adjudication cannot allow execution. Disabled shell execution,
forbidden destructive commands, missing approvals, invalid Python contracts,
and execution-policy violations are hard boundaries.


## Shell Command Contract

For `shell_command`, the LLM returns:

- concrete command;
- cwd;
- risk;
- declared output shape;
- timeout;
- reason;
- `interaction_mode`;
- `execution_mode`.

Prompt guidance asks the LLM to prefer machine-readable command output when the
tool supports it: JSON, `--format`, tab-separated fields, explicit columns, or
quiet flags.

If an operation may need a passphrase, password, credential prompt, or
controlling terminal, it must be a `shell_command` with
`interaction_mode: "may_prompt"`, not a Python subprocess.


## Python Action Contract

For `python_action`, code must start exactly with:

```python
def main(inputs):
```

Use `python_action` when one Python program should run commands, inspect files,
manipulate data, and produce the final answer.

The runtime structurally validates code, runs it as trusted local Python, and
uses action risk plus approval policy to gate mutation. Python may use normal
stdlib modules, local packages, filesystem APIs, and subprocesses when the
approved action allows it.


## Python Transform Contract

For `python_transform`, code must start exactly with:

```python
def transform(inputs):
```

Use `python_transform` when the Python step mainly transforms declared upstream
inputs.

Generated imports should live inside the function body. The runtime preloads
common stdlib modules such as `re`, `json`, `math`, `csv`, and `datetime` as a
resilience measure, but generated code should still import what it uses.


## Deferred Python Contract

If Python depends on prior action output, planning may defer code generation.

The later code-generation prompt includes:

- user goal;
- action label and reason;
- input names and source fields;
- bounded stdout/stderr/output previews;
- output shape;
- prior validation/execution errors when repairing;
- exact required function signature.

This is the main defense against code that guesses a CLI output format before
the CLI has actually run.


## Observation Review Contract

After execution, the LLM can return:

- finalize;
- propose more actions;
- repair;
- ask the user one clarification;
- fail with evidence.

Observation review sees both success and failure records. It should treat live
runtime output as stronger evidence than memory or prior assumptions.


## Final Formatter Contract

The final formatter exists to avoid dumping raw stdout as the final answer.

The formatter receives action summaries, result metadata, and bounded source
previews according to the configured final-response mode. It must not invent
rows, totals, files, or success states.

When the answer judge is enabled, final output is checked against required
artifacts. For example:

- if source rows exist and the user asked to list them, an empty table fails;
- if a computed total is zero while non-empty numeric inputs exist, repair is
  attempted unless zero was explicitly allowed.

Scheduled event runs use detailed final output so the Event History pane stores
useful result text rather than only "completed" status.


## Feedback-To-Memory Contract

Post-run feedback is captured as a typed object:

- outcome;
- request id;
- prompt;
- run status;
- final response;
- error;
- model name;
- applied memory ids/counts.

The LLM may draft a memory proposal from the user's feedback. The proposal is
stored as `proposed`; it becomes active only after the user applies it in the
Memory drawer.

Feedback can be classified as task memory, preference memory, or validation
policy. The validation feedback UI is only a hint: if the content is really
workflow advice or output interpretation, the draft should become normal task
memory.


## Prompt Hardening And Diagnostics

Current prompt hardening choices:

- compact JSON-only prompts;
- explicit stage responsibilities;
- examples only where they reduce ambiguity;
- machine-readable command-output guidance;
- "do not ask for discoverable facts" guidance;
- terminal-prompt routing guidance;
- Python function-shape reminders;
- structured validation feedback;
- schema-only structured-output repair prompt;
- memory constraints and memory compliance repair guidance;
- validation-policy adjudication prompt scoped to context-sensitive failures;
- event-draft prompt that never executes the scheduled task;
- exact exception/code/input previews for repair;
- trace events for proposed, rejected, repaired, accepted, and memory-applied
  artifacts.


## Anti-Drift Choices

The choices that most directly reduce drift are:

1. one shared operator-native pipeline;
2. known semantic verb and object vocabularies;
3. runtime-owned backend candidate sets;
4. concrete commands at approval time;
5. deferred Python after real upstream output;
6. terminal routing for prompt-shaped commands;
7. targeted memory retrieval after classification;
8. actionable memory directives plus compliance review;
9. deterministic safety and approval;
10. code-only repair before broad plan repair;
11. structured-output repair without schema weakening;
12. trace and visualization visibility for every rejected plan/action.
