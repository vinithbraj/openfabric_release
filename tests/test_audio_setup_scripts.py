from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_audio_setup_installs_prerequisites_and_downloads_default_model() -> None:
    script = _read("setup-audio-transcriber.sh")

    assert "REPO_ROOT=\"${SCRIPT_DIR}\"" in script
    assert "vendor/audio-transcriber" in script
    assert "vendored_cli_path" in script
    assert "scripts/build-whisper-cli.sh" in script
    assert "VENDORED_MODEL_BASE_PATH" in script
    assert "VENDORED_MODEL_LARGE_PATH" in script
    assert "apt-get install -y git cmake build-essential ffmpeg curl ca-certificates" in script
    assert "brew install git cmake ffmpeg curl" in script
    assert "AOR_DOWNLOAD_WHISPER_MODEL" in script
    assert "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin" in script
    assert "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin" in script
    assert "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin" in script


def test_startup_audio_supports_model_tier_flags() -> None:
    startup = _read("startup-audio.sh")

    assert "--low" in startup
    assert "--med" in startup
    assert "--high" in startup
    assert "AOR_AUDIO_TRANSCRIBER_MODEL_TIER" in startup
    assert "AOR_AUDIO_TRANSCRIBER_MODEL_TIER:-low" in startup
    assert "resolve_model_path" in startup


def test_top_level_startup_serves_agent_audio_and_manual() -> None:
    startup = _read("startup.sh")

    assert "agent_runtime.api.app:create_app" in startup
    assert "--audio-port" in startup
    assert "--manual-port" in startup
    assert "--website-port" in startup
    assert "AOR_AUDIO_RUNTIME_HOST=\"${HOST_OVERRIDE}\"" in startup
    assert "AOR_MANUAL_HOST=\"${HOST_OVERRIDE}\"" in startup
    assert "AOR_WEBSITE_HOST=\"${HOST_OVERRIDE}\"" in startup
    assert "AOR_AUDIO_RUNTIME_PORT=\"${AUDIO_PORT_OVERRIDE:-${AOR_AUDIO_RUNTIME_PORT:-8012}}\"" in startup
    assert "AOR_MANUAL_PORT=\"${MANUAL_PORT_OVERRIDE:-${AOR_MANUAL_PORT:-8013}}\"" in startup
    assert "AOR_WEBSITE_PORT=\"${WEBSITE_PORT_OVERRIDE:-${AOR_WEBSITE_PORT:-8014}}\"" in startup
    assert "AOR_AUDIO_RUNTIME_PROXY_HOST" in startup
    assert "./startup-audio.sh --host" in startup
    assert "./startmanual.sh --host" in startup
    assert "./startwebsite.sh --host" in startup
    assert "AOR_AUDIO_TRANSCRIBER_MODEL_TIER:-low" in startup
    assert "AOR_MANUAL_AUTOSTART" in startup
    assert "AOR_WEBSITE_AUTOSTART" in startup
    assert "cleanup_auxiliary_runtimes" in startup


def test_top_level_install_bootstraps_system_and_audio_dependencies() -> None:
    script = _read("install.sh")

    assert "install_system_deps" in script
    assert "python3-venv" in script
    assert "ffmpeg" in script
    assert "openssl" in script
    assert "AOR_AUDIO_TRANSCRIBER_SETUP_SCRIPT" in script
    assert "setup-audio-transcriber-no-build.sh" in script
    assert "startwebsite.sh" in script
    assert "src/gateway_macos/startup.sh" in script
    assert "sort -V" not in script


def test_no_build_install_uses_no_build_audio_setup() -> None:
    script = _read("install-no-build.sh")
    audio_script = _read("setup-audio-transcriber-no-build.sh")

    assert "AOR_INSTALL_NO_BUILD" in script
    assert "AOR_INSTALL_WHISPER_CPP=0" in script
    assert "AOR_DOWNLOAD_WHISPER_MODEL=0" in script
    assert "AOR_REQUIRE_AUDIO_TRANSCRIBER_SETUP=\"${AOR_REQUIRE_AUDIO_TRANSCRIBER_SETUP:-0}\"" in script
    assert "setup-audio-transcriber-no-build.sh" in script
    assert "git clone" not in audio_script
    assert "cmake --build" not in audio_script
    assert "curl -L" not in audio_script
    assert "No no-build whisper-cli is available; audio transcription will stay disabled." in audio_script
    assert "Run ./setup-audio-transcriber.sh to build whisper-cli" in audio_script
    assert "exit 1" not in audio_script
    assert "${WHISPER_BIN_DIR}/whisper-cli" in audio_script


