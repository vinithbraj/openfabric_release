#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

PYTHON_BIN="${PYTHON_BIN:-}"
MIN_PYTHON_VERSION="3.11"

have_command() {
  command -v "$1" >/dev/null 2>&1
}

sudocmd() {
  if [[ "$(id -u)" == "0" ]]; then
    "$@"
  elif have_command sudo; then
    sudo "$@"
  else
    return 1
  fi
}

install_system_deps() {
  if [[ "${AOR_SKIP_SYSTEM_DEPS:-0}" =~ ^(1|true|yes|on)$ ]] || [[ "${GATEWAY_SKIP_SYSTEM_DEPS:-0}" =~ ^(1|true|yes|on)$ ]]; then
    return 0
  fi

  if have_command apt-get; then
    sudocmd apt-get update
    sudocmd apt-get install -y ca-certificates curl git python3 python3-dev python3-pip python3-venv
    return 0
  fi

  if have_command dnf; then
    sudocmd dnf install -y ca-certificates curl git python3 python3-devel python3-pip
    return 0
  fi

  if have_command yum; then
    sudocmd yum install -y ca-certificates curl git python3 python3-devel python3-pip
    return 0
  fi

  if have_command pacman; then
    sudocmd pacman -Sy --needed --noconfirm ca-certificates curl git python python-pip python-virtualenv
    return 0
  fi

  if have_command zypper; then
    sudocmd zypper --non-interactive install ca-certificates curl git python3 python3-devel python3-pip
    return 0
  fi

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

ensure_venv_writable() {
  if [[ -d "${VENV_DIR}" && ! -w "${VENV_DIR}" ]]; then
    echo "Gateway virtual environment exists but is not writable: ${VENV_DIR}" >&2
    echo "Re-run as the owning user, or remove it first and reinstall:" >&2
    echo "  rm -rf ${VENV_DIR}" >&2
    exit 1
  fi
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

install_system_deps

if ! PYTHON_BIN="$(choose_python_bin)"; then
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    echo "Python ${MIN_PYTHON_VERSION} or newer is required, but ${PYTHON_BIN} is unavailable or too old." >&2
  else
    echo "Python ${MIN_PYTHON_VERSION} or newer is required, but no compatible interpreter was found." >&2
  fi
  echo "Install Python ${MIN_PYTHON_VERSION}+ and rerun with, for example:" >&2
  echo "  PYTHON_BIN=python3.11 ./src/gateway_agent/install.sh" >&2
  exit 1
fi

PYTHON_VERSION="$(python_version_of "${PYTHON_BIN}")"

ensure_venv_writable

if [[ ! -d "${VENV_DIR}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
elif [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  ensure_venv_writable
  rm -rf "${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
else
  VENV_PYTHON_VERSION="$(python_version_of "${VENV_DIR}/bin/python")"
  if ! version_gte "${VENV_PYTHON_VERSION}" "${MIN_PYTHON_VERSION}" || [[ "${VENV_PYTHON_VERSION}" != "${PYTHON_VERSION}" ]]; then
    ensure_venv_writable
    echo "Recreating gateway .venv with ${PYTHON_BIN} (${PYTHON_VERSION}); existing environment uses Python ${VENV_PYTHON_VERSION}."
    rm -rf "${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  fi
fi

source "${VENV_DIR}/bin/activate"

cd "${REPO_ROOT}"

python -m pip install -U pip setuptools wheel
python -m pip install -e ".[python-tools]"

chmod +x src/gateway_agent/startup.sh

echo "Gateway install complete."
echo "Python interpreter: $(command -v python) (${PYTHON_VERSION})"
echo "Gateway virtual environment: ${VENV_DIR}"
echo "Gateway launcher: ${SCRIPT_DIR}/startup.sh"
