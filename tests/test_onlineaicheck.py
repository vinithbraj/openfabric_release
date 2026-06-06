from __future__ import annotations

import sys
import threading
from types import ModuleType, SimpleNamespace

from agent_runtime import onlineaicheck
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.onlineaicheck import (
    ONLINE_AI_CHECK_CONTEXT_KEY,
    OnlineAiCheckResult,
    lookup_duck_ai_answer,
    online_ai_check_profile_ready,
    online_ai_check_profile_setup_message,
    online_ai_check_prompt_lines,
    online_ai_check_requested_from_context,
    parse_duck_ai_html,
    prepare_duck_ai_conversation,
    resolve_online_ai_check_profile_dir,
    sanitize_online_ai_check_query,
)
from agent_runtime.operator.user_macros import USER_MACRO_SUMMARY_CONTEXT_KEY


class _FakeLocator:
    def __init__(self, *, visible: bool = True, count: int = 1) -> None:
        self.clicked = False
        self.waited = False
        self.visible = visible
        self.count_value = count

    @property
    def first(self) -> "_FakeLocator":
        return self

    def count(self) -> int:
        return self.count_value

    def is_visible(self, *, timeout: int | None = None) -> bool:
        return self.visible

    def click(self, *, timeout: int | None = None) -> None:
        self.clicked = True

    def wait_for(self, *, timeout: int | None = None) -> None:
        self.waited = True


class _FakeButtonPage:
    def __init__(
        self,
        *,
        selector_locator: _FakeLocator,
        text_locator: _FakeLocator | None = None,
    ) -> None:
        self.selector_locator = selector_locator
        self.text_locator = text_locator or _FakeLocator(visible=False, count=0)
        self.load_state_waited = False
        self.timeout_waited = False
        self.style_tags: list[str] = []

    def get_by_role(self, role: str, name: str) -> _FakeLocator:
        if role == "button" and name == "Agree and Continue":
            return self.selector_locator
        return _FakeLocator(visible=False, count=0)

    def get_by_text(self, pattern: object) -> _FakeLocator:
        return self.text_locator

    def content(self) -> str:
        return "<html><body><button>Agree and Continue</button></body></html>"

    def wait_for_load_state(self, state: str, *, timeout: int | None = None) -> None:
        self.load_state_waited = True

    def wait_for_timeout(self, timeout: int) -> None:
        self.timeout_waited = True

    def add_style_tag(self, *, content: str) -> None:
        self.style_tags.append(content)


class _FakeDuckInput:
    def __init__(self, page: "_FakeDuckPage") -> None:
        self.page = page

    @property
    def last(self) -> "_FakeDuckInput":
        return self

    def click(self, *, timeout: int | None = None) -> None:
        self.page.focused = True

    def fill(self, query: str, *, timeout: int | None = None) -> None:
        raise AssertionError("Duck.ai query input should use keystrokes, not fill()")

    def press_sequentially(
        self,
        query: str,
        *,
        delay: int | None = None,
        timeout: int | None = None,
    ) -> None:
        if not self.page.focused:
            raise AssertionError("Duck.ai query input was typed before focus")
        self.page.query += query
        self.page.typed_query += query

    def press(self, key: str, *, timeout: int | None = None) -> None:
        if key in {"Control+A", "Meta+A"}:
            self.page.selected_all = True
            return
        if key == "Backspace":
            if self.page.selected_all:
                self.page.query = ""
                self.page.selected_all = False
            return
        if key == "Enter":
            self.page.submitted = True


