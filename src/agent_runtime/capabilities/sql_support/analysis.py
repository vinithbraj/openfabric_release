"""SQL prompt rendering, query analysis, identifier quoting, and clarification helpers."""

from __future__ import annotations

from agent_runtime.capabilities.sql_support.common import *
from agent_runtime.capabilities.sql_support.discovery import *

def _complete_schema_summary(schema: dict[str, Any]) -> str:
    """Return all discovered table and column metadata, without credentials."""

    return _schema_summary(schema, max_tables=None, max_columns=None)


def _schema_relevant_tables(
    schema: dict[str, Any],
    *,
    max_tables: int = 40,
) -> list[dict[str, Any]]:
    """Return credential-free table metadata safe for planner prompt context."""

    tables = schema.get("tables") if isinstance(schema, dict) else []
    if not isinstance(tables, list):
        return []
    relevant: list[dict[str, Any]] = []
    for table in tables[:max_tables]:
        if not isinstance(table, dict):
            continue
        columns = table.get("columns") if isinstance(table.get("columns"), list) else []
        relevant.append(
            {
                "schema": table.get("schema"),
                "table": table.get("table"),
                "qualified_name": _qualified_table_name(
                    str(table.get("schema") or ""),
                    str(table.get("table") or ""),
                ),
                "columns": [
                    {"name": column.get("name"), "type": column.get("type")}
                    for column in columns[:30]
                    if isinstance(column, dict)
                ],
            }
        )
    return relevant


def _bounded_json(value: Any, *, max_chars: int) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 24)] + "... <truncated>"


def sql_prompt_lines_from_context(context: Any, *, stage: str = "") -> list[str]:
    """Render SQL-agentic planner guidance without exposing database credentials."""

    payload = dict(getattr(context, "session_context", context) or {})
    sql_context = payload.get(SQL_AGENTIC_CONTEXT_KEY)
    if not isinstance(sql_context, dict) or not sql_context:
        return []
    parameter_key = str(sql_context.get("parameter_key") or "").strip()
    engine = str(sql_context.get("engine") or "postgresql").strip() or "postgresql"
    schema_summary = str(sql_context.get("schema_summary") or "").strip()
    relevant_tables = sql_context.get("relevant_tables")
    relation_foreign_scheme = sql_context.get("relation_foreign_scheme")
    domain_context = (
        sql_context.get("domain_context_summary")
        if isinstance(sql_context.get("domain_context_summary"), dict)
        else {}
    )
    warnings = sql_context.get("schema_cache_warnings")
    lines = [
        "SQL agentic context is available for this request. "
        "Use sql.query for database work.",
        f"Selected database profile key: {parameter_key or '<unresolved>'}; engine: {engine}.",
        "Do not ask for raw host, user, password, port, or connection values; "
        "they are intentionally hidden.",
        "Do not use sqlite3, find, or invented OF_INPUT_DB_PATH for this saved PostgreSQL profile.",
        "Do not author SQL in decomposition, capability selection, or argument extraction. "
        "The sql.query runtime will ask its SQL LLM loop to author SQL from "
        "the natural-language prompt and schema.",
    ]
    normalized_stage = str(stage or "").strip().lower()
    if normalized_stage == "classification":
        lines.append(
            "Classification guidance: classify this as tool-required SQL work "
            "with likely_domains including sql."
        )
    elif normalized_stage == "decomposition":
        lines.append(
            "Decomposition guidance: use sql.query for database questions and sql.discover "
            "only for explicit schema inspection; use operator.python_transform only for "
            "post-query transformation of SQL results."
        )
        lines.append(
            "Decompose into smaller natural-language SQL tasks only when required; do not "
            "choose tables, columns, joins, filters, or SQL text deterministically."
        )
    elif normalized_stage == "argument_extraction":
        lines.append(
            "Argument extraction guidance: fill parameter_key from this context when unambiguous. "
            "For sql.query, pass a natural-language prompt with the original user request, "
            "task, dependencies, and DB key. Do not include a raw SQL argument."
        )
    elif normalized_stage == "failure_repair":
        lines.append(
            "Failure repair guidance: repair sql.query natural-language prompts using "
            "this schema context. "
            "Stay on sql capabilities; do not fall back to shell database tools "
            "or invented local database paths."
        )
    if warnings:
        lines.append("Schema cache warnings: " + _bounded_json(warnings, max_chars=800))
    if schema_summary:
        lines.extend(["SQL schema summary:", schema_summary[:10000]])
    if isinstance(relation_foreign_scheme, dict) and relation_foreign_scheme:
        lines.extend(
            [
                "Autodetected relation_foreign_scheme:",
                _bounded_json(relation_foreign_scheme, max_chars=6000),
                "Only relation_foreign_scheme.approved_foreign_keys are validator-approved joins; "
                "inferred_secondary_keys are prompt-only diagnostics.",
            ]
        )
    if domain_context:
        lines.extend(
            [
                "Parameter context guidance (prompt-only, not validation approval):",
                _bounded_json(domain_context, max_chars=6000),
            ]
        )
    if relevant_tables:
        lines.extend([
            "Relevant SQL tables and columns:",
            _bounded_json(relevant_tables, max_chars=6000),
        ])
    return lines


