---
id: parameter-store-context-sql
title: Adding Parameter Metadata For The Agent
kind: manual
tags: [parameters, parameter-store, metadata, context-json, value-json, prompt-guidance, clarification-guidance, concepts, metrics, relationships, sql, discoverdb, dicom, canonical, doctors, service-manual]
summary: A detailed guide for adding Parameter Store metadata so the agent can use values safely, understand domain semantics, and ask fewer unnecessary clarifications.
order: 16
---

# Adding Parameter Metadata For The Agent

Parameter metadata is how domain users teach the agent what a stored value
means without putting every detail into every prompt. It is useful for SQL
database profiles, credentials, API endpoints, project paths, service
configuration, clinical definitions, reporting metrics, and other reusable
objects.

The key idea is simple:

- `value_json` is for executable data the runtime may use.
- `context_json` is for non-secret guidance that helps the agent reason.

This page explains each section of the Parameter Editor, where the information
is used, what to enter, and what to avoid.

## Where To Edit Metadata

Open the full editor from the Agent UI or by URL:

- `/parameter-editor`
- `/parameter-editor?key=canonical_v1`
- `/prompt-editor?mode=parameters&key=canonical_v1`

The Parameter Store drawer also has an Open Editor action for existing rows.
Use the full editor when you want to edit context, concepts, metrics, or
relationships.

## The Runtime Contract

Every parameter has identity fields plus two JSON layers.

| Area | Purpose | Used By |
| --- | --- | --- |
| Identity | Key, description, aliases, tags, sensitive toggle. | UI search, parameter matching, audits, routing. |
| `value_json` | Executable values and generated machine metadata. | Execution context, shell environment names, SQL database connection and discovery. |
| `context_json` | Non-secret agent guidance and domain metadata. | Prompt construction, clarification resolution, parameter matching, SQL/entity selection. |

`context_json` is never placed in `parameter_shell_env` and never becomes raw
execution input. Raw execution values come from `value_json` only.

## Safety Rule

Parameter metadata helps the agent choose better assumptions. It does not give
the agent permission to skip runtime checks.

Context guidance cannot:

- reveal masked secrets;
- approve SQL joins as DB-declared foreign keys;
- bypass SQL table or column validation;
- bypass read-only or mutation checks;
- bypass command approval;
- bypass credential or private-key flows;
- override current user instructions;
- override live command output or database errors.

For SQL, context can make a read-only semantic join acceptable with a warning,
but only actual database-declared foreign keys count as approved joins.

## Identity Fields

### Key

The key is the durable name users and agents can refer to.

Good keys:

- `canonical_v1`
- `prod_reporting_db`
- `github_deploy_key`
- `imaging_project_root`
- `orthanc_public_api`

Use short stable names. Avoid spaces when possible.

### Description

Use the description for a human-readable label.

Example:

```text
Canonical DICOM/RT oncology PostgreSQL profile discovered from flathr schema.
```

Descriptions are visible in the UI and help people distinguish similar keys.

### Aliases

Aliases help the agent match natural language to the right parameter.

For a database profile:

```text
canonical, canonicaldb, canonical database, dicom, flathr
```

For a path:

```text
repo root, imaging repo, workspace
```

For a key:

```text
deploy key, ssh key, github push key
```

### Tags

Tags make filtering and search easier. They also make the manual information
discoverable by theme.

Common tags:

- `database`
- `postgresql`
- `sql`
- `dicom`
- `clinical`
- `credential`
- `api`
- `path`
- `project`
- `production`
- `staging`

For `canonical_v1`, good tags are:

```text
database, postgresql, sql, dicom, oncology, canonical, discoverdb
```

### Sensitive Toggle

Use Sensitive when `value_json` contains credentials, tokens, hosts that should
not be casually displayed, private keys, connection strings, or other
operational secrets.

Sensitive values load masked. Reveal is audited. You can still edit
`context_json` without revealing the sensitive value.

## Value JSON

`value_json` is the executable payload. It may be used by runtime code, SQL
connection logic, shell environment derivation, or capability execution.

Put these in `value_json`:

- database engine, host, port, user, password, dbname, schema;
- API base URLs and token values;
- filesystem paths;
- service IDs;
- SSH key paths or private-key material;
- SQL `schema_catalog`;
- SQL `relation_foreign_scheme`;
- SQL `schema_discovery`.

