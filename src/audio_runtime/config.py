"""Configuration for the standalone local audio runtime."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_model_path_for_tier(tier: str, model_dir: Path) -> Path:
    """Resolve a whisper model path from a quality tier."""

    normalized = (tier or "high").strip().lower()
    if normalized == "med":
        filename = "ggml-medium.en.bin"
    elif normalized == "low":
        filename = "ggml-base.en.bin"
    else:
        filename = "ggml-large-v3.bin"
    return Path(model_dir) / filename


class AudioRuntimeSettings(BaseModel):
    """Settings needed by the local audio transcription microservice."""

    audio_transcriber_enabled: bool = Field(
        default_factory=lambda: _env_bool("AOR_AUDIO_TRANSCRIBER_ENABLED", True)
    )
    audio_transcriber_engine: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_ENGINE", "whisper_cpp").strip().lower()
    )
    audio_transcriber_model_path: Path = Field(
        default_factory=lambda: Path(
            os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_PATH", "/data/models/whisper/ggml-large-v3.bin")
        )
    )
    audio_transcriber_model_tier: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_TIER", "high").strip().lower()
    )
    audio_transcriber_binary: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_BINARY", "whisper-cli").strip()
    )
    audio_transcriber_ffmpeg_binary: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_FFMPEG_BINARY", "ffmpeg").strip()
    )
    audio_transcriber_ffprobe_binary: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_FFPROBE_BINARY", "ffprobe").strip()
    )
    audio_transcriber_max_seconds: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AUDIO_TRANSCRIBER_MAX_SECONDS", "120"))
    )
    audio_transcriber_max_upload_mb: int = Field(
        default_factory=lambda: int(os.getenv("AOR_AUDIO_TRANSCRIBER_MAX_UPLOAD_MB", "25"))
    )
    audio_transcriber_language: str = Field(
        default_factory=lambda: os.getenv("AOR_AUDIO_TRANSCRIBER_LANGUAGE", "auto").strip().lower()
    )

    @model_validator(mode="after")
    def validate_audio_settings(self) -> "AudioRuntimeSettings":
        self.audio_transcriber_engine = (
            str(self.audio_transcriber_engine or "whisper_cpp").strip().lower() or "whisper_cpp"
        )
        if self.audio_transcriber_engine not in {"whisper_cpp"}:
            raise ValueError("audio_transcriber_engine must be whisper_cpp.")
        model_tier = str(self.audio_transcriber_model_tier or "high").strip().lower() or "high"
        if model_tier not in {"low", "med", "high"}:
            model_tier = "high"
        self.audio_transcriber_model_tier = model_tier

        env_model_path = os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_PATH")
        if env_model_path is None:
            default_model_path = Path(
                os.getenv("AOR_AUDIO_TRANSCRIBER_MODEL_PATH", "/data/models/whisper/ggml-large-v3.bin")
            ).expanduser()
            requested_model_path = Path(self.audio_transcriber_model_path).expanduser()
            if requested_model_path == default_model_path:
                model_dir = Path(
                    os.getenv(
                        "AOR_AUDIO_TRANSCRIBER_MODEL_DIR",
                        Path(requested_model_path).parent,
                    )
                ).expanduser()
                self.audio_transcriber_model_path = _resolve_model_path_for_tier(model_tier, model_dir)
            else:
                self.audio_transcriber_model_path = requested_model_path
        else:
            self.audio_transcriber_model_path = Path(self.audio_transcriber_model_path).expanduser()
        self.audio_transcriber_binary = str(self.audio_transcriber_binary or "whisper-cli").strip() or "whisper-cli"
        self.audio_transcriber_ffmpeg_binary = str(self.audio_transcriber_ffmpeg_binary or "ffmpeg").strip() or "ffmpeg"
        self.audio_transcriber_ffprobe_binary = str(self.audio_transcriber_ffprobe_binary or "ffprobe").strip() or "ffprobe"
        if self.audio_transcriber_max_seconds <= 0:
            raise ValueError("audio_transcriber_max_seconds must be greater than zero.")
        if self.audio_transcriber_max_upload_mb <= 0:
            raise ValueError("audio_transcriber_max_upload_mb must be greater than zero.")
        self.audio_transcriber_max_seconds = min(3600, int(self.audio_transcriber_max_seconds))
        self.audio_transcriber_max_upload_mb = min(1024, int(self.audio_transcriber_max_upload_mb))
        self.audio_transcriber_language = (
            str(self.audio_transcriber_language or "auto").strip().lower() or "auto"
        )
        return self


def load_audio_runtime_settings() -> AudioRuntimeSettings:
    """Load audio runtime settings from the process environment."""

    return AudioRuntimeSettings()
