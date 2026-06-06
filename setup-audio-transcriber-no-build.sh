#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
TOOLS_ROOT="${AOR_AUDIO_TOOLS_ROOT:-${REPO_ROOT}/.aor/audio-transcriber}"
WHISPER_BIN_DIR="${TOOLS_ROOT}/bin"
MODEL_DIR="${AOR_AUDIO_TRANSCRIBER_MODEL_DIR:-${REPO_ROOT}/artifacts/models/whisper}"
MODEL_PATH="${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-${MODEL_DIR}/ggml-base.en.bin}"
VENDORED_ROOT="${AOR_AUDIO_TRANSCRIBER_VENDOR_ROOT:-${REPO_ROOT}/vendor/audio-transcriber}"
VENDORED_MODEL_PATH="${AOR_AUDIO_TRANSCRIBER_VENDOR_MODEL_PATH:-${VENDORED_ROOT}/models/ggml-base.en.bin}"
INSTALL_SYSTEM_DEPS="${AOR_INSTALL_SYSTEM_DEPS:-1}"

mkdir -p "${WHISPER_BIN_DIR}" "${MODEL_DIR}"

have_command() {
  command -v "$1" >/dev/null 2>&1
}

resolve_executable() {
  local candidate="$1"
  local path=""

  if [[ -z "${candidate}" ]]; then
    return 0
  fi
  if [[ "${candidate}" == */* ]]; then
    path="${candidate/#\~/${HOME}}"
    if [[ -x "${path}" ]]; then
      printf '%s\n' "${path}"
    fi
    return 0
  fi
  command -v "${candidate}" || true
}

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

is_disabled() {
  case "$(lower "${1:-}")" in
    0|false|no|off) return 0 ;;
    *) return 1 ;;
  esac
}

vendored_cli_path() {
  local os_name
  local arch_name
  os_name="$(uname -s)"
  arch_name="$(uname -m)"

  if [[ "${os_name}" == "Linux" && ( "${arch_name}" == "x86_64" || "${arch_name}" == "amd64" ) ]]; then
    printf '%s\n' "${VENDORED_ROOT}/linux-x86_64/whisper-cli"
    return 0
  fi

  printf '\n'
}

sudocmd() {
  if [[ "$(id -u)" == "0" ]]; then
    "$@"
  elif have_command sudo; then
    sudo "$@"
  else
    echo "Need root privileges to install system packages, but sudo was not found." >&2
    return 1
  fi
}

install_system_deps() {
  if is_disabled "${INSTALL_SYSTEM_DEPS}" || [[ "${AOR_SKIP_SYSTEM_DEPS:-0}" =~ ^(1|true|yes|on)$ ]]; then
    return 0
  fi

  if have_command apt-get; then
    sudocmd apt-get update
    sudocmd apt-get install -y ffmpeg ca-certificates
    return 0
  fi

  if have_command dnf; then
    sudocmd dnf install -y ffmpeg ca-certificates
    return 0
  fi

  if have_command yum; then
    sudocmd yum install -y ffmpeg ca-certificates
    return 0
  fi

  if have_command pacman; then
    sudocmd pacman -Sy --needed --noconfirm ffmpeg ca-certificates
    return 0
  fi

  if have_command zypper; then
    sudocmd zypper --non-interactive install ffmpeg ca-certificates
    return 0
  fi

  if have_command brew; then
    brew install ffmpeg
    return 0
  fi

  return 0
}

collect_missing_runtime_deps() {
  local missing=()
  local tool=""

  for tool in ffmpeg ffprobe; do
    if ! have_command "${tool}"; then
      missing+=("${tool}")
    fi
  done

  printf '%s\n' "${missing[@]}"
}

RUNTIME_DEPS_READY=1
missing=()
while IFS= read -r tool; do
  [[ -n "${tool}" ]] && missing+=("${tool}")
done < <(collect_missing_runtime_deps)

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Installing no-build audio runtime prerequisites: ${missing[*]}"
  install_system_deps || true

  missing=()
  while IFS= read -r tool; do
    [[ -n "${tool}" ]] && missing+=("${tool}")
  done < <(collect_missing_runtime_deps)

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Audio runtime prerequisites are still missing: ${missing[*]}" >&2
    echo "Audio transcription will stay disabled until ffmpeg/ffprobe are installed." >&2
    RUNTIME_DEPS_READY=0
  fi
fi

WHISPER_CLI_PATH="$(resolve_executable "${AOR_AUDIO_TRANSCRIBER_BINARY:-whisper-cli}" || true)"
if [[ -z "${WHISPER_CLI_PATH}" ]]; then
  VENDORED_WHISPER_CLI="$(vendored_cli_path)"
  if [[ -x "${WHISPER_BIN_DIR}/whisper-cli" ]]; then
    WHISPER_CLI_PATH="${WHISPER_BIN_DIR}/whisper-cli"
  elif [[ -n "${VENDORED_WHISPER_CLI}" && -x "${VENDORED_WHISPER_CLI}" ]]; then
    cp "${VENDORED_WHISPER_CLI}" "${WHISPER_BIN_DIR}/whisper-cli"
    chmod +x "${WHISPER_BIN_DIR}/whisper-cli"
    WHISPER_CLI_PATH="${WHISPER_BIN_DIR}/whisper-cli"
  fi
fi

if [[ -z "${WHISPER_CLI_PATH}" ]]; then
  echo "No no-build whisper-cli is available; audio transcription will stay disabled." >&2
  echo "Run ./setup-audio-transcriber.sh to build whisper-cli, or provide AOR_AUDIO_TRANSCRIBER_BINARY." >&2
  exit 0
fi

if [[ ! -f "${MODEL_PATH}" ]]; then
  if [[ -f "${VENDORED_MODEL_PATH}" ]]; then
    cp "${VENDORED_MODEL_PATH}" "${MODEL_PATH}"
  else
    echo "No no-build Whisper model is available; audio transcription will stay disabled." >&2
    echo "Run ./setup-audio-transcriber.sh to download models, or provide AOR_AUDIO_TRANSCRIBER_MODEL_PATH." >&2
    exit 0
  fi
fi

if [[ "${RUNTIME_DEPS_READY}" != "1" ]]; then
  echo "No-build audio transcriber setup skipped because runtime dependencies are missing." >&2
  exit 0
fi

cat <<EOF
No-build audio transcriber setup complete.
whisper-cli: ${WHISPER_CLI_PATH}
ffmpeg: $(command -v ffmpeg)
ffprobe: $(command -v ffprobe)
model path: ${MODEL_PATH}
model status: ready

Suggested environment:
  export AOR_AUDIO_TRANSCRIBER_BINARY="${WHISPER_CLI_PATH}"
  export AOR_AUDIO_TRANSCRIBER_MODEL_PATH="${MODEL_PATH}"
EOF
