# Capability Evolution Layer

The Capability Evolution Layer extends the Learning Ledger from "remember this
lesson" into "turn run evidence into reviewed runtime improvements."

It records normalized evidence from completed requests, drafts typed
improvement proposals, applies safe proposal targets to local runtime stores,
and keeps executable backend changes behind an explicit code-editing workflow.


## What It Improves

The layer can propose five target kinds:

| Target kind | What it changes | Auto-applied after approval? |
| --- | --- | --- |
| `task_memory` | Active memory for future task strategy. | Yes, through `agent_memory.db`. |
| `validation_policy` | Active validator guidance for matching errors. | Yes, through `agent_memory.db`. |
| `prompt_patch` | A bounded guidance block in the active prompt DB. | Yes, with template validation and rollback metadata. |
| `capability_manifest_overlay` | Planner-visible additive metadata for existing capability manifests. | Yes, stored in the ledger and applied during registry export. |
| `executable_backend_patch` | A stored patch bundle for a future backend/code change. | No. It stops at `approved_pending_apply`. |

The layer does not let learned data bypass validation, approval, confirmation,
or execution policy. It only adds reviewed guidance to the local runtime.

This reviewable proposal workflow is separate from private learned runtime
caches such as LRN-T/LR-T total-task structures and LR Direct/LR-EX replay
entries. Those caches accelerate local reuse, can be cleared independently,
and still have to pass current validation and safety gates.


## User Workflow

Open the Learning Ledger page:

```text
/learning-ledger
```

The page now has two review lanes:

- Lessons: the older memory-oriented learning records.
- Proposals: typed capability evolution proposals.

For each proposal, review:

- target kind;
- status;
- confidence;
- source request id;
- draft payload;
- evidence;
- safety decision;
- applied artifact or apply error.

Common actions:

| Action | Result |
| --- | --- |
| Save | Updates the proposal summary or draft fields exposed by the UI. |
| Remember | Approves the proposal. Memory, prompt, and overlay targets are applied immediately. Executable backend patches become pending. |
| Apply Proposal | Applies an already approved proposal. This is mainly useful for manual retry after fixing a missing store or prompt. |
| Reject | Marks the proposal rejected with a reason. |
| Retire | Marks the proposal retired without applying it. |


## Proposal Statuses

| Status | Meaning |
| --- | --- |
| `draft` | Created by analysis, not yet approved. |
| `approved` | Approved and ready to apply. Most non-executable targets move quickly through this state. |
| `applied` | The proposal was applied to memory, prompts, or planner overlays. |
| `approved_pending_apply` | Approved executable patch bundle waiting for explicit code application. |
| `rejected` | Reviewed and rejected. |
| `retired` | No longer active or relevant. |


## Auto-Approval Rules

Auto-approval is controlled by:

```text
AOR_AGENT_LEARNING_LEDGER_AUTO_LEARN_ENABLED
```

and by the Agent UI runtime control:

```text
agent_learning_ledger_auto_learn_enabled
```

When auto-learning is enabled:

- deterministic proposals need confidence `>= 0.72`;
- LLM-drafted proposals need confidence `>= 0.85`;
- unsafe language blocks automatic approval;
- secrets block proposal creation or approval;
- risk relaxation blocks manifest overlays;
- executable backend proposals never auto-apply code.

Unsafe language includes approval bypass, validation bypass, confirmation
bypass, sandbox bypass, risk relaxation, and executable hot-load language.


## Deterministic Evidence Sources

The analyzer currently drafts proposals from:

- validator errors, starting with `unresolved_shell_placeholder`;
- failed command followed by corrected success;
- capability gap evidence in planning metadata;
- structured LLM proposal metadata when available.

Duplicate traces do not create duplicate proposals because proposals have
stable dedupe keys built from target kind, source error, target id, and
normalized draft content.


## Prompt Patch Behavior

Prompt patches write only to `artifacts/prompts.db`, not to checked-in prompt
template source files.

The applied block is bounded by markers:

```text
<<<CAPABILITY_EVOLUTION_GUIDANCE:{proposal_id}>>>
...
<<<END_CAPABILITY_EVOLUTION_GUIDANCE:{proposal_id}>>>
```

Before storing a prompt patch, the runtime:

1. loads the active prompt template;
2. inserts or replaces only that proposal's marked guidance block;
3. validates template syntax;
4. verifies existing template variables were not removed;
5. records rollback metadata containing the previous prompt body.


## Manifest Overlay Behavior

Manifest overlays are planner-visible only. They can add:

- `description_append`;
- `semantic_verbs`;
- `object_types`;
- `semantic_tags`;
- `output_object_types`;
- `output_fields`;
- `output_affordances`;
- `examples`;
- `safety_notes`.

They cannot alter execution backend, operation id, argument schema, output
schema, risk level, read-only status, mutation status, or confirmation
requirements.

Approved overlays are loaded from `agent_learning_ledger.db` when the runtime
builds the capability registry. They affect LLM-visible manifest export, not
the executable backend object.


## Executable Backend Patches

Executable backend patch proposals are intentionally conservative.

Approval stores the patch bundle and sets:

```text
approved_pending_apply
```

The Learning Ledger will not hot-load, write, or activate backend code. A human
must explicitly apply the code change through a normal code-editing workflow,
run required tests, and restart the runtime before the new backend behavior is
visible.


## API Routes

Proposal routes live beside the lesson routes:

```text
GET   /api/agent/learning-ledger/proposals
GET   /api/agent/learning-ledger/proposals/{proposal_id}
PATCH /api/agent/learning-ledger/proposals/{proposal_id}
POST  /api/agent/learning-ledger/proposals/{proposal_id}/approve
POST  /api/agent/learning-ledger/proposals/{proposal_id}/apply
POST  /api/agent/learning-ledger/proposals/{proposal_id}/reject
POST  /api/agent/learning-ledger/proposals/{proposal_id}/retire
```

Run detail responses also include proposals:

```text
GET /api/agent/learning-ledger/runs/{request_id}
```


## Storage

The Capability Evolution Layer uses the existing Learning Ledger database:

```text
artifacts/agent_learning_ledger.db
```

Override the path with:

```text
AOR_AGENT_LEARNING_LEDGER_DB_PATH
```

The database stores:

- request/run evidence;
- legacy lessons;
- capability insights;
- capability proposals;
- proposal application state;
- applied refs and apply errors.

It does not modify tracked seed memory or checked-in prompt template source by
default.


## Trace Events

Proposal lifecycle events are appended to the source request trace when a
source request is known:

- `learning.proposal.proposed`;
- `learning.proposal.auto_approved`;
- `learning.proposal.applied`;
- `learning.proposal.apply_failed`;
- `learning.proposal.rejected`;
- `learning.proposal.retired`;
- `learning.proposal.approved_pending_apply`.

Use the Developer Trace pane or trace API to inspect these events.


## Maintenance Rules

When adding new proposal kinds or applicators:

1. keep the proposal model typed;
2. add dedupe keys before proposal creation;
3. make safety checks stricter than normal memory checks;
4. ensure prompt/template mutations have rollback metadata;
5. keep manifest overlays additive and planner-only;
6. keep executable backend changes out of automatic learning;
7. add analyzer, safety, application, API, and UI tests.
