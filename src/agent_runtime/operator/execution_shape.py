"""Generic execution-shape hints for operator planning."""

from __future__ import annotations

import json
import re
from typing import Any


_SINGLE_ACTION_WORDING_RE = re.compile(
    r"\b(?:single|one)\s+(?:query|command|step|action|shot|pass)\b"
    r"|\brather than multiple\s+(?:steps|actions|commands)\b"
    r"|\bnot\s+(?:as\s+)?multiple\s+(?:steps|actions|commands)\b"
    r"|\bin\s+one\s+(?:go|command|query|step|action)\b",
    re.IGNORECASE,
)

_ALL_ENTITY_RE = re.compile(r"\b(?:for each|each|per|all|every)\b", re.IGNORECASE)
_REPORT_RE = re.compile(
    r"\b(?:list|show|get|report|table|summari[sz]e|summary|count|total|"
    r"latest|oldest|earliest|newest|first|last|largest|smallest|date|size|status)\b",
    re.IGNORECASE,
)
_FIELD_KEYWORDS: tuple[tuple[str, str, str], ...] = (
    ("oldest", "oldest", "text"),
    ("earliest", "oldest", "text"),
    ("latest", "latest", "text"),
    ("newest", "latest", "text"),
    ("first", "first", "text"),
    ("last", "last", "text"),
    ("date", "date", "date"),
    ("timestamp", "timestamp", "date"),
    ("time", "time", "date"),
    ("count", "count", "number"),
    ("total", "total", "number"),
    ("number", "number", "number"),
    ("size", "size", "size"),
    ("largest", "largest", "size"),
    ("smallest", "smallest", "size"),
    ("status", "status", "text"),
    ("summary", "summary", "text"),
)
_MUTATION_RE = re.compile(
    r"\b(?:edit|modify|update|write|create|delete|remove|rename|move|install|"
    r"start|stop|restart|deploy|commit|push|merge|apply|fix|implement|save|send|publish)\b",
    re.IGNORECASE,
)
_NATURAL_LANGUAGE_MUTATION_RE = re.compile(
    r"(?:^|[.;:]|\b(?:then|and|also|after(?:wards)?|next|please|to)\s+)"
    r"\s*(?:edit|modify|update|write|create|delete|remove|rename|move|install|"
    r"start|stop|restart|deploy|commit|push|merge|apply|fix|implement|save|send|publish)\b",
    re.IGNORECASE,
)
_SEQUENCE_RE = re.compile(r"\b(?:then|and then|after(?:wards)?|before|next)\b", re.IGNORECASE)
_ENTITY_PHRASE_RE = re.compile(
    r"\b(?:for each|per|each|all|every)\s+([a-z][a-z0-9_.-]*(?:\s+[a-z][a-z0-9_.-]*){0,3})",
    re.IGNORECASE,
)
_CURRENT_ONLY_RE = re.compile(
    r"\b(?:only\s+current|current\s+(?:entity|item|target|selection|branch|container|image|file|directory|row|record)|selected|active|default|only first|first only)\b",
    re.IGNORECASE,
)
_DATE_LIKE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}"
    r"(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z| ?[+-]\d{2}:?\d{2})?)?\b"
)
_NUMBER_LIKE_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:%|\b)")
_SIZE_LIKE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:b|kb|mb|gb|tb|kib|mib|gib|tib|bytes?)\b", re.IGNORECASE)
_ZERO_SIZE_VALUE_RE = r"0+(?:\.0+)?\s*(?:b|bytes?|kb|kib|mb|mib|gb|gib|tb|tib)"
_ZERO_SIZE_AGGREGATE_RE = re.compile(
    rf"""
    (?:^|\n)
    [^\n]{{0,120}}
    \b(?:total|sum|aggregate|combined|overall)\b
    [^\n]{{0,120}}
    \b{_ZERO_SIZE_VALUE_RE}\b
    |
    (?:^|\n)
    [^\n]{{0,120}}
    \b(?:size|space|usage|bytes?)\b
    [^\n]{{0,120}}
    \b{_ZERO_SIZE_VALUE_RE}\b
    |
    ^\s*{_ZERO_SIZE_VALUE_RE}\s*$
    """,
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)
_EMPTY_SET_EVIDENCE_RE = re.compile(
    r"\b(?:no|zero)\s+(?:\S+\s+){0,5}(?:found|available|present|returned|matched)\b"
    r"|\bnone\s+(?:found|available|present|returned|matched)\b"
    r"|\bempty\s+(?:input|result|set|dataset|collection)\b"
    r"|\b(?:source|entity|value|result|row|record|item)\s+(?:set|collection|list|values?)?\s*(?:is|are|was|were)\s+empty\b",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(
    r"\b(?:Unknown|N/A|null|none|not available|unavailable|not found|no [A-Za-z0-9_. -]+ found)\b",
    re.IGNORECASE,
)
_PLACEHOLDER_LITERAL_RE = re.compile(
    r"['\"](?:Unknown|N/A|null|not available|unavailable)['\"]",
    re.IGNORECASE,
)
_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\):|^\s*File \"[^\"]+\", line \d+",
    re.IGNORECASE | re.MULTILINE,
)
_SILENT_SKIP_ON_FAILURE_RE = re.compile(
    r"(?:returncode\s*!=\s*0[^\n]*:|except\b[^\n]*:)"
    r"(?:(?:\n\s*(?:#.*)?))*\n\s*continue\b",
    re.IGNORECASE,
)
_COMPLETE_EXTREMA_FILTER_RE = re.compile(
    r"--(?:since|until|grep)\b"
    r"|\b(?:since|until)\s*="
    r"|\bdatetime\.now\s*\("
    r"|\b(?:today|current\s+date)\b"
    r"|\bgrep\s*[=(]",
    re.IGNORECASE,
)
_USER_TEMPORAL_OR_FILTER_INTENT_RE = re.compile(
    r"\b(?:since|until|after|before|during|between|from|to|today|yesterday|"
    r"this\s+(?:week|month|year)|last\s+(?:week|month|year)|matching|matches|"
    r"search|grep|filter|where|only)\b",
    re.IGNORECASE,
)


