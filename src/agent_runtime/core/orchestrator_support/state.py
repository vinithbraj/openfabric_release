"""Runtime state, registry, failure, plan, and rephrase helpers."""

from __future__ import annotations

from .common import *
from .formatting import *


class _StateMixin:
    """Runtime state, registry, failure, plan, and rephrase helpers."""

    @staticmethod
    def _observability(user_request: UserRequest) -> ObservabilityContext | None:
        """Return the request-scoped observability context when available."""

        observability = user_request.safety_context.get("observability")
        if isinstance(observability, ObservabilityContext):
            return observability
        return None

    def _runtime_state_snapshot(self) -> dict[str, Any]:
        """Return the current runtime-owned introspection state."""

        return {
            "last_plan": self.last_plan_summary,
            "last_failure": self.last_failure_summary,
        }

    def _assert_registry_consistency(self) -> None:
        """Ensure planning and execution use the same canonical capability contracts."""

        errors = validate_registry_consistency(self.registry, self.execution_engine.registry)
        if errors:
            raise ValidationError(
                "Capability registry drift detected between planning and execution: "
                + "; ".join(errors[:8])
            )

    def _record_last_failure(
        self,
        *,
        request_id: str,
        prompt: str,
        category: str,
        stage: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist a safe summary of the most recent failure."""

        self.last_failure_summary = {
            "request_id": request_id,
            "prompt": prompt,
            "category": category,
            "stage": stage,
            "reason": reason,
            "metadata": dict(metadata or {}),
        }

    def _record_last_plan(
        self,
        *,
        user_request: UserRequest,
        tasks,
        selections,
        dag: ActionDAG,
        safety_decision,
    ) -> None:
        """Persist a safe summary of the most recent trusted plan."""

        rows = [
            {
                "node_id": node.id,
                "task_id": node.task_id,
                "capability_id": node.capability_id,
                "operation_id": node.operation_id,
                "depends_on": ", ".join(node.depends_on) if node.depends_on else "-",
            }
            for node in dag.nodes
        ]
        self.last_plan_summary = {
            "request_id": user_request.request_id,
            "prompt": user_request.raw_prompt,
            "task_count": len(tasks),
            "tasks": [
                {
                    "task_id": task.id,
                    "description": task.description,
                    "semantic_verb": task.semantic_verb,
                    "object_type": task.object_type,
                    "operation_intent": task.operation_intent,
                    "side_effect_type": task.side_effect_type,
                    "dependencies": list(task.dependencies),
                }
                for task in tasks
            ],
            "selected_capabilities": [
                {
                    "task_id": selection.task_id,
                    "capability_id": (
                        selection.selected.capability_id if selection.selected is not None else None
                    ),
                    "operation_id": (
                        selection.selected.operation_id if selection.selected is not None else None
                    ),
                    "unresolved_reason": selection.unresolved_reason,
                }
                for selection in selections
            ],
            "dag_id": dag.dag_id,
            "rows": rows,
            "safety_decision": {
                "allowed": safety_decision.allowed,
                "requires_confirmation": safety_decision.requires_confirmation,
                "blocked_reasons": list(safety_decision.blocked_reasons),
                "warnings": list(safety_decision.warnings),
            },
        }

    def _bind_trace_to_request(self, user_request: UserRequest) -> PlanningTrace:
        """Attach a request-scoped planning trace to the user request."""

        trace = user_request.safety_context.get("planning_trace")
        if not isinstance(trace, PlanningTrace):
            trace = PlanningTrace(
                request_id=user_request.request_id,
                raw_prompt=user_request.raw_prompt,
                capability_manifest_hash=registry_contract_hash(self.registry),
            )
            user_request.safety_context["planning_trace"] = trace
        trace.request_id = user_request.request_id
        trace.raw_prompt = user_request.raw_prompt
        trace.capability_manifest_hash = registry_contract_hash(self.registry)
        trace.capability_manifest_version = trace.capability_manifest_hash
        trace.metadata["canonical_capability_registry"] = {
            "contract_hash": trace.capability_manifest_hash,
            "capability_ids": registry_capability_ids(self.registry),
        }
        trace.metadata["execution_capability_registry"] = {
            "contract_hash": registry_contract_hash(self.execution_engine.registry),
            "capability_ids": registry_capability_ids(self.execution_engine.registry),
        }
        request_config = self._runtime_config_for_context(user_request.session_context)
        profile_policy = operator_profile_policy(
            request_config.reasoning_profile,
            llm_operator_verbose_enabled=request_config.llm_operator_verbose_enabled,
        )
        trace.metadata["llm_operator_verbose_enabled"] = bool(
            request_config.llm_operator_verbose_enabled
        )
        trace.metadata["effective_llm_operator_verbose_enabled"] = (
            profile_policy.contract_mode == "verbose"
        )
        trace.metadata["llm_contract_mode"] = profile_policy.contract_mode
        trace.metadata["operator_profile_policy"] = {
            "contract_mode": profile_policy.contract_mode,
            "include_rationales": profile_policy.include_rationales,
            "run_decomposition_critique": profile_policy.run_decomposition_critique,
            "run_semantic_verb_llm": profile_policy.run_semantic_verb_llm,
            "run_plan_review": profile_policy.run_plan_review,
            "run_answer_coverage": profile_policy.run_answer_coverage,
            "run_final_formatter": profile_policy.run_final_formatter,
            "run_self_brief": profile_policy.run_self_brief,
        }
        trace.metadata["operator_policy_profile"] = request_config.operator_policy_profile
        trace.metadata["reasoning_profile"] = request_config.reasoning_profile
        trace.metadata["repair_profile"] = request_config.repair_profile
        trace.metadata["workflow_execution_mode"] = request_config.workflow_execution_mode
        trace.metadata["effective_workflow_execution_mode"] = (
            "streaming"
            if workflow_uses_streaming(request_config.workflow_execution_mode)
            else "full_plan"
        )
        trace.metadata["operator_execution_mode"] = trace.metadata[
            "effective_workflow_execution_mode"
        ]
        trace.metadata["shell_input_bindings_mode"] = request_config.shell_input_bindings_mode
        trace.metadata["response_streaming_enabled"] = bool(
            request_config.response_streaming_enabled
        )
        self.last_planning_trace = trace
        return trace

    def _record_prompt_rephrase_entry(
        self,
        user_request: UserRequest,
        trace: PlanningTrace,
        llm_client: Any,
        outcome: PromptRephraseOutcome,
    ) -> None:
        """Append the preflight prompt normalization attempt to the planning trace."""

        model_name, temperature = llm_client_metadata(llm_client)
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="prompt_rephrase",
                request_id=user_request.request_id,
                model_name=model_name,
                llm_temperature=temperature,
                prompt_template_id="input.prompt_rephrase",
                raw_llm_response=(
                    outcome.proposal.model_dump(mode="json")
                    if outcome.proposal is not None
                    else None
                ),
                parsed_proposal=(
                    outcome.proposal.model_dump(mode="json")
                    if outcome.proposal is not None
                    else None
                ),
                selected_candidate={
                    "rephrased_prompt": outcome.rephrased_prompt,
                    "applied": outcome.applied,
                    "changed": outcome.changed,
                    "confidence": outcome.confidence,
                    "reason": outcome.reason,
                    "fallback_reason": outcome.fallback_reason,
                },
                deterministic_normalizations=(
                    [f"prompt_rephrase_fallback:{outcome.fallback_reason}"]
                    if outcome.fallback_reason
                    else ["prompt_rephrase_applied"]
                    if outcome.applied
                    else []
                ),
            ),
        )

    def _maybe_rephrase_user_request(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        llm_client: Any,
        observability: ObservabilityContext,
        trace: PlanningTrace,
    ) -> UserRequest:
        """Normalize one new prompt before semantic planning when the public setting allows it."""

        original_prompt = user_request.raw_prompt
        request_config = self._runtime_config_for_context(user_request.session_context)
        enabled = effective_prompt_rephrase_enabled(request_config)
        trace.metadata["original_user_prompt"] = original_prompt
        trace.metadata["prompt_rephrase_enabled"] = enabled
        user_request.session_context["original_user_prompt"] = original_prompt
        user_request.session_context["prompt_rephrase_enabled"] = enabled
        if not enabled:
            trace.metadata["prompt_rephrase"] = {
                "enabled": False,
                "original_prompt": original_prompt,
                "rephrased_prompt": original_prompt,
                "changed": False,
                "confidence": None,
                "fallback_reason": "disabled",
            }
            observability.info(
                STAGE_PROMPT_REPHRASE,
                "prompt_rephrase.skipped",
                "Prompt rephrase skipped",
                "Prompt rephrasing is disabled for this request.",
                details=trace.metadata["prompt_rephrase"],
                debug_only=True,
            )
            return user_request

        outcome = rephrase_prompt_preflight(original_prompt, llm_client)
        trace.metadata["prompt_rephrase"] = {
            "enabled": True,
            "original_prompt": original_prompt,
            "rephrased_prompt": outcome.rephrased_prompt,
            "changed": outcome.changed,
            "applied": outcome.applied,
            "confidence": outcome.confidence,
            "reason": outcome.reason,
            "fallback_reason": outcome.fallback_reason,
        }
        self._record_prompt_rephrase_entry(user_request, trace, llm_client, outcome)
        event_details = {
            "enabled": True,
            "changed": outcome.changed,
            "applied": outcome.applied,
            "confidence": outcome.confidence,
            "fallback_reason": outcome.fallback_reason,
            "original_prompt": original_prompt,
            "rephrased_prompt": outcome.rephrased_prompt,
            "original_prompt_preview": original_prompt[:240],
            "rephrased_prompt_preview": outcome.rephrased_prompt[:240],
        }
        if not outcome.applied:
            observability.info(
                STAGE_PROMPT_REPHRASE,
                "prompt_rephrase.fallback",
                "Prompt rephrase fallback",
                "The runtime kept the original prompt for semantic planning.",
                details=event_details,
                debug_only=True,
            )
            return user_request

        user_request = user_request.model_copy(
            update={"raw_prompt": outcome.rephrased_prompt}
        )
        trace.raw_prompt = outcome.rephrased_prompt
        user_request.safety_context["planning_trace"] = trace
        request_context["original_user_prompt"] = original_prompt
        request_context["prompt_rephrase_enabled"] = enabled
        request_context["prompt_rephrase_applied"] = True
        observability.info(
            STAGE_PROMPT_REPHRASE,
            "prompt_rephrase.applied",
            "Prompt rephrased",
            "The runtime normalized the prompt before semantic planning.",
            details=event_details,
            debug_only=True,
        )
        return user_request
