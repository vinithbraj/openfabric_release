"""Memory and parameter helpers for AgentRuntime."""

from __future__ import annotations

from .common import *
from .formatting import *


class _MemoryParameterMixin:
    """Memory and parameter helpers for AgentRuntime."""

    def _build_user_request(self, raw_prompt: str, context: dict[str, Any]) -> UserRequest:
        """Build a typed user request with runtime-owned safety context."""

        payload = dict(context or {})
        user_context = dict(payload.pop("user_context", {}))
        session_context = dict(payload.pop("session_context", {}))
        safety_context = dict(payload.pop("safety_context", {}))
        private_user_macros = payload.pop(USER_MACRO_PRIVATE_CONTEXT_KEY, None)
        if isinstance(private_user_macros, list):
            safety_context[USER_MACRO_PRIVATE_CONTEXT_KEY] = [
                dict(item) for item in private_user_macros if isinstance(item, dict)
            ]
        session_context.update(payload)
        request_config = self._runtime_config_for_context(session_context)
        session_context.setdefault(
            "shell_input_bindings_mode",
            request_config.shell_input_bindings_mode,
        )
        safety_context.setdefault(
            "shell_input_bindings_mode",
            request_config.shell_input_bindings_mode,
        )
        session_context.setdefault("runtime_state", self._runtime_state_snapshot())
        safety_context["capability_registry"] = self.registry
        safety_context.setdefault("result_store", self.execution_engine.result_store)
        safety_context.setdefault("allow_full_output_access", False)
        safety_context.setdefault(
            "planning_trace",
            PlanningTrace(
                request_id="pending",
                raw_prompt=raw_prompt,
                capability_manifest_hash=registry_contract_hash(self.registry),
            ),
        )
        user_request = UserRequest(
            raw_prompt=raw_prompt,
            user_context=user_context,
            session_context=session_context,
            safety_context=safety_context,
        )
        return user_request

    def _active_memory_model_name(self, context: dict[str, Any]) -> str:
        """Return the best model identifier available for memory scoping."""

        for key in ("active_llm_model", "llm_model"):
            value = str(dict(context or {}).get(key) or "").strip()
            if value and value.lower() != "auto":
                return value
        model_name, _ = llm_client_metadata(self.llm_client)
        model_name = str(model_name or "").strip()
        return "" if model_name.lower() == "auto" else model_name

    def _attach_agent_memory(
        self,
        user_request: UserRequest,
        context: dict[str, Any],
        *,
        task_type: str = "",
        tool_type: str = "",
        intent_type: str = "",
        tags: list[str] | None = None,
    ) -> None:
        """Retrieve bounded persistent memory and attach it to the request."""

        config = self._runtime_config_for_context(context)
        if (
            self.memory_store is None
            or not bool(getattr(config, "agent_memory_enabled", True))
        ):
            user_request.session_context["agent_memory"] = []
            user_request.session_context["agent_memory_matches"] = []
            user_request.session_context["agent_memory_directives"] = []
            user_request.session_context["agent_memory_disabled"] = True
            return
        model_name = self._active_memory_model_name(context)
        family = normalize_model_family(model_name)
        context_tags = list(tags or [])
        for key in ("agent_mode", "mode"):
            value = str(dict(context or {}).get(key) or "").strip()
            if value:
                context_tags.append(value)
        hints = enrich_memory_retrieval_hints(
            user_request.raw_prompt,
            task_type=task_type,
            tags=context_tags,
        )
        retrieval = MemoryRetrievalContext(
            prompt=user_request.raw_prompt,
            model_name=model_name,
            model_family=family,
            task_type=hints.task_type,
            tool_type=tool_type,
            intent_type=intent_type,
            tags=hints.tags,
            max_chars=int(getattr(config, "agent_memory_prompt_max_chars", 3000)),
        )
        user_request.session_context["agent_memory_context"] = retrieval.model_dump(mode="json")
        try:
            matches = self.memory_store.retrieve_matches(retrieval)
        except Exception as exc:
            user_request.session_context["agent_memory_error"] = str(exc)
            user_request.session_context["agent_memory"] = []
            user_request.session_context["agent_memory_matches"] = []
            user_request.session_context["agent_memory_directives"] = []
            return
        entries = [match.entry for match in matches]
        match_payloads = [match.model_dump(mode="json") for match in matches]
        user_request.session_context["agent_memory"] = [
            entry.model_dump(mode="json") for entry in entries
        ]
        user_request.session_context["agent_memory_matches"] = match_payloads
        user_request.session_context["agent_memory_directives"] = [
            directive.model_dump(mode="json")
            for directive in memory_directives_from_entries(
                [entry.model_dump(mode="json") for entry in entries],
                match_details=match_payloads,
            )
        ]

    def _emit_agent_memory_check(
        self,
        user_request: UserRequest,
        trace: PlanningTrace,
        observability: ObservabilityContext,
    ) -> None:
        """Record the targeted persistent-memory retrieval checkpoint."""

        session_context = dict(user_request.session_context or {})
        memory_entries = session_context.get("agent_memory")
        if not isinstance(memory_entries, list):
            memory_entries = []
        memory_matches = session_context.get("agent_memory_matches")
        if not isinstance(memory_matches, list):
            memory_matches = []
        memory_directives = session_context.get("agent_memory_directives")
        if not isinstance(memory_directives, list):
            memory_directives = []
        retrieval_context = session_context.get("agent_memory_context")
        if not isinstance(retrieval_context, dict):
            retrieval_context = {}
        memory_error = str(session_context.get("agent_memory_error") or "").strip()
        memory_disabled = bool(session_context.get("agent_memory_disabled"))
        memory_ids = [
            str(entry.get("memory_id") or "")
            for entry in memory_entries
            if isinstance(entry, dict) and str(entry.get("memory_id") or "").strip()
        ]

        def _memory_use_count(entry: dict[str, Any]) -> int:
            try:
                return max(0, int(entry.get("use_count") or 0))
            except (TypeError, ValueError):
                return 0

        memory_use_counts = {
            str(entry.get("memory_id") or ""): _memory_use_count(entry)
            for entry in memory_entries
            if isinstance(entry, dict) and str(entry.get("memory_id") or "").strip()
        }
        memory_use_count = sum(memory_use_counts.values())
        rendered_stage_ids = session_context.get("agent_memory_rendered_stage_ids")
        if not isinstance(rendered_stage_ids, dict):
            rendered_stage_ids = {}
        filtered_stage_ids = session_context.get("agent_memory_filtered_stage_ids")
        if not isinstance(filtered_stage_ids, dict):
            filtered_stage_ids = {}
        scoped_diagnostics = session_context.get("agent_memory_scopes")
        if not isinstance(scoped_diagnostics, dict):
            scoped_diagnostics = {}
        trace.metadata["agent_memory_count"] = len(memory_ids)
        trace.metadata["agent_memory_use_count"] = memory_use_count
        trace.metadata["agent_memory_context"] = retrieval_context
        trace.metadata["agent_memory_matches"] = memory_matches
        trace.metadata["agent_memory_directives"] = memory_directives
        if memory_error:
            trace.metadata["agent_memory_error"] = memory_error
        selected_candidate = {
            "memory_count": len(memory_ids),
            "memory_ids": memory_ids,
            "retrieved_memory_ids": memory_ids,
            "retrieved_candidate_ids": memory_ids,
            "rendered_directive_ids": rendered_stage_ids,
            "filtered_directive_ids": filtered_stage_ids,
            "agent_memory_scopes": scoped_diagnostics,
            "memory_use_count": memory_use_count,
            "memory_use_counts": memory_use_counts,
            "retrieval_context": retrieval_context,
            "disabled": memory_disabled,
            "error": memory_error,
            "matches": memory_matches,
            "directives": memory_directives,
        }
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage=STAGE_MEMORY_CHECK,
                request_id=user_request.request_id,
                prompt_template_id="agent_memory_retrieval",
                raw_llm_response=None,
                parsed_proposal=None,
                selected_candidate=selected_candidate,
                deterministic_normalizations=["targeted_memory_retrieval"],
            ),
        )
        observability.stage_started(
            STAGE_MEMORY_CHECK,
            "Memory check started",
            "The runtime is checking persistent memory for entries relevant to this request.",
        )
        if memory_error:
            observability.stage_completed(
                STAGE_MEMORY_CHECK,
                "Memory check skipped",
                "Persistent memory lookup failed, so the request will continue without memory guidance.",
                details={"error": memory_error},
            )
            return
        if memory_disabled:
            observability.stage_completed(
                STAGE_MEMORY_CHECK,
                "Memory check disabled",
                "Persistent memory is disabled for this request.",
                details={"memory_count": 0},
            )
            return
        observability.stage_completed(
            STAGE_MEMORY_CHECK,
            "Memory check completed",
            f"Retrieved {len(memory_ids)} relevant memory item(s) for this request.",
            details={
                "memory_count": len(memory_ids),
                "memory_ids": memory_ids,
                "retrieved_memory_ids": memory_ids,
                "retrieved_candidate_ids": memory_ids,
                "rendered_directive_ids": rendered_stage_ids,
                "filtered_directive_ids": filtered_stage_ids,
                "agent_memory_scopes": scoped_diagnostics,
                "memory_use_count": memory_use_count,
                "memory_use_counts": memory_use_counts,
                "matches": memory_matches,
                "directives": memory_directives,
            },
        )

    def _adjudicate_database_parameter_matches(
        self,
        user_request: UserRequest,
        matches: list[Any],
    ) -> tuple[list[Any], dict[str, Any]]:
        """Use the LLM only to break weak/ambiguous DB profile ties."""

        db_matches: list[tuple[Any, dict[str, Any]]] = []
        for match in matches:
            profile = parameter_database_profile(match.record)
            if profile.get("profile_type") == "database_connection":
                db_matches.append((match, profile))
        if len(db_matches) < 2 or any(bool(match.exact) for match, _profile in db_matches):
            return matches, {}

        complete_json = getattr(self.llm_client, "complete_json", None)
        if not callable(complete_json):
            return matches, {
                "decision": "clarify",
                "source": "deterministic",
                "reason": "Multiple database_connection parameters matched and none was exact.",
            }

        candidates: list[dict[str, Any]] = []
        for match, profile in db_matches:
            summary = match.summary.model_dump(mode="json")
            candidates.append(
                {
                    "key": match.record.key,
                    "normalized_key": match.record.normalized_key,
                    "score": match.score,
                    "exact": match.exact,
                    "match_reasons": list(match.match_reasons),
                    "description": summary.get("description") or "",
                    "aliases": summary.get("aliases") or [],
                    "tags": summary.get("tags") or [],
                    "value_schema": summary.get("value_schema") or {},
                    "database_profile": profile,
                }
            )

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "decision": {"type": "string", "enum": ["select", "clarify"]},
                "selected_normalized_key": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
            },
            "required": ["decision", "confidence", "reason"],
        }
        prompt = "\n".join(
            [
                *prompt_lines("parameters.database_profile_adjudication"),
                "User prompt:",
                str(user_request.raw_prompt or ""),
                "Masked candidates:",
                json.dumps(candidates, sort_keys=True, ensure_ascii=True),
            ]
        )
        try:
            raw = complete_json(prompt, schema)
        except Exception as exc:
            return matches, {
                "decision": "clarify",
                "source": "llm_error",
                "reason": str(exc)[:240],
            }
        if not isinstance(raw, dict):
            raw = {}
        decision = str(raw.get("decision") or "").strip().lower()
        selected_key = normalize_parameter_key(
            str(raw.get("selected_normalized_key") or raw.get("selected_key") or "")
        )
        try:
            confidence = float(raw.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        reason = str(raw.get("reason") or "").strip()[:500]
        db_keys = {match.record.normalized_key for match, _profile in db_matches}
        adjudication = {
            "decision": "clarify",
            "source": "llm",
            "selected_normalized_key": selected_key,
            "confidence": confidence,
            "reason": reason,
        }
        if decision == "select" and confidence >= 0.75 and selected_key in db_keys:
            filtered = [
                match
                for match in matches
                if parameter_database_profile(match.record).get("profile_type") != "database_connection"
                or match.record.normalized_key == selected_key
            ]
            adjudication["decision"] = "select"
            return filtered, adjudication
        return matches, adjudication

    def _attach_agent_parameters(
        self,
        user_request: UserRequest,
        context: dict[str, Any],
    ) -> None:
        """Retrieve relevant parameter summaries and keep raw values execution-only."""

        config = self._runtime_config_for_context(context)
        session_context = user_request.session_context
        safety_context = user_request.safety_context
        if (
            self.parameter_store is None
            or not bool(getattr(config, "agent_parameter_store_enabled", True))
        ):
            session_context[PARAMETER_CONTEXT_KEY] = []
            session_context["agent_parameters"] = []
            session_context["agent_parameter_matches"] = []
            session_context[PARAMETER_ENV_CONTEXT_KEY] = {}
            session_context[PARAMETER_AGENT_CONTEXT_KEY] = {}
            session_context["agent_parameter_shell_env_names"] = []
            session_context["agent_parameter_store_disabled"] = True
            safety_context.pop("agent_parameter_shell_env_raw", None)
            safety_context.pop("agent_parameter_execution_values", None)
            return
        try:
            matches = self.parameter_store.retrieve_matches(
                user_request.raw_prompt,
                limit=6,
                record_use=False,
            )
            matches, db_adjudication = self._adjudicate_database_parameter_matches(
                user_request,
                matches,
            )
            if matches:
                updated_records = self.parameter_store.record_use(
                    [match.record for match in matches],
                    actor="runtime",
                )
                by_key = {record.normalized_key: record for record in updated_records}
                matches = [
                    match.model_copy(
                        update={
                            "record": by_key.get(match.record.normalized_key, match.record),
                            "summary": masked_parameter_summary(
                                by_key.get(match.record.normalized_key, match.record)
                            ),
                        }
                    )
                    for match in matches
                ]
        except Exception as exc:
            session_context[PARAMETER_CONTEXT_KEY] = []
            session_context["agent_parameters"] = []
            session_context["agent_parameter_matches"] = []
            session_context[PARAMETER_ENV_CONTEXT_KEY] = {}
            session_context[PARAMETER_AGENT_CONTEXT_KEY] = {}
            session_context["agent_parameter_shell_env_names"] = []
            session_context["agent_parameter_store_error"] = str(exc)
            safety_context.pop("agent_parameter_shell_env_raw", None)
            safety_context.pop("agent_parameter_execution_values", None)
            return

        summaries: list[dict[str, Any]] = []
        match_payloads: list[dict[str, Any]] = []
        raw_env: dict[str, str] = {}
        execution_values: dict[str, Any] = {}
        context_values: dict[str, Any] = {}
        for match in matches:
            record = match.record
            env = parameter_shell_env(record)
            raw_env.update(env)
            execution_values[record.normalized_key] = record.value_json
            context_summary = parameter_context_summary(record.context_json)
            if context_summary:
                context_values[record.normalized_key] = context_summary
            summary = masked_parameter_summary(record).model_dump(mode="json")
            summary["score"] = match.score
            summary["exact"] = match.exact
            summary["match_reasons"] = list(match.match_reasons)
            summary["env_names"] = sorted(env)
            summary["database_profile"] = parameter_database_profile(record)
            if context_summary:
                summary["context_json"] = context_summary
            summaries.append(summary)
            match_payloads.append(
                {
                    "key": record.key,
                    "normalized_key": record.normalized_key,
                    "score": match.score,
                    "exact": match.exact,
                    "match_reasons": list(match.match_reasons),
                    "summary": summary,
                }
            )
        session_context[PARAMETER_CONTEXT_KEY] = summaries
        session_context["agent_parameters"] = summaries
        session_context["agent_parameter_matches"] = match_payloads
        if db_adjudication:
            session_context["agent_parameter_db_match_adjudication"] = db_adjudication
        else:
            session_context.pop("agent_parameter_db_match_adjudication", None)
        session_context[PARAMETER_ENV_CONTEXT_KEY] = {name: "••••" for name in sorted(raw_env)}
        session_context[PARAMETER_AGENT_CONTEXT_KEY] = context_values
        session_context["agent_parameter_shell_env_names"] = sorted(raw_env)
        session_context["agent_parameter_store_disabled"] = False
        safety_context["agent_parameter_shell_env_raw"] = raw_env
        safety_context["agent_parameter_execution_values"] = execution_values
        self._attach_exact_parameter_typein_macro(user_request, matches)

    def _attach_exact_parameter_typein_macro(
        self,
        user_request: UserRequest,
        matches: list[Any],
    ) -> None:
        """Use an exact credential parameter as private terminal input when possible."""

        if self.parameter_store is None:
            return
        safety_context = user_request.safety_context
        existing = safety_context.get(USER_MACRO_PRIVATE_CONTEXT_KEY)
        if isinstance(existing, list) and any(
            isinstance(item, dict)
            and str(item.get("kind") or "").strip().lower() == TYPEIN_MACRO_KIND
            for item in existing
        ):
            return
        explicit_sudo_record = explicit_sudo_parameter_record_from_prompt(
            self.parameter_store,
            user_request.raw_prompt,
        )
        exact_matches = [
            match
            for match in matches
            if bool(getattr(match, "exact", False))
            and getattr(match, "record", None) is not None
        ]
        explicit_sudo_parameter = explicit_sudo_record is not None
        if explicit_sudo_parameter:
            record = explicit_sudo_record
        elif len(exact_matches) == 1:
            record = exact_matches[0].record
        else:
            return
        prompt_kind = _credential_input_kind_from_text(user_request.raw_prompt)
        if explicit_sudo_parameter:
            credential_use = True
        else:
            credential_use = _prompt_suggests_exact_parameter_credential_use(
                user_request.raw_prompt,
                record,
            )
        if prompt_kind == "unknown" and credential_use:
            prompt_kind = "credential"
        if prompt_kind == "unknown" or not credential_use:
            return
        try:
            private_payload, public_summary = typein_macro_payloads_from_parameter_record(
                record,
                field=_credential_field_hint(prompt_kind),
                parameter_store=self.parameter_store,
                record_use=False,
                macro_id="macro_typein_1",
                input_name=f"{TYPEIN_INPUT_PREFIX}1",
                actor="exact_parameter_typein",
            )
        except Exception:
            return
        safety_context[USER_MACRO_PRIVATE_CONTEXT_KEY] = [private_payload]
        summaries = user_request.session_context.get(USER_MACRO_SUMMARY_CONTEXT_KEY)
        if not isinstance(summaries, list):
            summaries = []
        user_request.session_context[USER_MACRO_SUMMARY_CONTEXT_KEY] = [
            *[dict(item) for item in summaries if isinstance(item, dict)],
            public_summary,
        ]

    def _enrich_parameter_clarification_request(
        self,
        user_request: UserRequest,
        request: OperatorClarificationRequest,
    ) -> OperatorClarificationRequest:
        """Attach masked Parameter Store choices to credential clarifications."""

        if _clarification_primary_text_is_plain_answer(request):
            return request
        packet = "\n".join(
            [
                str(user_request.raw_prompt or ""),
                str(request.question or ""),
                str(request.reason or ""),
                str(request.missing_information or ""),
            ]
        )
        input_kind = str(getattr(request, "input_kind", "unknown") or "unknown").strip().lower()
        if input_kind == "unknown":
            input_kind = _credential_input_kind_from_text(packet)
        if input_kind == "unknown":
            return request
        updates: dict[str, Any] = {
            "input_kind": input_kind,
            "secret_input": True,
            "allow_freeform": True,
        }
        if self.parameter_store is not None:
            try:
                choices = parameter_clarification_choices(
                    self.parameter_store,
                    packet,
                    relevant_limit=6,
                    total_limit=20,
                )
            except Exception:
                choices = []
            if choices:
                updates["parameter_choices"] = choices
        return request.model_copy(update=updates)

    def _emit_agent_parameter_check(
        self,
        user_request: UserRequest,
        trace: PlanningTrace,
        observability: ObservabilityContext,
    ) -> None:
        """Record the parameter-store retrieval checkpoint without raw values."""

        session_context = dict(user_request.session_context or {})
        summaries = session_context.get(PARAMETER_CONTEXT_KEY)
        if not isinstance(summaries, list):
            summaries = []
        matches = session_context.get("agent_parameter_matches")
        if not isinstance(matches, list):
            matches = []
        env_names = session_context.get("agent_parameter_shell_env_names")
        if not isinstance(env_names, list):
            env_names = []
        disabled = bool(session_context.get("agent_parameter_store_disabled"))
        error = str(session_context.get("agent_parameter_store_error") or "").strip()
        keys = [
            str(item.get("key") or "")
            for item in summaries
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        ]
        trace.metadata["agent_parameter_count"] = len(keys)
        trace.metadata["agent_parameter_keys"] = keys
        trace.metadata["agent_parameter_matches"] = matches
        trace.metadata["agent_parameter_env_names"] = env_names
        if error:
            trace.metadata["agent_parameter_store_error"] = error
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage=STAGE_PARAMETER_CHECK,
                request_id=user_request.request_id,
                prompt_template_id="agent_parameter_retrieval",
                raw_llm_response=None,
                parsed_proposal=None,
                selected_candidate={
                    "parameter_count": len(keys),
                    "keys": keys,
                    "env_names": env_names,
                    "disabled": disabled,
                    "error": error,
                    "matches": matches,
                },
                deterministic_normalizations=["parameter_store_retrieval"],
            ),
        )
        observability.stage_started(
            STAGE_PARAMETER_CHECK,
            "Parameter store check started",
            "The runtime is checking local parameters relevant to this request.",
        )
        if error:
            observability.stage_completed(
                STAGE_PARAMETER_CHECK,
                "Parameter store check skipped",
                "Parameter lookup failed, so the request will continue without stored parameters.",
                details={"error": error},
            )
            return
        if disabled:
            observability.stage_completed(
                STAGE_PARAMETER_CHECK,
                "Parameter store disabled",
                "The local parameter store is disabled for this request.",
                details={"parameter_count": 0},
            )
            return
        observability.stage_completed(
            STAGE_PARAMETER_CHECK,
            "Parameter store check completed",
            f"Retrieved {len(keys)} relevant parameter item(s) for this request.",
            details={"parameter_count": len(keys), "keys": keys, "env_names": env_names},
        )

    def _execution_context_with_agent_parameters(
        self,
        user_request: UserRequest,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Return execution context with raw parameter values attached only for execution."""

        execution_context = dict(context or {})
        raw_parameter_env = user_request.safety_context.get("agent_parameter_shell_env_raw")
        if isinstance(raw_parameter_env, dict) and raw_parameter_env:
            shell_env = dict(execution_context.get("shell_env") or {})
            shell_env.update({str(key): str(value) for key, value in raw_parameter_env.items()})
            execution_context["shell_env"] = shell_env
        parameter_values = user_request.safety_context.get("agent_parameter_execution_values")
        if isinstance(parameter_values, dict):
            execution_context["agent_parameter_execution_values"] = parameter_values
        if self.parameter_store is not None:
            execution_context["parameter_store"] = self.parameter_store
        return execution_context

    @staticmethod
    def _restore_agent_memory_from_trace(
        user_request: UserRequest,
        trace: PlanningTrace,
        execution_context: dict[str, Any] | None = None,
    ) -> list[str]:
        """Carry retrieved memory from a saved trace into replay/continuation requests."""

        metadata = getattr(trace, "metadata", None)
        if not isinstance(metadata, dict):
            return []

        def _copy_jsonish(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: _copy_jsonish(item) for key, item in value.items()}
            if isinstance(value, list):
                return [_copy_jsonish(item) for item in value]
            return value

        def _empty(value: Any) -> bool:
            if value is None:
                return True
            if isinstance(value, (dict, list, str)):
                return not value
            return False

        session_context = user_request.session_context
        restored: list[str] = []
        list_keys = (
            "agent_memory",
            "agent_memory_matches",
            "agent_memory_directives",
        )
        has_scoped_memory = isinstance(metadata.get("agent_memory_scoped_cache"), dict) and bool(
            metadata.get("agent_memory_scoped_cache")
        )
        dict_keys = (
            "agent_memory_context",
            "agent_memory_scoped_cache",
            "agent_memory_scopes",
            "agent_memory_rendered_stage_ids",
            "agent_memory_filtered_stage_ids",
            "agent_memory_stage_filter_notes",
        )
        scalar_keys = (
            "agent_memory_error",
            "agent_memory_disabled",
            "agent_memory_count",
            "agent_memory_use_count",
        )
        for key in list_keys:
            if has_scoped_memory:
                continue
            value = metadata.get(key)
            if not isinstance(value, list) or not value or not _empty(session_context.get(key)):
                continue
            copied = _copy_jsonish(value)
            session_context[key] = copied
            if execution_context is not None and _empty(execution_context.get(key)):
                execution_context[key] = _copy_jsonish(value)
            restored.append(key)
        for key in dict_keys:
            value = metadata.get(key)
            if not isinstance(value, dict) or not value or not _empty(session_context.get(key)):
                continue
            copied = _copy_jsonish(value)
            session_context[key] = copied
            if execution_context is not None and _empty(execution_context.get(key)):
                execution_context[key] = _copy_jsonish(value)
            restored.append(key)
        for key in scalar_keys:
            if key not in metadata or not _empty(session_context.get(key)):
                continue
            value = metadata.get(key)
            if value in (None, "", [], {}):
                continue
            session_context[key] = value
            if execution_context is not None and _empty(execution_context.get(key)):
                execution_context[key] = value
            restored.append(key)
        if restored and (
            session_context.get("agent_memory")
            or session_context.get("agent_memory_directives")
            or session_context.get("agent_memory_scoped_cache")
        ):
            session_context["agent_memory_retrieved_after_self_brief"] = True
            if execution_context is not None:
                execution_context.setdefault("agent_memory_retrieved_after_self_brief", True)
            metadata["agent_memory_restored_from_trace"] = True
            metadata["agent_memory_restored_keys"] = list(restored)
        return restored
