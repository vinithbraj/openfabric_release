from __future__ import annotations

import os
import platform as platform_module
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


def _env_bool(value: str | None, *, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _env_bool_optional(name: str) -> bool | None:
    if name not in os.environ:
        return None
    return _env_bool(os.getenv(name))


def _detect_platform() -> str:
    value = str(os.getenv("GATEWAY_PLATFORM") or "").strip().lower()
    if value in {"linux", "macos", "windows"}:
        return value
    system = platform_module.system().strip().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    if system == "windows":
        return "windows"
    return "unknown"


def _platform_label(value: str) -> str:
    return {
        "linux": "Linux",
        "macos": "macOS",
        "windows": "Windows",
        "unknown": "Unknown",
    }.get(str(value or "").strip().lower(), str(value or "Unknown").strip() or "Unknown")


def _platform_version(value: str) -> str:
    platform_name = str(value or "").strip().lower()
    if platform_name == "macos":
        version = platform_module.mac_ver()[0]
        return version or platform_module.release()
    return platform_module.release()


def _default_shell() -> str:
    return str(os.getenv("GATEWAY_SHELL") or "/bin/bash").strip() or "/bin/bash"


def _default_command_profile(platform_name: str) -> str:
    explicit = str(os.getenv("GATEWAY_COMMAND_PROFILE") or "").strip()
    if explicit:
        return explicit
    if platform_name == "macos":
        return "posix-bash-macos"
    if platform_name == "linux":
        return "posix-bash-linux"
    if platform_name == "windows":
        return "windows"
    return "posix-bash"


def _default_capability_tags(platform_name: str) -> list[str]:
    tags = ["exec", "exec_stream", "exec_cancel", "python", "terminal", "posix_shell", "bash"]
    if platform_name == "macos":
        tags.append("macos_cli")
    elif platform_name == "linux":
        tags.append("linux_cli")
    elif platform_name == "windows":
        tags.append("windows_cli")
    return tags


def _parse_capability_tags(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


class Settings(BaseModel):
    node_name: str = Field(default_factory=lambda: os.getenv("GATEWAY_NODE_NAME", "localhost"))
    bind_host: str = Field(default_factory=lambda: os.getenv("GATEWAY_BIND_HOST", "127.0.0.1"))
    bind_port: int = Field(default_factory=lambda: int(os.getenv("GATEWAY_BIND_PORT", "8787")))
    exec_timeout_seconds: float = Field(default_factory=lambda: float(os.getenv("GATEWAY_EXEC_TIMEOUT_SECONDS", "0")))
    terminal_idle_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("GATEWAY_TERMINAL_IDLE_TIMEOUT_SECONDS", "3600"))
    )
    trace_commands: bool = Field(
        default_factory=lambda: os.getenv("GATEWAY_TRACE_COMMANDS", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    workdir: Path | None = Field(
        default_factory=lambda: Path(os.getenv("GATEWAY_WORKDIR")).expanduser() if os.getenv("GATEWAY_WORKDIR") else None
    )
    app_title: str = Field(default_factory=lambda: os.getenv("GATEWAY_APP_TITLE", "OpenFabric Gateway Agent"))
    platform: str = Field(default_factory=_detect_platform)
    platform_label: str = ""
    platform_version: str = ""
    architecture: str = Field(default_factory=lambda: platform_module.machine() or "")
    shell_path: str = Field(default_factory=_default_shell)
    command_profile: str = ""
    capability_tags: list[str] = Field(default_factory=list)
    enable_llm_runtime: bool | None = Field(default_factory=lambda: _env_bool_optional("GATEWAY_ENABLE_LLM_RUNTIME"))

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        normalized_node = str(self.node_name or "").strip()
        self.node_name = normalized_node or "localhost"
        normalized_platform = str(self.platform or "").strip().lower()
        self.platform = normalized_platform if normalized_platform in {"linux", "macos", "windows"} else "unknown"
        self.platform_label = str(self.platform_label or "").strip() or _platform_label(self.platform)
        self.platform_version = str(self.platform_version or "").strip() or _platform_version(self.platform)
        self.architecture = str(self.architecture or "").strip() or platform_module.machine()
        self.shell_path = str(self.shell_path or "").strip() or _default_shell()
        self.command_profile = str(self.command_profile or "").strip() or _default_command_profile(self.platform)
        env_tags = _parse_capability_tags(os.getenv("GATEWAY_CAPABILITY_TAGS"))
        self.capability_tags = list(dict.fromkeys([*(self.capability_tags or []), *env_tags]))
        if not self.capability_tags:
            self.capability_tags = _default_capability_tags(self.platform)
        if self.enable_llm_runtime is None:
            self.enable_llm_runtime = self.platform != "macos"
        if self.bind_port <= 0 or self.bind_port > 65535:
            raise ValueError("GATEWAY_BIND_PORT must be between 1 and 65535.")
        if self.exec_timeout_seconds < 0:
            raise ValueError("GATEWAY_EXEC_TIMEOUT_SECONDS must be zero or greater; zero disables task timeouts.")
        if self.terminal_idle_timeout_seconds <= 0:
            raise ValueError("GATEWAY_TERMINAL_IDLE_TIMEOUT_SECONDS must be greater than zero.")
        if self.workdir is not None:
            self.workdir = self.workdir.resolve()
            if not self.workdir.exists():
                raise ValueError(f"GATEWAY_WORKDIR does not exist: {self.workdir}")
            if not self.workdir.is_dir():
                raise ValueError(f"GATEWAY_WORKDIR is not a directory: {self.workdir}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