Do not put long domain explanations in `value_json`. Put them in
`context_json`.

Example database `value_json`:

```json
{
  "engine": "postgres",
  "host": "10.44.102.204",
  "port": 9501,
  "user": "readonly_user",
  "password": "••••",
  "dbname": "canonical_v1",
  "schema": "flathr",
  "schema_catalog": {
    "version": 1,
    "schemas": ["flathr"],
    "tables": []
  },
  "relation_foreign_scheme": {
    "version": 1,
    "source": "discoverdb",
    "approved_foreign_keys": [],
    "inferred_secondary_keys": []
  },
  "schema_discovery": {
    "status": "success",
    "refreshed_at": "2026-05-27T00:00:00Z"
  }
}
```

Example API `value_json`:

```json
{
  "base_url": "https://api.internal.example/v1",
  "token": "••••",
  "timeout_seconds": 30
}
```

Example project path `value_json`:

```json
{
  "path": "/home/user/work/imaging-platform",
  "default_branch": "main"
}
```

## Context JSON

`context_json` is non-secret metadata used to help the agent understand what a
parameter means. The canonical shape is:

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

The editor exposes each part as its own field.

## Context Summary

Use the summary for a short description of the parameter in plain language.
This is the first thing the runtime can safely show or summarize.

Used by:

- masked parameter summaries;
- audits and inspector panels;
- parameter matching;
- compact prompt context.

Good examples:

```text
Canonical DICOM/RT oncology database. Data is organized around Patient, Study, Series, Instance, and RT treatment objects.
```

```text
Production GitHub deploy key for pushing release branches from the imaging platform repository.
```

```text
Internal Orthanc API endpoint for public DICOM resource hierarchy lookups.
```

Keep it short. One to three sentences is usually enough.

## Prompt Guidance

Prompt guidance is compact information that can be included in normal agent
prompts when the parameter is relevant. It should help the model do the right
thing without bloating every request.

Used by:

- SQL prompt generation;
- operator planning;
- parameter selection;
- capability-specific prompts;
- execution repair prompts when relevant.

Good prompt guidance includes:

- default interpretation of common user words;
- preferred table, file, service, or workflow choices;
- query-shaping rules;
- reporting conventions;
- safe assumptions.

Avoid:

- secrets;
- huge schema dumps;
- temporary notes;
- instructions to bypass validation;
- contradictory rules.

Canonical SQL example:

```text
Use the DICOM hierarchy Patient -> Study -> Series -> Instance for patient-to-object traversal.
Map "rtplan", "rtplans", "radiation plans", and "treatment plans" to RTPlan unless the user explicitly asks for delivered treatment sessions.
For patient counts with grouped criteria, return one scalar outer count over an inner grouped subquery unless the user asks for per-patient rows.
```

Git key example:

```text
Use this parameter only for GitHub SSH operations in the imaging platform repository. If a command may prompt for a passphrase, prefer terminal-backed execution.
```

API endpoint example:

```text
Use this API for read-only Orthanc resource metadata lookups. Prefer resource IDs returned by the API over guessing DICOM UIDs.
```

## Clarification Guidance

Clarification guidance is richer than prompt guidance. It is used when the
runtime is about to ask the user a question. The typed clarification resolver
can use this field to decide whether to ask or continue with an assumption.

Used by:

- typed clarification resolver;
- SQL clarification handling;
- operator clarification handling;
- parameter selection when multiple candidates exist;
- failure repair when the next step is ambiguous.

Good clarification guidance includes:

- when not to ask;
- when to ask;
- synonyms that should be treated as equivalent;
- evidence fields to inspect first;
- ambiguity thresholds;
- domain-specific distinctions.

Canonical SQL example:

```text
Do not ask for clarification when a user term strongly matches one schema object by casing, pluralization, acronym, or spelling similarity. "rtplans" means RTPlan when RTPlan is present.

For vague anatomy or disease requests such as "brain related issues", first inspect available DICOM/RT semantic fields such as ROI names, structure labels, study descriptions, series descriptions, body part fields, diagnosis/site fields if present, and treatment/anatomic site fields if present.

Ask the user only if no plausible evidence fields exist or if diagnosis-based disease cohort versus treatment-site/ROI-based cohort would materially change the result.
```

