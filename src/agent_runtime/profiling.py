"""Request-scoped runtime profiling helpers.

The profiler is intentionally small and passive: it records timing and LLM
usage from existing stage boundaries instead of becoming another pipeline.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def stable_json(value: Any) -> str:
    """Serialize a value stably for prompt-size accounting."""

    try:
        return json.dumps(value, sort_keys=True, default=str, ensure_ascii=True)
    except Exception:
        return str(value)


def estimate_tokens(value: Any) -> int:
    """Return a cheap operational token estimate."""

    text = value if isinstance(value, str) else stable_json(value)
    stripped = str(text or "").strip()
    if not stripped:
        return 0
    return max(1, round(len(stripped) / 4))


def latency_distribution(values: list[float]) -> dict[str, float | int | None]:
    """Return compact latency distribution stats for a list of millisecond values."""

    if not values:
        return {
            "count": 0,
            "average_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }
    ordered = sorted(float(value) for value in values)
    count = len(ordered)

    def percentile(percent: float) -> float:
        if count == 1:
            return ordered[0]
        index = min(count - 1, max(0, round((percent / 100.0) * (count - 1))))
        return ordered[index]

    return {
        "count": count,
        "average_ms": round(sum(ordered) / count, 2),
        "p50_ms": round(percentile(50), 2),
        "p95_ms": round(percentile(95), 2),
        "max_ms": round(max(ordered), 2),
    }


@dataclass
class RuntimeProfiler:
    """Collect request-local stage timings and LLM-call measurements."""

    request_id: str
    started_at: float = field(default_factory=time.perf_counter)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _active_stages: dict[str, float] = field(default_factory=dict)
    stage_timings: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)

    def stage_started(self, stage: str) -> None:
        """Mark a stage start if it is not already active."""

        normalized = str(stage or "unknown")
        self._active_stages[normalized] = time.perf_counter()

    def stage_completed(self, stage: str, *, status: str = "completed") -> float | None:
        """Mark a stage completion and return its elapsed milliseconds."""

        normalized = str(stage or "unknown")
        started = self._active_stages.pop(normalized, None)
        if started is None:
            return None
        duration_ms = (time.perf_counter() - started) * 1000.0
        self.stage_timings.append(
            {
                "stage": normalized,
                "status": status,
                "duration_ms": round(duration_ms, 2),
            }
        )
        return duration_ms

    def close_open_stages(self, *, status: str = "aborted") -> None:
        """Close any stages left open by an exception or early return."""

        for stage in list(self._active_stages):
            self.stage_completed(stage, status=status)

    def record_llm_call(
        self,
        *,
        stage: str,
        schema_name: str,
        model: str,
        prompt_chars: int,
        schema_chars: int,
        input_tokens_estimate: int,
        output_tokens_estimate: int | None,
        duration_ms: float | None,
        status: str,
        error_type: str | None = None,
        retry_hint: bool = False,
        prompt_hash: str | None = None,
    ) -> None:
        """Record one structured LLM request."""

        self.llm_calls.append(
            {
                "call_index": len(self.llm_calls) + 1,
                "stage": str(stage or "llm"),
                "schema_name": str(schema_name or "json_schema"),
                "model": str(model or "unknown"),
                "prompt_chars": int(prompt_chars),
                "schema_chars": int(schema_chars),
                "input_tokens_estimate": int(input_tokens_estimate),
                "output_tokens_estimate": output_tokens_estimate,
                "total_tokens_estimate": (
                    int(input_tokens_estimate) + int(output_tokens_estimate)
                    if output_tokens_estimate is not None
                    else None
                ),
                "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
                "status": str(status or "unknown"),
                "error_type": error_type,
                "retry_hint": bool(retry_hint),
                "prompt_hash": prompt_hash,
            }
        )

    def summary(self) -> dict[str, Any]:
        """Return a JSON-safe profile report for the request."""

        total_duration_ms = (time.perf_counter() - self.started_at) * 1000.0
        stage_totals: dict[str, dict[str, Any]] = {}
        for timing in self.stage_timings:
            stage = str(timing.get("stage") or "unknown")
            bucket = stage_totals.setdefault(
                stage,
                {"stage": stage, "count": 0, "total_duration_ms": 0.0, "max_duration_ms": 0.0},
            )
            duration = float(timing.get("duration_ms") or 0.0)
            bucket["count"] += 1
            bucket["total_duration_ms"] = round(float(bucket["total_duration_ms"]) + duration, 2)
            bucket["max_duration_ms"] = round(max(float(bucket["max_duration_ms"]), duration), 2)

        longest_stage = None
        if stage_totals:
            longest_stage = max(stage_totals.values(), key=lambda item: item["total_duration_ms"])

        llm_calls_by_stage: dict[str, int] = {}
        repeated_call_keys: dict[tuple[str, str], int] = {}
        for call in self.llm_calls:
            stage = str(call.get("stage") or "llm")
            llm_calls_by_stage[stage] = llm_calls_by_stage.get(stage, 0) + 1
            key = (stage, str(call.get("schema_name") or "json_schema"))
            repeated_call_keys[key] = repeated_call_keys.get(key, 0) + 1

        retry_count = sum(max(0, count - 1) for count in repeated_call_keys.values())
        llm_retry_count = sum(1 for call in self.llm_calls if bool(call.get("retry_hint")))
        retry_groups = [
            {
                "stage": stage,
                "schema_name": schema_name,
                "call_count": count,
            }
            for (stage, schema_name), count in sorted(repeated_call_keys.items())
            if count > 1
        ]
        llm_durations = [
            float(call["duration_ms"])
            for call in self.llm_calls
            if call.get("duration_ms") is not None
        ]
        stage_durations = [float(item["duration_ms"]) for item in self.stage_timings]
        longest_llm_call = None
        completed_calls = [call for call in self.llm_calls if call.get("duration_ms") is not None]
        if completed_calls:
            longest_llm_call = max(completed_calls, key=lambda call: float(call["duration_ms"]))

        prompt_chars_total = sum(int(call.get("prompt_chars") or 0) for call in self.llm_calls)
        input_tokens_total = sum(
            int(call.get("input_tokens_estimate") or 0) for call in self.llm_calls
        )
        output_tokens_total = sum(
            int(call.get("output_tokens_estimate") or 0)
            for call in self.llm_calls
            if call.get("output_tokens_estimate") is not None
        )
        opportunities: list[dict[str, Any]] = []
        for group in retry_groups:
            opportunities.append(
                {
                    "type": "repeated_semantic_stage",
                    "stage": group["stage"],
                    "schema_name": group["schema_name"],
                    "call_count": group["call_count"],
                    "suggestion": "Review whether this is a true corrective retry or duplicate semantic pass.",
                }
            )
        for call in self.llm_calls:
            if int(call.get("prompt_chars") or 0) > 12000:
                opportunities.append(
                    {
                        "type": "large_prompt",
                        "stage": call.get("stage"),
                        "schema_name": call.get("schema_name"),
                        "prompt_chars": call.get("prompt_chars"),
                        "suggestion": "Consider caching static manifest text or trimming safe previews.",
                    }
                )

        return {
            "request_id": self.request_id,
            "created_at": self.created_at,
            "total_duration_ms": round(total_duration_ms, 2),
            "stage_timings": list(self.stage_timings),
            "stage_totals": list(stage_totals.values()),
            "longest_stage": longest_stage,
            "llm_call_count": len(self.llm_calls),
            "llm_retry_count": llm_retry_count,
            "llm_retry_like_count": retry_count,
            "llm_retry_groups": retry_groups,
            "llm_calls_by_stage": llm_calls_by_stage,
            "llm_calls": list(self.llm_calls),
            "longest_llm_call": longest_llm_call,
            "prompt_chars_total": prompt_chars_total,
            "input_tokens_estimate_total": input_tokens_total,
            "output_tokens_estimate_total": output_tokens_total,
            "llm_latency_distribution": latency_distribution(llm_durations),
            "stage_latency_distribution": latency_distribution(stage_durations),
            "deterministic_opportunities": opportunities,
        }


__all__ = [
    "RuntimeProfiler",
    "estimate_tokens",
    "latency_distribution",
    "stable_json",
]
