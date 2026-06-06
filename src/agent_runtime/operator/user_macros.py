"""Deterministic user-authored macros for operator execution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.operator.spoken_controls import normalize_spoken_quoted_segments


USER_MACRO_PRIVATE_CONTEXT_KEY = "_operator_user_macros_private"
USER_MACRO_SUMMARY_CONTEXT_KEY = "operator_user_macro_summaries"
TYPEIN_MACRO_KIND = "typein"
TYPEIN_INPUT_PREFIX = "typein_"
CHECKONLINE_MACRO_KIND = "checkonline"
CHECKONLINEAI_MACRO_KIND = "checkonlineai"
AUTOAPPROVE_MACRO_KIND = "autoapprove"
ADDTOMEMORY_MACRO_KIND = "addtomemory"
DISCOVERDB_MACRO_KIND = "discoverdb"
RUNLATER_MACRO_KIND = "runlater"
REMIND_MACRO_KIND = "remind"
TODO_MACRO_KIND = "todo"
RESTART_MACRO_KIND = "restart"
SLASH_MACRO_CANDIDATE_KIND = "slash_macro_candidate"

_TYPEIN_TOKEN_PATTERN = r"(?:typein|type\s+in|tpein|tpe\s+in)"
_TYPEIN_WORD_RE = re.compile(rf"(?<!\S)/?{_TYPEIN_TOKEN_PATTERN}\b", re.IGNORECASE)
_TYPEIN_QUOTE_RE = re.compile(rf"(?<!\S)/?{_TYPEIN_TOKEN_PATTERN}\s*\"", re.IGNORECASE)
_PARAMETER_KEY_CAPTURE = (
    r"(?P<key>[A-Za-z0-9_.:\-][A-Za-z0-9_.:\- ]{0,159}?)"
    r"(?=$|[,.;!?]|\s+(?:for|to|and|then|please|using)\b)"
)
_TYPEIN_PARAMETER_REF_PATTERNS = (
    re.compile(
        rf"\s+(?:using\s+)?"
        rf"(?:(?P<field>[A-Za-z][A-Za-z0-9 _-]{{0,60}})\s+)?"
        rf"(?:stored|saved)\s+(?:in|under|as)\s+(?:key\s+)?{_PARAMETER_KEY_CAPTURE}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\s+(?:using\s+)?"
        rf"(?:(?P<field>[A-Za-z][A-Za-z0-9 _-]{{0,60}})\s+)?"
        rf"(?:from|in|under|with)\s+(?:the\s+)?(?:parameter\s+store\s+)?(?:key\s+)?"
        rf"{_PARAMETER_KEY_CAPTURE}",
        re.IGNORECASE,
    ),
)
_TYPEIN_STORE_QUERY_RE = re.compile(
    r"\s+(?:using\s+|use\s+)?(?P<query>.+?)\s+(?:from|in)\s+(?:the\s+)?(?:parameter\s+)?store\b",
    re.IGNORECASE,
)
_PARAMETER_STORE_TYPEIN_RE = re.compile(
    r"(?<!\S)(?:use|enter|input)\s+(?P<query>.+?)\s+(?:from|in)\s+(?:the\s+)?(?:parameter\s+)?store\b",
    re.IGNORECASE,
)
_SUDO_PARAMETER_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\buse\s+sudo\s+(?:password\s+)?(?:with|using)\s+(?:the\s+)?"
        r"(?:password\s+)?(?:parameter\s+|param\s+|key\s+)?"
        r"(?P<key>[A-Za-z0-9_.:-]{1,160})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\buse\s+sudo\s+password\s+(?:parameter|param|key)\s+"
        r"(?P<key>[A-Za-z0-9_.:-]{1,160})\b",
        re.IGNORECASE,
    ),
)
_SUDO_PARAMETER_KEY_STOPWORDS = {
    "key",
    "param",
    "parameter",
    "password",
    "sudo",
}
_CREDENTIAL_INPUT_KIND_PATTERNS: tuple[tuple[str, str], ...] = (
    ("passphrase", r"\bpass\s*phrase\b|\bpassphrase\b"),
    ("private_key", r"\bprivate\s+key\b|\bssh\s+key\b|\bkey\s+file\b"),
    ("token", r"\bapi\s*key\b|\bapi[_ -]?token\b|\baccess\s+token\b|\btoken\b"),
    ("password", r"\bpassword\b|\bpasswd\b|\bpasscode\b|\bpin\b"),
    ("credential", r"\bcredential\b|\bcredentials\b|\bauth\b|\blogin\b|\bsecret\b"),
)
_EXACT_PARAMETER_CREDENTIAL_CONTEXT_RE = re.compile(
    r"\b("
    r"git\s+push|push|ssh|sudo|login|log\s+in|authenticate|authentication|"
    r"auth|credential|secret|password|passphrase|token|api\s*key"
    r")\b",
    re.IGNORECASE,
)
_EXACT_PARAMETER_CREDENTIAL_DELIVERY_RE = re.compile(
    r"\b(?:use|using|with|enter|input|type|provide|pass|authenticate|"
    r"authentication|login|log\s+in|connect|push)\b",
    re.IGNORECASE,
)
_EXPLANATORY_CREDENTIAL_QUERY_RE = re.compile(
    r"^\s*(?:what\s+(?:is|are|does|happens?|would|will)|what\s+happens\s+when|"
    r"explain|describe|summari[sz]e|tell\s+me\s+about)\b",
    re.IGNORECASE,
)
_CHECKONLINE_RE = re.compile(
    r"(?<!\S)(?:/\s*checkonline|/\s*check\s+online|slash\s+checkonline|slash\s+check\s+online)\b",
    re.IGNORECASE,
)
_CHECKONLINEAI_RE = re.compile(
    r"(?<!\S)(?:"
    r"/\s*checkonlineai|/\s*check\s+online\s+a\.?\s*i\.?|"
    r"slash\s+checkonlineai|slash\s+check\s+online\s+a\.?\s*i\.?|"
    r"checkonlineai|check\s+online\s+a\.?\s*i\.?"
    r")\b",
    re.IGNORECASE,
)
_CHECKONLINE_PHRASE_RE = re.compile(
    r"\b(?:check(?:ed|ing)?|search(?:ed|ing)?|look(?:ed|ing)?(?:\s+up)?)\s+online\b",
    re.IGNORECASE,
)
_AUTOAPPROVE_TOKEN_RE = (
    r"(?:"
    r"/?\s*auto[\s_-]*approv(?:e|ed)|"
    r"/?\s*auto[\s_-]*approval|"
    r"/?\s*auto[\s_-]*confirm(?:ed)?|"
    r"slash\s+auto[\s_-]*(?:approv(?:e|ed)|approval|confirm(?:ed)?)|"
    r"approve\s+automatically|"
    r"automatically\s+approve"
    r")"
)
_AUTOAPPROVE_RE = re.compile(
    rf"(?:^\s*{_AUTOAPPROVE_TOKEN_RE}(?=$|[\s,.;:!?])|"
    rf"(?<=[\s,.;:!?]){_AUTOAPPROVE_TOKEN_RE}(?=$|[\s,.;:!?])[\s,.;:!?]*$)",
    re.IGNORECASE,
)
_RUNLATER_TOKEN_RE = (
    r"(?:/\s*runlater|/\s*run\s+later|slash\s+runlater|slash\s+run\s+later|"
    r"runlater|run\s+later|agent\s+action|scheduled\s+action)"
)
_RUNLATER_RE = re.compile(
    rf"(?:^\s*{_RUNLATER_TOKEN_RE}(?=$|[\s,.;:!?])|"
    rf"(?<=[\s,.;:!?]){_RUNLATER_TOKEN_RE}\s*$)",
    re.IGNORECASE,
)
_REMIND_TOKEN_RE = (
    r"(?:/\s*remind(?:er)?|slash\s+remind(?:er)?|remind\s+only|reminder\s+only|"
    r"notify\s+only|notification\s+only|save\s+as\s+(?:a\s+)?reminder)"
)
_REMIND_RE = re.compile(
    rf"(?:^\s*{_REMIND_TOKEN_RE}(?=$|[\s,.;:!?])|"
    rf"(?<=[\s,.;:!?]){_REMIND_TOKEN_RE}\s*$)",
    re.IGNORECASE,
)
_TODO_TOKEN_RE = (
    r"(?:/\s*todo|/\s*to\s+do|slash\s+todo|slash\s+to\s+do|"
    r"add\s+(?:a\s+)?todo|save\s+as\s+(?:a\s+)?todo|task\s+reminder|todo|to\s+do)"
)
_TODO_RE = re.compile(
    rf"^\s*{_TODO_TOKEN_RE}(?=$|[\s,.;:!?])",
    re.IGNORECASE,
)
_RESTART_TOKEN_RE = r"(?:/\s*restart|slash\s+restart)"
_RESTART_RE = re.compile(
    rf"(?:^\s*{_RESTART_TOKEN_RE}(?=$|[\s,.;:!?])|"
    rf"(?<=[\s,.;:!?]){_RESTART_TOKEN_RE}\s*$)",
    re.IGNORECASE,
)
_SLASH_MACRO_TOKEN_RE = re.compile(r"(?<!\S)/([A-Za-z][A-Za-z0-9_-]*)(?=$|[\s,.;:!?])")
_KNOWN_SLASH_MACRO_NAMES = {
    TYPEIN_MACRO_KIND,
    "type",
    "tpe",
    "tpein",
    CHECKONLINE_MACRO_KIND,
    CHECKONLINEAI_MACRO_KIND,
    AUTOAPPROVE_MACRO_KIND,
    ADDTOMEMORY_MACRO_KIND,
    "addmemory",
    "add_to_memory",
    "add-to-memory",
    DISCOVERDB_MACRO_KIND,
    RUNLATER_MACRO_KIND,
    REMIND_MACRO_KIND,
    TODO_MACRO_KIND,
    RESTART_MACRO_KIND,
}
_SENSITIVE_MARKERS = (
    "sudo",
    "password",
    "passphrase",
    "token",
    "secret",
    "credential",
    "credentials",
    "auth",
)
_SANITIZED_TYPEIN_PLACEHOLDERS = {"[provided]", "[redacted]"}
_PARAMETER_REF_STRIP_CHARS = ' `\'".,;:'
_PARAMETER_FIELD_QUERY_PATTERNS = (
    ("private_key", r"\bprivate\s+key\b"),
    ("api_key", r"\bapi\s+key\b|\bapikey\b"),
    ("passphrase", r"\bpassphrase\b|\bpass\s+phrase\b"),
    ("password", r"\bpassword\b|\bpasswd\b|\bpass\b"),
    ("token", r"\btoken\b|\baccess\s+token\b|\bauth\s+token\b"),
    ("secret", r"\bsecret\b"),
    ("value", r"\bvalue\b"),
)

_PROMPT_MACRO_REGISTRY = (
    {
        "id": TYPEIN_MACRO_KIND,
        "label": TYPEIN_MACRO_KIND,
        "description": "Send deterministic input to terminal or stdin prompts",
        "template": '/typein "<your text>"',
        "placeholder": "<your text>",
        "aliases": [
            "typein",
            "terminal",
            "stdin",
            "input",
            "type in",
            "tpe in",
            "tpein",
            "type in begin end",
        ],
    },
    {
        "id": CHECKONLINE_MACRO_KIND,
        "label": CHECKONLINE_MACRO_KIND,
        "description": "Look up one compact Duck.ai answer before planning",
        "template": "/checkonline",
        "placeholder": "",
        "aliases": ["online", "web", "duckai", "lookup", "check online", "search online"],
    },
    {
        "id": CHECKONLINEAI_MACRO_KIND,
        "label": CHECKONLINEAI_MACRO_KIND,
        "description": "Fetch Duck.ai context before planning",
        "template": '/checkonlineai "<your search query>"',
        "placeholder": "<your search query>",
        "aliases": [
            "ai",
            "duckai",
            "lookup",
            "check online ai",
            "check online ai begin end",
            "look up online",
            "search online",
        ],
    },
    {
        "id": AUTOAPPROVE_MACRO_KIND,
        "label": AUTOAPPROVE_MACRO_KIND,
        "description": "Auto-approve confirmation prompts for this request only",
        "template": "/autoapprove",
        "placeholder": "",
        "aliases": [
            "approve",
            "auto approve",
            "auto approved",
            "auto approval",
            "auto confirm",
            "autoconfirm",
            "automatically approve",
            "slash auto approve",
        ],
    },
    {
        "id": ADDTOMEMORY_MACRO_KIND,
        "label": ADDTOMEMORY_MACRO_KIND,
        "description": "Digest, categorize, and save the following text as active memory",
        "template": "/addtomemory",
        "placeholder": "<memory text>",
        "aliases": [
            "memory",
            "remember",
            "save memory",
            "add memory",
            "add to memory",
            "slash add to memory",
        ],
    },
    {
        "id": DISCOVERDB_MACRO_KIND,
        "label": DISCOVERDB_MACRO_KIND,
        "description": "Discover PostgreSQL databases and draft Parameter Store profiles",
        "template": (
            '/discoverdb engine=postgres host=<ip> port=<port> '
            'user="<username>" password="<password>"'
        ),
        "placeholder": "<ip>",
        "aliases": [
            "database discovery",
            "db discovery",
            "discover database",
            "postgres discovery",
            "parameter store db",
        ],
    },
    {
        "id": RUNLATER_MACRO_KIND,
        "label": RUNLATER_MACRO_KIND,
        "description": "Force a delayed prompt to run as a scheduled agent action",
        "template": "/runlater",
        "placeholder": "",
        "aliases": ["schedule", "later", "agent", "run later", "scheduled action", "agent action"],
    },
    {
        "id": REMIND_MACRO_KIND,
        "label": REMIND_MACRO_KIND,
        "description": "Force a delayed prompt to save as a reminder notification",
        "template": "/remind",
        "placeholder": "",
        "aliases": ["reminder", "notify", "alert", "reminder only", "notification only", "save as reminder"],
    },
    {
        "id": TODO_MACRO_KIND,
        "label": TODO_MACRO_KIND,
        "description": "Force a prompt to save as a todo reminder notification",
        "template": "/todo",
        "placeholder": "",
        "aliases": ["todo", "to do", "task reminder", "save as todo"],
    },
    {
        "id": RESTART_MACRO_KIND,
        "label": RESTART_MACRO_KIND,
        "description": "Restart the Agent UI server and selected gateway",
        "template": "/restart",
        "placeholder": "",
        "aliases": [
            "restart",
            "restart server",
            "restart gateway",
            "restart server and gateway",
            "restart gateway and server",
        ],
    },
)


class UserMacroParseError(ValueError):
    """Raised when a deterministic user macro cannot be parsed safely."""


def prompt_macro_registry() -> list[dict[str, Any]]:
    """Return browser-safe prompt macro definitions for slash completion."""

    return [dict(item) for item in _PROMPT_MACRO_REGISTRY]


@dataclass(frozen=True)
class UserMacro:
    """One parsed user macro with private value and public metadata."""

    macro_id: str
    kind: str
    input_name: str
    value: str
    redacted: bool
    preview: str = ""
    consumed: bool = False
    deliveries: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""
    parameter_key: str = ""
    parameter_field: str = ""

    def private_payload(self) -> dict[str, Any]:
        payload = {
            "macro_id": self.macro_id,
            "kind": self.kind,
            "input_name": self.input_name,
            "value": self.value,
            "redacted": self.redacted,
            "preview": "" if self.redacted else self.preview,
            "value_length": len(self.value),
            "consumed": self.consumed,
            "deliveries": list(self.deliveries),
        }
        if self.source:
            payload["source"] = self.source
        if self.parameter_key:
            payload["parameter_key"] = self.parameter_key
        if self.parameter_field:
            payload["parameter_field"] = self.parameter_field
        return payload

    def public_summary(self) -> dict[str, Any]:
        summary = {
            "macro_id": self.macro_id,
            "kind": self.kind,
            "input_name": self.input_name,
            "redacted": self.redacted,
            "value_length": len(self.value),
            "delivery_scope": "terminal_prompt_or_declared_stdin",
            "consumed": self.consumed,
        }
        if not self.redacted and self.preview:
            summary["preview"] = self.preview
        if self.source:
            summary["source"] = self.source
        if self.parameter_key:
            summary["parameter_key"] = self.parameter_key
        if self.parameter_field:
            summary["parameter_field"] = self.parameter_field
        return summary


@dataclass(frozen=True)
class UserMacroParseResult:
    """Parsed prompt plus private and public macro payloads."""

    sanitized_prompt: str
    private_macros: list[dict[str, Any]] = field(default_factory=list)
    public_summaries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_macros(self) -> bool:
        return bool(self.private_macros or self.public_summaries)


def _find_json_string_end(text: str, quote_index: int, *, macro_name: str = "typein") -> int:
    escaped = False
    index = quote_index + 1
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return index
        index += 1
    raise UserMacroParseError(
        f'Malformed {macro_name} macro: missing closing quote after {macro_name} ".'
    )


def _parse_json_string_token(
    text: str,
    quote_index: int,
    end_index: int,
    *,
    macro_name: str = "typein",
) -> str:
    token = text[quote_index : end_index + 1]
    try:
        value = json.loads(token)
    except json.JSONDecodeError as exc:
        raise UserMacroParseError(f"Malformed {macro_name} macro string: {exc.msg}.") from exc
    if not isinstance(value, str):
        raise UserMacroParseError(
            f"Malformed {macro_name} macro: quoted value must parse as a string."
        )
    return value


def _macro_is_sensitive(prompt: str, span: tuple[int, int]) -> bool:
    lower = str(prompt or "").lower()
    start, end = span
    window = lower[max(0, start - 80) : min(len(lower), end + 80)]
    return any(marker in lower or marker in window for marker in _SENSITIVE_MARKERS)


def _preview(value: str, limit: int = 80) -> str:
    compact = " ".join(str(value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _macro_syntax_mask(text: str) -> list[bool]:
    """Return a mask for quoted strings and markdown code spans/fences."""

    value = str(text or "")
    mask = [False] * len(value)
    in_single_quote = False
    in_double_quote = False
    in_inline_code = False
    in_fenced_code = False
    escaped = False
    index = 0
    while index < len(value):
        if value.startswith("```", index) and not in_single_quote and not in_double_quote:
            for offset in range(3):
                if index + offset < len(mask):
                    mask[index + offset] = True
            in_fenced_code = not in_fenced_code
            index += 3
            escaped = False
            continue
        char = value[index]
        if in_fenced_code:
            mask[index] = True
            index += 1
            continue
        if char == "`" and not in_single_quote and not in_double_quote:
            mask[index] = True
            in_inline_code = not in_inline_code
            index += 1
            escaped = False
            continue
        if in_inline_code:
            mask[index] = True
            index += 1
            continue
        if in_single_quote:
            mask[index] = True
            if char == "'" and not escaped:
                in_single_quote = False
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            index += 1
            continue
        if in_double_quote:
            mask[index] = True
            if char == '"' and not escaped:
                in_double_quote = False
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            index += 1
            continue
        if char == "'":
            mask[index] = True
            in_single_quote = True
            escaped = False
        elif char == '"':
            mask[index] = True
            in_double_quote = True
            escaped = False
        index += 1
    return mask


def _unmasked_matches(pattern: re.Pattern[str], text: str, mask: list[bool]) -> list[re.Match[str]]:
    return [match for match in pattern.finditer(text) if not mask[match.start()]]


def _normalize_parameter_field(value: str) -> str:
    text = " ".join(str(value or "").strip().split()).lower()
    text = re.sub(r"^(?:the|a|an)\s+", "", text)
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"[^a-z0-9_.]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_.")
    return text


def _compact_parameter_query(value: str) -> str:
    text = re.sub(r"[^\w_.:\- ]+", " ", str(value or ""))
    text = re.sub(r"\b(?:the|a|an|stored|saved|parameter|store)\b", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip()


def _compact_parameter_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_.:-]+", "", str(value or "").strip().lower())


def explicit_sudo_parameter_key_from_prompt(prompt: str) -> str:
    """Return the explicit sudo password Parameter Store key mentioned by a prompt."""

    text = str(prompt or "")
    if not text.strip():
        return ""
    mask = _macro_syntax_mask(text)
    for pattern in _SUDO_PARAMETER_KEY_PATTERNS:
        for match in _unmasked_matches(pattern, text, mask):
            key = str(match.group("key") or "").strip("`'\".,;:()[]{}<>")
            normalized = _compact_parameter_key(key)
            if normalized and normalized not in _SUDO_PARAMETER_KEY_STOPWORDS:
                return key
    return ""


def explicit_sudo_parameter_record_from_prompt(parameter_store: Any, prompt: str) -> Any | None:
    """Return a sensitive Parameter Store record named by an explicit sudo phrase."""

    key = explicit_sudo_parameter_key_from_prompt(prompt)
    if not key:
        return None
    try:
        record = parameter_store.get(key)
    except Exception:
        return None
    if record is not None and bool(getattr(record, "sensitive", False)):
        return record
    return None


def _credential_input_kind_from_text(value: Any) -> str:
    text = " ".join(str(value or "").lower().split())
    if not text:
        return "unknown"
    for kind, pattern in _CREDENTIAL_INPUT_KIND_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return kind
    return "unknown"


def _prompt_suggests_exact_parameter_credential_use(prompt: str, record: Any) -> bool:
    if not bool(getattr(record, "sensitive", False)):
        return False
    text = " ".join(str(prompt or "").lower().split())
    if _EXPLANATORY_CREDENTIAL_QUERY_RE.search(text):
        return False
    return bool(
        _EXACT_PARAMETER_CREDENTIAL_CONTEXT_RE.search(text)
        and _EXACT_PARAMETER_CREDENTIAL_DELIVERY_RE.search(text)
    )


def prompt_requests_parameter_typein_terminal(
    prompt: str,
    parameter_store: Any | None,
) -> bool:
    """Return whether a prompt will likely create private terminal input from Parameter Store."""

    if parameter_store is None:
        return False
    if explicit_sudo_parameter_record_from_prompt(parameter_store, prompt) is not None:
        return True
    try:
        matches = parameter_store.retrieve_matches(
            str(prompt or ""),
            limit=6,
            record_use=False,
        )
    except Exception:
        return False
    exact_matches = [
        match
        for match in matches or []
        if bool(getattr(match, "exact", False))
        and getattr(match, "record", None) is not None
    ]
    if len(exact_matches) != 1:
        return False
    record = exact_matches[0].record
    if _EXPLANATORY_CREDENTIAL_QUERY_RE.search(str(prompt or "")):
        return False
    return _prompt_suggests_exact_parameter_credential_use(prompt, record)


def _parameter_query_field(value: str) -> tuple[str, str]:
    query = str(value or "").strip()
    for field, pattern in _PARAMETER_FIELD_QUERY_PATTERNS:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match is None:
            continue
        without_field = f"{query[:match.start()]} {query[match.end():]}"
        return field, _compact_parameter_query(without_field)
    return "", _compact_parameter_query(query)


def _scalar_parameter_leaves(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        leaves: list[tuple[str, str]] = []
        for key, child in sorted(value.items()):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            leaves.extend(_scalar_parameter_leaves(child, child_prefix))
        return leaves
    if isinstance(value, list) or value is None:
        return []
    return [(prefix, str(value))] if prefix else []


def _parameter_field_aliases(field: str) -> set[str]:
    normalized = _normalize_parameter_field(field)
    aliases = {normalized}
    alias_groups = {
        "password": {"password", "passwd", "pass"},
        "passphrase": {"passphrase", "pass_phrase"},
        "token": {"token", "access_token", "auth_token", "bearer_token"},
        "secret": {"secret", "client_secret"},
        "api_key": {"api_key", "apikey", "api"},
        "private_key": {"private_key", "privatekey", "private_key_path", "key_path", "ssh_key", "ssh"},
        "value": {"value"},
    }
    for canonical, values in alias_groups.items():
        if (
            normalized == canonical
            or normalized in values
            or normalized.endswith(f"_{canonical}")
        ):
            aliases.update(values)
            aliases.add(canonical)
    return {item for item in aliases if item}


def _select_parameter_typein_value(value_json: Any, requested_field: str = "") -> tuple[str, str]:
    leaves = _scalar_parameter_leaves(value_json)
    if not leaves:
        raise UserMacroParseError("Parameter value has no scalar JSON field that can be typed.")

    field = _normalize_parameter_field(requested_field)
    if field:
        aliases = _parameter_field_aliases(field)
        for path, value in leaves:
            normalized_path = _normalize_parameter_field(path)
            normalized_leaf = _normalize_parameter_field(path.rsplit(".", 1)[-1])
            if normalized_path in aliases or normalized_leaf in aliases:
                return value, path
        available = ", ".join(path for path, _value in leaves[:8])
        raise UserMacroParseError(
            f'Parameter field "{requested_field}" was not found. Available fields: {available}.'
        )

    if len(leaves) == 1:
        return leaves[0][1], leaves[0][0]

    preferred = (
        "password",
        "passphrase",
        "token",
        "secret",
        "private_key",
        "api_key",
        "value",
    )
    for name in preferred:
        aliases = _parameter_field_aliases(name)
        for path, value in leaves:
            normalized_leaf = _normalize_parameter_field(path.rsplit(".", 1)[-1])
            if normalized_leaf in aliases:
                return value, path
    available = ", ".join(path for path, _value in leaves[:8])
    raise UserMacroParseError(
        "Parameter contains multiple scalar fields; specify which field to type. "
        f"Available fields: {available}."
    )


def select_parameter_typein_value(value_json: Any, requested_field: str = "") -> tuple[str, str]:
    """Return the scalar parameter value/path suitable for terminal input."""

    return _select_parameter_typein_value(value_json, requested_field=requested_field)


def typein_macro_payloads(
    value: Any,
    *,
    macro_id: str = "",
    input_name: str = "",
    source: str = "",
    parameter_key: str = "",
    parameter_field: str = "",
    redacted: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return private/public typein macro payloads without exposing raw values."""

    clean_macro_id = str(macro_id or "macro_typein_1").strip() or "macro_typein_1"
    clean_input_name = str(input_name or f"{TYPEIN_INPUT_PREFIX}1").strip() or f"{TYPEIN_INPUT_PREFIX}1"
    macro = UserMacro(
        macro_id=clean_macro_id,
        kind=TYPEIN_MACRO_KIND,
        input_name=clean_input_name,
        value=str(value or ""),
        redacted=bool(redacted),
        preview="" if redacted else _preview(str(value or "")),
        source=str(source or ""),
        parameter_key=str(parameter_key or ""),
        parameter_field=str(parameter_field or ""),
    )
    return macro.private_payload(), macro.public_summary()


