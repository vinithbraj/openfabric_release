"""macOS gateway app wrapper."""

from __future__ import annotations

from fastapi import FastAPI

from gateway_macos.config import get_settings
from gateway_core.app import create_app as create_core_app
from gateway_core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    return create_core_app(settings or get_settings())


app = create_app()

__all__ = ["app", "create_app"]
