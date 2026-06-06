"""SQL intent, text normalization, identifiers, scalar, sanitization, and CSV helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.discovery_support.common import *

def looks_like_sql_intent(prompt: str, *, has_db_profile: bool = False) -> bool:
    """Return whether a prompt looks like actionable database/SQL work."""

    text = str(prompt or "").strip()
    if not text:
        return False
    is_explanatory = re.search(r"\b(?:what|explain|describe)\b", text, re.IGNORECASE)
    if _MUTATING_SQL_RE.search(text) and not is_explanatory:
        return True
    if _SQL_INTENT_RE.search(text) and (has_db_profile or _SQL_PROFILE_HINT_RE.search(text)):
        return True
    if _SQL_COUNT_INTENT_RE.search(text) and (
        has_db_profile or _PLURAL_DOMAIN_NOUN_RE.search(text)
    ):
        return True
    return bool(has_db_profile and _PLURAL_DOMAIN_NOUN_RE.search(text))


def _strip_sql_comments(sql: str) -> str:
    return re.sub(r"/\*.*?\*/|--[^\n\r]*", " ", str(sql or ""), flags=re.DOTALL)


def _compact_sql(sql: str) -> str:
    return " ".join(str(sql or "").strip().rstrip(";").split())


def _uses_temp_table_materialization(sql: str) -> bool:
    """Return whether SQL materializes intermediate data through a temp table."""

    return bool(_TEMP_TABLE_MATERIALIZATION_RE.search(_compact_sql(_strip_sql_comments(sql))))


def _prompt_explicitly_requests_temp_table(prompt: str) -> bool:
    """Return whether the user explicitly asked to create/materialize a temp table."""

    return bool(_TEMP_TABLE_REQUEST_RE.search(str(prompt or "")))


def _select_join_without_from_error(expression: exp.Expression) -> str:
    """Return a retryable PostgreSQL syntax hint for JOINs without a FROM source."""

    for select in expression.find_all(exp.Select):
        if select.args.get("joins") and select.args.get("from") is None:
            return (
                "join_without_from_source: SQL JOIN clauses require a FROM source. "
                "Move generated buckets, VALUES, function calls, or subqueries into a "
                "WITH CTE, a derived table, or the FROM clause before joining. For "
                "example use WITH requested(bucket) AS (...) SELECT ... FROM requested "
                "LEFT JOIN ...; or SELECT ... FROM generate_series(...) AS gs(bucket) "
                "LEFT JOIN ..."
            )
    return ""


def _sql_signature(sql: str) -> str:
    """Return a loose signature for detecting repeated SQL plans."""

    return re.sub(r'[\s"`]+', "", _compact_sql(sql).lower())


def _safe_limit(value: int | None, config: RuntimeConfig) -> int:
    default = int(getattr(config, "sql_agent_default_limit", 100) or 100)
    maximum = int(getattr(config, "sql_agent_max_rows", 1000) or 1000)
    requested = int(value or default)
    return max(1, min(maximum, requested))


def _safe_identifier(value: Any) -> str:
    text = str(value or "").strip()
    return text if _SAFE_IDENTIFIER_RE.match(text) else ""


def _profile_engine(profile: dict[str, Any]) -> str:
    engine = str(profile.get("engine") or "postgresql").strip().lower()
    if engine in {"postgres", "postgresql", "unknown_sql"}:
        return "postgresql"
    return engine


def _get_scalar(values: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in values and values[name] not in (None, ""):
            return str(values[name])
    lowered = {str(key).lower(): value for key, value in values.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return str(value)
    return ""


def _iter_scalar_values(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        payload: dict[str, Any] = {}
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            payload.update(_iter_scalar_values(child, child_prefix))
        return payload
    if isinstance(value, list) or value is None:
        return {}
    leaf = prefix.rsplit(".", 1)[-1] if prefix else ""
    if leaf and leaf != prefix:
        return {leaf: value, prefix: value}
    return {leaf: value} if leaf else {}


def _sanitize_text(text: Any, secrets: list[Any]) -> str:
    sanitized = str(text or "")
    for secret in secrets:
        value = str(secret or "")
        if value:
            sanitized = sanitized.replace(value, "****")
    return sanitized


def _parse_csv(stdout: str) -> tuple[list[str], list[dict[str, Any]]]:
    text = str(stdout or "").strip()
    if not text:
        return [], []
    reader = csv.DictReader(StringIO(text))
    rows = [dict(row) for row in reader]
    return list(reader.fieldnames or []), rows


def _coerce_int(value: Any) -> int | None:
    raw = str(value if value is not None else "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _tokenize_identifier(value: Any) -> list[str]:
    """Return coarse normalized tokens from a user/table/profile identifier."""

    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", text)
        if len(token) > 1
    ]


def _quote_postgres_identifier(value: str) -> str:
    return '"' + str(value or "").replace('"', '""') + '"'


def _qualified_table_sql(schema_name: str, table_name: str) -> str:
    if schema_name:
        return f"{_quote_postgres_identifier(schema_name)}.{_quote_postgres_identifier(table_name)}"
    return _quote_postgres_identifier(table_name)


def _qualified_table_name(schema_name: str, table_name: str) -> str:
    return f"{schema_name}.{table_name}" if schema_name else table_name


def _table_key(schema_name: str, table_name: str) -> str:
    schema = str(schema_name or "").strip().lower()
    table = str(table_name or "").strip().lower()
    return f"{schema}.{table}" if schema and table else table


__all__ = [name for name in globals() if not name.startswith("__")]
