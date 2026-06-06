# Gateway Agent

`src/gateway_agent/` is the node-local execution service used by the Agent
Runtime for shell commands.

The gateway:

- runs on one host;
- executes concrete validated commands locally on that host;
- only accepts requests whose `node` matches its configured logical node name;
- supports buffered execution, streaming execution, and cancellation;
- supports PTY-backed terminal sessions for interactive Agent UI commands;
- exposes a small local LLM process manager used by the Agent UI Settings
  launch terminal/status controls;
- returns command failures as structured command results instead of HTTP
  failures.


## API

- `GET /healthz`
  - returns `{ "status": "ok", "node": "<configured node>" }`
- `GET /capabilities`
  - returns `{ "node": "<configured node>", "version": "...", "capabilities": [...] }`
- `POST /exec`
  - request: `{ "node": str, "command": str, "execution_id": str | null }`
  - response: `{ "stdout": str, "stderr": str, "exit_code": int }`
- `POST /exec/stream`
  - request: `{ "node": str, "command": str, "execution_id": str | null }`
  - response: `text/event-stream`
- `POST /exec/cancel`
  - request: `{ "node": str, "execution_id": str }`
  - response: `{ "cancelled": bool, "execution_id": str, "message": str }`
- `WebSocket /terminal/ws`
  - opens or attaches to a PTY-backed terminal session
- `POST /terminal/exec/stream`
  - runs one command inside an active terminal session and streams structured
    output
- `POST /terminal/exec/detach`
  - starts a terminal-owned command without runtime output capture
- `POST /terminal/write`
  - mirrors runtime-produced output into an active terminal session
- `POST /terminal/input`
  - writes runtime-provided input bytes into an active terminal PTY
- `GET /llm/status`
  - returns status for the gateway-managed local LLM launch command
- `GET /llm/log`
  - returns recent local LLM launch log output
- `POST /llm/start`
  - starts or restarts the configured local LLM command
- `POST /llm/stop`
  - stops the configured local LLM command

Command failures return `200` with a non-zero `exit_code`. Request validation
problems return `4xx`.


## Streaming Events

`POST /exec/stream` emits server-sent events.

Common event names:

- `stdout`
- `stderr`
- `completed`
- `cancelled`
- `error`

The Agent UI renders these events as collapsible command output capsules.


## Terminal Sessions

The terminal API is used by the Agent UI for commands that need a real PTY:

- SSH passphrases;
- password or credential prompts;
- tools that require a controlling terminal;
- long-running commands the user wants to observe directly.

The runtime marks such commands with `interaction_mode: may_prompt` or
`execution_mode: terminal_detached`. If the command output looks like it is
waiting for input, the Agent UI brings the terminal forward so the user can
type into it.

Terminal sessions are process-local. They can disappear when the gateway
restarts or when idle cleanup closes them.

The Agent UI stores one saved terminal cwd per gateway in
`artifacts/agent_gateways.db`. The terminal Save Workspace button, browser
close/pagehide autosave, and gateway-change autosave update that record so
requests against each gateway resume in the expected directory.


## Cancellation

The gateway tracks active streamed commands by `execution_id`.

When `/exec/cancel` is called, the gateway attempts to terminate the active
process group for that execution id. The runtime stop button uses this path when
a long-running command is active and an execution id is available.


## Install

```bash
cd /path/to/openfabric
./src/gateway_agent/install.sh
```

This creates a dedicated gateway virtual environment at
`src/gateway_agent/.venv`.


## Configuration

- `GATEWAY_NODE_NAME`
  - logical node served by this agent
  - default: `localhost`
- `GATEWAY_BIND_HOST`
  - bind host
  - default: `127.0.0.1`
- `GATEWAY_BIND_PORT`
  - bind port
  - default: `8787`
- `GATEWAY_EXEC_TIMEOUT_SECONDS`
  - per-command timeout; set to `0` to disable task timeouts
  - default: `0`
- `GATEWAY_TERMINAL_IDLE_TIMEOUT_SECONDS`
  - terminal idle cleanup window, when configured by the gateway settings
- `GATEWAY_TRACE_COMMANDS`
  - log each requested command on the gateway server
  - default: `0`
- `GATEWAY_WORKDIR`
  - optional working directory for command execution
- `GATEWAY_LLM_LOG_DIR`
  - log directory for the gateway-managed local LLM process
  - default: `/tmp/openfabric-llm`


## Run

```bash
export GATEWAY_NODE_NAME=localhost
export GATEWAY_BIND_HOST=127.0.0.1
export GATEWAY_BIND_PORT=8787

python -m uvicorn --app-dir src gateway_agent.app:app --host "${GATEWAY_BIND_HOST}" --port "${GATEWAY_BIND_PORT}"
```

Or use the helper script:

```bash
./src/gateway_agent/startup.sh
```

The helper accepts host and port overrides:

```bash
./src/gateway_agent/startup.sh --host 0.0.0.0 --port 8787
```

This is useful when the Agent UI/runtime is on a different machine from the
gateway. Because the gateway has no built-in authentication, bind to
`0.0.0.0` only on a trusted network or behind a trusted proxy/firewall.

To print each requested command on the gateway server side:

```bash
./src/gateway_agent/startup.sh --trace-commands
```

or:

```bash
export GATEWAY_TRACE_COMMANDS=1
./src/gateway_agent/startup.sh
```


## Runtime Wiring

The runtime can discover the gateway through config or environment variables.
A typical node entry points at the gateway `/exec` endpoint:

```yaml
nodes:
  default: localhost
  endpoints:
    - name: localhost
      url: http://127.0.0.1:8787/exec
```

The runtime can derive the matching streaming and cancellation endpoints from
the gateway base URL when shell streaming is enabled.


## Security Notes

This service does not include authentication. Keep it bound to loopback or
behind a trusted proxy/network boundary.

The gateway is not a prompt interpreter. It should receive only concrete
commands that have already passed runtime validation and approval policy.
