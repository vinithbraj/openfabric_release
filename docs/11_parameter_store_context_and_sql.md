# Parameter Store Context And SQL Discovery

This document describes the current Parameter Store context model, the full
Parameter Editor, and how SQL discovery metadata is used by the agent.


## Core Split

Each Parameter Store entry has two top-level JSON areas:

| Field | Purpose | Prompt Exposure |
| --- | --- | --- |
| `value_json` | Executable data: credentials, hosts, ports, tokens, paths, IDs, database names, SQL `schema_catalog`, `relation_foreign_scheme`, and `schema_discovery`. | Masked summaries only unless execution needs the raw value. |
| `context_json` | Non-secret domain guidance for planning, SQL prompting, clarification resolution, and parameter matching. | Bounded summaries in normal prompts; richer matched slices for clarification. |

`parameter_shell_env` and raw execution context are derived from `value_json`
only. They never include `context_json`.

The old `user_provided_data_organization` value is no longer part of the
runtime contract. Store that information under `context_json` instead.


## Canonical Context Shape

All parameter entries share this context shape, regardless of whether the
parameter is a SQL database, token, path, project profile, service endpoint, or
other reusable value:

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

Use the fields as follows:

| Field | Use |
| --- | --- |
| `summary` | Short human description of what the parameter represents. |
| `prompt_guidance` | Compact instructions safe to include in normal agent prompts. |
| `clarification_guidance` | Richer guidance used when the runtime is about to ask a question. |
| `concepts` | Named domain objects, user phrases, synonyms, preferred tables/files/options, or evidence hints. |
| `metrics` | Reusable metric definitions, thresholds, formulas, cohort criteria, or preferred aggregation behavior. |
| `relationships` | Semantic relationships between concepts, tables, services, files, workflows, or business entities. |

Context should be non-secret. The runtime still redacts secret-looking fields
defensively before prompts and traces.


## Prompt And Clarification Use

Normal prompts receive bounded context:

- `summary`;
- `prompt_guidance`;
- compact concept, metric, and relationship indexes.

Clarification resolution receives matched context:

- `clarification_guidance`;
- matched concepts, metrics, and relationships;
- available options or schema/entity candidates;
- risk flags and the proposed user-facing question.

The typed resolver can return:

- `continue_with_assumption`, with selected entities, assumptions, and
  execution directives; or
- `ask_user`, with a user-facing question.

Context guidance never bypasses validators, SQL safety checks, command
confirmations, credential secret-input flows, or auto-approve policy.


## Parameter Editor

The full editor is available at:

- `/parameter-editor`
- `/parameter-editor?key=canonical_v1`
- `/prompt-editor?mode=parameters&key=canonical_v1`

The existing prompt/memory editor shell now has a `Parameters` mode. Existing
Parameter Store drawer rows include an Open Editor action that opens this full
page.

The editor separates:

- Identity: key, description, aliases, tags, and sensitive toggle.
- Value: dedicated `value_json` JSON editor.
- Context: structured fields for summary, prompt guidance, clarification
  guidance, concepts, metrics, and relationships, plus an advanced raw
  `context_json` editor.
- Inspector: masked summary, schemas, environment names, audit data, and
  validation errors.

Sensitive values load masked. Reveal uses the audited reveal endpoint before
showing raw `value_json`. Users can edit `context_json` without revealing
secrets, and can replace `value_json` without seeing the old value.


## SQL Discovery

`/discoverdb` discovers database schemas on each run. Preview returns
reviewable drafts. Commit creates new database parameters or updates existing
database parameters.

Generated SQL discovery fields live in `value_json`:

- `schema_catalog`;
- `relation_foreign_scheme`;
- `schema_discovery`.

`context_json` is preserved during refresh. Doctors or domain users add DICOM
hierarchy notes, clinical definitions, cohort definitions, evidence sources,
and clarification rules through the Parameter Editor context section.

If the database schema changes, the user should run `/discoverdb` again and
commit the update. The runtime does not silently rewrite stored schema metadata
outside that review-and-commit flow.


## SQL Prompting

For normal SQL questions, the SQL agent prefers stored schema metadata from the
matched database parameter. It falls back to live discovery only when stored
metadata is missing or a request asks for refresh behavior.

SQL prompts receive:

- bounded schema summary from `value_json.schema_catalog`;
- autodetected `relation_foreign_scheme`;
- bounded domain context from `context_json`;
- selected clarification assumptions when the typed resolver auto-resolved an
  entity match.

The SQL prompt explicitly reminds the model that parameter context and inferred
relationships are prompt-only guidance, not validation approval.


## SQL Join Validation

The validator separates safety from semantic usefulness:

| Classification | Behavior |
| --- | --- |
| DB-declared FK join | Approved silently. |
| Inferred/user-context/DICOM semantic join | Allowed for read-only SQL with `join_relationship_warnings`. |
| Join with no known FK or semantic evidence | Allowed for read-only SQL with a louder warning. |
| Unknown table/column, parse failure, multiple statements, unsafe SQL, mutation without confirmation, invalid limit | Rejected. |

Read-only SQL with join warnings executes normally. Mutating or
confirmation-gated SQL still uses the existing confirmation path. Request-scoped
auto-approve can continue only after a confirmation has been generated; it does
not convert hard validation rejects into executable SQL.


## Clarification Mode

Clarification behavior is controlled by one runtime setting:

| Mode | Behavior |
| --- | --- |
| `auto_pilot` | Make high-confidence assumptions for most missing details. Confirmations still protect mutations. |
| `balanced` | Default. Auto-resolve obvious typos, plurals, casing, acronyms, and near-exact entity matches. |
| `pedantic` | Ask whenever a meaningful option is missing or ambiguous. |

Legacy `llm_operator_clarification_strategy` values are accepted only as
compatibility input and are normalized into `agent_clarification_mode`.

