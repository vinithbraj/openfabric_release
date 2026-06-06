"""SQL schema payload, relation, catalog, and summary helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.discovery_support.common import *
from agent_runtime.capabilities.sql_support.discovery_support.sql_text import *

def _join_signature(
    left_schema: str,
    left_table: str,
    left_column: str,
    right_schema: str,
    right_table: str,
    right_column: str,
) -> tuple[str, str, str, str]:
    return (
        _table_key(left_schema, left_table),
        str(left_column or "").strip().lower(),
        _table_key(right_schema, right_table),
        str(right_column or "").strip().lower(),
    )


def _relation_signature_from_payload(item: dict[str, Any]) -> tuple[str, str, str, str]:
    left = str(item.get("left") or "").strip()
    right = str(item.get("right") or "").strip()
    left_schema, _, left_table = left.rpartition(".")
    right_schema, _, right_table = right.rpartition(".")
    if not left_table:
        left_table = left
        left_schema = ""
    if not right_table:
        right_table = right
        right_schema = ""
    return _join_signature(
        left_schema,
        left_table,
        str(item.get("left_column") or ""),
        right_schema,
        right_table,
        str(item.get("right_column") or ""),
    )


def _relation_foreign_scheme_from_catalog(schema_catalog: dict[str, Any]) -> dict[str, Any]:
    refreshed_at = str(schema_catalog.get("refreshed_at") or datetime.now(UTC).isoformat())
    return {
        "version": 1,
        "source": "postgresql_information_schema",
        "refreshed_at": refreshed_at,
        "approved_foreign_keys": list(schema_catalog.get("approved_joins") or []),
        "inferred_secondary_keys": list(schema_catalog.get("inferred_secondary_keys") or []),
    }


def _legacy_tables_from_schema_catalog(schema_catalog: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for table in schema_catalog.get("tables") or []:
        if not isinstance(table, dict):
            continue
        schema_name = str(table.get("schema") or table.get("table_schema") or "").strip()
        table_name = str(table.get("table") or table.get("table_name") or "").strip()
        if not table_name:
            continue
        columns: list[dict[str, Any]] = []
        for column in table.get("columns") or []:
            if not isinstance(column, dict):
                continue
            column_name = str(column.get("name") or column.get("column_name") or "").strip()
            if not column_name:
                continue
            columns.append(
                {
                    "name": column_name,
                    "type": str(column.get("type") or column.get("data_type") or "").strip(),
                    "udt_name": str(column.get("udt_name") or ""),
                    "is_nullable": str(column.get("is_nullable") or ""),
                    "column_default": str(column.get("column_default") or ""),
                    "character_maximum_length": _coerce_int(
                        column.get("character_maximum_length")
                    ),
                    "numeric_precision": _coerce_int(column.get("numeric_precision")),
                    "numeric_scale": _coerce_int(column.get("numeric_scale")),
                    "ordinal_position": _coerce_int(
                        column.get("ordinal_position") or column.get("ordinal")
                    )
                    or 0,
                    "schema": schema_name,
                    "table": table_name,
                }
            )
        payload: dict[str, Any] = {
            "schema": schema_name,
            "table": table_name,
            "columns": sorted(columns, key=lambda item: int(item.get("ordinal_position") or 0)),
        }
        for key in (
            "primary_keys",
            "indexes",
            "approx_rows",
            "approx_dead_rows",
            "last_analyze",
            "last_autoanalyze",
        ):
            if key in table:
                payload[key] = table[key]
        tables.append(payload)
    return sorted(
        tables,
        key=lambda item: (str(item.get("schema") or ""), str(item.get("table") or "")),
    )


def _schema_payload_from_catalog(
    *,
    parameter_key: str,
    schema_catalog: dict[str, Any],
    relation_foreign_scheme: dict[str, Any] | None = None,
    context_json: dict[str, Any] | None = None,
    schema_discovery: dict[str, Any] | None = None,
    schema_columns: list[str] | None = None,
) -> dict[str, Any]:
    relation_payload = (
        dict(relation_foreign_scheme)
        if isinstance(relation_foreign_scheme, dict)
        else _relation_foreign_scheme_from_catalog(schema_catalog)
    )
    return {
        "parameter_key": parameter_key,
        "engine": "postgresql",
        "schemas": list(schema_catalog.get("schemas") or []),
        "schema_catalog": dict(schema_catalog),
        "relation_foreign_scheme": relation_payload,
        "context_json": dict(context_json or {}),
        "domain_context_summary": parameter_context_summary(context_json or {}),
        "schema_discovery": dict(schema_discovery or {}),
        "tables": _legacy_tables_from_schema_catalog(schema_catalog),
        "schema_columns": list(schema_columns or []),
        "refreshed_at": str(
            schema_catalog.get("refreshed_at")
            or relation_payload.get("refreshed_at")
            or ""
        ),
    }


def _stored_schema_payload_from_profile(resolved: _ResolvedProfile) -> dict[str, Any] | None:
    value_json = resolved.record.value_json if isinstance(resolved.record.value_json, dict) else {}
    schema_discovery = value_json.get("schema_discovery")
    if (
        isinstance(schema_discovery, dict)
        and str(schema_discovery.get("status") or "").strip().lower()
        not in {"", "success"}
    ):
        return None
    schema_catalog = value_json.get("schema_catalog")
    if not isinstance(schema_catalog, dict) or not isinstance(schema_catalog.get("tables"), list):
        return None
    relation_foreign_scheme = value_json.get("relation_foreign_scheme")
    if not isinstance(relation_foreign_scheme, dict):
        relation_foreign_scheme = _relation_foreign_scheme_from_catalog(schema_catalog)
    return _schema_payload_from_catalog(
        parameter_key=resolved.key,
        schema_catalog=schema_catalog,
        relation_foreign_scheme=relation_foreign_scheme,
        context_json=resolved.record.context_json,
        schema_discovery=schema_discovery if isinstance(schema_discovery, dict) else {},
    )


def _build_inferred_secondary_keys(
    column_rows: list[dict[str, Any]],
    *,
    approved_signatures: set[tuple[str, str, str, str]],
    limit: int = 500,
) -> list[dict[str, Any]]:
    by_schema_and_column: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for row in column_rows:
        schema_name = str(row.get("table_schema") or "").strip()
        table_name = str(row.get("table_name") or "").strip()
        column_name = str(row.get("column_name") or "").strip()
        if not (schema_name and table_name and column_name):
            continue
        column_key = column_name.lower()
        if not (
            column_key.endswith("id")
            or column_key.endswith("uid")
            or column_key.endswith("number")
            or column_key.endswith("key")
        ):
            continue
        by_schema_and_column.setdefault((schema_name, column_key), []).append(
            (table_name, column_name, schema_name)
        )
    inferred: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for (_schema_name, _column_key), entries in sorted(by_schema_and_column.items()):
        if len(entries) < 2:
            continue
        for left_index, left in enumerate(entries):
            for right in entries[left_index + 1 :]:
                left_table, left_column, left_schema = left
                right_table, right_column, right_schema = right
                signature = _join_signature(
                    left_schema,
                    left_table,
                    left_column,
                    right_schema,
                    right_table,
                    right_column,
                )
                reverse = _join_signature(
                    right_schema,
                    right_table,
                    right_column,
                    left_schema,
                    left_table,
                    left_column,
                )
                if (
                    signature in approved_signatures
                    or reverse in approved_signatures
                    or signature in seen
                    or reverse in seen
                ):
                    continue
                seen.add(signature)
                seen.add(reverse)
                inferred.append(
                    {
                        "left": _qualified_table_name(left_schema, left_table),
                        "left_column": left_column,
                        "right": _qualified_table_name(right_schema, right_table),
                        "right_column": right_column,
                        "match_reason": "same_secondary_key_name",
                        "confidence": "inferred",
                        "requires_validation": True,
                        "status": "NOT_APPROVED_INFERRED",
                    }
                )
                if len(inferred) >= limit:
                    return inferred
    return inferred


def _build_schema_payload_from_discovery_rows(
    *,
    parameter_key: str,
    schemas: list[str],
    schema_columns: list[str],
    column_rows: list[dict[str, Any]],
    primary_key_rows: list[dict[str, Any]],
    foreign_key_rows: list[dict[str, Any]],
    index_rows: list[dict[str, Any]],
    stats_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    refreshed_at = datetime.now(UTC).isoformat()
    table_map: dict[tuple[str, str], dict[str, Any]] = {}

    def table_payload(schema_name: str, table_name: str) -> dict[str, Any]:
        key = (schema_name, table_name)
        if key not in table_map:
            table_map[key] = {
                "schema": schema_name,
                "table": table_name,
                "qualified_name": _qualified_table_name(schema_name, table_name),
                "columns": [],
                "primary_keys": [],
                "indexes": [],
            }
        return table_map[key]

    for row in column_rows:
        schema_name = str(row.get("table_schema") or "").strip()
        table_name = str(row.get("table_name") or "").strip()
        column_name = str(row.get("column_name") or "").strip()
        if not (schema_name and table_name and column_name):
            continue
        table_payload(schema_name, table_name)["columns"].append(
            {
                "name": column_name,
                "type": str(row.get("data_type") or "").strip(),
                "data_type": str(row.get("data_type") or "").strip(),
                "udt_name": str(row.get("udt_name") or ""),
                "is_nullable": str(row.get("is_nullable") or ""),
                "column_default": str(row.get("column_default") or ""),
                "character_maximum_length": _coerce_int(
                    row.get("character_maximum_length")
                ),
                "numeric_precision": _coerce_int(row.get("numeric_precision")),
                "numeric_scale": _coerce_int(row.get("numeric_scale")),
                "ordinal_position": _coerce_int(row.get("ordinal_position")) or 0,
            }
        )

    pk_seen: set[tuple[str, str, str]] = set()
    for row in primary_key_rows:
        schema_name = str(row.get("table_schema") or "").strip()
        table_name = str(row.get("table_name") or "").strip()
        column_name = str(row.get("column_name") or "").strip()
        if not (schema_name and table_name and column_name):
            continue
        signature = (schema_name, table_name, column_name)
        if signature in pk_seen:
            continue
        pk_seen.add(signature)
        table_payload(schema_name, table_name)["primary_keys"].append(column_name)

    index_seen: set[tuple[str, str, str]] = set()
    for row in index_rows:
        schema_name = str(row.get("table_schema") or "").strip()
        table_name = str(row.get("table_name") or "").strip()
        index_name = str(row.get("index_name") or "").strip()
        indexdef = str(row.get("indexdef") or "").strip()
        if not (schema_name and table_name and index_name):
            continue
        signature = (schema_name, table_name, index_name)
        if signature in index_seen:
            continue
        index_seen.add(signature)
        table_payload(schema_name, table_name)["indexes"].append(
            {"index_name": index_name, "indexdef": indexdef, "name": index_name, "def": indexdef}
        )

    for row in stats_rows:
        schema_name = str(row.get("table_schema") or "").strip()
        table_name = str(row.get("table_name") or "").strip()
        if not (schema_name and table_name):
            continue
        payload = table_payload(schema_name, table_name)
        payload["approx_rows"] = _coerce_int(row.get("approx_rows"))
        payload["approx_dead_rows"] = _coerce_int(row.get("approx_dead_rows"))
        if str(row.get("last_analyze") or "").strip():
            payload["last_analyze"] = str(row.get("last_analyze") or "").strip()
        if str(row.get("last_autoanalyze") or "").strip():
            payload["last_autoanalyze"] = str(row.get("last_autoanalyze") or "").strip()

    approved_joins: list[dict[str, Any]] = []
    approved_signatures: set[tuple[str, str, str, str]] = set()
    for row in foreign_key_rows:
        child_schema = str(row.get("child_schema") or "").strip()
        child_table = str(row.get("child_table") or "").strip()
        child_column = str(row.get("child_column") or "").strip()
        parent_schema = str(row.get("parent_schema") or "").strip()
        parent_table = str(row.get("parent_table") or "").strip()
        parent_column = str(row.get("parent_column") or "").strip()
        if not (
            child_schema
            and child_table
            and child_column
            and parent_schema
            and parent_table
            and parent_column
        ):
            continue
        signature = _join_signature(
            child_schema,
            child_table,
            child_column,
            parent_schema,
            parent_table,
            parent_column,
        )
        reverse = _join_signature(
            parent_schema,
            parent_table,
            parent_column,
            child_schema,
            child_table,
            child_column,
        )
        if signature in approved_signatures or reverse in approved_signatures:
            continue
        approved_signatures.add(signature)
        approved_signatures.add(reverse)
        approved_joins.append(
            {
                "left": _qualified_table_name(child_schema, child_table),
                "left_column": child_column,
                "right": _qualified_table_name(parent_schema, parent_table),
                "right_column": parent_column,
                "constraint_name": str(row.get("constraint_name") or ""),
                "match_reason": "foreign_key",
                "confidence": "approved",
                "requires_validation": False,
                "status": "APPROVED",
            }
        )

    inferred_secondary_keys = _build_inferred_secondary_keys(
        column_rows,
        approved_signatures=approved_signatures,
    )
    for table in table_map.values():
        table["columns"] = sorted(
            table.get("columns") or [],
            key=lambda item: int(item.get("ordinal_position") or 0),
        )
        table["indexes"] = sorted(
            table.get("indexes") or [],
            key=lambda item: str(item.get("index_name") or item.get("name") or ""),
        )
    schema_catalog = {
        "version": 1,
        "parameter_key": parameter_key,
        "engine": "postgresql",
        "schemas": list(schemas),
        "tables": sorted(
            table_map.values(),
            key=lambda item: (str(item.get("schema") or ""), str(item.get("table") or "")),
        ),
        "approved_joins": approved_joins,
        "inferred_secondary_keys": inferred_secondary_keys,
        "refreshed_at": refreshed_at,
    }
    return _schema_payload_from_catalog(
        parameter_key=parameter_key,
        schema_catalog=schema_catalog,
        relation_foreign_scheme=_relation_foreign_scheme_from_catalog(schema_catalog),
        schema_discovery={"status": "success", "refreshed_at": refreshed_at, "warnings": []},
        schema_columns=schema_columns,
    )


def _schema_value_json_from_schema_result(
    schema_result: dict[str, Any],
    *,
    parameter_key: str,
) -> dict[str, Any]:
    if schema_result.get("status") != "success":
        refreshed_at = datetime.now(UTC).isoformat()
        schema_catalog = {
            "version": 1,
            "parameter_key": parameter_key,
            "engine": "postgresql",
            "schemas": [],
            "tables": [],
            "approved_joins": [],
            "inferred_secondary_keys": [],
            "refreshed_at": refreshed_at,
        }
        return {
            "schema_catalog": schema_catalog,
            "relation_foreign_scheme": _relation_foreign_scheme_from_catalog(schema_catalog),
            "schema_discovery": {
                "status": "error",
                "refreshed_at": refreshed_at,
                "warnings": list(schema_result.get("warnings") or []),
                "errors": [str(schema_result.get("error") or "schema_discovery_failed")],
            },
        }
    schema = schema_result.get("schema") if isinstance(schema_result.get("schema"), dict) else {}
    schema_catalog = schema.get("schema_catalog") if isinstance(schema.get("schema_catalog"), dict) else {}
    relation_foreign_scheme = (
        schema.get("relation_foreign_scheme")
        if isinstance(schema.get("relation_foreign_scheme"), dict)
        else _relation_foreign_scheme_from_catalog(schema_catalog)
    )
    refreshed_at = str(
        schema.get("refreshed_at")
        or relation_foreign_scheme.get("refreshed_at")
        or datetime.now(UTC).isoformat()
    )
    return {
        "schema_catalog": schema_catalog,
        "relation_foreign_scheme": relation_foreign_scheme,
        "schema_discovery": {
            "status": "success",
            "refreshed_at": refreshed_at,
            "warnings": list(schema_result.get("warnings") or []),
            "errors": [],
        },
    }


def _merge_discovery_update_value_json(
    existing: AgentParameterRecord,
    draft_value_json: dict[str, Any],
) -> dict[str, Any]:
    existing_value = dict(existing.value_json or {})
    merged = {
        key: value
        for key, value in existing_value.items()
        if key not in _DISCOVERDB_GENERATED_VALUE_FIELDS
    }
    for key, value in draft_value_json.items():
        if key in _DISCOVERDB_GENERATED_VALUE_FIELDS:
            merged[key] = value
    if "schema_catalog" not in merged and "schema_catalog" in draft_value_json:
        merged["schema_catalog"] = draft_value_json["schema_catalog"]
    if "relation_foreign_scheme" not in merged and "relation_foreign_scheme" in draft_value_json:
        merged["relation_foreign_scheme"] = draft_value_json["relation_foreign_scheme"]
    if "schema_discovery" not in merged and "schema_discovery" in draft_value_json:
        merged["schema_discovery"] = draft_value_json["schema_discovery"]
    return merged


def _schema_summary(
    schema: dict[str, Any],
    *,
    max_tables: int | None = 80,
    max_columns: int | None = 40,
) -> str:
    tables = schema.get("tables") if isinstance(schema, dict) else []
    if not isinstance(tables, list):
        return "{}"
    compact_tables = []
    selected_tables = tables if max_tables is None else tables[:max_tables]
    truncated_columns: list[dict[str, Any]] = []
    for table in selected_tables:
        if not isinstance(table, dict):
            continue
        columns = table.get("columns") if isinstance(table.get("columns"), list) else []
        selected_columns = columns if max_columns is None else columns[:max_columns]
        if max_columns is not None and len(columns) > max_columns:
            truncated_columns.append(
                {
                    "schema": table.get("schema"),
                    "table": table.get("table"),
                    "column_count": len(columns),
                    "included_column_count": max_columns,
                }
            )
        compact_tables.append(
            {
                "schema": table.get("schema"),
                "table": table.get("table"),
                "columns": [
                    {"name": column.get("name"), "type": column.get("type")}
                    for column in selected_columns
                    if isinstance(column, dict)
                ],
            }
        )
    return json.dumps(
        {
            "tables": compact_tables,
            "table_count": len(tables),
            "included_table_count": len(compact_tables),
            "approved_foreign_key_count": len(
                (
                    schema.get("relation_foreign_scheme", {})
                    if isinstance(schema.get("relation_foreign_scheme"), dict)
                    else {}
                ).get("approved_foreign_keys", [])
            ),
            "inferred_secondary_key_count": len(
                (
                    schema.get("relation_foreign_scheme", {})
                    if isinstance(schema.get("relation_foreign_scheme"), dict)
                    else {}
                ).get("inferred_secondary_keys", [])
            ),
            "truncated": bool(
                (max_tables is not None and len(tables) > max_tables) or truncated_columns
            ),
            "truncated_columns": truncated_columns,
        },
        sort_keys=True,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
