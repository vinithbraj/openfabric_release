#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
TOOLS_ROOT="${AOR_AUDIO_TOOLS_ROOT:-${REPO_ROOT}/.aor/audio-transcriber}"
WHISPER_CPP_DIR="${TOOLS_ROOT}/whisper.cpp"
WHISPER_BIN_DIR="${TOOLS_ROOT}/bin"
MODEL_DIR="${AOR_AUDIO_TRANSCRIBER_MODEL_DIR:-${REPO_ROOT}/artifacts/models/whisper}"
MODEL_LARGE_PATH="${AOR_AUDIO_TRANSCRIBER_MODEL_PATH:-${MODEL_DIR}/ggml-large-v3.bin}"
MODEL_LARGE_URL="${AOR_AUDIO_TRANSCRIBER_MODEL_URL:-https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin}"
VENDORED_ROOT="${AOR_AUDIO_TRANSCRIBER_VENDOR_ROOT:-${REPO_ROOT}/vendor/audio-transcriber}"
VENDORED_MODEL_LARGE_PATH="${AOR_AUDIO_TRANSCRIBER_VENDOR_LARGE_MODEL_PATH:-${VENDORED_ROOT}/models/ggml-large-v3.bin}"
WHISPER_GPU_BIN_NAME="${AOR_AUDIO_TRANSCRIBER_GPU_BINARY_NAME:-whisper-gpu-cli}"
WHISPER_GPU_CLI_PATH="${WHISPER_BIN_DIR}/${WHISPER_GPU_BIN_NAME}"
INSTALL_WHISPER="${AOR_INSTALL_WHISPER_CPP:-auto}"
INSTALL_SYSTEM_DEPS="${AOR_INSTALL_SYSTEM_DEPS:-1}"
DOWNLOAD_MODEL="${AOR_DOWNLOAD_WHISPER_MODEL:-1}"

mkdir -p "${WHISPER_BIN_DIR}" "${MODEL_DIR}"

have_command() {
  command -v "$1" >/dev/null 2>&1
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
    sudocmd apt-get install -y git cmake build-essential ffmpeg curl ca-certificates nvidia-cuda-toolkit
    return 0
  fi

  if have_command dnf; then
    sudocmd dnf install -y git cmake gcc gcc-c++ make ffmpeg curl ca-certificates cuda-toolkit
    return 0
  fi

  if have_command yum; then
    sudocmd yum install -y git cmake gcc gcc-c++ make ffmpeg curl ca-certificates cuda
    return 0
  fi

  if have_command pacman; then
    sudocmd pacman -Sy --needed --noconfirm git cmake base-devel ffmpeg curl ca-certificates
    sudocmd pacman -S --needed --noconfirm cuda
    return 0
  fi

  if have_command zypper; then
    sudocmd zypper --non-interactive install git cmake gcc gcc-c++ make ffmpeg curl ca-certificates cuda
    return 0
  fi

  if have_command brew; then
    echo "Homebrew CUDA installs are typically GPU-specific and may require manual setup."
    echo "brew install cmake ffmpeg curl"
    return 0
  fi

  return 0
}

collect_missing() {
  local missing=()
  local tool=""

  for tool in git cmake ffmpeg ffprobe; do
    if ! have_command "${tool}"; then
      missing+=("${tool}")
    fi
  done

  if [[ "${INSTALL_WHISPER}" != "0" ]]; then
    if ! have_command nvcc; then
      missing+=("nvcc")
    fi
  fi

  if ! is_disabled "${DOWNLOAD_MODEL}" && ! have_command curl; then
    missing+=("curl")
  fi

  printf '%s\n' "${missing[@]}"
}

check_cuda_toolchain() {
  if ! have_command nvcc; then
    echo "nvcc is not available. Install a CUDA toolkit before building GPU whisper-cli."
    return 1
  fi

  if have_command nvidia-smi; then
    if ! nvidia-smi -L >/dev/null 2>&1; then
      echo "nvidia-smi is available but could not query devices. Check your NVIDIA driver installation." >&2
      return 1
    fi
  else
    echo "nvidia-smi not found. The build may still work if a CUDA toolkit and driver are already configured."
  fi
}

collect_missing_gpu_build_requirements() {
  local missing=()

  collect_missing | while IFS= read -r item; do
    [[ -n "${item}" ]] && missing+=("${item}")
  done

  if ! is_disabled "${INSTALL_WHISPER}" && ! check_cuda_toolchain; then
    missing+=("CUDA toolkit/runtime (check nvidia-smi or nvcc)")
  fi

  printf '%s\n' "${missing[@]}"
}

missing=()
while IFS= read -r tool; do
  [[ -n "${tool}" ]] && missing+=("${tool}")