def explicit_single_action_requested(prompt: str) -> bool:
    return bool(_SINGLE_ACTION_WORDING_RE.search(str(prompt or "")))


def _classification_requires_tools(classification_context: dict[str, Any] | None) -> bool:
    if not isinstance(classification_context, dict):
        return True
    return bool(classification_context.get("requires_tools", True))


def _has_requested_mutation(text: str) -> bool:
    """Return whether natural language asks to perform a side effect.

    Broad mutation keywords can also be entity nouns in reports, such as a
    record date or deployment status.  For execution-shape routing, only
    command-like verb positions should force staged workflow.
    """

    return bool(_NATURAL_LANGUAGE_MUTATION_RE.search(str(text or "")))


def _field_specs(prompt: str) -> list[dict[str, str]]:
    lowered = prompt.lower()
    fields: list[dict[str, str]] = []
    seen: set[str] = set()
    for keyword, label, field_type in _FIELD_KEYWORDS:
        if keyword not in lowered or label in seen:
            continue
        seen.add(label)
        fields.append({"name": label, "type": field_type})
    names = {field["name"] for field in fields}
    if "date" in names:
        for field in fields:
            if field["name"] in {"oldest", "earliest", "latest", "newest", "first", "last"}:
                field["name"] = f"{field['name']}_date"
                field["type"] = "date"
        fields = [field for field in fields if field["name"] != "date"]
    deduped: list[dict[str, str]] = []
    seen.clear()
    for field in fields:
        name = field["name"]
        if name in seen:
            continue
        seen.add(name)
        deduped.append(field)
    return deduped or [{"name": "final_answer", "type": "text"}]


def _extrema_contract(fields: list[dict[str, str]]) -> dict[str, Any]:
    """Return generic extrema metadata for report contracts.

    This is deliberately domain-agnostic: it describes relationships between
    requested fields, not how a particular tool should compute them.
    """

    names = {str(field.get("name") or "") for field in fields}
    opposing_pairs: list[list[str]] = []
    for older, newer in (
        ("oldest_date", "latest_date"),
        ("earliest_date", "latest_date"),
        ("first_date", "last_date"),
        ("oldest", "latest"),
        ("earliest", "latest"),
        ("first", "last"),
        ("smallest", "largest"),
    ):
        if older in names and newer in names:
            opposing_pairs.append([older, newer])
    return {
        "opposing_extrema_pairs": opposing_pairs,
        "requires_underlying_record_sets": bool(opposing_pairs),
    }