class _FakeDuckPage:
    def __init__(self) -> None:
        self.url = "https://duck.ai/?origin=funnel_home_website"
        self.query = ""
        self.typed_query = ""
        self.focused = False
        self.selected_all = False
        self.submitted = False
        self.closed = False
        self.style_tags: list[str] = []

    def goto(self, url: str, *, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.url = url

    def get_by_text(self, pattern: object) -> _FakeLocator:
        return _FakeLocator()

    def get_by_role(self, role: str, name: str) -> _FakeLocator:
        return _FakeLocator(visible=False, count=0)

    def locator(self, selector: str) -> _FakeDuckInput:
        return _FakeDuckInput(self)

    def wait_for_load_state(self, state: str, *, timeout: int | None = None) -> None:
        return None

    def wait_for_timeout(self, timeout: int) -> None:
        return None

    def add_style_tag(self, *, content: str) -> None:
        self.style_tags.append(content)

    def content(self) -> str:
        if not self.submitted:
            return "<html><body>Duck.ai Ask anything privately</body></html>"
        return (
            f"<html><body><div>{self.query}</div>"
            "<div>The Visual Studio Code process name on Linux is usually code.</div>"
            "</body></html>"
        )

    def close(self) -> None:
        self.closed = True


class _FakeDuckChallengePage(_FakeDuckPage):
    def content(self) -> str:
        return "<html><body>Unfortunately, bots use DuckDuckGo too.</body></html>"


class _ClosedDuckContext:
    def new_page(self) -> _FakeDuckPage:
        raise RuntimeError("BrowserContext.new_page: Target page, context or browser has been closed")


class _HealthyDuckContext:
    @property
    def pages(self) -> list[object]:
        return []

    def __init__(self, page: _FakeDuckPage | None = None) -> None:
        self.page = page or _FakeDuckPage()

    def new_page(self) -> _FakeDuckPage:
        return self.page


class _FakeChromiumLauncher:
    def __init__(self, context: object) -> None:
        self.context = context
        self.launch_kwargs: dict[str, object] = {}

    def launch_persistent_context(self, **kwargs: object) -> object:
        self.launch_kwargs = dict(kwargs)
        return self.context


class _FakePlaywrightRuntime:
    def __init__(self, context: object) -> None:
        self.chromium = _FakeChromiumLauncher(context)
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeSyncPlaywrightFactory:
    def __init__(self, runtime: _FakePlaywrightRuntime) -> None:
        self.runtime = runtime

    def start(self) -> _FakePlaywrightRuntime:
        return self.runtime


class _FakeCdpSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []

    def send(self, method: str, params: object | None = None) -> dict[str, int]:
        self.calls.append((method, params))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 42}
        return {}


class _FakeCdpContext:
    def __init__(self, session: _FakeCdpSession) -> None:
        self.session = session

    def new_cdp_session(self, page: object) -> _FakeCdpSession:
        _ = page
        return self.session


class _FakeCdpPage:
    def __init__(self, session: _FakeCdpSession) -> None:
        self.context = _FakeCdpContext(session)


def test_online_ai_check_extracts_duck_ai_text() -> None:
    html = """
    <html><body>
      <div>what command lists free GPU RAM?</div>
      <div>Use nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits.</div>
    </body></html>
    """

    result = parse_duck_ai_html(
        html,
        query="what command lists free GPU RAM?",
        search_url="https://duck.ai/?origin=funnel_home_website",
    )

    assert result.available is True
    assert "nvidia-smi --query-gpu" in result.answer_text
    assert result.source_title == "Duck.ai"
    assert result.source_url.startswith("https://duck.ai")


def test_online_ai_check_strips_duck_ai_chrome_from_short_answer() -> None:
    html = """
    <html><body>
      <div>what is 2 plus 2? answer briefly</div>
      <div>
        GPT-5 mini
        4
        Related Searches
        basic arithmetic addition
        2+2 equals
        All chats are private . AI can make mistakes.
      </div>
    </body></html>
    """

    result = parse_duck_ai_html(html, query="what is 2 plus 2? answer briefly")

    assert result.available is True
    assert result.answer_text == "4"


def test_online_ai_check_keeps_short_duck_ai_answer_after_query() -> None:
    html = """
    <html><body>
      <div>is sudo required? answer yes or no</div>
      <div>Yes.</div>
    </body></html>
    """

    result = parse_duck_ai_html(html, query="is sudo required? answer yes or no")

    assert result.available is True
    assert result.answer_text == "Yes."


def test_online_ai_check_removes_duck_ai_model_label_from_answer() -> None:
    html = """
    <html><body>
      <div>name one JWST fact</div>
      <div>GPT-5 mini JWST's primary mirror has 18 gold-coated segments.</div>
    </body></html>
    """

    result = parse_duck_ai_html(html, query="name one JWST fact")

    assert result.available is True
    assert result.answer_text == "JWST's primary mirror has 18 gold-coated segments."


def test_online_ai_check_keeps_larger_ai_overview_context() -> None:
    long_answer = " ".join(f"detail-{index}" for index in range(900))
    html = f"""
    <html><body>
      <div>long duck ai answer</div>
      <div>{long_answer}</div>
    </body></html>
    """

    result = parse_duck_ai_html(html, query="long duck ai answer")

    assert result.available is True
    assert len(result.answer_text) > 4500
    assert len(result.answer_text) <= 5000
    assert result.answer_text.endswith("...")