Credential example:

```text
If a request says "push with the deploy key" and this is the only matching deploy-key parameter, do not ask which key. If more than one production deploy key matches, ask the user to choose the repository or key.
```

Project path example:

```text
If the user says "the imaging repo" or "workspace repo", select this path. Ask only if another project path has a stronger exact key or alias match.
```

## Concepts JSON

Concepts are structured domain objects the agent should recognize. They are
especially useful when doctors, operators, or domain experts use terms that do
not directly match table names, file names, or API fields.

Concepts must be a JSON array.

Recommended fields:

| Field | Meaning |
| --- | --- |
| `concept_id` | Stable machine-friendly ID. |
| `display_name` | Human-readable name. |
| `one_line_definition` | Short definition. |
| `user_phrases` | Phrases users may type. |
| `synonyms` | Equivalent terms, acronyms, casing variants. |
| `preferred_tables` | SQL tables to consider first. |
| `preferred_columns` | SQL columns to consider first. |
| `preferred_column_patterns` | Column-name patterns to search for evidence. |
| `preferred_files` | Files or folders to consider first for non-SQL tasks. |
| `evidence_hints` | What would count as evidence for this concept. |

Canonical concepts example:

```json
[
  {
    "concept_id": "rt_plan",
    "display_name": "RT Plan",
    "one_line_definition": "Radiotherapy treatment planning object represented by RTPlan.",
    "user_phrases": ["rtplan", "rtplans", "RT plans", "radiation plans", "treatment plans", "plans"],
    "synonyms": ["RTPLAN", "RTPlan", "plan"],
    "preferred_tables": ["RTPlan"],
    "preferred_columns": ["RTPlan.SOPInstanceUID"]
  },
  {
    "concept_id": "brain_related",
    "display_name": "Brain Related Cohort",
    "one_line_definition": "Patients with brain, CNS, cranial, intracranial, or head-related imaging, structures, treatment sites, or diagnoses when such fields exist.",
    "user_phrases": ["brain related", "brain issues", "brain patients", "CNS", "head cases", "cranial", "intracranial"],
    "synonyms": ["brain", "CNS", "central nervous system", "cranial", "cranium", "head", "intracranial", "cerebral", "cerebellum", "brainstem"],
    "preferred_tables": ["StructureSetROISequence", "RTROIObservationsSequence", "Study", "Series", "Patient"],
    "preferred_column_patterns": ["ROIName", "StructureSetLabel", "ROIObservationLabel", "StudyDescription", "SeriesDescription", "BodyPartExamined", "Diagnosis", "Disease", "Site", "Anatomic"]
  }
]
```

API concepts example:

```json
[
  {
    "concept_id": "orthanc_resource",
    "display_name": "Orthanc Resource",
    "one_line_definition": "A DICOM resource in Orthanc, usually a patient, study, series, or instance.",
    "user_phrases": ["orthanc resource", "resource id", "dicom resource"],
    "synonyms": ["resource", "internalid", "orthanc id"],
    "evidence_hints": ["resources.internalid", "resources.parentid", "DICOM hierarchy"]
  }
]
```

## Metrics JSON

Metrics define reusable counts, thresholds, formulas, and reporting rules.
They are useful when users ask for business, clinical, or operational metrics
by name.

Metrics must be a JSON array.

Recommended fields:

| Field | Meaning |
| --- | --- |
| `metric_id` | Stable machine-friendly ID. |
| `display_name` | Human-readable metric name. |
| `one_line_definition` | What the metric means. |
| `user_phrases` | Phrases users may type. |
| `formula` | Plain-language or structured formula. |
| `default_threshold` | Optional default threshold. |
| `preferred_aggregation` | Count, sum, average, scalar count, grouped rows, etc. |
| `evidence_hints` | Tables, columns, files, APIs, or fields that support the metric. |
| `clarification_rule` | When to ask before computing. |

Canonical metrics example:

