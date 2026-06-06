"""SQL service schema discovery operations."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.service_support.common import *

class SqlAgentSchemaDiscoveryMixin:
    def prepare_agentic_context(
        self,
        *,
        prompt: str,
        parameter_key: str = "",
        refresh_schema: bool = False,
        selected_table: str = "",
    ) -> dict[str, Any]:
        """Resolve DB profile and schema metadata for the typed SQL DAG path."""

        if not bool(getattr(self.config, "sql_agent_enabled", True)):
            return self._error("SQL agent is disabled by runtime configuration.")
        profile_result = self._resolve_profile(prompt=prompt, parameter_key=parameter_key)
        if profile_result.get("status") != "success":
            return profile_result
        resolved = profile_result["profile"]
        engine = _profile_engine(resolved.profile)
        if engine != "postgresql":
            return self._error(
                f"SQL agent v1 supports PostgreSQL profiles only; matched engine is {engine}.",
                parameter_key=resolved.key,
                engine=engine,
            )
        schema_result = self.discover_schema(resolved, refresh=refresh_schema)
        if schema_result.get("status") != "success":
            return {
                **schema_result,
                "parameter_key": resolved.key,
                "engine": "postgresql",
            }
        schema = (
            schema_result.get("schema")
            if isinstance(schema_result.get("schema"), dict)
            else {}
        )
        warnings = list(schema_result.get("warnings") or [])
        _emit_sql_agent_event(
            self.context,
            event_type="sql.context.prepared",
            title="SQL context prepared",
            summary=(
                "Stored or discovered SQL schema context was loaded for SQL agent prompting."
            ),
            details=_schema_trace_details(
                parameter_key=resolved.key,
                schema=schema,
                warnings=warnings,
            ),
        )
        return {
            "status": "success",
            "parameter_key": resolved.key,
            "engine": "postgresql",
            "schema_summary": _schema_summary(schema),
            "relevant_tables": _schema_relevant_tables(schema),
            "relation_foreign_scheme": (
                schema.get("relation_foreign_scheme")
                if isinstance(schema.get("relation_foreign_scheme"), dict)
                else {}
            ),
            "domain_context_summary": (
                schema.get("domain_context_summary")
                if isinstance(schema.get("domain_context_summary"), dict)
                else {}
            ),
            "schema_cache_warnings": warnings,
            "original_prompt": prompt,
            "warnings": warnings,
            "error": "",
        }


    def discover_schema(
        self,
        resolved: _ResolvedProfile,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Discover PostgreSQL schema metadata with a session-scoped cache."""

        cache = _schema_cache_from_context(self.context)
        cache_key = resolved.normalized_key
        if not refresh and isinstance(cache.get(cache_key), dict):
            return {
                "status": "success",
                "schema": cache[cache_key],
                "warnings": ["schema_cache_hit"],
            }
        if not refresh:
            stored_schema = _stored_schema_payload_from_profile(resolved)
            if stored_schema is not None:
                cache[cache_key] = stored_schema
                return {
                    "status": "success",
                    "schema": stored_schema,
                    "warnings": ["stored_schema"],
                }

        schemas_result = self._run_psql(
            resolved,
            _SQL_SCHEMAS_QUERY,
            operation="schema_discovery",
        )
        if schemas_result.get("status") != "success":
            return schemas_result
        columns_result = self._run_psql(
            resolved,
            _SQL_COLUMNS_QUERY,
            operation="schema_discovery",
        )
        if columns_result.get("status") != "success":
            return columns_result
        primary_keys_result = self._run_psql(
            resolved,
            _SQL_PRIMARY_KEYS_QUERY,
            operation="schema_discovery",
        )
        if primary_keys_result.get("status") != "success":
            return primary_keys_result
        foreign_keys_result = self._run_psql(
            resolved,
            _SQL_FOREIGN_KEYS_QUERY,
            operation="schema_discovery",
        )
        if foreign_keys_result.get("status") != "success":
            return foreign_keys_result
        indexes_result = self._run_psql(
            resolved,
            _SQL_INDEXES_QUERY,
            operation="schema_discovery",
        )
        if indexes_result.get("status") != "success":
            return indexes_result
        stats_result = self._run_psql(
            resolved,
            _SQL_TABLE_STATS_QUERY,
            operation="schema_discovery",
        )
        if stats_result.get("status") != "success":
            return stats_result
        schema_columns, schema_rows = _parse_csv(str(schemas_result.get("stdout") or ""))
        _columns, column_rows = _parse_csv(str(columns_result.get("stdout") or ""))
        _pk_columns, primary_key_rows = _parse_csv(str(primary_keys_result.get("stdout") or ""))
        _fk_columns, foreign_key_rows = _parse_csv(str(foreign_keys_result.get("stdout") or ""))
        _index_columns, index_rows = _parse_csv(str(indexes_result.get("stdout") or ""))
        _stats_columns, stats_rows = _parse_csv(str(stats_result.get("stdout") or ""))
        schemas = [
            str(row.get("schema_name") or "").strip()
            for row in schema_rows
            if str(row.get("schema_name") or "").strip()
        ]
        schema = _build_schema_payload_from_discovery_rows(
            parameter_key=resolved.key,
            schemas=schemas,
            schema_columns=schema_columns,
            column_rows=column_rows,
            primary_key_rows=primary_key_rows,
            foreign_key_rows=foreign_key_rows,
            index_rows=index_rows,
            stats_rows=stats_rows,
        )
        schema["context_json"] = (
            dict(resolved.record.context_json)
            if isinstance(resolved.record.context_json, dict)
            else {}
        )
        schema["domain_context_summary"] = parameter_context_summary(schema["context_json"])
        cache[cache_key] = schema
        return {"status": "success", "schema": schema, "warnings": []}


__all__ = []
