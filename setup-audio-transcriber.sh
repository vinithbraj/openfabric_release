#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
TOOLS_ROOT="${AOR_AUDIO_TOOLS_ROOT:-${REPO_ROOT}/.aor/audio-transcriber}"
WHISPER_CPP_DIR="${TOOLS_ROOT}/whisper.cpp"
WHISPER_BIN_DIR="${TOOLS_ROOT}/bin"
MODEL_DIR="${AOR_AUDIO_TRANSCRIBER_MODEL_DIR:-${REPO_ROOT}/artifacts/models/whisper}"
MODEL_BASE_PATH="${AOR_AUDIO_TRANSCRIBER_BASE_MODEL_PATH:-${MODEL_DIR}/ggml-base.en.bin}"
MODEL_LARGE_PATH="${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-${MODEL_DIR}/ggml-large-v3.bin}"
MODEL_MED_PATH="${AOR_AUDIO_TRANSCRIBER_MEDIUM_MODEL_PATH:-${MODEL_DIR}/ggml-medium.en.bin}"
MODEL_BASE_URL="${AOR_AUDIO_BASE_TRANSCRIBER_MODEL_URL:-https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin}"
MODEL_MED_URL="${AOR_AUDIO_MEDIUM_TRANSCRIBER_MODEL_URL:-https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin}"
MODEL_LARGE_URL="${AOR_AUDIO_TRANSCRIBER_MODEL_URL:-https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin}"
VENDORED_ROOT="${AOR_AUDIO_TRANSCRIBER_VENDOR_ROOT:-${REPO_ROOT}/vendor/audio-transcriber}"
VENDORED_MODEL_BASE_PATH="${AOR_AUDIO_TRANSCRIBER_VENDOR_BASE_MODEL_PATH:-${VENDORED_ROOT}/models/ggml-base.en.bin}"
VENDORED_MODEL_MED_PATH="${AOR_AUDIO_TRANSCRIBER_VENDOR_MEDIUM_MODEL_PATH:-${VENDORED_ROOT}/models/ggml-medium.en.bin}"
VENDORED_MODEL_LARGE_PATH="${AOR_AUDIO_TRANSCRIBER_VENDOR_LARGE_MODEL_PATH:-${VENDORED_ROOT}/models/ggml-large-v3.bin}"
INSTALL_WHISPER="${AOR_INSTALL_WHISPER_CPP:-auto}"
INSTALL_SYSTEM_DEPS="${AOR_INSTALL_SYSTEM_DEPS:-1}"
DOWNLOAD_MODEL="${AOR_DOWNLOAD_WHISPER_MODEL:-1}"

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
    sudocmd apt-get install -y git cmake build-essential ffmpeg curl ca-certificates
    return 0
  fi

  if have_command dnf; then
    sudocmd dnf install -y git cmake gcc gcc-c++ make ffmpeg curl ca-certificates
    return 0
  fi

  if have_command yum; then
    sudocmd yum install -y git cmake gcc gcc-c++ make ffmpeg curl ca-certificates
    return 0
  fi

  if have_command pacman; then
    sudocmd pacman -Sy --needed --noconfirm git cmake base-devel ffmpeg curl ca-certificates
    return 0
  fi

  if have_command zypper; then
    sudocmd zypper --non-interactive install git cmake gcc gcc-c++ make ffmpeg curl ca-certificates
    return 0
  fi

  if have_command brew; then
    brew install git cmake ffmpeg curl
    return 0
  fi

  return 0
}

collect_missing() {
  local missing=()
  local tool=""
  local needs_whisper_build=0
  local needs_model_download=0
  local vendored_cli=""
  local resolved_cli=""

  vendored_cli="$(vendored_cli_path)"
  resolved_cli="$(resolve_executable "${AOR_AUDIO_TRANSCRIBER_BINARY:-whisper-cli}" || true)"
  if [[ -z "${resolved_cli}" && ! -x "${WHISPER_BIN_DIR}/whisper-cli" && ( -z "${vendored_cli}" || ! -x "${vendored_cli}" ) ]]; then
    needs_whisper_build=1
  fi

  if [[ ! -f "${MODEL_LARGE_PATH}" && ! -f "${VENDORED_MODEL_LARGE_PATH}" ]] && ! is_disabled "${DOWNLOAD_MODEL}"; then
    needs_model_download=1
  fi
  if [[ ! -f "${MODEL_MED_PATH}" && ! -f "${VENDORED_MODEL_MED_PATH}" ]] && ! is_disabled "${DOWNLOAD_MODEL}"; then
    needs_model_download=1
  fi

  for tool in ffmpeg ffprobe; do
    if ! have_command "${tool}"; then
      missing+=("${tool}")
    fi
  done

  if [[ "${needs_whisper_build}" == "1" ]]; then
    for tool in git cmake; do
      if ! have_command "${tool}"; then
        missing+=("${tool}")
      fi
    done
  fi

  if [[ "${needs_model_download}" == "1" ]] && ! have_command curl; then
    missing+=("curl")
  fi

  printf '%s\n' "${missing[@]}"
}

