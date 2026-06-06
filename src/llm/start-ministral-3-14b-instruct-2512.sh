#!/usr/bin/env bash
set -euo pipefail

export HF_HOME="${HF_HOME:-$HOME/models/data}"

MODEL_ID="${MODEL_ID:-mistralai/Ministral-3-14B-Instruct-2512}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.9}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-12288}"
ENFORCE_EAGER="${ENFORCE_EAGER:-1}"

EXTRA_ARGS=()
if [[ "$ENFORCE_EAGER" == "1" || "$ENFORCE_EAGER" == "true" ]]; then
  EXTRA_ARGS+=(--enforce-eager)
fi

python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL_ID" \
  --host "$HOST" \
  --port "$PORT" \
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --max-model-len "$MAX_MODEL_LEN" \
  --tokenizer-mode mistral \
  --config-format mistral \
  --load-format mistral \
  --tool-call-parser mistral \
  --enable-auto-tool-choice \
  --no-enable-log-requests \
  "${EXTRA_ARGS[@]}" \
  "$@"
