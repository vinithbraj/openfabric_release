"""Composed LLM operator pipeline class."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator._shared import *
from agent_runtime.operator.events import *
from agent_runtime.operator.utils import *
from agent_runtime.operator.stdout_classification import *
from agent_runtime.operator.shell_bindings import *
from agent_runtime.operator.prompts import *
from agent_runtime.operator.validation import OperatorPlanValidator, PlanValidationMixin
from agent_runtime.operator.memory_compliance import MemoryComplianceMixin
from agent_runtime.operator.clarification import ClarificationMixin
from agent_runtime.operator.repair_control import RepairControlMixin
from agent_runtime.operator.step_runner import PythonTransformExecutor, StepRunnerMixin
from agent_runtime.settings_consolidation import effective_prompt_rephrase_enabled

class LLMOperatorPipeline(
    PlanValidationMixin,
    MemoryComplianceMixin,
    ClarificationMixin,
    RepairControlMixin,
    StepRunnerMixin,
):
    """Parallel LLM-authored, runtime-validated operator pipeline."""

    def __init__(
        self,
        *,
        llm_client: Any,
        gateway_client: GatewayClient,
        config: RuntimeConfig,
        result_store: InMemoryResultStore,
        memory_store: AgentMemoryStore | None = None,
        plan_cache_store: AgentPlanCacheStore | None = None,
        command_template_cache_store: AgentCommandTemplateCacheStore | None = None,
        computation_cache_store: AgentComputationCacheStore | None = None,
        reliability_store: AgentReliabilityStore | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.gateway_client = gateway_client
        self.config = config
        self.result_store = result_store
        self.memory_store = memory_store
        self.plan_cache_store = plan_cache_store
        self.command_template_cache_store = command_template_cache_store
        self.computation_cache_store = computation_cache_store
        self.reliability_store = reliability_store
        self.reliability = ReliabilityController(
            store=reliability_store,
            model_id=model_id_from_client(llm_client),
            mode=config.reliability_mode,
            max_recovery_probes=config.reliability_max_recovery_probes,
            max_autonomous_repair_attempts=config.reliability_max_autonomous_repair_attempts,
            weak_model_plan_action_cap=config.reliability_weak_model_plan_action_cap,
            verifier_enforced=config.reliability_verifier_enforced,
            approval_envelope_budget=config.reliability_approval_envelope_budget,
        )
        self.validator = OperatorPlanValidator(config)
        self.python_executor = PythonTransformExecutor()

    @staticmethod
    def _llm_chain(client: Any) -> list[Any]:
        """Return wrapper chain objects from outermost client to innermost client."""

        chain: list[Any] = []
        seen: set[int] = set()
        current = client
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            chain.append(current)
            current = getattr(current, "inner", None)
        return chain

    @classmethod
    def _call_with_max_tokens(
        cls,
        client: Any,
        prompt: str,
        model_type: type[Any],
        *,
        max_tokens: int,
    ) -> Any:
        """Run one structured call with a temporary max-token cap when supported."""

        targets: list[tuple[Any, Any]] = []
        for item in cls._llm_chain(client):
            attrs = getattr(item, "__dict__", {})
            if isinstance(attrs, dict) and "max_tokens" in attrs:
                previous = attrs.get("max_tokens")
                try:
                    effective = int(max_tokens)
                    if previous is not None:
                        effective = min(effective, int(previous))
                    setattr(item, "max_tokens", max(1, effective))
                    targets.append((item, previous))
                except (TypeError, ValueError):
                    continue
        try:
            return structured_call(client, prompt, model_type)
        finally:
            for item, previous in reversed(targets):
                setattr(item, "max_tokens", previous)

    @staticmethod
    def _attempt_count(repair_attempts: int) -> int:
        """Return total tries: the first attempt plus configured repair attempts."""

        return max(1, int(repair_attempts) + 1)

    @staticmethod
    def _emit(
        observability: ObservabilityContext | None,
        *,
        level: str,
        event_type: str,
        title: str,
        summary: str,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if observability is None:
            return
        emitter = getattr(observability, level, observability.info)
        debug_only = not str(event_type).startswith("operator.tryout.")
        emitter(OPERATOR_STAGE, event_type, title, summary, details or {}, debug_only=debug_only)
        if error and level != "error":
            observability.error(
                OPERATOR_STAGE,
                event_type,
                title,
                summary,
                details or {},
                debug_only=debug_only,
            )

    def _auto_rephrase_retry_enabled(self, user_request: UserRequest) -> bool:
        enabled = effective_prompt_rephrase_enabled(self.config)
        metadata = _operator_planning_trace_metadata(user_request)
        if metadata is not None:
            metadata["operator_auto_rephrase_retry_enabled"] = enabled
            metadata["prompt_rephrase_enabled"] = enabled
        return enabled

    @staticmethod
    def _auto_rephrase_retry_count(user_request: UserRequest) -> int:
        raw_count = dict(user_request.session_context or {}).get("operator_auto_rephrase_retry_count", 0)
        try:
            return max(0, int(raw_count))
        except (TypeError, ValueError):
            return 0

    def _append_auto_rephrase_retry_trace(
        self,
        user_request: UserRequest,
        *,
        attempt: int,
        source: str,
        validation_errors: list[dict[str, Any]],
        proposal: OperatorRephraseRetryProposal,
    ) -> int | None:
        metadata = _operator_planning_trace_metadata(user_request)
        if metadata is None:
            return None
        metadata["operator_auto_rephrase_retry_status"] = "disabled"
        retries = list(metadata.get("operator_auto_rephrase_retries") or [])
        entry = {
            "attempt": attempt,
            "source": source,
            "status": "started",
            "original_prompt": user_request.raw_prompt,
            "rephrased_prompt": proposal.rephrased_prompt,
            "reason": proposal.reason,
            "confidence": proposal.confidence,
            "validation_errors": list(validation_errors),
        }
        retries.append(entry)
        metadata["operator_auto_rephrase_retries"] = retries
        return len(retries) - 1

    def _mark_auto_rephrase_retry_trace(
        self,
        user_request: UserRequest,
        *,
        status: str,
        validation_errors: list[dict[str, Any]] | None = None,
    ) -> None:
        metadata = _operator_planning_trace_metadata(user_request)
        if metadata is None:
            return
        retries = list(metadata.get("operator_auto_rephrase_retries") or [])
        raw_index = dict(user_request.session_context or {}).get(
            "operator_auto_rephrase_retry_trace_index"
        )
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            index = len(retries) - 1
        if 0 <= index < len(retries):
            entry = dict(retries[index])
            entry["status"] = status
            if validation_errors is not None:
                entry["retry_validation_errors"] = list(validation_errors)
            retries[index] = entry
            metadata["operator_auto_rephrase_retries"] = retries
        metadata["operator_auto_rephrase_retry_status"] = status

    def _auto_rephrase_retry_request(
        self,
        user_request: UserRequest,
        validation_errors: list[dict[str, Any]],
        observability: ObservabilityContext | None,
        *,
        source: str,
    ) -> UserRequest | None:
        if not self._auto_rephrase_retry_enabled(user_request):
            metadata = _operator_planning_trace_metadata(user_request)
            if metadata is not None:
                metadata["operator_auto_rephrase_retry_status"] = "skipped_disabled"
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_AUTO_REPHRASE_RETRY,
                title="Operator auto rephrase skipped",
                summary="Auto retry by rephrasing is disabled for this request.",
                details={"source": source, "validation_errors": validation_errors},
            )
            return None
        count = self._auto_rephrase_retry_count(user_request)
        if count >= 1:
            metadata = _operator_planning_trace_metadata(user_request)
            if metadata is not None:
                metadata["operator_auto_rephrase_retry_status"] = "skipped_attempt_limit"
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_AUTO_REPHRASE_RETRY,
                title="Operator auto rephrase skipped",
                summary="Auto retry by rephrasing already ran once for this request.",
                details={"source": source, "retry_count": count, "validation_errors": validation_errors},
            )
            return None
        try:
            proposal = structured_call(
                self.llm_client,
                build_operator_rephrase_retry_prompt(user_request, validation_errors),
                OperatorRephraseRetryProposal,
            )
        except (PydanticValidationError, StructuredCallError) as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_AUTO_REPHRASE_RETRY,
                title="Operator auto rephrase skipped",
                summary="The LLM could not produce a valid one-shot rephrase retry proposal.",
                details={"source": source, "error": str(exc), "validation_errors": validation_errors},
            )
            metadata = _operator_planning_trace_metadata(user_request)
            if metadata is not None:
                metadata["operator_auto_rephrase_retry_status"] = "skipped_invalid_proposal"
            return None
        rephrased_prompt = str(proposal.rephrased_prompt or "").strip()
        original_prompt = str(user_request.raw_prompt or "").strip()
        if not rephrased_prompt or rephrased_prompt == original_prompt:
            metadata = _operator_planning_trace_metadata(user_request)
            if metadata is not None:
                metadata["operator_auto_rephrase_retry_status"] = "skipped_unchanged"
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_AUTO_REPHRASE_RETRY,
                title="Operator auto rephrase skipped",
                summary="The rephrased prompt was empty or unchanged.",
                details={
                    "source": source,
                    "reason": proposal.reason,
                    "confidence": proposal.confidence,
                },
            )
            return None
        trace_index = self._append_auto_rephrase_retry_trace(
            user_request,
            attempt=count + 1,
            source=source,
            validation_errors=validation_errors,
            proposal=proposal,
        )
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_AUTO_REPHRASE_RETRY,
            title="Operator auto rephrase retry started",
            summary="Planning validation failed after normal repairs, so the current request will be retried once with a clearer equivalent prompt.",
            details={
                "source": source,
                "attempt": count + 1,
                "original_prompt": user_request.raw_prompt,
                "rephrased_prompt": rephrased_prompt,
                "reason": proposal.reason,
                "confidence": proposal.confidence,
                "validation_errors": validation_errors,
            },
        )
        session_context = dict(user_request.session_context or {})
        session_context["operator_auto_rephrase_retry_count"] = count + 1
        session_context["operator_auto_rephrase_original_prompt"] = user_request.raw_prompt
        session_context["operator_auto_rephrase_rephrased_prompt"] = rephrased_prompt
        session_context["operator_auto_rephrase_reason"] = proposal.reason
        if trace_index is not None:
            session_context["operator_auto_rephrase_retry_trace_index"] = trace_index
        for stale_key in (
            "operator_self_brief",
            "operator_self_brief_skipped",
            "guided_deliberation_frame",
        ):
            session_context.pop(stale_key, None)
        return user_request.model_copy(
            update={
                "raw_prompt": rephrased_prompt,
                "session_context": session_context,
                "safety_context": dict(user_request.safety_context or {}),
            }
        )


__all__ = ["LLMOperatorPipeline"]
