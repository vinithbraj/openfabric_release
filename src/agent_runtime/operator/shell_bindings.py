"""Shell placeholder, input-binding, and literal-payload helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator._shared import *
from agent_runtime.operator.utils import *

_SIMPLE_BRACE_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
_MUSTACHE_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_ANGLE_PLACEHOLDER_RE = re.compile(r"<[A-Za-z_][A-Za-z0-9_]*(?:[ _-][A-Za-z0-9_]+)*>")
_LITERAL_PAYLOAD_PLACEHOLDER_RE = re.compile(r"\[provided [^\]\n]{1,160} payload\]", re.IGNORECASE)
_SHELL_VARIABLE_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_SHELL_ASSIGNMENT_RE = re.compile(r"(?:^|[\s;&|])([A-Za-z_][A-Za-z0-9_]*)=")
_COMMON_SHELL_VARIABLES = {
    "HOME",
    "IFS",
    "LANG",
    "LC_ALL",
    "OLDPWD",
    "PATH",
    "PWD",
    "SHELL",
    "TMPDIR",
    "USER",
}
_SHELL_INPUT_BINDING_MODES = {"off", "confirm_bound", "allow"}
_SHELL_INPUT_BINDING_ENV_PREFIX = "OF_INPUT_"
_SHELL_INPUT_BINDING_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHELL_INPUT_BINDING_MAX_CHARS = 4096
_SHELL_LITERAL_INPUT_MAX_CHARS = 20000
_SHELL_STDIN_MAX_CHARS = 20000
_TYPEIN_LITERAL_INPUT_ALIASES = {
    "auth",
    "credential",
    "credentials",
    "pass",
    "passphrase",
    "password",
    "secret",
    "sudo_password",
    "token",
    "typein",
}
_SHELL_STDIN_FILE_FLAGS = {
    "-F",
    "-f",
    "--file",
    "--input",
    "--stdin",
    "--data",
    "--data-binary",
    "--upload-file",
}
_SHELL_STDIN_DASH_COMMANDS = {"bash", "cat", "node", "perl", "python", "python3", "ruby", "sh"}
_SHELL_STDIN_READING_COMMANDS = {"tee", "xargs"}
_SHELL_STDIN_READING_BUILTINS = {"read"}


def _shell_input_bindings_mode_from_value(value: Any) -> str:
    mode = str(value or "allow").strip().lower()
    return mode if mode in _SHELL_INPUT_BINDING_MODES else "allow"


def _shell_input_bindings_mode_from_request(user_request: UserRequest) -> str:
    for context in (user_request.session_context, user_request.safety_context):
        if not isinstance(context, dict):
            continue
        if "shell_input_bindings_mode" in context:
            return _shell_input_bindings_mode_from_value(context.get("shell_input_bindings_mode"))
    return "allow"


def _typein_macro_input_name_from_request(user_request: UserRequest) -> str | None:
    """Return the reserved input name for the first available typein macro."""

    for context in (user_request.session_context, user_request.safety_context):
        for macro in user_macro_summaries_from_context(context):
            if str(macro.get("kind") or "") == "typein":
                input_name = str(macro.get("input_name") or "").strip()
                if input_name:
                    return input_name
        for macro in private_user_macros_from_context(context):
            if str(macro.get("kind") or "") == "typein":
                input_name = str(macro.get("input_name") or "").strip()
                if input_name:
                    return input_name
    return None


def _input_name_looks_like_typein_alias(input_name: Any) -> bool:
    """Return whether an LLM-authored input name is probably an alias for typein."""

    lowered = re.sub(r"[^a-z0-9]+", "_", str(input_name or "").strip().lower()).strip("_")
    if not lowered:
        return False
    if lowered in _TYPEIN_LITERAL_INPUT_ALIASES:
        return True
    return any(part in _TYPEIN_LITERAL_INPUT_ALIASES for part in lowered.split("_"))


def _shell_binding_env_name(input_name: str) -> str:
    name = str(input_name or "").strip()
    if not _SHELL_INPUT_BINDING_NAME_RE.match(name):
        raise ValueError(
            "Shell input binding names must match [A-Za-z_][A-Za-z0-9_]* "
            "so they can map to safe OF_INPUT_* environment variables."
        )
    return f"{_SHELL_INPUT_BINDING_ENV_PREFIX}{name.upper()}"


def _shell_variable_name(placeholder: str) -> str | None:
    text = str(placeholder or "").strip()
    if not text.startswith("$"):
        return None
    if text.startswith("${") and text.endswith("}"):
        return text[2:-1]
    return text[1:]


def _shell_command_references_env(command: str, env_name: str) -> bool:
    pattern = re.compile(
        r"\$(?:\{" + re.escape(env_name) + r"\}|" + re.escape(env_name) + r"\b)"
    )
    return bool(pattern.search(_mask_single_quoted_shell_segments(str(command or ""))))


def _shell_command_has_inline_stdin_source(command: str) -> bool:
    """Return whether the command itself supplies stdin through shell syntax."""

    text = _mask_single_quoted_shell_segments(str(command or ""))
    return bool(re.search(r"(\||<<<?|<)", text))


def _shell_command_external_stdin_reason(command: str) -> str:
    """Return a generic reason a captured shell command appears to require stdin."""

    text = str(command or "").strip()
    if not text or _shell_command_has_inline_stdin_source(text):
        return ""
    try:
        tokens = shlex.split(text, comments=False, posix=True)
    except ValueError:
        return ""
    if not tokens:
        return ""
    command_tokens = [token for token in tokens if token not in {"command", "env"}]
    base = Path(command_tokens[0]).name if command_tokens else ""
    base = base.lower()
    args: list[str] = []
    skip_next = False
    redirection_ops = {">", ">>", "1>", "1>>", "2>", "2>>", "&>", ">&"}
    for token in command_tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in redirection_ops:
            skip_next = True
            continue
        if re.match(r"^(?:[12]?>>?|&>)", token):
            continue
        args.append(token)
    if base in _SHELL_STDIN_READING_BUILTINS:
        return f"{base} reads from stdin but no stdin payload was declared."
    if base in _SHELL_STDIN_READING_COMMANDS:
        return f"{base} reads from stdin but no stdin payload was declared."
    if base == "cat":
        data_args = [token for token in args if not token.startswith("-")]
        if not data_args:
            return "cat reads from stdin but no stdin payload was declared."
    for index, token in enumerate(command_tokens):
        if token == "@-":
            return "Command uses @- to read from stdin but no stdin payload was declared."
        if token != "-":
            continue
        previous = command_tokens[index - 1] if index > 0 else ""
        if previous in _SHELL_STDIN_FILE_FLAGS:
            return (
                f"Command option {previous} reads payload from '-' stdin, "
                "but no stdin payload was declared."
            )
        if base in _SHELL_STDIN_DASH_COMMANDS:
            return f"{base} reads program/input from '-' stdin but no stdin payload was declared."
    return ""


def _action_stdin_binding_name(action: OperatorAction) -> str:
    if action.kind != "shell_command" or action.stdin_mode != "input_binding":
        return ""
    return str(action.stdin_input_name or "").strip()


def _action_stdin_preview(action: OperatorAction) -> dict[str, Any]:
    mode = str(getattr(action, "stdin_mode", "none") or "none")
    if mode == "literal":
        text = str(action.stdin_text or "")
        return {
            "stdin_mode": mode,
            "stdin_length": len(text),
            "stdin_preview": _truncate(text, 500),
        }
    if mode == "input_binding":
        return {
            "stdin_mode": mode,
            "stdin_input_name": str(action.stdin_input_name or ""),
        }
    return {"stdin_mode": "none"}


def _captured_foreground_service_reason(command: str) -> str:
    """Return why a captured shell command looks like a foreground service start."""

    try:
        tokens = [str(token or "").lower() for token in shlex.split(str(command or ""))]
    except ValueError:
        return ""
    if not tokens:
        return ""
    if "-d" in tokens or "--detach" in tokens or "--no-start" in tokens:
        return ""

    def _has_docker_compose_up() -> bool:
        for index, token in enumerate(tokens):
            if token == "docker" and index + 1 < len(tokens) and tokens[index + 1] == "compose":
                return "up" in tokens[index + 2 :]
            if token == "docker-compose":
                return "up" in tokens[index + 1 :]
        return False

    if _has_docker_compose_up():
        return (
            "docker compose up without -d attaches to service output and may not complete "
            "under captured execution."
        )
    return ""


def _docker_compose_file_option_order_reason(command: str) -> str:
    try:
        tokens = [str(token or "").lower() for token in shlex.split(str(command or ""))]
    except ValueError:
        return ""
    if not tokens:
        return ""
    compose_start = -1
    for index, token in enumerate(tokens):
        if token == "docker" and index + 1 < len(tokens) and tokens[index + 1] == "compose":
            compose_start = index + 2
            break
        if token == "docker-compose":
            compose_start = index + 1
            break
    if compose_start < 0:
        return ""
    file_option_indexes = [
        index for index, token in enumerate(tokens[compose_start:], start=compose_start)
        if token in {"-f", "--file"}
    ]
    if not file_option_indexes:
        return ""
    subcommand_indexes = [
        index for index, token in enumerate(tokens[compose_start:], start=compose_start)
        if token in {"up", "down", "ps", "logs", "pull", "restart", "stop", "start", "build"}
    ]
    if not subcommand_indexes:
        return ""
    if min(file_option_indexes) > min(subcommand_indexes):
        return (
            "Docker Compose -f/--file is a global compose option and must appear "
            "before the subcommand."
        )
    return ""


def _shell_path_output_absolute_reason(command: str) -> str:
    """Return why a shell path-discovery command may emit relative paths."""

    text = str(command or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(
        marker in lowered
        for marker in (
            "realpath",
            "readlink -f",
            "$pwd",
            "${pwd}",
            "$(pwd",
            "`pwd",
        )
    ):
        return ""
    try:
        tokens = [str(token or "") for token in shlex.split(text, comments=False, posix=True)]
    except ValueError:
        return ""
    for index, token in enumerate(tokens):
        if Path(token).name != "find":
            continue
        path_tokens: list[str] = []
        for candidate in tokens[index + 1 :]:
            if candidate in {"(", ")", "!", "-o", "-a", ","}:
                break
            if candidate.startswith("-"):
                break
            path_tokens.append(candidate)
        if not path_tokens:
            return "find without an explicit search root defaults to relative '.' output."
        relative_roots = [
            path
            for path in path_tokens
            if path and not Path(path).expanduser().is_absolute()
        ]
        if relative_roots:
            return (
                "find is searching relative path(s) "
                f"{', '.join(relative_roots)!r}, so stdout may contain relative paths."
            )
    return ""


def _shell_input_value_to_text(value: Any) -> str:
    """Return a stable string representation for shell literal inputs."""

    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, bool, int, float)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _action_shell_inputs_preview(action: OperatorAction) -> list[dict[str, Any]]:
    """Return safe previews for shell literal inputs exposed as OF_INPUT_* vars."""

    if action.kind != "shell_command" or not action.inputs:
        return []
    previews: list[dict[str, Any]] = []
    for input_name, raw_value in sorted(dict(action.inputs or {}).items()):
        try:
            env_name = _shell_binding_env_name(str(input_name))
        except ValueError:
            env_name = ""
        text = _shell_input_value_to_text(raw_value)
        previews.append(
            {
                "input_name": str(input_name),
                "env_name": env_name,
                "value_length": len(text),
                "value_preview": _truncate(text, 500),
            }
        )
    return previews


def _mask_single_quoted_shell_segments(command: str) -> str:
    """Return command text with single-quoted spans blanked for placeholder scans."""

    chars = list(command)
    in_single_quote = False
    index = 0
    while index < len(chars):
        char = chars[index]
        if char == "'" and not in_single_quote:
            in_single_quote = True
            index += 1
            continue
        if char == "'" and in_single_quote:
            in_single_quote = False
            index += 1
            continue
        if in_single_quote:
            chars[index] = " "
        index += 1
    return "".join(chars)


def unresolved_shell_placeholders(command: str) -> list[str]:
    """Return template-like placeholders not resolved inside one concrete shell command."""

    text = str(command or "")
    placeholders: list[str] = []
    placeholders.extend(match.group(0) for match in _MUSTACHE_PLACEHOLDER_RE.finditer(text))
    for match in _SIMPLE_BRACE_PLACEHOLDER_RE.finditer(text):
        prefix = text[match.start() - 1] if match.start() > 0 else ""
        if prefix in {"%", "$"}:
            continue
        placeholders.append(match.group(0))
    placeholders.extend(match.group(0) for match in _ANGLE_PLACEHOLDER_RE.finditer(text))

    scan_text = _mask_single_quoted_shell_segments(text)
    assigned_names = set(_SHELL_ASSIGNMENT_RE.findall(scan_text))
    for match in _SHELL_VARIABLE_RE.finditer(scan_text):
        name = str(match.group(1) or match.group(2) or "")
        if not name or name in assigned_names or name in _COMMON_SHELL_VARIABLES:
            continue
        placeholders.append(match.group(0))

    seen: set[str] = set()
    unique: list[str] = []
    for placeholder in placeholders:
        if placeholder in seen:
            continue
        seen.add(placeholder)
        unique.append(placeholder)
    return unique


_FILE_OUTPUT_REDIRECT_RE = re.compile(r"(?:^|[\s;&|])(?:1?>{1,2})\s*(?![&])\S+")
_TEE_FILE_WRITE_RE = re.compile(r"(?:^|[\s;&|])tee\s+(?:-[A-Za-z]*a[A-Za-z]*\s+)?\S+")
_HEREDOC_DELIMITER_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")


def _quoted_shell_content_mask(command: str) -> list[bool]:
    """Return a character mask for content inside shell single/double quotes."""

    text = str(command or "")
    in_single_quote = False
    in_double_quote = False
    escaped = False
    mask = [False] * len(text)
    for index, char in enumerate(text):
        if in_single_quote:
            if char == "'":
                in_single_quote = False
            else:
                mask[index] = True
            continue
        if in_double_quote:
            if escaped:
                mask[index] = True
                escaped = False
                continue
            if char == "\\":
                mask[index] = True
                escaped = True
                continue
            if char == '"':
                in_double_quote = False
            else:
                mask[index] = True
            continue
        if char == "'":
            in_single_quote = True
        elif char == '"':
            in_double_quote = True
    return mask


def _mask_quoted_shell_segments(command: str) -> str:
    """Return command text with shell-quoted content blanked for syntax scans."""

    chars = list(str(command or ""))
    quote_mask = _quoted_shell_content_mask(command)
    for index, masked in enumerate(quote_mask):
        if masked:
            chars[index] = " "
    return "".join(chars)


def _placeholder_inside_shell_quotes(command: str, placeholder: str) -> bool:
    """Return whether a placeholder-like token appears inside shell literal quotes."""

    text = str(command or "")
    quote_mask = _quoted_shell_content_mask(text)
    start = 0
    while True:
        index = text.find(placeholder, start)
        if index < 0:
            return False
        end = index + len(placeholder)
        if any(quote_mask[index:end]):
            return True
        start = end


def _command_writes_quoted_literal_payload(command: str, placeholders: list[str]) -> bool:
    """Return whether a quoted placeholder-like token is being written as file content."""

    text = str(command or "")
    if not any(_placeholder_inside_shell_quotes(text, placeholder) for placeholder in placeholders):
        return False
    syntax_text = _mask_quoted_shell_segments(text)
    return bool(
        _FILE_OUTPUT_REDIRECT_RE.search(syntax_text)
        or _TEE_FILE_WRITE_RE.search(syntax_text)
    )


def _has_shell_heredoc_operator(command: str) -> bool:
    """Return whether command contains a shell heredoc redirection outside quotes."""

    text = str(command or "")
    in_single_quote = False
    in_double_quote = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_single_quote:
            if char == "'":
                in_single_quote = False
            index += 1
            continue
        if in_double_quote:
            if escaped:
                escaped = False
                index += 1
                continue
            if char == "\\":
                escaped = True
                index += 1
                continue
            if char == '"':
                in_double_quote = False
            index += 1
            continue
        if char == "'":
            in_single_quote = True
            index += 1
            continue
        if char == '"':
            in_double_quote = True
            index += 1
            continue
        if text.startswith("<<", index):
            cursor = index + 2
            if cursor < len(text) and text[cursor] == "-":
                cursor += 1
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            quote = text[cursor] if cursor < len(text) and text[cursor] in {"'", '"'} else ""
            if quote:
                cursor += 1
            start = cursor
            while cursor < len(text) and text[cursor] in _HEREDOC_DELIMITER_CHARS:
                cursor += 1
            if cursor > start and (not quote or (cursor < len(text) and text[cursor] == quote)):
                return True
        index += 1
    return False


def _literal_payload_placeholder_evidence(command: str, placeholders: list[str]) -> list[str]:
    """Return evidence that placeholder-like syntax may be literal file payload text."""

    text = str(command or "")
    if not placeholders:
        return []
    has_heredoc = _has_shell_heredoc_operator(text)
    writes_quoted_payload = _command_writes_quoted_literal_payload(text, placeholders)
    if not has_heredoc and not writes_quoted_payload:
        return []
    lines = text.splitlines()
    evidence: list[str] = []
    if has_heredoc:
        evidence.append("command_contains_heredoc_literal_payload")
    if writes_quoted_payload:
        evidence.append("command_writes_quoted_literal_payload_to_file")
    for placeholder in placeholders[:6]:
        for line in lines:
            if placeholder in line:
                evidence.append(_truncate(line.strip(), 240))
                break
    return evidence[:8]

__all__ = [name for name in globals() if not name.startswith("__")]