def _memory_hints(
    memory_store: AgentMemoryStore | None,
    *,
    prompt: str,
    profile_key: str,
) -> list[str]:
    if memory_store is None:
        return []
    try:
        entries = memory_store.retrieve(
            MemoryRetrievalContext(
                prompt=prompt,
                task_type="sql",
                tool_type="sql_agent",
                intent_type="query",
                tags=["sql", "database", profile_key],
                limit=5,
                max_chars=1800,
            ),
            record_use=True,
        )
    except Exception:
        return []
    hints: list[str] = []
    for entry in entries:
        text = " ".join([entry.summary, entry.instruction]).strip()
        if text:
            hints.append(text[:500])
    return hints


def _query_tables(expression: exp.Expression) -> set[str]:
    tables: set[str] = set()
    for table in expression.find_all(exp.Table):
        parts = [
            part
            for part in [table.args.get("db"), table.args.get("this")]
            if part is not None
        ]
        name = ".".join(str(part).strip('"') for part in parts if str(part).strip())
        if name:
            tables.add(name.lower())
    return tables


def _query_columns(expression: exp.Expression) -> set[str]:
    columns: set[str] = set()
    for column in expression.find_all(exp.Column):
        name = str(column.args.get("this") or "").strip('"')
        if name and name != "*":
            columns.add(name.lower())
    return columns


def _iter_join_column_pairs(
    expression: exp.Expression,
) -> list[tuple[exp.Column, exp.Column]]:
    pairs: list[tuple[exp.Column, exp.Column]] = []
    for condition in expression.find_all(exp.EQ):
        columns = [column for column in condition.find_all(exp.Column)]
        if len(columns) == 2:
            pairs.append((columns[0], columns[1]))
    return pairs


def _query_cte_names(expression: exp.Expression) -> set[str]:
    names: set[str] = set()
    for cte in expression.find_all(exp.CTE):
        alias = str(cte.alias or "").strip('"').lower()
        if alias:
            names.add(alias)
    return names


def _select_output_column_names(select_expression: exp.Expression) -> set[str]:
    if not isinstance(select_expression, exp.Select):
        return set()
    names: set[str] = set()
    for projection in list(select_expression.expressions or []):
        if isinstance(projection, exp.Alias):
            alias = _identifier_name(projection.args.get("alias"))
            if alias:
                names.add(alias.lower())
            continue
        if isinstance(projection, exp.Column):
            name = _identifier_name(projection.args.get("this"))
            if name and name != "*":
                names.add(name.lower())
    return names


