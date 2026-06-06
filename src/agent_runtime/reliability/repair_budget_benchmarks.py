"""Repair-budget recommendation helpers for execution-repair evals."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def recommend_execution_repair_budget(
    results: Iterable[dict[str, Any]],
    *,
    target_success_rate: float = 0.95,
    max_false_accept_rate: float = 0.0,
    fallback_budget: int = 2,
) -> dict[str, Any]:
    """Return the smallest execution-repair budget that satisfies eval thresholds."""

    grouped: dict[int, list[dict[str, Any]]] = {}
    for result in results:
        try:
            budget = int(result.get("budget"))
        except (TypeError, ValueError):
            continue
        grouped.setdefault(budget, []).append(dict(result))

    summaries: list[dict[str, Any]] = []
    for budget in sorted(grouped):
        cases = grouped[budget]
        total = len(cases)
        successes = sum(1 for case in cases if bool(case.get("success")))
        false_accepts = sum(1 for case in cases if bool(case.get("false_accept")))
        latencies = [
            float(case["duration_ms"])
            for case in cases
            if case.get("duration_ms") is not None
        ]
        llm_calls = [
            int(case["llm_calls"])
            for case in cases
            if case.get("llm_calls") is not None
        ]
        attempts_used = [
            int(case["attempts_used"])
            for case in cases
            if case.get("attempts_used") is not None
        ]
        summaries.append(
            {
                "budget": budget,
                "total_cases": total,
                "successes": successes,
                "failures": total - successes,
                "success_rate": round(successes / max(1, total), 4),
                "false_accepts": false_accepts,
                "false_accept_rate": round(false_accepts / max(1, total), 4),
                "median_duration_ms": _median(latencies),
                "median_llm_calls": _median(llm_calls),
                "median_attempts_used": _median(attempts_used),
            }
        )

    recommended = None
    for summary in summaries:
        if (
            float(summary["success_rate"]) >= target_success_rate
            and float(summary["false_accept_rate"]) <= max_false_accept_rate
        ):
            recommended = int(summary["budget"])
            break
    if recommended is None:
        recommended = fallback_budget

    return {
        "recommended_budget": recommended,
        "target_success_rate": target_success_rate,
        "max_false_accept_rate": max_false_accept_rate,
        "summaries": summaries,
    }


def _median(values: list[float] | list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


__all__ = ["recommend_execution_repair_budget"]