```json
[
  {
    "metric_id": "patients_with_more_than_n_rtplans",
    "display_name": "Patients With More Than N RT Plans",
    "one_line_definition": "Scalar count of patients whose grouped RTPlan count exceeds a user-provided threshold.",
    "user_phrases": ["patients with more than 10 rtplans", "patients over 10 rt plans", "more than N plans"],
    "formula": "Group by Patient.PatientID, count RTPlan.SOPInstanceUID, filter count > N, then outer count the grouped patients.",
    "preferred_aggregation": "scalar_outer_count",
    "evidence_hints": ["Patient.PatientID", "RTPlan.SOPInstanceUID", "DICOM Patient -> Study -> Series hierarchy"]
  },
  {
    "metric_id": "dose_over_gy_threshold",
    "display_name": "Dose Over Gy Threshold",
    "one_line_definition": "Patients or objects associated with radiation dose above a user-provided Gy threshold.",
    "user_phrases": ["over 30gy", "more than 30 Gy", "dose greater than", "received dose"],
    "default_threshold": "user_provided",
    "preferred_aggregation": "scalar_count_unless_rows_requested",
    "evidence_hints": ["RTDOSE", "DoseReferenceSequence", "FractionGroupSequence"]
  }
]
```

Service metric example:

```json
[
  {
    "metric_id": "failed_deployments_last_24h",
    "display_name": "Failed Deployments Last 24 Hours",
    "one_line_definition": "Count deployments with failed status in the last 24 hours.",
    "user_phrases": ["failed deploys", "failed deployments today", "deploy failures"],
    "formula": "Filter deployment events where status is failed and timestamp >= now - 24 hours, then count.",
    "preferred_aggregation": "count",
    "evidence_hints": ["deployments.status", "deployments.created_at", "CI provider API"]
  }
]
```

## Relationships JSON

Relationships describe how concepts connect. They can be semantic, workflow,
data-model, or operational relationships.

Relationships must be a JSON array.

Recommended fields:

| Field | Meaning |
| --- | --- |
| `relationship_id` | Stable machine-friendly ID. |
| `display_name` | Human-readable label. |
| `source` | Source concept/table/file/service. |
| `target` | Target concept/table/file/service. |
| `relationship_type` | Hierarchy, semantic join, dependency, ownership, workflow, maps_to, etc. |
| `definition` | Plain-language explanation. |
| `evidence` | Fields, API routes, docs, or domain notes supporting the relationship. |
| `validation_note` | Any validator boundary the agent must respect. |

Canonical DICOM relationships example:

```json
[
  {
    "relationship_id": "dicom_patient_study",
    "display_name": "Patient To Study",
    "source": "Patient.PatientID",
    "target": "Study.PatientID",
    "relationship_type": "dicom_hierarchy",
    "definition": "Studies belong to patients through PatientID.",
    "evidence": ["Patient.PatientID -> Study.PatientID"],
    "validation_note": "Semantic guidance only unless discoverdb reports a DB-declared FK."
  },
  {
    "relationship_id": "rtplan_beamsequence",
    "display_name": "RTPlan To BeamSequence",
    "source": "RTPlan.SOPInstanceUID",
    "target": "BeamSequence.SOPInstanceUID",
    "relationship_type": "dicom_rt_object_child",
    "definition": "BeamSequence rows describe beams associated with an RTPlan SOP instance.",
    "evidence": ["RTPlan.SOPInstanceUID -> BeamSequence.SOPInstanceUID"],
    "validation_note": "Allowed for read-only SQL with join warning when not DB-declared."
  }
]
```

Project relationship example:

```json
[
  {
    "relationship_id": "repo_owns_deploy_key",
    "display_name": "Repository Owns Deploy Key",
    "source": "imaging-platform repository",
    "target": "github_deploy_key",
    "relationship_type": "credential_scope",
    "definition": "This deploy key should only be used for Git operations in the imaging platform repository.",
    "validation_note": "Does not bypass command approval or terminal passphrase routing."
  }
]
```

## Advanced Context JSON

The Advanced context editor shows the full raw `context_json` object. The
structured fields sync into:

```json
{
  "user_provided_domain_context": {
    "summary": "...",
    "prompt_guidance": "...",
    "clarification_guidance": "...",
    "concepts": [],
    "metrics": [],
    "relationships": []
  }
}
```

Use Advanced context when:

