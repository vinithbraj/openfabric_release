---
id: example-workflows
title: Example Workflows
kind: manual
tags: [examples, workflows, agent-ui]
summary: Practical workflows for learning the runtime through safe, inspectable requests.
order: 11
---

# Example Workflows

These examples are meant to exercise the runtime without requiring risky changes.

## Inspect A Repository

```text
Summarize this repository structure and identify the main FastAPI entry points.
```

Expected behavior:

- source inspection;
- no file mutation;
- route and package summary;
- optional generated table or DisplayDocument.

## Produce A Local Report

```text
Create a short Markdown report about Python files changed most recently and save it as artifacts/source-report.md.
```

Expected behavior:

- approval before writing;
- command or Python action capsule;
- verification that the file exists;
- final response with path.

## Schedule A Reminder

```text
Remind me tomorrow morning to review the gateway logs.
```

Expected behavior:

- event draft;
- timezone handling;
- saved runtime event;
- future execution through the same pipeline.

## Add A Memory

```text
Remember that for this repository I prefer python -m pytest for test runs.
```

Expected behavior:

- memory proposal;
- user review;
- reusable future planning guidance.

## Debug A Gateway

```text
Check whether the local gateway is healthy and explain any failure without changing files.
```

Expected behavior:

- health probe;
- no mutation;
- concise diagnosis;
- gateway metadata in trace or capsule.