def test_gateway_installers_bootstrap_python_without_gnu_sort_dependency() -> None:
    linux_script = _read("src/gateway_agent/install.sh")
    macos_script = _read("src/gateway_macos/install.sh")

    assert "python3-venv" in linux_script
    assert "brew install python git" in macos_script
    assert "sort -V" not in linux_script
    assert "sort -V" not in macos_script


def test_docker_audio_service_downloads_model_on_first_start() -> None:
    compose = _read("docker-all-services/docker-compose.yaml")
    build_script = _read("docker-all-services/build.sh")
    dockerfile = _read("docker/server.Dockerfile")
    audio_entrypoint = _read("docker/audio-entrypoint.sh")

    assert "openfabric-audio-entrypoint" in compose
    assert 'AOR_BUILD_AUDIO: "1"' in compose
    assert "--build-arg AOR_BUILD_AUDIO=1" in build_script
    assert "AOR_DOWNLOAD_WHISPER_MODEL: \"1\"" in compose
    assert "ARG AOR_BUILD_AUDIO=0" in dockerfile
    assert 'if [ "${AOR_BUILD_AUDIO}" = "1" ]' in dockerfile
    assert "COPY vendor ./vendor" not in dockerfile
    assert "COPY certs ./certs" not in dockerfile
    assert "openssl" in dockerfile
    assert "scripts/build-whisper-cli.sh" in dockerfile
    assert "vendor/audio-transcriber/linux-x86_64/whisper-cli" not in dockerfile
    assert "COPY docker/audio-entrypoint.sh" in dockerfile
    assert "download_whisper_model_if_missing" in audio_entrypoint
    assert "VENDORED_WHISPER_MODEL" not in audio_entrypoint
    assert "audio_runtime.api.app:create_app" in audio_entrypoint


def test_simple_docker_build_does_not_opt_into_audio_build() -> None:
    build_script = _read("docker/build.sh")
    compose = _read("docker/docker-compose.yaml")
    server_compose = _read("docker/docker-compose.server.yaml")

    assert "AOR_BUILD_AUDIO=1" not in build_script
    assert "AOR_BUILD_AUDIO" not in compose
    assert "AOR_BUILD_AUDIO" not in server_compose
    assert 'AOR_AUDIO_TRANSCRIBER_ENABLED: "0"' in compose
    assert 'AOR_DOWNLOAD_WHISPER_MODEL: "0"' in compose


def test_generated_dev_ssl_cert_is_wired_into_startup_and_docker() -> None:
    startup = _read("startup.sh")
    compose = _read("docker/docker-compose.server.yaml")
    entrypoint = _read("docker/server-entrypoint.sh")

    assert "--https|--ssl" in startup
    assert "CHECKED_IN_SSL_CERTFILE" not in startup
    assert "certs/dev/openfabric-agent.crt" not in startup
    assert "certs/dev/openfabric-agent.key" not in startup
    assert "artifacts/ssl/agent-ui.crt" in startup
    assert "artifacts/ssl/agent-ui.key" in startup
    assert "--ssl-certfile" in startup
    assert 'AOR_SSL_ENABLED: "0"' in compose
    assert "AOR_SSL_CERTFILE:" not in compose
    assert "AOR_SSL_KEYFILE:" not in compose
    assert "CHECKED_IN_SSL_CERTFILE" not in entrypoint
    assert "certs/dev/openfabric-agent.crt" not in entrypoint
    assert "${ARTIFACTS_DIR}/ssl/agent-ui.crt" in entrypoint
    assert "${ARTIFACTS_DIR}/ssl/agent-ui.key" in entrypoint
    assert "--ssl-certfile" in entrypoint


def test_audio_artifacts_are_not_checked_in() -> None:
    whisper_cli = REPO_ROOT / "vendor/audio-transcriber/linux-x86_64/whisper-cli"
    model = REPO_ROOT / "vendor/audio-transcriber/models/ggml-base.en.bin"
    gitignore = _read(".gitignore")

    assert not whisper_cli.exists()
    assert not model.exists()
    assert "/vendor/audio-transcriber/" in gitignore


def test_startup_auto_disables_audio_when_assets_are_missing() -> None:
    startup = _read("startup.sh")
    startup_audio = _read("startup-audio.sh")

    assert "AOR_AUDIO_TRANSCRIBER_ENABLED_WAS_SET" in startup
    assert "Audio transcription disabled because" in startup
    assert "AUDIO_BINARY_READY" in startup
    assert "AUDIO_MODEL_READY" in startup
    assert "Audio runtime not started because" in startup_audio
    assert "LOCAL_VENDOR_WHISPER_CLI" in startup_audio
