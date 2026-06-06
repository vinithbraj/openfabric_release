"""OpenFABRIC package metadata for the current runtime surfaces.

Purpose:
    Expose package version metadata and runtime version helpers for the API,
    CLI, and compatibility layers.

Responsibilities:
    Resolve package version, Git revision, and dirty-worktree suffix for API
    identity surfaces.

Data flow / Interfaces:
    Exports __version__, __package_version__, get_runtime_version, and
    get_runtime_version_info for CLI and API surfaces.

Boundaries:
    Performs read-only Git inspection only and must not mutate repository
    state.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

__all__ = [
    "__package_version__",
    "__version__",
    "get_current_runtime_version_info",
    "get_runtime_version",
    "get_runtime_version_info",
    "get_runtime_version_update_status",
]

__package_version__ = "1.0"
_STARTUP_VERSION_INFO: dict[str, object] | None = None


@lru_cache(maxsize=1)
def get_runtime_version() -> str:
    """Get runtime version for the surrounding runtime workflow.

    Inputs:
        Uses module or instance state; no caller-supplied data parameters are required.

    Returns:
        Returns the computed value described by the function name and type hints.

    Used by:
        Used by OpenFABRIC runtime support code paths that import or call agent_runtime.api.__init__.get_runtime_version.
    """
    return str(_startup_runtime_version_info().get("runtime_version") or __package_version__)


@lru_cache(maxsize=1)
def get_runtime_version_info() -> dict[str, object]:
    """Return structured runtime version metadata for UI and diagnostics."""

    return dict(_startup_runtime_version_info())


def get_current_runtime_version_info() -> dict[str, object]:
    """Return uncached version metadata for the currently checked-out repository."""

    return _runtime_version_info_snapshot()


def get_runtime_version_update_status() -> dict[str, object]:
    """Compare the running agent version with the currently checked-out version."""

    running = get_runtime_version_info()
    latest = get_current_runtime_version_info()
    running_hash = str(running.get("git_hash") or "").strip()
    latest_hash = str(latest.get("git_hash") or "").strip()
    check_available = bool(running_hash and latest_hash)
    update_available = check_available and running_hash != latest_hash
    return {
        "status": "update_available" if update_available else "current",
        "check_available": check_available,
        "new_version_available": update_available,
        "restart_recommended": update_available,
        "gateway_restart_recommended": update_available,
        "comparison_key": "git_hash",
        "running": running,
        "latest": latest,
    }


def _startup_runtime_version_info() -> dict[str, object]:
    """Return the immutable version snapshot captured when this process started."""

    global _STARTUP_VERSION_INFO
    if _STARTUP_VERSION_INFO is None:
        _STARTUP_VERSION_INFO = _runtime_version_info_snapshot()
    return dict(_STARTUP_VERSION_INFO)


def _runtime_version_info_snapshot() -> dict[str, object]:
    """Build one uncached runtime version metadata snapshot."""

    repo_root = _find_repo_root(Path(__file__).resolve().parent)
    revision = ""
    dirty = False
    if (repo_root / ".git").exists():
        revision = _run_git_command(repo_root, ["rev-parse", "--short", "HEAD"])
        dirty = _git_worktree_is_dirty(repo_root) if revision else False
    display = __package_version__
    if revision:
        display = f"{__package_version__} {revision}"
    runtime_version = __package_version__
    if revision:
        runtime_version = f"{__package_version__}+{revision}"
        if dirty:
            runtime_version = f"{runtime_version}.dirty"
    return {
        "version": __package_version__,
        "git_hash": revision,
        "dirty": dirty,
        "runtime_version": runtime_version,
        "display": display,
    }


def _git_worktree_is_dirty(repo_root: Path) -> bool:
    """Handle the internal git worktree is dirty helper path for this module.

    Inputs:
        Receives repo_root for this function; type hints and validators define accepted shapes.

    Returns:
        Returns the computed value described by the function name and type hints.

    Used by:
        Used by OpenFABRIC runtime support code paths that import or call agent_runtime.api.__init__._git_worktree_is_dirty.
    """
    status = _run_git_command(repo_root, ["status", "--short"])
    return bool(status)


def _find_repo_root(start: Path) -> Path:
    """Find the nearest Git repository root at or above the given path."""

    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def _run_git_command(repo_root: Path, args: list[str]) -> str:
    """Handle the internal run git command helper path for this module.

    Inputs:
        Receives repo_root, args for this function; type hints and validators define accepted shapes.

    Returns:
        Returns the computed value described by the function name and type hints.

    Used by:
        Used by OpenFABRIC runtime support code paths that import or call agent_runtime.api.__init__._run_git_command.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


__version__ = get_runtime_version()
