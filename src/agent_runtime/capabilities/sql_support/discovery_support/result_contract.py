"""SQL result-shape contract fallback helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.discovery_support.common import *

_COUNT_BUCKET_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "by",
    "each",
    "for",
    "from",
    "get",
    "give",
    "grouped",
    "in",
    "into",
    "me",
    "number",
    "of",
    "on",
    "per",
    "the",
    "there",
    "to",
    "up",
    "with",
}


def _singular_term(term: str) -> str:
    value = str(term or "").strip().lower()
    if len(value) > 4 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 3 and value.endswith("s"):
        return value[:-1]
    return value


def _count_bucket_contract(prompt: str) -> dict[str, Any] | None:
    normalized = re.sub(r"\s+", " ", str(prompt or "").lower())
    if not normalized:
        return None
    count_requested = any(
        token in normalized
        for token in (
            "count",
            "number of",
            "how many",
            "how much",
            "total number",
        )
    )
    if not count_requested:
        return None
    bucket_requested = any(
        token in normalized
        for token in (
            "for each",
            " per ",
            " by ",
            "group by",
            "grouped by",
            "1, 2",
            " through ",
            " to ",
            "upto",
            "up to",
            "all the way",
        )
    )
    if not bucket_requested:
        return None
    subject_terms: list[str] = []
    subject_patterns = [
        r"\b(?:number|count)\s+of\s+([a-zA-Z0-9_]+)",
        r"\bhow\s+many\s+([a-zA-Z0-9_]+)",
        r"\bcount\s+([a-zA-Z0-9_]+)",
    ]
    for pattern in subject_patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        term = _singular_term(match.group(1))
        if term and term not in _COUNT_BUCKET_STOPWORDS and term not in subject_terms:
            subject_terms.append(term)
    return {"kind": "count_bucket", "subject_terms": subject_terms}


def _fallback_sql_result_shape_error(
    *,
    prompt: str,
    sql: str,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> str:
    """Return a conservative retryable result-shape error if the LLM review is unavailable."""

    contract = _count_bucket_contract(prompt)
    if not contract:
        return ""
    normalized_columns = {str(column or "").strip().lower() for column in columns}
    countish_columns = {
        column
        for column in normalized_columns
        if column in {"count", "row_count", "total", "number", "num"}
        or "count" in column
        or column.startswith("num_")
    }
    has_count_column = any(
        column in countish_columns
        for column in normalized_columns
    )
    has_bucket_column = any(
        column not in countish_columns
        for column in normalized_columns
    )
    normalized_sql = str(sql or "").lower()
    subject_terms = [
        str(term or "").strip().lower()
        for term in list(contract.get("subject_terms") or [])
        if str(term or "").strip()
    ]
    uses_requested_subject = not subject_terms or any(
        term in normalized_sql or f"{term}s" in normalized_sql
        for term in subject_terms
    )
    if has_bucket_column and has_count_column and uses_requested_subject:
        return ""
    missing: list[str] = []
    if not has_bucket_column:
        missing.append("a bucket/entity label column")
    if not has_count_column:
        missing.append("a count/total metric column")
    if not uses_requested_subject:
        missing.append(
            "a query over the requested subject data: " + ", ".join(subject_terms)
        )
    if not rows:
        missing.append("non-empty result rows")
    return (
        "sql_result_shape_does_not_answer_count_bucket_request: expected "
        "one row per requested bucket/entity with a bucket label column and "
        "a count/total metric column, computed from the requested subject data. "
        "Missing " + ", ".join(missing) + "."
    )


def _should_review_sql_result_contract(
    *,
    prompt: str,
    sql: str,
    result_strategy: str,
) -> bool:
    """Return whether this result benefits from a typed LLM answer-contract review."""

    if str(result_strategy or "") == "append_rows":
        return True
    lowered_sql = str(sql or "").lower()
    if "generate_series" in lowered_sql or re.search(r"\bvalues\s*\(", lowered_sql):
        return True
    lowered_prompt = re.sub(r"\s+", " ", str(prompt or "").lower())
    if not lowered_prompt:
        return False
    has_aggregate_intent = bool(
        re.search(
            r"\b(?:count|counts|how\s+many|number\s+of|total|sum|average|avg|minimum|maximum|min|max)\b",
            lowered_prompt,
        )
    )
    has_bucket_or_partition_intent = bool(
        re.search(
            r"\b(?:for\s+each|per|group(?:ed)?\s+by|by|through|upto|up\s+to|"
            r"all\s+the\s+way|range|bucket|buckets|1\s*,\s*2)\b",
            lowered_prompt,
        )
    )
    return has_aggregate_intent and has_bucket_or_partition_intent


__all__ = [name for name in globals() if not name.startswith("__")]
