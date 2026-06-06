"""SQL agent action prompt and typed action helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.service_support.common import *

class SqlAgentPromptingMixin:
    def _build_sql_action_prompt(
        self,
        *,
        user_request: str,
        resolved: _ResolvedProfile,
        schema: dict[str, Any],
        limit: int,
        attempts: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        hints = _memory_hints(
            self.memory_store,
            prompt=user_request,
            profile_key=resolved.normalized_key,
        )
        settings = {
            "default_limit": int(getattr(self.config, "sql_agent_default_limit", 100) or 100),
            "max_rows": int(getattr(self.config, "sql_agent_max_rows", 1000) or 1000),
            "safe_limit_for_this_request": int(limit),
            "mutation_policy": "explicit_confirmation_required",
            "agent_clarification_mode": normalize_agent_clarification_mode(
                self.context.get("agent_clarification_mode")
                or getattr(self.config, "agent_clarification_mode", ""),
            ),
            "runtime_will_rewrite_sql": False,
            "runtime_will_quote_schema_identifiers_before_execution": True,
            "runtime_will_repair_sql": False,
            "runtime_will_choose_tables_or_columns": False,
        }
        relation_foreign_scheme = (
            schema.get("relation_foreign_scheme")
            if isinstance(schema.get("relation_foreign_scheme"), dict)
            else {}
        )
        domain_context = (
            schema.get("domain_context_summary")
            if isinstance(schema.get("domain_context_summary"), dict)
            else parameter_context_summary(schema.get("context_json") if isinstance(schema.get("context_json"), dict) else {})
        )
        return "\n".join(
            [
                *__prompt_lines__(
                    "sql.agent_action",
                    {
                        "limit": int(limit),
                        "agent_clarification_mode": settings["agent_clarification_mode"],
                    },
                ),
                f"Original user request: {user_request}",
                f"Database profile key: {resolved.key}",
                "Use only database-declared approved foreign-key joins as approved joins. "
                "Autodetected inferred secondary keys and parameter context guidance are "
                "prompt-only guidance, not validation approval.",
                "Runtime settings:",
                json.dumps(settings, ensure_ascii=True, sort_keys=True),
                "Complete discovered schema, all tables and columns:",
                _complete_schema_summary(schema),
                "Autodetected relation_foreign_scheme:",
                _bounded_json(relation_foreign_scheme, max_chars=12000),
                "Parameter context guidance (bounded, prompt-only, not validation approval):",
                _bounded_json(domain_context, max_chars=12000),
                "Semantic memory hints:",
                json.dumps(hints, ensure_ascii=True),
                "Prior attempts, validation errors, database stderr/stdout samples, and warnings:",
                _bounded_json(
                    {
                        "attempts": attempts[-6:],
                        "warnings": warnings,
                        "typed_clarification_assumptions": list(
                            self.context.get("agent_clarification_assumptions") or []
                        )[-12:],
                        "typed_clarification_selected_entities": list(
                            self.context.get("agent_clarification_selected_entities") or []
                        )[-12:],
                        "typed_clarification_execution_directives": list(
                            self.context.get("agent_clarification_execution_directives") or []
                        )[-12:],
                    },
                    max_chars=12000,
                ),
            ]
        )


    def _next_sql_action(
        self,
        *,
        user_request: str,
        resolved: _ResolvedProfile,
        schema: dict[str, Any],
        limit: int,
        attempts: list[dict[str, Any]],
        warnings: list[str],
    ) -> SqlAgentAction:
        complete_json = getattr(self.llm_client, "complete_json", None)
        if not callable(complete_json):
            raise RuntimeError("SQL query generation requires an LLM client.")
        action_prompt = self._build_sql_action_prompt(
            user_request=user_request,
            resolved=resolved,
            schema=schema,
            limit=limit,
            attempts=attempts,
            warnings=warnings,
        )
        domain_context = (
            schema.get("domain_context_summary")
            if isinstance(schema.get("domain_context_summary"), dict)
            else {}
        )
        _emit_sql_agent_event(
            self.context,
            event_type="sql.llm.prompt",
            title="SQL LLM prompt prepared",
            summary=(
                "The SQL LLM prompt was prepared with schema, relation metadata, "
                "and bounded parameter context guidance."
            ),
            details={
                **_schema_trace_details(
                    parameter_key=resolved.key,
                    schema=schema,
                    warnings=warnings,
                ),
                "prompt_contains_domain_context": bool(domain_context),
                "prompt_length": len(action_prompt),
                "prompt": action_prompt[:60000],
                "prompt_truncated": len(action_prompt) > 60000,
            },
            debug_only=True,
        )
        raw = complete_json(action_prompt, SqlAgentAction.model_json_schema())
        return _coerce_sql_agent_action(raw)


__all__ = []
