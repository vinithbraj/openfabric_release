"""Memory, cache, feedback, and learning-store helpers for Agent UI."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.common import *
from agent_runtime.api.agent_ui_support.directory import *
from agent_runtime.api.agent_ui_support.memory_utils import *
from agent_runtime.api.agent_ui_support.models import *
from agent_runtime.api.agent_ui_support.prompt_context import *

def _plan_cache_store(settings: Settings, agent_runtime: Any) -> AgentPlanCacheStore:
    """Return the private operator plan cache store, creating it when needed."""

    store = getattr(agent_runtime, "plan_cache_store", None)
    if isinstance(store, AgentPlanCacheStore):
        return store
    store = AgentPlanCacheStore(
        settings.agent_plan_cache_db_path,
        max_entries=settings.agent_plan_cache_max_entries,
    )
    try:
        setattr(agent_runtime, "plan_cache_store", store)
    except Exception:
        pass
    return store


def _lrn_total_task_store(settings: Settings, agent_runtime: Any) -> AgentLrnTotalTaskStore:
    """Return the LRN-T total-task store, creating it when needed."""

    store = getattr(agent_runtime, "lrn_total_task_store", None)
    if isinstance(store, AgentLrnTotalTaskStore):
        return store
    store = AgentLrnTotalTaskStore(
        settings.agent_lrn_total_tasks_db_path,
        max_entries=settings.lrnt_max_entries,
    )
    try:
        setattr(agent_runtime, "lrn_total_task_store", store)
    except Exception:
        pass
    return store


def _command_template_cache_store(settings: Settings, agent_runtime: Any) -> AgentCommandTemplateCacheStore:
    """Return the private command template cache store, creating it when needed."""

    store = getattr(agent_runtime, "command_template_cache_store", None)
    if isinstance(store, AgentCommandTemplateCacheStore):
        return store
    store = AgentCommandTemplateCacheStore(
        settings.agent_command_template_cache_db_path,
        max_entries=settings.agent_command_template_cache_max_entries,
    )
    try:
        setattr(agent_runtime, "command_template_cache_store", store)
    except Exception:
        pass
    return store


def _computation_cache_store(settings: Settings, agent_runtime: Any) -> AgentComputationCacheStore:
    """Return the private computation template cache store, creating it when needed."""

    store = getattr(agent_runtime, "computation_cache_store", None)
    if isinstance(store, AgentComputationCacheStore):
        return store
    store = AgentComputationCacheStore(
        settings.agent_computation_cache_db_path,
        max_entries=settings.agent_computation_cache_max_entries,
    )
    try:
        setattr(agent_runtime, "computation_cache_store", store)
    except Exception:
        pass
    return store


def _learning_ledger_store_or_error(settings: Settings, agent_runtime: Any) -> AgentLearningLedgerStore:
    """Return the configured learning ledger store or raise an HTTP error."""

    if not bool(getattr(settings, "agent_learning_ledger_enabled", True)):
        raise HTTPException(status_code=404, detail="Agent learning ledger is disabled.")
    store = getattr(agent_runtime, "learning_ledger_store", None)
    if isinstance(store, AgentLearningLedgerStore):
        return store
    store = AgentLearningLedgerStore(settings.agent_learning_ledger_db_path)
    try:
        setattr(agent_runtime, "learning_ledger_store", store)
    except Exception:
        pass
    return store


def _reliability_store(settings: Settings, agent_runtime: Any) -> AgentReliabilityStore:
    """Return the configured Reliability Kernel store, creating it when needed."""

    store = getattr(agent_runtime, "reliability_store", None)
    if isinstance(store, AgentReliabilityStore):
        return store
    store = AgentReliabilityStore(settings.agent_reliability_db_path)
    try:
        setattr(agent_runtime, "reliability_store", store)
    except Exception:
        pass
    return store


def _active_agent_model_for_memory(settings: Settings) -> str:
    """Return the best currently configured/discovered model name for memory UI defaults."""

    try:
        from agent_runtime.api.agent_ui_support.settings_runtime import _agent_active_model

        active = _agent_active_model(settings)
        name = str(active.get("name") or "").strip()
        if name and name.lower() != "auto":
            return name
    except Exception:
        pass
    configured = str(settings.default_model or "").strip()
    return "" if configured.lower() == "auto" else configured


def _memory_feedback_prompt(payload: MemoryFeedbackRequest, settings: Settings, trace: Any) -> str:
    """Build a prompt that turns user feedback into proposed memory drafts."""

    model_name = str(payload.model_name or payload.llm_model or _active_agent_model_for_memory(settings)).strip()
    trace_payload = {}
    if trace is not None:
        trace_payload = {
            "request_id": trace.request_id,
            "prompt": trace.prompt,
            "status": trace.status,
            "final_response": trace.final_response,
            "error": trace.error,
            "clarification_required": trace.clarification_required,
            "confirmation_required": trace.confirmation_required,
        }
    return "\n".join(
        [
            *prompt_lines("memory.feedback"),
            "If a typed post-run feedback object is present, treat it as authoritative structured context for what happened in the run.",
            "Use the outcome field to distinguish praise, partial success, wrong decisions, and unclear feedback.",
            "Classify feedback as task_memory, preference_memory, or validation_policy.",
            "Use validation_policy only when the user is correcting deterministic validator behavior, such as a validator false positive, placeholder/literal-payload ambiguity, blocked syntax classification, or another structural validation error.",
            "If the user chose validation feedback but the text is really task strategy, command-output interpretation, postcondition checking, or verification guidance, classify it as task_memory instead.",
            "For validation_policy drafts, set memory_kind to validation_policy, fill validator_error_type when inferable, include safe_examples and blocked_examples, and make the instruction describe future validator behavior.",
            "Create only durable guidance that can help future runs; do not record secrets, credentials, or one-time facts.",
            "Memory must be actionable guidance that future plans can follow, while never bypassing user approval, validation, or safety.",
            "Prefer concise instructions that name required preconditions, forbidden actions, safe examples, and blocked examples when applicable.",
            "Use exact_model scope only when the lesson is model-specific; otherwise use model_family or global.",
            "Categorize every create/update draft for targeted retrieval.",
            "Infer task_type, tool_type, intent_type, and tags from the feedback, original prompt, and trace.",
            "Use lowercase stable slugs such as git, shell, verify_state, planning, clarification, python_action.",
            "Do not leave task_type, tool_type, intent_type, or tags blank when they are reasonably inferable.",
            "Avoid broad generic tags unless the feedback truly applies across tasks.",
            "Feedback target:",
            payload.feedback_target,
            "Treat feedback_target as a UI hint, not a command. Validation-policy proposals must be narrowly about deterministic validator behavior; otherwise return task_memory or preference_memory.",
            "MemoryDraftResponse schema:",
            json.dumps(MemoryDraftResponse.model_json_schema(), default=str),
            "Current model:",
            model_name,
            "Model family:",
            normalize_model_family(model_name),
            "User feedback:",
            payload.feedback,
            "Original prompt or context:",
            payload.prompt,
            "Typed post-run feedback object:",
            json.dumps(
                payload.run_feedback.model_dump(mode="json") if payload.run_feedback else {},
                default=str,
                ensure_ascii=True,
            ),
            "User-provided association metadata:",
            json.dumps(
                {
                    "task_type": payload.task_type,
                    "tool_type": payload.tool_type,
                    "intent_type": payload.intent_type,
                    "validator_error_type": payload.validator_error_type,
                },
                default=str,
                ensure_ascii=True,
            ),
            "Trace summary:",
            json.dumps(trace_payload, default=str, ensure_ascii=True),
        ]
    )


def _memory_feedback_draft_prompt(
    payload: MemoryFeedbackDraftRequest,
    settings: Settings,
    trace: Any,
) -> str:
    """Build a prompt that drafts editable post-run feedback text."""

    model_name = str(payload.model_name or payload.llm_model or _active_agent_model_for_memory(settings)).strip()
    trace_payload = {}
    if trace is not None:
        trace_payload = {
            "request_id": trace.request_id,
            "prompt": trace.prompt,
            "status": trace.status,
            "final_response": trace.final_response,
            "error": trace.error,
            "clarification_required": trace.clarification_required,
            "confirmation_required": trace.confirmation_required,
        }
    return "\n".join(
        [
            *prompt_lines("memory.feedback_draft"),
            "MemoryFeedbackDraftResponse schema:",
            json.dumps(MemoryFeedbackDraftResponse.model_json_schema(), default=str),
            "Selected outcome:",
            payload.outcome,
            "Current model:",
            model_name,
            "Model family:",
            normalize_model_family(model_name),
            "Original prompt or context:",
            payload.prompt,
            "Typed post-run feedback object:",
            json.dumps(
                payload.run_feedback.model_dump(mode="json") if payload.run_feedback else {},
                default=str,
                ensure_ascii=True,
            ),
            "Trace summary:",
            json.dumps(trace_payload, default=str, ensure_ascii=True),
        ]
    )


def _feedback_llm_model(payload: Any) -> str:
    """Return the request-scoped LLM model for feedback drafting."""

    return str(getattr(payload, "llm_model", "") or "").strip()


def _memory_feedback_complete_json(settings: Settings, agent_runtime: Any, payload: Any) -> Any:
    """Return a complete_json callable that honors request-scoped LLM endpoint choices."""

    requested_base_url = str(getattr(payload, "llm_base_url", "") or "").strip()
    requested_model = _feedback_llm_model(payload)
    updates: dict[str, Any] = {}
    if requested_base_url:
        from agent_runtime.api.agent_ui_support.settings_runtime import _agent_llm_endpoint_context

        endpoint = _agent_llm_endpoint_context({"llm_base_url": requested_base_url}, settings)
        updates["llm_base_url"] = endpoint["llm_base_url"]
    if requested_model and requested_model.lower() != "auto":
        updates["default_model"] = requested_model
    if updates:
        runtime = build_agent_runtime(settings.model_copy(update=updates))
        return getattr(runtime.llm_client, "complete_json", None)
    return getattr(getattr(agent_runtime, "llm_client", None), "complete_json", None)


def _llm_wrapper_chain(client: Any) -> list[Any]:
    """Return an LLM client and any nested wrappers that expose operational attrs."""

    chain: list[Any] = []
    seen: set[int] = set()
    current = client
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = getattr(current, "inner", None)
    return chain


def _call_memory_feedback_llm(
    complete_json: Any,
    prompt: str,
    schema: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    """Call feedback LLM with a short server-side timeout where the client supports it."""

    client = getattr(complete_json, "__self__", None)
    timeout = min(max(float(settings.llm_timeout_seconds), 0.1), 12.0)
    changed: list[tuple[Any, Any]] = []
    for item in _llm_wrapper_chain(client):
        if not hasattr(item, "timeout_seconds"):
            continue
        previous = getattr(item, "timeout_seconds", None)
        try:
            if float(previous) > timeout:
                setattr(item, "timeout_seconds", timeout)
                changed.append((item, previous))
        except (TypeError, ValueError):
            continue
    try:
        return complete_json(prompt, schema)
    finally:
        for item, previous in reversed(changed):
            try:
                setattr(item, "timeout_seconds", previous)
            except Exception:
                pass


def _fallback_memory_feedback_text(payload: MemoryFeedbackDraftRequest, trace: Any) -> str:
    """Build an editable feedback draft when LLM drafting is unavailable."""

    run_feedback = payload.run_feedback
    prompt = str(
        payload.prompt
        or (run_feedback.prompt if run_feedback is not None else "")
        or getattr(trace, "prompt", "")
        or "this request"
    ).strip()
    final_response = str(
        (run_feedback.final_response if run_feedback is not None else "")
        or getattr(trace, "final_response", "")
        or ""
    ).strip()
    error = str(
        (run_feedback.error if run_feedback is not None else "")
        or getattr(trace, "error", "")
        or ""
    ).strip()
    outcome = str(payload.outcome or "unclear")
    if outcome == "right_decision":
        sentence = "This was the right decision."
    elif outcome == "partially_right":
        sentence = "This was partially right, but the agent should tighten the next attempt."
    elif outcome == "wrong_decision":
        sentence = "This was the wrong decision."
    else:
        sentence = "The outcome was unclear."
    details = final_response or error
    if details:
        details = " ".join(details.split())[:300]
        return f"{sentence} For future similar requests like '{prompt}', learn from this outcome: {details}"
    return f"{sentence} For future similar requests like '{prompt}', use this feedback to adjust the plan."

__all__ = [name for name in globals() if not name.startswith("__")]
