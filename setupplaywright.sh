#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
PROFILE_DIR="${AOR_ONLINE_AI_CHECK_PROFILE_DIR:-${REPO_ROOT}/artifacts/playwright-duckai-profile}"

if [[ "${PROFILE_DIR}" != /* ]]; then
  PROFILE_DIR="${REPO_ROOT}/${PROFILE_DIR}"
fi

cd "${REPO_ROOT}"

if [[ ! -d ".venv" ]]; then
  echo "Missing .venv in ${REPO_ROOT}." >&2
  echo "Run: ./install.sh" >&2
  exit 1
fi

source .venv/bin/activate

python -m playwright install chromium
mkdir -p "${PROFILE_DIR}"

python - "${PROFILE_DIR}" <<'PY'
import os
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright

profile_dir = Path(sys.argv[1]).expanduser().resolve()
wait_seconds = max(1, int(os.getenv("AOR_ONLINE_AI_SETUP_WAIT_SECONDS", "600")))
tty_path = Path("/dev/tty")
has_tty = tty_path.exists()
print(f"Opening visible Chromium with profile: {profile_dir}", flush=True)
print("Complete Duck.ai onboarding or human verification in the browser window if prompted.", flush=True)
if has_tty:
    print("When finished, return here and press Enter to close the browser.", flush=True)
else:
    print(
        "No interactive stdin is available; close the browser window when finished. "
        f"The script will also close it after {wait_seconds} seconds.",
        flush=True,
    )


def wait_for_browser_close(context, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            open_pages = [page for page in context.pages if not page.is_closed()]
            if not open_pages:
                return
            open_pages[0].wait_for_timeout(1000)
        except Exception as exc:
            if "Target" in str(exc) and "closed" in str(exc).lower():
                return
            if "has been closed" in str(exc).lower():
                return
            raise


def close_context(context) -> None:
    try:
        context.close()
    except Exception as exc:
        if "Target" in str(exc) and "closed" in str(exc).lower():
            return
        if "has been closed" in str(exc).lower():
            return
        raise


def wait_for_enter_or_browser_close(context, timeout_seconds: int) -> None:
    if not has_tty:
        wait_for_browser_close(context, timeout_seconds)
        return
    try:
        with tty_path.open("r", encoding="utf-8") as tty:
            tty.readline()
    except OSError:
        wait_for_browser_close(context, timeout_seconds)


visible_scrollbar_style = """
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


def apply_visible_scrollbar_styles(page) -> None:
    try:
        page.add_style_tag(content=visible_scrollbar_style)
    except Exception:
        pass


def minimize_browser_window(page) -> None:
    try:
        session = page.context.new_cdp_session(page)
        window = session.send("Browser.getWindowForTarget")
        window_id = window.get("windowId")
        if window_id is None:
            return
        session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "minimized"}},
        )
    except Exception:
        pass


with sync_playwright() as playwright:
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        viewport={"width": 800, "height": 600},
        screen={"width": 800, "height": 600},
        locale="en-US",
        args=[
            "--window-size=800,600",
            "--start-minimized",
            "--disable-features=OverlayScrollbar,OverlayScrollbars",
        ],
        bypass_csp=True,
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = context.pages[0] if context.pages else context.new_page()
    minimize_browser_window(page)
    page.goto(
        "https://duck.ai/?origin=funnel_home_website",
        wait_until="domcontentloaded",
    )
    apply_visible_scrollbar_styles(page)
    minimize_browser_window(page)
    try:
        wait_for_enter_or_browser_close(context, wait_seconds)
    except EOFError:
        print(
            "No terminal input was available; keeping the browser open so setup can continue.",
            flush=True,
        )
        wait_for_browser_close(context, wait_seconds)
    finally:
        close_context(context)

cookie_paths = list(profile_dir.glob("**/Cookies"))
if not any(path.is_file() and path.stat().st_size > 0 for path in cookie_paths):
    print(
        "Warning: no Chromium Cookies file was found in the profile. "
        "If /checkonlineai still asks for setup, rerun this script and complete Duck.ai onboarding.",
        flush=True,
    )

print("Playwright Duck.ai profile setup complete.", flush=True)
PY
