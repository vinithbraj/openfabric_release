---
id: troubleshooting
title: Troubleshooting
kind: manual
tags: [troubleshooting, health, diagnostics]
summary: Common checks for install, service startup, model connectivity, gateway execution, search, and manual rendering.
order: 10
---

# Troubleshooting

Start with health endpoints:

```text
http://127.0.0.1:8011/healthz
http://127.0.0.1:8012/healthz
http://127.0.0.1:8013/healthz
http://127.0.0.1:8787/healthz
```

## Manual Service

If `/manual` loads but search does not return expected results:

- check `/api/manual/catalog`;
- check `/api/manual/search?q=agent`;
- restart with `./startmanual.sh --reload`;
- confirm copied legacy docs exist in `src/manual/content/legacy`.

## Agent UI

If a prompt does not run:

- confirm the model endpoint in Settings;
- check gateway health;
- inspect the trace pane;
- inspect command capsules for stderr or non-zero exit codes;
- check whether an approval or clarification is waiting.

If Agentic mode appears to deliberate without visible action:

- open Settings -> Behavior;
- choose the Speed preset or set `workflow_execution_mode` to streaming;
- confirm trace metadata shows the expected backend-owned profiles;
- switch Quick controls -> Agent behavior -> Answers to Simple when the command
  capsules already contain enough detail and final-response latency matters
  more than prose.

## Gateway

If shell execution fails:

- verify the selected gateway URL;
- check `GATEWAY_WORKDIR`;
- confirm the command profile and shell path;
- use the terminal route for commands that require interactive input.

## Audio

If dictation fails:

- check the audio service health;
- confirm `ffmpeg` and `ffprobe`;
- confirm the model path;
- check upload size and duration limits.

## Search And Generated References

The manual search index is local. If SQLite FTS5 is unavailable, the service falls back to Python scoring. Generated references refresh on service start.
