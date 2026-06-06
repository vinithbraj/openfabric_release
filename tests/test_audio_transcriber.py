from __future__ import annotations

from typing import Any
from pathlib import Path
from urllib import error as urllib_error

from fastapi.testclient import TestClient

from agent_runtime.api.app import create_app as create_agent_app
from agent_runtime.api.config import Settings as AgentSettings
from audio_runtime.api.app import create_app as create_audio_app
from audio_runtime.config import AudioRuntimeSettings


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _audio_client(tmp_path: Path, *, enabled: bool = True, max_seconds: int = 120, max_upload_mb: int = 25) -> TestClient:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    model_path = tmp_path / "ggml-base.en.bin"
    model_path.write_text("model", encoding="utf-8")
    ffprobe = _write_executable(
        bin_dir / "ffprobe",
        "#!/usr/bin/env bash\nprintf '1.25\\n'\n",
    )
    ffmpeg = _write_executable(
        bin_dir / "ffmpeg",
        "#!/usr/bin/env bash\nout=\"${@: -1}\"\nprintf 'wav' > \"${out}\"\n",
    )
    whisper = _write_executable(
        bin_dir / "whisper-cli",
        """#!/usr/bin/env bash
out=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-of" ]]; then
    out="$2"
    shift 2
    continue
  fi
  shift
done
printf 'hello local agent\\n' > "${out}.txt"
""",
    )
    return TestClient(
        create_audio_app(
            AudioRuntimeSettings(
                workspace_root=tmp_path,
                audio_transcriber_enabled=enabled,
                audio_transcriber_model_path=model_path,
                audio_transcriber_binary=str(whisper),
                audio_transcriber_ffmpeg_binary=str(ffmpeg),
                audio_transcriber_ffprobe_binary=str(ffprobe),
                audio_transcriber_max_seconds=max_seconds,
                audio_transcriber_max_upload_mb=max_upload_mb,
            )
        )
    )