def typein_macro_payloads_from_parameter_record(
    record: Any,
    *,
    field: str = "",
    parameter_store: Any | None = None,
    record_use: bool = True,
    macro_id: str = "",
    input_name: str = "",
    actor: str = "parameter_store",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return typein macro payloads for one Parameter Store record."""

    value, parameter_field = _select_parameter_typein_value(
        getattr(record, "value_json", {}),
        requested_field=field,
    )
    if record_use and parameter_store is not None:
        try:
            parameter_store.record_use([record], actor=actor)
        except Exception:
            pass
    return typein_macro_payloads(
        value,
        macro_id=macro_id,
        input_name=input_name,
        source="parameter_store",
        parameter_key=str(getattr(record, "key", "") or ""),
        parameter_field=parameter_field,
        redacted=True,
    )


def _typein_parameter_reference(
    text: str,
    match: re.Match[str],
) -> tuple[str, str, int] | None:
    tail = text[match.end() :]
    for pattern in _TYPEIN_PARAMETER_REF_PATTERNS:
        ref_match = pattern.match(tail)
        if ref_match is None:
            continue
        key = str(ref_match.group("key") or "").strip(_PARAMETER_REF_STRIP_CHARS)
        field = str(ref_match.groupdict().get("field") or "").strip(_PARAMETER_REF_STRIP_CHARS)
        if not key:
            continue
        if _normalize_parameter_field(key) in {"store", "parameter_store"}:
            continue
        return key, field, match.end() + ref_match.end()
    return None


def _typein_store_query_reference(text: str, match: re.Match[str]) -> tuple[str, int] | None:
    tail = text[match.end() :]
    query_match = _TYPEIN_STORE_QUERY_RE.match(tail)
    if query_match is None:
        return None
    query = str(query_match.group("query") or "").strip(_PARAMETER_REF_STRIP_CHARS)
    if not query:
        return None
    return query, match.end() + query_match.end()


def _record_identity(record: Any) -> str:
    return str(getattr(record, "normalized_key", "") or getattr(record, "key", "") or "").strip()


def _parameter_typein_candidate_records(
    parameter_store: Any,
    *,
    query: str,
    lookup_query: str,
) -> list[Any]:
    records: list[Any] = []
    seen: set[str] = set()

    def add_record(record: Any) -> None:
        identity = _record_identity(record)
        if not identity or identity in seen:
            return
        seen.add(identity)
        records.append(record)

    for search_text in [query, lookup_query]:
        clean = _compact_parameter_query(search_text)
        if not clean:
            continue
        try:
            matches = parameter_store.retrieve_matches(clean, limit=8, record_use=False)
        except TypeError:
            matches = []
        except Exception:
            matches = []
        for item in matches or []:
            record = getattr(item, "record", None)
            if record is not None:
                add_record(record)
        if records:
            continue
        try:
            listed = parameter_store.list(query=clean, limit=8)
        except Exception:
            listed = []
        for record in listed or []:
            add_record(record)
    return records


def _macro_from_parameter_record(
    record: Any,
    *,
    field: str,
    parameter_store: Any,
    record_use: bool = True,
) -> UserMacro:
    value, parameter_field = _select_parameter_typein_value(
        getattr(record, "value_json", {}),
        requested_field=field,
    )
    if record_use:
        try:
            parameter_store.record_use([record], actor="typein_macro")
        except Exception:
            pass
    return UserMacro(
        macro_id="macro_typein_1",
        kind=TYPEIN_MACRO_KIND,
        input_name=f"{TYPEIN_INPUT_PREFIX}1",
        value=value,
        redacted=True,
        preview="",
        source="parameter_store",
        parameter_key=str(getattr(record, "key", "") or ""),
        parameter_field=parameter_field,
    )


def _replace_parameter_query_typein(
    text: str,
    *,
    start_index: int,
    end_index: int,
    query: str,
    parameter_store: Any | None,
    replacement_prefix: str,
    require_field: bool,
) -> tuple[str, UserMacro] | None:
    field, lookup_query = _parameter_query_field(query)
    if require_field and not field:
        return None
    if parameter_store is None:
        raise UserMacroParseError("Parameter store is unavailable for parameter-backed typein.")

    records = _parameter_typein_candidate_records(
        parameter_store,
        query=query,
        lookup_query=lookup_query,
    )
    compatible: list[tuple[Any, UserMacro]] = []
    last_error: UserMacroParseError | None = None
    for record in records:
        try:
            compatible.append(
                (
                    record,
                    _macro_from_parameter_record(
                        record,
                        field=field,
                        parameter_store=parameter_store,
                        record_use=False,
                    ),
                )
            )
        except UserMacroParseError as exc:
            last_error = exc
    if not compatible:
        if last_error is not None:
            raise last_error
        raise UserMacroParseError(f'No matching parameter was found for "{query}".')
    if len(compatible) > 1:
        keys = ", ".join(str(getattr(record, "key", "") or "") for record, _macro in compatible[:5])
        raise UserMacroParseError(
            "Parameter reference matched multiple store entries; specify the key. "
            f"Matched: {keys}."
        )

    record, macro = compatible[0]
    try:
        parameter_store.record_use([record], actor="typein_macro")
    except Exception:
        pass
    sanitized = text[:start_index] + f'{replacement_prefix} "[redacted]"' + text[end_index:]
    return sanitized, macro


def _replace_parameter_typein(
    text: str,
    *,
    word_matches: list[re.Match[str]],
    parameter_store: Any | None,
) -> tuple[str, UserMacro] | None:
    references: list[tuple[re.Match[str], str, str, int]] = []
    query_references: list[tuple[re.Match[str], str, int]] = []
    for match in word_matches:
        reference = _typein_parameter_reference(text, match)
        if reference is not None:
            key, field, end_index = reference
            references.append((match, key, field, end_index))
            continue
        query_reference = _typein_store_query_reference(text, match)
        if query_reference is not None:
            query, end_index = query_reference
            query_references.append((match, query, end_index))
    if references and query_references:
        raise UserMacroParseError("Only one typein macro is supported in this version.")
    if not references:
        if query_references:
            if len(query_references) > 1:
                raise UserMacroParseError("Only one typein macro is supported in this version.")
            match, query, end_index = query_references[0]
            return _replace_parameter_query_typein(
                text,
                start_index=match.start(),
                end_index=end_index,
                query=query,
                parameter_store=parameter_store,
                replacement_prefix=text[match.start() : match.end()],
                require_field=False,
            )
        return None
    if len(references) > 1:
        raise UserMacroParseError("Only one typein macro is supported in this version.")
    if parameter_store is None:
        raise UserMacroParseError("Parameter store is unavailable for parameter-backed typein.")

    match, key, field, end_index = references[0]
    record = None
    try:
        record = parameter_store.get(key)
    except Exception as exc:  # pragma: no cover - defensive around pluggable stores
        raise UserMacroParseError(f'Unable to read parameter "{key}" from the parameter store.') from exc
    if record is None:
        raise UserMacroParseError(f'Parameter "{key}" was not found in the parameter store.')

    value, parameter_field = _select_parameter_typein_value(
        getattr(record, "value_json", {}),
        requested_field=field,
    )
    try:
        parameter_store.record_use([record], actor="typein_macro")
    except Exception:
        pass

    macro = UserMacro(
        macro_id="macro_typein_1",
        kind=TYPEIN_MACRO_KIND,
        input_name=f"{TYPEIN_INPUT_PREFIX}1",
        value=value,
        redacted=True,
        preview="",
        source="parameter_store",
        parameter_key=str(getattr(record, "key", key) or key),
        parameter_field=parameter_field,
    )
    macro_prefix = text[match.start() : match.end()]
    sanitized = text[: match.start()] + f'{macro_prefix} "[redacted]"' + text[end_index:]
    return sanitized, macro


def _replace_parameter_store_typein_without_macro(
    text: str,
    *,
    mask: list[bool],
    parameter_store: Any | None,
) -> tuple[str, UserMacro] | None:
    matches = []
    for match in _unmasked_matches(_PARAMETER_STORE_TYPEIN_RE, text, mask):
        query = str(match.group("query") or "").strip(_PARAMETER_REF_STRIP_CHARS)
        field, _lookup_query = _parameter_query_field(query)
        if not field:
            continue
        matches.append((match, query))
    if not matches:
        return None
    if len(matches) > 1:
        raise UserMacroParseError("Only one typein macro is supported in this version.")
    match, query = matches[0]
    return _replace_parameter_query_typein(
        text,
        start_index=match.start(),
        end_index=match.end(),
        query=query,
        parameter_store=parameter_store,
        replacement_prefix=TYPEIN_MACRO_KIND,
        require_field=True,
    )


def _sudo_parameter_typein_macro(
    parameter_store: Any | None,
    text: str,
) -> UserMacro | None:
    if parameter_store is None:
        return None
    record = explicit_sudo_parameter_record_from_prompt(parameter_store, text)
    if record is None:
        return None
    try:
        macro = _macro_from_parameter_record(
            record,
            field="",
            parameter_store=parameter_store,
            record_use=True,
        )
    except UserMacroParseError:
        return None
    return macro


def _compact_control_sanitized_text(text: str) -> str:
    sanitized = re.sub(r"[ \t]{2,}", " ", str(text or ""))
    sanitized = re.sub(r"^\s*[,.;:!?]\s*", "", sanitized)
    sanitized = re.sub(r"\s*[,.;:!?]\s*$", "", sanitized)
    sanitized = re.sub(r"\s+([,.;:!?])", r"\1", sanitized)
    return sanitized.strip()


def _checkonline_summary() -> dict[str, Any]:
    return {
        "macro_id": "macro_checkonline_1",
        "kind": CHECKONLINE_MACRO_KIND,
        "input_name": "",
        "redacted": False,
        "value_length": 0,
        "delivery_scope": "online_lookup_context",
        "consumed": False,
    }


def _checkonlineai_summary(query: str = "") -> dict[str, Any]:
    clean_query = str(query or "").strip()
    summary = {
        "macro_id": "macro_checkonlineai_1",
        "kind": CHECKONLINEAI_MACRO_KIND,
        "input_name": "",
        "redacted": False,
        "value_length": len(clean_query),
        "delivery_scope": "online_ai_lookup_context",
        "consumed": False,
    }
    if clean_query:
        summary["query"] = clean_query[:1000]
        summary["preview"] = _preview(clean_query)
    return summary


def _autoapprove_summary() -> dict[str, Any]:
    return {
        "macro_id": "macro_autoapprove_1",
        "kind": AUTOAPPROVE_MACRO_KIND,
        "input_name": "",
        "redacted": False,
        "value_length": 0,
        "delivery_scope": "request_confirmation_policy",
        "consumed": False,
    }


def _event_extraction_summary(kind: str) -> dict[str, Any]:
    action_type = "agent_prompt" if kind == RUNLATER_MACRO_KIND else "notification"
    return {
        "macro_id": f"macro_{kind}_1",
        "kind": kind,
        "input_name": "",
        "redacted": False,
        "value_length": 0,
        "delivery_scope": "event_extraction_hint",
        "consumed": False,
        "event_action_type": action_type,
    }


def _restart_summary() -> dict[str, Any]:
    return {
        "macro_id": "macro_restart_1",
        "kind": RESTART_MACRO_KIND,
        "input_name": "",
        "redacted": False,
        "value_length": 0,
        "delivery_scope": "agent_ui_restart",
        "consumed": False,
        "restart_targets": ["server", "gateway"],
    }


def _slash_macro_candidate_summary(token: str, index: int) -> dict[str, Any]:
    clean_token = str(token or "").strip()
    return {
        "macro_id": f"macro_slash_candidate_{index}",
        "kind": SLASH_MACRO_CANDIDATE_KIND,
        "input_name": "",
        "redacted": False,
        "value_length": len(clean_token),
        "delivery_scope": "slash_macro_candidate",
        "consumed": False,
        "token": clean_token,
        "preview": clean_token,
    }


def _slash_macro_candidate_summaries(text: str) -> list[dict[str, Any]]:
    mask = _macro_syntax_mask(text)
    summaries: list[dict[str, Any]] = []
    for match in _unmasked_matches(_SLASH_MACRO_TOKEN_RE, text, mask):
        token = str(match.group(1) or "").strip()
        if token.lower() in _KNOWN_SLASH_MACRO_NAMES:
            continue
        summaries.append(_slash_macro_candidate_summary(f"/{token}", len(summaries) + 1))
    return summaries


def _replace_autoapprove(text: str) -> tuple[str, list[dict[str, Any]]]:
    mask = _macro_syntax_mask(text)
    matches = _unmasked_matches(_AUTOAPPROVE_RE, text, mask)
    if not matches:
        return text, []
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(text[cursor : match.start()])
        cursor = match.end()
    parts.append(text[cursor:])
    sanitized = "".join(parts)
    sanitized = _compact_control_sanitized_text(sanitized)
    return sanitized, [_autoapprove_summary()]


def _replace_event_extraction_macro(
    text: str,
    *,
    pattern: re.Pattern[str],
    kind: str,
) -> tuple[str, list[dict[str, Any]]]:
    mask = _macro_syntax_mask(text)
    matches = _unmasked_matches(pattern, text, mask)
    if not matches:
        return text, []
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(text[cursor : match.start()])
        cursor = match.end()
    parts.append(text[cursor:])
    sanitized = "".join(parts)
    sanitized = _compact_control_sanitized_text(sanitized)
    return sanitized, [_event_extraction_summary(kind)]


def _replace_restart(text: str) -> tuple[str, list[dict[str, Any]]]:
    mask = _macro_syntax_mask(text)
    matches = _unmasked_matches(_RESTART_RE, text, mask)
    if not matches:
        return text, []
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(text[cursor : match.start()])
        cursor = match.end()
    parts.append(text[cursor:])
    remainder = _compact_control_sanitized_text("".join(parts))
    restart_prompt = "restart the Agent UI server and selected gateway"
    sanitized = f"{restart_prompt}. {remainder}" if remainder else restart_prompt
    return sanitized, [_restart_summary()]


def _replace_checkonlineai(text: str, *, preserve_hint: bool = False) -> tuple[str, list[dict[str, Any]]]:
    mask = _macro_syntax_mask(text)
    matches = _unmasked_matches(_CHECKONLINEAI_RE, text, mask)
    if not matches:
        return text, []
    parts: list[str] = []
    summaries: list[dict[str, Any]] = []
    cursor = 0
    for match in matches:
        parts.append(text[cursor : match.start()])
        quote_index = match.end()
        while quote_index < len(text) and text[quote_index].isspace():
            quote_index += 1
        query = ""
        consume_end = match.end()
        if quote_index < len(text) and text[quote_index] == '"':
            end_index = _find_json_string_end(
                text,
                quote_index,
                macro_name=CHECKONLINEAI_MACRO_KIND,
            )
            query = _parse_json_string_token(
                text,
                quote_index,
                end_index,
                macro_name=CHECKONLINEAI_MACRO_KIND,
            )
            consume_end = end_index + 1
        replacement = (
            f"using online AI context for {query}"
            if preserve_hint and query
            else ("using online AI context" if preserve_hint else "")
        )
        parts.append(replacement)
        summaries.append(_checkonlineai_summary(query))
        cursor = consume_end
    parts.append(text[cursor:])
    sanitized = "".join(parts)
    sanitized = _compact_control_sanitized_text(sanitized)
    return sanitized, summaries


def _replace_checkonline_phrase_as_checkonlineai(
    text: str,
    *,
    original_prompt: str,
) -> tuple[str, list[dict[str, Any]]]:
    mask = _macro_syntax_mask(text)
    matches = _unmasked_matches(_CHECKONLINE_PHRASE_RE, text, mask)
    if not matches:
        return text, []
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(text[cursor : match.start()])
        cursor = match.end()
    parts.append(text[cursor:])
    sanitized = "".join(parts)
    sanitized = _compact_control_sanitized_text(sanitized)
    return sanitized, [_checkonlineai_summary(original_prompt)]


def _replace_checkonline(text: str, *, preserve_hint: bool = False) -> tuple[str, list[dict[str, Any]]]:
    mask = _macro_syntax_mask(text)
    matches = _unmasked_matches(_CHECKONLINE_RE, text, mask)
    if not matches:
        return text, []
    replacement = "check online" if preserve_hint else ""
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(text[cursor : match.start()])
        parts.append(replacement)
        cursor = match.end()
    parts.append(text[cursor:])
    sanitized = "".join(parts)
    sanitized = _compact_control_sanitized_text(sanitized)
    return sanitized, [_checkonline_summary()]


def parse_user_macros(
    prompt: str,
    *,
    preserve_checkonline_hint: bool = False,
    parameter_store: Any | None = None,
) -> UserMacroParseResult:
    """Parse deterministic user macros from one prompt.

    V1 supports one case-insensitive ``typein "..."`` macro and request-level
    slash markers such as ``/checkonline``, ``/autoapprove``, and event
    extraction hints.
    The returned sanitized prompt never contains the raw macro value.
    """

    original_text = normalize_spoken_quoted_segments(str(prompt or ""))
    text, autoapprove_summaries = _replace_autoapprove(original_text)
    text, runlater_summaries = _replace_event_extraction_macro(
        text,
        pattern=_RUNLATER_RE,
        kind=RUNLATER_MACRO_KIND,
    )
    text, remind_summaries = _replace_event_extraction_macro(
        text,
        pattern=_REMIND_RE,
        kind=REMIND_MACRO_KIND,
    )
    text, todo_summaries = _replace_event_extraction_macro(
        text,
        pattern=_TODO_RE,
        kind=TODO_MACRO_KIND,
    )
    text, restart_summaries = _replace_restart(text)
    text, ai_summaries = _replace_checkonlineai(
        text,
        preserve_hint=preserve_checkonline_hint,
    )
    text, public_summaries = _replace_checkonline(
        text,
        preserve_hint=preserve_checkonline_hint,
    )
    if not ai_summaries and not public_summaries:
        text, phrase_summaries = _replace_checkonline_phrase_as_checkonlineai(
            text,
            original_prompt=original_text,
        )
        ai_summaries = phrase_summaries
    slash_macro_summaries = _slash_macro_candidate_summaries(text)
    public_summaries = [
        *autoapprove_summaries,
        *runlater_summaries,
        *remind_summaries,
        *todo_summaries,
        *restart_summaries,
        *ai_summaries,
        *public_summaries,
        *slash_macro_summaries,
    ]
    mask = _macro_syntax_mask(text)
    typein_word_matches = _unmasked_matches(_TYPEIN_WORD_RE, text, mask)
    if not typein_word_matches:
        sudo_parameter_typein = _sudo_parameter_typein_macro(parameter_store, text)
        if sudo_parameter_typein is not None:
            return UserMacroParseResult(
                sanitized_prompt=text,
                private_macros=[sudo_parameter_typein.private_payload()],
                public_summaries=[*public_summaries, sudo_parameter_typein.public_summary()],
            )
        parameter_store_typein = _replace_parameter_store_typein_without_macro(
            text,
            mask=mask,
            parameter_store=parameter_store,
        )
        if parameter_store_typein is not None:
            sanitized, macro = parameter_store_typein
            return UserMacroParseResult(
                sanitized_prompt=sanitized,
                private_macros=[macro.private_payload()],
                public_summaries=[*public_summaries, macro.public_summary()],
            )
        return UserMacroParseResult(sanitized_prompt=text, public_summaries=public_summaries)

    raw_matches = _unmasked_matches(_TYPEIN_QUOTE_RE, text, mask)
    if not raw_matches:
        parameter_typein = _replace_parameter_typein(
            text,
            word_matches=typein_word_matches,
            parameter_store=parameter_store,
        )
        if parameter_typein is not None:
            sanitized, macro = parameter_typein
            return UserMacroParseResult(
                sanitized_prompt=sanitized,
                private_macros=[macro.private_payload()],
                public_summaries=[*public_summaries, macro.public_summary()],
            )
        raise UserMacroParseError('Malformed typein macro: expected exact syntax typein "value".')
    matches: list[tuple[re.Match[str], int, int, str]] = []
    for raw_match in raw_matches:
        quote_index = raw_match.end() - 1
        end_index = _find_json_string_end(text, quote_index, macro_name=TYPEIN_MACRO_KIND)
        value = _parse_json_string_token(
            text,
            quote_index,
            end_index,
            macro_name=TYPEIN_MACRO_KIND,
        )
        if value in _SANITIZED_TYPEIN_PLACEHOLDERS:
            continue
        matches.append((raw_match, quote_index, end_index, value))
    if not matches:
        return UserMacroParseResult(sanitized_prompt=text, public_summaries=public_summaries)
    if len(matches) > 1:
        raise UserMacroParseError("Only one typein macro is supported in this version.")

    match, quote_index, end_index, value = matches[0]
    redacted = _macro_is_sensitive(text, (match.start(), end_index + 1))
    macro = UserMacro(
        macro_id="macro_typein_1",
        kind=TYPEIN_MACRO_KIND,
        input_name=f"{TYPEIN_INPUT_PREFIX}1",
        value=value,
        redacted=redacted,
        preview="" if redacted else _preview(value),
    )
    replacement = f'{text[match.start():quote_index]}"{"[redacted]" if redacted else "[provided]"}"'
    sanitized = text[: match.start()] + replacement + text[end_index + 1 :]
    return UserMacroParseResult(
        sanitized_prompt=sanitized,
        private_macros=[macro.private_payload()],
        public_summaries=[*public_summaries, macro.public_summary()],
    )


def user_macro_summaries_from_context(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = dict(context or {}).get(USER_MACRO_SUMMARY_CONTEXT_KEY)
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def private_user_macros_from_context(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = dict(context or {}).get(USER_MACRO_PRIVATE_CONTEXT_KEY)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def is_user_macro_input_name(input_name: str, context: dict[str, Any] | None) -> bool:
    name = str(input_name or "").strip()
    if not name:
        return False
    return any(
        str(macro.get("kind") or "") == TYPEIN_MACRO_KIND
        and str(macro.get("input_name") or "").strip() == name
        for macro in private_user_macros_from_context(context)
    )


def consume_typein_macro(
    context: dict[str, Any] | None,
    *,
    input_name: str | None = None,
    delivery: str,
    action_id: str,
    reusable: bool = False,
) -> dict[str, Any] | None:
    """Return and mark one typein macro as delivered in-place."""

    if not isinstance(context, dict):
        return None
    macros = private_user_macros_from_context(context)
    wanted_input_name = str(input_name or "").strip()
    for macro in macros:
        if str(macro.get("kind") or "") != TYPEIN_MACRO_KIND:
            continue
        if wanted_input_name and str(macro.get("input_name") or "").strip() != wanted_input_name:
            continue
        if bool(macro.get("consumed")) and not reusable:
            continue
        macro["consumed"] = True
        if reusable:
            try:
                macro["delivery_count"] = int(macro.get("delivery_count") or 0) + 1
            except (TypeError, ValueError):
                macro["delivery_count"] = 1
        deliveries = list(macro.get("deliveries") or [])
        deliveries.append({"delivery": delivery, "action_id": str(action_id or "")})
        macro["deliveries"] = deliveries
        context[USER_MACRO_PRIVATE_CONTEXT_KEY] = macros
        return macro
    return None


def user_macro_public_delivery(macro: dict[str, Any], *, delivery: str, action_id: str) -> dict[str, Any]:
    return {
        "macro_id": str(macro.get("macro_id") or ""),
        "kind": str(macro.get("kind") or ""),
        "input_name": str(macro.get("input_name") or ""),
        "delivery": delivery,
        "action_id": str(action_id or ""),
        "redacted": bool(macro.get("redacted")),
        "value_length": len(str(macro.get("value") or "")),
        **(
            {"preview": str(macro.get("preview") or "")}
            if not bool(macro.get("redacted")) and str(macro.get("preview") or "")
            else {}
        ),
    }


def macro_value_with_enter(value: Any) -> str:
    text = str(value or "")
    return text if text.endswith("\n") else text + "\n"
