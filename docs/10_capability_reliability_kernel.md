# Capability Reliability Kernel

The Capability Reliability Kernel turns weak-model failures into typed runtime
events with deterministic recovery decisions, stored evidence, bounded approval
envelopes, and outcome verification.

It is designed for the local Agent UI first. The default posture is
`aggressive`, which assumes the active LLM may produce malformed structured
output, vague plans, wrong cwd values, failed commands, brittle Python, or
final answers that omit authoritative execution values.

This is part of the local-first smaller-model pitch. OpenFabric makes smaller
models more useful for agent work by treating weak outputs as recoverable,
inspectable runtime events: typed contracts define what was expected,
validation records what failed, approval envelopes bound what can change, and
recovery plus evals show whether the runtime safely repaired or blocked the
work.

## Runtime Responsibilities

The kernel owns runtime reliability state, not safety authority.

Authoritative gates remain in the existing operator validator, policy modules,
memory compliance checks, approval flow, and gateway execution boundary. The
kernel records what failed, chooses the next recovery shape, and reports what
happened.

Core contracts live under `agent_runtime.reliability`:

| Contract | Role |
| --- | --- |
| `FailureKind` | Stable taxonomy for schema, JSON, validation, cwd, dependency, command, Python, verification, completion, formatting, loop, budget, and envelope failures. |
| `ReliabilityEvent` | Persisted evidence packet for failures, plan compilation, decisions, accepted repairs, probes, envelopes, verification, and outcomes. |
| `ModelCapabilityProfile` | Per-model counters for weakness and recovery behavior. |
| `RecoveryDecision` | Deterministic action such as `retry_same`, `repair_plan`, `run_probe`, `ask_user`, `block`, or `finalize`. |
| `RecoveryBudget` | Per-request budget shape for schema, validation, execution, probes, continuations, rephrases, and envelope mutation use. |
| `ApprovalEnvelope` | Confirmation-time boundary for goal, cwd, gateway, risk, and in-envelope mutation recovery. |
| `EvidenceObligation` | LLM-audited semantic fact or postcondition that the final answer may need to preserve. |
| `AnswerCoverageReview` | LLM-authored semantic review of whether the final answer covered required evidence, contradicted it, or added unsupported claims. |

## Plan Compiler

Before execution, the operator passes candidate plans through the reliability
compiler. It records diagnostics for:

- missing or invalid cwd values;
- unresolved shell placeholders;
- weak-model action-cap pressure;
- plan shape that should be repaired before execution.

Compiler diagnostics become trace events and persisted reliability events. They
do not bypass normal validation.

## Bounded Approval Envelope

When a mutating plan asks for confirmation, the kernel stores an approval
envelope containing the visible goal, gateway identity, cwd values, action ids,
maximum risk, and mutation budget.

Execution repair can run a repaired mutating plan without another prompt only
when it stays inside that envelope:

- no new cwd;
- no higher risk;
- no mutation budget overflow.

Any violation is persisted as `approval_envelope_violation` and the runtime
returns to user confirmation.

## Outcome Verification

Every completed operator run receives a typed verification result:

- `satisfied`;
- `partially_satisfied`;
- `unsatisfied`;
- `unknown`.

The verifier combines execution records, runtime status, and final-response
presence. Final-result metadata includes the verification payload, and the
trace API exposes a compact reliability summary.

For semantic evidence coverage, the kernel now uses an LLM-assisted
Authoritative Evidence Contract:

- an Evidence Auditor receives the user request, plan summary, verification
  hints, successful execution records, and bounded redacted output previews;
- the auditor returns compact `EvidenceObligation` records rather than
  command-specific parsed facts;
- the final formatter receives those obligations before writing the answer;
- a Coverage Verifier reviews the final answer for semantic coverage,
  paraphrase equivalence, contradictions, and unsupported claims;
- if coverage needs repair, the formatter retries within budget with the
  verifier's repair instruction;
- if budget is exhausted, execution can remain completed while reliability
  verification is marked `unsatisfied`.

Deterministic code is intentionally limited to schema validation, redaction,
budgeting, persistence, safety boundaries, and simple sanity checks.

## Storage

Reliability state is stored in:

```text
artifacts/agent_reliability.db
```

The database contains:

- `reliability_events`;
- `model_profiles`;
- `approval_envelopes`;
- `reliability_evals`.

The Learning Ledger remains the durable learning and proposal layer. Reliability
events are runtime audit evidence and model-profile data.

## API

Available endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/agent/reliability/profile` | Dashboard summary and all model profiles. |
| `GET` | `/api/agent/reliability/profile/{model_id}` | One model profile plus recent events. |
| `GET` | `/api/agent/reliability/runs` | Recent request recovery timelines. |
| `GET` | `/api/agent/reliability/runs/{request_id}` | One full recovery timeline. |
| `GET` | `/api/agent/reliability/runs/{request_id}/report.json` | Customer-facing JSON report. |
| `GET` | `/api/agent/reliability/runs/{request_id}/report.md` | Customer-facing Markdown report. |
| `GET` | `/api/agent/reliability/evals` | Deterministic eval summaries. |
| `POST` | `/api/agent/reliability/evals/run` | Run weak-model deterministic eval cases. |

`GET /api/agent/trace/{request_id}` also includes `reliability_summary`.

## UI

Open:

```text
/reliability
```

The page shows:

- model health cards;
- weakness breakdowns;
- failure taxonomy;
- recent autonomous recoveries;
- per-run event timelines;
- approval envelope and verification evidence;
- eval history and on-demand eval execution;
- JSON and Markdown report exports.

Runtime controls are also available from Mission Control:

- reliability mode: `off`, `standard`, `aggressive`;
- verifier enforcement;
- max recovery probes;
- max autonomous repair attempts;
- weak-model action cap;
- approval-envelope mutation budget.

## Eval Harness

The deterministic eval harness simulates common weak-model failures without a
live LLM:

- malformed JSON;
- unresolved placeholders;
- wrong cwd;
- bad shell syntax;
- command not found;
- fake Python success;
- stale verifier;
- dropped final result;
- dropped required version value;
- paraphrased required fact;
- unsupported final success claim;
- redacted sensitive value;
- repeated failed repair;
- ambiguous stdout.

The score rewards recovered success, safe blocks, and truthful finalization.
