"""macOS gateway settings wrapper."""

from __future__ import annotations

import os
import platform
import subprocess
from functools import lru_cache

from gateway_core.config import Settings


def _default_macos_node_name() -> str:
    explicit = str(os.getenv("GATEWAY_NODE_NAME") or "").strip()
    if explicit:
        return explicit
    try:
        local_hostname = subprocess.check_output(
            ["scutil", "--get", "LocalHostName"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        ).strip()
        if local_hostname:
            return local_hostname
    except Exception:
        pass
    return platform.node().split(".")[0] or "macos"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        node_name=_default_macos_node_name(),
        app_title="OpenFabric macOS Gateway Agent",
        platform="macos",
        platform_label="macOS",
        shell_path=os.getenv("GATEWAY_SHELL", "/bin/bash"),
        command_profile=os.getenv("GATEWAY_COMMAND_PROFILE", "posix-bash-macos"),
        capability_tags=[
            "exec",
            "exec_stream",
            "exec_cancel",
            "python",
            "terminal",
            "posix_shell",
            "bash",
            "macos_cli",
        ],
        enable_llm_runtime=False,
    )


__all__ = ["Settings", "get_settings"]
