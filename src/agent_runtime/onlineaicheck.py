"""Duck.ai lookup support for explicit /checkonlineai requests."""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse

from agent_runtime.operator.user_macros import USER_MACRO_SUMMARY_CONTEXT_KEY

ONLINE_AI_CHECK_CONTEXT_KEY = "agent_online_ai_check_context"
ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY = "agent_online_ai_check_requested"
ONLINE_AI_CHECK_PROVIDER = "duck_ai"

_MAX_QUERY_CHARS = 500
_MAX_ANSWER_CHARS = 5000
_MAX_URL_CHARS = 2000
_MAX_ERROR_CHARS = 300
_DUCK_AI_URL = "https://duck.ai/?origin=funnel_home_website"
_DEFAULT_PROFILE_DIR = "artifacts/playwright-duckai-profile"
_PROFILE_SETUP_SCRIPT = "./setupplaywright.sh"
_DUCK_AI_BROWSER_VIEWPORT = {"width": 800, "height": 600}
_VISIBLE_SCROLLBAR_BROWSER_ARGS = (
    "--window-size=800,600",
    "--start-minimized",
    "--disable-features=OverlayScrollbar,OverlayScrollbars",
)
_VISIBLE_SCROLLBAR_STYLE = """
html,
body {
  overflow: auto !important;
  scrollbar-gutter: stable both-edges !important;
}

html,
body,
* {
  scrollbar-width: auto !important;
  scrollbar-color: rgba(91, 100, 114, 0.86) rgba(226, 232, 240, 0.78) !important;
}

*::-webkit-scrollbar {
  display: block !important;
  width: 16px !important;
  height: 16px !important;
  background: rgba(226, 232, 240, 0.78) !important;
}

*::-webkit-scrollbar-thumb {
  min-height: 44px !important;
  border: 3px solid rgba(226, 232, 240, 0.78) !important;
  border-radius: 999px !important;
  background: rgba(91, 100, 114, 0.86) !important;
}

*::-webkit-scrollbar-track {
  background: rgba(226, 232, 240, 0.78) !important;
}
"""
_CHECKONLINEAI_RE = re.compile(r"(?<!\S)/checkonlineai\b", re.IGNORECASE)
_CHECKONLINEAI_QUOTED_RE = re.compile(
    r'(?<!\S)/checkonlineai\s*("(?:\\.|[^"\\])*")',
    re.IGNORECASE,
)
_TYPEIN_MACRO_RE = re.compile(r"\btypein\s*\"(?:\\.|[^\"\\])*\"", re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(
    r"(?is)<script\b.*?</script>|<style\b.*?</style>|<noscript\b.*?</noscript>"
)
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_ANTI_AUTOMATION_MARKERS = (
    "unfortunately, bots use duckduckgo too",
    "please complete the following challenge",
    "select all squares containing a duck",
    "our systems have detected unusual traffic",
    "unusual traffic from your computer network",
    "before you continue to google search",
    "to continue, please type the characters",
)
_DUCK_AI_BOILERPLATE_LINES = (
    "Duck.ai",
    "New Chat",
    "New Voice Chat",
    "New Image",
    "Settings & More",
    "Get the App",
    "Drop your photos or PDF files",
    "Say hello to Duck.ai",
    "Free and private chats, anonymized by us. No account required.",
    "No AI training on your conversations.",
    "Popular AI models from:",
    "By clicking “Agree and Continue,” you accept Duck.ai’s Privacy Policy and Terms of Service.",
    "Agree and Continue",
    "Anonymized by DuckDuckGo. Zero data retention for this chat. No AI training. Learn more",
    "All chats are private. AI can make mistakes.",
    "Generating response",
    "Tools",
    "Fast",
    "Free",
    "by DuckDuckGo",
    "|",
    "Got It!",
    "How It Works",
    "Your recent chats live here! You can refer to your chats or clear them anytime.",
    "Chats",
    "Copy",
    "Share",
    "Good response",
    "Bad response",
)
_DUCK_AI_SUBMIT_SELECTORS = (
    "button[aria-label*='Send' i]",
    "button[type='submit']",
)
_DUCK_AI_KEYSTROKE_DELAY_MS = 20
_DUCK_AI_CHALLENGE_RE = re.compile(
    r"(unfortunately,\s+bots\s+use\s+duckduckgo\s+too|please\s+complete\s+the\s+following\s+challenge|select\s+all\s+squares\s+containing\s+a\s+duck)",
    re.IGNORECASE,
)
_DUCK_AI_READY_RE = re.compile(
    r"(Ask anything privately|Agree and Continue|Duck\.ai)",
    re.IGNORECASE,
)
_DUCK_AI_GENERATING_RE = re.compile(r"\bGenerating response\b", re.IGNORECASE)
_DUCK_AI_MODEL_LABEL_RE = re.compile(
    r"^(?:"
    r"GPT(?:-\d+(?:\.\d+)?)?(?:\s+\w+)?|"
    r"Claude(?:\s+\d+(?:\.\d+)?)?(?:\s+\w+)*|"
    r"Llama(?:\s+\d+(?:\.\d+)?)?(?:\s+\w+)*|"
    r"Mistral(?:\s+\w+)*|"
    r"o\d+(?:-\w+)*"
    r")\b[\s:.-]*",
    re.IGNORECASE,
)
_DUCK_AI_ANSWER_TAIL_RE = re.compile(
    r"\b(?:Related Searches|All chats are private|AI can make mistakes)\b.*$",
    re.IGNORECASE,
)
_PLAYWRIGHT_CLOSED_RE = re.compile(
    r"(target page, context or browser has been closed|browser has been closed|context has been closed|target closed)",
    re.IGNORECASE,
)

OnlineAiStatusCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class OnlineAiCheckResult:
    """One compact Duck.ai result suitable for prompt context."""

    provider: str
    query: str
    available: bool
    answer_text: str = ""
    source_title: str = ""
    source_url: str = ""
    search_url: str = ""
    fetched_at: str = ""
    status: str = "failed"
    error: str = ""

    def to_context(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "query": self.query,
            "available": self.available,
            "answer_text": self.answer_text,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "search_url": self.search_url,
            "fetched_at": self.fetched_at,
            "status": self.status,
            "error": self.error,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cap(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def sanitize_online_ai_check_query(query: str) -> str:
    """Return the Duck.ai query for explicit macro text.

    Quoted ``/checkonlineai "..."`` uses the quoted text exactly. Unquoted
    ``/checkonlineai`` keeps the full prompt context and only redacts private
    deterministic input macros.
    """

    raw = str(query or "")
    quoted_match = _CHECKONLINEAI_QUOTED_RE.search(raw)
    if quoted_match:
        try:
            quoted_value = json.loads(quoted_match.group(1))
        except json.JSONDecodeError:
            quoted_value = ""
        if isinstance(quoted_value, str) and quoted_value.strip():
            return _cap(_compact_text(quoted_value), _MAX_QUERY_CHARS)
    text = _TYPEIN_MACRO_RE.sub(" ", raw)
    return _cap(_compact_text(text), _MAX_QUERY_CHARS)


def _duck_ai_url(query: str = "") -> str:
    clean_query = sanitize_online_ai_check_query(query)
    if not clean_query:
        return _DUCK_AI_URL
    params = urllib_parse.urlencode({"origin": "funnel_home_website", "q": clean_query})
    return f"https://duck.ai/?{params}"


# Compatibility for older private tests/imports.
def _google_search_url(query: str) -> str:
    return _duck_ai_url(query)


def resolve_online_ai_check_profile_dir(profile_dir: str | os.PathLike[str] | None = None) -> Path:
    raw = str(profile_dir or os.getenv("AOR_ONLINE_AI_CHECK_PROFILE_DIR") or _DEFAULT_PROFILE_DIR)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def online_ai_check_profile_ready(profile_dir: str | os.PathLike[str] | None = None) -> bool:
    path = resolve_online_ai_check_profile_dir(profile_dir)
    if not path.is_dir():
        return False
    cookie_paths = [
        path / "Default" / "Cookies",
        path / "Default" / "Network" / "Cookies",
    ]
    cookie_paths.extend(candidate for candidate in path.glob("**/Cookies") if candidate.is_file())
    return any(candidate.is_file() and candidate.stat().st_size > 0 for candidate in cookie_paths)


def online_ai_check_profile_setup_message(profile_dir: str | os.PathLike[str] | None = None) -> str:
    path = resolve_online_ai_check_profile_dir(profile_dir)
    return (
        f"Duck.ai browser profile is not set up at {path}. "
        f"Run {_PROFILE_SETUP_SCRIPT} on the server once if you want to persist Duck.ai "
        "onboarding or human-challenge state."
    )


def _result(
    *,
    query: str,
    available: bool,
    answer_text: str = "",
    source_url: str = "",
    search_url: str = "",
    status: str = "failed",
    error: str = "",
) -> OnlineAiCheckResult:
    return OnlineAiCheckResult(
        provider=ONLINE_AI_CHECK_PROVIDER,
        query=_cap(_compact_text(query), _MAX_QUERY_CHARS),
        available=bool(available),
        answer_text=_cap(_compact_text(answer_text), _MAX_ANSWER_CHARS),
        source_title="Duck.ai" if available else "",
        source_url=_cap(_compact_text(source_url), _MAX_URL_CHARS),
        search_url=_cap(_compact_text(search_url), _MAX_URL_CHARS),
        fetched_at=_now_iso(),
        status=_cap(_compact_text(status), 80) or ("completed" if available else "failed"),
        error=_cap(_compact_text(error), _MAX_ERROR_CHARS),
    )


def _strip_html(value: str) -> str:
    from html import unescape

    text = _SCRIPT_STYLE_RE.sub(" ", str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    return _compact_text(unescape(text))


def _strip_html_lines(value: str) -> list[str]:
    from html import unescape

    text = _SCRIPT_STYLE_RE.sub(" ", str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>|</article>|</section>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    lines = [_compact_text(unescape(line)) for line in text.splitlines()]
    return [line for line in lines if line]


def _is_duck_ai_boilerplate_line(line: str) -> bool:
    compact = _compact_text(line)
    if not compact:
        return True
    if _DUCK_AI_MODEL_LABEL_RE.fullmatch(compact):
        return True
    if compact in _DUCK_AI_BOILERPLATE_LINES:
        return True
    if compact.lower().replace(" . ", ". ") in {
        "all chats are private. ai can make mistakes.",
        "related searches",
    }:
        return True
    if compact.startswith("CTRL +"):
        return True
    if compact.startswith("AI can make mistakes"):
        return True
    return False


def _clean_duck_ai_answer_line(line: str) -> str:
    compact = _compact_text(line)
    compact = _DUCK_AI_MODEL_LABEL_RE.sub("", compact).strip()
    compact = _DUCK_AI_ANSWER_TAIL_RE.sub("", compact).strip()
    return _compact_text(compact)


def extract_duck_ai_answer_text(html: str, *, query: str = "") -> str:
    """Best-effort extraction of Duck.ai assistant text from a rendered chat DOM."""

    clean_query = _compact_text(query)
    lines = _strip_html_lines(html)
    if not lines:
        return ""

    start_index = -1
    if clean_query:
        lowered_query = clean_query.lower()
        for index, line in enumerate(lines):
            if _compact_text(line).lower() == lowered_query:
                start_index = index
        if start_index < 0:
            for index, line in enumerate(lines):
                if lowered_query and lowered_query in _compact_text(line).lower():
                    start_index = index

    candidate_lines = lines[start_index + 1 :] if start_index >= 0 else lines
    answer_lines: list[str] = []
    for line in candidate_lines:
        raw_compact = _compact_text(line)
        has_answer_tail = bool(_DUCK_AI_ANSWER_TAIL_RE.search(raw_compact))
        compact = _clean_duck_ai_answer_line(line)
        if not compact:
            if has_answer_tail:
                break
            continue
        if clean_query and compact.lower() == clean_query.lower():
            continue
        if _is_duck_ai_boilerplate_line(compact):
            if has_answer_tail:
                break
            continue
        if _DUCK_AI_CHALLENGE_RE.search(compact):
            break
        answer_lines.append(compact)
        if has_answer_tail:
            break

    answer = _compact_text(" ".join(answer_lines))
    if not answer:
        return ""
    if start_index >= 0 or len(answer) >= 20:
        return _cap(answer, _MAX_ANSWER_CHARS)
    return ""


# Compatibility for older private tests/imports.
def extract_google_ai_overview_text(html: str) -> str:
    return extract_duck_ai_answer_text(html)


def parse_duck_ai_html(
    html: str,
    *,
    query: str,
    search_url: str = "",
) -> OnlineAiCheckResult:
    """Parse a rendered Duck.ai page and return assistant text only."""

    text = _strip_html(html)
    lower = text.lower()
    if any(marker in lower for marker in _ANTI_AUTOMATION_MARKERS):
        return _result(
            query=query,
            available=False,
            search_url=search_url,
            error="Duck.ai returned a human-verification challenge or consent page.",
        )
    answer = extract_duck_ai_answer_text(html, query=query)
    if not answer:
        return _result(
            query=query,
            available=False,
            search_url=search_url,
            error="No Duck.ai answer text could be extracted.",
        )
    return _result(
        query=query,
        available=True,
        answer_text=answer,
        source_url=search_url,
        search_url=search_url,
        status="completed",
    )


# Compatibility for older public/private imports.
def parse_google_ai_overview_html(
    html: str,
    *,
    query: str,
    search_url: str = "",
) -> OnlineAiCheckResult:
    return parse_duck_ai_html(html, query=query, search_url=search_url)


def _duck_ai_challenge_result(
    *,
    query: str,
    search_url: str,
    browser_left_open: bool,
) -> OnlineAiCheckResult:
    message = "Duck.ai returned a human-verification challenge."
    if browser_left_open:
        message += " The browser window was left open; complete the challenge there and retry."
    return _result(
        query=query,
        available=False,
        search_url=search_url,
        error=message,
    )


def _duck_ai_result_is_challenge(result: OnlineAiCheckResult | None) -> bool:
    return bool(
        result is not None
        and not result.available
        and "human-verification challenge" in str(result.error or "").lower()
    )


def _safe_locator_count(locator: Any) -> int | None:
    try:
        return int(locator.count())
    except Exception:
        return None


def _safe_locator_visible(locator: Any, *, timeout_ms: int) -> bool:
    try:
        return bool(locator.is_visible(timeout=timeout_ms))
    except TypeError:
        try:
            return bool(locator.is_visible())
        except Exception:
            return False
    except Exception:
        return False


def _click_visible_locator(locator: Any, *, timeout_ms: int) -> bool:
    candidate = getattr(locator, "first", locator)
    count = _safe_locator_count(candidate)
    if count == 0:
        return False
    if not _safe_locator_visible(candidate, timeout_ms=min(timeout_ms, 1000)):
        return False
    try:
        candidate.click(timeout=timeout_ms)
        return True
    except TypeError:
        try:
            candidate.click()
            return True
        except Exception:
            return False
    except Exception:
        return False


def _wait_for_duck_ai_ready(page: Any, *, timeout_ms: int) -> None:
    try:
        page.get_by_text(_DUCK_AI_READY_RE).first.wait_for(timeout=timeout_ms)
    except Exception:
        return


def _page_has_duck_ai_challenge(page: Any) -> bool:
    try:
        content = str(page.content() or "")
    except Exception:
        return False
    return bool(_DUCK_AI_CHALLENGE_RE.search(_strip_html(content)))


def _apply_visible_scrollbar_styles(page: Any) -> bool:
    try:
        page.add_style_tag(content=_VISIBLE_SCROLLBAR_STYLE)
        return True
    except Exception:
        return False


def _minimize_browser_window(page: Any) -> bool:
    try:
        context = getattr(page, "context", None)
        new_cdp_session = getattr(context, "new_cdp_session", None)
        if not callable(new_cdp_session):
            return False
        session = new_cdp_session(page)
        window = session.send("Browser.getWindowForTarget")
        window_id = window.get("windowId") if isinstance(window, dict) else None
        if window_id is None:
            return False
        session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "minimized"}},
        )
        return True
    except Exception:
        return False


def prepare_duck_ai_conversation(
    page: Any,
    *,
    timeout_ms: int = 3000,
    status_callback: OnlineAiStatusCallback | None = None,
) -> bool:
    """Accept Duck.ai onboarding and dismiss lightweight overlays when present."""

    _apply_visible_scrollbar_styles(page)
    clicked = False
    for label in ("Agree and Continue", "Got It!"):
        try:
            locator = page.get_by_role("button", name=label)
        except Exception:
            continue
        if _click_visible_locator(locator, timeout_ms=min(timeout_ms, 2000)):
            clicked = True
            _emit_status(
                status_callback,
                "preparing_context",
                f"Handled Duck.ai onboarding control: {label}",
            )
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
    except Exception:
        pass
    try:
        page.wait_for_timeout(min(timeout_ms, 500))
    except Exception:
        pass
    _apply_visible_scrollbar_styles(page)
    return clicked


def _last_locator(locator: Any) -> Any:
    candidate = getattr(locator, "last", locator)
    if callable(candidate):
        try:
            return candidate()
        except Exception:
            return locator
    return candidate


def _click_text_input(locator: Any, *, timeout_ms: int) -> bool:
    try:
        locator.click(timeout=timeout_ms)
        return True
    except TypeError:
        try:
            locator.click()
            return True
        except Exception:
            return False
    except Exception:
        return False


def _press_text_input_key(locator: Any, key: str, *, timeout_ms: int) -> bool:
    try:
        locator.press(key, timeout=timeout_ms)
        return True
    except TypeError:
        try:
            locator.press(key)
            return True
        except Exception:
            return False
    except Exception:
        return False


def _type_text_input(locator: Any, page: Any, text: str, *, timeout_ms: int) -> bool:
    text = str(text or "")
    if not text:
        return False

    candidate = _last_locator(locator)
    if not _click_text_input(candidate, timeout_ms=timeout_ms):
        return False

    # Keep this human-shaped: focus, keyboard-clear, then emit sequential key events.
    _press_text_input_key(candidate, "Control+A", timeout_ms=timeout_ms)
    _press_text_input_key(candidate, "Backspace", timeout_ms=timeout_ms)

    press_sequentially = getattr(candidate, "press_sequentially", None)
    if callable(press_sequentially):
        try:
            press_sequentially(
                text,
                delay=_DUCK_AI_KEYSTROKE_DELAY_MS,
                timeout=timeout_ms,
            )
            return True
        except TypeError:
            try:
                press_sequentially(text, delay=_DUCK_AI_KEYSTROKE_DELAY_MS)
                return True
            except Exception:
                pass
        except Exception:
            pass

    type_method = getattr(candidate, "type", None)
    if callable(type_method):
        try:
            type_method(text, delay=_DUCK_AI_KEYSTROKE_DELAY_MS, timeout=timeout_ms)
            return True
        except TypeError:
            try:
                type_method(text, delay=_DUCK_AI_KEYSTROKE_DELAY_MS)
                return True
            except Exception:
                pass
        except Exception:
            pass

    keyboard = getattr(page, "keyboard", None)
    keyboard_type = getattr(keyboard, "type", None)
    if callable(keyboard_type):
        try:
            keyboard_type(text, delay=_DUCK_AI_KEYSTROKE_DELAY_MS)
            return True
        except TypeError:
            try:
                keyboard_type(text)
                return True
            except Exception:
                return False
        except Exception:
            return False
    return False


def _submit_duck_ai_query(page: Any, query: str, *, timeout_ms: int) -> bool:
    for selector in ("textarea", "[contenteditable='true']", "[role='textbox']"):
        try:
            locator = page.locator(selector)
            if not _type_text_input(locator, page, query, timeout_ms=timeout_ms):
                continue
            break
        except Exception:
            continue
    else:
        return False
    if _press_text_input_key(_last_locator(locator), "Enter", timeout_ms=timeout_ms):
        return True
    for selector in _DUCK_AI_SUBMIT_SELECTORS:
        try:
            if _click_visible_locator(page.locator(selector), timeout_ms=timeout_ms):
                return True
        except Exception:
            continue
    try:
        page.keyboard.press("Enter")
        return True
    except Exception:
        return False


def _wait_for_duck_ai_answer(
    page: Any,
    *,
    query: str,
    timeout_ms: int,
) -> OnlineAiCheckResult | None:
    deadline = time.monotonic() + max(1.0, timeout_ms / 1000)
    last_answer = ""
    stable_answer = ""
    stable_answer_polls = 0
    while time.monotonic() < deadline:
        try:
            html = page.content()
            current_url = str(getattr(page, "url", "") or _duck_ai_url(query))
        except Exception:
            html = ""
            current_url = _duck_ai_url(query)
        parsed = parse_duck_ai_html(html, query=query, search_url=current_url)
        if _page_has_duck_ai_challenge(page):
            return parsed
        if parsed.available and parsed.answer_text:
            last_answer = parsed.answer_text
            if not _DUCK_AI_GENERATING_RE.search(_strip_html(html)):
                if parsed.answer_text == stable_answer:
                    stable_answer_polls += 1
                else:
                    stable_answer = parsed.answer_text
                    stable_answer_polls = 1
                if stable_answer_polls >= 10:
                    return parsed
            else:
                stable_answer = ""
                stable_answer_polls = 0
        try:
            page.wait_for_timeout(500)
        except Exception:
            time.sleep(0.5)
    if last_answer:
        return _result(
            query=query,
            available=True,
            answer_text=last_answer,
            source_url=str(getattr(page, "url", "") or _duck_ai_url(query)),
            search_url=str(getattr(page, "url", "") or _duck_ai_url(query)),
            status="completed",
        )
    return None


# Compatibility for older public/private imports.
def expand_google_ai_overview_section(
    page: Any,
    *,
    timeout_ms: int = 3000,
    status_callback: OnlineAiStatusCallback | None = None,
) -> bool:
    return prepare_duck_ai_conversation(
        page,
        timeout_ms=timeout_ms,
        status_callback=status_callback,
    )


def _emit_status(callback: OnlineAiStatusCallback | None, state: str, message: str) -> None:
    if callback is None:
        return
    try:
        callback(state, message)
    except Exception:
        return


class _GoogleAiOverviewWorker:
    """Lazy persistent Playwright browser used by /checkonlineai Duck.ai lookups."""

    def __init__(self) -> None:
        self._thread_lock = threading.Lock()
        self._jobs: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._playwright: Any = None
        self._context: Any = None
        self._profile_dir: Path | None = None
        self._headless: bool | None = None

    @staticmethod
    def _looks_like_closed_browser_error(exc: BaseException) -> bool:
        return bool(_PLAYWRIGHT_CLOSED_RE.search(str(exc or "")))

    @staticmethod
    def _context_looks_alive(context: Any) -> bool:
        if context is None:
            return False
        try:
            pages = getattr(context, "pages", None)
            if pages is not None:
                list(pages)
        except Exception:
            return False
        return True

    def _discard_context(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        self._profile_dir = None
        self._headless = None

    def _ensure_context(
        self,
        *,
        profile_dir: Path,
        headless: bool,
        callback: OnlineAiStatusCallback | None,
    ) -> Any:
        if (
            self._context is not None
            and self._profile_dir == profile_dir
            and self._headless == headless
        ):
            if self._context_looks_alive(self._context):
                return self._context
            _emit_status(
                callback,
                "launching_browser",
                "Discarding closed Duck.ai browser context.",
            )
            self._discard_context()
        if self._context is not None:
            self._discard_context()
        _emit_status(
            callback,
            "launching_browser",
            "Launching browser for Duck.ai lookup.",
        )
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError(
                "Playwright is not installed. Install the project dependency and run "
                "`python -m playwright install chromium`."
            ) from exc
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                locale="en-US",
                viewport=dict(_DUCK_AI_BROWSER_VIEWPORT),
                screen=dict(_DUCK_AI_BROWSER_VIEWPORT),
                args=list(_VISIBLE_SCROLLBAR_BROWSER_ARGS),
                bypass_csp=True,
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
        except Exception as exc:
            self._playwright.stop()
            self._playwright = None
            self._context = None
            raise RuntimeError(
                "Chromium is not available for Playwright. Run "
                "`python -m playwright install chromium`."
            ) from exc
        self._profile_dir = profile_dir
        self._headless = headless
        return self._context

    def close(self) -> None:
        self._discard_context()
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _lookup_in_current_thread(
        self,
        *,
        clean_query: str,
        timeout_seconds: float,
        headless: bool,
        profile_path: Path,
        callback: OnlineAiStatusCallback | None,
    ) -> OnlineAiCheckResult:
        search_url = _duck_ai_url(clean_query)
        timeout_ms = max(1000, int(float(timeout_seconds or 20.0) * 1000))
        for attempt in range(2):
            page = None
            leave_page_open = False
            try:
                context = self._ensure_context(
                    profile_dir=profile_path,
                    headless=headless,
                    callback=callback,
                )
                _emit_status(callback, "fetching_context", "Opening Duck.ai.")
                page = context.new_page()
                _minimize_browser_window(page)
                page.goto(_DUCK_AI_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                _apply_visible_scrollbar_styles(page)
                _minimize_browser_window(page)
                _wait_for_duck_ai_ready(page, timeout_ms=min(timeout_ms, 15000))
                _emit_status(
                    callback,
                    "preparing_context",
                    "Preparing Duck.ai chat session.",
                )
                prepare_duck_ai_conversation(
                    page,
                    timeout_ms=min(timeout_ms, 5000),
                    status_callback=callback,
                )
                if _page_has_duck_ai_challenge(page):
                    leave_page_open = not headless
                    if leave_page_open:
                        _emit_status(
                            callback,
                            "failed",
                            "Duck.ai needs human verification; leaving the browser open.",
                        )
                    return _duck_ai_challenge_result(
                        query=clean_query,
                        search_url=page.url or search_url,
                        browser_left_open=leave_page_open,
                    )
                _emit_status(callback, "fetching_context", "Submitting query to Duck.ai.")
                if not _submit_duck_ai_query(page, clean_query, timeout_ms=min(timeout_ms, 5000)):
                    return _result(
                        query=clean_query,
                        available=False,
                        search_url=page.url or search_url,
                        error="Duck.ai input box was not available.",
                    )
                _emit_status(callback, "extracting_context", "Waiting for Duck.ai answer.")
                parsed = _wait_for_duck_ai_answer(
                    page,
                    query=clean_query,
                    timeout_ms=timeout_ms,
                )
                if parsed is not None:
                    if _duck_ai_result_is_challenge(parsed) or _page_has_duck_ai_challenge(page):
                        leave_page_open = not headless
                        if leave_page_open:
                            _emit_status(
                                callback,
                                "failed",
                                "Duck.ai needs human verification; leaving the browser open.",
                            )
                        return _duck_ai_challenge_result(
                            query=clean_query,
                            search_url=page.url or search_url,
                            browser_left_open=leave_page_open,
                        )
                    return parsed
                return _result(
                    query=clean_query,
                    available=False,
                    search_url=page.url or search_url,
                    status="timeout",
                    error="Duck.ai answer did not finish before the lookup timeout.",
                )
            except Exception as exc:
                if self._looks_like_closed_browser_error(exc):
                    self._discard_context()
                    if attempt == 0:
                        _emit_status(
                            callback,
                            "launching_browser",
                            "Duck.ai browser was closed; reopening and retrying once.",
                        )
                        continue
                return _result(
                    query=clean_query,
                    available=False,
                    search_url=search_url,
                    error=str(exc),
                )
            finally:
                if page is not None and not leave_page_open:
                    try:
                        page.close()
                    except Exception:
                        pass
        return _result(
            query=clean_query,
            available=False,
            search_url=search_url,
            error="Duck.ai browser was closed before the lookup could complete.",
        )

    def _ensure_worker_thread(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._playwright = None
            self._context = None
            self._profile_dir = None
            self._headless = None
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="online-ai-check-worker",
                daemon=True,
            )
            self._thread.start()

    def _worker_loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                self.close()
                return
            result: OnlineAiCheckResult
            try:
                result = self._lookup_in_current_thread(
                    clean_query=str(job["clean_query"]),
                    timeout_seconds=float(job["timeout_seconds"]),
                    headless=bool(job["headless"]),
                    profile_path=Path(job["profile_path"]),
                    callback=job.get("callback"),
                )
            except BaseException as exc:
                result = _result(
                    query=str(job.get("clean_query") or ""),
                    available=False,
                    search_url=_duck_ai_url(str(job.get("clean_query") or "")),
                    error=str(exc),
                )
            job["result"] = result
            done = job.get("done")
            if isinstance(done, threading.Event):
                done.set()

    def lookup_once(
        self,
        query: str,
        *,
        timeout_seconds: float,
        headless: bool,
        profile_dir: str | os.PathLike[str] | None,
        callback: OnlineAiStatusCallback | None,
    ) -> OnlineAiCheckResult:
        clean_query = sanitize_online_ai_check_query(query)
        if not clean_query:
            return _result(
                query=clean_query,
                available=False,
                error="Empty online AI lookup query.",
            )
        profile_path = resolve_online_ai_check_profile_dir(profile_dir)
        try:
            return self._lookup_in_current_thread(
                clean_query=clean_query,
                timeout_seconds=timeout_seconds,
                headless=headless,
                profile_path=profile_path,
                callback=callback,
            )
        finally:
            self.close()

    def lookup(
        self,
        query: str,
        *,
        timeout_seconds: float,
        headless: bool,
        profile_dir: str | os.PathLike[str] | None,
        callback: OnlineAiStatusCallback | None,
    ) -> OnlineAiCheckResult:
        clean_query = sanitize_online_ai_check_query(query)
        if not clean_query:
            return _result(
                query=clean_query,
                available=False,
                error="Empty online AI lookup query.",
            )
        search_url = _duck_ai_url(clean_query)
        profile_path = resolve_online_ai_check_profile_dir(profile_dir)
        self._ensure_worker_thread()
        done = threading.Event()
        job: dict[str, Any] = {
            "clean_query": clean_query,
            "timeout_seconds": timeout_seconds,
            "headless": headless,
            "profile_path": profile_path,
            "callback": callback,
            "done": done,
        }
        self._jobs.put(job)
        wait_seconds = max(5.0, float(timeout_seconds or 20.0) + 15.0)
        if not done.wait(wait_seconds):
            return _result(
                query=clean_query,
                available=False,
                search_url=search_url,
                status="timeout",
                error="Duck.ai lookup timed out in the browser worker.",
            )
        result = job.get("result")
        if isinstance(result, OnlineAiCheckResult):
            return result
        return _result(
            query=clean_query,
            available=False,
            search_url=search_url,
            error="Duck.ai browser worker did not return a result.",
        )


_WORKER = _GoogleAiOverviewWorker()


def lookup_duck_ai_answer(
    query: str,
    *,
    timeout_seconds: float = 120.0,
    reuse_browser: bool = True,
    headless: bool = False,
    profile_dir: str | os.PathLike[str] | None = None,
    status_callback: OnlineAiStatusCallback | None = None,
) -> OnlineAiCheckResult:
    """Lookup Duck.ai answer text with a Playwright browser."""

    clean_query = sanitize_online_ai_check_query(query)
    _emit_status(status_callback, "init", "Initializing Duck.ai lookup.")
    if reuse_browser:
        result = _WORKER.lookup(
            clean_query,
            timeout_seconds=timeout_seconds,
            headless=headless,
            profile_dir=profile_dir,
            callback=status_callback,
        )
    else:
        result = _GoogleAiOverviewWorker().lookup_once(
            clean_query,
            timeout_seconds=timeout_seconds,
            headless=headless,
            profile_dir=profile_dir,
            callback=status_callback,
        )
    _emit_status(
        status_callback,
        "completed" if result.available else "failed",
        "Duck.ai lookup completed." if result.available else result.error,
    )
    return result


# Compatibility for older public/private imports.
def lookup_google_ai_overview(
    query: str,
    *,
    timeout_seconds: float = 120.0,
    reuse_browser: bool = True,
    headless: bool = False,
    profile_dir: str | os.PathLike[str] | None = None,
    status_callback: OnlineAiStatusCallback | None = None,
) -> OnlineAiCheckResult:
    return lookup_duck_ai_answer(
        query,
        timeout_seconds=timeout_seconds,
        reuse_browser=reuse_browser,
        headless=headless,
        profile_dir=profile_dir,
        status_callback=status_callback,
    )


def online_ai_check_requested_from_context(context: dict[str, Any] | None) -> bool:
    payload = dict(context or {})
    if bool(payload.get(ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY)):
        return True
    summaries = payload.get(USER_MACRO_SUMMARY_CONTEXT_KEY)
    return any(
        isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "checkonlineai"
        for item in list(summaries or [])
    )


def online_ai_check_context_from_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    raw = dict(context or {}).get(ONLINE_AI_CHECK_CONTEXT_KEY)
    if not isinstance(raw, dict):
        return None
    payload = {
        "provider": _cap(raw.get("provider") or ONLINE_AI_CHECK_PROVIDER, 120),
        "query": _cap(raw.get("query"), _MAX_QUERY_CHARS),
        "available": bool(raw.get("available")),
        "answer_text": _cap(raw.get("answer_text"), _MAX_ANSWER_CHARS),
        "source_title": _cap(raw.get("source_title"), 240),
        "source_url": _cap(raw.get("source_url"), _MAX_URL_CHARS),
        "search_url": _cap(raw.get("search_url"), _MAX_URL_CHARS),
        "fetched_at": _cap(raw.get("fetched_at"), 80),
        "status": _cap(raw.get("status"), 80),
        "error": _cap(raw.get("error"), _MAX_ERROR_CHARS),
    }
    if not payload["query"] and not payload["answer_text"] and not payload["error"]:
        return None
    return payload


def online_ai_check_prompt_lines(context: dict[str, Any] | None) -> list[str]:
    lookup = online_ai_check_context_from_context(context)
    if not lookup:
        if online_ai_check_requested_from_context(context):
            return [
                "User-requested Duck.ai context:",
                "Duck.ai lookup was requested but no result is available in this planning scope.",
                "Do not claim that Duck.ai checking succeeded unless a result/source is present.",
            ]
        return []
    return [
        "User-requested Duck.ai context:",
        json.dumps(lookup, sort_keys=True, default=str),
        "Use this Duck.ai text only when relevant. If used, cite source_url or search_url. "
        "If available is false, do not claim online AI checking succeeded.",
    ]
