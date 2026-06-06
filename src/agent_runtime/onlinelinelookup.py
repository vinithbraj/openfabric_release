"""Compact explicit online lookup support for the agent runtime."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

ONLINE_LOOKUP_CONTEXT_KEY = "agent_online_check_context"
ONLINE_LOOKUP_CONTEXTS_KEY = "agent_online_check_contexts"
ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY = "agent_online_check_requested"
ONLINE_LOOKUP_PROVIDER = "duck_ai"
GEMINI_GROUNDING_PROVIDER = "gemini_grounding"
COMBINED_ONLINE_LOOKUP_PROVIDER = "duck_ai+gemini_grounding"

_MAX_QUERY_CHARS = 500
_MAX_ANSWER_CHARS = 1200
_MAX_TITLE_CHARS = 240
_MAX_URL_CHARS = 2000
_MAX_ERROR_CHARS = 300
_GEMINI_GENERATE_CONTENT_URL = "https://generativelanguage.googleapis.com/{version}/models/{model}:generateContent"
_DEFAULT_GEMINI_GROUNDING_MODEL = "gemini-2.5-flash"
_DEFAULT_GEMINI_API_VERSION = "v1beta"
_SUMMARY_CLASS_RE = re.compile(
    r"<(?P<tag>div|span)[^>]+class=[\"'][^\"']*(?:hgKElc|kno-rdesc|ifM9O|IZ6rdc|Z0LcW|LGOjhe)[^\"']*[\"'][^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_ORGANIC_LINK_RE = re.compile(
    r"<a[^>]+href=[\"'](?P<href>/url\?q=[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r"<(?P<tag>div|span)[^>]+class=[\"'][^\"']*(?:VwiC3b|BNeawe|st|hgKElc)[^\"']*[\"'][^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_TYPEIN_MACRO_RE = re.compile(r"\btypein\s*\"(?:\\.|[^\"\\])*\"", re.IGNORECASE)
_CHECKONLINE_RE = re.compile(r"(?<!\S)/checkonline\b", re.IGNORECASE)


@dataclass(frozen=True)
class OnlineLookupResult:
    """One compact online lookup answer suitable for prompt context."""

    provider: str
    query: str
    available: bool
    answer_text: str = ""
    source_title: str = ""
    source_url: str = ""
    fetched_at: str = ""
    error: str = ""
    provider_results: tuple[dict[str, Any], ...] = ()

    def to_context(self) -> dict[str, Any]:
        payload = {
            "provider": self.provider,
            "query": self.query,
            "available": self.available,
            "answer_text": self.answer_text,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at,
            "error": self.error,
        }
        if self.provider_results:
            payload["provider_results"] = list(self.provider_results)
        return payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cap(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def sanitize_online_lookup_query(query: str) -> str:
    """Remove deterministic input macros from online lookup text."""

    text = _CHECKONLINE_RE.sub(" ", str(query or ""))
    text = _TYPEIN_MACRO_RE.sub(" ", text)
    return _cap(_compact_text(text), _MAX_QUERY_CHARS)


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", str(value or ""))
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return _compact_text(unescape(text))


def _result(
    *,
    query: str,
    available: bool,
    answer_text: str = "",
    source_title: str = "",
    source_url: str = "",
    error: str = "",
    provider: str = ONLINE_LOOKUP_PROVIDER,
    provider_results: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> OnlineLookupResult:
    return OnlineLookupResult(
        provider=_cap(_compact_text(provider), 120) or ONLINE_LOOKUP_PROVIDER,
        query=_cap(_compact_text(query), _MAX_QUERY_CHARS),
        available=bool(available),
        answer_text=_cap(_compact_text(answer_text), _MAX_ANSWER_CHARS),
        source_title=_cap(_compact_text(source_title), _MAX_TITLE_CHARS),
        source_url=_cap(_compact_text(source_url), _MAX_URL_CHARS),
        fetched_at=_now_iso(),
        error=_cap(_compact_text(error), _MAX_ERROR_CHARS),
        provider_results=tuple(
            _normalized_provider_result(item) for item in (provider_results or [])
        ),
    )


def _normalized_provider_result(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": _cap(raw.get("provider"), 120),
        "query": _cap(raw.get("query"), _MAX_QUERY_CHARS),
        "available": bool(raw.get("available")),
        "answer_text": _cap(raw.get("answer_text"), _MAX_ANSWER_CHARS),
        "source_title": _cap(raw.get("source_title"), _MAX_TITLE_CHARS),
        "source_url": _cap(raw.get("source_url"), _MAX_URL_CHARS),
        "fetched_at": _cap(raw.get("fetched_at"), 80),
        "error": _cap(raw.get("error"), _MAX_ERROR_CHARS),
    }


def _duck_ai_url(query: str = "") -> str:
    clean_query = sanitize_online_lookup_query(query)
    if not clean_query:
        return "https://duck.ai/?origin=funnel_home_website"
    params = urllib_parse.urlencode({"origin": "funnel_home_website", "q": clean_query})
    return f"https://duck.ai/?{params}"


# Compatibility for older private tests/imports.
def _google_search_url(query: str) -> str:
    return _duck_ai_url(query)


def _decode_google_result_url(href: str) -> str:
    parsed = urllib_parse.urlsplit(unescape(str(href or "")))
    if parsed.path != "/url":
        return ""
    query = urllib_parse.parse_qs(parsed.query)
    target = str((query.get("q") or [""])[0] or "").strip()
    if not target.startswith(("http://", "https://")):
        return ""
    return target


def _provider_context(result: OnlineLookupResult) -> dict[str, Any]:
    return _normalized_provider_result(result.to_context())


def _combine_lookup_results(query: str, results: list[OnlineLookupResult]) -> OnlineLookupResult:
    provider_results = [_provider_context(result) for result in results]
    available_results = [result for result in results if result.available]
    if not available_results:
        error = "; ".join(
            f"{result.provider}: {result.error or 'unavailable'}" for result in results
        )
        return _result(
            query=query,
            available=False,
            error=error or "No online lookup providers returned an answer.",
            provider=COMBINED_ONLINE_LOOKUP_PROVIDER,
            provider_results=provider_results,
        )

    answer_parts: list[str] = []
    for result in available_results:
        if not result.answer_text:
            continue
        answer_parts.append(f"{result.provider}: {result.answer_text}")
    primary = available_results[0]
    return _result(
        query=query,
        available=True,
        answer_text=" ".join(answer_parts) or primary.answer_text,
        source_title=primary.source_title,
        source_url=primary.source_url,
        provider=COMBINED_ONLINE_LOOKUP_PROVIDER,
        provider_results=provider_results,
    )


def _extract_summary(html: str) -> str:
    for match in _SUMMARY_CLASS_RE.finditer(html):
        text = _strip_html(match.group("body"))
        if text and len(text) >= 20:
            return text
    return ""


def _extract_first_organic(html: str) -> tuple[str, str, str]:
    for match in _ORGANIC_LINK_RE.finditer(html):
        source_url = _decode_google_result_url(match.group("href"))
        if not source_url or "google." in urllib_parse.urlsplit(source_url).netloc:
            continue
        title = _strip_html(match.group("body"))
        if not title:
            continue
        next_link = _ORGANIC_LINK_RE.search(html, match.end())
        segment = html[match.end() : next_link.start() if next_link else match.end() + 2500]
        snippet = ""
        snippet_match = _SNIPPET_RE.search(segment)
        if snippet_match:
            snippet = _strip_html(snippet_match.group("body"))
        return title, source_url, snippet
    return "", "", ""


def parse_google_lookup_html(html: str, *, query: str) -> OnlineLookupResult:
    """Extract one compact answer from a Google HTML response."""

    text = _strip_html(html)
    lower = text.lower()
    if "unusual traffic" in lower or "our systems have detected" in lower:
        return _result(
            query=query,
            available=False,
            error="Google returned an anti-automation page.",
        )
    if "before you continue to google search" in lower:
        return _result(query=query, available=False, error="Google returned a consent page.")

    summary = _extract_summary(html)
    title, source_url, snippet = _extract_first_organic(html)
    if summary:
        return _result(
            query=query,
            available=True,
            answer_text=summary,
            source_title=title,
            source_url=source_url,
        )
    if title or snippet:
        return _result(
            query=query,
            available=True,
            answer_text=snippet or title,
            source_title=title,
            source_url=source_url,
        )
    return _result(query=query, available=False, error="No compact answer could be extracted.")


def parse_duck_ai_lookup_html(html: str, *, query: str, source_url: str = "") -> OnlineLookupResult:
    """Extract one compact answer from a rendered Duck.ai chat page."""

    from agent_runtime.onlineaicheck import parse_duck_ai_html

    parsed = parse_duck_ai_html(html, query=query, search_url=source_url or _duck_ai_url(query))
    return _result(
        query=parsed.query,
        available=parsed.available,
        answer_text=parsed.answer_text,
        source_title=parsed.source_title,
        source_url=parsed.source_url or parsed.search_url,
        error=parsed.error,
        provider=ONLINE_LOOKUP_PROVIDER,
    )


def lookup_duck_ai_answer(
    query: str,
    *,
    timeout_seconds: float = 30.0,
    reuse_browser: bool = True,
    headless: bool = False,
    profile_dir: str | os.PathLike[str] | None = None,
) -> OnlineLookupResult:
    """Query Duck.ai and return one compact online answer.

    Failures are represented as unavailable results so callers can continue
    without weakening validation or pretending that online lookup succeeded.
    """

    clean_query = sanitize_online_lookup_query(query)
    if not clean_query:
        return _result(query="", available=False, error="Online lookup query was empty.")
    try:
        from agent_runtime.onlineaicheck import lookup_duck_ai_answer as lookup_duck_ai_check_answer

        result = lookup_duck_ai_check_answer(
            clean_query,
            timeout_seconds=max(1.0, float(timeout_seconds or 30.0)),
            reuse_browser=reuse_browser,
            headless=headless,
            profile_dir=profile_dir,
        )
    except Exception as exc:
        return _result(query=clean_query, available=False, error=f"Duck.ai lookup failed: {exc}")
    return _result(
        query=result.query,
        available=result.available,
        answer_text=result.answer_text,
        source_title=result.source_title,
        source_url=result.source_url or result.search_url,
        error=result.error,
        provider=ONLINE_LOOKUP_PROVIDER,
    )


# Compatibility for older public/private imports.
def lookup_google_answer(query: str, *, timeout_seconds: float = 6.0) -> OnlineLookupResult:
    return lookup_duck_ai_answer(
        query,
        timeout_seconds=max(6.0, float(timeout_seconds or 6.0)),
    )


def _gemini_grounding_api_key(explicit_api_key: str | None = None) -> str:
    return str(
        explicit_api_key
        or os.getenv("AOR_GEMINI_GROUNDING_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or ""
    ).strip()


def _gemini_grounding_url(*, model: str | None = None, api_version: str | None = None) -> str:
    model_name = str(
        model or os.getenv("AOR_GEMINI_GROUNDING_MODEL") or _DEFAULT_GEMINI_GROUNDING_MODEL
    ).strip()
    api_version_name = str(
        api_version
        or os.getenv("AOR_GEMINI_GROUNDING_API_VERSION")
        or _DEFAULT_GEMINI_API_VERSION
    ).strip()
    safe_model = urllib_parse.quote(
        model_name or _DEFAULT_GEMINI_GROUNDING_MODEL,
        safe="",
    )
    safe_version = urllib_parse.quote(
        api_version_name or _DEFAULT_GEMINI_API_VERSION,
        safe="",
    )
    return _GEMINI_GENERATE_CONTENT_URL.format(version=safe_version, model=safe_model)


def _gemini_grounding_request_body(query: str) -> dict[str, Any]:
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Use Google Search grounding to answer this query compactly. "
                            "Prefer exact commands or facts when the query asks for tool usage. "
                            "Keep the answer under 120 words.\n\n"
                            f"Query: {sanitize_online_lookup_query(query)}"
                        )
                    }
                ],
            }
        ],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 512},
    }


def _extract_gemini_answer_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return ""
    parts: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text = _compact_text(part.get("text"))
                if text:
                    parts.append(text)
    return _compact_text(" ".join(parts))


def _extract_gemini_grounding_source(payload: dict[str, Any]) -> tuple[str, str]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return "", ""
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        metadata = candidate.get("groundingMetadata")
        if not isinstance(metadata, dict):
            continue
        chunks = metadata.get("groundingChunks")
        if not isinstance(chunks, list):
            continue
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            web = chunk.get("web")
            if not isinstance(web, dict):
                continue
            uri = str(web.get("uri") or "").strip()
            title = str(web.get("title") or "").strip()
            if uri.startswith(("http://", "https://")):
                return title, uri
    return "", ""


def parse_gemini_grounding_response(payload: dict[str, Any], *, query: str) -> OnlineLookupResult:
    """Extract one compact answer from a Gemini grounded response."""

    if not isinstance(payload, dict):
        return _result(
            query=query,
            available=False,
            provider=GEMINI_GROUNDING_PROVIDER,
            error="Gemini grounding returned a non-object response.",
        )
    if isinstance(payload.get("error"), dict):
        error_payload = payload["error"]
        message = (
            error_payload.get("message")
            or error_payload.get("status")
            or "Gemini grounding API error."
        )
        return _result(
            query=query,
            available=False,
            provider=GEMINI_GROUNDING_PROVIDER,
            error=f"Gemini grounding error: {message}",
        )
    answer_text = _extract_gemini_answer_text(payload)
    source_title, source_url = _extract_gemini_grounding_source(payload)
    if not answer_text:
        return _result(
            query=query,
            available=False,
            provider=GEMINI_GROUNDING_PROVIDER,
            error="Gemini grounding returned no compact answer text.",
        )
    return _result(
        query=query,
        available=True,
        provider=GEMINI_GROUNDING_PROVIDER,
        answer_text=answer_text,
        source_title=source_title or "Gemini grounding",
        source_url=source_url,
    )


def lookup_gemini_grounding_answer(
    query: str,
    *,
    timeout_seconds: float = 6.0,
    api_key: str | None = None,
    model: str | None = None,
    api_version: str | None = None,
) -> OnlineLookupResult:
    """Query Gemini with Google Search grounding and return one compact answer."""

    clean_query = sanitize_online_lookup_query(query)
    if not clean_query:
        return _result(
            query="",
            available=False,
            provider=GEMINI_GROUNDING_PROVIDER,
            error="Gemini grounding query was empty.",
        )
    resolved_api_key = _gemini_grounding_api_key(api_key)
    if not resolved_api_key:
        return _result(
            query=clean_query,
            available=False,
            provider=GEMINI_GROUNDING_PROVIDER,
            error="Gemini grounding API key is not configured.",
        )
    request = urllib_request.Request(
        _gemini_grounding_url(model=model, api_version=api_version),
        data=json.dumps(_gemini_grounding_request_body(clean_query)).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": resolved_api_key,
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=float(timeout_seconds)) as response:
            raw = response.read(512_000)
            payload = json.loads(raw.decode("utf-8", errors="replace"))
    except urllib_error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(20_000).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        message = _compact_text(body)
        return _result(
            query=clean_query,
            available=False,
            provider=GEMINI_GROUNDING_PROVIDER,
            error=f"HTTP {exc.code} from Gemini grounding. {message}",
        )
    except Exception as exc:
        return _result(
            query=clean_query,
            available=False,
            provider=GEMINI_GROUNDING_PROVIDER,
            error=f"Gemini grounding lookup failed: {exc}",
        )
    return parse_gemini_grounding_response(payload, query=clean_query)


def lookup_online_answer(
    query: str,
    *,
    timeout_seconds: float = 30.0,
    gemini_api_key: str | None = None,
    gemini_model: str | None = None,
    gemini_api_version: str | None = None,
    duck_ai_reuse_browser: bool = True,
    duck_ai_headless: bool = False,
    duck_ai_profile_dir: str | os.PathLike[str] | None = None,
) -> OnlineLookupResult:
    """Query all configured online providers and return one compact combined answer."""

    clean_query = sanitize_online_lookup_query(query)
    if not clean_query:
        return _result(
            query="",
            available=False,
            provider=COMBINED_ONLINE_LOOKUP_PROVIDER,
            error="Online lookup query was empty.",
        )
    duck_ai_result = lookup_duck_ai_answer(
        clean_query,
        timeout_seconds=timeout_seconds,
        reuse_browser=duck_ai_reuse_browser,
        headless=duck_ai_headless,
        profile_dir=duck_ai_profile_dir,
    )
    gemini_result = lookup_gemini_grounding_answer(
        clean_query,
        timeout_seconds=min(6.0, max(1.0, float(timeout_seconds or 30.0))),
        api_key=gemini_api_key,
        model=gemini_model,
        api_version=gemini_api_version,
    )
    return _combine_lookup_results(clean_query, [duck_ai_result, gemini_result])


def online_lookup_requested_from_context(context: dict[str, Any] | None) -> bool:
    payload = dict(context or {})
    if bool(payload.get(ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY)):
        return True
    if isinstance(payload.get(ONLINE_LOOKUP_CONTEXTS_KEY), list):
        return True
    summaries = payload.get("operator_user_macro_summaries")
    if not isinstance(summaries, list):
        return False
    return any(
        isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "checkonline"
        for item in summaries
    )


def _normalized_online_lookup_context(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    payload = {
        "provider": str(raw.get("provider") or ONLINE_LOOKUP_PROVIDER),
        "query": _cap(raw.get("query"), _MAX_QUERY_CHARS),
        "available": bool(raw.get("available")),
        "answer_text": _cap(raw.get("answer_text"), _MAX_ANSWER_CHARS),
        "source_title": _cap(raw.get("source_title"), _MAX_TITLE_CHARS),
        "source_url": _cap(raw.get("source_url"), _MAX_URL_CHARS),
        "fetched_at": _cap(raw.get("fetched_at"), 80),
        "error": _cap(raw.get("error"), _MAX_ERROR_CHARS),
    }
    provider_results = raw.get("provider_results")
    if isinstance(provider_results, list):
        payload["provider_results"] = [
            _normalized_provider_result(item)
            for item in provider_results
            if isinstance(item, dict)
        ][:4]
    for key in ("task_id", "streaming_step_id", "streaming_step_index", "source"):
        if key in raw:
            payload[key] = _cap(raw.get(key), 120)
    return payload


def online_lookup_context_from_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    return _normalized_online_lookup_context(dict(context or {}).get(ONLINE_LOOKUP_CONTEXT_KEY))


def online_lookup_contexts_from_context(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw_items = dict(context or {}).get(ONLINE_LOOKUP_CONTEXTS_KEY)
    if not isinstance(raw_items, list):
        return []
    contexts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_items:
        payload = _normalized_online_lookup_context(raw)
        if payload is None:
            continue
        key = (str(payload.get("query") or ""), str(payload.get("task_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        contexts.append(payload)
    return contexts


def online_lookup_prompt_lines(context: dict[str, Any] | None) -> list[str]:
    lookup = online_lookup_context_from_context(context)
    lookups = online_lookup_contexts_from_context(context)
    if lookup:
        single_key = (str(lookup.get("query") or ""), str(lookup.get("task_id") or ""))
        lookups = [
            item
            for item in lookups
            if (str(item.get("query") or ""), str(item.get("task_id") or "")) != single_key
        ]
    if lookup and lookups:
        return [
            "Online lookup context:",
            json.dumps(
                {"explicit_checkonline": lookup, "online_mode_results": lookups},
                sort_keys=True,
                default=str,
            ),
            "Use these online answers only when relevant to the current task. "
            "If used, cite source_url. If available is false, do not claim online "
            "checking succeeded.",
        ]
    if lookups:
        return [
            "Online mode lookup context:",
            json.dumps(lookups, sort_keys=True, default=str),
            "Use these online answers only when relevant to the current task. "
            "If used, cite source_url. If available is false, do not claim online "
            "checking succeeded.",
        ]
    if not lookup:
        if online_lookup_requested_from_context(context):
            return [
                "User-requested online check context:",
                "Online lookup was requested but no result is available in this planning scope.",
                "Do not claim that online checking succeeded unless a source URL/result "
                "is present.",
            ]
        return []
    label = (
        "Online lookup context:"
        if str(lookup.get("source") or "").strip().lower() == "online_mode"
        else "User-requested online check context:"
    )
    return [
        label,
        json.dumps(lookup, sort_keys=True, default=str),
        "Use this online answer only when it is relevant to the current task. "
        "If used, cite source_url. If available is false, do not claim online "
        "checking succeeded.",
    ]
