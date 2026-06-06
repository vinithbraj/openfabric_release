"""Compatibility facade for PostgreSQL-first SQL agent capabilities."""

from __future__ import annotations

# ruff: noqa: F401,F403

from agent_runtime.capabilities.sql_support.common import *
from agent_runtime.capabilities.sql_support.discovery import *
from agent_runtime.capabilities.sql_support.analysis import *
from agent_runtime.capabilities.sql_support.service import *
from agent_runtime.capabilities.sql_support.capabilities import *

__all__ = [name for name in globals() if not name.startswith("__")]