def _entity_label(prompt: str) -> str:
    match = _ENTITY_PHRASE_RE.search(prompt)
    if not match:
        return "entity"
    raw = str(match.group(1) or "")
    raw = re.split(
        r"\b(?:get|show|list|with|and|where|that|which|from|in|on|of|to|as)\b",
        raw,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    raw = re.sub(r"[^A-Za-z0-9_. -]+", " ", raw).strip(" ._-")
    words = [word for word in raw.split() if word.lower() not in {"the", "a", "an"}]
    label = "_".join(words[:2]).strip(" ._-").lower()
    if label.endswith("ies") and len(label) > 3:
        label = label[:-3] + "y"
    elif label.endswith("s") and not label.endswith("ss") and len(label) > 1:
        label = label[:-1]
    return label or "entity"


def classify_execution_shape(
    prompt: str,
    *,
    classification_context: dict[str, Any] | None = None,
    tasks: list[Any] | None = None,
    global_constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return diagnostic shape metadata without selecting a fast lane."""

    del global_constraints
    text = str(prompt or "")
    explicit_single = explicit_single_action_requested(text)
    mutating = _has_requested_mutation(text)
    sequenced = bool(_SEQUENCE_RE.search(text)) or len(tasks or []) > 1

    if mutating and (sequenced or not explicit_single):
        return {
            "shape": "staged_workflow",
            "preferred_action_count": None,
            "hard_max_action_count": None,
            "decomposition_policy": "force",
            "report_contract": {},
            "reason": "The request contains side-effecting work or staged workflow language, so decomposition remains appropriate.",
        }

    return {
        "shape": "unknown",
        "preferred_action_count": None,
        "hard_max_action_count": None,
        "decomposition_policy": "allow",
        "report_contract": {},
        "reason": (
            "The user requested one action; decomposition may still return one atomic task if that is sufficient."
            if explicit_single
            else "No deterministic execution-shape routing preference was detected."
        ),
    }


def execution_shape_from_request(user_request: Any) -> dict[str, Any]:
    contexts = [
        getattr(user_request, "safety_context", None),
        getattr(user_request, "session_context", None),
    ]
    for index, context in enumerate(contexts):
        if not isinstance(context, dict):
            continue
        hint = context.get("execution_shape")
        if isinstance(hint, dict):
            return dict(hint)
        intent = context.get("operator_intent_block")
        if isinstance(intent, dict) and isinstance(intent.get("execution_shape"), dict):
            return dict(intent["execution_shape"])
    return {}


def execution_shape_bypasses_decomposition(hint: dict[str, Any] | None) -> bool:
    del hint
    return False


def execution_shape_is_single_action_report(user_request: Any) -> bool:
    return execution_shape_bypasses_decomposition(execution_shape_from_request(user_request))


def execution_shape_collapses_decomposition(hint: dict[str, Any] | None, tasks: list[Any]) -> bool:
    del hint, tasks
    return False


def _task_read_onlyish(task: Any) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            getattr(task, "description", ""),
            getattr(task, "semantic_verb", ""),
            getattr(task, "object_type", ""),
            getattr(task, "operation_intent", ""),
            getattr(task, "side_effect_type", ""),
        )
    )
    risk = str(getattr(task, "risk_level", "") or "").lower()
    if risk in {"high", "critical"}:
        return False
    return not _has_requested_mutation(text)


def _action_text(action: Any) -> str:
    return "\n".join(
        str(part or "")
        for part in (
            getattr(action, "command", None),
            getattr(action, "code", None),
            getattr(action, "reason", None),
            getattr(action, "effect_summary", None),
        )
        if part not in (None, "")
    )


def _action_read_onlyish(action: Any) -> bool:
    risk = str(getattr(action, "risk", "") or "").lower()
    effect = str(getattr(action, "effect_intent", "") or "").lower()
    if risk in {"high", "critical"}:
        return False
    if effect in {"mutating", "write", "network_write", "destructive"}:
        return False
    if effect in {"read_only", "read-only", "query", "inspect", "observe"}:
        return True
    return not bool(_MUTATION_RE.search(_action_text(action)))


def execution_shape_plan_errors(user_request: Any, plan: Any) -> list[dict[str, Any]]:
    """Execution-shape hints are diagnostic only; they never reject plan shape."""

    del user_request, plan
    return []


def _record_text(record: Any) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    stdout_text = ""
    for attr in ("stdout", "stderr"):
        value = getattr(record, attr, "")
        if value:
            text = _drop_repeated_json_lines(str(value))
            if attr == "stdout":
                stdout_text = text
            key = text.strip()
            if key and key not in seen:
                seen.add(key)
                parts.append(text)
    output = getattr(record, "output", None)
    if output not in (None, ""):
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=True, default=str)
        text = _drop_repeated_json_lines(text)
        key = str(text).strip()
        if key and key not in seen and not _json_output_repeated_in_stdout(key, stdout_text):
            parts.append(text)
    return "\n".join(parts)


def report_text_from_records(records: list[Any]) -> str:
    chunks = [_record_text(record).strip() for record in records or []]
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\][^\x07]*(?:\x07|\x1b\\)"
    r"|\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b[@-Z\\-_]"
)
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _normalize_terminal_text(value: Any) -> str:
    text = str(value or "")
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARACTER_RE.sub("", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _markdown_code_block(text: str, *, language: str = "text") -> str:
    fence = "````" if "```" in text else "```"
    return f"{fence}{language}\n{text.strip()}\n{fence}"


def _looks_like_markdown_report(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    lines = stripped.splitlines()
    if stripped.startswith("```"):
        return True
    if re.search(r"(?m)^#{1,6}\s+\S", stripped):
        return True
    return any(
        "|" in lines[index]
        and index + 1 < len(lines)
        and re.match(
            r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$",
            lines[index + 1],
        )
        for index in range(len(lines) - 1)
    )


def _parse_single_json_display_value(text: str) -> Any | None:
    stripped = str(text or "").strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except (TypeError, ValueError):
        return None


def _report_text_as_markdown(text: str) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""
    had_terminal_controls = bool(
        "\r" in raw or "\x1b" in raw or _CONTROL_CHARACTER_RE.search(raw)
    )
    normalized = _normalize_terminal_text(raw)
    if not normalized:
        return ""
    parsed = _parse_single_json_display_value(normalized)
    if isinstance(parsed, (dict, list)):
        return _markdown_code_block(
            json.dumps(parsed, ensure_ascii=True, indent=2, sort_keys=True, default=str),
            language="json",
        )
    if _looks_like_markdown_report(normalized):
        return normalized
    if had_terminal_controls:
        return _markdown_code_block(normalized)
    return normalized


def report_markdown_from_records(records: list[Any]) -> str:
    return _report_text_as_markdown(report_text_from_records(records))


def _json_scalar_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_json_scalar_values(item))
        return values
    if isinstance(value, (list, tuple)):
        values = []
        for item in value:
            values.extend(_json_scalar_values(item))
        return values
    if value is None or isinstance(value, bool):
        return []
    text = str(value).strip()
    return [text] if text else []


def _json_output_repeated_in_stdout(output: str, stdout: str) -> bool:
    if not stdout.strip() or not output.strip().startswith(("{", "[")):
        return False
    try:
        payload = json.loads(output)
    except (TypeError, ValueError):
        return False
    values = _json_scalar_values(payload)
    meaningful = [value for value in values if value.lower() not in {"0", "0.0", "false", "true"}]
    if not meaningful:
        return False
    stdout_lower = stdout.lower()
    return all(str(value).lower() in stdout_lower for value in values)


def _drop_repeated_json_lines(text: str) -> str:
    lines: list[str] = []
    prior = ""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("{", "[")) and _json_output_repeated_in_stdout(stripped, prior):
            continue
        lines.append(line)
        prior = "\n".join(lines)
    return "\n".join(lines)


def _json_payloads_from_output(output: str) -> list[Any]:
    payloads: list[Any] = []
    seen: set[str] = set()
    candidates = [str(output or "").strip()]
    candidates.extend(part.strip() for part in re.split(r"\n\s*\n", str(output or "")) if part.strip())
    candidates.extend(
        line.strip()
        for line in str(output or "").splitlines()
        if line.strip().startswith(("[", "{"))
    )
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            payloads.append(json.loads(candidate))
        except Exception:
            continue
    return payloads


def _structured_report_row_count(output: str, required_fields: list[str]) -> int | None:
    """Return the apparent number of rows in structured report output."""

    rows = _structured_report_rows(output, required_fields)
    if rows is None:
        return None
    return len(rows)


def _structured_report_rows(output: str, required_fields: list[str]) -> list[dict[str, Any]] | None:
    """Return structured report rows from JSON object/list output when possible."""

    payloads = _json_payloads_from_output(output)
    if not payloads:
        return None
    required = {
        str(field or "").strip().lower()
        for field in required_fields
        if str(field or "").strip()
    }
    candidate_rows: list[list[dict[str, Any]]] = []
    saw_structured = False
    for payload in payloads:
        if isinstance(payload, list):
            saw_structured = True
            rows = [dict(item) for item in payload if isinstance(item, dict)]
            if rows or not payload:
                candidate_rows.append(rows)
            continue
        if not isinstance(payload, dict):
            continue
        saw_structured = True
        if not payload:
            candidate_rows.append([])
            continue
        keys = {str(key).strip().lower() for key in payload}
        if required and keys.intersection(required):
            candidate_rows.append([dict(payload)])
            continue
        rows = []
        for key, value in payload.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("entity", key)
                rows.append(row)
            else:
                rows.append({"entity": key, "value": value})
        candidate_rows.append(rows)
    if not candidate_rows:
        return [] if saw_structured else None
    return max(candidate_rows, key=len)


def _uniform_typed_field_errors(
    rows: list[dict[str, Any]] | None,
    required_fields: list[str],
    field_types: dict[str, Any],
) -> list[dict[str, Any]]:
    if rows is None or len(rows) < 3:
        return []
    typed_fields = [
        field
        for field in required_fields
        if str(field_types.get(field) or "text") in {"date", "number", "size"}
    ]
    if len(typed_fields) < 2:
        return []
    uniform_fields: list[dict[str, Any]] = []
    for field in typed_fields:
        field_key = str(field or "").strip().lower()
        values: list[str] = []
        missing = False
        for row in rows:
            match_key = next(
                (
                    key
                    for key in row
                    if str(key or "").strip().lower() == field_key
                ),
                None,
            )
            if match_key is None:
                missing = True
                break
            value = row.get(match_key)
            if value in (None, ""):
                missing = True
                break
            values.append(str(value).strip())
        if missing or len(values) != len(rows):
            continue
        unique = {value for value in values if value}
        if len(unique) == 1:
            uniform_fields.append(
                {
                    "field": field,
                    "field_type": str(field_types.get(field) or "text"),
                    "value": next(iter(unique)),
                }
            )
    if len(uniform_fields) < 2:
        return []
    return [
        {
            "error": "execution_shape_unscoped_report_fields",
            "message": (
                "Multiple typed fields have the same value on every row; this often means the action "
                "computed a global value and reused it for each entity."
            ),
            "field_errors": uniform_fields,
            "row_count": len(rows),
            "repair_hint": (
                "Compute every required field inside the per-entity scope. Do not reuse one global "
                "aggregate for all rows unless the output includes clear evidence that the identical "
                "values are genuinely per-entity results."
            ),
        }
    ]


def _matching_key(row: dict[str, Any], field: str) -> str | None:
    field_key = str(field or "").strip().lower()
    return next(
        (
            key
            for key in row
            if str(key or "").strip().lower() == field_key
        ),
        None,
    )


def _row_count_evidence(row: dict[str, Any]) -> int | None:
    for key, value in row.items():
        normalized = str(key or "").strip().lower()
        if normalized not in {
            "source_count",
            "record_count",
            "evidence_count",
            "event_count",
            "sample_count",
            "value_count",
            "commit_count",
        }:
            continue
        try:
            count = int(float(str(value).strip()))
        except (TypeError, ValueError):
            continue
        return max(0, count)
    return None


def _opposing_extrema_equal_errors(
    rows: list[dict[str, Any]] | None,
    opposing_pairs: list[Any],
) -> list[dict[str, Any]]:
    if rows is None or len(rows) < 3 or not opposing_pairs:
        return []
    errors: list[dict[str, Any]] = []
    for pair in opposing_pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        left, right = str(pair[0] or ""), str(pair[1] or "")
        if not left or not right:
            continue
        comparable_rows = 0
        equal_rows = 0
        equal_without_single_record_evidence = 0
        for row in rows:
            left_key = _matching_key(row, left)
            right_key = _matching_key(row, right)
            if left_key is None or right_key is None:
                continue
            left_value = str(row.get(left_key) or "").strip()
            right_value = str(row.get(right_key) or "").strip()
            if not left_value or not right_value:
                continue
            comparable_rows += 1
            if left_value != right_value:
                continue
            equal_rows += 1
            if _row_count_evidence(row) != 1:
                equal_without_single_record_evidence += 1
        if comparable_rows < 3:
            continue
        equal_ratio = equal_rows / comparable_rows
        unexplained_ratio = equal_without_single_record_evidence / comparable_rows
        if equal_ratio >= 0.8 and unexplained_ratio >= 0.5:
            errors.append(
                {
                    "error": "execution_shape_opposing_extrema_not_evidenced",
                    "message": (
                        "Opposing extrema fields are equal on most rows without per-row evidence "
                        "that each entity had only one underlying record. This often means the "
                        "action used one current/latest metadata value per entity instead of "
                        "enumerating the underlying record set."
                    ),
                    "field_pair": [left, right],
                    "row_count": len(rows),
                    "comparable_row_count": comparable_rows,
                    "equal_row_count": equal_rows,
                    "repair_hint": (
                        "Repair the report action so it gathers the complete underlying "
                        "records/events/measurements for each entity and computes the requested "
                        "extrema from those values. If equal extrema are legitimate, include a "
                        "source_count/record_count/evidence_count field showing a count of 1 for "
                        "those rows."
                    ),
                }
            )
    return errors


def _field_value_samples(output: str, field_name: str, *, limit: int = 6) -> list[str]:
    field = str(field_name or "").strip().lower()
    if not field:
        return []
    payloads = _json_payloads_from_output(output)
    if not payloads:
        return []
    samples: list[str] = []

    def visit(value: Any) -> None:
        if len(samples) >= limit:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if len(samples) >= limit:
                    return
                if str(key).strip().lower() == field:
                    text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=True, default=str)
                    text = " ".join(str(text).split())
                    if text:
                        samples.append(text[:160])
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
                if len(samples) >= limit:
                    return

    for payload in payloads:
        visit(payload)
        if len(samples) >= limit:
            break
    return samples


def _zero_size_aggregate_error(
    output: str,
    *,
    prompt: str,
    required_fields: list[str],
    field_types: dict[str, Any],
) -> dict[str, Any] | None:
    has_size_field = any(str(kind or "text") == "size" for kind in field_types.values())
    if not has_size_field:
        return None
    aggregate_requested = (
        any(str(field or "").strip().lower() in {"total", "sum", "aggregate", "combined", "overall"} for field in required_fields)
        or re.search(r"\b(?:total|sum|aggregate|combined|overall)\b", str(prompt or ""), re.IGNORECASE)
    )
    if not aggregate_requested:
        return None
    if not _ZERO_SIZE_AGGREGATE_RE.search(output):
        return None
    if _EMPTY_SET_EVIDENCE_RE.search(output):
        return None
    return {
        "error": "execution_shape_zero_size_report_output",
        "message": (
            "The report returned a zero total size without evidence that the measured entity/value set was empty. "
            "This often means parsing matched no size values and silently returned zero."
        ),
        "required_fields": required_fields,
        "observed_output_preview": output[:500],
        "repair_hint": (
            "Repair the one report action so it counts parsed size values and raises when a non-empty source yields "
            "zero parsed values. If the source is genuinely empty, state that explicitly."
        ),
    }


def execution_shape_output_errors(user_request: Any, records: list[Any]) -> list[dict[str, Any]]:
    """Execution-shape hints are diagnostic only; they never trigger output repair."""

    del user_request, records
    return []