def test_online_ai_check_accepts_duck_ai_onboarding() -> None:
    selector_locator = _FakeLocator()
    page = _FakeButtonPage(selector_locator=selector_locator)
    statuses: list[tuple[str, str]] = []

    prepared = prepare_duck_ai_conversation(
        page,
        timeout_ms=1000,
        status_callback=lambda state, message: statuses.append((state, message)),
    )

    assert prepared is True
    assert selector_locator.clicked is True
    assert page.load_state_waited is True
    assert page.timeout_waited is True
    assert any("::-webkit-scrollbar" in content for content in page.style_tags)
    assert any("scrollbar-width: auto" in content for content in page.style_tags)
    assert (
        "preparing_context",
        "Handled Duck.ai onboarding control: Agree and Continue",
    ) in statuses


def test_online_ai_check_noops_when_onboarding_control_is_missing() -> None:
    selector_locator = _FakeLocator(visible=False, count=0)
    text_locator = _FakeLocator()
    page = _FakeButtonPage(selector_locator=selector_locator, text_locator=text_locator)

    prepared = prepare_duck_ai_conversation(page, timeout_ms=1000)

    assert prepared is False
    assert selector_locator.clicked is False


def test_online_ai_check_reports_missing_duck_ai_answer() -> None:
    result = parse_duck_ai_html(
        "<html><body><h3>Organic result</h3><p>Useful normal result.</p></body></html>",
        query="normal result",
    )

    assert result.available is False
    assert "No Duck.ai answer" in result.error


def test_online_ai_check_blocks_duck_ai_challenge() -> None:
    result = parse_duck_ai_html(
        "<html><body>Unfortunately, bots use DuckDuckGo too.</body></html>",
        query="blocked",
    )

    assert result.available is False
    assert "human-verification" in result.error


def test_online_ai_check_profile_setup_detection(tmp_path) -> None:
    profile_dir = tmp_path / "playwright-profile"

    assert resolve_online_ai_check_profile_dir(profile_dir) == profile_dir
    assert online_ai_check_profile_ready(profile_dir) is False
    assert "setupplaywright.sh" in online_ai_check_profile_setup_message(profile_dir)

    cookie_path = profile_dir / "Default" / "Network" / "Cookies"
    cookie_path.parent.mkdir(parents=True)
    cookie_path.write_bytes(b"sqlite-cookie-db")

    assert online_ai_check_profile_ready(profile_dir) is True


def test_online_ai_playwright_browser_uses_visible_scrollbar_args(monkeypatch, tmp_path) -> None:
    worker = onlineaicheck._GoogleAiOverviewWorker()
    context = _HealthyDuckContext()
    runtime = _FakePlaywrightRuntime(context)
    sync_api_module = ModuleType("playwright.sync_api")
    sync_api_module.sync_playwright = lambda: _FakeSyncPlaywrightFactory(runtime)
    playwright_module = ModuleType("playwright")
    playwright_module.sync_api = sync_api_module
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

    launched = worker._ensure_context(
        profile_dir=tmp_path / "profile",
        headless=False,
        callback=None,
    )

    assert launched is context
    assert runtime.chromium.launch_kwargs["viewport"] == {"width": 800, "height": 600}
    assert runtime.chromium.launch_kwargs["screen"] == {"width": 800, "height": 600}
    assert "--window-size=800,600" in runtime.chromium.launch_kwargs["args"]
    assert "--start-minimized" in runtime.chromium.launch_kwargs["args"]
    assert "--disable-features=OverlayScrollbar,OverlayScrollbars" in runtime.chromium.launch_kwargs["args"]
    assert runtime.chromium.launch_kwargs["bypass_csp"] is True


def test_online_ai_browser_window_can_be_minimized_with_cdp() -> None:
    session = _FakeCdpSession()
    page = _FakeCdpPage(session)

    minimized = onlineaicheck._minimize_browser_window(page)

    assert minimized is True
    assert session.calls == [
        ("Browser.getWindowForTarget", None),
        (
            "Browser.setWindowBounds",
            {"windowId": 42, "bounds": {"windowState": "minimized"}},
        ),
    ]


def test_online_ai_lookup_injects_visible_scrollbar_styles(monkeypatch, tmp_path) -> None:
    worker = onlineaicheck._GoogleAiOverviewWorker()
    context = _HealthyDuckContext()
    monkeypatch.setattr(worker, "_ensure_context", lambda **_: context)

    result = worker._lookup_in_current_thread(
        clean_query="the visual studio process name on ubuntu",
        timeout_seconds=1,
        headless=True,
        profile_path=tmp_path,
        callback=None,
    )

    assert result.available is True
    assert any("::-webkit-scrollbar" in content for content in context.page.style_tags)


