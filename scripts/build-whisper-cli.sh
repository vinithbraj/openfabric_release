#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TOOLS_ROOT="${AOR_AUDIO_TOOLS_ROOT:-${REPO_ROOT}/.aor/audio-transcriber}"
OUTPUT_PATH="${1:-${TOOLS_ROOT}/bin/whisper-cli}"
WHISPER_CPP_DIR="${2:-${TOOLS_ROOT}/whisper.cpp}"
WHISPER_CPP_BUILD_DIR="${AOR_WHISPER_CPP_BUILD_DIR:-${WHISPER_CPP_DIR}/build}"
WHISPER_CPP_REPO_URL="${AOR_WHISPER_CPP_REPO_URL:-https://github.com/ggml-org/whisper.cpp.git}"
BUILD_JOBS="${AOR_WHISPER_CPP_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"

mkdir -p "$(dirname "${OUTPUT_PATH}")" "$(dirname "${WHISPER_CPP_DIR}")"

if [[ ! -f "${WHISPER_CPP_DIR}/CMakeLists.txt" ]]; then
  if [[ -d "${WHISPER_CPP_DIR}" ]] && [[ -n "$(find "${WHISPER_CPP_DIR}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "whisper.cpp source directory exists but does not look buildable:" >&2
    echo "  ${WHISPER_CPP_DIR}" >&2
    echo "Remove it or set AOR_AUDIO_TOOLS_ROOT/AOR_WHISPER_CPP_BUILD_DIR to a clean location." >&2
    exit 1
  fi
  git clone --depth 1 "${WHISPER_CPP_REPO_URL}" "${WHISPER_CPP_DIR}"
fi

cmake -S "${WHISPER_CPP_DIR}" -B "${WHISPER_CPP_BUILD_DIR}" \
  -DWHISPER_BUILD_TESTS=OFF \
  -DWHISPER_BUILD_EXAMPLES=ON
cmake --build "${WHISPER_CPP_BUILD_DIR}" --config Release -j"${BUILD_JOBS}"

BUILT_WHISPER_CLI="$(find "${WHISPER_CPP_BUILD_DIR}" -type f -name whisper-cli | head -n 1)"
if [[ -z "${BUILT_WHISPER_CLI}" ]]; then
  echo "whisper.cpp built, but whisper-cli was not found." >&2
  exit 1
fi

install -m 0755 "${BUILT_WHISPER_CLI}" "${OUTPUT_PATH}"
printf '%s\n' "${OUTPUT_PATH}"