missing=()
while IFS= read -r tool; do
  [[ -n "${tool}" ]] && missing+=("${tool}")
done < <(collect_missing)

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Installing audio transcriber prerequisites: ${missing[*]}"
  if ! install_system_deps; then
    echo "Automatic dependency installation failed." >&2
  fi

  missing=()
  while IFS= read -r tool; do
    [[ -n "${tool}" ]] && missing+=("${tool}")
  done < <(collect_missing)

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Audio transcriber prerequisites are still missing: ${missing[*]}" >&2
    echo "Install them with your OS package manager, then rerun this script." >&2
    echo "Ubuntu example: sudo apt-get install -y git cmake build-essential ffmpeg curl ca-certificates" >&2
    echo "macOS example: brew install git cmake ffmpeg curl" >&2
    echo "Set AOR_INSTALL_SYSTEM_DEPS=0 to skip automatic package installation." >&2
    exit 1
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
  if is_disabled "${INSTALL_WHISPER}"; then
    echo "whisper-cli is not installed and AOR_INSTALL_WHISPER_CPP disables local build." >&2
    exit 1
  fi

  bash "${REPO_ROOT}/scripts/build-whisper-cli.sh" "${WHISPER_BIN_DIR}/whisper-cli" "${WHISPER_CPP_DIR}"
  WHISPER_CLI_PATH="${WHISPER_BIN_DIR}/whisper-cli"
fi

download_model_file() {
  local model_path="$1"
  local model_url="$2"
  local vendored_path="$3"
  local tmp_model

  if [[ -f "${model_path}" ]]; then
    return 0
  fi
  if [[ -f "${vendored_path}" ]]; then
    cp "${vendored_path}" "${model_path}"
    return 0
  fi
  if is_disabled "${DOWNLOAD_MODEL}"; then
    echo "Whisper model is missing and AOR_DOWNLOAD_WHISPER_MODEL disables download:" >&2
    echo "  ${model_path}" >&2
    return 0
  fi

  echo "Downloading whisper.cpp model:"
  echo "  ${model_url}"
  tmp_model="${model_path}.tmp"
  rm -f "${tmp_model}"
  curl -L --fail --retry 3 --retry-delay 2 --progress-bar "${model_url}" -o "${tmp_model}"
  mv "${tmp_model}" "${model_path}"
}

download_model_file "${MODEL_BASE_PATH}" "${MODEL_BASE_URL}" "${VENDORED_MODEL_BASE_PATH}"
download_model_file "${MODEL_MED_PATH}" "${MODEL_MED_URL}" "${VENDORED_MODEL_MED_PATH}"
download_model_file "${MODEL_LARGE_PATH}" "${MODEL_LARGE_URL}" "${VENDORED_MODEL_LARGE_PATH}"

if [[ ! -f "${MODEL_LARGE_PATH}" ]]; then
  echo "Large whisper model is required for the audio runtime default path." >&2
  echo "Set AOR_AUDIO_TRANSCRIBER_MODEL_PATH to a valid model path and retry setup."
  exit 1
fi

cat <<EOF
Audio transcriber setup complete.
whisper-cli: ${WHISPER_CLI_PATH}
ffmpeg: $(command -v ffmpeg)
ffprobe: $(command -v ffprobe)
model path: ${MODEL_LARGE_PATH}
medium model path: ${MODEL_MED_PATH}
base model status: $(if [[ -f "${MODEL_BASE_PATH}" ]]; then echo ready; else echo missing; fi)
medium model status: $(if [[ -f "${MODEL_MED_PATH}" ]]; then echo ready; else echo missing; fi)
large model status: $(if [[ -f "${MODEL_LARGE_PATH}" ]]; then echo ready; else echo missing; fi)

Suggested environment:
  export AOR_AUDIO_TRANSCRIBER_BINARY="${WHISPER_CLI_PATH}"
  export AOR_AUDIO_TRANSCRIBER_MODEL_PATH="${MODEL_LARGE_PATH}"
EOF