def test_online_ai_check_lookup_delegates_to_duck_ai_worker(monkeypatch, tmp_path) -> None:
    def fake_lookup_once(
        self: object,
        query: str,
        *,
        timeout_seconds: float,
        headless: bool,
        profile_dir: object,
        callback: object,
    ) -> OnlineAiCheckResult:
        _ = self, timeout_seconds, headless, profile_dir, callback
        return OnlineAiCheckResult(
            provider="duck_ai",
            query=query,
            available=True,
            answer_text="Use nvidia-smi.",
        )

    monkeypatch.setattr(onlineaicheck._GoogleAiOverviewWorker, "lookup_once", fake_lookup_once)

    result = lookup_duck_ai_answer(
        "use nvidia-smi",
        profile_dir=tmp_path / "missing-profile",
        reuse_browser=False,
    )

    assert result.available is True
    assert result.provider == "duck_ai"
    assert "nvidia-smi" in result.answer_text


def test_online_ai_reusable_worker_uses_one_background_thread(monkeypatch, tmp_path) -> None:
    worker = onlineaicheck._GoogleAiOverviewWorker()
    seen_threads: list[int] = []
    main_thread = threading.get_ident()

    def fake_lookup(
        self: object,
        *,
        clean_query: str,
        timeout_seconds: float,
        headless: bool,
        profile_path: object,
        callback: object,
    ) -> OnlineAiCheckResult:
        _ = self, timeout_seconds, headless, profile_path, callback
        seen_threads.append(threading.get_ident())
        return OnlineAiCheckResult(
            provider="duck_ai",
            query=clean_query,
            available=True,
            answer_text=f"answer for {clean_query}",
        )

    monkeypatch.setattr(
        onlineaicheck._GoogleAiOverviewWorker,
        "_lookup_in_current_thread",
        fake_lookup,
    )

    first = worker.lookup(
        "postgres images",
        timeout_seconds=1,
        headless=True,
        profile_dir=tmp_path,
        callback=None,
    )
    second = worker.lookup(
        "redis images",
        timeout_seconds=1,
        headless=True,
        profile_dir=tmp_path,
        callback=None,
    )
    worker._jobs.put(None)

    assert first.available is True
    assert second.available is True
    assert len(seen_threads) == 2
    assert seen_threads[0] == seen_threads[1]
    assert seen_threads[0] != main_thread


def test_online_ai_reusable_worker_reopens_closed_browser_context(monkeypatch, tmp_path) -> None:
    worker = onlineaicheck._GoogleAiOverviewWorker()
    healthy_context = _HealthyDuckContext()
    contexts = [_ClosedDuckContext(), healthy_context]
    statuses: list[tuple[str, str]] = []

    def fake_ensure_context(
        *,
        profile_dir: object,
        headless: bool,
        callback: object,
    ) -> object:
        _ = profile_dir, headless, callback
        return contexts.pop(0)

    monkeypatch.setattr(worker, "_ensure_context", fake_ensure_context)

    result = worker._lookup_in_current_thread(
        clean_query="the visual studio process name on ubuntu",
        timeout_seconds=1,
        headless=True,
        profile_path=tmp_path,
        callback=lambda state, message: statuses.append((state, message)),
    )

    assert result.available is True
    assert result.provider == "duck_ai"
    assert "process name" in result.answer_text
    assert healthy_context.page.typed_query == "the visual studio process name on ubuntu"
    assert healthy_context.page.closed is True
    assert not contexts
    assert any("reopening and retrying once" in message for _, message in statuses)


def test_online_ai_visible_challenge_page_stays_open(monkeypatch, tmp_path) -> None:
    worker = onlineaicheck._GoogleAiOverviewWorker()
    challenge_page = _FakeDuckChallengePage()
    challenge_context = _HealthyDuckContext(challenge_page)
    statuses: list[tuple[str, str]] = []

    def fake_ensure_context(
        *,
        profile_dir: object,
        headless: bool,
        callback: object,
    ) -> object:
        _ = profile_dir, headless, callback
        return challenge_context

    monkeypatch.setattr(worker, "_ensure_context", fake_ensure_context)

    result = worker._lookup_in_current_thread(
        clean_query="what is the visual studio process name in ubuntu",
        timeout_seconds=1,
        headless=False,
        profile_path=tmp_path,
        callback=lambda state, message: statuses.append((state, message)),
    )

    assert result.available is False
    assert "human-verification challenge" in result.error
    assert "left open" in result.error
    assert challenge_page.closed is False
    assert any("leaving the browser open" in message for _, message in statuses)


