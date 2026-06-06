# Local vLLM launch scripts

These scripts are quick launch profiles for trying OpenAI-compatible vLLM
servers on a single RTX 3090 24 GB GPU.

Common overrides:

```bash
PORT=8001 MAX_MODEL_LEN=16384 ./src/llm/start-qwen2.5-coder-32b-awq.sh
GPU_MEMORY_UTILIZATION=0.86 ./src/llm/start-qwen3-coder-30b-a3b-awq.sh
CPU_OFFLOAD_GB=48 MAX_MODEL_LEN=32768 ./src/llm/start-cyankiwi-qwen3-coder-next-awq-4bit-offload.sh
```

The Agent UI expects an OpenAI-compatible base URL such as:

```text
http://127.0.0.1:8000/v1
```

The runtime discovers model metadata from the OpenAI-compatible server when it
can. Context-window estimates should follow the active model/server metadata
instead of assuming one hard-coded value. If model metadata is unavailable, the
runtime falls back to configured defaults such as
`AOR_LLM_CONTEXT_WINDOW_TOKENS`.

The Agent UI Settings drawer also includes an xterm-backed launch terminal. It
can use the configured conda environment and working directory, then run one of
these scripts so model-loading logs stay visible in the browser.

Default launch-terminal values:

```text
conda env: vllm
command: ./src/llm/start-current-qwen3-coder-30b-a3b-awq.sh
```

You can override those defaults with:

```bash
AOR_AGENT_UI_LLM_LAUNCH_CONDA_ENV=vllm
AOR_AGENT_UI_LLM_LAUNCH_COMMAND=./src/llm/start-current-qwen3-coder-30b-a3b-awq.sh
AOR_AGENT_UI_LLM_LAUNCH_CWD=/path/to/openfabric
```

## Suggested order to try

1. `start-current-qwen3-coder-30b-a3b-awq.sh`
   - Exact copy of the current known-good launch profile you use today.
2. `start-qwen3-coder-30b-a3b-awq.sh`
   - Best first candidate for this agent. MoE coder model, strong agentic coding,
     only a small active parameter count per token, and configurable by env vars.
3. `start-qwen2.5-coder-14b-awq.sh`
   - Fast, stable dense code model. Good baseline when the MoE model is flaky.
4. `start-qwen2.5-coder-7b-awq.sh`
   - Smaller dense code model. Useful when startup speed and low VRAM pressure
     matter more than deep planning quality.
5. `start-qwen2.5-coder-32b-awq.sh`
   - Strong dense code model. More memory pressure, so the default context is
     lower.
6. `start-deepseek-coder-v2-lite-instruct-awq.sh`
   - DeepSeek coder MoE-lite alternative for command/code planning.
7. `start-yi-coder-9b-chat-awq.sh`
   - Compact coder chat alternative.
8. `start-qwen3-14b-awq.sh`
   - General instruct/reasoning model. Useful for non-code planning and
     structured reasoning.
9. `start-qwen3-8b-awq.sh`
   - Very fast small model for UI/debug loops.
10. `start-meta-llama-3-8b-instruct-awq.sh`
   - Compact Llama 3 instruct AWQ baseline. Defaults to the model's 8k context
     window.
11. `start-qwen3-30b-a3b-instruct-awq.sh`
   - General-purpose MoE instruct sibling to the coder model.
12. `start-qwen3-30b-a3b-thinking-awq.sh`
   - Reasoning-oriented Qwen3 30B-A3B MoE candidate. Defaults to a shorter
     context than the non-thinking sibling to leave VRAM headroom.
13. `start-deepseek-v2-lite-chat-awq.sh`
   - DeepSeek V2 Lite chat MoE candidate. Uses remote code and the same
     OpenAI-compatible vLLM serving flow.
14. `start-devstral-small-2507-awq.sh`
   - Experimental agentic coding alternative. Uses Mistral/vLLM-specific flags.
15. `start-mistral-small-24b-instruct-2501-awq.sh`
   - AWQ-quantized Mistral Small 3 instruct candidate. Defaults below the full
     32k context window to leave a little more 24 GB GPU headroom.
16. `start-glm-4.5-air-awq.sh`
   - GLM Air MoE instruct alternative. Uses a shorter default context to fit
     a 24 GB card more comfortably.
17. `start-ministral-3-8b-instruct-2512.sh`
   - Fast Mistral 3 instruct candidate with official vLLM/Mistral-format flags.
     Defaults to eager execution and a shorter context because the compile path
     can be slow or fragile on a 3090.
