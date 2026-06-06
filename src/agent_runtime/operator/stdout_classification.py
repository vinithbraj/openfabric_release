"""Shell stdout classification helpers for operator execution."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator._shared import *
from agent_runtime.operator.utils import *
from agent_runtime.prompts import prompt_lines

@dataclass(frozen=True)
class ShellStdoutClassification:
    """Classification of non-zero shell stdout under the stdout-primary rule."""

    stdout_usable: bool
    reason: str
    confidence: float
    classifier_source: str

_STDOUT_STRONG_DIAGNOSTIC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bformat modifier is not recognized\b", re.IGNORECASE), "format_modifier_not_recognized"),
    (re.compile(r"\b(?:is\s+)?not recognized\b", re.IGNORECASE), "not_recognized"),
    (
        re.compile(
            r"\b(?:unknown|invalid|unrecognized)\s+(?:option|argument|flag|modifier|parameter|command)\b",
            re.IGNORECASE,
        ),
        "invalid_invocation_argument",
    ),
    (re.compile(r"\bcommand not found\b", re.IGNORECASE), "command_not_found"),
    (re.compile(r"\bpermission denied\b", re.IGNORECASE), "permission_denied"),
    (re.compile(r"\bno such file or directory\b", re.IGNORECASE), "missing_file"),
    (re.compile(r"\bsyntax error\b", re.IGNORECASE), "syntax_error"),
    (re.compile(r"(?im)^\s*usage\s*:", re.IGNORECASE), "usage_output"),
    (re.compile(r"\btry\b.{0,120}\b--help\b", re.IGNORECASE | re.DOTALL), "help_hint_after_failure"),
    (re.compile(r"\b(?:parse|parser|configuration|config)\s+error\b", re.IGNORECASE), "parse_or_config_error"),
    (
        re.compile(
            r"\berror\s*:\s*(?:unknown|invalid|unrecognized|cannot|unable|missing|required|unsupported)\b",
            re.IGNORECASE,
        ),
        "explicit_error_diagnostic",
    ),
)

_STDOUT_AMBIGUOUS_DIAGNOSTIC_RE = re.compile(
    r"\b(?:aborted|cannot|can't|unable|exception|traceback|fatal|error|missing|required|unsupported)\b",
    re.IGNORECASE,
)


def _shell_stdout_candidate(
    *,
    action_kind: str,
    exit_code: int | None,
    stdout: str,
    command_cancelled: bool,
    macro_delivery_failed: bool,
) -> bool:
    return (
        action_kind == "shell_command"
        and exit_code is not None
        and exit_code != 0
        and bool(str(stdout or "").strip())
        and not command_cancelled
        and not macro_delivery_failed
        and exit_code not in {124, 130}
    )


def classify_shell_stdout_deterministic(
    *,
    command: str,
    exit_code: int | None,
    stdout: str,
    stderr: str = "",
) -> ShellStdoutClassification:
    """Classify stdout-only shell failures without invoking the LLM."""

    text = str(stdout or "").strip()
    if not text:
        return ShellStdoutClassification(
            stdout_usable=False,
            reason="stdout_empty",
            confidence=1.0,
            classifier_source="deterministic",
        )
    for pattern, reason in _STDOUT_STRONG_DIAGNOSTIC_PATTERNS:
        if pattern.search(text):
            return ShellStdoutClassification(
                stdout_usable=False,
                reason=reason,
                confidence=0.95,
                classifier_source="deterministic",
            )
    return ShellStdoutClassification(
        stdout_usable=True,
        reason="no_strong_stdout_diagnostic_match",
        confidence=0.65,
        classifier_source="deterministic",
    )


def _stdout_needs_llm_error_judge(stdout: str) -> bool:
    text = str(stdout or "").strip()
    if not text:
        return False
    if text.lower() == "failed":
        return False
    return bool(_STDOUT_AMBIGUOUS_DIAGNOSTIC_RE.search(text))


def _shell_stdout_error_judge_cache_key(
    *,
    command: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
) -> str:
    payload = _stable_json(
        {
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        }
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _shell_stdout_error_judge_prompt(
    *,
    command: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
) -> str:
    return "\n".join(
        [
            *prompt_lines("operator.stdout_classification"),
            "",
            f"command: {_truncate(command, 500)}",
            f"exit_code: {exit_code}",
            f"stdout: {_truncate(stdout, 1200)}",
            f"stderr: {_truncate(stderr, 800)}",
            "",
            "ShellStdoutErrorJudge schema:",
            _stable_json(ShellStdoutErrorJudge.model_json_schema()),
        ]
    )


def _sudo_retry_judge_cache_key(
    *,
    command: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    error: str = "",
) -> str:
    payload = _stable_json(
        {
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "error": error,
        }
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _sudo_retry_judge_prompt(
    *,
    command: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    error: str = "",
) -> str:
    return "\n".join(
        [
            *prompt_lines("operator.sudo_retry_judge"),
            "If stdout/stderr/error primarily show usage/help text, unknown option, invalid option, invalid argument, invalid command syntax, or a help screen, return is_permission_denied=false and sudo_required_or_likely_to_fix=false unless the same output also explicitly says permission denied, root required, or insufficient privileges.",
            "Exit code alone, including exit code 1, 2, or 22, is never enough evidence for sudo.",
            "",
            f"command: {_truncate(command, 500)}",
            f"exit_code: {exit_code}",
            f"stdout: {_truncate(stdout, 1200)}",
            f"stderr: {_truncate(stderr, 1200)}",
            f"error: {_truncate(error, 800)}",
            "",
            "SudoRetryJudge schema:",
            _stable_json(SudoRetryJudge.model_json_schema()),
        ]
    )

__all__ = [name for name in globals() if not name.startswith("__")]
