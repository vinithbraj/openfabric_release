"""LLM client wrapper for request profiling."""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from agent_runtime.llm.tracing import infer_stage_from_prompt
from agent_runtime.profiling import RuntimeProfiler, estimate_tokens, stable_json


class ProfilingLLMClient:
    """Wrap an LLM client and record structured-call timings."""

    def __init__(self, inner: Any, profiler: RuntimeProfiler) -> None:
        self.inner = inner
        self.profiler = profiler
        self.model = str(getattr(inner, "model", "unknown") or "unknown")
        self.temperature = getattr(inner, "temperature", None)

    def __getattr__(self, name: str) -> Any:
        """Delegate non-profile attributes to the wrapped client."""

        return getattr(self.inner, name)

    @staticmethod
    def _retry_hint(prompt: str) -> bool:
        """Infer whether one prompt is a corrective retry."""

        lowered = str(prompt or "").lower()
        if "previous" in lowered and "validation feedback" in lowered:
            return True
        match = re.search(r"candidate index:\s*(\d+)\s+of\s+(\d+)", lowered)
        if match:
            try:
                return int(match.group(1)) > 1 and int(match.group(2)) > 1
            except ValueError:
                return False
        return False

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Forward one LLM call and record latency, prompt size, and token estimates."""

        stage = infer_stage_from_prompt(prompt)
        schema_name = str(schema.get("title") or schema.get("$id") or "json_schema")
        prompt_chars = len(str(prompt or ""))
        schema_text = stable_json(schema)
        schema_chars = len(schema_text)
        input_tokens = estimate_tokens(prompt) + estimate_tokens(schema)
        prompt_hash = hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()
        retry_hint = self._retry_hint(prompt)
        started = time.perf_counter()
        try:
            response = self.inner.complete_json(prompt, schema)
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self.profiler.record_llm_call(
                stage=stage,
                schema_name=schema_name,
                model=self.model,
                prompt_chars=prompt_chars,
                schema_chars=schema_chars,
                input_tokens_estimate=input_tokens,
                output_tokens_estimate=None,
                duration_ms=duration_ms,
                status="error",
                error_type=type(exc).__name__,
                retry_hint=retry_hint,
                prompt_hash=prompt_hash,
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000.0
        output_tokens = estimate_tokens(response)
        self.profiler.record_llm_call(
            stage=stage,
            schema_name=schema_name,
            model=self.model,
            prompt_chars=prompt_chars,
            schema_chars=schema_chars,
            input_tokens_estimate=input_tokens,
            output_tokens_estimate=output_tokens,
            duration_ms=duration_ms,
            status="ok",
            retry_hint=retry_hint,
            prompt_hash=prompt_hash,
        )
        return response


__all__ = ["ProfilingLLMClient"]
