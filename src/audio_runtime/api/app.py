"""FastAPI app factory for the standalone local audio runtime."""

from __future__ import annotations

from fastapi import FastAPI

from audio_runtime.config import AudioRuntimeSettings, load_audio_runtime_settings
from audio_runtime.service import register_audio_runtime_routes


def create_app(settings: AudioRuntimeSettings | None = None) -> FastAPI:
    """Create the audio transcription microservice app."""

    configured_settings = settings or load_audio_runtime_settings()
    app = FastAPI(title="OpenFABRIC Audio Runtime", version="1.0")
    app.state.settings = configured_settings
    register_audio_runtime_routes(app, settings=configured_settings)
    return app
