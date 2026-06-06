#!/usr/bin/env bash
set -euo pipefail

TRACE_COMMANDS="${GATEWAY_TRACE_COMMANDS:-0}"
HOST_OVERRIDE=""
PORT_OVERRIDE=""

usage() {
  echo "Usage: $0 [--host HOST] [--port PORT] [--trace-commands]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --host" >&2
        usage
        exit 1
      fi
      HOST_OVERRIDE="$2"
      shift 2
      ;;
    --port)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --port" >&2
        usage
        exit 1
      fi
      PORT_OVERRIDE="$2"
      shift 2
      ;;
    --trace-commands)
      TRACE_COMMANDS=1
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

export GATEWAY_NODE_NAME="${GATEWAY_NODE_NAME:-localhost}"
export GATEWAY_BIND_HOST="${HOST_OVERRIDE:-${GATEWAY_BIND_HOST:-127.0.0.1}}"
export GATEWAY_BIND_PORT="${PORT_OVERRIDE:-${GATEWAY_BIND_PORT:-8787}}"
export GATEWAY_EXEC_TIMEOUT_SECONDS="${GATEWAY_EXEC_TIMEOUT_SECONDS:-0}"
export GATEWAY_TRACE_COMMANDS="${TRACE_COMMANDS}"

cd "${REPO_ROOT}"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Missing gateway .venv in ${SCRIPT_DIR}." >&2
  echo "Run: ./src/gateway_agent/install.sh" >&2
  exit 1
fi

source "${VENV_DIR}/bin/activate"

exec python -m uvicorn --app-dir src gateway_agent.app:app \
  --host "${GATEWAY_BIND_HOST}" \
  --port "${GATEWAY_BIND_PORT}"
