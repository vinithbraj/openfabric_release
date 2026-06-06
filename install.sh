#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"

PYTHON_BIN="${PYTHON_BIN:-}"
LAUNCH_AFTER_INSTALL=0
MIN_PYTHON_VERSION="3.11"
AUDIO_SETUP_SCRIPT="${AOR_AUDIO_TRANSCRIBER_SETUP_SCRIPT:-./setup-audio-transcriber.sh}"

have_command() {
  command -v "$1" >/dev/null 2>&1
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
  local packages=()

  if [[ "${AOR_SKIP_SYSTEM_DEPS:-0}" =~ ^(1|true|yes|on)$ ]] || [[ "${AOR_INSTALL_SYSTEM_DEPS:-1}" =~ ^(0|false|no|off)$ ]]; then
    return 0
  fi

  if have_command apt-get; then
    packages=(
      ca-certificates
      curl
      ffmpeg
      git
      openssl
      python3
      python3-dev
      python3-pip
      python3-venv
    )
    if [[ ! "${AOR_INSTALL_NO_BUILD:-0}" =~ ^(1|true|yes|on)$ ]]; then
      packages+=(build-essential cmake)
    fi
    sudocmd apt-get update
    sudocmd apt-get install -y "${packages[@]}"
    return 0
  fi

  if have_command dnf; then
    packages=(
      ca-certificates \
      cmake \
      curl \
      ffmpeg \
      gcc \
      gcc-c++ \
      git \
      make \
      openssl \
      python3 \
      python3-devel \
      python3-pip
    )
    if [[ "${AOR_INSTALL_NO_BUILD:-0}" =~ ^(1|true|yes|on)$ ]]; then
      packages=(ca-certificates curl ffmpeg git openssl python3 python3-devel python3-pip)
    fi
    sudocmd dnf install -y "${packages[@]}"
    return 0
  fi

  if have_command yum; then
    packages=(
      ca-certificates \
      cmake \
      curl \
      ffmpeg \
      gcc \
      gcc-c++ \
      git \
      make \
      openssl \
      python3 \
      python3-devel \
      python3-pip
    )
    if [[ "${AOR_INSTALL_NO_BUILD:-0}" =~ ^(1|true|yes|on)$ ]]; then
      packages=(ca-certificates curl ffmpeg git openssl python3 python3-devel python3-pip)
    fi
    sudocmd yum install -y "${packages[@]}"
    return 0
  fi

  if have_command pacman; then
    packages=(
      base-devel \
      ca-certificates \
      cmake \
      curl \
      ffmpeg \
      git \
      openssl \
      python \
      python-pip \
      python-virtualenv
    )
    if [[ "${AOR_INSTALL_NO_BUILD:-0}" =~ ^(1|true|yes|on)$ ]]; then
      packages=(ca-certificates curl ffmpeg git openssl python python-pip python-virtualenv)
    fi
    sudocmd pacman -Sy --needed --noconfirm "${packages[@]}"
    return 0
  fi

  if have_command zypper; then
    packages=(
      ca-certificates \
      cmake \
      curl \
      ffmpeg \
      gcc \
      gcc-c++ \
      git \
      make \
      openssl \
      python3 \
      python3-devel \
      python3-pip
    )
    if [[ "${AOR_INSTALL_NO_BUILD:-0}" =~ ^(1|true|yes|on)$ ]]; then
      packages=(ca-certificates curl ffmpeg git openssl python3 python3-devel python3-pip)
    fi
    sudocmd zypper --non-interactive install "${packages[@]}"
    return 0
  fi

  if have_command brew; then
    packages=(python git ffmpeg curl openssl)
    if [[ ! "${AOR_INSTALL_NO_BUILD:-0}" =~ ^(1|true|yes|on)$ ]]; then
      packages+=(cmake)
    fi
    brew install "${packages[@]}"
    return 0
  fi

  echo "No supported system package manager found; continuing with existing tools." >&2
  return 0
}

version_gte() {
  local lhs="$1"
  local rhs="$2"
  local lhs_major="${lhs%%.*}"
  local lhs_minor="${lhs#*.}"
  local rhs_major="${rhs%%.*}"
  local rhs_minor="${rhs#*.}"
  lhs_minor="${lhs_minor%%.*}"
  rhs_minor="${rhs_minor%%.*}"

  (( lhs_major > rhs_major || (lhs_major == rhs_major && lhs_minor >= rhs_minor) ))
}

