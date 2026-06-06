#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
START_SCRIPT="${SCRIPT_DIR}/start.sh"

cd "${REPO_ROOT}"

exec "${START_SCRIPT}" "$@"
