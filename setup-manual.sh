#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
MIN_PYTHON_VERSION="3.11"
PYTHON_BIN="${PYTHON_BIN:-}"

have_command() {
  command -v "$1" >/dev/null 2>&1
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
    if ! have_command "${candidate}"; then
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

cd "${REPO_ROOT}"

if ! PYTHON_BIN="$(choose_python_bin)"; then
  echo "Python ${MIN_PYTHON_VERSION} or newer is required." >&2
  echo "Install Python ${MIN_PYTHON_VERSION}+ or rerun with PYTHON_BIN=/path/to/python." >&2
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
  if ! version_gte "${VENV_PYTHON_VERSION}" "${MIN_PYTHON_VERSION}"; then
    echo "Recreating .venv because it uses Python ${VENV_PYTHON_VERSION}."
    rm -rf .venv
    "${PYTHON_BIN}" -m venv .venv
  fi
fi

source .venv/bin/activate

python -m pip install -U pip setuptools wheel
python -m pip install -e ".[manual]"

chmod +x setup-manual.sh startmanual.sh

echo "Manual setup complete."
echo "Python interpreter: $(command -v "${PYTHON_BIN}") (${PYTHON_VERSION})"
echo "Manual launcher: ${REPO_ROOT}/startmanual.sh"
echo "Manual URL: http://${AOR_MANUAL_HOST:-127.0.0.1}:${AOR_MANUAL_PORT:-8013}/manual"

