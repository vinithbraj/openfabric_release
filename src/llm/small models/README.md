# Small model vLLM launch scripts

These profiles are intended for quick agent testing on small GPUs such as an
RTX 3060 with 8 GB VRAM. They expose the same OpenAI-compatible vLLM server as
the larger launch scripts in `src/llm`.

Recommended first run:

```bash
./src/llm/small\ models/start-qwen2.5-coder-3b-awq.sh
```

General instruct/tool-use alternative:

```bash
./src/llm/small\ models/start-qwen3-4b-instruct-2507-awq.sh
```

Both scripts default to:

```text
HOST=127.0.0.1
PORT=8000
MAX_MODEL_LEN=8192
MAX_NUM_SEQS=1
GPU_MEMORY_UTILIZATION=0.86
```

The Agent UI base URL should be:

```text
http://127.0.0.1:8000/v1
```

If a profile has spare VRAM, raise `MAX_MODEL_LEN` first. If it fails to load,
lower `MAX_MODEL_LEN` or `GPU_MEMORY_UTILIZATION`.