- you need to inspect the exact saved shape;
- you want to add future non-secret metadata that does not yet have a separate
  field;
- you need to paste or repair a full context object;
- you need to confirm structured fields synchronized correctly.

Do not put secrets in Advanced context.

## How The Agent Uses Metadata

### Normal Prompting

Normal prompts receive bounded context:

- summary;
- prompt guidance;
- compact concept names and definitions;
- compact metric names and definitions;
- compact relationship names.

This keeps the context window under control while still giving the model the
most important domain rules.

### Clarification Resolution

When the agent is about to ask a clarification, the typed resolver can receive
richer matched context:

- clarification guidance;
- matching concepts;
- matching metrics;
- matching relationships;
- available schema objects or options;
- risk flags;
- the proposed user-facing question.

The resolver returns either:

- `continue_with_assumption`, with selected entities, assumptions, and
  execution directives; or
- `ask_user`, with a user-facing question.

### SQL Queries

For SQL parameters, generated schema metadata stays in `value_json` and domain
metadata stays in `context_json`.

SQL prompts receive:

- bounded schema summary from `value_json.schema_catalog`;
- relation metadata from `value_json.relation_foreign_scheme`;
- domain guidance from `context_json`;
- selected assumptions from clarification resolution.

SQL validation still rejects:

- unknown tables;
- unknown columns;
- parse failures;
- multiple statements;
- unsafe SQL;
- mutation without confirmation;
- invalid limits.

Read-only joins that are semantic, inferred, or context-supported can execute
with warnings. DB-declared foreign keys are the only silently approved joins.

## DiscoverDB And Context

`/discoverdb` refreshes generated database metadata. It writes or replaces
these fields in `value_json`:

- `schema_catalog`;
- `relation_foreign_scheme`;
- `schema_discovery`.

It preserves `context_json`. That means users can safely add domain notes once
and refresh the database schema later.

Run `/discoverdb` again when:

- new tables are added;
- columns are renamed;
- foreign keys or indexes change;
- table ownership changes;
- schema discovery failed previously;
- SQL generation seems stale.

## Complete Canonical Example

Use this as a starting point for the `canonical_v1` context fields.

### Summary

```text
Canonical DICOM/RT oncology database. Data is organized around the DICOM hierarchy Patient -> Study -> Series -> Instance, with RTPlan, RTDose, RTSTRUCT, RTRecord, beams, fractions, contours, ROIs, and treatment sessions stored as related tables.
```

### Prompt Guidance

```text
Use canonical DICOM hierarchy when linking patients to imaging or RT objects:
Patient.PatientID -> Study.PatientID
Study.StudyInstanceUID -> Series.StudyInstanceUID
Series.SeriesInstanceUID -> Instance.SeriesInstanceUID

For RT plans, map user terms such as "rtplan", "rtplans", "plans", or "radiation plans" to RTPlan unless the user explicitly asks for treatment delivery records or sessions.

For patient counts with grouped conditions, return one scalar count using an inner grouped subquery unless the user asks for per-patient rows.
```

### Clarification Guidance

```text
Avoid asking clarification for obvious DICOM naming variants, casing differences, plurals, or near-exact schema matches. "rtplans" means RTPlan when RTPlan is present.

For vague clinical concepts like "brain related issues", first try available DICOM/RT semantic fields such as ROI names, structure labels, body part fields, study/series descriptions, diagnosis/site fields if present, and treatment/anatomic site fields if present.

Ask only when no plausible schema fields exist for the concept, or when two materially different interpretations would produce different results, such as diagnosis-based disease cohort versus treatment-site/ROI-based cohort.
```

### Concepts JSON

