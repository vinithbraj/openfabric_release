"""Local audio transcription service for Agent UI dictation."""

from agent_runtime.audio_transcriber.service import (
    AudioTranscriberProxy,
    register_audio_transcriber_routes,
)

__all__ = ["AudioTranscriberProxy", "register_audio_transcriber_routes"]