18. `start-ministral-3-14b-instruct-2512.sh`
   - Strongest promising Mistral instruct candidate for a 24 GB card if FP8
     serving works on the local CUDA stack. Defaults to eager execution.
19. `start-devstral-small-2-24b-instruct-2512.sh`
   - Mistral's newer agentic coding model; likely memory/runtime sensitive on
     an RTX 3090.
20. `start-mistral-small-3.2-24b-instruct-2506.sh`
   - Strong Mistral general instruct model, but the official card notes much
     higher GPU RAM needs for non-compressed serving.
21. `start-mistral-nemo-instruct-2407.sh`
   - Older 12B Mistral/NVIDIA instruct baseline. Defaults to 8k context because
     uncompressed BF16 nearly fills a 24 GB card at 32k.
22. `start-nemotron-3-nano-30b-a3b-bf16.sh`
   - NVIDIA Nemotron 3 Nano BF16 MoE candidate. Uses remote code, local Nano v3
     reasoning parser file, and Qwen3 Coder tool parsing. This profile is not
     expected to fit a 24 GB card with 30 GB system RAM; keep it as a
     multi-GPU/high-memory reference.
23. `start-nemotron-3-nano-30b-a3b-awq.sh`
   - Community 4-bit AWQ quantization of Nemotron 3 Nano. This is the practical
     local vLLM profile to try on a single RTX 3090.
24. `start-nemotron-3-cascade-2-30b-a3b-awq.sh`
   - Experimental Nemotron Cascade AWQ profile. Some hybrid/Mamba KV-cache
     layouts may require a newer or compatible vLLM build.
25. `start-qwen3-next-80b-a3b-instruct-awq-4bit.sh`
   - Large Qwen3-Next AWQ candidate. Download size, vLLM version support, and
     memory pressure are expected to be the main constraints.
26. `start-cyankiwi-qwen3-coder-next-awq-4bit-offload.sh`
   - Qwen3-Coder-Next 80B-A3B 4-bit AWQ candidate with CPU offload defaults
     for a 32k context on a single RTX 3090 plus high system RAM. This script
     auto-uses `$HOME/miniconda3/envs/vllm/bin/python` when present so it can be
     launched from a plain shell.
   - Loads a repo-local `sitecustomize.py` shim for vLLM 0.19.0. Qwen3-Next's
     hybrid attention/Mamba cache layout needs vLLM to rebuild the input batch
     after model load, but this vLLM build blocks that rebuild when UVA CPU
     weight offload is active. The shim allows that rebuild while preserving the
     configured CPU offload amount.
   - The shim is local to this launcher, not a global vLLM patch. It is useful
     for the specific `CPU offload + input-batch reinitialize blocked` failure
     path, but it is not a generic fix for OOMs, missing kernels, unsupported
     quantization, backend bugs, or slow CPU offload.
   - Tested live on port `18081`: `/v1/models` returned HTTP 200 after about
     320 seconds with `MAX_MODEL_LEN=32768`, `CPU_OFFLOAD_GB=48`,
     `--block-size 544`, `TRITON_ATTN`, and `--enforce-eager`.

If a model OOMs, reduce `MAX_MODEL_LEN` first, then lower
`GPU_MEMORY_UTILIZATION`.

## Benchmarking candidates

Run the shared prompt suite from the `vllm` conda environment:

```bash
./src/llm/run-benchmark.sh --models qwen3-8b-awq --prompt-limit 3
./src/llm/run-benchmark.sh --models all
```

The benchmark launches each selected vLLM profile on port `18000`, waits for
`/v1/models`, runs the same agent-style prompt suite, and writes JSON plus
Markdown reports under `src/llm/results/`.

To test an already-running server instead of launching one:

```bash
./src/llm/run-benchmark.sh --no-launch --base-url http://127.0.0.1:8000/v1 --models qwen3-coder-30b-a3b-awq
```

Benchmark reports are written to `src/llm/results/`. Those reports are local
artifacts and are not required by the runtime.

## Notes for 24 GB GPUs

- Reduce `MAX_MODEL_LEN` first when a model OOMs.
- Lower `GPU_MEMORY_UTILIZATION` if startup is unstable.
- Very large context windows can make prefill slower and reduce concurrency.
- `--enforce-eager` can avoid some compile issues, but it may reduce runtime
  performance because vLLM cannot use its optimized graph path.
- Chunked prefill can help responsiveness on long prompts, but it is still
  workload and model dependent.
- CPU offload uses system RAM. Setting it above available RAM can crash the
  terminal or process; setting it too low can still OOM on GPU.
- Tool-call parsers and reasoning parsers are vLLM/model-specific. Remove or
  change parser flags when trying a model family that does not support the
  selected parser.
