---
id: architecture-maintenance
title: Maintenance Guide
kind: architecture
tags: [architecture, maintenance, docs]
summary: How to keep authored docs, legacy docs, generated references, tests, and launch scripts healthy.
order: 8
---

# Maintenance Guide

Keep the manual useful by preserving three layers: readable authored docs, copied legacy source material, and generated references that make drift visible.

## Updating Authored Pages

Add Markdown files under `src/manual/content`. Include frontmatter with `id`, `title`, `kind`, `tags`, `summary`, and `order`.

Use slug-only document IDs:

```text
lowercase-letters-numbers-and-dashes
```

## Updating Legacy Docs

Legacy docs are copied from root `docs/` into `src/manual/content/legacy`. The loader infers metadata when no frontmatter exists.

## Checking Generated Pages

Generated references inspect source files under:

- `src/agent_runtime`
- `src/audio_runtime`
- `src/gateway_agent`
- `src/gateway_core`
- `src/llm`

Caches, artifacts, virtual environments, vendored files, and binaries are skipped.

Curated references such as System Surfaces And API Reference are authored
manually and should be updated whenever routes, standalone pages, settings
preferences, or UI drawer behavior changes. Generated references make source
drift visible, but they do not replace the curated reader-facing API map.

## Test Commands

```bash
python -m pytest tests/test_manual_service.py
bash -n setup-manual.sh startmanual.sh
```

## Release Checklist

- Manual opens at `/manual`.
- Health returns `mode: manual`.
- Search works with and without FTS5.
- Legacy docs appear in the catalog.
- The current browser surfaces and API groups are represented in System
  Surfaces And API Reference.
- Root docs and served manual pages agree on settings persistence, standalone
  pages, drawers, and immersive-mode behavior.
- Generated route and environment pages reflect source changes.
- Mermaid diagrams render locally.