```json
[
  {
    "concept_id": "dicom_patient",
    "display_name": "Patient",
    "one_line_definition": "A unique patient in the canonical DICOM hierarchy.",
    "user_phrases": ["patient", "patients", "case", "cases"],
    "synonyms": ["PatientID"],
    "preferred_tables": ["Patient"],
    "preferred_columns": ["Patient.PatientID"]
  },
  {
    "concept_id": "rt_plan",
    "display_name": "RT Plan",
    "one_line_definition": "Radiotherapy treatment planning object represented by RTPlan.",
    "user_phrases": ["rtplan", "rtplans", "RT plans", "radiation plans", "treatment plans", "plans"],
    "synonyms": ["RTPLAN", "RTPlan", "plan"],
    "preferred_tables": ["RTPlan"],
    "preferred_columns": ["RTPlan.SOPInstanceUID"]
  },
  {
    "concept_id": "brain_related",
    "display_name": "Brain Related Cohort",
    "one_line_definition": "Patients with brain, CNS, cranial, intracranial, or head-related imaging, structures, treatment sites, or diagnoses when such fields exist.",
    "user_phrases": ["brain related", "brain issues", "brain patients", "CNS", "head cases", "cranial", "intracranial"],
    "synonyms": ["brain", "CNS", "central nervous system", "cranial", "head", "intracranial", "cerebral", "brainstem"],
    "preferred_tables": ["StructureSetROISequence", "RTROIObservationsSequence", "Study", "Series", "Patient"],
    "preferred_column_patterns": ["ROIName", "StructureSetLabel", "ROIObservationLabel", "StudyDescription", "SeriesDescription", "BodyPartExamined", "Diagnosis", "Disease", "Site", "Anatomic"]
  }
]
```

### Metrics JSON

```json
[
  {
    "metric_id": "patients_with_more_than_n_rtplans",
    "display_name": "Patients With More Than N RT Plans",
    "one_line_definition": "Scalar count of patients whose grouped RTPlan count exceeds a user-provided threshold.",
    "user_phrases": ["patients with more than 10 rtplans", "patients over 10 rt plans", "more than N plans"],
    "formula": "Group by Patient.PatientID, count RTPlan.SOPInstanceUID, filter count > N, then outer count the grouped patients.",
    "preferred_aggregation": "scalar_outer_count"
  }
]
```

### Relationships JSON

```json
[
  {
    "relationship_id": "dicom_patient_study_series_instance",
    "display_name": "DICOM Patient Study Series Instance Hierarchy",
    "source": "Patient",
    "target": "Instance",
    "relationship_type": "hierarchy",
    "definition": "Patient links to Study by PatientID, Study links to Series by StudyInstanceUID, and Series links to Instance by SeriesInstanceUID.",
    "evidence": [
      "Patient.PatientID -> Study.PatientID",
      "Study.StudyInstanceUID -> Series.StudyInstanceUID",
      "Series.SeriesInstanceUID -> Instance.SeriesInstanceUID"
    ],
    "validation_note": "Semantic guidance only unless discoverdb reports DB-declared foreign keys."
  }
]
```

## Copyable Empty Template

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

## Checklist Before Saving

- `value_json` is valid JSON object.
- `context_json` is valid JSON object.
- Concepts, metrics, and relationships are valid JSON arrays.
- Secrets are only in `value_json`, not `context_json`.
- Summary is short and human-readable.
- Prompt guidance is compact enough for normal prompts.
- Clarification guidance says when to ask and when not to ask.
- Concepts include user phrases and synonyms.
- Metrics include aggregation rules when counts or thresholds matter.
- Relationships include validation notes when they are not DB-declared FKs.
- Sensitive is enabled when `value_json` contains secrets.

## Troubleshooting

### The Agent Still Asks Too Many Clarifications

Add more `clarification_guidance`, especially:

- known synonyms;
- "do not ask if..." rules;
- table/file/entity preference rules;
- fields to inspect before asking;
- examples of user phrases.

For SQL, add concepts for vague user language such as "brain related",
"rtplans", "dose over 30Gy", or "treatment sessions".

### The Agent Picks The Wrong Parameter

Improve:

- key;
- aliases;
- tags;
- summary;
- concept user phrases.

If two parameters are genuinely similar, use clarification guidance to explain
when each should be selected.

### The SQL Query Uses A Join Warning

This is expected when the join is read-only and supported by context or
inference but not declared as a database foreign key. If the join should be
silently approved, the database schema itself needs a declared FK and
`/discoverdb` must be rerun.

### The Agent Cannot Find A Clinical Concept

Add a concept with:

- clinical phrase;
- synonyms;
- likely tables;
- likely column patterns;
- evidence hints.

For example, "brain related issues" needs anatomy, ROI, diagnosis, treatment
site, and CNS synonyms so the agent has something concrete to inspect.

