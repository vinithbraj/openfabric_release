"""Fast LLM service reachability checks for startup preflight."""

from __future__ import annotations

import argparse
import socket
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

from agent_runtime.api.config import Settings, get_settings


@dataclass(frozen=True)
class LLMEndpoint:
    """Network endpoint resolved from an OpenAI-compatible base URL."""

    base_url: str
    host: str
    port: int


@dataclass(frozen=True)
class LLMHealthResult:
    """Result from one LLM preflight check."""

    ok: bool
    endpoint: LLMEndpoint | None
    message: str


def endpoint_from_base_url(base_url: str) -> LLMEndpoint:
    """Resolve host and port from an LLM base URL."""

    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("llm.base_url must start with http:// or https://.")
    if not parsed.hostname:
        raise ValueError("llm.base_url must include a hostname.")
    if parsed.port is not None:
        port = int(parsed.port)
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    return LLMEndpoint(base_url=str(base_url).rstrip("/"), host=parsed.hostname, port=port)


def check_llm_service(settings: Settings, timeout_seconds: float = 3.0) -> LLMHealthResult:
    """Check whether the configured LLM service accepts a TCP connection."""

    try:
        endpoint = endpoint_from_base_url(settings.llm_base_url)
    except Exception as exc:
        return LLMHealthResult(ok=False, endpoint=None, message=str(exc))

    timeout = max(0.1, float(timeout_seconds))
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout):
            pass
    except OSError as exc:
        return LLMHealthResult(
            ok=False,
            endpoint=endpoint,
            message=(
                f"LLM service is not reachable at {endpoint.host}:{endpoint.port} "
                f"for {endpoint.base_url}: {exc}"
            ),
        )
    return LLMHealthResult(
        ok=True,
        endpoint=endpoint,
        message=f"LLM service is reachable at {endpoint.host}:{endpoint.port}.",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint used by startup.sh."""

    parser = argparse.ArgumentParser(description="Check configured LLM service reachability.")
    parser.add_argument("--config", default=None, help="Path to explicit legacy bootstrap YAML.")
    parser.add_argument("--timeout", type=float, default=3.0, help="Connection timeout in seconds.")
    args = parser.parse_args(argv)

    try:
        settings = get_settings(config_path=args.config or None)
        result = check_llm_service(settings, timeout_seconds=args.timeout)
    except Exception as exc:
        print(f"LLM preflight failed while loading settings: {exc}", file=sys.stderr)
        return 1

    stream = sys.stdout if result.ok else sys.stderr
    prefix = "LLM preflight OK: " if result.ok else "LLM preflight failed: "
    print(prefix + result.message, file=stream)
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised through subprocesses in production
    raise SystemExit(main())


__all__ = [
    "LLMEndpoint",
    "LLMHealthResult",
    "check_llm_service",
    "endpoint_from_base_url",
    "main",
]
