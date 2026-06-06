# Agent Memory Seed And Capability Store

`artifacts/agent_memory.db` is intentionally tracked as the portable seed
memory database for this repository.

The database is meant to act as both:

- a reusable agent memory library;
- a capability-style guidance store for common tools and workflows.

It is not a live personal runtime log.

The memory seed DB is intentionally the only reusable runtime-state database
checked in by default. Treat it like a capability catalog: curated, portable,
and safe for other users. The separate `artifacts/prompts.db` seed stores LLM
prompt templates, not live runtime history.

Capability evolution proposals created by the Learning Ledger do not belong in
this seed DB automatically. They live in `artifacts/agent_learning_ledger.db`
until reviewed. Only promote a learned memory, prompt guidance block, or
manifest-overlay idea into tracked seed artifacts after a separate curation
step.


## What Belongs Here

Reusable, user-approved instructions that help any agent choose safer and more
useful actions belong in this database.

Good entries are scoped and conditional:

- when to use a tool;
- when not to use that tool;
- what output shape or flags are preferred;
- what empty output means;
- what examples are safe;
- what examples are blocked.

Examples:

- For Docker status checks, use `docker ps`; do not start containers.
- For Git commits, prove the directory is a repository before `git add` or
  `git commit`.
- For HTTP probes, use timeouts and do not print authorization headers.
- For Kubernetes work, check context and namespace before mutations.
- For Python tooling, prefer `python -m ...` so the active interpreter owns the
  command.
- For SQL work, inspect schema first, keep read-only queries read-only, and use
  transactions/backups for mutations.


## What Does Not Belong Here

Do not check in memory entries containing:

- personal paths that are not generally useful;
- private gateway URLs or LAN addresses;
- secrets, tokens, passwords, passphrases, or key material;
- one-off run traces;
- prompt caches;
- learned total-task structures;
- LR Direct or LR-EX replay entries;
- command template caches;
- command output caches;
- scheduled event run history;
- stale plans tied to a specific model or workspace.

Those belong in local runtime artifact databases, not the portable seed DB.


## Capability-Style Encoding

Capability memories should use the existing memory fields as structured
metadata:

| Field | Purpose |
| --- | --- |
| `memory_kind` | Usually `task_memory` for reusable tool/capability guidance. |
| `task_type` | Broad domain such as `git`, `python`, `docker`, `sql`, `http`, `cloud`, `node`, or `system`. |
| `tool_type` | Usually `shell_command`, `python_action`, `apply_patch`, or another runtime action kind. |
| `intent_type` | Narrow intent such as `pytest`, `api_probe`, `containers`, `repo_workflow`, or `diagnostics`. |
| `tags` | Tool and domain tokens used by retrieval, such as `curl`, `kubectl`, `pytest`, or `read_only`. |
| `safe_examples` | Good command shapes or situations. |
| `blocked_examples` | Command shapes that should not be selected for that intent. |

Prefer narrow intent fields. Broad memories like “use Docker Compose” can bias
the agent incorrectly; better wording is “use `docker compose up -d` only when
the user explicitly asks to start Compose services.”

Use `validation_policy` only when the memory is truly about validator behavior,
such as distinguishing literal source payload from a placeholder false
positive. Workflow advice, command-output interpretation, and verification
rules should stay as `task_memory`.


## Retrieval Behavior

The runtime retrieves memory after a self-brief or classification stage, not by
dumping the whole database into every prompt.

Retrieval uses:

- model scope;
- `memory_kind`;
- `task_type`;
- `tool_type`;
- `intent_type`;
- validator error type for validation policies;
- tags;
- rare text overlap.

Unstructured global memories need strong overlap or a tag/domain match. This is
intentional; otherwise a broad Docker or Git memory can bias unrelated tasks.

The runtime also has conservative deterministic hints for obvious domains. For
example, a prompt containing commit/push/repo language can be promoted to the
Git retrieval context even if an LLM self-brief says the goal is generic. A
prompt like "stage the presentation" should remain generic and retrieve no Git
memory.


## Current Seed Shape

At the time this seed was created, the DB contained:

- total entries: 524;
- active entries: 521;
- retired entries: 3;
- task-memory entries: 523;
- preference-memory entries: 1;
- validation-policy entries: 0;
- SQL entries: 80;
- Python entries: 205.

Major reusable domains include:

- shell and CLI search;
- filesystem inspection;
- Git and GitHub;
- Docker;
- Python, pytest, linting, formatting, data parsing, packaging, and profiling;
- Node/frontend workflows;
- HTTP/API probing;
- SQLite, DuckDB, Postgres, MySQL, and Redis inspection;
- SQL query authoring, read-only inspection, joins, aggregation, mutation
  safety, transactions, indexes, migrations, dialect handling, and injection
  safety;
- system diagnostics;
- archives and file integrity;
- credential-sensitive workflows;
- cloud/infra tools such as `kubectl`, `helm`, `terraform`, `aws`, `gcloud`,
  and `az`.


## Related Local Databases

Only reusable seed artifacts should be tracked by default:

- `artifacts/agent_memory.db`;
- `artifacts/prompts.db`.

Do not track:

- `artifacts/runtime.db`;
- `artifacts/agent_learning_ledger.db`;
- `artifacts/agent_lrn_total_tasks.db`;
- `artifacts/agent_plan_cache.db`;
- `artifacts/agent_command_template_cache.db`;
- `artifacts/agent_computation_cache.db`;
- `artifacts/agent_events.db`;
- `artifacts/agent_gateways.db`.

Those databases contain local runtime history, caches, scheduled event runs, or
machine-specific gateway configuration.

Gateway cwd, scheduled events, request traces, learned total-task structures,
plan caches, command template caches, and computation caches are useful locally
but should not be reused as shared capability data without a separate curation
step.


## Maintenance Checklist

Before updating the tracked memory DB:

1. Keep entries reusable across machines and users.
2. Avoid personal paths, credentials, and live runtime outputs.
3. Use structured fields so retrieval can stay narrow.
4. Include blocked examples for tools that can mutate state.
5. Test at least one positive and one negative retrieval case for broad new
   domains.
6. Run:

```bash
python -m pytest tests/test_agent_memory.py -q
git diff --check
```

If a memory caused a bad plan, do not only add a competing memory. Narrow or
retire the broad memory that created the bad bias.

For large additions, prefer many small scoped memories over one giant
instruction block. The memory system can rank, dedupe, and cap small entries
more safely.
