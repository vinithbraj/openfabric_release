#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"

HOST_OVERRIDE=""
PORT_OVERRIDE=""
RELOAD_OVERRIDE=""
MODEL_TIER=""
AOR_AUDIO_TRANSCRIBER_ENABLED_WAS_SET="${AOR_AUDIO_TRANSCRIBER_ENABLED+x}"
MODEL_DIR="${REPO_ROOT}/artifacts/models/whisper"
MODEL_LOW_PATH="${AOR_AUDIO_TRANSCRIBER_BASE_MODEL_PATH:-${MODEL_DIR}/ggml-base.en.bin}"
MODEL_MED_PATH="${AOR_AUDIO_TRANSCRIBER_MEDIUM_MODEL_PATH:-${MODEL_DIR}/ggml-medium.en.bin}"
MODEL_HIGH_PATH="${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-${MODEL_DIR}/ggml-large-v3.bin}"

resolve_model_path() {
  local tier="${1,,}"
  case "${tier}" in
    low)
      echo "${MODEL_LOW_PATH}"
      ;;
    med)
      echo "${MODEL_MED_PATH}"
      ;;
    high)
      echo "${MODEL_HIGH_PATH}"
      ;;
    *)
      echo "Invalid audio model tier: ${tier}" >&2
      echo "Usage: $0 [--host HOST] [--port PORT] [--reload] [--low|--med|--high]" >&2
      exit 1
      ;;
  esac
}

resolve_vendor_model_path() {
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
      echo "Invalid audio model tier: ${tier}" >&2
      echo "Usage: $0 [--host HOST] [--port PORT] [--reload] [--low|--med|--high]" >&2
      exit 1
      ;;
  esac
}

resolve_executable() {
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --host" >&2
        echo "Usage: $0 [--host HOST] [--port PORT] [--reload] [--low|--med|--high]" >&2
        exit 1
      fi
      HOST_OVERRIDE="$2"
      shift 2
      ;;
    --port)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --port" >&2
        echo "Usage: $0 [--host HOST] [--port PORT] [--reload] [--low|--med|--high]" >&2
        exit 1
      fi
      PORT_OVERRIDE="$2"
      shift 2
      ;;
    --reload)
      RELOAD_OVERRIDE="1"
      shift
      ;;
    --low)
      MODEL_TIER="low"
      shift
      ;;
    --med)
      MODEL_TIER="med"
      shift
      ;;
    --high)
      MODEL_TIER="high"
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--host HOST] [--port PORT] [--reload] [--low|--med|--high]" >&2
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

LOCAL_WHISPER_CLI="${REPO_ROOT}/.aor/audio-transcriber/bin/whisper-cli"
LOCAL_VENDOR_WHISPER_CLI="${REPO_ROOT}/vendor/audio-transcriber/linux-x86_64/whisper-cli"
AUDIO_BINARY_READY=0
AUDIO_MODEL_READY=0
AUDIO_RESOLVED_BINARY=""

if [[ -n "${AOR_AUDIO_TRANSCRIBER_BINARY:-}" ]]; then
  if AUDIO_RESOLVED_BINARY="$(resolve_executable "${AOR_AUDIO_TRANSCRIBER_BINARY}")"; then
    export AOR_AUDIO_TRANSCRIBER_BINARY="${AUDIO_RESOLVED_BINARY}"
    AUDIO_BINARY_READY=1
  fi
else
  for audio_binary_candidate in "${LOCAL_WHISPER_CLI}" whisper-cli "${LOCAL_VENDOR_WHISPER_CLI}"; do
    if AUDIO_RESOLVED_BINARY="$(resolve_executable "${audio_binary_candidate}")"; then
      export AOR_AUDIO_TRANSCRIBER_BINARY="${AUDIO_RESOLVED_BINARY}"
      AUDIO_BINARY_READY=1
      break
    fi
  done
fi

export AOR_AUDIO_RUNTIME_HOST="${HOST_OVERRIDE:-${AOR_AUDIO_RUNTIME_HOST:-127.0.0.1}}"
export AOR_AUDIO_RUNTIME_PORT="${PORT_OVERRIDE:-${AOR_AUDIO_RUNTIME_PORT:-8012}}"
export AOR_AUDIO_RUNTIME_RELOAD="${RELOAD_OVERRIDE:-${AOR_AUDIO_RUNTIME_RELOAD:-0}}"

export AOR_AUDIO_TRANSCRIBER_MODEL_TIER="${MODEL_TIER:-${AOR_AUDIO_TRANSCRIBER_MODEL_TIER:-low}}"
if [[ -z "${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-}" ]]; then
  AUDIO_DEFAULT_MODEL_PATH="$(resolve_model_path "${AOR_AUDIO_TRANSCRIBER_MODEL_TIER}")"
  AUDIO_VENDOR_MODEL_PATH="$(resolve_vendor_model_path "${AOR_AUDIO_TRANSCRIBER_MODEL_TIER}")"
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
  echo "Audio runtime not started because ${AUDIO_MISSING[*]} is missing."
  echo "Run ./setup-audio-transcriber.sh to build whisper-cli and download models."
  exit 0
fi

export AOR_AUDIO_TRANSCRIBER_ENABLED="${AOR_AUDIO_TRANSCRIBER_ENABLED:-1}"
export AOR_AUDIO_TRANSCRIBER_ENGINE="${AOR_AUDIO_TRANSCRIBER_ENGINE:-whisper_cpp}"
export AOR_AUDIO_TRANSCRIBER_BINARY="${AOR_AUDIO_TRANSCRIBER_BINARY:-whisper-cli}"
export AOR_AUDIO_TRANSCRIBER_FFMPEG_BINARY="${AOR_AUDIO_TRANSCRIBER_FFMPEG_BINARY:-ffmpeg}"
export AOR_AUDIO_TRANSCRIBER_FFPROBE_BINARY="${AOR_AUDIO_TRANSCRIBER_FFPROBE_BINARY:-ffprobe}"
export AOR_AUDIO_TRANSCRIBER_MAX_SECONDS="${AOR_AUDIO_TRANSCRIBER_MAX_SECONDS:-120}"
export AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB="${AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB:-25}"
export AOR_AUDIO_TRANSCRIBER_LANGUAGE="${AOR_AUDIO_TRANSCRIBER_LANGUAGE:-auto}"

UVICORN_ARGS=(
  --app-dir src
  audio_runtime.api.app:create_app
  --factory
  --host "${AOR_AUDIO_RUNTIME_HOST}"
  --port "${AOR_AUDIO_RUNTIME_PORT}"
)

case "${AOR_AUDIO_RUNTIME_RELOAD,,}" in
  1|true|yes|on)
    UVICORN_ARGS+=(--reload)
    ;;
esac

exec python -m uvicorn "${UVICORN_ARGS[@]}"
