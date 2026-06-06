---
id: capability-reliability-kernel
title: Capability Reliability Kernel
kind: manual
tags: [reliability, weak-models, operator, recovery, verification]
summary: How OpenFabric records, repairs, verifies, and reports weak-model runtime failures.
order: 15
---

# Capability Reliability Kernel

The Reliability page shows how the operator recovered from weak-model failures:
bad JSON, bad plans, wrong cwd values, command errors, Python tracebacks, failed
verification, dropped final answers, and approval-envelope violations.

Smaller-model support is one reason this kernel exists. OpenFabric does not
assume a smaller model will always plan like a larger model; it records the
failure mode, applies bounded recovery, preserves approval envelopes, verifies
outcomes, and turns the result into evidence that an operator can inspect.

Open:

```text
/reliability
```

## What The Page Shows

| Area | Meaning |
| --- | --- |
| Summary | Total reliability events, runs, failures, recoveries, verifications, and model profiles. |
| Runtime controls | Reliability mode, verifier enforcement, probe budget, repair budget, weak-model action cap, and approval-envelope budget. |
| Models | Per-model weakness and recovery counters. |
| Failure taxonomy | Which failure classes the current model or fleet is producing most often. |
| Recent runs | Request-level recovery timelines. |
| Timeline | Failure detected, recovery chosen, plan compiled, envelope used, verifier result, and final outcome. |
| Evals | Deterministic weak-model simulation runs. |

## Runtime Modes

| Mode | Behavior |
| --- | --- |
| `aggressive` | Optional heavier profile. Compiles plans, records all events, and spends larger bounded recovery budgets. |
| `standard` | Records and verifies with more conservative recovery defaults. |
| `off` | Leaves existing operator safety in place but disables Reliability Kernel event recording and decisions. |

## Approval Envelopes

When a mutating plan asks for confirmation, OpenFabric records the approved
goal, gateway, cwd values, risk ceiling, and mutation budget.

Execution repair may continue automatically only when the repaired plan stays
inside that envelope. If it changes cwd, escalates risk, or uses too much
mutation budget, the run pauses for confirmation again.

## Reports

Select a run and use:

- `JSON` for machine-readable support evidence;
- `Markdown` for a customer-facing recovery report.

Reports include the timeline, failure taxonomy, recovery choices, verification
status, and approval-envelope state.

## Evals

Use `Run` in the Evals pane to execute deterministic weak-model cases. These do
not require a live LLM. They cover malformed structured output, placeholders,
wrong cwd, command failures, Python failures, stale verification, final-answer
loss, repeated repair loops, and ambiguous stdout.

The target score for safe tasks is at least 90 percent recovered or safely
blocked.
