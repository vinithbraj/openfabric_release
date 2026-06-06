"""Operator plan validation logic."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

import base64
import subprocess
import tempfile

from agent_runtime.operator.action_runtime import _COMMON_OPERATOR_PYTHON_MODULES
from agent_runtime.operator._shared import *
from agent_runtime.operator.events import *
from agent_runtime.operator.utils import *
from agent_runtime.operator.shell_bindings import *
from agent_runtime.operator.prompts import *
from agent_runtime.operator.execution_shape import (
    execution_shape_plan_errors,
)

class OperatorValidationError(RuntimeError):
    """Raised when an operator plan cannot pass deterministic validation."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("Operator plan failed validation.")
        self.errors = list(errors)


class DeferredPythonCodeGenerationError(ValueError):
    """Raised when deferred Python code cannot be structurally generated."""

    def __init__(
        self,
        message: str,
        *,
        errors: list[dict[str, Any]],
        rejected_proposal: dict[str, Any] | None,
        function_name: str,
    ) -> None:
        super().__init__(message)
        self.errors = list(errors)
        self.rejected_proposal = dict(rejected_proposal) if isinstance(rejected_proposal, dict) else None
        self.function_name = str(function_name)


class GeneratedPythonRuntimeProofError(ValueError):
    """Raised when concrete operator Python fails proof before execution."""

    def __init__(self, proof: GeneratedPythonProofResult) -> None:
        super().__init__(f"Generated Python failed proof before execution: {proof.reason}")
        self.proof = proof


def _normalize_single_report_plan_references(
    user_request: UserRequest,
    plan: OperatorPlan,
    observability: ObservabilityContext | None = None,
) -> None:
    """Retired execution-shape normalization hook kept for compatibility."""

    del user_request, plan, observability
    return

_PYTHON_REGEX_FUNCTIONS = {
    "compile",
    "findall",
    "finditer",
    "fullmatch",
    "match",
    "search",
    "split",
    "sub",
    "subn",
}


_EXPLICIT_VERIFICATION_REQUEST_RE = re.compile(
    r"\b(verif(?:y|ies|ied|ication)|confirm|prove|validate|cross-check|double-check)\b",
    re.IGNORECASE,
)

_VERIFICATION_ACTION_TEXT_RE = re.compile(
    r"\b(verif(?:y|ies|ied|ication)|confirm|validate|compare|cross-check|double-check)\b",
    re.IGNORECASE,
)

_COMPACT_NUMERIC_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?([A-Za-z]{1,8})\b")

_SHELL_ARITHMETIC_AGGREGATION_RE = re.compile(
    r"\bawk\b(?:(?!\n).)*(?:\+=|sum|total|\$[0-9]+)"
    r"|\bbc\b|\bexpr\b|\$\(\(",
    re.IGNORECASE | re.DOTALL,
)

_SHELL_HUMAN_UNIT_SOURCE_RE = re.compile(
    r"\{\{\s*\.(?:Size|SharedSize|UniqueSize|VirtualSize|DiskUsage|ContentSize)\s*\}\}"
    r"|\b(?:du|df|ls)\b[^|;&\n]*\s-[A-Za-z]*h[A-Za-z]*\b"
    r"|\b--human-readable\b"
    r"|\b(?:size|usage|bytes?)\b[^|;&\n]*\b(?:KB|MB|GB|TB|KiB|MiB|GiB|TiB)\b",
    re.IGNORECASE,
)

_SHELL_CANONICAL_UNIT_OUTPUT_RE = re.compile(
    r"\b(?:nounits|noheader,nounits|--bytes|--block-size=1|--si=false)\b"
    r"|\bnumfmt\b[^|;&\n]*\b--from",
    re.IGNORECASE,
)

_SIZE_TOKEN_SPLIT_RE = re.compile(
    r"\b(?:size|unit)[A-Za-z0-9_]*\s*=\s*[^=\n]+\.split\(\)\s*\n"
    r"(?:(?!\n\s*\n).){0,300}?"
    r"len\(\s*(?:size|unit)[A-Za-z0-9_]*\s*\)\s*(?:!=|<)\s*2",
    re.DOTALL,
)
_SIZE_SUFFIX_BARE_B_FIRST_RE = re.compile(
    r"\.endswith\(\s*['\"]B['\"]\s*\)"
    r"(?:(?!def\s+\w+\s*\().){0,1200}?"
    r"\.endswith\(\s*['\"](?:KB|MB|GB|TB|KIB|MIB|GIB|TIB)['\"]\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_REGEX_DOUBLE_ESCAPED_CLASS_RE = re.compile(r"\\\\[dDsSwWbBAZ]")
_SIZE_PARSER_TEXT_RE = re.compile(r"\b(?:size|sizes|bytes?|unit|units)\b", re.IGNORECASE)

_PROMPT_DIRECTORY_IGNORE_WORDS = {
    "a",
    "all",
    "and",
    "any",
    "branch",
    "branches",
    "count",
    "date",
    "file",
    "files",
    "first",
    "from",
    "give",
    "in",
    "into",
    "last",
    "list",
    "of",
    "or",
    "repo",
    "repository",
    "root",
    "show",
    "summary",
    "table",
    "the",
    "to",
    "total",
    "under",
    "with",
}

_PROMPT_PATH_TOKEN_RE = re.compile(r"(?:`([^`]+)`|\"([^\"]+)\"|'([^']+)'|\b([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)\b)")
_FIND_WILDCARD_PATH_DIR_RE = re.compile(r"(?:^|[*/])([A-Za-z0-9_.-]+)/(?:\*|$|[^*]*)")
_DB_GENERIC_OF_INPUT_RE = re.compile(
    r"\$(?:\{(?P<braced>OF_INPUT_(?:DATABASE|DATABASE_NAME|DB|DBNAME|DB_NAME|DB_PATH|"
    r"DATABASE_PATH|HOST|HOSTNAME|PORT|USER|USERNAME|PASSWORD|PASS|PASSWD))\}"
    r"|(?P<bare>OF_INPUT_(?:DATABASE|DATABASE_NAME|DB|DBNAME|DB_NAME|DB_PATH|"
    r"DATABASE_PATH|HOST|HOSTNAME|PORT|USER|USERNAME|PASSWORD|PASS|PASSWD))\b)"
)
_DB_PROMPT_INTENT_RE = re.compile(
    r"\b(?:catalog|column|columns|database|databases|db|namespace|query|schema|schemas|sql|table|tables)\b",
    re.IGNORECASE,
)
_DB_FILESYSTEM_FALLBACK_RE = re.compile(r"\b(?:fd|find|ls|tree)\b")
_DB_CLIENT_RE = re.compile(r"\b(?:mysql|psql|sqlite3)\b")


__all__ = [name for name in globals() if not name.startswith("__")]
