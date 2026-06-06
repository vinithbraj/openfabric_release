"""Linux gateway executor compatibility wrapper."""

from gateway_core.executor import (
    CANCELLED_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    cancel_command,
    execute_command,
    stream_command,
)

__all__ = [
    "CANCELLED_EXIT_CODE",
    "TIMEOUT_EXIT_CODE",
    "cancel_command",
    "execute_command",
    "stream_command",
]