def test_online_ai_headless_challenge_page_closes(monkeypatch, tmp_path) -> None:
    worker = onlineaicheck._GoogleAiOverviewWorker()
    challenge_page = _FakeDuckChallengePage()
    challenge_context = _HealthyDuckContext(challenge_page)

    monkeypatch.setattr(
        worker,
        "_ensure_context",
        lambda **_: challenge_context,
    )

    result = worker._lookup_in_current_thread(
        clean_query="what is the visual studio process name in ubuntu",
        timeout_seconds=1,
        headless=True,
        profile_path=tmp_path,
        callback=None,
    )

    assert result.available is False
    assert "human-verification challenge" in result.error
    assert "left open" not in result.error
    assert challenge_page.closed is True


def test_online_ai_check_sanitizes_typein_values() -> None:
    query = sanitize_online_ai_check_query('/checkonlineai use sudo typein "secret" for password')

    assert "secret" not in query
    assert "typein" not in query.lower()
    assert query == "/checkonlineai use sudo for password"


def test_online_ai_check_sanitize_prefers_quoted_macro_query() -> None:
    query = sanitize_online_ai_check_query(
        '/checkonlineai "what are postgres docker image names" and install them'
    )

    assert query == "what are postgres docker image names"


def test_online_ai_check_prompt_lines_include_context() -> None:
    result = OnlineAiCheckResult(
        provider="duck_ai",
        query="free gpu ram",
        available=True,
        answer_text="Use nvidia-smi.",
        source_title="Duck.ai",
        source_url="https://duck.ai/?origin=funnel_home_website",
        search_url="https://duck.ai/?origin=funnel_home_website",
        fetched_at="2026-05-15T00:00:00Z",
        status="completed",
    )

    lines = online_ai_check_prompt_lines({ONLINE_AI_CHECK_CONTEXT_KEY: result.to_context()})
    joined = "\n".join(lines)

    assert "Duck.ai context" in joined
    assert "Use nvidia-smi." in joined
    assert len(joined) < 2200


def test_online_ai_check_requested_from_macro_summary() -> None:
    assert online_ai_check_requested_from_context(
        {
            USER_MACRO_SUMMARY_CONTEXT_KEY: [
                {"kind": "checkonlineai", "macro_id": "macro_checkonlineai_1"}
            ]
        }
    )


def test_online_ai_explicit_macro_query_is_used_from_context() -> None:
    query = AgentRuntime._explicit_online_ai_query_from_context(
        {
            USER_MACRO_SUMMARY_CONTEXT_KEY: [
                {
                    "kind": "checkonlineai",
                    "macro_id": "macro_checkonlineai_1",
                    "query": "what are postgres docker image names",
                }
            ]
        }
    )

    assert query == "what are postgres docker image names"


def test_streaming_online_ai_check_is_scoped_to_marked_step() -> None:
    state = {
        "online_ai_check_requested": True,
        "online_ai_check_task_ids": [],
        "current_index": 0,
        "tasks": [
            {"task_id": "task-1", "description": "List local GPU devices"},
            {"task_id": "task-2", "description": "check online ai for nvidia-smi memory query"},
        ],
    }

    assert not AgentRuntime._streaming_task_should_lookup_online_ai(
        state,
        state["tasks"][0],
    )
    state["current_index"] = 1
    assert AgentRuntime._streaming_task_should_lookup_online_ai(
        state,
        state["tasks"][1],
    )


def test_streaming_drops_pure_online_ai_lookup_task_when_real_work_remains() -> None:
    tasks = [
        SimpleNamespace(id="task-1", description="Check online AI status using the provided query"),
        SimpleNamespace(id="task-2", description="Check free VRAM on the machine"),
    ]

    filtered = AgentRuntime._strip_pure_online_ai_lookup_tasks(tasks)

    assert [task.id for task in filtered] == ["task-2"]


def test_streaming_step_prompt_includes_explicit_online_ai_query() -> None:
    runtime = AgentRuntime.__new__(AgentRuntime)
    state = {
        "online_ai_check_requested": True,
        "online_ai_check_task_ids": [],
        "online_ai_check_query": "docker image names for postgres",
        "current_index": 0,
        "original_prompt": "check online ai and install the docker images",
        "tasks": [
            {
                "task_id": "task-1",
                "description": "check online ai for available docker images",
            },
            {"task_id": "task-2", "description": "install selected images"},
        ],
    }

    prompt = runtime._streaming_step_prompt(state, state["tasks"][0])

    assert (
        "Explicit /checkonlineai lookup query for this step: docker image names for postgres"
        in prompt
    )
