#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE="${OPENFABRIC_IMAGE:-openfabric-all-services}"
VERSION="${1:-latest}"

if [[ $# -gt 1 || "${VERSION}" == -* ]]; then
  echo "Usage: $0 [version]" >&2
  echo "Example: $0 v1.0.0" >&2
  exit 1
fi

cd "${REPO_ROOT}"

docker build \
  -f docker/server.Dockerfile \
  --build-arg AOR_BUILD_AUDIO=1 \
  -t "${IMAGE}:${VERSION}" \
  .

echo "Built ${IMAGE}:${VERSION}"
