#!/usr/bin/env bash
set -euo pipefail

export HF_HOME="${HF_HOME:-$HOME/models/data}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PATCH_DIR="$SCRIPT_DIR/vllm_qwen3next_patch"
export PYTHONPATH="$PATCH_DIR${PYTHONPATH:+:$PYTHONPATH}"
export QWEN3NEXT_CPU_OFFLOAD_REINIT_PATCH="${QWEN3NEXT_CPU_OFFLOAD_REINIT_PATCH:-1}"

if [[ -z "${PYTHON_BIN:-}" && -x "$HOME/miniconda3/envs/vllm/bin/python" ]]; then
  PYTHON_BIN="$HOME/miniconda3/envs/vllm/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi

# Qwen3-Coder-Next 80B-A3B 4-bit AWQ profile for a single 24 GB GPU with
# system-RAM offload. These defaults target a 32k context on an RTX 3090 plus
# high system RAM. Eager mode avoids CUDA graph profiling with CPU offload. The
# repo-local vLLM shim above allows Qwen3-Next's hybrid KV-cache layout to rebuild
# the input batch after the model is loaded, which vLLM 0.19.0 otherwise blocks
# when UVA CPU weight offload is active. The explicit block size matches this
# model's resolved attention page size, and Triton attention keeps the kernel
# block size aligned with that cache block.
MODEL_ID="${MODEL_ID:-cyankiwi/Qwen3-Coder-Next-AWQ-4bit}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"
ATTENTION_BACKEND="${ATTENTION_BACKEND:-TRITON_ATTN}"
GDN_PREFILL_BACKEND="${GDN_PREFILL_BACKEND:-triton}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
BLOCK_SIZE="${BLOCK_SIZE:-544}"
CPU_OFFLOAD_GB="${CPU_OFFLOAD_GB:-48}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-1}"

"$PYTHON_BIN" -m vllm.entrypoints.openai.api_server \
  --model "$MODEL_ID" \
  --host "$HOST" \
  --port "$PORT" \
  --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
  --attention-backend "$ATTENTION_BACKEND" \
  --gdn-prefill-backend "$GDN_PREFILL_BACKEND" \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --max-model-len "$MAX_MODEL_LEN" \
  --block-size "$BLOCK_SIZE" \
  --cpu-offload-gb "$CPU_OFFLOAD_GB" \
  --max-num-seqs "$MAX_NUM_SEQS" \
  --enable-chunked-prefill \
  --trust-remote-code \
  --no-enable-log-requests \
  --enforce-eager \
  "$@"
