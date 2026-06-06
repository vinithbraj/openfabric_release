"""Live benchmark runner for granular report and workflow routing."""

from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


DEFAULT_PROMPTS: tuple[dict[str, Any], ...] = (
    {
        "case_id": "branch-date-report",
        "prompt": "in this git repo get me the date of the oldest commit and the latest commit for each branch.",
        "category": "granular_report",
    },
    {
        "case_id": "image-size-total",
        "prompt": "list all container images and calculate their total size",
        "category": "granular_report",
    },
    {
        "case_id": "largest-files",
        "prompt": "show the 10 largest files in this repository with their sizes",
        "category": "granular_report",
    },
    {
        "case_id": "per-directory-counts",
        "prompt": "for each top level directory summarize the number of files and total size",
        "category": "granular_report",
    },
    {
        "case_id": "discover-then-mutate",
        "prompt": "search for docker-compose.yaml or yml and start them using docker compose up",
        "category": "staged_workflow",
    },
    {
        "case_id": "edit-test-workflow",
        "prompt": "edit the README to add a short note, then run tests",
        "category": "staged_workflow",
    },
)


@dataclass
class BenchmarkCaseResult:
    case_id: str
    category: str
    request_id: str
    status: str
    duration_ms: float | None = None
    llm_call_count: int | None = None
    total_tokens_estimate: int | None = None
    action_count: int | None = None
    decomposition_suppressed: bool = False
    streaming_used: bool = False
    final_response_preview: str = ""
    final_answer_visible: bool = False
    placeholder_or_type_failure: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
    insecure_tls: bool = True,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = ssl._create_unverified_context() if insecure_tls else None
    with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _poll_trace(base_url: str, request_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_payload = _request_json(
            "GET",
            f"{base_url.rstrip('/')}/api/agent/trace/{request_id}",
            timeout=10,
        )
        trace = last_payload.get("trace") if isinstance(last_payload.get("trace"), dict) else last_payload
        status = str(trace.get("status") or "")
        if status in {"completed", "failed", "cancelled"}:
            return last_payload
        time.sleep(1.0)
    return last_payload


def _learning_run(base_url: str, request_id: str) -> dict[str, Any]:
    try:
        return _request_json(
            "GET",
            f"{base_url.rstrip('/')}/api/agent/learning-ledger/runs/{request_id}",
            timeout=10,
        )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return {}


def _case_result(case: dict[str, Any], submit: dict[str, Any], trace_payload: dict[str, Any], ledger: dict[str, Any]) -> BenchmarkCaseResult:
    request_id = str(submit.get("request_id") or "")
    trace = trace_payload.get("trace") if isinstance(trace_payload.get("trace"), dict) else trace_payload
    run = ledger.get("run") if isinstance(ledger.get("run"), dict) else {}
    actions = ledger.get("actions") if isinstance(ledger.get("actions"), list) else []
    metadata = trace.get("metadata") if isinstance(trace.get("metadata"), dict) else {}
    events = trace.get("events") if isinstance(trace.get("events"), list) else []
    raw_payloads = (
        trace.get("raw_payloads")
        if isinstance(trace.get("raw_payloads"), dict)
        else trace_payload.get("raw_payloads")
        if isinstance(trace_payload.get("raw_payloads"), dict)
        else {}
    )
    event_types = {str(event.get("event_type") or "") for event in events if isinstance(event, dict)}
    event_titles = {
        str(event.get("title") or "").strip().lower()
        for event in events
        if isinstance(event, dict)
    }
    final_response = str(
        run.get("final_response_preview")
        or trace.get("final_response")
        or trace_payload.get("final_response")
        or ""
    )
    lower_final = final_response.lower()
    return BenchmarkCaseResult(
        case_id=str(case.get("case_id") or ""),
        category=str(case.get("category") or ""),
        request_id=request_id,
        status=str(run.get("status") or trace.get("status") or ""),
        duration_ms=float(run["duration_ms"]) if run.get("duration_ms") is not None else None,
        llm_call_count=int(run["llm_call_count"]) if run.get("llm_call_count") is not None else None,
        total_tokens_estimate=(
            int(run["total_tokens_estimate"])
            if run.get("total_tokens_estimate") is not None
            else None
        ),
        action_count=len(actions) if actions else (len(raw_payloads) if raw_payloads else None),
        decomposition_suppressed=(
            bool(metadata.get("operator_streaming_suppressed_by_execution_shape"))
            or "streaming decomposition suppressed" in event_titles
        ),
        streaming_used=bool(metadata.get("workflow_execution_mode") == "streaming")
        or any(event_type.startswith("operator.streaming") for event_type in event_types),
        final_response_preview=final_response[:500],
        final_answer_visible=bool(final_response.strip())
        and "task completed" not in lower_final
        and "detailed output is available" not in lower_final,
        placeholder_or_type_failure=(
            "unknown" in lower_final
            or "n/a" in lower_final
            or "report validation blocked" in lower_final
            or "execution-shape report output rejected" in event_titles
            or bool(metadata.get("execution_shape_output_validation_failed"))
        ),
        raw={"submit": submit, "trace": trace_payload, "ledger": ledger},
    )


def run_live_execution_shape_benchmarks(
    *,
    base_url: str,
    prompts: tuple[dict[str, Any], ...] = DEFAULT_PROMPTS,
    context: dict[str, Any] | None = None,
    timeout_seconds: float = 240.0,
) -> list[BenchmarkCaseResult]:
    results: list[BenchmarkCaseResult] = []
    for case in prompts:
        submit = _request_json(
            "POST",
            f"{base_url.rstrip('/')}/api/agent/request",
            payload={
                "prompt": case["prompt"],
                "context": {
                    "workflow_execution_mode": "streaming",
                    "llm_operator_final_response_mode": "simple",
                    **dict(context or {}),
                },
            },
            timeout=30,
        )
        request_id = str(submit.get("request_id") or "")
        trace = _poll_trace(base_url, request_id, timeout_seconds=timeout_seconds)
        ledger = _learning_run(base_url, request_id)
        results.append(_case_result(case, submit, trace, ledger))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run live execution-shape benchmarks against an Agent UI server.")
    parser.add_argument("--base-url", default="https://localhost:8011")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)
    results = run_live_execution_shape_benchmarks(
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
    )
    payload = [result.__dict__ for result in results]
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=True, default=str))
    else:
        for result in results:
            print(
                f"{result.case_id}: status={result.status} "
                f"llm_calls={result.llm_call_count} duration_ms={result.duration_ms} "
                f"actions={result.action_count} suppressed={result.decomposition_suppressed} "
                f"visible={result.final_answer_visible}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