python_version_of() {
  local python_bin="$1"
  "${python_bin}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

choose_python_bin() {
  local candidates=()
  local candidate=""
  local version=""

  if [[ -n "${PYTHON_BIN}" ]]; then
    candidates+=("${PYTHON_BIN}")
  else
    candidates+=(python3.13 python3.12 python3.11 python3)
  fi

  for candidate in "${candidates[@]}"; do
    if ! command -v "${candidate}" >/dev/null 2>&1; then
      continue
    fi

    version="$(python_version_of "${candidate}")"
    if version_gte "${version}" "${MIN_PYTHON_VERSION}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --launch)
      LAUNCH_AFTER_INSTALL=1
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--launch]" >&2
      exit 1
      ;;
  esac
done

cd "${REPO_ROOT}"

install_system_deps

if ! PYTHON_BIN="$(choose_python_bin)"; then
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    echo "Python ${MIN_PYTHON_VERSION} or newer is required, but ${PYTHON_BIN} is unavailable or too old." >&2
  else
    echo "Python ${MIN_PYTHON_VERSION} or newer is required, but no compatible interpreter was found." >&2
  fi
  echo "Install Python ${MIN_PYTHON_VERSION}+ and rerun with, for example:" >&2
  echo "  PYTHON_BIN=python3.11 ./install.sh" >&2
  exit 1
fi

PYTHON_VERSION="$(python_version_of "${PYTHON_BIN}")"

if [[ ! -d ".venv" ]]; then
  "${PYTHON_BIN}" -m venv .venv
elif [[ ! -x ".venv/bin/python" ]]; then
  rm -rf .venv
  "${PYTHON_BIN}" -m venv .venv
else
  VENV_PYTHON_VERSION="$(python_version_of ".venv/bin/python")"
  if ! version_gte "${VENV_PYTHON_VERSION}" "${MIN_PYTHON_VERSION}" || [[ "${VENV_PYTHON_VERSION}" != "${PYTHON_VERSION}" ]]; then
    echo "Recreating .venv with ${PYTHON_BIN} (${PYTHON_VERSION}); existing environment uses Python ${VENV_PYTHON_VERSION}."
    rm -rf .venv
    "${PYTHON_BIN}" -m venv .venv
  fi
fi

source .venv/bin/activate

python -m pip install -U pip setuptools wheel
python -m pip install -e ".[python-tools]"
python -m playwright install chromium

chmod +x \
  startup.sh \
  startup-audio.sh \
  setup-manual.sh \
  startmanual.sh \
  startwebsite.sh \
  setupplaywright.sh \
  setup-audio-transcriber.sh \
  setup-audio-transcriber-no-build.sh \
  install-no-build.sh \
  scripts/build-whisper-cli.sh \
  src/gateway_agent/startup.sh \
  src/gateway_macos/startup.sh

if [[ "${AOR_SKIP_AUDIO_TRANSCRIBER_SETUP:-0}" =~ ^(1|true|yes|on)$ ]]; then
  echo "Skipping audio transcriber setup because AOR_SKIP_AUDIO_TRANSCRIBER_SETUP is set."
else
  echo "Checking local audio transcriber setup..."
  if [[ "${AUDIO_SETUP_SCRIPT}" != /* ]]; then
    AUDIO_SETUP_SCRIPT="${REPO_ROOT}/${AUDIO_SETUP_SCRIPT#./}"
  fi
  if [[ ! -x "${AUDIO_SETUP_SCRIPT}" ]]; then
    echo "Audio setup script is missing or not executable: ${AUDIO_SETUP_SCRIPT}" >&2
    exit 1
  fi
  if ! "${AUDIO_SETUP_SCRIPT}"; then
    echo "Audio transcriber setup did not complete." >&2
    echo "The server will still run, but voice dictation will stay unavailable until ffmpeg, ffprobe, whisper-cli, and a Whisper model are configured." >&2
    if [[ "${AOR_REQUIRE_AUDIO_TRANSCRIBER_SETUP:-0}" =~ ^(1|true|yes|on)$ ]]; then
      exit 1
    fi
  fi
fi

echo "Install complete."
echo "Python interpreter: $(command -v "${PYTHON_BIN}") (${PYTHON_VERSION})"
echo "Virtual environment: ${REPO_ROOT}/.venv"
echo "Agent launcher: ${REPO_ROOT}/startup.sh"
echo "Audio runtime launcher: ${REPO_ROOT}/startup-audio.sh"
echo "Manual setup: ${REPO_ROOT}/setup-manual.sh"
echo "Manual launcher: ${REPO_ROOT}/startmanual.sh"
echo "Website launcher: ${REPO_ROOT}/startwebsite.sh"
echo "Playwright setup: ${REPO_ROOT}/setupplaywright.sh"
echo "Gateway launcher: ${REPO_ROOT}/src/gateway_agent/startup.sh"
echo "Audio transcriber setup: ${AUDIO_SETUP_SCRIPT}"

if [[ "${LAUNCH_AFTER_INSTALL}" == "1" ]]; then
  exec ./startup.sh
fi
