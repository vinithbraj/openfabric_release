#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"

HOST_OVERRIDE=""
PORT_OVERRIDE=""
RELOAD_OVERRIDE=""

is_enabled() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

usage() {
  echo "Usage: $0 [--host HOST] [--port PORT] [--reload]" >&2
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
    --reload)
      RELOAD_OVERRIDE="1"
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

cd "${REPO_ROOT}"

if [[ ! -d ".venv" ]]; then
  echo "Missing .venv in ${REPO_ROOT}." >&2
  echo "Run: ./setup-manual.sh" >&2
  exit 1
fi

source .venv/bin/activate

export AOR_MANUAL_HOST="${HOST_OVERRIDE:-${AOR_MANUAL_HOST:-127.0.0.1}}"
export AOR_MANUAL_PORT="${PORT_OVERRIDE:-${AOR_MANUAL_PORT:-8013}}"
export AOR_MANUAL_RELOAD="${RELOAD_OVERRIDE:-${AOR_MANUAL_RELOAD:-0}}"

UVICORN_ARGS=(
  --app-dir src
  manual.app:create_app
  --factory
  --host "${AOR_MANUAL_HOST}"
  --port "${AOR_MANUAL_PORT}"
)

if is_enabled "${AOR_MANUAL_RELOAD}"; then
  UVICORN_ARGS+=(--reload)
fi

echo "Starting OpenFabric manual at http://${AOR_MANUAL_HOST}:${AOR_MANUAL_PORT}/manual"
exec python -m uvicorn "${UVICORN_ARGS[@]}"