def test_audio_transcriber_config_reports_ready(tmp_path: Path) -> None:
    client = _audio_client(tmp_path)

    response = client.get("/api/audio-transcriber/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["available"] is True
    assert payload["engine"] == "whisper_cpp"
    assert payload["model"]["status"] == "ready"
    assert payload["binary"]["status"] == "ready"


def test_audio_transcriber_rejects_disabled_service(tmp_path: Path) -> None:
    client = _audio_client(tmp_path, enabled=False)

    config = client.get("/api/audio-transcriber/config").json()
    assert config["enabled"] is False
    assert config["available"] is False

    response = client.post(
        "/api/audio-transcriber/transcribe",
        content=b"audio",
        headers={"content-type": "audio/webm"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Audio transcription is disabled."


def test_audio_transcriber_rejects_invalid_uploads(tmp_path: Path) -> None:
    client = _audio_client(tmp_path, max_upload_mb=1)

    unsupported = client.post(
        "/api/audio-transcriber/transcribe",
        content=b"audio",
        headers={"content-type": "text/plain"},
    )
    assert unsupported.status_code == 415

    empty = client.post(
        "/api/audio-transcriber/transcribe",
        content=b"",
        headers={"content-type": "audio/webm"},
    )
    assert empty.status_code == 400

    oversized = client.post(
        "/api/audio-transcriber/transcribe",
        content=b"x" * (1024 * 1024 + 1),
        headers={"content-type": "audio/webm"},
    )
    assert oversized.status_code == 413


def test_audio_transcriber_runs_local_whisper_cpp(tmp_path: Path) -> None:
    client = _audio_client(tmp_path)

    response = client.post(
        "/api/audio-transcriber/transcribe",
        content=b"audio",
        headers={"content-type": "audio/webm;codecs=opus"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["transcript"] == "hello local agent"
    assert payload["engine"] == "whisper_cpp"
    assert payload["duration_seconds"] == 1.25


def test_audio_transcriber_transcribe_when_ffprobe_fails(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    model_path = tmp_path / "ggml-base.en.bin"
    model_path.write_text("model", encoding="utf-8")
    ffprobe = _write_executable(
        bin_dir / "ffprobe",
        "#!/usr/bin/env bash\nprintf 'n/a\\n'; exit 1\n",
    )
    ffmpeg = _write_executable(
        bin_dir / "ffmpeg",
        "#!/usr/bin/env bash\nout=\"${@: -1}\"\nprintf 'wav' > \"${out}\"\nprintf 'Duration: 00:00:01.00, start: 0.000000, bitrate: 128 kb/s\\\\n' >&2\n",
    )
    whisper = _write_executable(
        bin_dir / "whisper-cli",
        """#!/usr/bin/env bash
out=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-of" ]]; then
    out="$2"
    shift 2
    continue
  fi
  shift
done
printf 'hello local agent\\n' > \"${out}.txt\"
""",
    )
    client = TestClient(
        create_audio_app(
            AudioRuntimeSettings(
                workspace_root=tmp_path,
                audio_transcriber_model_path=model_path,
                audio_transcriber_binary=str(whisper),
                audio_transcriber_ffmpeg_binary=str(ffmpeg),
                audio_transcriber_ffprobe_binary=str(ffprobe),
            )
        )
    )

    response = client.post(
        "/api/audio-transcriber/transcribe",
        content=b"audio",
        headers={"content-type": "audio/webm"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["transcript"] == "hello local agent"
    assert payload["duration_seconds"] == 1.0


def test_audio_transcriber_reports_missing_model(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    whisper = _write_executable(bin_dir / "whisper-cli", "#!/usr/bin/env bash\nexit 0\n")
    ffmpeg = _write_executable(bin_dir / "ffmpeg", "#!/usr/bin/env bash\nexit 0\n")
    ffprobe = _write_executable(bin_dir / "ffprobe", "#!/usr/bin/env bash\nprintf '1\\n'\n")
    client = TestClient(
        create_audio_app(
            AudioRuntimeSettings(
                workspace_root=tmp_path,
                audio_transcriber_model_path=tmp_path / "missing.bin",
                audio_transcriber_binary=str(whisper),
                audio_transcriber_ffmpeg_binary=str(ffmpeg),
                audio_transcriber_ffprobe_binary=str(ffprobe),
            )
        )
    )

    config = client.get("/api/audio-transcriber/config").json()

    assert config["available"] is False
    assert config["model"]["status"] == "missing"


def test_agent_audio_proxy_reports_unreachable_runtime(monkeypatch: Any, tmp_path: Path) -> None:
    import agent_runtime.audio_transcriber.service as audio_service

    def _raise(*args: Any, **kwargs: Any):  # pragma: no cover - transport-level fail path
        raise urllib_error.URLError("Audio transcriber offline.")

    monkeypatch.setattr(audio_service.urllib_request, "urlopen", _raise)
    client = TestClient(
        create_agent_app(
            AgentSettings(
                workspace_root=tmp_path,
                audio_transcriber_service_url="http://127.0.0.1:9",
                audio_transcriber_proxy_timeout_seconds=0.2,
            )
        )
    )

    config = client.get("/api/audio-transcriber/config").json()
    assert config["enabled"] is True
    assert config["available"] is False
    assert config["service"]["status"] == "unreachable"

    response = client.post(
        "/api/audio-transcriber/transcribe",
        content=b"audio",
        headers={"content-type": "audio/webm"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Audio runtime is unreachable."


def test_agent_audio_proxy_uses_runtime_control_endpoint(tmp_path: Path) -> None:
    client = TestClient(
        create_agent_app(
            AgentSettings(
                workspace_root=tmp_path,
                audio_transcriber_service_url="http://127.0.0.1:9",
                audio_transcriber_proxy_timeout_seconds=0.2,
            )
        )
    )

    controls = client.post(
        "/api/agent/runtime-controls",
        json={
            "audio_transcriber_service_host": "127.0.0.2",
            "audio_transcriber_service_port": 9912,
        },
    )
    config = client.get("/api/audio-transcriber/config").json()

    assert controls.status_code == 200
    assert controls.json()["audio_transcriber_service_url"] == "http://127.0.0.2:9912"
    assert config["service"]["url"] == "http://127.0.0.2:9912"
