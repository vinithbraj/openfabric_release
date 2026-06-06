#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"

CONFIG_OVERRIDE=""
HOST_OVERRIDE=""
PORT_OVERRIDE=""
AUDIO_PORT_OVERRIDE=""
MANUAL_PORT_OVERRIDE=""
WEBSITE_PORT_OVERRIDE=""
RELOAD_OVERRIDE=""
HTTPS_OVERRIDE=""
AOR_HOST_WAS_SET="${AOR_HOST+x}"
AOR_AUDIO_TRANSCRIBER_ENABLED_WAS_SET="${AOR_AUDIO_TRANSCRIBER_ENABLED+x}"

usage() {
  echo "Usage: $0 [--config PATH] [--host HOST] [--port AGENT_PORT] [--audio-port AUDIO_PORT] [--manual-port MANUAL_PORT] [--website-port WEBSITE_PORT] [--reload] [--https]" >&2
}

is_enabled() {
  case "${1:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_audio_model_path() {
  local tier="${1,,}"
  local model_dir="${REPO_ROOT}/artifacts/models/whisper"
  local low_path="${AOR_AUDIO_TRANSCRIBER_BASE_MODEL_PATH:-${model_dir}/ggml-base.en.bin}"
  local med_path="${AOR_AUDIO_TRANSCRIBER_MEDIUM_MODEL_PATH:-${model_dir}/ggml-medium.en.bin}"
  local high_path="${AOR_AUDIO_TRANSCRIBER_HIGH_MODEL_PATH:-${model_dir}/ggml-large-v3.bin}"

  case "${tier}" in
    low)
      echo "${low_path}"
      ;;
    med)
      echo "${med_path}"
      ;;
    high)
      echo "${high_path}"
      ;;
    *)
      echo "Invalid AOR_AUDIO_TRANSCRIBER_MODEL_TIER: ${tier}" >&2
      echo "Expected one of: low, med, high." >&2
      exit 1
      ;;
  esac
}

resolve_audio_vendor_model_path() {
  local tier="${1,,}"
  local vendor_dir="${REPO_ROOT}/vendor/audio-transcriber/models"

  case "${tier}" in
    low)
      echo "${AOR_AUDIO_TRANSCRIBER_VENDOR_BASE_MODEL_PATH:-${vendor_dir}/ggml-base.en.bin}"
      ;;
    med)
      echo "${AOR_AUDIO_TRANSCRIBER_VENDOR_MEDIUM_MODEL_PATH:-${vendor_dir}/ggml-medium.en.bin}"
      ;;
    high)
      echo "${AOR_AUDIO_TRANSCRIBER_VENDOR_LARGE_MODEL_PATH:-${vendor_dir}/ggml-large-v3.bin}"
      ;;
    *)
      echo "Invalid AOR_AUDIO_TRANSCRIBER_MODEL_TIER: ${tier}" >&2
      echo "Expected one of: low, med, high." >&2
      exit 1
      ;;
  esac
}

