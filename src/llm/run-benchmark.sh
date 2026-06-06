#!/usr/bin/env bash
set -euo pipefail

CONDA_HOME="${CONDA_HOME:-$HOME/miniconda3}"
CONDA_ENV="${CONDA_ENV:-vllm}"

# shellcheck source=/dev/null
source "$CONDA_HOME/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

python "$(dirname "$0")/benchmark_vllm_models.py" "$@"
