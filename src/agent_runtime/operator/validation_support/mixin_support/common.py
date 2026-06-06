"""Shared imports and constants for validation mixin support modules."""

from __future__ import annotations

from agent_runtime.operator.validation_support.common import *
from agent_runtime.operator.validation_support.context import *
from agent_runtime.operator.validation_support.generated_python import *
from agent_runtime.operator.validation_support.validator_core import *


class _ValidationConstantsMixin:

    _STREAMING_PRIOR_TRANSFORM_VERBS = {
        "aggregate",
        "analyze",
        "calculate",
        "compare",
        "count",
        "extract",
        "filter",
        "rank",
        "select",
        "sort",
        "summarize",
        "transform",
    }
    _STREAMING_PRIOR_CONSUMER_VERBS = {
        "append",
        "create",
        "deliver",
        "execute",
        "export",
        "fetch",
        "inspect",
        "lookup",
        "open",
        "persist",
        "post",
        "publish",
        "query",
        "read",
        "run",
        "save",
        "send",
        "store",
        "upload",
        "use",
        "write",
    }
    _STREAMING_PRIOR_LITERAL_MIN_CHARS = 80
    _PER_ENTITY_SCOPE_RE = re.compile(r"\b(?:for each|per|each|every)\b", re.IGNORECASE)
    _ALL_ENTITY_SCOPE_RE = re.compile(r"\ball\s+[a-z][a-z0-9_.-]*(?:\s+[a-z][a-z0-9_.-]*){0,3}", re.IGNORECASE)
    _PER_ENTITY_REPORT_RE = re.compile(
        r"\b(?:extract|find|get|list|show|report|summari[sz]e|table|date|time|"
        r"status|size|oldest|latest|earliest|newest|first|last|largest|smallest)\b",
        re.IGNORECASE,
    )
    _PER_ENTITY_AGGREGATE_RE = re.compile(
        r"\b(?:total|sum|count|average|avg|aggregate|combined|overall)\b",
        re.IGNORECASE,
    )
    _PER_ENTITY_SCALAR_SELECTOR_RE = re.compile(
        r"\b(?:head|tail)\s+(?:-[n ]\s*)?1\b"
        r"|\b(?:head|tail)\s+-1\b"
        r"|\bsed\s+-n\s+['\"]?\s*(?:1p|\$p)\s*['\"]?",
        re.IGNORECASE,
    )
    _PER_ENTITY_CURRENT_ONLY_SELECTOR_RE = re.compile(
        r"\bgit\s+(?:log|show|rev-list)\b[^\n|;&]*\bHEAD\b"
        r"|\bgit\s+rev-parse\b[^\n|;&]*\bHEAD\b"
        r"|\b(?:current|checked[- ]out|active|selected|default)\s+"
        r"(?:branch|entity|item|target|record|row)\b",
        re.IGNORECASE,
    )
    _PER_ENTITY_ENUMERATION_RE = re.compile(
        r"\bfor\s+[A-Za-z_][A-Za-z0-9_]*\s+in\b"
        r"|\bwhile\s+(?:IFS=.*\s+)?read\b"
        r"|\b(?:xargs|parallel|foreach)\b"
        r"|\bfor[-_]?each\b"
        r"|\bmap\s*\(",
        re.IGNORECASE,
    )
    _STREAMING_READ_ONLY_REPORT_RE = re.compile(
        r"\b(?:analy[sz]e|calculate|check|compare|count|discover|extract|find|get|"
        r"gather|inspect|list|measure|observe|print|query|read|retrieve|report|"
        r"return|show|summari[sz]e|verify|output|emit|display)\b"
        r"|\b(?:info|information|metadata|metrics?|stats?|statistics|status|"
        r"usage|available|free|current)\b",
        re.IGNORECASE,
    )
    _STREAMING_STRONG_MUTATION_RE = re.compile(
        r"\b(?:add|apply|build|checkout|configure|copy|create|delete|deploy|edit|"
        r"install|launch|merge|move|pull|publish|push|rebase|remove|rename|"
        r"restart|stage|start|stop|sync|synchroni[sz]e|transfer|uninstall|"
        r"update|upgrade|write)\b"
        r"|\bcommit\b(?!\s+(?:date|dates|hash|hashes|history|histories|log|logs|"
        r"message|messages|metadata|info|information|summary|summaries)\b)",
        re.IGNORECASE,
    )
    _STREAMING_PRIOR_BINDING_INPUT_NAMES = (
        "stdout",
        "output",
        "records",
        "rows",
        "data",
        "text",
    )
    _STREAMING_PRIOR_SHELL_CONTENT_INPUT_NAMES = {
        "answer",
        "body",
        "commit",
        "commit_hash",
        "content",
        "count",
        "data",
        "hash",
        "id",
        "ids",
        "identifier",
        "key",
        "link",
        "message",
        "output",
        "payload",
        "record",
        "records",
        "revision",
        "row",
        "rows",
        "sha",
        "sql",
        "result",
        "summary",
        "table",
        "text",
        "total",
        "total_size",
        "uri",
        "url",
        "value",
    }
    _STREAMING_PRIOR_SHELL_FILE_INPUT_NAMES = {
        "destination",
        "file",
        "file_name",
        "filename",
        "path",
        "target",
        "target_file",
    }
    _SHELL_LITERAL_ONLY_INPUT_NAMES = {
        "commit_message",
    }
    _SAME_PLAN_RUNTIME_LITERAL_SKIP_WORDS = {
        "bash",
        "cat",
        "cd",
        "curl",
        "docker",
        "echo",
        "find",
        "grep",
        "head",
        "jq",
        "mkdir",
        "printf",
        "psql",
        "python",
        "python3",
        "sed",
        "sh",
        "sort",
        "sqlite3",
        "tail",
        "tee",
        "tr",
        "uniq",
        "xargs",
    }
    _STREAMING_PRIOR_MATCH_STOPWORDS = {
        "a",
        "all",
        "and",
        "as",
        "calculate",
        "computed",
        "current",
        "for",
        "from",
        "give",
        "in",
        "list",
        "of",
        "output",
        "prior",
        "requested",
        "result",
        "results",
        "size",
        "step",
        "task",
        "the",
        "their",
        "this",
        "to",
        "total",
        "with",
    }


__all__ = [
    name
    for name in globals()
    if not name.startswith("__") and name != "_ValidationConstantsMixin"
]
