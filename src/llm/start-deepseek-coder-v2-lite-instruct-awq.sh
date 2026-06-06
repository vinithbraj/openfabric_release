#!/usr/bin/env bash
set -euo pipefail

export HF_HOME="${HF_HOME:-$HOME/models/data}"

MODEL_ID="${MODEL_ID:-TechxGenus/DeepSeek-Coder-V2-Lite-Instruct-AWQ}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.7}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-18576}"

python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL_ID" \
  --host "$HOST" \
  --port "$PORT" \
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --max-model-len "$MAX_MODEL_LEN" \
  --trust-remote-code \
  --no-enable-log-requests \
  "$@"
