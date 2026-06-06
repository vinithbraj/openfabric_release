"""Runtime service construction and direct service handlers."""

from __future__ import annotations

from .common import *
from .formatting import *


class _RuntimeServicesMixin:
    """Runtime service construction and direct service handlers."""

    def _build_direct_answer_prompt(self, user_request: UserRequest) -> str:
        """Build the structured prompt used for no-tool direct answers."""

        return "\n".join(
            [
                *prompt_lines("direct_answer"),
                *memory_prompt_lines_from_context(user_request, stage="direct_answer"),
                *parameter_prompt_lines_from_context(user_request, stage="direct_answer"),
                *online_lookup_prompt_lines(user_request.session_context),
                *online_ai_check_prompt_lines(user_request.session_context),
                "JSON schema:",
                str(DirectAnswerProposal.model_json_schema()),
                "User prompt:",
                user_request.raw_prompt,
            ]
        )

    def _direct_answer(self, user_request: UserRequest, llm_client=None) -> str:
        """Answer a no-tool simple question through a structured LLM call."""

        client = llm_client or self.llm_client
        proposal = DirectAnswerProposal.model_validate(
            client.complete_json(
                self._build_direct_answer_prompt(user_request),
                DirectAnswerProposal.model_json_schema(),
            )
        )
        answer = str(proposal.answer or "").strip()
        if not answer:
            return "I could not produce a direct answer for that request."
        return answer

    def _runtime_config_for_context(self, context: dict[str, Any] | None = None):
        """Return request-scoped runtime config overlays such as terminal cwd."""

        config = self.execution_engine.safety_policy.config
        payload = dict(context or {})
        updates: dict[str, Any] = {
            "operator_policy_profile": normalize_operator_policy_profile(
                payload.get("operator_policy_profile")
                or getattr(config, "operator_policy_profile", "deterministic")
            ),
            "reasoning_profile": normalize_reasoning_profile(
                payload.get("reasoning_profile") or getattr(config, "reasoning_profile", "fast")
            ),
            "repair_profile": normalize_repair_profile(
                payload.get("repair_profile") or getattr(config, "repair_profile", "balanced")
            ),
            "workflow_execution_mode": normalize_workflow_execution_mode(
                payload.get("workflow_execution_mode")
                or payload.get("operator_execution_mode")
                or getattr(config, "workflow_execution_mode", "streaming")
            ),
            "prompt_rephrase_enabled": effective_prompt_rephrase_enabled(
                payload
                if "prompt_rephrase_enabled" in payload
                or "operator_auto_rephrase_retry_enabled" in payload
                else config
            ),
            "response_streaming_enabled": bool(
                payload.get(
                    "response_streaming_enabled",
                    getattr(config, "response_streaming_enabled", False),
                )
            ),
            "shell_input_bindings_mode": normalize_shell_input_bindings_mode(
                payload.get("shell_input_bindings_mode")
                or getattr(config, "shell_input_bindings_mode", "allow")
            ),
        }
        terminal_session_id = str(payload.get("terminal_session_id") or "").strip()
        terminal_cwd = str(payload.get("terminal_cwd") or "").strip()
        if terminal_session_id and terminal_cwd:
            updates["terminal_session_id"] = terminal_session_id
            updates["terminal_cwd"] = terminal_cwd
        gateway_default_cwd = str(payload.get("gateway_default_cwd") or "").strip()
        if gateway_default_cwd and not (terminal_session_id and terminal_cwd):
            updates["workspace_root"] = gateway_default_cwd
        gateway_node = str(payload.get("gateway_node") or payload.get("node") or "").strip()
        gateway_url = str(payload.get("gateway_url") or "").strip()
        if gateway_node:
            updates["gateway_default_node"] = gateway_node
            existing_endpoints = dict(getattr(config, "gateway_endpoints", {}) or {})
            payload_endpoints = payload.get("gateway_endpoints")
            if isinstance(payload_endpoints, dict):
                for raw_node, raw_url in payload_endpoints.items():
                    node = str(raw_node or "").strip()
                    url = str(raw_url or "").strip()
                    if node and url:
                        existing_endpoints[node] = url
            if gateway_url:
                existing_endpoints[gateway_node] = gateway_url
                updates["gateway_url"] = gateway_url
            if existing_endpoints:
                updates["gateway_endpoints"] = existing_endpoints
        updates["terminal_execution_enabled"] = bool(
            payload.get("execute_in_terminal")
            and terminal_session_id
            and terminal_cwd
        )
        mode = str(payload.get("agent_clarification_mode") or "").strip().lower()
        if mode:
            normalized_mode = normalize_agent_clarification_mode(
                mode,
            )
            updates["agent_clarification_mode"] = normalized_mode
        final_response_mode = str(payload.get("llm_operator_final_response_mode") or "").strip().lower()
        if final_response_mode:
            updates["llm_operator_final_response_mode"] = (
                final_response_mode
                if final_response_mode in {"detailed", "simple"}
                else "detailed"
            )
        cardinality_judge_mode = str(
            payload.get("llm_operator_cardinality_judge_mode") or ""
        ).strip().lower()
        if cardinality_judge_mode:
            updates["llm_operator_cardinality_judge_mode"] = normalize_cardinality_judge_mode(
                cardinality_judge_mode
            )
        sql_agent_chat_route_mode = str(payload.get("sql_agent_chat_route_mode") or "").strip().lower()
        if sql_agent_chat_route_mode:
            updates["sql_agent_chat_route_mode"] = (
                sql_agent_chat_route_mode
                if sql_agent_chat_route_mode in {"agentic", "direct"}
                else "agentic"
            )
        bool_overlays = (
            "llm_operator_step_validation_enabled",
            "llm_operator_verification_enforced",
            "llm_operator_verbose_enabled",
            "operator_workspace_cwd_guard_enabled",
            "reliability_verifier_enforced",
            "agent_memory_enabled",
            "agent_parameter_store_enabled",
            "lrnt_enabled",
            "lrdirect_enabled",
            "sql_agent_enabled",
            "online_ai_check_reuse_browser",
            "online_ai_check_headless",
        )
        for key in bool_overlays:
            if key in payload:
                updates[key] = bool(payload.get(key))
        int_overlays = {
            "llm_operator_formatter_source_preview_chars": (200, 20000),
            "llm_operator_max_clarification_rounds": (0, 10),
            "reliability_max_recovery_probes": (0, 20),
            "reliability_max_autonomous_repair_attempts": (0, 20),
            "reliability_weak_model_plan_action_cap": (1, 32),
            "reliability_approval_envelope_budget": (0, 20),
            "agent_memory_prompt_max_chars": (200, 20000),
            "agent_parameter_prompt_max_chars": (200, 20000),
            "sql_agent_default_limit": (1, 1000),
            "sql_agent_max_rows": (1, 10000),
            "sql_agent_max_repair_attempts": (0, 10),
            "lrnt_max_entries": (1, 50000),
        }
        for key, (minimum, maximum) in int_overlays.items():
            if key not in payload:
                continue
            try:
                value = int(payload.get(key))
            except (TypeError, ValueError):
                continue
            updates[key] = max(minimum, min(maximum, value))
        float_overlays = {
            "agent_command_template_cache_similarity_threshold": (0.0, 1.0),
            "agent_command_template_cache_secondary_similarity_threshold": (0.0, 1.0),
            "lrnt_similarity_threshold": (0.0, 1.0),
        }
        for key, (minimum, maximum) in float_overlays.items():
            if key not in payload:
                continue
            try:
                value = float(payload.get(key))
            except (TypeError, ValueError):
                continue
            updates[key] = max(minimum, min(maximum, value))
        if "online_ai_check_timeout_seconds" in payload:
            try:
                timeout_seconds = float(payload.get("online_ai_check_timeout_seconds"))
            except (TypeError, ValueError):
                timeout_seconds = None
            if timeout_seconds is not None:
                updates["online_ai_check_timeout_seconds"] = max(1.0, min(300.0, timeout_seconds))
        reliability_mode = str(payload.get("reliability_mode") or "").strip().lower()
        if reliability_mode:
            updates["reliability_mode"] = (
                reliability_mode
                if reliability_mode in {"off", "standard", "aggressive"}
                else "aggressive"
            )
        allowlist_hashes = payload.get("operator_command_allowlist_hashes")
        if isinstance(allowlist_hashes, list):
            updates["operator_command_allowlist_hashes"] = [
                value
                for value in (str(item or "").strip().lower() for item in allowlist_hashes)
                if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)
            ]
        if not updates:
            return config
        return config.model_copy(update=updates)

    def _operator_pipeline(self, llm_client=None, context: dict[str, Any] | None = None) -> LLMOperatorPipeline:
        """Build the explicit Conversational pipeline over existing execution seams."""

        config = self._runtime_config_for_context(context)
        profile_policy = operator_profile_policy(
            config.reasoning_profile,
            llm_operator_verbose_enabled=config.llm_operator_verbose_enabled,
        )
        pipeline_type = (
            LLMOperatorPipeline
            if profile_policy.contract_mode == "verbose"
            else MachineOperatorPipeline
        )
        return pipeline_type(
            llm_client=llm_client or self.llm_client,
            gateway_client=self.execution_engine.gateway_client,
            config=config,
            result_store=self.execution_engine.result_store,
            memory_store=self.memory_store,
            plan_cache_store=self.plan_cache_store,
            command_template_cache_store=self.command_template_cache_store,
            computation_cache_store=self.computation_cache_store,
            reliability_store=self.reliability_store,
        )

    def _sql_agent_service(
        self,
        *,
        llm_client=None,
        context: dict[str, Any] | None = None,
    ) -> SqlAgentService:
        """Build the PostgreSQL-first SQL agent service."""

        runtime_context = dict(context or {})
        runtime_context.setdefault("gateway_client", self.execution_engine.gateway_client)
        if self.memory_store is not None:
            runtime_context.setdefault("memory_store", self.memory_store)
        if self.parameter_store is not None:
            runtime_context.setdefault("parameter_store", self.parameter_store)
        runtime_config = self._runtime_config_for_context(runtime_context)
        gateway_client = (
            runtime_context.get("gateway_client")
            or self.execution_engine.gateway_client
        )
        if type(gateway_client) is GatewayClient:
            gateway_client = GatewayClient(runtime_config)
        return SqlAgentService(
            parameter_store=self.parameter_store,
            gateway_client=gateway_client,
            llm_client=llm_client or self.llm_client,
            memory_store=self.memory_store,
            config=runtime_config,
            context=runtime_context,
        )

    def _database_discovery_service(
        self,
        *,
        context: dict[str, Any] | None = None,
    ) -> SqlDatabaseDiscoveryService:
        """Build the deterministic /discoverdb service."""

        runtime_context = dict(context or {})
        runtime_context.setdefault("gateway_client", self.execution_engine.gateway_client)
        if self.parameter_store is not None:
            runtime_context.setdefault("parameter_store", self.parameter_store)
        runtime_config = self._runtime_config_for_context(runtime_context)
        gateway_client = (
            runtime_context.get("gateway_client")
            or self.execution_engine.gateway_client
        )
        if type(gateway_client) is GatewayClient:
            gateway_client = GatewayClient(runtime_config)
        return SqlDatabaseDiscoveryService(
            parameter_store=self.parameter_store,
            gateway_client=gateway_client,
            config=runtime_config,
            context=runtime_context,
        )

    def handle_database_discovery_request(
        self,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Preview Parameter Store DB profiles from a /discoverdb request."""

        try:
            request = DatabaseDiscoveryRequest.model_validate(payload)
            request_context = {
                **(
                    request.context
                    if isinstance(request.context, dict)
                    else {}
                ),
                **dict(context or {}),
            }
            parsed = parse_discoverdb_macro(request.prompt) if request.prompt.strip() else None
            if parsed is not None:
                request = parsed
        except Exception as exc:
            return {
                "status": "error",
                "engine": "",
                "discovered": [],
                "skipped_existing": [],
                "created": [],
                "updated": [],
                "errors": [str(exc)],
                "warnings": [],
                "error": "invalid_discoverdb_request",
            }
        return self._database_discovery_service(context=request_context).discover(request)

    def handle_database_discovery_commit(
        self,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Save reviewed /discoverdb drafts into Parameter Store."""

        request_context = dict(context or {})
        try:
            request = DatabaseDiscoveryCommitRequest.model_validate(payload)
        except Exception as exc:
            return {
                "status": "error",
                "engine": "postgresql",
                "discovered": [],
                "created": [],
                "updated": [],
                "skipped_existing": [],
                "errors": [str(exc)],
                "warnings": [],
                "error": "invalid_discoverdb_commit",
            }
        return self._database_discovery_service(context=request_context).commit(request)

    def _maybe_handle_database_discovery_macro_request(
        self,
        raw_prompt: str,
        context: dict[str, Any] | None = None,
    ) -> str | None:
        """Short-circuit /discoverdb before generic logging, tracing, or LLM planning."""

        if not re.match(r"^\s*/discoverdb\b", str(raw_prompt or ""), re.IGNORECASE):
            return None
        payload = self.handle_database_discovery_request({"prompt": raw_prompt}, context or {})
        if payload.get("status") != "preview":
            errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
            return "Database discovery failed: " + (
                "; ".join(str(error) for error in errors)
                or str(payload.get("error") or "Unknown error.")
            )
        discovered = payload.get("discovered") if isinstance(payload.get("discovered"), list) else []
        skipped = (
            payload.get("skipped_existing")
            if isinstance(payload.get("skipped_existing"), list)
            else []
        )
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        lines = ["Database discovery preview ready."]
        if discovered:
            lines.append(
                "Profiles to save or refresh: "
                + ", ".join(
                    str(item.get("key") or "")
                    for item in discovered
                    if isinstance(item, dict) and item.get("key")
                )
            )
        else:
            lines.append("No database profile changes were discovered.")
        if skipped:
            lines.append(
                "Skipped existing: "
                + ", ".join(
                    str(item.get("key") or item.get("normalized_key") or "")
                    for item in skipped
                    if isinstance(item, dict)
                )
            )
        if warnings:
            lines.append("Warnings: " + "; ".join(str(warning) for warning in warnings))
        lines.append("Use the /discoverdb review action in the UI to save these profiles.")
        return "\n".join(lines)

    def handle_sql_query_request(
        self,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle the dedicated SQL API request."""

        request = SqlAgentRequest.model_validate(payload)
        request_context = {
            **(
                request.context
                if isinstance(request.context, dict)
                else {}
            ),
            **dict(context or {}),
        }
        prompt = request.prompt or request.sql or "SQL query"
        user_request = self._build_user_request(prompt, request_context)
        self._attach_agent_parameters(user_request, request_context)
        sql_context = {
            **request_context,
            **user_request.session_context,
            "raw_prompt": prompt,
            "gateway_client": self.execution_engine.gateway_client,
        }
        return self._sql_agent_service(context=sql_context).run(
            prompt=request.prompt,
            parameter_key=request.parameter_key,
            sql=request.sql,
            operation=request.operation,
            limit=request.limit,
            refresh_schema=request.refresh_schema,
        )
