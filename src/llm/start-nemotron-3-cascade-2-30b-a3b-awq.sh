#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export HF_HOME="${HF_HOME:-$HOME/models/data}"

# Experimental 4-bit AWQ quantization of NVIDIA Nemotron 3 Nano 30B-A3B.
# This is the practical local profile for a single RTX 3090. If it OOMs,
# reduce --max-model-len first.

# python -m vllm.entrypoints.openai.api_server \
#  --model stelterlab/NVIDIA-Nemotron-3-Nano-30B-A3B-AWQ \
#  --served-model-name stelterlab/NVIDIA-Nemotron-3-Nano-30B-A3B-AWQ \
#  --tensor-parallel-size 1 \
#  --gpu-memory-utilization 0.9 \
#  --max-model-len 32768 \
#  --max-num-seqs 1 \
#  --dtype auto \
#  --trust-remote-code \
#  --enable-auto-tool-choice \
#  --tool-call-parser qwen3_coder \
#  --reasoning-parser-plugin "$SCRIPT_DIR/nano_v3_reasoning_parser.py" \
#  --reasoning-parser nano_v3 \
#  --no-enable-log-requests \
#  --port 8000


python -m vllm.entrypoints.openai.api_server \
  --model stelterlab/Nemotron-Cascade-2-30B-A3B-AWQ \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95 \
  --max-model-len 32000 \
  --enable-chunked-prefill \
  --max-num-seqs 1 \
  --trust-remote-code \
  --port 8000
