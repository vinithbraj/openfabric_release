"""SQL agentic orchestration helpers."""

from __future__ import annotations

from .common import *
from .formatting import *


class _SqlAgenticMixin:
    """SQL agentic orchestration helpers."""

    def _maybe_handle_sql_agent_request(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        llm_client,
        trace: PlanningTrace,
    ) -> str | None:
        """Prepare SQL context for the typed DAG path, or run legacy direct SQL mode."""

        config = self._runtime_config_for_context(request_context)
        if not bool(getattr(config, "sql_agent_enabled", True)):
            return None
        matches = user_request.session_context.get(PARAMETER_CONTEXT_KEY)
        has_db_profile = isinstance(matches, list) and any(
            isinstance(item, dict)
            and isinstance(item.get("database_profile"), dict)
            and item["database_profile"].get("profile_type") == "database_connection"
            for item in matches
        )
        pending_sql = request_context.get(SQL_PENDING_CONTEXT_KEY)
        if not isinstance(pending_sql, dict):
            pending_sql = {}
        if not pending_sql and not looks_like_sql_intent(
            user_request.raw_prompt,
            has_db_profile=has_db_profile,
        ):
            return None
        route_mode = (
            str(getattr(config, "sql_agent_chat_route_mode", "agentic") or "agentic")
            .strip()
            .lower()
        )
        if route_mode not in {"agentic", "direct"}:
            route_mode = "agentic"

        prompt = user_request.raw_prompt
        parameter_key = ""
        selected_table = ""
        if pending_sql:
            latest_clarification = {}
            clarifications = request_context.get("clarifications")
            if isinstance(clarifications, list) and clarifications:
                latest = clarifications[-1]
                latest_clarification = latest if isinstance(latest, dict) else {}
            selected_option_id = str(
                latest_clarification.get("selected_option_id") or ""
            ).strip()
            if selected_option_id.startswith("sql_table:"):
                selected_table = selected_option_id[len("sql_table:") :]
            if selected_option_id.startswith("sql_param:"):
                parameter_key = selected_option_id[len("sql_param:") :]
            answer = str(latest_clarification.get("answer") or "").strip()
            if not selected_table and answer.startswith("sql_table:"):
                selected_table = answer[len("sql_table:") :]
            original_prompt = str(pending_sql.get("original_prompt") or prompt).strip()
            if answer:
                prompt = f"{original_prompt}\nUser clarification: {answer}"
            else:
                prompt = original_prompt
            parameter_key = parameter_key or str(pending_sql.get("parameter_key") or "").strip()
            if not parameter_key and answer:
                parameter_key = answer

        sql_context = {
            **request_context,
            **user_request.session_context,
            "raw_prompt": prompt,
            "gateway_client": self.execution_engine.gateway_client,
        }
        service = self._sql_agent_service(llm_client=llm_client, context=sql_context)
        if route_mode == "direct":
            payload = service.run(
                prompt=prompt,
                parameter_key=parameter_key,
                operation="query",
                selected_table=selected_table,
            )
        else:
            payload = service.prepare_agentic_context(
                prompt=prompt,
                parameter_key=parameter_key,
                selected_table=selected_table,
            )
        if (
            payload.get("status") == "error"
            and not has_db_profile
            and not pending_sql
            and "No matching database connection parameter" in str(payload.get("error") or "")
        ):
            return None
        schema_cache = service.context.get(SQL_SCHEMA_CACHE_CONTEXT_KEY)
        if isinstance(schema_cache, dict):
            sql_context[SQL_SCHEMA_CACHE_CONTEXT_KEY] = schema_cache
            user_request.session_context[SQL_SCHEMA_CACHE_CONTEXT_KEY] = schema_cache
            request_context[SQL_SCHEMA_CACHE_CONTEXT_KEY] = schema_cache
        trace.metadata["sql_agent"] = {
            "route_mode": route_mode,
            "status": payload.get("status"),
            "parameter_key": payload.get("parameter_key"),
            "engine": payload.get("engine"),
            "row_count": payload.get("row_count"),
            "warnings": payload.get("warnings") or [],
        }
        if payload.get("status") == "clarification_required" and isinstance(
            payload.get("clarification_request"),
            dict,
        ):
            return self._record_sql_clarification(
                user_request=user_request,
                request_context=request_context,
                payload=payload,
                trace=trace,
                phase="sql_agent",
            )
        if payload.get("status") == "confirmation_required":
            actions = (
                list(payload.get("confirmation_actions"))
                if isinstance(payload.get("confirmation_actions"), list)
                else []
            )
            pending = (
                dict(payload.get("sql_pending_confirmation"))
                if isinstance(payload.get("sql_pending_confirmation"), dict)
                else {}
            )
            trace.metadata["operator_confirmation_actions"] = actions
            trace.metadata["sql_pending_confirmation"] = pending
            trace.metadata["sql_agent"]["confirmation_required"] = True
            self._record_last_failure(
                request_id=user_request.request_id,
                prompt=user_request.raw_prompt,
                category="confirmation_required",
                stage="sql_agent",
                reason="The SQL agent needs confirmation before executing generated SQL.",
                metadata={"confirmation_actions": actions},
            )
            return render_sql_agent_response(payload)
        if payload.get("status") == "error" and route_mode == "agentic":
            if not has_db_profile and not pending_sql:
                return None
            return render_sql_agent_response(payload)
        if route_mode == "agentic":
            safe_context = {
                key: value
                for key, value in payload.items()
                if key
                in {
                    "status",
                    "parameter_key",
                    "engine",
                    "schema_summary",
                    "relevant_tables",
                    "relation_foreign_scheme",
                    "domain_context_summary",
                    "schema_cache_warnings",
                    "original_prompt",
                    "warnings",
                }
            }
            safe_context.setdefault("original_prompt", prompt)
            safe_context.setdefault("engine", payload.get("engine") or "postgresql")
            safe_context.setdefault("schema_summary", "")
            safe_context.setdefault("relevant_tables", [])
            safe_context.setdefault("relation_foreign_scheme", {})
            safe_context.setdefault("domain_context_summary", {})
            user_request.session_context[SQL_AGENTIC_CONTEXT_KEY] = safe_context
            request_context[SQL_AGENTIC_CONTEXT_KEY] = safe_context
            if payload.get("status") == "success":
                user_request.session_context.pop(SQL_PENDING_CONTEXT_KEY, None)
                request_context.pop(SQL_PENDING_CONTEXT_KEY, None)
            trace.metadata["sql_agent"]["agentic_context_attached"] = True
            return None
        return render_sql_agent_response(payload)

    @staticmethod
    def _sql_agentic_context_present(request_context: dict[str, Any]) -> bool:
        """Return whether a safe SQL context was prepared for this request."""

        return isinstance(request_context.get(SQL_AGENTIC_CONTEXT_KEY), dict)

    @staticmethod
    def _apply_sql_agentic_classification(
        classification: Any,
        request_context: dict[str, Any],
    ) -> Any:
        """Force prepared SQL chat onto the typed SQL DAG path."""

        if not _SqlAgenticMixin._sql_agentic_context_present(request_context):
            return classification
        likely_domains = []
        for domain in [*list(getattr(classification, "likely_domains", []) or []), "sql"]:
            normalized = str(domain or "").strip()
            if normalized and normalized not in likely_domains:
                likely_domains.append(normalized)
        updates = {
            "requires_tools": True,
            "likely_domains": likely_domains,
            "needs_clarification": False,
            "clarification_question": None,
        }
        if str(getattr(classification, "prompt_type", "") or "") in {"simple_question", "ambiguous"}:
            updates["prompt_type"] = "simple_tool_task"
        if hasattr(classification, "model_copy"):
            return classification.model_copy(update=updates)
        return classification

    @staticmethod
    def _sql_agentic_single_query_decomposition(
        user_request: UserRequest,
        request_context: dict[str, Any],
    ) -> DecompositionResult:
        """Collapse a prepared SQL request to one natural-language SQL task."""

        sql_context = request_context.get(SQL_AGENTIC_CONTEXT_KEY)
        sql_context = sql_context if isinstance(sql_context, dict) else {}
        original_prompt = str(
            sql_context.get("original_prompt") or user_request.raw_prompt or ""
        ).strip()
        parameter_key = str(sql_context.get("parameter_key") or "").strip()
        constraints: dict[str, Any] = {"sql_agentic_single_query": True}
        if parameter_key:
            constraints["parameter_key"] = parameter_key
        task = TaskFrame(
            id="task_1",
            description=original_prompt or "Answer the database question using SQL.",
            semantic_verb="calculate",
            object_type="sql.result",
            intent_confidence=1.0,
            constraints=constraints,
            dependencies=[],
            raw_evidence=original_prompt or user_request.raw_prompt,
            requires_confirmation=False,
            risk_level="low",
        )
        return DecompositionResult(
            tasks=[task],
            global_constraints={"sql_agentic_single_query": True},
            unresolved_references=[],
            assumptions=[
                "Prepared SQL context routes the full database request through one sql.query node."
            ],
        )

    @staticmethod
    def _sql_agentic_capability_selections(
        tasks: list[TaskFrame],
    ) -> list[CapabilitySelectionResult]:
        """Force prepared SQL tasks to the SQL LLM loop capability."""

        selections: list[CapabilitySelectionResult] = []
        for task in tasks:
            selected = CapabilityRef(
                capability_id="sql.query",
                operation_id="query",
                confidence=1.0,
                reason=(
                    "Prepared SQL context routes database work through sql.query; "
                    "the SQL LLM loop authors SQL from schema."
                ),
            )
            selections.append(
                CapabilitySelectionResult(
                    task_id=task.id,
                    candidates=[selected],
                    selected=selected,
                    unresolved_reason=None,
                )
            )
        return selections

    @staticmethod
    def _sql_agentic_fit_decisions(
        tasks: list[TaskFrame],
        registry: CapabilityRegistry,
        classification_context: dict[str, Any],
    ) -> list[CapabilityFitDecision]:
        """Return trusted fit decisions for the forced SQL capability."""

        try:
            manifest = registry.get("sql.query").manifest
            contract = manifest_contract(manifest)
            contract_hash = manifest_contract_hash(manifest)
            manifest_domain = str(manifest.domain or "")
            manifest_object_types = list(manifest.object_types or [])
        except Exception:
            contract = None
            contract_hash = None
            manifest_domain = "sql"
            manifest_object_types = ["sql.result"]
        likely_domains = [
            str(domain or "").strip().lower()
            for domain in classification_context.get("likely_domains", [])
            if str(domain or "").strip()
        ]
        return [
            CapabilityFitDecision(
                task_id=task.id,
                candidate_capability_id="sql.query",
                candidate_operation_id="query",
                status="fit",
                confidence=1.0,
                reasons=[
                    "Prepared SQL context requires the SQL LLM loop for database work."
                ],
                deterministic_rejections=[],
                normalized_task_domain="sql",
                normalized_likely_domains=likely_domains,
                normalized_task_object_type=str(task.object_type or "sql.result"),
                normalized_manifest_domain=manifest_domain,
                normalized_manifest_object_types=manifest_object_types,
                candidate_manifest_contract=contract,
                candidate_manifest_hash=contract_hash,
            )
            for task in tasks
        ]

    @staticmethod
    def _sql_agentic_argument_results(
        tasks: list[TaskFrame],
        request_context: dict[str, Any],
    ) -> list[ArgumentExtractionResult]:
        """Build sql.query arguments without letting generic extraction invent code."""

        sql_context = request_context.get(SQL_AGENTIC_CONTEXT_KEY)
        sql_context = sql_context if isinstance(sql_context, dict) else {}
        original_prompt = str(sql_context.get("original_prompt") or "").strip()
        parameter_key = str(sql_context.get("parameter_key") or "").strip()
        results: list[ArgumentExtractionResult] = []
        for task in tasks:
            prompt_parts = [
                f"User request: {original_prompt or task.description}",
                f"SQL task: {task.description}",
            ]
            if parameter_key:
                prompt_parts.append(f"Database profile key: {parameter_key}")
            arguments: dict[str, Any] = {"prompt": "\n".join(prompt_parts)}
            if parameter_key:
                arguments["parameter_key"] = parameter_key
            results.append(
                ArgumentExtractionResult(
                    task_id=task.id,
                    capability_id="sql.query",
                    operation_id="query",
                    arguments=arguments,
                    generated_arguments=[],
                    rejected_generated_arguments=[],
                    missing_required_arguments=[],
                    assumptions=[
                        "Prepared SQL context supplied sql.query arguments without "
                        "generic Python/operator argument extraction."
                    ],
                    confidence=1.0,
                )
            )
        return results

    @staticmethod
    def _reconcile_fit_approved_capability_selections(
        selections: list[CapabilitySelectionResult],
        fit_decisions: list[CapabilityFitDecision],
    ) -> tuple[list[CapabilitySelectionResult], list[str]]:
        """Carry a fit-approved fallback candidate back into downstream selection state."""

        fit_by_task = {
            decision.task_id: decision
            for decision in fit_decisions
            if decision.is_fit
            and decision.candidate_capability_id
            and decision.candidate_operation_id
        }
        updated: list[CapabilitySelectionResult] = []
        reconciled_task_ids: list[str] = []
        for selection in selections:
            decision = fit_by_task.get(selection.task_id)
            if decision is None:
                updated.append(selection)
                continue
            selected = selection.selected
            if (
                selected is not None
                and selected.capability_id == decision.candidate_capability_id
                and selected.operation_id == decision.candidate_operation_id
            ):
                updated.append(selection)
                continue

            matching_candidate = next(
                (
                    candidate
                    for candidate in selection.candidates
                    if candidate.capability_id == decision.candidate_capability_id
                    and candidate.operation_id == decision.candidate_operation_id
                ),
                None,
            )
            confidence = max(
                float(decision.confidence or 0.0),
                float(matching_candidate.confidence or 0.0) if matching_candidate else 0.0,
            )
            reason = (
                str(matching_candidate.reason or "").strip()
                if matching_candidate is not None
                else ""
            )
            if not reason:
                reason = (
                    str(decision.reasons[0] or "").strip()
                    if decision.reasons
                    else "Capability fit accepted this fallback candidate."
                )
            replacement = CapabilityRef(
                capability_id=str(decision.candidate_capability_id),
                operation_id=str(decision.candidate_operation_id),
                confidence=min(1.0, max(confidence, 0.60)),
                reason=reason,
            )
            updated.append(
                selection.model_copy(
                    update={
                        "selected": replacement,
                        "candidates": [
                            replacement,
                            *[
                                candidate
                                for candidate in selection.candidates
                                if not (
                                    candidate.capability_id == replacement.capability_id
                                    and candidate.operation_id == replacement.operation_id
                                )
                            ],
                        ],
                        "unresolved_reason": None,
                    }
                )
            )
            reconciled_task_ids.append(selection.task_id)
        return updated, reconciled_task_ids

    def _record_sql_clarification(
        self,
        *,
        user_request: UserRequest,
        request_context: dict[str, Any],
        payload: dict[str, Any],
        trace: PlanningTrace,
        phase: str = "sql_agent",
    ) -> str:
        """Convert a SQL capability clarification payload into the standard pending state."""

        clarification_request = OperatorClarificationRequest.model_validate(
            payload["clarification_request"]
        )
        clarification_payload = clarification_request.model_dump(mode="json")
        pending_state = (
            dict(payload.get("sql_pending_state"))
            if isinstance(payload.get("sql_pending_state"), dict)
            else {}
        )
        user_request.session_context[SQL_PENDING_CONTEXT_KEY] = pending_state
        request_context[SQL_PENDING_CONTEXT_KEY] = pending_state
        trace.metadata["operator_clarification_request"] = clarification_payload
        trace.metadata["operator_clarification_pending"] = True
        trace.metadata["operator_clarification_phase"] = phase
        trace.metadata["sql_agent_pending_state"] = pending_state
        self._record_last_failure(
            request_id=user_request.request_id,
            prompt=user_request.raw_prompt,
            category="clarification_required",
            stage=phase,
            reason="The SQL agent needs one clarification before continuing.",
            metadata={"clarification_request": clarification_payload},
        )
        return (
            "## Clarification Required\n\n"
            + str(clarification_request.question)
            + (
                "\n\n" + str(clarification_request.reason)
                if clarification_request.reason
                else ""
            )
        )

    @staticmethod
    def _sql_clarification_payload_from_bundle(result_bundle: ResultBundle) -> dict[str, Any] | None:
        """Return a SQL clarification payload from execution results, if one exists."""

        for result in result_bundle.results:
            preview = result.data_preview if isinstance(result.data_preview, dict) else {}
            if preview.get("status") == "clarification_required" and isinstance(
                preview.get("clarification_request"),
                dict,
            ):
                return dict(preview)
        return None

    @staticmethod
    def _sql_confirmation_payload_from_bundle(result_bundle: ResultBundle) -> dict[str, Any] | None:
        """Return a SQL confirmation payload from execution results, if one exists."""

        for result in result_bundle.results:
            preview = result.data_preview if isinstance(result.data_preview, dict) else {}
            if preview.get("status") == "confirmation_required" and isinstance(
                preview.get("sql_pending_confirmation"),
                dict,
            ):
                return dict(preview)
        return None

    def _bundle_with_sql_confirmation(
        self,
        result_bundle: ResultBundle,
        payload: dict[str, Any],
    ) -> ResultBundle:
        """Promote a SQL capability pause into the standard confirmation bundle shape."""

        actions = payload.get("confirmation_actions")
        pending = payload.get("sql_pending_confirmation")
        metadata = dict(result_bundle.metadata)
        metadata["confirmation_required"] = True
        metadata["confirmation_actions"] = list(actions) if isinstance(actions, list) else []
        if isinstance(pending, dict):
            metadata["sql_pending_confirmation"] = dict(pending)
        return ResultBundle(
            dag_id=result_bundle.dag_id,
            results=result_bundle.results,
            status="confirmation_required",
            safe_summary=str(payload.get("summary") or "SQL execution requires confirmation."),
            metadata=metadata,
        )
