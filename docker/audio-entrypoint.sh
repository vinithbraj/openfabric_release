#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${AOR_DATA_ROOT:-/data}"
WHISPER_MODELS_DIR="${AOR_WHISPER_MODELS_DIR:-${DATA_ROOT}/models/whisper}"
WHISPER_MODEL_URL="${AOR_AUDIO_TRANSCRIBER_MODEL_URL:-https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin}"
WHISPER_MED_MODEL_FALLBACK_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin"
WHISPER_MED_MODEL_URL="${AOR_AUDIO_MEDIUM_TRANSCRIBER_MODEL_URL:-}"
if [[ -z "${WHISPER_MED_MODEL_URL}" ]]; then
  WHISPER_MED_MODEL_URL="${AOR_AUDIO_TRANSCRIBER_MEDIUM_TRANSCRIBER_MODEL_URL:-${WHISPER_MED_MODEL_FALLBACK_URL}}"
fi
WHISPER_BASE_MODEL_FALLBACK_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
WHISPER_BASE_MODEL_URL="${AOR_AUDIO_BASE_TRANSCRIBER_MODEL_URL:-}"
if [[ -z "${WHISPER_BASE_MODEL_URL}" ]]; then
  WHISPER_BASE_MODEL_URL="${AOR_AUDIO_TRANSCRIBER_BASE_TRANSCRIBER_MODEL_URL:-${WHISPER_BASE_MODEL_FALLBACK_URL}}"
fi
MODEL_TIER="${AOR_AUDIO_TRANSCRIBER_MODEL_TIER:-high}"
DOWNLOAD_WHISPER_MODEL="${AOR_DOWNLOAD_WHISPER_MODEL:-1}"

mkdir -p "${WHISPER_MODELS_DIR}"

is_disabled() {
  case "${1:-}" in
    0|false|FALSE|False|no|NO|No|off|OFF|Off) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_model_url() {
  local tier="${1,,}"
  case "${tier}" in
    low)
      echo "${WHISPER_BASE_MODEL_URL}"
      ;;
    med)
      echo "${WHISPER_MED_MODEL_URL}"
      ;;
    high|"")
      echo "${WHISPER_MODEL_URL}"
      ;;
    *)
      echo "Invalid model tier: ${tier}" >&2
      exit 1
      ;;
  esac
}

resolve_model_path() {
  local tier="${1,,}"
  local default_model="${WHISPER_MODELS_DIR}/ggml-large-v3.bin"
  case "${tier}" in
    low)
      echo "${WHISPER_MODELS_DIR}/ggml-base.en.bin"
      ;;
    med)
      echo "${WHISPER_MODELS_DIR}/ggml-medium.en.bin"
      ;;
    high|"")
      echo "${default_model}"
      ;;
    *)
      echo "Invalid model tier: ${tier}" >&2
      exit 1
      ;;
  esac
}

download_whisper_model_if_missing() {
  local model_path="${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-$(resolve_model_path "${MODEL_TIER}")}"
  local model_url="${WHISPER_MODEL_URL}"
  local tier_path="${MODEL_TIER}"
  model_url="$(resolve_model_url "${tier_path}")"
  local tmp_model="${model_path}.tmp"
  local lock_dir="${model_path}.download.lock"

  if [[ -f "${model_path}" ]] || is_disabled "${DOWNLOAD_WHISPER_MODEL}"; then
    return 0
  fi

  if ! mkdir "${lock_dir}" 2>/dev/null; then
    echo "Waiting for another process to finish downloading ${model_path}"
    for _ in $(seq 1 180); do
      [[ -f "${model_path}" ]] && return 0
      sleep 1
    done
    echo "Timed out waiting for Whisper model download: ${model_path}" >&2
    return 1
  fi
  trap 'rm -rf "${lock_dir}"' EXIT

  mkdir -p "$(dirname "${model_path}")"
  echo "Downloading ${tier_path} whisper.cpp model to ${model_path}"
  echo "  ${model_url}"
  rm -f "${tmp_model}"
  curl -L --fail --retry 3 --retry-delay 2 "${model_url}" -o "${tmp_model}"
  mv "${tmp_model}" "${model_path}"
  rm -rf "${lock_dir}"
  trap - EXIT
}

export AOR_AUDIO_TRANSCRIBER_ENABLED="${AOR_AUDIO_TRANSCRIBER_ENABLED:-1}"
export AOR_AUDIO_TRANSCRIBER_ENGINE="${AOR_AUDIO_TRANSCRIBER_ENGINE:-whisper_cpp}"
export AOR_AUDIO_TRANSCRIBER_MODEL_TIER="${MODEL_TIER}"
export AOR_AUDIO_TRANSCRIBER_MODEL_PATH="${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-$(resolve_model_path "${MODEL_TIER}")}"
export AOR_AUDIO_TRANSCRIBER_BINARY="${AOR_AUDIO_TRANSCRIBER_BINARY:-whisper-cli}"
export AOR_AUDIO_TRANSCRIBER_MAX_SECONDS="${AOR_AUDIO_TRANSCRIBER_MAX_SECONDS:-120}"
export AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB="${AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB:-25}"
export AOR_AUDIO_TRANSCRIBER_LANGUAGE="${AOR_AUDIO_TRANSCRIBER_LANGUAGE:-auto}"
export AOR_AUDIO_RUNTIME_HOST="${AOR_AUDIO_RUNTIME_HOST:-127.0.0.1}"
export AOR_AUDIO_RUNTIME_PORT="${AOR_AUDIO_RUNTIME_PORT:-8012}"
export AOR_DOWNLOAD_WHISPER_MODEL="${DOWNLOAD_WHISPER_MODEL}"

download_whisper_model_if_missing

cd "${DATA_ROOT}"

exec python -m uvicorn \
  --app-dir "${APP_ROOT}/src" \
  audio_runtime.api.app:create_app \
  --factory \
  --host "${AOR_AUDIO_RUNTIME_HOST}" \
  --port "${AOR_AUDIO_RUNTIME_PORT}"
