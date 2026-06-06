#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export HF_HOME="${HF_HOME:-$HOME/models/data}"

# NVIDIA Nemotron 3 Nano BF16 MoE profile.
# Download the Nano v3 reasoning parser once before first launch:
# wget -O "$SCRIPT_DIR/nano_v3_reasoning_parser.py" \
#  https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16/resolve/main/nano_v3_reasoning_parser.py
# This BF16 model is too large to fit fully on a single RTX 3090. These defaults
# use CPU offload and a small context as a slow "can it boot" profile.

python -m vllm.entrypoints.openai.api_server \
  --model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16 \
  --served-model-name nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16 \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95 \
  --max-model-len 4096 \
  --max-num-seqs 1 \
  --dtype bfloat16 \
  --cpu-offload-gb 2 \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser-plugin "$SCRIPT_DIR/nano_v3_reasoning_parser.py" \
  --reasoning-parser nano_v3 \
  --no-enable-log-requests \
  --port 8000
