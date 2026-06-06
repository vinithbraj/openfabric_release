"""Local audio transcription routes and whisper.cpp runner."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from audio_runtime.config import AudioRuntimeSettings


SUPPORTED_AUDIO_TYPES = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".mp4",
    "audio/x-m4a": ".m4a",
    "audio/ogg": ".ogg",
    "application/octet-stream": ".bin",
}


class AudioTranscriberService:
    """Run local browser-recorded audio through whisper.cpp."""

    def __init__(self, settings: AudioRuntimeSettings) -> None:
        self.settings = settings

    @property
    def max_upload_bytes(self) -> int:
        return int(self.settings.audio_transcriber_max_upload_mb) * 1024 * 1024

    def config_payload(self) -> dict[str, Any]:
        binary_path = self._resolve_binary(self.settings.audio_transcriber_binary)
        ffmpeg_path = self._resolve_binary(self.settings.audio_transcriber_ffmpeg_binary)
        ffprobe_path = self._resolve_binary(self.settings.audio_transcriber_ffprobe_binary)
        model_path = Path(self.settings.audio_transcriber_model_path)
        binary_available = bool(binary_path)
        ffmpeg_available = bool(ffmpeg_path)
        ffprobe_available = bool(ffprobe_path)
        model_available = model_path.is_file()
        enabled = bool(self.settings.audio_transcriber_enabled)
        available = (
            enabled
            and self.settings.audio_transcriber_engine == "whisper_cpp"
            and binary_available
            and ffmpeg_available
            and ffprobe_available
            and model_available
        )
        return {
            "available": available,
            "enabled": enabled,
            "engine": self.settings.audio_transcriber_engine,
            "language": self.settings.audio_transcriber_language,
            "max_duration_seconds": int(self.settings.audio_transcriber_max_seconds),
            "max_upload_mb": int(self.settings.audio_transcriber_max_upload_mb),
            "supported_content_types": sorted(SUPPORTED_AUDIO_TYPES),
            "model": {
                "path": str(model_path),
                "status": "ready" if model_available else "missing",
                "available": model_available,
            },
            "binary": {
                "path": str(self.settings.audio_transcriber_binary),
                "resolved_path": binary_path or "",
                "status": "ready" if binary_available else "missing",
                "available": binary_available,
            },
            "ffmpeg": {
                "path": str(self.settings.audio_transcriber_ffmpeg_binary),
                "resolved_path": ffmpeg_path or "",
                "status": "ready" if ffmpeg_available else "missing",
                "available": ffmpeg_available,
            },
            "ffprobe": {
                "path": str(self.settings.audio_transcriber_ffprobe_binary),
                "resolved_path": ffprobe_path or "",
                "status": "ready" if ffprobe_available else "missing",
                "available": ffprobe_available,
            },
        }

    async def transcribe_request(self, request: Request) -> dict[str, Any]:
        config = self.config_payload()
        if not config["enabled"]:
            raise HTTPException(status_code=503, detail="Audio transcription is disabled.")
        if not config["available"]:
            raise HTTPException(
                status_code=503,
                detail=self._unavailable_reason(config),
            )
        content_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if content_type not in SUPPORTED_AUDIO_TYPES:
            raise HTTPException(status_code=415, detail="Unsupported audio format.")
        body = await self._read_limited_body(request)
        if not body:
            raise HTTPException(status_code=400, detail="Recorded audio was empty.")

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="aor-audio-") as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / f"recording{SUPPORTED_AUDIO_TYPES[content_type]}"
            wav_path = temp_path / "recording.wav"
            transcript_base = temp_path / "transcript"
            input_path.write_bytes(body)

            duration_seconds = self._probe_duration(
                input_path,
                ffprobe_path=str(config["ffprobe"]["resolved_path"]),
                ffmpeg_path=str(config["ffmpeg"]["resolved_path"]),
            )
            max_seconds = int(self.settings.audio_transcriber_max_seconds)
            if duration_seconds is not None and duration_seconds > max_seconds:
                raise HTTPException(
                    status_code=400,
                    detail=f"Recording is too long. Maximum is {max_seconds} seconds.",
                )
            self._convert_to_wav(input_path, wav_path, str(config["ffmpeg"]["resolved_path"]))
            transcript = self._run_whisper(
                wav_path=wav_path,
                output_base=transcript_base,
                binary_path=str(config["binary"]["resolved_path"]),
            )
        elapsed = time.monotonic() - started
        return {
            "status": "ok" if transcript else "empty",
            "transcript": transcript,
            "engine": self.settings.audio_transcriber_engine,
            "language": self.settings.audio_transcriber_language,
            "duration_seconds": round(duration_seconds, 3) if duration_seconds is not None else 0.0,
            "elapsed_seconds": round(elapsed, 3),
            "upload_bytes": len(body),
        }

    async def _read_limited_body(self, request: Request) -> bytes:
        chunks: list[bytes] = []
        total = 0
        limit = self.max_upload_bytes
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"Audio upload is too large. Maximum is {self.settings.audio_transcriber_max_upload_mb} MB.",
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _probe_duration(self, input_path: Path, ffprobe_path: str, ffmpeg_path: str) -> float | None:
        duration = self._probe_duration_from_ffprobe(input_path, ffprobe_path)
        if duration is not None:
            return duration
        return self._probe_duration_from_ffmpeg(input_path, ffmpeg_path)

    @staticmethod
    def _parse_duration_text(text: str) -> float | None:
        raw = (text or "").strip()
        if not raw:
            return None
        lines = raw.splitlines()
        for candidate in reversed(lines):
            candidate = candidate.strip()
            if not candidate:
                continue
            try:
                value = float(candidate)
            except ValueError:
                continue
            if value > 0:
                return value
        match = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", raw)
        if not match:
            return None
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return max(hours * 3600 + minutes * 60 + seconds, 0.0)

    def _probe_duration_from_ffprobe(self, input_path: Path, ffprobe_path: str) -> float | None:
        try:
            result = subprocess.run(
                [
                    ffprobe_path,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(input_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None
        if result.returncode != 0:
            return None
        return self._parse_duration_text(result.stdout or "")

    def _probe_duration_from_ffmpeg(self, input_path: Path, ffmpeg_path: str) -> float | None:
        try:
            result = subprocess.run(
                [
                    ffmpeg_path,
                    "-v",
                    "error",
                    "-i",
                    str(input_path),
                    "-f",
                    "null",
                    "-",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode not in (0, 1):
            return None
        return self._parse_duration_text(f"{result.stderr or ''}\n{result.stdout or ''}")

    def _convert_to_wav(self, input_path: Path, wav_path: Path, ffmpeg_path: str) -> None:
        try:
            result = subprocess.run(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(input_path),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=max(30, int(self.settings.audio_transcriber_max_seconds) + 10),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=503, detail="Could not decode recorded audio.") from exc
        if result.returncode != 0 or not wav_path.exists():
            raise HTTPException(status_code=400, detail="Could not decode recorded audio.")

    def _run_whisper(self, *, wav_path: Path, output_base: Path, binary_path: str) -> str:
        command = [
            binary_path,
            "-m",
            str(self.settings.audio_transcriber_model_path),
            "-f",
            str(wav_path),
            "-otxt",
            "-of",
            str(output_base),
            "-nt",
            "-np",
        ]
        language = str(self.settings.audio_transcriber_language or "auto").strip()
        if language and language.lower() != "auto":
            command.extend(["-l", language])
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=max(60, int(self.settings.audio_transcriber_max_seconds) * 3),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=503, detail="Local transcription failed to run.") from exc
        transcript_path = Path(f"{output_base}.txt")
        transcript = ""
        if transcript_path.exists():
            transcript = transcript_path.read_text(encoding="utf-8", errors="replace").strip()
        if not transcript:
            transcript = (result.stdout or "").strip()
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="Local transcription failed.")
        return self._clean_transcript(transcript)

    @staticmethod
    def _resolve_binary(value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            return ""
        path = Path(candidate).expanduser()
        if path.is_absolute() or os.sep in candidate:
            return str(path) if path.exists() and os.access(path, os.X_OK) else ""
        return shutil.which(candidate) or ""

    @staticmethod
    def _clean_transcript(text: str) -> str:
        lines = [line.strip() for line in str(text or "").splitlines()]
        return " ".join(line for line in lines if line).strip()

    @staticmethod
    def _unavailable_reason(config: dict[str, Any]) -> str:
        if str(config.get("engine") or "") != "whisper_cpp":
            return "Audio transcription engine is unsupported."
        for key, label in (
            ("binary", "whisper-cli is unavailable"),
            ("ffmpeg", "ffmpeg is unavailable"),
            ("ffprobe", "ffprobe is unavailable"),
            ("model", "Whisper model file is missing"),
        ):
            item = config.get(key)
            if isinstance(item, dict) and not item.get("available"):
                return f"{label}."
        return "Audio transcription service is unavailable."


def register_audio_runtime_routes(app: FastAPI, *, settings: AudioRuntimeSettings) -> None:
    """Attach local audio transcription endpoints."""

    service = AudioTranscriberService(settings)

    @app.get("/healthz")
    def audio_healthz() -> dict[str, str]:
        return {"status": "ok", "service": "audio_runtime"}

    @app.get("/config")
    def audio_transcriber_config() -> dict[str, Any]:
        return service.config_payload()

    @app.post("/transcribe")
    async def audio_transcriber_transcribe(request: Request) -> dict[str, Any]:
        return await service.transcribe_request(request)

    @app.get("/api/audio-transcriber/config")
    def audio_transcriber_compat_config() -> dict[str, Any]:
        return service.config_payload()

    @app.post("/api/audio-transcriber/transcribe")
    async def audio_transcriber_compat_transcribe(request: Request) -> dict[str, Any]:
        return await service.transcribe_request(request)
