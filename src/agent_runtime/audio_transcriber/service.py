"""Agent API proxy for the standalone audio runtime microservice."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urljoin

from fastapi import FastAPI, HTTPException, Request

from agent_runtime.api.config import Settings


def _audio_service_url(settings: Settings, app: FastAPI | None = None) -> str:
    controls = getattr(getattr(app, "state", None), "agent_runtime_controls", None)
    base = ""
    if isinstance(controls, dict):
        base = str(controls.get("audio_transcriber_service_url") or "").strip()
    if not base:
        base = str(settings.audio_transcriber_service_url or "").strip()
    return base.rstrip("/") or "http://localhost:8012"


def _json_from_bytes(data: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _upstream_detail(data: bytes, fallback: str) -> str:
    payload = _json_from_bytes(data)
    detail = payload.get("detail") or payload.get("message") or payload.get("error")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if detail:
        return json.dumps(detail, default=str)
    text = data.decode("utf-8", errors="replace").strip()
    return text or fallback


class AudioTranscriberProxy:
    """Proxy Agent UI audio endpoints to the separate audio runtime process."""

    def __init__(self, settings: Settings, app: FastAPI | None = None) -> None:
        self.settings = settings
        self.app = app

    def config_payload(self) -> dict[str, Any]:
        if not bool(self.settings.audio_transcriber_enabled):
            return self._disabled_config()
        try:
            payload = self._request_json("GET", "/config")
        except HTTPException as exc:
            return self._unavailable_config(str(exc.detail or "Audio runtime is unavailable."))
        payload["service"] = {
            "status": "ready" if payload.get("available") else "unavailable",
            "url": _audio_service_url(self.settings, self.app),
        }
        return payload

    async def transcribe_request(self, request: Request) -> dict[str, Any]:
        if not bool(self.settings.audio_transcriber_enabled):
            raise HTTPException(status_code=503, detail="Audio transcription is disabled.")
        content_type = str(request.headers.get("content-type") or "application/octet-stream")
        body = await self._read_limited_body(request)
        return await asyncio.to_thread(
            self._request_json,
            "POST",
            "/transcribe",
            body,
            {"Content-Type": content_type},
        )

    async def _read_limited_body(self, request: Request) -> bytes:
        chunks: list[bytes] = []
        total = 0
        limit = int(self.settings.audio_transcriber_max_upload_mb) * 1024 * 1024
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"Audio upload is too large. Maximum is {self.settings.audio_transcriber_max_upload_mb} MB.",
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _request_json(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = urljoin(f"{_audio_service_url(self.settings, self.app)}/", path.lstrip("/"))
        request = urllib_request.Request(
            url,
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib_request.urlopen(
                request,
                timeout=float(self.settings.audio_transcriber_proxy_timeout_seconds),
            ) as response:
                data = response.read()
        except urllib_error.HTTPError as exc:
            data = exc.read()
            raise HTTPException(
                status_code=int(exc.code),
                detail=_upstream_detail(data, "Audio runtime request failed."),
            ) from exc
        except (OSError, TimeoutError, urllib_error.URLError) as exc:
            raise HTTPException(status_code=503, detail="Audio runtime is unreachable.") from exc
        payload = _json_from_bytes(data)
        if not payload:
            raise HTTPException(status_code=502, detail="Audio runtime returned an invalid response.")
        return payload

    def _disabled_config(self) -> dict[str, Any]:
        return {
            "available": False,
            "enabled": False,
            "engine": str(self.settings.audio_transcriber_engine or "whisper_cpp"),
            "language": str(self.settings.audio_transcriber_language or "auto"),
            "max_duration_seconds": int(self.settings.audio_transcriber_max_seconds),
            "max_upload_mb": int(self.settings.audio_transcriber_max_upload_mb),
            "supported_content_types": [],
            "model": {
                "path": str(self.settings.audio_transcriber_model_path),
                "status": "disabled",
                "available": False,
            },
            "binary": {
                "path": str(self.settings.audio_transcriber_binary),
                "resolved_path": "",
                "status": "disabled",
                "available": False,
            },
            "ffmpeg": {
                "path": str(self.settings.audio_transcriber_ffmpeg_binary),
                "resolved_path": "",
                "status": "disabled",
                "available": False,
            },
            "ffprobe": {
                "path": str(self.settings.audio_transcriber_ffprobe_binary),
                "resolved_path": "",
                "status": "disabled",
                "available": False,
            },
            "service": {
                "status": "disabled",
                "url": _audio_service_url(self.settings, self.app),
            },
        }

    def _unavailable_config(self, reason: str) -> dict[str, Any]:
        payload = self._disabled_config()
        payload.update(
            {
                "enabled": True,
                "engine": str(self.settings.audio_transcriber_engine or "whisper_cpp"),
                "service": {
                    "status": "unreachable",
                    "url": _audio_service_url(self.settings, self.app),
                    "error": reason,
                },
            }
        )
        payload["model"]["status"] = "unknown"
        payload["binary"]["status"] = "unknown"
        payload["ffmpeg"]["status"] = "unknown"
        payload["ffprobe"]["status"] = "unknown"
        return payload


def register_audio_transcriber_routes(app: FastAPI, *, settings: Settings) -> None:
    """Attach Agent UI audio proxy endpoints."""

    proxy = AudioTranscriberProxy(settings, app)

    @app.get("/api/audio-transcriber/config")
    def audio_transcriber_config() -> dict[str, Any]:
        return proxy.config_payload()

    @app.post("/api/audio-transcriber/transcribe")
    async def audio_transcriber_transcribe(request: Request) -> dict[str, Any]:
        return await proxy.transcribe_request(request)
