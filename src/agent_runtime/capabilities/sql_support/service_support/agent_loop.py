"""SQL agent public run entrypoint and LLM loop."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.service_support.common import *

class SqlAgentLoopMixin:
    def run(
        self,
        *,
        prompt: str = "",
        parameter_key: str = "",
        sql: str = "",
        operation: str = "query",
        limit: int | None = None,
        refresh_schema: bool = False,
        selected_table: str = "",
    ) -> dict[str, Any]:
        """Run one SQL-agent operation and return an API-safe payload."""

        if not bool(getattr(self.config, "sql_agent_enabled", True)):
            return self._error("SQL agent is disabled by runtime configuration.")
        profile_result = self._resolve_profile(prompt=prompt or sql, parameter_key=parameter_key)
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
        operation = str(operation or "query").strip().lower() or "query"
        safe_limit = _safe_limit(limit, self.config)
        if operation == "query" and not sql.strip() and _DISCOVERY_RE.search(prompt):
            operation = "discover"
        if operation == "discover":
            schema = self.discover_schema(resolved, refresh=refresh_schema)
            if schema.get("status") != "success":
                return schema
            columns, rows = _discovery_rows_for_prompt(
                prompt,
                schema["schema"],
                limit=int(getattr(self.config, "sql_agent_max_rows", 1000) or 1000),
            )
            return {
                "status": "success",
                "parameter_key": resolved.key,
                "engine": engine,
                "summary": self._summarize_discovery(
                    schema["schema"],
                    prompt=prompt,
                    columns=columns,
                    rows=rows,
                ),
                "schema": schema["schema"],
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": False,
                "warnings": list(schema.get("warnings") or []),
                "sql": "",
                "error": "",
            }
        schema_result = self.discover_schema(resolved, refresh=refresh_schema)
        if schema_result.get("status") != "success":
            return {
                **schema_result,
                "parameter_key": resolved.key,
                "engine": "postgresql",
                "sql": sql,
            }
        schema = schema_result.get("schema") or {}
        _emit_sql_agent_event(
            self.context,
            event_type="sql.context.loaded",
            title="SQL context loaded",
            summary="SQL schema context was loaded for the SQL LLM loop.",
            details=_schema_trace_details(
                parameter_key=resolved.key,
                schema=schema if isinstance(schema, dict) else {},
                warnings=list(schema_result.get("warnings") or []),
            ),
        )
        loop_prompt = str(prompt or "").strip()
        if sql.strip() or operation == "execute_readonly":
            supplied_sql = str(sql or "").strip()
            loop_prompt = "\n".join(
                part
                for part in [
                    loop_prompt or "Evaluate this user-provided SQL request.",
                    "User-provided SQL text to evaluate through the SQL LLM loop:",
                    supplied_sql,
                ]
                if part
            )
            operation = "query"
        return self._run_llm_sql_loop(
            loop_prompt,
            prompt=prompt,
            resolved=resolved,
            schema=schema if isinstance(schema, dict) else {},
            limit=safe_limit,
            operation=operation,
        )


    def _run_llm_sql_loop(
        self,
        user_request: str,
        *,
        prompt: str,
        resolved: _ResolvedProfile,
        schema: dict[str, Any],
        limit: int,
        operation: str = "query",
    ) -> dict[str, Any]:
        configured_repair_attempts = getattr(self.config, "sql_agent_max_repair_attempts", 2)
        if configured_repair_attempts is None:
            configured_repair_attempts = 2
        repair_attempts = max(0, int(configured_repair_attempts))
        attempts: list[dict[str, Any]] = []
        warnings: list[str] = []
        confirmed_action = (
            self.context.get("sql_confirmed_action")
            if isinstance(self.context.get("sql_confirmed_action"), dict)
            else None
        )
        confirmation_granted = bool(self.context.get("confirmation", False))

        if confirmation_granted and confirmed_action is not None:
            generated_sql = str(
                confirmed_action.get("generated_sql")
                or confirmed_action.get("sql")
                or confirmed_action.get("executed_sql")
                or ""
            ).strip()
            action = SqlAgentAction(
                action="execute_sql",
                sql=str(confirmed_action.get("executed_sql") or generated_sql),
                sql_steps=[
                    str(confirmed_action.get("executed_sql") or generated_sql),
                ],
                result_strategy=str(confirmed_action.get("result_strategy") or "final_step")
                if str(confirmed_action.get("result_strategy") or "final_step")
                in {"final_step", "append_rows"}
                else "final_step",
                summary=str(confirmed_action.get("summary") or ""),
                assumptions=[
                    str(item)
                    for item in (
                        confirmed_action.get("assumptions")
                        if isinstance(confirmed_action.get("assumptions"), list)
                        else []
                    )
                ],
                confidence=1.0,
            )
            max_actions = 1
        else:
            generated_sql = ""
            action = None
            max_actions = repair_attempts + 3

        for attempt_index in range(max_actions):
            if action is None:
                try:
                    action = self._next_sql_action(
                        user_request=user_request,
                        resolved=resolved,
                        schema=schema,
                        limit=limit,
                        attempts=attempts,
                        warnings=warnings,
                    )
                except Exception as exc:
                    return self._error(
                        f"SQL query generation failed: {exc}",
                        parameter_key=resolved.key,
                        warnings=warnings,
                    )
            step_sqls = [
                str(item).strip()
                for item in list(action.sql_steps or [])
                if str(item).strip()
            ]
            if not step_sqls and str(action.sql or "").strip():
                step_sqls = [str(action.sql or "").strip()]
            generated_sql = "\n\n".join(step_sqls)
            result_strategy = (
                action.result_strategy
                if action.result_strategy in {"final_step", "append_rows"}
                else "final_step"
            )

            attempt: dict[str, Any] = {
                "attempt": attempt_index + 1,
                "action": action.action,
                "generated_sql": generated_sql,
                "executed_sql": generated_sql,
                "result_strategy": result_strategy,
                "assumptions": list(action.assumptions),
                "confidence": float(action.confidence or 0.0),
            }

            if action.action == "ask_clarification":
                question = str(
                    action.question
                    or action.summary
                    or "What should I clarify before querying the database?"
                ).strip()
                clarification_mode = normalize_agent_clarification_mode(
                    self.context.get("agent_clarification_mode")
                    or getattr(self.config, "agent_clarification_mode", ""),
                )
                _emit_sql_agent_event(
                    self.context,
                    event_type="clarification.resolution.started",
                    title="Clarification resolution started",
                    summary="The SQL agent asked for clarification; the typed resolver is deciding whether to proceed.",
                    details={
                        "parameter_key": resolved.key,
                        "mode": clarification_mode,
                        "question": question,
                        "missing_information": "SQL query clarification",
                    },
                )
                resolution = resolve_agent_clarification(
                    llm_client=self.llm_client,
                    mode=clarification_mode,
                    user_prompt=user_request,
                    proposed_question=question,
                    missing_information="SQL query clarification",
                    reason=str(action.summary or ""),
                    candidates=_sql_clarification_candidates(schema, user_request),
                    context={
                        "parameter_key": resolved.key,
                        "schema_tables": [
                            _qualified_table_name(
                                str(table.get("schema") or ""),
                                str(table.get("table") or ""),
                            )
                            for table in list(schema.get("tables") or [])[:200]
                            if isinstance(table, dict)
                        ],
                        "parameter_context": (
                            schema.get("domain_context_summary")
                            if isinstance(schema.get("domain_context_summary"), dict)
                            else {}
                        ),
                        "prior_attempts": attempts[-3:],
                    },
                    risk_flags=[],
                )
                if resolution.decision == "continue_with_assumption":
                    resolution_payload = resolution.model_dump(mode="json")
                    attempt["clarification_resolution"] = resolution_payload
                    attempts.append(attempt)
                    warnings.append("sql_clarification_auto_resolved")
                    self.context.setdefault("agent_clarification_resolutions", []).append(
                        resolution_payload
                    )
                    self.context.setdefault("agent_clarification_assumptions", []).extend(
                        list(resolution.assumptions)
                    )
                    self.context.setdefault("agent_clarification_selected_entities", []).extend(
                        [
                            entity.model_dump(mode="json")
                            for entity in resolution.selected_entities
                        ]
                    )
                    self.context.setdefault(
                        "agent_clarification_execution_directives",
                        [],
                    ).extend(list(resolution.execution_directives))
                    _emit_sql_agent_event(
                        self.context,
                        event_type="clarification.auto_resolved",
                        title="Clarification auto-resolved",
                        summary="The typed resolver supplied SQL assumptions and the SQL agent will retry generation.",
                        details={
                            "parameter_key": resolved.key,
                            **resolution_payload,
                        },
                    )
                    action = None
                    continue
                if resolution.user_question.strip():
                    question = resolution.user_question.strip()
                _emit_sql_agent_event(
                    self.context,
                    event_type="clarification.resolution.ask_user",
                    title="Clarification resolution asks user",
                    summary="The typed resolver kept the SQL clarification user-facing.",
                    details={
                        "parameter_key": resolved.key,
                        **resolution.model_dump(mode="json"),
                    },
                )
                return {
                    "status": "clarification_required",
                    "parameter_key": resolved.key,
                    "engine": "postgresql",
                    "sql": "",
                    "generated_sql": "",
                    "executed_sql": "",
                    "attempts": [*attempts, attempt],
                    "summary": question,
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "truncated": False,
                    "schema": {},
                    "warnings": warnings,
                    "error": "sql_llm_clarification_required",
                    "clarification_request": {
                        "question": question,
                        "reason": str(action.summary or ""),
                        "missing_information": "SQL query clarification",
                        "options": [],
                        "input_kind": "unknown",
                        "parameter_choices": [],
                        "secret_input": False,
                        "allow_freeform": True,
                        "confidence": float(action.confidence or 0.0),
                    },
                    "sql_pending_state": {
                        "original_prompt": prompt or user_request,
                        "parameter_key": resolved.key,
                        "schema_cache_key": resolved.normalized_key,
                        "attempts": [*attempts, attempt],
                    },
                }
            if action.action == "finish":
                summary = str(
                    action.summary or "SQL agent completed without executing SQL."
                ).strip()
                return {
                    "status": "success",
                    "parameter_key": resolved.key,
                    "engine": "postgresql",
                    "sql": "",
                    "generated_sql": "",
                    "executed_sql": "",
                    "attempts": [*attempts, attempt],
                    "summary": summary,
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "truncated": False,
                    "schema": {},
                    "warnings": warnings,
                    "error": "",
                }
            if action.action == "abort":
                return {
                    **self._error(
                        str(action.summary or action.question or "The SQL LLM aborted the query."),
                        parameter_key=resolved.key,
                        sql=generated_sql,
                        warnings=warnings,
                    ),
                    "generated_sql": generated_sql,
                    "executed_sql": str(action.sql or "").strip(),
                    "attempts": [*attempts, attempt],
                }

            if not step_sqls:
                attempt["validation_error"] = "SQL LLM returned execute_sql without SQL."
                attempts.append(attempt)
                warnings.append("llm_sql_retry_after_validation")
                action = None
                continue

            final_columns: list[str] = []
            final_rows: list[dict[str, Any]] = []
            final_executed_sql = ""
            executed_step_sqls: list[str] = []
            final_safety_classification: dict[str, Any] = {}
            step_failed = False
            for step_index, step_sql in enumerate(step_sqls, start=1):
                step_attempt = {
                    **attempt,
                    "step": step_index,
                    "step_count": len(step_sqls),
                    "generated_sql": step_sql,
                    "executed_sql": step_sql,
                }
                _emit_sql_agent_event(
                    self.context,
                    event_type="sql.llm.generated",
                    title="SQL generated",
                    summary="The SQL LLM generated a SQL statement for validation.",
                    details={
                        "parameter_key": resolved.key,
                        "generated_sql": step_sql,
                        "executed_sql": step_sql,
                        "attempt": attempt_index + 1,
                        "step": step_index,
                        "step_count": len(step_sqls),
                    },
                )
                validation = self._classify_sql_for_execution(
                    step_sql,
                    schema=schema,
                    limit=limit,
                )
                safety_classification = (
                    validation.get("safety_classification")
                    if isinstance(validation.get("safety_classification"), dict)
                    else {}
                )
                join_warnings = (
                    safety_classification.get("join_relationship_warnings")
                    if isinstance(
                        safety_classification.get("join_relationship_warnings"),
                        list,
                    )
                    else []
                )
                if join_warnings:
                    if "sql_join_relationships_not_db_approved" not in warnings:
                        warnings.append("sql_join_relationships_not_db_approved")
                    _emit_sql_agent_event(
                        self.context,
                        event_type="sql.validation.join_warning",
                        title="SQL join relationship warning",
                        summary=(
                            "Read-only SQL uses semantic or inferred joins that are not "
                            "database-declared foreign keys."
                        ),
                        details={
                            "parameter_key": resolved.key,
                            "generated_sql": step_sql,
                            "join_relationship_warnings": join_warnings,
                            "attempt": attempt_index + 1,
                            "step": step_index,
                            "step_count": len(step_sqls),
                        },
                        level="warning",
                    )
                step_attempt["safety_classification"] = safety_classification
                if (
                    validation.get("status") == "confirmation_required"
                    and not confirmation_granted
                    and _uses_temp_table_materialization(step_sql)
                    and not _prompt_explicitly_requests_temp_table(prompt or user_request)
                ):
                    executed_sql = str(validation.get("executed_sql") or step_sql).strip()
                    error = (
                        "cte_preferred_over_temp_table_materialization: this analytical "
                        "answer can be expressed as read-only SQL. Rewrite temporary "
                        "table or SELECT INTO TEMP materialization as a WITH CTE, derived "
                        "table, or inline VALUES/generate_series SELECT unless the user "
                        "explicitly asks to create a temporary table."
                    )
                    step_attempt["executed_sql"] = executed_sql
                    step_attempt["validation_error"] = error
                    attempts.append(step_attempt)
                    warnings.append("llm_sql_retry_after_validation")
                    _emit_sql_agent_event(
                        self.context,
                        event_type="sql.validation.rejected",
                        title="SQL temp table materialization rejected",
                        summary=(
                            "The runtime requested a CTE rewrite instead of asking for "
                            "confirmation on a temporary intermediate table."
                        ),
                        details={
                            "parameter_key": resolved.key,
                            "generated_sql": step_sql,
                            "executed_sql": executed_sql,
                            "error": error,
                            "safety_classification": safety_classification,
                            "attempt": attempt_index + 1,
                            "step": step_index,
                            "step_count": len(step_sqls),
                        },
                        level="warning",
                    )
                    action = None
                    step_failed = True
                    break
                if validation.get("status") == "confirmation_required" and confirmation_granted:
                    validation = {
                        **validation,
                        "status": "success",
                        "error": "",
                    }
                if validation.get("status") == "confirmation_required":
                    executed_sql = str(validation.get("executed_sql") or step_sql).strip()
                    step_attempt["executed_sql"] = executed_sql
                    attempts.append(step_attempt)
                    pending = {
                        "prompt": prompt or user_request,
                        "parameter_key": resolved.key,
                        "engine": "postgresql",
                        "operation": operation or "query",
                        "generated_sql": step_sql,
                        "executed_sql": executed_sql,
                        "sql": executed_sql,
                        "assumptions": list(action.assumptions),
                        "summary": str(action.summary or ""),
                        "safety_classification": safety_classification,
                        "attempts": attempts,
                    }
                    action_payload = self._sql_confirmation_action(
                        prompt=prompt or user_request,
                        resolved=resolved,
                        generated_sql=step_sql,
                        executed_sql=executed_sql,
                        safety_classification=safety_classification,
                        operation=operation,
                    )
                    _emit_sql_agent_event(
                        self.context,
                        event_type="sql.confirmation.required",
                        title="SQL confirmation required",
                        summary="The SQL LLM proposed SQL that requires explicit confirmation.",
                        details={
                            "parameter_key": resolved.key,
                            "generated_sql": step_sql,
                            "executed_sql": executed_sql,
                            "safety_classification": safety_classification,
                            "step": step_index,
                            "step_count": len(step_sqls),
                        },
                        level="warning",
                    )
                    return {
                        "status": "confirmation_required",
                        "parameter_key": resolved.key,
                        "engine": "postgresql",
                        "sql": executed_sql,
                        "generated_sql": step_sql,
                        "executed_sql": executed_sql,
                        "attempts": attempts,
                        "safety_classification": safety_classification,
                        "confirmation_required": True,
                        "confirmation_actions": [action_payload],
                        "sql_pending_confirmation": pending,
                        "summary": "SQL execution requires explicit confirmation.",
                        "columns": [],
                        "rows": [],
                        "row_count": 0,
                        "truncated": False,
                        "schema": {},
                        "warnings": warnings,
                        "error": "",
                    }
                if validation.get("status") != "success":
                    error = str(validation.get("error") or "SQL validation failed.")
                    rejected_executed_sql = str(
                        validation.get("executed_sql")
                        or validation.get("sql")
                        or step_sql
                    ).strip()
                    step_attempt["executed_sql"] = rejected_executed_sql
                    step_attempt["validation_error"] = error
                    attempts.append(step_attempt)
                    warnings.append("llm_sql_retry_after_validation")
                    _emit_sql_agent_event(
                        self.context,
                        event_type="sql.validation.rejected",
                        title="SQL rejected",
                        summary="The runtime rejected generated SQL during safety validation.",
                        details={
                            "parameter_key": resolved.key,
                            "generated_sql": step_sql,
                            "executed_sql": rejected_executed_sql,
                            "error": error,
                            "safety_classification": safety_classification,
                            "attempt": attempt_index + 1,
                            "step": step_index,
                            "step_count": len(step_sqls),
                        },
                        level="warning",
                    )
                    action = None
                    step_failed = True
                    break

                executed_sql = str(
                    validation.get("executed_sql") or validation.get("sql") or step_sql
                ).strip()
                step_attempt["executed_sql"] = executed_sql
                executed = self._run_psql(
                    resolved,
                    executed_sql,
                    operation=operation,
                    generated_sql=step_sql,
                    executed_sql=executed_sql,
                )
                if executed.get("status") != "success":
                    error = str(executed.get("error") or "Database execution failed.")
                    step_attempt["db_error"] = error
                    step_attempt["stdout_sample"] = str(executed.get("stdout") or "")[:1000]
                    step_attempt["stderr_sample"] = str(executed.get("stderr") or "")[:1000]
                    attempts.append(step_attempt)
                    warnings.append("llm_sql_retry_after_execution_error")
                    action = None
                    step_failed = True
                    break

                step_columns, step_rows = _parse_csv(str(executed.get("stdout") or ""))
                if result_strategy == "append_rows":
                    if not final_columns:
                        final_columns = step_columns
                    elif step_columns != final_columns:
                        error = (
                            "incompatible_multi_step_result_columns: expected "
                            f"{', '.join(final_columns) or '<none>'}; got "
                            f"{', '.join(step_columns) or '<none>'}"
                        )
                        step_attempt["validation_error"] = error
                        step_attempt["expected_columns"] = list(final_columns)
                        step_attempt["actual_columns"] = list(step_columns)
                        attempts.append(step_attempt)
                        warnings.append("llm_sql_retry_after_validation")
                        _emit_sql_agent_event(
                            self.context,
                            event_type="sql.validation.rejected",
                            title="SQL result shape rejected",
                            summary=(
                                "The runtime rejected decomposed SQL steps because "
                                "their result columns were incompatible."
                            ),
                            details={
                                "parameter_key": resolved.key,
                                "generated_sql": step_sql,
                                "executed_sql": executed_sql,
                                "error": error,
                                "attempt": attempt_index + 1,
                                "step": step_index,
                                "step_count": len(step_sqls),
                                "result_strategy": result_strategy,
                            },
                            level="warning",
                        )
                        action = None
                        step_failed = True
                        break
                    final_rows.extend(step_rows)
                else:
                    final_columns, final_rows = step_columns, step_rows
                final_executed_sql = executed_sql
                executed_step_sqls.append(executed_sql)
                final_safety_classification = safety_classification
                attempts.append(step_attempt)
            if step_failed:
                continue

            max_rows = int(getattr(self.config, "sql_agent_max_rows", 1000) or 1000)
            truncated = len(final_rows) > max_rows
            rows = final_rows[:max_rows]
            summary_sql = (
                "\n\n".join(executed_step_sqls)
                if result_strategy == "append_rows"
                else final_executed_sql
            )
            result_contract_review: dict[str, Any] = {}
            if (
                str(final_safety_classification.get("classification") or "") == "read_only"
                and _should_review_sql_result_contract(
                    prompt=prompt or user_request,
                    sql=summary_sql,
                    result_strategy=result_strategy,
                )
            ):
                result_contract_review = self._review_sql_result_contract(
                    prompt=prompt or user_request,
                    generated_sql=generated_sql,
                    executed_sql=summary_sql,
                    result_strategy=result_strategy,
                    columns=final_columns,
                    rows=final_rows,
                    truncated=truncated,
                )
                _emit_sql_agent_event(
                    self.context,
                    event_type="sql.result_contract.review",
                    title="SQL result contract reviewed",
                    summary="The typed reviewer checked whether the SQL result shape answers the request.",
                    details={
                        "parameter_key": resolved.key,
                        "attempt": attempt_index + 1,
                        "decision": result_contract_review.get("decision"),
                        "confidence": result_contract_review.get("confidence"),
                        "request_intent": result_contract_review.get("request_intent"),
                        "expected_result_shape": result_contract_review.get(
                            "expected_result_shape"
                        ),
                        "observed_result_shape": result_contract_review.get(
                            "observed_result_shape"
                        ),
                        "missing_requirements": result_contract_review.get(
                            "missing_requirements"
                        ),
                        "reason": result_contract_review.get("reason"),
                        "source": result_contract_review.get("source"),
                    },
                    debug_only=True,
                )
            result_shape_error = str(result_contract_review.get("error") or "").strip()
            if result_shape_error:
                if attempts:
                    attempts[-1]["validation_error"] = result_shape_error
                    attempts[-1]["result_contract_review"] = dict(result_contract_review)
                    attempts[-1]["result_columns"] = list(final_columns)
                    attempts[-1]["result_row_count"] = len(final_rows)
                warnings.append("llm_sql_retry_after_result_shape")
                _emit_sql_agent_event(
                    self.context,
                    event_type="sql.validation.rejected",
                    title="SQL result shape rejected",
                    summary="The SQL result did not satisfy the requested answer shape.",
                    details={
                        "parameter_key": resolved.key,
                        "generated_sql": generated_sql,
                        "executed_sql": summary_sql,
                        "error": result_shape_error,
                        "attempt": attempt_index + 1,
                        "result_columns": list(final_columns),
                        "result_row_count": len(final_rows),
                    },
                    level="warning",
                )
                action = None
                continue
            summary = self._summarize_result(
                prompt=prompt or user_request,
                sql=summary_sql,
                columns=final_columns,
                rows=rows,
                assumptions=list(action.assumptions),
            )
            return {
                "status": "success",
                "parameter_key": resolved.key,
                "engine": "postgresql",
                "sql": summary_sql,
                "generated_sql": generated_sql,
                "executed_sql": "\n\n".join(executed_step_sqls),
                "attempts": attempts,
                "safety_classification": final_safety_classification,
                "confirmation_required": False,
                "summary": summary,
                "columns": final_columns,
                "rows": rows,
                "row_count": len(final_rows),
                "truncated": truncated,
                "schema": {},
                "warnings": warnings,
                "error": "",
            }
        last = attempts[-1] if attempts else {}
        return {
            **self._error(
                str(
                    last.get("db_error")
                    or last.get("validation_error")
                    or "SQL LLM did not produce executable SQL."
                ),
                parameter_key=resolved.key,
                sql=str(last.get("executed_sql") or last.get("generated_sql") or ""),
                warnings=warnings,
            ),
            "generated_sql": str(last.get("generated_sql") or ""),
            "executed_sql": str(last.get("executed_sql") or ""),
            "attempts": attempts,
            "safety_classification": last.get("safety_classification") or {},
        }


__all__ = []
