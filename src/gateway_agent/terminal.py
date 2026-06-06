"""Linux gateway terminal compatibility wrapper."""

from gateway_core.terminal import (
    TerminalSession,
    _TerminalCommandCapture,
    _terminal_output_needs_user_input,
    cancel_terminal_command,
    cleanup_terminal_sessions,
    close_terminal_session,
    get_terminal_session,
    lookup_terminal_session,
    start_detached_terminal_command,
    stream_terminal_command,
)

__all__ = [
    "TerminalSession",
    "_TerminalCommandCapture",
    "_terminal_output_needs_user_input",
    "cancel_terminal_command",
    "cleanup_terminal_sessions",
    "close_terminal_session",
    "get_terminal_session",
    "lookup_terminal_session",
    "start_detached_terminal_command",
    "stream_terminal_command",
]
