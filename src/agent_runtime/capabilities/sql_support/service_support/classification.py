"""SQL execution classification and confirmation helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.service_support.common import *

class SqlAgentClassificationMixin:
    def _classify_sql_for_execution(
        self,
        sql: str,
        *,
        schema: dict[str, Any],
        limit: int,
    ) -> dict[str, Any]:
        original_sql = str(sql or "").strip()
        parse_sql = _compact_sql(_strip_sql_comments(original_sql))
        if not parse_sql:
            return self._error("SQL is empty.", sql=original_sql)
        try:
            parsed = sqlglot.parse(parse_sql, read="postgres")
        except Exception as exc:
            return {
                **self._error(f"SQL parse failed: {exc}", sql=original_sql),
                "safety_classification": {
                    "classification": "rejected",
                    "requires_confirmation": False,
                    "reason": "parse_failed",
                },
            }
        if len(parsed) != 1:
            return {
                **self._error("Only one SQL statement is allowed.", sql=original_sql),
                "safety_classification": {
                    "classification": "rejected",
                    "requires_confirmation": False,
                    "reason": "multiple_statements",
                },
            }
        expression = parsed[0]
        try:
            execution_expression, identifiers_quoted = _quote_schema_identifiers_for_execution(
                expression,
                schema,
            )
        except Exception:
            execution_expression = expression
            identifiers_quoted = False
        executed_sql = (
            execution_expression.sql(dialect="postgres") if identifiers_quoted else original_sql
        )

        def rejected(message: str, reason: str, **classification: Any) -> dict[str, Any]:
            return {
                **self._error(message, sql=executed_sql),
                "generated_sql": original_sql,
                "executed_sql": executed_sql,
                "safety_classification": {
                    "classification": "rejected",
                    "requires_confirmation": False,
                    "reason": reason,
                    **classification,
                },
            }

        blocked = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Drop,
            exp.Create,
            exp.Alter,
            exp.Command,
            exp.Transaction,
            exp.Copy,
            exp.Merge,
        )
        if (
            expression.find(*blocked) is not None
            or _MUTATING_SQL_RE.search(parse_sql)
            or _uses_temp_table_materialization(parse_sql)
        ):
            return {
                "status": "confirmation_required",
                "sql": executed_sql,
                "generated_sql": original_sql,
                "executed_sql": executed_sql,
                "safety_classification": {
                    "classification": "mutating",
                    "requires_confirmation": True,
                    "reason": "mutation_or_command_detected",
                    "identifier_quoting_applied": identifiers_quoted,
                },
                "error": "",
            }
        if not isinstance(execution_expression, (exp.Select, exp.Union)):
            return rejected(
                "Only SELECT/CTE read-only queries or confirmation-gated mutations "
                "are allowed.",
                "unsupported_statement",
            )
        malformed_join_error = _select_join_without_from_error(execution_expression)
        if malformed_join_error:
            return rejected(malformed_join_error, "join_without_from_source")
        table_names, column_names = _schema_table_and_column_sets(schema)
        cte_names = _query_cte_names(execution_expression)
        query_tables = {
            table for table in _query_tables(execution_expression) if table not in cte_names
        }
        if table_names and query_tables:
            unknown_tables = sorted(table for table in query_tables if table not in table_names)
            allowed = {
                "information_schema.schemata",
                "information_schema.columns",
                "schemata",
                "columns",
            }
            unknown_tables = [table for table in unknown_tables if table not in allowed]
            if unknown_tables:
                return rejected(
                    "SQL references unknown table(s): " + ", ".join(unknown_tables[:8]),
                    "unknown_table",
                )
        query_columns = _query_columns(execution_expression)
        if column_names and query_columns and query_tables:
            derived_column_names = _query_derived_column_names(execution_expression)
            unknown_columns = sorted(
                column
                for column in query_columns
                if column not in column_names and column not in derived_column_names
            )
            allowed_columns = {
                "schema_name",
                "table_schema",
                "table_name",
                "column_name",
                "data_type",
                "ordinal_position",
            }
            unknown_columns = [
                column for column in unknown_columns if column not in allowed_columns
            ]
            if unknown_columns:
                return rejected(
                    "SQL references unknown column(s): " + ", ".join(unknown_columns[:8]),
                    "unknown_column",
                )
        if column_names and query_columns and query_tables:
            alias_lookup, referenced_tables = _query_table_context(execution_expression, schema)
            referenced_column_sets = [
                _column_names_for_table(table_payload) for table_payload in referenced_tables
            ]
            for column in execution_expression.find_all(exp.Column):
                column_name = str(column.args.get("this") or "").strip('"')
                if not column_name or column_name == "*":
                    continue
                column_key = column_name.lower()
                qualifier = str(column.args.get("table") or "").strip('"').lower()
                if qualifier:
                    table_payload = alias_lookup.get(qualifier)
                    if table_payload is None:
                        continue
                    if column_key not in _column_names_for_table(table_payload):
                        table_label = _qualified_table_name(
                            str(table_payload.get("schema") or ""),
                            str(table_payload.get("table") or ""),
                        )
                        return rejected(
                            f'SQL references column "{column_name}" on {table_label}, '
                            "but that table does not contain it.",
                            "unknown_qualified_column",
                        )
                    continue
                if len(referenced_column_sets) == 1 and column_key not in referenced_column_sets[0]:
                    table_payload = referenced_tables[0]
                    table_label = _qualified_table_name(
                        str(table_payload.get("schema") or ""),
                        str(table_payload.get("table") or ""),
                    )
                    return rejected(
                        f'SQL references column "{column_name}", but referenced table '
                        f"{table_label} does not contain it.",
                        "unknown_unqualified_column",
                    )
        approved_join_signatures, inferred_join_signatures = _schema_relation_signature_sets(schema)
        join_relationship_warnings: list[dict[str, Any]] = []
        alias_lookup, referenced_tables = _query_table_context(execution_expression, schema)
        if len(referenced_tables) > 1:
            for left_column, right_column in _iter_join_column_pairs(execution_expression):
                left_name = str(left_column.args.get("this") or "").strip('"')
                right_name = str(right_column.args.get("this") or "").strip('"')
                if not left_name or not right_name or left_name == "*" or right_name == "*":
                    continue
                left_payload = _resolve_join_column_table(
                    left_column,
                    alias_lookup=alias_lookup,
                    referenced_tables=referenced_tables,
                )
                right_payload = _resolve_join_column_table(
                    right_column,
                    alias_lookup=alias_lookup,
                    referenced_tables=referenced_tables,
                )
                if not left_payload or not right_payload:
                    continue
                left_schema = str(left_payload.get("schema") or "")
                left_table = str(left_payload.get("table") or "")
                right_schema = str(right_payload.get("schema") or "")
                right_table = str(right_payload.get("table") or "")
                if _table_key(left_schema, left_table) == _table_key(right_schema, right_table):
                    continue
                signature = _join_signature(
                    left_schema,
                    left_table,
                    left_name,
                    right_schema,
                    right_table,
                    right_name,
                )
                left_label = _qualified_table_name(left_schema, left_table)
                right_label = _qualified_table_name(right_schema, right_table)
                if signature in approved_join_signatures:
                    continue
                if signature in inferred_join_signatures:
                    join_relationship_warnings.append(
                        {
                            "reason": "inferred_join_not_db_approved",
                            "left": left_label,
                            "right": right_label,
                            "left_column": left_name,
                            "right_column": right_name,
                        }
                    )
                    continue
                join_relationship_warnings.append(
                    {
                        "reason": "join_not_db_approved",
                        "left": left_label,
                        "right": right_label,
                        "left_column": left_name,
                        "right_column": right_name,
                    }
                )
        needs_limit = _query_requires_explicit_limit(execution_expression)
        limit_value = _top_level_limit_value(execution_expression)
        if needs_limit and limit_value is None:
            return rejected(
                "Read-only row-returning SQL must include an explicit literal LIMIT "
                f"no greater than {int(limit)}.",
                "missing_limit",
            )
        if limit_value is not None and (limit_value < 1 or limit_value > int(limit)):
            return rejected(
                f"SQL LIMIT must be a literal integer between 1 and {int(limit)}.",
                "limit_exceeds_runtime_limit",
                limit=limit_value,
            )
        return {
            "status": "success",
            "sql": executed_sql,
            "generated_sql": original_sql,
            "executed_sql": executed_sql,
            "safety_classification": {
                "classification": "read_only",
                "requires_confirmation": False,
                "reason": "single_read_only_statement",
                "limit": limit_value,
                "identifier_quoting_applied": identifiers_quoted,
                "join_relationship_warnings": join_relationship_warnings,
                "join_relationship_warning_count": len(join_relationship_warnings),
                "join_approval_policy": (
                    "database_foreign_keys_are_approved; "
                    "semantic_or_inferred_read_only_joins_warn_only"
                ),
            },
            "error": "",
        }


    def _sql_confirmation_action(
        self,
        *,
        prompt: str,
        resolved: _ResolvedProfile,
        generated_sql: str,
        executed_sql: str,
        safety_classification: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        return {
            "capability_id": "sql.query",
            "operation_id": operation or "query",
            "description": f"Execute confirmation-gated SQL against {resolved.key}.",
            "arguments": {
                "prompt": prompt,
                "parameter_key": resolved.key,
                "generated_sql": generated_sql,
                "executed_sql": executed_sql,
                "sql": executed_sql,
                "risk": "high",
                "safety_classification": safety_classification,
            },
        }


    def _validate_readonly_sql(
        self,
        sql: str,
        *,
        schema: dict[str, Any],
        limit: int,
    ) -> dict[str, Any]:
        validation = self._classify_sql_for_execution(sql, schema=schema, limit=limit)
        if validation.get("status") != "success":
            if validation.get("status") == "confirmation_required":
                return self._error("Only read-only SELECT statements are allowed.", sql=sql)
            return validation
        return {
            "status": "success",
            "sql": str(validation.get("executed_sql") or validation.get("sql") or "").strip(),
            "safety_classification": validation.get("safety_classification") or {},
            "error": "",
        }


__all__ = []
