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
  echo "Run: ./install.sh" >&2
  exit 1
fi

source .venv/bin/activate

export AOR_WEBSITE_HOST="${HOST_OVERRIDE:-${AOR_WEBSITE_HOST:-127.0.0.1}}"
export AOR_WEBSITE_PORT="${PORT_OVERRIDE:-${AOR_WEBSITE_PORT:-8014}}"
export AOR_WEBSITE_RELOAD="${RELOAD_OVERRIDE:-${AOR_WEBSITE_RELOAD:-0}}"

UVICORN_ARGS=(
  --app-dir src
  website.app:create_app
  --factory
  --host "${AOR_WEBSITE_HOST}"
  --port "${AOR_WEBSITE_PORT}"
)

if is_enabled "${AOR_WEBSITE_RELOAD}"; then
  UVICORN_ARGS+=(--reload)
fi

echo "Starting OpenFabric website at http://${AOR_WEBSITE_HOST}:${AOR_WEBSITE_PORT}/website"
exec python -m uvicorn "${UVICORN_ARGS[@]}"
