# macOS Gateway Agent

`src/gateway_macos/` is the macOS wrapper for the OpenFabric gateway agent. It
uses the shared gateway core and advertises `platform=macos` through
`GET /capabilities` so the runtime can ask the LLM for macOS-appropriate shell
commands.

## Scope

macOS v1 supports:

- shell command execution through `/bin/bash -lc`;
- streamed shell execution;
- cancellation by execution id;
- PTY-backed terminal sessions;
- terminal command streaming, detached terminal execution, terminal input, and
  terminal output mirroring;
- macOS platform and command profile discovery.

Native macOS features are accessed through command-line tools in v1. Useful
tools include `osascript`, `shortcuts`, `open`, `pbcopy`, `pbpaste`,
`screencapture`, `mdfind`, `system_profiler`, `networksetup`, `pmset`,
`launchctl`, and `defaults`.

## Install

```bash
cd /path/to/openfabric
./src/gateway_macos/install.sh
```

This creates a dedicated virtual environment at `src/gateway_macos/.venv`.

## Run

```bash
./src/gateway_macos/startup.sh
```

The helper accepts host and port overrides:

```bash
./src/gateway_macos/startup.sh --host 0.0.0.0 --port 8787
```

Because the gateway executes local commands and has no built-in authentication,
bind to `0.0.0.0` only on a trusted network or behind a trusted proxy/firewall.

## Configuration

- `GATEWAY_NODE_NAME`
  - logical node served by this gateway
  - default: macOS LocalHostName, short hostname, then `macos`
- `GATEWAY_BIND_HOST`
  - bind host
  - default: `127.0.0.1`
- `GATEWAY_BIND_PORT`
  - bind port
  - default: `8787`
- `GATEWAY_EXEC_TIMEOUT_SECONDS`
  - per-command timeout; `0` disables task timeouts
- `GATEWAY_WORKDIR`
  - optional working directory for command execution
- `GATEWAY_TRACE_COMMANDS`
  - log each requested command on the gateway server
- `GATEWAY_SHELL`
  - command shell path
  - default: `/bin/bash`

## macOS Permissions

Some macOS commands require permission grants to the terminal app or launch
service that runs the gateway:

- Accessibility
- Automation
- Screen Recording
- Full Disk Access
- Files and Folders

Grant only the permissions needed for the workflows you intend to run.
