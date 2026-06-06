---
id: capability-evolution-learning-ledger
title: Capability Evolution And Learning Ledger
kind: manual
tags: [learning-ledger, capability-evolution, memory, prompts, planner]
summary: How to review, approve, apply, and troubleshoot capability improvement proposals produced from run evidence.
order: 14
---

# Capability Evolution And Learning Ledger

The Learning Ledger now has two related review paths:

- lessons, which turn run feedback into reusable memory;
- capability proposals, which turn run evidence into typed runtime
  improvements.

Capability proposals are local, reviewable, and reversible where the target can
be reversed. They do not grant the model new execution authority by themselves.

## Open The Page

Use:

```text
/learning-ledger
```

The page shows:

- run, draft, approval, proposal, and suspect-cache counts;
- lesson filters and lesson cards;
- proposal filters and proposal cards;
- recent Learning Ledger runs;
- a detail pane with editable text and raw evidence.

## Reading A Proposal

Select a proposal card and inspect:

| Field | Meaning |
| --- | --- |
| Target kind | Which runtime surface the proposal wants to improve. |
| Status | Draft, approved, applied, pending apply, rejected, or retired. |
| Confidence | Analyzer or LLM confidence. |
| Source | Deterministic, LLM, or user. |
| Target id | Memory kind, prompt key, capability id, or backend patch target. |
| Draft | The typed change that would be applied. |
| Evidence | Trace details, safe examples, blocked examples, or capability gap details. |
| Safety decision | Why the proposal is or is not eligible for automatic approval. |
| Applied ref | The memory id, prompt version, or overlay ref after application. |
| Apply error | The most recent application failure, if any. |

## Actions

| Button | Lesson behavior | Proposal behavior |
| --- | --- | --- |
| Save | Saves the lesson instruction. | Saves the proposal summary. |
| Remember | Approves and mirrors a lesson into memory. | Approves and applies safe target kinds. |
| Reject | Rejects the selected lesson. | Rejects the selected proposal. |
| Retire | Retires the selected lesson. | Retires the selected proposal. |
| Restore | Restores a lesson. | Not used for proposals. |
| Apply Proposal | Not used for lessons. | Applies an already approved proposal. |
| Digest Note | Adds a note to a lesson. | Not used for proposals. |

## Target Kinds

### Task Memory

Use this for reusable task strategy, tool selection, command corrections, and
output interpretation.

Approval creates or updates active memory in `agent_memory.db`.

### Validation Policy

Use this for validator behavior only. A validation policy should explain how a
context-sensitive validator should interpret evidence.

Example: literal C++ source text can contain `<iostream>`, which looks like a
shell placeholder but is valid inside a quoted heredoc.

Approval creates or updates active validation-policy memory in
`agent_memory.db`.

### Prompt Patch

Use this when repeated evidence shows a prompt template needs bounded guidance.

Approval updates `artifacts/prompts.db` by inserting or replacing a marked
guidance block. It validates template syntax and preserves existing template
variables. Rollback metadata is stored with the prompt template record.

Prompt patch approval does not edit checked-in prompt template source files.

### Capability Manifest Overlay

Use this when the planner needs better semantic metadata for an existing
capability.

Allowed additions include:

- semantic verbs;
- object types;
- semantic tags;
- output object types;
- output fields;
- output affordances;
- examples;
- safety notes;
- description appendix.

The overlay cannot lower risk, remove confirmation, change backend operation,
change schemas, or alter execution policy.

Private learned runtime caches such as LRN-T/LR-T and LR Direct/LR-EX are
separate from this reviewed proposal workflow. They may speed up repeated local
requests, but they still pass current validation and can be cleared without
changing Learning Ledger records.

### Executable Backend Patch

Use this when the evidence points to a real code/backend change.

Approval stores the patch bundle and marks it `approved_pending_apply`. The
Learning Ledger never hot-loads or writes executable backend code. Apply the
patch through the normal repository editing workflow, run tests, and restart
the runtime.

## Auto-Learning

Auto-learning is enabled when both settings allow it:

```text
AOR_AGENT_LEARNING_LEDGER_ENABLED=true
AOR_AGENT_LEARNING_LEDGER_AUTO_LEARN_ENABLED=true
```

The Agent UI runtime control `agent_learning_ledger_auto_learn_enabled` can
also disable auto-approval.

Auto-approval thresholds:

| Source | Required confidence |
| --- | ---: |
| Deterministic proposal | 0.72 |
| LLM proposal | 0.85 |

Auto-approval is blocked by:

- approval-bypass language;
- validation-bypass language;
- confirmation-bypass language;
- sandbox-bypass language;
- secrets or credential-like text;
- risk relaxation;
- executable backend hot-load language;
- a safety decision marked unsafe.

Executable backend proposals may be approved to pending status, but they never
auto-apply code.

## Good Review Habits

Before approving:

1. Check that the proposal target kind matches the evidence.
2. Prefer `task_memory` for workflow advice.
3. Use `validation_policy` only for validator behavior.
4. Reject prompt patches that duplicate existing prompt guidance.
5. Reject overlays that try to change risk, confirmation, or backend behavior.
6. Leave executable patches pending until a maintainer can apply and test code.

## Troubleshooting

### A proposal did not auto-approve

Check confidence, source, safety decision, and the auto-learning runtime
control. LLM proposals require a higher confidence threshold than deterministic
proposals.

### A memory proposal failed to apply

Memory must be enabled for `task_memory` and `validation_policy` targets.
Prompt patches and manifest overlays do not require memory.

### A prompt patch failed

Open the proposal detail and read `apply_error`. Common causes are:

- unknown prompt key;
- invalid `$` template syntax;
- guidance that removed an existing template variable.

### A manifest overlay seems invisible

Manifest overlays are loaded when the runtime builds the capability registry.
Restart the Agent UI server after approving an overlay if the current runtime
was already running.

### A backend patch is pending forever

That is expected. The ledger stores executable patch proposals for explicit
human application only. Apply the patch in code, run tests, and restart.

## API Routes

Proposal review routes:

```text
GET   /api/agent/learning-ledger/proposals
GET   /api/agent/learning-ledger/proposals/{proposal_id}
PATCH /api/agent/learning-ledger/proposals/{proposal_id}
POST  /api/agent/learning-ledger/proposals/{proposal_id}/approve
POST  /api/agent/learning-ledger/proposals/{proposal_id}/apply
POST  /api/agent/learning-ledger/proposals/{proposal_id}/reject
POST  /api/agent/learning-ledger/proposals/{proposal_id}/retire
```

Run details include proposal evidence:

```text
GET /api/agent/learning-ledger/runs/{request_id}
```