def _query_derived_column_names(expression: exp.Expression) -> set[str]:
    """Return CTE/subquery output names that are valid within the query."""

    names: set[str] = set()
    for cte in expression.find_all(exp.CTE):
        alias = cte.args.get("alias")
        alias_columns = list(getattr(alias, "columns", []) or [])
        for column in alias_columns:
            name = _identifier_name(column)
            if name:
                names.add(name.lower())
        if not alias_columns:
            names.update(_select_output_column_names(cte.this))
    for subquery in expression.find_all(exp.Subquery):
        alias = subquery.args.get("alias")
        alias_columns = list(getattr(alias, "columns", []) or [])
        for column in alias_columns:
            name = _identifier_name(column)
            if name:
                names.add(name.lower())
        if not alias_columns:
            names.update(_select_output_column_names(subquery.this))
    return names


def _literal_limit_value(limit_expression: exp.Expression | None) -> int | None:
    if limit_expression is None:
        return None
    literal = limit_expression.args.get("expression")
    if not isinstance(literal, exp.Literal) or bool(literal.args.get("is_string")):
        return None
    try:
        return int(str(literal.this))
    except (TypeError, ValueError):
        return None


def _top_level_limit_value(expression: exp.Expression) -> int | None:
    limit_expression = expression.args.get("limit")
    if isinstance(limit_expression, exp.Expression):
        return _literal_limit_value(limit_expression)
    return None


def _query_requires_explicit_limit(expression: exp.Expression) -> bool:
    """Return whether a read-only query can return arbitrary rows."""

    if isinstance(expression, exp.Union):
        return True
    if not isinstance(expression, exp.Select):
        return False
    if expression.args.get("group") is not None:
        return True
    projections = list(expression.expressions or [])
    if not projections:
        return True
    for projection in projections:
        target = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(target, exp.AggFunc) or target.find(exp.AggFunc) is not None:
            continue
        if isinstance(target, exp.Literal):
            continue
        return True
    return False


def _coerce_sql_agent_action(raw: Any) -> SqlAgentAction:
    """Validate SQL action JSON, with a bridge for older fake LLM test payloads."""

    if isinstance(raw, SqlAgentAction):
        return raw
    if isinstance(raw, dict) and "action" not in raw and "sql" in raw:
        raw_assumptions = raw.get("assumptions")
        return SqlAgentAction(
            action="execute_sql",
            sql=str(raw.get("sql") or ""),
            sql_steps=[
                str(item)
                for item in (raw.get("sql_steps") if isinstance(raw.get("sql_steps"), list) else [])
                if str(item).strip()
            ],
            result_strategy=(
                str(raw.get("result_strategy") or "final_step")
                if str(raw.get("result_strategy") or "final_step") in {"final_step", "append_rows"}
                else "final_step"
            ),
            assumptions=[
                str(item)
                for item in (raw_assumptions if isinstance(raw_assumptions, list) else [])
            ],
            confidence=float(raw.get("confidence") or 0.0),
        )
    return SqlAgentAction.model_validate(raw)


def _unknown_columns_from_error(error: str) -> list[str]:
    """Extract rejected column identifiers from validation or PostgreSQL errors."""

    text = str(error or "")
    candidates: list[str] = []
    unknown_match = re.search(r"unknown column\(s\):\s*([^.;\n]+)", text, re.IGNORECASE)
    if unknown_match:
        candidates.extend(re.split(r"\s*,\s*", unknown_match.group(1)))
    candidates.extend(re.findall(r'column\s+"([^"]+)"', text, flags=re.IGNORECASE))
    return _dedupe_strings([item.strip().strip('"') for item in candidates if item.strip()])


def _schema_table_and_column_sets(schema: dict[str, Any]) -> tuple[set[str], set[str]]:
    table_names: set[str] = set()
    column_names: set[str] = set()
    for table in schema.get("tables") or []:
        if not isinstance(table, dict):
            continue
        schema_name = str(table.get("schema") or "").lower()
        table_name = str(table.get("table") or "").lower()
        if table_name:
            table_names.add(table_name)
            if schema_name:
                table_names.add(f"{schema_name}.{table_name}")
        for column in table.get("columns") or []:
            if isinstance(column, dict) and str(column.get("name") or "").strip():
                column_names.add(str(column.get("name")).lower())
    return table_names, column_names


