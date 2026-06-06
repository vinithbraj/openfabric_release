#!/usr/bin/env python3
"""Benchmark local vLLM launch profiles with agent-style prompts."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LLM_DIR = Path(__file__).resolve().parent
PROMPTS_PATH = LLM_DIR / "benchmark_prompts.json"
RESULTS_DIR = LLM_DIR / "results"

PROFILES: dict[str, dict[str, Any]] = {
    "qwen3-coder-30b-a3b-awq": {
        "model_id": "stelterlab/Qwen3-Coder-30B-A3B-Instruct-AWQ",
        "script": "start-qwen3-coder-30b-a3b-awq.sh",
        "notes": "Main coder MoE candidate.",
        "thinking_template_kwargs": True,
    },
    "qwen2.5-coder-14b-awq": {
        "model_id": "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ",
        "script": "start-qwen2.5-coder-14b-awq.sh",
        "notes": "Fast dense coder baseline.",
    },
    "qwen2.5-coder-7b-awq": {
        "model_id": "Qwen/Qwen2.5-Coder-7B-Instruct-AWQ",
        "script": "start-qwen2.5-coder-7b-awq.sh",
        "notes": "Small dense coder baseline.",
    },
    "qwen2.5-coder-32b-awq": {
        "model_id": "Qwen/Qwen2.5-Coder-32B-Instruct-AWQ",
        "script": "start-qwen2.5-coder-32b-awq.sh",
        "notes": "Strong dense coder, higher VRAM pressure.",
    },
    "deepseek-coder-v2-lite-instruct-awq": {
        "model_id": "TechxGenus/DeepSeek-Coder-V2-Lite-Instruct-AWQ",
        "script": "start-deepseek-coder-v2-lite-instruct-awq.sh",
        "notes": "DeepSeek coder MoE-lite alternative.",
    },
    "yi-coder-9b-chat-awq": {
        "model_id": "stelterlab/Yi-Coder-9B-Chat-AWQ",
        "script": "start-yi-coder-9b-chat-awq.sh",
        "notes": "Compact Yi coder chat alternative.",
    },
    "qwen3-14b-awq": {
        "model_id": "Qwen/Qwen3-14B-AWQ",
        "script": "start-qwen3-14b-awq.sh",
        "notes": "General instruct/reasoning model.",
        "thinking_template_kwargs": True,
    },
    "qwen3-8b-awq": {
        "model_id": "Qwen/Qwen3-8B-AWQ",
        "script": "start-qwen3-8b-awq.sh",
        "notes": "Very fast small-model loop.",
        "thinking_template_kwargs": True,
    },
    "meta-llama-3-8b-instruct-awq": {
        "model_id": "stelterlab/Meta-Llama-3-8B-Instruct-AWQ",
        "script": "start-meta-llama-3-8b-instruct-awq.sh",
        "notes": "Compact Llama 3 instruct AWQ baseline.",
    },
    "qwen3-30b-a3b-instruct-awq": {
        "model_id": "stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ",
        "script": "start-qwen3-30b-a3b-instruct-awq.sh",
        "notes": "General MoE instruct sibling.",
        "thinking_template_kwargs": True,
    },
    "qwen3-30b-a3b-thinking-awq": {
        "model_id": "QuantTrio/Qwen3-30B-A3B-Thinking-2507-AWQ",
        "script": "start-qwen3-30b-a3b-thinking-awq.sh",
        "notes": "Qwen3 30B-A3B reasoning MoE candidate.",
        "thinking_template_kwargs": True,
    },
    "devstral-small-2507-awq": {
        "model_id": "cpatonn/Devstral-Small-2507-AWQ-4bit",
        "script": "start-devstral-small-2507-awq.sh",
        "notes": "Experimental agentic coding alternative.",
    },
    "mistral-small-24b-instruct-2501-awq": {
        "model_id": "stelterlab/Mistral-Small-24B-Instruct-2501-AWQ",
        "script": "start-mistral-small-24b-instruct-2501-awq.sh",
        "notes": "AWQ-quantized Mistral Small 3 instruct candidate.",
    },
    "glm-4.5-air-awq": {
        "model_id": "cpatonn/GLM-4.5-Air-AWQ-4bit",
        "script": "start-glm-4.5-air-awq.sh",
        "notes": "GLM Air MoE instruct alternative.",
    },
    "ministral-3-8b-instruct-2512": {
        "model_id": "mistralai/Ministral-3-8B-Instruct-2512",
        "script": "start-ministral-3-8b-instruct-2512.sh",
        "notes": "Mistral 3 small instruct, fast agentic candidate.",
    },
    "ministral-3-14b-instruct-2512": {
        "model_id": "mistralai/Ministral-3-14B-Instruct-2512",
        "script": "start-ministral-3-14b-instruct-2512.sh",
        "notes": "Mistral 3 strongest local instruct candidate.",
    },
    "devstral-small-2-24b-instruct-2512": {
        "model_id": "mistralai/Devstral-Small-2-24B-Instruct-2512",
        "script": "start-devstral-small-2-24b-instruct-2512.sh",
        "notes": "Mistral agentic coding model; may exceed 24 GB on RTX 3090.",
    },
    "mistral-small-3.2-24b-instruct-2506": {
        "model_id": "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
        "script": "start-mistral-small-3.2-24b-instruct-2506.sh",
        "notes": "Mistral Small 3.2 general instruct; likely too large for 24 GB without compression.",
    },
    "mistral-nemo-instruct-2407": {
        "model_id": "mistralai/Mistral-Nemo-Instruct-2407",
        "script": "start-mistral-nemo-instruct-2407.sh",
        "notes": "Older 12B Mistral/NVIDIA instruct baseline.",
    },
    "deepseek-v2-lite-chat-awq": {
        "model_id": "TechxGenus/DeepSeek-V2-Lite-Chat-AWQ",
        "script": "start-deepseek-v2-lite-chat-awq.sh",
        "notes": "DeepSeek V2 Lite chat MoE alternative.",
    },
}

DEFAULT_MODELS = [
    "qwen3-coder-30b-a3b-awq",
    "qwen2.5-coder-14b-awq",
    "qwen2.5-coder-7b-awq",
    "qwen2.5-coder-32b-awq",
    "deepseek-coder-v2-lite-instruct-awq",
    "yi-coder-9b-chat-awq",
    "qwen3-14b-awq",
    "qwen3-8b-awq",
    "meta-llama-3-8b-instruct-awq",
    "qwen3-30b-a3b-instruct-awq",
    "qwen3-30b-a3b-thinking-awq",
    "devstral-small-2507-awq",
    "mistral-small-24b-instruct-2501-awq",
    "glm-4.5-air-awq",
    "ministral-3-8b-instruct-2512",
    "ministral-3-14b-instruct-2512",
    "devstral-small-2-24b-instruct-2512",
    "mistral-small-3.2-24b-instruct-2506",
    "mistral-nemo-instruct-2407",
    "deepseek-v2-lite-chat-awq",
]

SYSTEM_PROMPT = """You are being benchmarked as the reasoning and coding model for a local agent runtime.
Prefer operator-native plans, machine-readable command output, trusted Python actions when parsing or orchestration is needed, and clarification when missing information materially affects outcome.
When a prompt asks for JSON, return only valid JSON. When a prompt asks for code only, return only code.
Avoid hidden assumptions, avoid fake success, and preserve evidence from runtime errors.
Do not output hidden reasoning, analysis, or <think> blocks. Output only the requested final artifact.
/no_think"""


def _json_request(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server(base_url: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            return _get_json(f"{base_url}/models", timeout=5.0)
        except Exception as exc:  # noqa: BLE001 - startup probes need broad capture.
            last_error = repr(exc)
            time.sleep(2.0)
    raise TimeoutError(f"vLLM server did not become ready: {last_error}")


def wait_for_server_absent(base_url: str, timeout_s: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _get_json(f"{base_url}/models", timeout=2.0)
        except Exception:
            return
        time.sleep(2.0)
    raise TimeoutError(f"vLLM server on {base_url} did not shut down")


def wait_for_expected_server(base_url: str, expected_model: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_seen: str | None = None
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            payload = _get_json(f"{base_url}/models", timeout=5.0)
            served = [item.get("id") for item in payload.get("data", [])]
            if expected_model in served:
                return payload
            last_seen = ", ".join(str(item) for item in served) or "<empty>"
        except Exception as exc:  # noqa: BLE001 - startup probes need broad capture.
            last_error = repr(exc)
        time.sleep(2.0)
    details = f"last_error={last_error}" if last_error else f"last_seen={last_seen}"
    raise TimeoutError(f"vLLM did not serve expected model {expected_model}: {details}")


def launch_profile(name: str, port: int, startup_timeout_s: float) -> tuple[subprocess.Popen[str], float]:
    profile = PROFILES[name]
    script = LLM_DIR / profile["script"]
    base_url = f"http://127.0.0.1:{port}/v1"
    wait_for_server_absent(base_url)
    env = os.environ.copy()
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)
    env.setdefault("HF_HOME", str(Path.home() / "models" / "data"))
    env.setdefault("PYTHONUNBUFFERED", "1")
    log_path = RESULTS_DIR / f"{name}.server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [str(script)],
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            bufsize=1,
        )
    finally:
        log.close()
    start = time.monotonic()
    try:
        expected_model = str(profile["model_id"])
        while time.monotonic() - start < startup_timeout_s:
            if proc.poll() is not None:
                raise RuntimeError(f"{name} exited during startup. See {log_path}")
            try:
                payload = _get_json(f"{base_url}/models", timeout=2.0)
                served = [item.get("id") for item in payload.get("data", [])]
                if expected_model in served:
                    return proc, time.monotonic() - start
                if served:
                    time.sleep(2.0)
                    continue
                return proc, time.monotonic() - start
            except Exception:
                time.sleep(2.0)
        raise TimeoutError(f"{name} startup timed out after {startup_timeout_s}s. See {log_path}")
    except Exception:
        stop_process(proc)
        raise


def stop_process(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=30)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def chat_once(
    base_url: str,
    model: str,
    case: dict[str, Any],
    request_timeout_s: float,
    no_think: bool,
    thinking_template_kwargs: bool,
) -> dict[str, Any]:
    user_prompt = case["prompt"]
    if no_think:
        user_prompt = f"{user_prompt}\n\n/no_think"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": int(case.get("max_tokens", 512)),
    }
    if no_think and thinking_template_kwargs:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    started = time.perf_counter()
    response = _json_request(
        f"{base_url}/chat/completions",
        payload,
        timeout=request_timeout_s,
    )
    elapsed = time.perf_counter() - started
    text = response["choices"][0]["message"].get("content") or ""
    usage = response.get("usage") or {}
    completion_tokens = usage.get("completion_tokens")
    if not completion_tokens:
        completion_tokens = max(1, round(len(text) / 4))
    return {
        "text": text,
        "latency_s": elapsed,
        "completion_tokens": completion_tokens,
        "tokens_per_s": completion_tokens / elapsed if elapsed > 0 else 0.0,
        "usage": usage,
    }


def extract_json_object(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\})", stripped, re.S)
    if match:
        return json.loads(match.group(1))
    raise ValueError("no JSON object found")


def nested_value(payload: Any, dotted_key: str) -> Any:
    current = payload
    for part in dotted_key.split("."):
        if isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(dotted_key)
    return current


def evaluate(text: str, checks: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    lowered = text.lower()
    for item in checks.get("required", []):
        if str(item).lower() not in lowered:
            failures.append(f"missing required text: {item}")
    for item in checks.get("forbidden", []):
        if str(item).lower() in lowered:
            failures.append(f"contains forbidden text: {item}")
    for pattern in checks.get("regex", []):
        if not re.search(pattern, text, re.I | re.S):
            failures.append(f"missing regex: {pattern}")
    if checks.get("numeric"):
        try:
            payload = extract_json_object(text)
        except Exception as exc:  # noqa: BLE001
            payload = None
            failures.append(f"numeric check could not parse JSON: {exc}")
        if payload is not None:
            for spec in checks["numeric"]:
                try:
                    value = float(nested_value(payload, spec["key"]))
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"numeric key {spec['key']} missing/non-numeric: {exc}")
                    continue
                if "min" in spec and value < float(spec["min"]):
                    failures.append(f"numeric {spec['key']} below {spec['min']}: {value}")
                if "max" in spec and value > float(spec["max"]):
                    failures.append(f"numeric {spec['key']} above {spec['max']}: {value}")
    return {
        "passed": not failures,
        "failures": failures,
    }


def run_model(name: str, args: argparse.Namespace, cases: list[dict[str, Any]]) -> dict[str, Any]:
    base_url = args.base_url or f"http://127.0.0.1:{args.port}/v1"
    proc: subprocess.Popen[str] | None = None
    launched = not args.no_launch
    startup_s: float | None = None
    try:
        if launched:
            proc, startup_s = launch_profile(name, args.port, args.startup_timeout)
        if launched:
            model_payload = wait_for_expected_server(
                base_url,
                str(PROFILES[name]["model_id"]),
                args.server_timeout,
            )
        else:
            model_payload = wait_for_server(base_url, args.server_timeout)
        served_model = model_payload["data"][0]["id"]
        results = []
        for index, case in enumerate(cases, start=1):
            print(f"[{name}] {index}/{len(cases)} {case['id']}", flush=True)
            try:
                response = chat_once(
                    base_url,
                    served_model,
                    case,
                    args.request_timeout,
                    args.no_think,
                    bool(PROFILES.get(name, {}).get("thinking_template_kwargs")),
                )
                verdict = evaluate(response["text"], case.get("checks", {}))
                results.append({
                    "case_id": case["id"],
                    "category": case.get("category"),
                    **response,
                    **verdict,
                })
            except Exception as exc:  # noqa: BLE001 - benchmark should keep going.
                results.append({
                    "case_id": case["id"],
                    "category": case.get("category"),
                    "text": "",
                    "latency_s": None,
                    "completion_tokens": 0,
                    "tokens_per_s": 0.0,
                    "passed": False,
                    "failures": [f"request_error: {exc!r}"],
                })
        passed = sum(1 for item in results if item["passed"])
        latencies = [item["latency_s"] for item in results if item["latency_s"] is not None]
        speeds = [item["tokens_per_s"] for item in results if item["tokens_per_s"]]
        return {
            "name": name,
            "served_model": served_model,
            "notes": PROFILES.get(name, {}).get("notes", "external server"),
            "startup_s": startup_s,
            "passed": passed,
            "total": len(results),
            "accuracy": passed / len(results) if results else 0.0,
            "avg_latency_s": sum(latencies) / len(latencies) if latencies else None,
            "avg_tokens_per_s": sum(speeds) / len(speeds) if speeds else 0.0,
            "results": results,
        }
    finally:
        if launched:
            stop_process(proc)


def write_report(run: dict[str, Any], output_path: Path) -> None:
    lines = [
        "# vLLM Agent Benchmark Report",
        "",
        f"Started: {run['started_at']}",
        f"Prompt count: {run['prompt_count']}",
        "",
        "| Model | Accuracy | Startup | Avg latency | Avg tok/s | Notes |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for model in run["models"]:
        latency = "n/a" if model["avg_latency_s"] is None else f"{model['avg_latency_s']:.2f}s"
        startup = "external" if model.get("startup_s") is None else f"{model['startup_s']:.1f}s"
        lines.append(
            "| {name} | {passed}/{total} ({accuracy:.0%}) | {startup} | {latency} | {speed:.1f} | {notes} |".format(
                name=model["name"],
                passed=model["passed"],
                total=model["total"],
                accuracy=model["accuracy"],
                startup=startup,
                latency=latency,
                speed=model["avg_tokens_per_s"],
                notes=model["notes"],
            )
        )
    lines.append("")
    for model in run["models"]:
        lines.append(f"## {model['name']}")
        for result in model["results"]:
            status = "PASS" if result["passed"] else "FAIL"
            latency = result["latency_s"]
            latency_text = "n/a" if latency is None else f"{latency:.2f}s"
            lines.append(f"- {status} `{result['case_id']}` ({latency_text}, {result['tokens_per_s']:.1f} tok/s)")
            for failure in result.get("failures") or []:
                lines.append(f"  - {failure}")
        lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated model profile names, or 'all'.",
    )
    parser.add_argument("--prompt-limit", type=int, default=None)
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--base-url", default=None, help="Use an already-running /v1 server.")
    parser.add_argument("--no-launch", action="store_true", help="Do not launch a profile; use --base-url.")
    parser.add_argument("--startup-timeout", type=float, default=900)
    parser.add_argument("--server-timeout", type=float, default=30)
    parser.add_argument("--request-timeout", type=float, default=180)
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    parser.add_argument("--no-think", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = DEFAULT_MODELS if args.models == "all" else [item.strip() for item in args.models.split(",") if item.strip()]
    if args.no_launch and not args.base_url:
        print("--no-launch requires --base-url", file=sys.stderr)
        return 2
    unknown = [name for name in selected if name not in PROFILES and not args.no_launch]
    if unknown:
        print(f"Unknown model profiles: {', '.join(unknown)}", file=sys.stderr)
        return 2
    cases = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
    if args.prompt_limit:
        cases = cases[: args.prompt_limit]
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    run = {
        "started_at": timestamp,
        "prompt_count": len(cases),
        "models": [],
    }
    for name in selected:
        print(f"=== Benchmarking {name} ===", flush=True)
        try:
            model_result = run_model(name, args, cases)
        except Exception as exc:  # noqa: BLE001 - keep multi-model runs going.
            model_result = {
                "name": name,
                "served_model": None,
                "notes": PROFILES.get(name, {}).get("notes", "external server"),
                "startup_s": None,
                "passed": 0,
                "total": len(cases),
                "accuracy": 0.0,
                "avg_latency_s": None,
                "avg_tokens_per_s": 0.0,
                "results": [
                    {
                        "case_id": "startup_or_model_error",
                        "category": "startup",
                        "text": "",
                        "latency_s": None,
                        "completion_tokens": 0,
                        "tokens_per_s": 0.0,
                        "passed": False,
                        "failures": [repr(exc)],
                    }
                ],
            }
        run["models"].append(model_result)
        json_path = results_dir / f"benchmark-{timestamp}.json"
        json_path.write_text(json.dumps(run, indent=2), encoding="utf-8")
        write_report(run, results_dir / f"benchmark-{timestamp}.md")
    print(f"Results: {results_dir / f'benchmark-{timestamp}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
