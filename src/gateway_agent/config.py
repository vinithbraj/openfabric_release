"""Linux gateway settings wrapper."""

from __future__ import annotations

import os
from functools import lru_cache

from gateway_core.config import Settings as CoreSettings


class Settings(CoreSettings):
    """Linux-default gateway settings while preserving the shared core model."""

    def __init__(self, **data):
        data.setdefault("app_title", "OpenFabric Gateway Agent")
        data.setdefault("platform", "linux")
        data.setdefault("platform_label", "Linux")
        data.setdefault("shell_path", os.getenv("GATEWAY_SHELL", "/bin/bash"))
        data.setdefault("command_profile", os.getenv("GATEWAY_COMMAND_PROFILE", "posix-bash-linux"))
        data.setdefault(
            "capability_tags",
            ["exec", "exec_stream", "exec_cancel", "python", "terminal", "posix_shell", "bash", "linux_cli"],
        )
        super().__init__(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


__all__ = ["Settings", "get_settings"]
