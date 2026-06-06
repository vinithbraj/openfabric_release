#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${AOR_APP_ROOT:-/opt/openfabric}"
DATA_ROOT="${AOR_DATA_ROOT:-/data}"
ARTIFACTS_DIR="${AOR_ARTIFACTS_DIR:-${DATA_ROOT}/artifacts}"
OUTPUTS_DIR="${AOR_OUTPUTS_DIR:-${DATA_ROOT}/outputs}"
PROFILE_DIR="${AOR_ONLINE_AI_CHECK_PROFILE_DIR:-${DATA_ROOT}/playwright-duckai-profile}"
WHISPER_MODELS_DIR="${AOR_WHISPER_MODELS_DIR:-${DATA_ROOT}/models/whisper}"
WHISPER_MODEL_URL="${AOR_AUDIO_TRANSCRIBER_MODEL_URL:-https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin}"
DOWNLOAD_WHISPER_MODEL="${AOR_DOWNLOAD_WHISPER_MODEL:-1}"

mkdir -p "${ARTIFACTS_DIR}" "${OUTPUTS_DIR}" "${PROFILE_DIR}" "${WHISPER_MODELS_DIR}"

copy_seed_db() {
  local name="$1"
  local source="${APP_ROOT}/artifacts/${name}"
  local target="${ARTIFACTS_DIR}/${name}"

  if [[ -f "${source}" && ! -e "${target}" ]]; then
    cp "${source}" "${target}"
  fi
}

copy_seed_db "agent_memory.db"
copy_seed_db "prompts.db"

is_disabled() {
  case "${1:-}" in
    0|false|FALSE|False|no|NO|No|off|OFF|Off) return 0 ;;
    *) return 1 ;;
  esac
}

is_enabled() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

download_whisper_model_if_missing() {
  local model_path="${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-${WHISPER_MODELS_DIR}/ggml-large-v3.bin}"
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
  echo "Downloading default whisper.cpp model to ${model_path}"
  rm -f "${tmp_model}"
  curl -L --fail --retry 3 --retry-delay 2 "${WHISPER_MODEL_URL}" -o "${tmp_model}"
  mv "${tmp_model}" "${model_path}"
  rm -rf "${lock_dir}"
  trap - EXIT
}

ensure_self_signed_ssl_cert() {
  local certfile="${AOR_SSL_CERTFILE}"
  local keyfile="${AOR_SSL_KEYFILE}"
  local days="${AOR_SSL_CERT_DAYS:-3650}"

  if [[ -f "${certfile}" && -f "${keyfile}" ]]; then
    return 0
  fi
  if ! command -v openssl >/dev/null 2>&1; then
    echo "HTTPS is enabled, but openssl is not installed in the container." >&2
    exit 1
  fi

  if [[ -f "${certfile}" || -f "${keyfile}" ]]; then
    echo "HTTPS certificate/key pair is incomplete; generating a fresh matching self-signed pair."
  fi

  mkdir -p "$(dirname "${certfile}")" "$(dirname "${keyfile}")"
  echo "Generating fallback self-signed HTTPS certificate:"
  echo "  cert: ${certfile}"
  echo "  key:  ${keyfile}"
  openssl req \
    -x509 \
    -nodes \
    -newkey rsa:2048 \
    -days "${days}" \
    -keyout "${keyfile}" \
    -out "${certfile}" \
    -subj "/CN=OpenFabric Agent Dev" \
    -addext "subjectAltName=DNS:localhost,DNS:openfabric-agent,IP:127.0.0.1" >/dev/null 2>&1
  chmod 600 "${keyfile}" 2>/dev/null || true
  chmod 644 "${certfile}" 2>/dev/null || true
}