done < <(collect_missing_gpu_build_requirements)

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Installing prerequisites for GPU whisper setup: ${missing[*]}"
  if ! install_system_deps; then
    echo "Automatic dependency installation failed." >&2
  fi

  missing=()
  while IFS= read -r tool; do
    [[ -n "${tool}" ]] && missing+=("${tool}")
  done < <(collect_missing)

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "Audio transcriber GPU prerequisites are still missing: ${missing[*]}" >&2
    echo "Install them with your OS package manager, then rerun this script." >&2
    echo "Ubuntu example: sudo apt-get install -y git cmake build-essential ffmpeg curl ca-certificates nvidia-cuda-toolkit" >&2
    echo "macOS example: install NVIDIA runtime/CUDA separately, then install cmake and ffmpeg with brew." >&2
    echo "Set AOR_INSTALL_SYSTEM_DEPS=0 to skip automatic package installation." >&2
    echo "Set AOR_AUDIO_TRANSCRIBER_USE_GPU=1 and point AOR_AUDIO_TRANSCRIBER_BINARY to ${WHISPER_GPU_CLI_PATH} to use this build." >&2
    exit 1
  fi
fi

if [[ "${INSTALL_WHISPER}" != "0" ]]; then
  if [[ ! -d "${WHISPER_CPP_DIR}/.git" ]]; then
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "${WHISPER_CPP_DIR}"
  fi
  cmake -S "${WHISPER_CPP_DIR}" -B "${WHISPER_CPP_DIR}/build-gpu" \
    -DWHISPER_BUILD_TESTS=OFF \
    -DWHISPER_BUILD_EXAMPLES=ON \
    -DGGML_CUDA=ON \
    -DWHISPER_CUDA=ON \
    -DCMAKE_BUILD_TYPE=Release
  cmake --build "${WHISPER_CPP_DIR}/build-gpu" --config Release -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
  BUILT_WHISPER_CLI="$(find "${WHISPER_CPP_DIR}/build-gpu" -type f -name whisper-cli -executable | head -n 1)"
  if [[ -z "${BUILT_WHISPER_CLI}" ]]; then
    echo "whisper.cpp built, but whisper-cli was not found." >&2
    exit 1
  fi
  cp "${BUILT_WHISPER_CLI}" "${WHISPER_GPU_CLI_PATH}"
  chmod +x "${WHISPER_GPU_CLI_PATH}"
else
  echo "Build is disabled via AOR_INSTALL_WHISPER_CPP=0; checking for existing whisper-gpu-cli."
fi

if [[ ! -x "${WHISPER_GPU_CLI_PATH}" ]]; then
  echo "GPU whisper CLI not found. Set AOR_INSTALL_WHISPER_CPP=1 and rerun this script." >&2
  exit 1
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

download_model_file "${MODEL_LARGE_PATH}" "${MODEL_LARGE_URL}" "${VENDORED_MODEL_LARGE_PATH}"

if [[ ! -f "${MODEL_LARGE_PATH}" ]]; then
  echo "Large whisper model is required for the audio runtime default path." >&2
  echo "Set AOR_AUDIO_TRANSCRIBER_MODEL_PATH to a valid model path and retry setup."
  exit 1
fi

echo "Verifying GPU whisper-cli capabilities..."
if "${WHISPER_GPU_CLI_PATH}" --help >/tmp/aor-whisper-help.txt 2>&1; then
  :
else
  echo "Built whisper-cli returned a non-zero status for --help. Still proceeding, but please verify binary compatibility."
fi

if ! grep -Ei "cuda|gpu" /tmp/aor-whisper-help.txt | head -n 1 >/dev/null 2>&1; then
  echo "Warning: whisper-cli help output did not include explicit CUDA/GPU flags." >&2
  echo "It may still use CUDA by default when available. If this is unexpected, rebuild with a recent whisper.cpp."
else
  echo "whisper-cli GPU symbols detected in help output."
fi
rm -f /tmp/aor-whisper-help.txt

cat <<EOF
Audio transcriber GPU setup complete.
whisper-gpu-cli: ${WHISPER_GPU_CLI_PATH}
ffmpeg: $(command -v ffmpeg || echo unavailable)
ffprobe: $(command -v ffprobe || echo unavailable)
model path: ${MODEL_LARGE_PATH}
large model status: $(if [[ -f "${MODEL_LARGE_PATH}" ]]; then echo ready; else echo missing; fi)

To use GPU transcription, set:
  export AOR_AUDIO_TRANSCRIBER_USE_GPU="1"
  export AOR_AUDIO_TRANSCRIBER_BINARY="${WHISPER_GPU_CLI_PATH}"
EOF
