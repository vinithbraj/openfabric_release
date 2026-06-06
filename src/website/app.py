"""FastAPI application factory for the standalone OpenFabric website service."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agent_runtime.api import get_runtime_version_info
from website import __version__


PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
PAGE_FILES = {
    "home": STATIC_DIR / "index.html",
}


def _service_port(env_name: str, default: str) -> str:
    raw_port = str(os.getenv(env_name, default) or default).strip()
    try:
        port = int(raw_port)
    except ValueError:
        return default
    return str(port) if 1 <= port <= 65535 else default


def _render_page(page_name: str) -> HTMLResponse:
    page_path = PAGE_FILES.get(page_name) or PAGE_FILES["home"]
    if page_path is None or not page_path.is_file():
        raise HTTPException(status_code=404, detail="Website page not found")
    html = page_path.read_text(encoding="utf-8")
    html = html.replace("__MANUAL_PORT__", _service_port("AOR_MANUAL_PORT", "8013"))
    html = html.replace("__AGENT_PORT__", _service_port("AOR_PORT", "8011"))
    html = html.replace("__WEBSITE_VERSION__", __version__)
    return HTMLResponse(html)


def create_app() -> FastAPI:
    """Create the standalone OpenFabric website FastAPI application."""

    app = FastAPI(title="OpenFabric Website", version=__version__)
    app.mount("/website/static", StaticFiles(directory=str(STATIC_DIR)), name="website-static")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/website")

    @app.get("/website", include_in_schema=False)
    @app.get("/website/", include_in_schema=False)
    def website_home() -> HTMLResponse:
        return _render_page("home")

    @app.get("/website/product", include_in_schema=False)
    def website_product() -> HTMLResponse:
        return _render_page("home")

    @app.get("/website/use-cases", include_in_schema=False)
    def website_use_cases() -> HTMLResponse:
        return _render_page("home")

    @app.get("/website/resources", include_in_schema=False)
    def website_resources() -> HTMLResponse:
        return _render_page("home")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "website",
            "version": __version__,
            "runtime": get_runtime_version_info(),
        }

    return app