export AOR_HOST="${AOR_HOST:-0.0.0.0}"
export AOR_PORT="${AOR_PORT:-8011}"
export AOR_SSL_ENABLED="${AOR_SSL_ENABLED:-0}"
export AOR_SSL_CERTFILE="${AOR_SSL_CERTFILE:-${ARTIFACTS_DIR}/ssl/agent-ui.crt}"
export AOR_SSL_KEYFILE="${AOR_SSL_KEYFILE:-${ARTIFACTS_DIR}/ssl/agent-ui.key}"
export AOR_AGENT_MEMORY_DB_PATH="${AOR_AGENT_MEMORY_DB_PATH:-${ARTIFACTS_DIR}/agent_memory.db}"
export AOR_AGENT_PROMPTS_DB_PATH="${AOR_AGENT_PROMPTS_DB_PATH:-${ARTIFACTS_DIR}/prompts.db}"
export AOR_AGENT_GATEWAYS_DB_PATH="${AOR_AGENT_GATEWAYS_DB_PATH:-${ARTIFACTS_DIR}/agent_gateways.db}"
export AOR_AGENT_PLAN_CACHE_DB_PATH="${AOR_AGENT_PLAN_CACHE_DB_PATH:-${ARTIFACTS_DIR}/agent_plan_cache.db}"
export AOR_AGENT_COMPUTATION_CACHE_DB_PATH="${AOR_AGENT_COMPUTATION_CACHE_DB_PATH:-${ARTIFACTS_DIR}/agent_computation_cache.db}"
export AOR_AGENT_EVENTS_DB_PATH="${AOR_AGENT_EVENTS_DB_PATH:-${ARTIFACTS_DIR}/agent_events.db}"
export AOR_AGENT_COMMAND_ALLOWLIST_DB_PATH="${AOR_AGENT_COMMAND_ALLOWLIST_DB_PATH:-${ARTIFACTS_DIR}/agent_command_allowlist.db}"
export AOR_AGENT_CHATS_DB_PATH="${AOR_AGENT_CHATS_DB_PATH:-${ARTIFACTS_DIR}/chats.db}"
export AOR_AGENT_UI_SETTINGS_DB_PATH="${AOR_AGENT_UI_SETTINGS_DB_PATH:-${ARTIFACTS_DIR}/agent_ui_settings.db}"
export AOR_AUTO_ARTIFACT_DIR="${AOR_AUTO_ARTIFACT_DIR:-${OUTPUTS_DIR}}"
export AOR_ONLINE_AI_CHECK_PROFILE_DIR="${PROFILE_DIR}"
export AOR_ONLINE_AI_CHECK_HEADLESS="${AOR_ONLINE_AI_CHECK_HEADLESS:-1}"
export AOR_AUDIO_TRANSCRIBER_ENABLED="${AOR_AUDIO_TRANSCRIBER_ENABLED:-1}"
export AOR_AUDIO_TRANSCRIBER_ENGINE="${AOR_AUDIO_TRANSCRIBER_ENGINE:-whisper_cpp}"
export AOR_AUDIO_TRANSCRIBER_MODEL_PATH="${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-${WHISPER_MODELS_DIR}/ggml-large-v3.bin}"
export AOR_AUDIO_TRANSCRIBER_BINARY="${AOR_AUDIO_TRANSCRIBER_BINARY:-whisper-cli}"
export AOR_AUDIO_TRANSCRIBER_MAX_SECONDS="${AOR_AUDIO_TRANSCRIBER_MAX_SECONDS:-120}"
export AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB="${AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB:-25}"
export AOR_AUDIO_TRANSCRIBER_LANGUAGE="${AOR_AUDIO_TRANSCRIBER_LANGUAGE:-auto}"
export AOR_AUDIO_TRANSCRIBER_SERVICE_URL="${AOR_AUDIO_TRANSCRIBER_SERVICE_URL:-http://localhost:8012}"
export AOR_DOWNLOAD_WHISPER_MODEL="${DOWNLOAD_WHISPER_MODEL}"

download_whisper_model_if_missing

export AOR_AVAILABLE_NODES="${AOR_AVAILABLE_NODES:-localhost}"
export AOR_DEFAULT_NODE="${AOR_DEFAULT_NODE:-localhost}"
export AOR_GATEWAY_URL="${AOR_GATEWAY_URL:-http://127.0.0.1:8787}"
export AOR_GATEWAY_TIMEOUT_SECONDS="${AOR_GATEWAY_TIMEOUT_SECONDS:-30}"
export AOR_LLM_PREFLIGHT_ENABLED="${AOR_LLM_PREFLIGHT_ENABLED:-0}"

cd "${DATA_ROOT}"

UVICORN_ARGS=(
  --app-dir "${APP_ROOT}/src" \
  agent_runtime.api.app:create_app \
  --factory \
  --host "${AOR_HOST}" \
  --port "${AOR_PORT}"
)

if is_enabled "${AOR_SSL_ENABLED}"; then
  ensure_self_signed_ssl_cert
  UVICORN_ARGS+=(--ssl-certfile "${AOR_SSL_CERTFILE}" --ssl-keyfile "${AOR_SSL_KEYFILE}")
  echo "HTTPS enabled. Open https://localhost:${AOR_PORT}/agent-ui or https://<host-ip>:${AOR_PORT}/agent-ui and accept the browser warning."
fi

exec python -m uvicorn "${UVICORN_ARGS[@]}"