resolve_audio_executable() {
  local candidate="$1"
  local path=""

  if [[ -z "${candidate}" ]]; then
    return 1
  fi
  if [[ "${candidate}" == */* ]]; then
    path="${candidate/#\~/${HOME}}"
    if [[ -x "${path}" ]]; then
      printf '%s\n' "${path}"
      return 0
    fi
    return 1
  fi
  command -v "${candidate}"
}

audio_file_exists() {
  local candidate="${1/#\~/${HOME}}"

  if [[ -f "${candidate}" ]]; then
    return 0
  fi
  if [[ "${candidate}" != /* && -f "${REPO_ROOT}/${candidate}" ]]; then
    return 0
  fi
  return 1
}

add_ssl_alt_name() {
  local kind="$1"
  local value="$2"
  local token="${kind}:${value}"

  [[ -z "${value}" ]] && return 0
  if [[ " ${SSL_ALT_NAME_TOKENS[*]:-} " == *" ${token} "* ]]; then
    return 0
  fi
  SSL_ALT_NAME_TOKENS+=("${token}")
}

ensure_self_signed_ssl_cert() {
  local certfile="${AOR_SSL_CERTFILE}"
  local keyfile="${AOR_SSL_KEYFILE}"
  local days="${AOR_SSL_CERT_DAYS:-3650}"
  local config_file=""
  local dns_index=1
  local ip_index=1
  local token=""
  local kind=""
  local value=""
  local detected_ip=""

  if [[ -f "${certfile}" && -f "${keyfile}" ]]; then
    return 0
  fi
  if ! command -v openssl >/dev/null 2>&1; then
    echo "HTTPS is enabled, but openssl is not installed." >&2
    echo "Install openssl or run without --https / AOR_SSL_ENABLED=1." >&2
    exit 1
  fi

  if [[ -f "${certfile}" || -f "${keyfile}" ]]; then
    echo "HTTPS certificate/key pair is incomplete; generating a fresh matching self-signed pair."
  fi

  mkdir -p "$(dirname "${certfile}")" "$(dirname "${keyfile}")"
  chmod 700 "$(dirname "${keyfile}")" 2>/dev/null || true

  SSL_ALT_NAME_TOKENS=()
  add_ssl_alt_name DNS localhost
  add_ssl_alt_name IP 127.0.0.1
  add_ssl_alt_name DNS "$(hostname 2>/dev/null || true)"
  if [[ "${AOR_HOST}" != "0.0.0.0" && "${AOR_HOST}" != "::" ]]; then
    case "${AOR_HOST}" in
      *[!0-9.]*)
        add_ssl_alt_name DNS "${AOR_HOST}"
        ;;
      *)
        add_ssl_alt_name IP "${AOR_HOST}"
        ;;
    esac
  fi
  for detected_ip in $(hostname -I 2>/dev/null || true); do
    case "${detected_ip}" in
      *:*) ;;
      *[!0-9.]*) ;;
      *) add_ssl_alt_name IP "${detected_ip}" ;;
    esac
  done
  IFS=',' read -r -a EXTRA_SSL_ALT_NAMES <<< "${AOR_SSL_CERT_ALT_NAMES:-}"
  for token in "${EXTRA_SSL_ALT_NAMES[@]}"; do
    token="$(printf '%s' "${token}" | xargs)"
    [[ -z "${token}" ]] && continue
    case "${token}" in
      DNS:*|dns:*)
        add_ssl_alt_name DNS "${token#*:}"
        ;;
      IP:*|ip:*)
        add_ssl_alt_name IP "${token#*:}"
        ;;
      *[!0-9.]*)
        add_ssl_alt_name DNS "${token}"
        ;;
      *)
        add_ssl_alt_name IP "${token}"
        ;;
    esac
  done

  config_file="$(mktemp)"
  {
    cat <<'SSL_CONFIG'
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = OpenFabric Agent

[v3_req]
subjectAltName = @alt_names

[alt_names]
SSL_CONFIG
    for token in "${SSL_ALT_NAME_TOKENS[@]}"; do
      kind="${token%%:*}"
      value="${token#*:}"
      if [[ "${kind}" == "DNS" ]]; then
        printf 'DNS.%s = %s\n' "${dns_index}" "${value}"
        dns_index=$((dns_index + 1))
      else
        printf 'IP.%s = %s\n' "${ip_index}" "${value}"
        ip_index=$((ip_index + 1))
      fi
    done
  } > "${config_file}"

  echo "Generating self-signed HTTPS certificate:"
  echo "  cert: ${certfile}"
  echo "  key:  ${keyfile}"
  openssl req \
    -x509 \
    -nodes \
    -newkey rsa:2048 \
    -days "${days}" \
    -keyout "${keyfile}" \
    -out "${certfile}" \
    -config "${config_file}" >/dev/null 2>&1
  rm -f "${config_file}"
  chmod 600 "${keyfile}" 2>/dev/null || true
  chmod 644 "${certfile}" 2>/dev/null || true
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --config" >&2
        usage
        exit 1
      fi
      CONFIG_OVERRIDE="$2"
      shift 2
      ;;
    --host|--hosts)
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
    --audio-port)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --audio-port" >&2
        usage
        exit 1
      fi
      AUDIO_PORT_OVERRIDE="$2"
      shift 2
      ;;
    --manual-port)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --manual-port" >&2
        usage
        exit 1
      fi
      MANUAL_PORT_OVERRIDE="$2"
      shift 2
      ;;
    --website-port)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --website-port" >&2
        usage
        exit 1
      fi
      WEBSITE_PORT_OVERRIDE="$2"
      shift 2
      ;;
    --reload)
      RELOAD_OVERRIDE="1"
      shift
      ;;
    --https|--ssl)
      HTTPS_OVERRIDE="1"
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

# Launcher-level settings.
# Default to 0.0.0.0 so local Dockerized clients such as Open WebUI can reach
# the server without extra overrides. Set AOR_HOST=127.0.0.1 if you want the
# API bound to loopback only.
export AOR_HOST="${HOST_OVERRIDE:-${AOR_HOST:-0.0.0.0}}"
export AOR_PORT="${PORT_OVERRIDE:-${AOR_PORT:-8011}}"
export AOR_RELOAD="${RELOAD_OVERRIDE:-${AOR_RELOAD:-0}}"
export AOR_SSL_ENABLED="${HTTPS_OVERRIDE:-${AOR_SSL_ENABLED:-0}}"
if [[ -n "${HOST_OVERRIDE}" ]]; then
  export AOR_AUDIO_RUNTIME_HOST="${HOST_OVERRIDE}"
  export AOR_MANUAL_HOST="${HOST_OVERRIDE}"
  export AOR_WEBSITE_HOST="${HOST_OVERRIDE}"
elif [[ -n "${AOR_HOST_WAS_SET}" ]]; then
  export AOR_AUDIO_RUNTIME_HOST="${AOR_AUDIO_RUNTIME_HOST:-${AOR_HOST}}"
  export AOR_MANUAL_HOST="${AOR_MANUAL_HOST:-${AOR_HOST}}"
  export AOR_WEBSITE_HOST="${AOR_WEBSITE_HOST:-${AOR_HOST}}"
else
  export AOR_AUDIO_RUNTIME_HOST="${AOR_AUDIO_RUNTIME_HOST:-127.0.0.1}"
  export AOR_MANUAL_HOST="${AOR_MANUAL_HOST:-127.0.0.1}"
  export AOR_WEBSITE_HOST="${AOR_WEBSITE_HOST:-127.0.0.1}"
fi
export AOR_AUDIO_RUNTIME_PORT="${AUDIO_PORT_OVERRIDE:-${AOR_AUDIO_RUNTIME_PORT:-8012}}"
export AOR_MANUAL_PORT="${MANUAL_PORT_OVERRIDE:-${AOR_MANUAL_PORT:-8013}}"
export AOR_WEBSITE_PORT="${WEBSITE_PORT_OVERRIDE:-${AOR_WEBSITE_PORT:-8014}}"
export AOR_SSL_CERTFILE="${AOR_SSL_CERTFILE:-${REPO_ROOT}/artifacts/ssl/agent-ui.crt}"
export AOR_SSL_KEYFILE="${AOR_SSL_KEYFILE:-${REPO_ROOT}/artifacts/ssl/agent-ui.key}"
export AOR_LLM_PREFLIGHT_ENABLED="${AOR_LLM_PREFLIGHT_ENABLED:-0}"
export AOR_LLM_PREFLIGHT_TIMEOUT_SECONDS="${AOR_LLM_PREFLIGHT_TIMEOUT_SECONDS:-3}"

# Config loading. YAML is an explicit legacy bootstrap only; runtime controls
# live in the Agent UI settings database.
if [[ -n "${CONFIG_OVERRIDE}" ]]; then
  export AOR_APP_CONFIG_PATH="${CONFIG_OVERRIDE}"
elif [[ -n "${AOR_APP_CONFIG_PATH:-}" ]]; then
  export AOR_APP_CONFIG_PATH="${AOR_APP_CONFIG_PATH}"
else
  unset AOR_APP_CONFIG_PATH
fi

case "${AOR_LLM_PREFLIGHT_ENABLED,,}" in
  0|false|no|off)
    echo "Skipping LLM preflight check:"
    echo "  AOR_LLM_PREFLIGHT_ENABLED=${AOR_LLM_PREFLIGHT_ENABLED}"
    ;;
  *)
    LLM_PREFLIGHT_ARGS=(--timeout "${AOR_LLM_PREFLIGHT_TIMEOUT_SECONDS}")
    if [[ -n "${AOR_APP_CONFIG_PATH:-}" ]]; then
      LLM_PREFLIGHT_ARGS=(--config "${AOR_APP_CONFIG_PATH}" "${LLM_PREFLIGHT_ARGS[@]}")
    fi
    PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}" \
      python -m agent_runtime.api.llm_health "${LLM_PREFLIGHT_ARGS[@]}"
    ;;
esac

# Gateway and node routing.
export AOR_AVAILABLE_NODES="${AOR_AVAILABLE_NODES:-localhost}"
export AOR_DEFAULT_NODE="${AOR_DEFAULT_NODE:-localhost}"
export AOR_GATEWAY_URL="${AOR_GATEWAY_URL:-http://127.0.0.1:8787}"
export AOR_GATEWAY_TIMEOUT_SECONDS="${AOR_GATEWAY_TIMEOUT_SECONDS:-30}"

# Shell and execution controls.
export AOR_SHELL_MODE="${AOR_SHELL_MODE:-read_only}"
export AOR_SHELL_ALLOW_MUTATION_WITH_APPROVAL="${AOR_SHELL_ALLOW_MUTATION_WITH_APPROVAL:-0}"
export AOR_SHELL_ALLOWED_ROOTS="${AOR_SHELL_ALLOWED_ROOTS:-}"
export AOR_SHELL_DEFAULT_CWD="${AOR_SHELL_DEFAULT_CWD:-}"
export AOR_SHELL_MAX_OUTPUT_CHARS="${AOR_SHELL_MAX_OUTPUT_CHARS:-20000}"
export AOR_SHELL_COMMAND_TIMEOUT_SECONDS="${AOR_SHELL_COMMAND_TIMEOUT_SECONDS:-30}"
export AOR_SHUTDOWN_GRACE_SECONDS="${AOR_SHUTDOWN_GRACE_SECONDS:-5}"
export AOR_WORKER_JOIN_TIMEOUT_SECONDS="${AOR_WORKER_JOIN_TIMEOUT_SECONDS:-2}"
export AOR_TOOL_PROCESS_KILL_GRACE_SECONDS="${AOR_TOOL_PROCESS_KILL_GRACE_SECONDS:-1}"
export AOR_RUNTIME_TIMEZONE="${AOR_RUNTIME_TIMEZONE:-}"

# Planning and LLM-assisted behavior.
export AOR_ENABLE_LLM_INTENT_EXTRACTION="${AOR_ENABLE_LLM_INTENT_EXTRACTION:-0}"
export AOR_ENABLE_SQL_LLM_GENERATION="${AOR_ENABLE_SQL_LLM_GENERATION:-0}"
export AOR_ACTION_PLANNER_ENABLED="${AOR_ACTION_PLANNER_ENABLED:-1}"
export AOR_LEGACY_EXECUTION_PLANNER_ENABLED="${AOR_LEGACY_EXECUTION_PLANNER_ENABLED:-0}"

# Presentation and trace settings.
export AOR_PRESENTATION_MODE="${AOR_PRESENTATION_MODE:-user}"
export AOR_RESPONSE_RENDER_MODE="${AOR_RESPONSE_RENDER_MODE:-user}"
export AOR_ENABLE_LLM_SUMMARY="${AOR_ENABLE_LLM_SUMMARY:-0}"
export AOR_LLM_SUMMARY_MAX_FACTS="${AOR_LLM_SUMMARY_MAX_FACTS:-50}"
export AOR_INCLUDE_INTERNAL_TELEMETRY="${AOR_INCLUDE_INTERNAL_TELEMETRY:-0}"
export AOR_SHOW_EXECUTED_COMMANDS="${AOR_SHOW_EXECUTED_COMMANDS:-1}"
export AOR_SHOW_VALIDATION_EVENTS="${AOR_SHOW_VALIDATION_EVENTS:-0}"
export AOR_SHOW_PLANNER_EVENTS="${AOR_SHOW_PLANNER_EVENTS:-0}"
export AOR_SHOW_TOOL_EVENTS="${AOR_SHOW_TOOL_EVENTS:-0}"
export AOR_OPENWEBUI_TRACE_MODE="${AOR_OPENWEBUI_TRACE_MODE:-}"
export AOR_SHOW_RESPONSE_STATS="${AOR_SHOW_RESPONSE_STATS:-1}"
export AOR_SHOW_PROMPT_SUGGESTIONS="${AOR_SHOW_PROMPT_SUGGESTIONS:-0}"
export AOR_SHOW_DEBUG_METADATA="${AOR_SHOW_DEBUG_METADATA:-0}"

# Presentation LLM summary settings.
export AOR_ENABLE_PRESENTATION_LLM_SUMMARY="${AOR_ENABLE_PRESENTATION_LLM_SUMMARY:-0}"
export AOR_PRESENTATION_LLM_MAX_FACTS="${AOR_PRESENTATION_LLM_MAX_FACTS:-50}"
export AOR_PRESENTATION_LLM_MAX_INPUT_CHARS="${AOR_PRESENTATION_LLM_MAX_INPUT_CHARS:-4000}"
export AOR_PRESENTATION_LLM_MAX_OUTPUT_CHARS="${AOR_PRESENTATION_LLM_MAX_OUTPUT_CHARS:-1500}"
export AOR_PRESENTATION_LLM_INCLUDE_ROW_SAMPLES="${AOR_PRESENTATION_LLM_INCLUDE_ROW_SAMPLES:-0}"
export AOR_PRESENTATION_LLM_INCLUDE_PATHS="${AOR_PRESENTATION_LLM_INCLUDE_PATHS:-0}"

# Intelligent output and semantic frame settings.
export AOR_INTELLIGENT_OUTPUT_MODE="${AOR_INTELLIGENT_OUTPUT_MODE:-off}"
export AOR_INTELLIGENT_OUTPUT_MAX_FIELDS="${AOR_INTELLIGENT_OUTPUT_MAX_FIELDS:-8}"
export AOR_SEMANTIC_FRAME_MODE="${AOR_SEMANTIC_FRAME_MODE:-enforce}"
export AOR_SEMANTIC_FRAME_MAX_DEPTH="${AOR_SEMANTIC_FRAME_MAX_DEPTH:-10}"
export AOR_SEMANTIC_FRAME_MAX_CHILDREN="${AOR_SEMANTIC_FRAME_MAX_CHILDREN:-8}"
export AOR_LLM_STAGE_MAX_DEPTH="${AOR_LLM_STAGE_MAX_DEPTH:-10}"
export AOR_PRESENTATION_INTENT_MAX_DEPTH="${AOR_PRESENTATION_INTENT_MAX_DEPTH:-10}"

# Insight layer settings.
export AOR_ENABLE_INSIGHT_LAYER="${AOR_ENABLE_INSIGHT_LAYER:-1}"
export AOR_ENABLE_LLM_INSIGHTS="${AOR_ENABLE_LLM_INSIGHTS:-0}"
export AOR_INSIGHT_MAX_FACTS="${AOR_INSIGHT_MAX_FACTS:-50}"
export AOR_INSIGHT_MAX_INPUT_CHARS="${AOR_INSIGHT_MAX_INPUT_CHARS:-4000}"
export AOR_INSIGHT_MAX_OUTPUT_CHARS="${AOR_INSIGHT_MAX_OUTPUT_CHARS:-1500}"

# Automatic artifact settings.
export AOR_AUTO_ARTIFACTS_ENABLED="${AOR_AUTO_ARTIFACTS_ENABLED:-1}"
export AOR_AUTO_ARTIFACT_ROW_THRESHOLD="${AOR_AUTO_ARTIFACT_ROW_THRESHOLD:-50}"
export AOR_AUTO_ARTIFACT_DIR="${AOR_AUTO_ARTIFACT_DIR:-outputs}"
export AOR_AUTO_ARTIFACT_FORMAT="${AOR_AUTO_ARTIFACT_FORMAT:-csv}"
export AOR_AGENT_UI_ALLOW_RAW_PREVIEWS="${AOR_AGENT_UI_ALLOW_RAW_PREVIEWS:-1}"
export AOR_AGENT_UI_ALLOW_FULL_PAYLOADS="${AOR_AGENT_UI_ALLOW_FULL_PAYLOADS:-0}"

# Local audio dictation service. The backend runs whisper.cpp locally after the
# browser records audio; no cloud transcription API is used.
LOCAL_WHISPER_CLI="${REPO_ROOT}/.aor/audio-transcriber/bin/whisper-cli"
LOCAL_VENDOR_WHISPER_CLI="${REPO_ROOT}/vendor/audio-transcriber/linux-x86_64/whisper-cli"
AUDIO_BINARY_READY=0
AUDIO_MODEL_READY=0
AUDIO_RESOLVED_BINARY=""

if [[ -n "${AOR_AUDIO_TRANSCRIBER_BINARY:-}" ]]; then
  if AUDIO_RESOLVED_BINARY="$(resolve_audio_executable "${AOR_AUDIO_TRANSCRIBER_BINARY}")"; then
    export AOR_AUDIO_TRANSCRIBER_BINARY="${AUDIO_RESOLVED_BINARY}"
    AUDIO_BINARY_READY=1
  fi
else
  for audio_binary_candidate in "${LOCAL_WHISPER_CLI}" whisper-cli "${LOCAL_VENDOR_WHISPER_CLI}"; do
    if AUDIO_RESOLVED_BINARY="$(resolve_audio_executable "${audio_binary_candidate}")"; then
      export AOR_AUDIO_TRANSCRIBER_BINARY="${AUDIO_RESOLVED_BINARY}"
      AUDIO_BINARY_READY=1
      break
    fi
  done
fi

export AOR_AUDIO_TRANSCRIBER_ENGINE="${AOR_AUDIO_TRANSCRIBER_ENGINE:-whisper_cpp}"
export AOR_AUDIO_TRANSCRIBER_MODEL_TIER="${AOR_AUDIO_TRANSCRIBER_MODEL_TIER:-low}"
if [[ -z "${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-}" ]]; then
  AUDIO_DEFAULT_MODEL_PATH="$(resolve_audio_model_path "${AOR_AUDIO_TRANSCRIBER_MODEL_TIER}")"
  AUDIO_VENDOR_MODEL_PATH="$(resolve_audio_vendor_model_path "${AOR_AUDIO_TRANSCRIBER_MODEL_TIER}")"
  if [[ ! -f "${AUDIO_DEFAULT_MODEL_PATH}" && -f "${AUDIO_VENDOR_MODEL_PATH}" ]]; then
    export AOR_AUDIO_TRANSCRIBER_MODEL_PATH="${AUDIO_VENDOR_MODEL_PATH}"
  else
    export AOR_AUDIO_TRANSCRIBER_MODEL_PATH="${AUDIO_DEFAULT_MODEL_PATH}"
  fi
fi
if audio_file_exists "${AOR_AUDIO_TRANSCRIBER_MODEL_PATH}"; then
  AUDIO_MODEL_READY=1
fi

if [[ -z "${AOR_AUDIO_TRANSCRIBER_ENABLED_WAS_SET}" && ( "${AUDIO_BINARY_READY}" != "1" || "${AUDIO_MODEL_READY}" != "1" ) ]]; then
  AUDIO_MISSING=()
  [[ "${AUDIO_BINARY_READY}" == "1" ]] || AUDIO_MISSING+=("whisper-cli")
  [[ "${AUDIO_MODEL_READY}" == "1" ]] || AUDIO_MISSING+=("Whisper model")
  export AOR_AUDIO_TRANSCRIBER_ENABLED=0
  export AOR_AUDIO_TRANSCRIBER_AUTOSTART="${AOR_AUDIO_TRANSCRIBER_AUTOSTART:-0}"
  echo "Audio transcription disabled because ${AUDIO_MISSING[*]} is missing."
  echo "Run ./setup-audio-transcriber.sh to build whisper-cli and download models."
else
  export AOR_AUDIO_TRANSCRIBER_ENABLED="${AOR_AUDIO_TRANSCRIBER_ENABLED:-1}"
fi
export AOR_AUDIO_TRANSCRIBER_BINARY="${AOR_AUDIO_TRANSCRIBER_BINARY:-whisper-cli}"
export AOR_AUDIO_TRANSCRIBER_FFMPEG_BINARY="${AOR_AUDIO_TRANSCRIBER_FFMPEG_BINARY:-ffmpeg}"
export AOR_AUDIO_TRANSCRIBER_FFPROBE_BINARY="${AOR_AUDIO_TRANSCRIBER_FFPROBE_BINARY:-ffprobe}"
export AOR_AUDIO_TRANSCRIBER_MAX_SECONDS="${AOR_AUDIO_TRANSCRIBER_MAX_SECONDS:-120}"
export AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB="${AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB:-25}"
export AOR_AUDIO_TRANSCRIBER_LANGUAGE="${AOR_AUDIO_TRANSCRIBER_LANGUAGE:-auto}"
export AOR_AUDIO_RUNTIME_RELOAD="${AOR_AUDIO_RUNTIME_RELOAD:-${AOR_RELOAD}}"
AOR_AUDIO_RUNTIME_PROXY_HOST="${AOR_AUDIO_RUNTIME_HOST}"
case "${AOR_AUDIO_RUNTIME_PROXY_HOST}" in
  0.0.0.0|::)
    AOR_AUDIO_RUNTIME_PROXY_HOST="127.0.0.1"
    ;;
esac
export AOR_AUDIO_TRANSCRIBER_SERVICE_URL="${AOR_AUDIO_TRANSCRIBER_SERVICE_URL:-http://${AOR_AUDIO_RUNTIME_PROXY_HOST}:${AOR_AUDIO_RUNTIME_PORT}}"
export AOR_AUDIO_TRANSCRIBER_AUTOSTART="${AOR_AUDIO_TRANSCRIBER_AUTOSTART:-1}"
export AOR_MANUAL_RELOAD="${AOR_MANUAL_RELOAD:-${AOR_RELOAD}}"
export AOR_MANUAL_AUTOSTART="${AOR_MANUAL_AUTOSTART:-1}"
export AOR_WEBSITE_RELOAD="${AOR_WEBSITE_RELOAD:-${AOR_RELOAD}}"
export AOR_WEBSITE_AUTOSTART="${AOR_WEBSITE_AUTOSTART:-1}"

AUDIO_RUNTIME_PID=""
MANUAL_RUNTIME_PID=""
WEBSITE_RUNTIME_PID=""
cleanup_child_runtime() {
  local pid="$1"
  local name="$2"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    echo "Stopping ${name}..."
    kill "${pid}" >/dev/null 2>&1 || true
    wait "${pid}" >/dev/null 2>&1 || true
  fi
}

cleanup_auxiliary_runtimes() {
  cleanup_child_runtime "${WEBSITE_RUNTIME_PID}" "website runtime"
  cleanup_child_runtime "${MANUAL_RUNTIME_PID}" "manual runtime"
  cleanup_child_runtime "${AUDIO_RUNTIME_PID}" "audio runtime"
}

case "${AOR_AUDIO_TRANSCRIBER_ENABLED,,}:${AOR_AUDIO_TRANSCRIBER_AUTOSTART,,}" in
  1:1|1:true|1:yes|1:on|true:1|true:true|true:yes|true:on|yes:1|yes:true|yes:yes|yes:on|on:1|on:true|on:yes|on:on)
    echo "Starting OpenFabric audio runtime at http://${AOR_AUDIO_RUNTIME_HOST}:${AOR_AUDIO_RUNTIME_PORT} (${AOR_AUDIO_TRANSCRIBER_MODEL_TIER})"
    ./startup-audio.sh --host "${AOR_AUDIO_RUNTIME_HOST}" --port "${AOR_AUDIO_RUNTIME_PORT}" &
    AUDIO_RUNTIME_PID="$!"
    ;;
esac

case "${AOR_MANUAL_AUTOSTART,,}" in
  1|true|yes|on)
    ./startmanual.sh --host "${AOR_MANUAL_HOST}" --port "${AOR_MANUAL_PORT}" &
    MANUAL_RUNTIME_PID="$!"
    ;;
esac

case "${AOR_WEBSITE_AUTOSTART,,}" in
  1|true|yes|on)
    ./startwebsite.sh --host "${AOR_WEBSITE_HOST}" --port "${AOR_WEBSITE_PORT}" &
    WEBSITE_RUNTIME_PID="$!"
    ;;
esac

if [[ -n "${AUDIO_RUNTIME_PID}${MANUAL_RUNTIME_PID}${WEBSITE_RUNTIME_PID}" ]]; then
  trap cleanup_auxiliary_runtimes EXIT INT TERM
fi

UVICORN_ARGS=(
  --app-dir src
  agent_runtime.api.app:create_app
  --factory
  --host "${AOR_HOST}"
  --port "${AOR_PORT}"
)

if is_enabled "${AOR_SSL_ENABLED}"; then
  ensure_self_signed_ssl_cert
  UVICORN_ARGS+=(--ssl-certfile "${AOR_SSL_CERTFILE}" --ssl-keyfile "${AOR_SSL_KEYFILE}")
  echo "HTTPS enabled. Open https://localhost:${AOR_PORT}/agent-ui or https://<host-ip>:${AOR_PORT}/agent-ui and accept the browser warning."
fi

case "${AOR_RELOAD,,}" in
  1|true|yes|on)
    UVICORN_ARGS+=(--reload)
    ;;
esac

if [[ -n "${AUDIO_RUNTIME_PID}${MANUAL_RUNTIME_PID}${WEBSITE_RUNTIME_PID}" ]]; then
  python -m uvicorn "${UVICORN_ARGS[@]}"
else
  exec python -m uvicorn "${UVICORN_ARGS[@]}"
fi