def _schema_table_lookup(schema: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    lookup: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for table in schema.get("tables") or []:
        if not isinstance(table, dict):
            continue
        schema_name = str(table.get("schema") or "").strip()
        table_name = str(table.get("table") or "").strip()
        if not table_name:
            continue
        payload = (schema_name, table_name, table)
        lookup.setdefault(table_name.lower(), payload)
        if schema_name:
            lookup.setdefault(f"{schema_name}.{table_name}".lower(), payload)
    return lookup


_POSTGRES_UNQUOTED_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_$]*$")


def _identifier_name(value: Any) -> str:
    if isinstance(value, exp.Identifier):
        return str(value.name or "").strip()
    return str(value or "").strip().strip('"')


def _identifier_is_quoted(value: Any) -> bool:
    return isinstance(value, exp.Identifier) and bool(value.args.get("quoted"))


def _postgres_identifier_needs_quotes(value: str) -> bool:
    return _POSTGRES_UNQUOTED_IDENTIFIER_RE.fullmatch(str(value or "")) is None


def _schema_column_lookup(table_payload: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for column in table_payload.get("columns") or []:
        if not isinstance(column, dict):
            continue
        name = str(column.get("name") or "").strip()
        if name:
            lookup.setdefault(name.lower(), name)
    return lookup


def _schema_global_column_lookup(schema: dict[str, Any]) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for table in schema.get("tables") or []:
        if not isinstance(table, dict):
            continue
        for column in table.get("columns") or []:
            if not isinstance(column, dict):
                continue
            name = str(column.get("name") or "").strip()
            if name:
                candidates.setdefault(name.lower(), set()).add(name)
    return {key: next(iter(values)) for key, values in candidates.items() if len(values) == 1}


def _resolve_schema_table(
    table_lookup: dict[str, tuple[str, str, dict[str, Any]]],
    *,
    schema_name: str,
    table_name: str,
) -> tuple[str, str, dict[str, Any]] | None:
    keys = [
        f"{schema_name}.{table_name}".lower() if schema_name else "",
        table_name.lower(),
    ]
    return next((table_lookup.get(key) for key in keys if key), None)


def _table_requires_execution_quotes(schema_name: str, table_name: str) -> bool:
    return (
        bool(schema_name and _postgres_identifier_needs_quotes(schema_name))
        or _postgres_identifier_needs_quotes(table_name)
    )


def _quote_table_expression_for_execution(
    table: exp.Table,
    resolved: tuple[str, str, dict[str, Any]],
) -> bool:
    exact_schema, exact_table, _table_payload = resolved
    changed = False
    table_identifier = table.args.get("this")
    schema_identifier = table.args.get("db")
    table_name = _identifier_name(table_identifier)
    table_needs_quotes = _postgres_identifier_needs_quotes(exact_table)
    if table_name != exact_table or (
        table_needs_quotes and not _identifier_is_quoted(table_identifier)
    ):
        table.set("this", exp.to_identifier(exact_table, quoted=table_needs_quotes))
        changed = True
    schema_name = _identifier_name(schema_identifier)
    schema_needs_quotes = _postgres_identifier_needs_quotes(exact_schema)
    if exact_schema and (
        schema_name != exact_schema
        or (schema_needs_quotes and not _identifier_is_quoted(schema_identifier))
    ):
        table.set("db", exp.to_identifier(exact_schema, quoted=schema_needs_quotes))
        changed = True
    return changed


def _quote_column_identifier_for_execution(column: exp.Column, exact_column: str) -> bool:
    identifier = column.args.get("this")
    column_name = _identifier_name(identifier)
    column_needs_quotes = _postgres_identifier_needs_quotes(exact_column)
    if column_name == exact_column and (
        not column_needs_quotes or _identifier_is_quoted(identifier)
    ):
        return False
    column.set("this", exp.to_identifier(exact_column, quoted=column_needs_quotes))
    return True


def _quote_schema_column_identifier(
    schema_expression: exp.Schema,
    table_payload: dict[str, Any],
) -> bool:
    column_lookup = _schema_column_lookup(table_payload)
    if not column_lookup:
        return False
    changed = False
    expressions = list(schema_expression.expressions or [])
    replacement: list[exp.Expression] = []
    for item in expressions:
        if isinstance(item, exp.Identifier):
            exact_column = column_lookup.get(_identifier_name(item).lower())
            column_needs_quotes = _postgres_identifier_needs_quotes(exact_column)
            if exact_column and (
                _identifier_name(item) != exact_column
                or (column_needs_quotes and not _identifier_is_quoted(item))
            ):
                replacement.append(exp.to_identifier(exact_column, quoted=column_needs_quotes))
                changed = True
                continue
        replacement.append(item)
    if changed:
        schema_expression.set("expressions", replacement)
    return changed


def _quote_schema_identifiers_for_execution(
    expression: exp.Expression,
    schema: dict[str, Any],
) -> tuple[exp.Expression, bool]:
    """Quote schema-known PostgreSQL identifiers that would otherwise be folded."""

    execution_expression = expression.copy()
    table_lookup = _schema_table_lookup(schema)
    cte_names = _query_cte_names(execution_expression)
    changed = False
    for table in execution_expression.find_all(exp.Table):
        schema_name = _identifier_name(table.args.get("db"))
        table_name = _identifier_name(table.args.get("this"))
        if not table_name:
            continue
        if not schema_name and table_name.lower() in cte_names:
            continue
        resolved = _resolve_schema_table(
            table_lookup,
            schema_name=schema_name,
            table_name=table_name,
        )
        if resolved is not None:
            changed = _quote_table_expression_for_execution(table, resolved) or changed

    alias_lookup, referenced_tables = _query_table_context(execution_expression, schema)
    explicit_aliases = {
        str(table.alias or "").strip('"').lower()
        for table in execution_expression.find_all(exp.Table)
        if str(table.alias or "").strip('"')
    }
    global_columns = _schema_global_column_lookup(schema)
    referenced_column_lookups = [
        _schema_column_lookup(table_payload) for table_payload in referenced_tables
    ]
    for column in execution_expression.find_all(exp.Column):
        column_name = _identifier_name(column.args.get("this"))
        if not column_name or column_name == "*":
            continue
        column_key = column_name.lower()
        qualifier = _identifier_name(column.args.get("table"))
        qualifier_key = qualifier.lower()
        db_name = _identifier_name(column.args.get("db"))
        table_payload: dict[str, Any] | None = None
        resolved_table: tuple[str, str, dict[str, Any]] | None = None
        if qualifier:
            if db_name:
                resolved_table = _resolve_schema_table(
                    table_lookup,
                    schema_name=db_name,
                    table_name=qualifier,
                )
                if resolved_table is not None:
                    table_payload = resolved_table[2]
            else:
                table_payload = alias_lookup.get(qualifier_key)
                if qualifier_key not in explicit_aliases:
                    resolved_table = _resolve_schema_table(
                        table_lookup,
                        schema_name="",
                        table_name=qualifier,
                    )
        elif len(referenced_tables) == 1:
            table_payload = referenced_tables[0]

        exact_column = ""
        if table_payload is not None:
            exact_column = _schema_column_lookup(table_payload).get(column_key, "")
        else:
            matching_columns = {
                lookup[column_key]
                for lookup in referenced_column_lookups
                if column_key in lookup
            }
            if len(matching_columns) == 1:
                exact_column = next(iter(matching_columns))
            else:
                exact_column = global_columns.get(column_key, "")
        if exact_column:
            changed = _quote_column_identifier_for_execution(column, exact_column) or changed

        if resolved_table is not None and qualifier_key not in explicit_aliases:
            exact_schema, exact_table, _table_payload = resolved_table
            table_identifier = column.args.get("table")
            schema_identifier = column.args.get("db")
            table_needs_quotes = _postgres_identifier_needs_quotes(exact_table)
            if _identifier_name(table_identifier) != exact_table or (
                table_needs_quotes and not _identifier_is_quoted(table_identifier)
            ):
                column.set("table", exp.to_identifier(exact_table, quoted=table_needs_quotes))
                changed = True
            schema_needs_quotes = _postgres_identifier_needs_quotes(exact_schema)
            if db_name and exact_schema and (
                _identifier_name(schema_identifier) != exact_schema
                or (schema_needs_quotes and not _identifier_is_quoted(schema_identifier))
            ):
                column.set("db", exp.to_identifier(exact_schema, quoted=schema_needs_quotes))
                changed = True

    for schema_expression in execution_expression.find_all(exp.Schema):
        target = schema_expression.this
        if not isinstance(target, exp.Table):
            continue
        resolved = _resolve_schema_table(
            table_lookup,
            schema_name=_identifier_name(target.args.get("db")),
            table_name=_identifier_name(target.args.get("this")),
        )
        if resolved is not None:
            changed = _quote_schema_column_identifier(schema_expression, resolved[2]) or changed
    return execution_expression, changed


def _column_names_for_table(table_payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for column in table_payload.get("columns") or []:
        if isinstance(column, dict) and str(column.get("name") or "").strip():
            names.add(str(column.get("name")).strip().lower())
    return names


def _column_payloads_for_table(table_payload: dict[str, Any]) -> list[dict[str, str]]:
    columns: list[dict[str, str]] = []
    for column in table_payload.get("columns") or []:
        if not isinstance(column, dict):
            continue
        name = str(column.get("name") or "").strip()
        if not name:
            continue
        columns.append({"name": name, "type": str(column.get("type") or "").strip()})
    return columns


def _query_table_context(
    expression: exp.Expression,
    schema: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Map SQL table aliases/names to discovered table metadata."""

    table_lookup = _schema_table_lookup(schema)
    alias_lookup: dict[str, dict[str, Any]] = {}
    referenced_tables: list[dict[str, Any]] = []
    seen_payload_ids: set[int] = set()
    for table in expression.find_all(exp.Table):
        schema_name = str(table.args.get("db") or "").strip('"')
        table_name = str(table.args.get("this") or "").strip('"')
        if not table_name:
            continue
        lookup_keys = [
            f"{schema_name}.{table_name}".lower() if schema_name else "",
            table_name.lower(),
        ]
        resolved = next((table_lookup.get(key) for key in lookup_keys if key), None)
        if resolved is None:
            continue
        exact_schema, exact_table, table_payload = resolved
        aliases = {
            str(table.alias or "").strip('"').lower(),
            str(table.alias_or_name or "").strip('"').lower(),
            exact_table.lower(),
            table_name.lower(),
        }
        if exact_schema:
            aliases.add(f"{exact_schema}.{exact_table}".lower())
            aliases.add(f"{exact_schema}.{table_name}".lower())
        for alias in aliases:
            if alias:
                alias_lookup[alias] = table_payload
        payload_id = id(table_payload)
        if payload_id not in seen_payload_ids:
            seen_payload_ids.add(payload_id)
            referenced_tables.append(table_payload)
    return alias_lookup, referenced_tables


def _resolve_join_column_table(
    column: exp.Column,
    *,
    alias_lookup: dict[str, dict[str, Any]],
    referenced_tables: list[dict[str, Any]],
) -> dict[str, Any] | None:
    qualifier = str(column.args.get("table") or "").strip('"')
    schema_name = str(column.args.get("db") or "").strip('"')
    qualified = f"{schema_name}.{qualifier}".strip(".").lower()
    if qualified and qualified in alias_lookup:
        return alias_lookup[qualified]
    if qualifier and qualifier.lower() in alias_lookup:
        return alias_lookup[qualifier.lower()]
    if not qualifier:
        column_name = str(column.args.get("this") or "").strip('"').lower()
        if not column_name:
            return None
        matches = [
            table_payload
            for table_payload in referenced_tables
            if column_name in _column_names_for_table(table_payload)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(referenced_tables) == 1:
            return referenced_tables[0]
    return None


def _schema_relation_signature_sets(
    schema: dict[str, Any],
) -> tuple[set[tuple[str, str, str, str]], set[tuple[str, str, str, str]]]:
    relation = (
        schema.get("relation_foreign_scheme")
        if isinstance(schema.get("relation_foreign_scheme"), dict)
        else {}
    )
    approved: set[tuple[str, str, str, str]] = set()
    inferred: set[tuple[str, str, str, str]] = set()
    for item in relation.get("approved_foreign_keys") or []:
        if not isinstance(item, dict):
            continue
        signature = _relation_signature_from_payload(item)
        reverse = (signature[2], signature[3], signature[0], signature[1])
        if all(signature):
            approved.add(signature)
            approved.add(reverse)
    for item in relation.get("inferred_secondary_keys") or []:
        if not isinstance(item, dict):
            continue
        signature = _relation_signature_from_payload(item)
        reverse = (signature[2], signature[3], signature[0], signature[1])
        if all(signature):
            inferred.add(signature)
            inferred.add(reverse)
    return approved, inferred


def _sql_clarification_candidates(schema: dict[str, Any], user_request: str) -> list[dict[str, Any]]:
    prompt_tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", str(user_request or ""))
        if len(token) >= 3
    }
    candidates: list[dict[str, Any]] = []
    domain = {}
    context_json = schema.get("context_json")
    if isinstance(context_json, dict):
        raw_domain = context_json.get("user_provided_domain_context")
        if isinstance(raw_domain, dict):
            domain = raw_domain
    for table in schema.get("tables") or []:
        if not isinstance(table, dict):
            continue
        schema_name = str(table.get("schema") or "").strip()
        table_name = str(table.get("table") or "").strip()
        if not table_name:
            continue
        qualified = _qualified_table_name(schema_name, table_name)
        table_norm = re.sub(r"[^a-z0-9]+", "", table_name.lower())
        best_score = 0.0
        best_token = ""
        for token in prompt_tokens:
            token_norm = re.sub(r"[^a-z0-9]+", "", token.lower())
            token_singular = token_norm[:-1] if token_norm.endswith("s") else token_norm
            table_singular = table_norm[:-1] if table_norm.endswith("s") else table_norm
            score = max(
                SequenceMatcher(None, token_norm, table_norm).ratio(),
                SequenceMatcher(None, token_singular, table_singular).ratio(),
            )
            if token_singular and token_singular == table_singular:
                score = 1.0
            if score > best_score:
                best_score = score
                best_token = token
        if best_score >= 0.72:
            candidates.append(
                {
                    "entity_type": "sql_table",
                    "name": qualified,
                    "value": qualified,
                    "source": "discovered_schema",
                    "confidence": round(best_score, 3),
                    "matched_user_token": best_token,
                    "columns": [
                        str(column.get("name") or "")
                        for column in list(table.get("columns") or [])[:20]
                        if isinstance(column, dict)
                    ],
                }
            )
    for collection_name, entity_type, id_field in (
        ("concepts", "domain_concept", "concept_id"),
        ("metrics", "domain_metric", "metric_id"),
        ("relationships", "domain_relationship", "relationship_id"),
    ):
        for item in domain.get(collection_name) or []:
            if not isinstance(item, dict):
                continue
            labels = [
                str(item.get(id_field) or ""),
                str(item.get("display_name") or ""),
                str(item.get("one_line_definition") or item.get("definition") or ""),
                *[str(value) for value in item.get("user_phrases") or []],
                *[str(value) for value in item.get("synonyms") or []],
            ]
            label_tokens = {
                token.lower()
                for label in labels
                for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", label)
                if len(token) >= 3
            }
            overlap = sorted(prompt_tokens & label_tokens)
            if not overlap:
                continue
            confidence = min(0.98, 0.74 + (0.04 * len(overlap)))
            name = str(item.get("display_name") or item.get(id_field) or entity_type).strip()
            candidates.append(
                {
                    "entity_type": entity_type,
                    "name": name,
                    "value": item,
                    "source": "parameter_context",
                    "confidence": round(confidence, 3),
                    "matched_user_token": ", ".join(overlap[:6]),
                }
            )
    return sorted(candidates, key=lambda item: float(item.get("confidence") or 0.0), reverse=True)[
        :20
    ]


def _profile_identity_candidates(record: AgentParameterRecord) -> set[str]:
    candidates = {record.normalized_key, normalize_parameter_key(record.key)}
    candidates.update(normalize_parameter_key(alias) for alias in record.aliases)
    for _path, value in _safe_profile_identity_pairs(record):
        normalized = normalize_parameter_key(value)
        if normalized:
            candidates.add(normalized)
    expanded: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        expanded.add(candidate)
        expanded.update(part for part in re.split(r"[_:.-]+", candidate) if len(part) > 2)
    return expanded


def _safe_profile_identity_pairs(record: AgentParameterRecord) -> list[tuple[str, str]]:
    values = _iter_scalar_values(record.value_json)
    pairs: list[tuple[str, str]] = []
    for key, value in values.items():
        leaf = str(key or "").rsplit(".", 1)[-1].lower()
        if leaf not in {
            "alias",
            "connection_name",
            "database",
            "database_name",
            "db",
            "db_name",
            "dbname",
            "engine",
            "name",
        }:
            continue
        text = " ".join(str(value or "").strip().split())
        if text:
            pairs.append((leaf, text[:160]))
    return pairs


def _prompt_profile_candidates(prompt: str) -> set[str]:
    candidates = {
        normalize_parameter_key(match.group(0))
        for match in re.finditer(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,}", str(prompt or ""))
    }
    expanded: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        expanded.add(candidate)
        expanded.update(part for part in re.split(r"[_:.-]+", candidate) if len(part) > 2)
    return expanded


def _fuzzy_profile_score(prompt: str, record: AgentParameterRecord) -> tuple[float, str]:
    prompt_candidates = _prompt_profile_candidates(prompt)
    record_candidates = _profile_identity_candidates(record)
    best_score = 0.0
    best_reason = ""
    for prompt_candidate in prompt_candidates:
        if len(prompt_candidate) < 4:
            continue
        for record_candidate in record_candidates:
            if len(record_candidate) < 4:
                continue
            if prompt_candidate == record_candidate:
                score = 1.0
            elif prompt_candidate in record_candidate or record_candidate in prompt_candidate:
                score = 0.88
            else:
                score = SequenceMatcher(None, prompt_candidate, record_candidate).ratio()
            if score > best_score:
                best_score = score
                best_reason = f"{prompt_candidate}~{record_candidate}"
    return best_score, best_reason


def _discovery_rows_for_prompt(
    prompt: str,
    schema: dict[str, Any],
    *,
    limit: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    text = str(prompt or "")
    if re.search(r"\bcolumns?\b", text, re.IGNORECASE):
        rows = []
        for table in schema.get("tables") or []:
            if not isinstance(table, dict):
                continue
            for column in table.get("columns") or []:
                if not isinstance(column, dict):
                    continue
                rows.append(
                    {
                        "schema": str(table.get("schema") or ""),
                        "table": str(table.get("table") or ""),
                        "column": str(column.get("name") or ""),
                        "type": str(column.get("type") or ""),
                    }
                )
        return ["schema", "table", "column", "type"], rows[:limit]
    if re.search(r"\btables?\b", text, re.IGNORECASE):
        rows = [
            {
                "schema": str(table.get("schema") or ""),
                "table": str(table.get("table") or ""),
                "column_count": len(table.get("columns") or []),
            }
            for table in schema.get("tables") or []
            if isinstance(table, dict)
        ]
        return ["schema", "table", "column_count"], rows[:limit]
    rows = [{"schema": str(item)} for item in schema.get("schemas") or []]
    return ["schema"], rows[:limit]

__all__ = [name for name in globals() if not name.startswith("__")]
