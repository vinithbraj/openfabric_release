#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yaml"
IMAGE="${OPENFABRIC_IMAGE:-openfabric-all-services}"
VERSION="${OPENFABRIC_VERSION:-latest}"

if [[ $# -gt 0 && "${1}" != -* ]]; then
  VERSION="$1"
  shift
fi

cd "${REPO_ROOT}"

OPENFABRIC_IMAGE="${IMAGE}" OPENFABRIC_VERSION="${VERSION}" \
  exec docker compose -f "${COMPOSE_FILE}" up --remove-orphans "$@"
