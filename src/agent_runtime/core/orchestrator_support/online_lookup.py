"""Online lookup and AI-check helpers."""

from __future__ import annotations

import sys

from .common import *
from .formatting import *


class _OnlineLookupMixin:
    """Online lookup and AI-check helpers."""

    @staticmethod
    def _task_text_for_online_lookup(task: dict[str, Any]) -> str:
        return " ".join(
            [
                str(task.get("description") or ""),
                str(task.get("semantic_verb") or ""),
                str(task.get("object_type") or ""),
                str(dict(task.get("constraints") or {})),
            ]
        ).lower()

    @classmethod
    def _task_has_online_lookup_intent(cls, task: dict[str, Any]) -> bool:
        text = cls._task_text_for_online_lookup(task)
        markers = (
            "/checkonline",
            "check online",
            "online",
            "web",
            "internet",
            "google",
            "lookup",
            "look up",
            "research",
            "current documentation",
            "latest documentation",
        )
        return any(marker in text for marker in markers)

    @classmethod
    def _task_has_online_ai_check_intent(cls, task: dict[str, Any]) -> bool:
        text = cls._task_text_for_online_lookup(task)
        markers = (
            "/checkonlineai",
            "check online ai",
            "online ai",
            "ai overview",
            "google ai overview",
            "google ai",
        )
        return any(marker in text for marker in markers)

    @classmethod
    def _streaming_task_should_lookup_online(
        cls,
        state: dict[str, Any],
        task: dict[str, Any],
    ) -> bool:
        if not bool(state.get("online_lookup_requested")):
            return False
        task_id = str(task.get("task_id") or "")
        used_task_ids = {
            str(item or "")
            for item in list(state.get("online_lookup_task_ids") or [])
            if str(item or "").strip()
        }
        if task_id and task_id in used_task_ids:
            return False
        if bool(state.get("online_lookup_mode_enabled")):
            return True
        tasks = [item for item in list(state.get("tasks") or []) if isinstance(item, dict)]
        current_index = int(state.get("current_index") or 0)
        if cls._task_has_online_lookup_intent(task):
            return True
        future_has_marker = any(
            cls._task_has_online_lookup_intent(dict(candidate))
            for candidate in tasks[current_index + 1 :]
        )
        return not used_task_ids and not future_has_marker

    @classmethod
    def _streaming_task_should_lookup_online_ai(
        cls,
        state: dict[str, Any],
        task: dict[str, Any],
    ) -> bool:
        if not bool(state.get("online_ai_check_requested")):
            return False
        task_id = str(task.get("task_id") or "")
        used_task_ids = {
            str(item or "")
            for item in list(state.get("online_ai_check_task_ids") or [])
            if str(item or "").strip()
        }
        if task_id and task_id in used_task_ids:
            return False
        tasks = [item for item in list(state.get("tasks") or []) if isinstance(item, dict)]
        current_index = int(state.get("current_index") or 0)
        if cls._task_has_online_ai_check_intent(task):
            return True
        future_has_marker = any(
            cls._task_has_online_ai_check_intent(dict(candidate))
            for candidate in tasks[current_index + 1 :]
        )
        return not used_task_ids and not future_has_marker

    @classmethod
    def _is_pure_online_ai_lookup_task(cls, task: Any) -> bool:
        description = str(
            task.get("description") if isinstance(task, dict) else getattr(task, "description", "")
        ).strip().lower()
        if not description or not cls._task_has_online_ai_check_intent({"description": description}):
            return False
        action_markers = (
            "install",
            "remove",
            "delete",
            "start",
            "stop",
            "restart",
            "write",
            "create",
            "update",
            "commit",
            "push",
            "execute",
            "run ",
            "download",
            "copy",
            "move",
            "rename",
            "compose",
            "shutdown",
            "kill",
        )
        if any(marker in description for marker in action_markers):
            return False
        lookup_only_markers = (
            "provided query",
            "status",
            "service",
            "lookup",
            "context",
            "check online ai",
            "online ai",
            "ai overview",
        )
        return any(marker in description for marker in lookup_only_markers)

    @classmethod
    def _strip_pure_online_ai_lookup_tasks(cls, tasks: list[TaskFrame]) -> list[TaskFrame]:
        if len(tasks) < 2:
            return tasks
        filtered = [task for task in tasks if not cls._is_pure_online_ai_lookup_task(task)]
        return filtered or tasks

    @staticmethod
    def _streaming_online_lookup_query(state: dict[str, Any], task: dict[str, Any]) -> str:
        description = str(task.get("description") or "").strip()
        original = str(state.get("original_prompt") or "").strip()
        if description and original and description.lower() not in original.lower():
            return f"{description}. Original request: {original}"
        return description or original

    @staticmethod
    def _task_online_lookup_query(original_prompt: str, task: TaskFrame, *, index: int) -> str:
        description = str(task.description or "").strip()
        semantic = str(task.semantic_verb or "").strip()
        object_type = str(task.object_type or "").strip()
        parts = [f"Task {index + 1}: {description}" if description else f"Task {index + 1}"]
        if semantic or object_type:
            parts.append(f"Intent: {semantic} {object_type}".strip())
        original = str(original_prompt or "").strip()
        if original and description.lower() not in original.lower():
            parts.append(f"Original request: {original}")
        return ". ".join(part for part in parts if part)

    @staticmethod
    def _online_lookup_payload_with_scope(
        payload: dict[str, Any],
        *,
        source: str,
        task_id: str = "",
        step_index: int | None = None,
        step_id: str = "",
    ) -> dict[str, Any]:
        scoped = {**dict(payload), "source": source}
        if task_id:
            scoped["task_id"] = task_id
        if step_id:
            scoped["streaming_step_id"] = step_id
        if step_index is not None:
            scoped["streaming_step_index"] = step_index
        return scoped

    @staticmethod
    def _explicit_online_ai_query_from_context(*contexts: dict[str, Any] | None) -> str:
        for context in contexts:
            summaries = dict(context or {}).get(USER_MACRO_SUMMARY_CONTEXT_KEY)
            if not isinstance(summaries, list):
                continue
            for item in summaries:
                if not isinstance(item, dict):
                    continue
                if str(item.get("kind") or "").strip().lower() != CHECKONLINEAI_MACRO_KIND:
                    continue
                query = sanitize_online_ai_check_query(str(item.get("query") or ""))
                if query:
                    return query
        return ""

    def _perform_online_lookup_for_context(
        self,
        *,
        query: str,
        observability: ObservabilityContext,
        summary_scope: str,
        source: str,
        task_id: str = "",
        step_index: int | None = None,
        step_id: str = "",
    ) -> dict[str, Any]:
        start_details: dict[str, Any] = {
            "provider": COMBINED_ONLINE_LOOKUP_PROVIDER,
            "query": sanitize_online_lookup_query(query)[:500],
            "source": source,
        }
        if task_id:
            start_details["task_id"] = task_id
        if step_id:
            start_details["streaming_step_id"] = step_id
        if step_index is not None:
            start_details["streaming_step_index"] = step_index
        observability.info(
            OPERATOR_STAGE,
            "operator.online_lookup.started",
            "Online lookup started",
            f"The runtime is fetching one compact online answer for {summary_scope}.",
            start_details,
        )
        lookup_config = self.execution_engine.safety_policy.config
        facade = sys.modules.get("agent_runtime.core.orchestrator")
        lookup = getattr(facade, "lookup_online_answer", lookup_online_answer)
        result = lookup(
            query,
            timeout_seconds=float(getattr(lookup_config, "online_ai_check_timeout_seconds", 120.0)),
            gemini_api_key=getattr(lookup_config, "online_lookup_gemini_api_key", ""),
            gemini_model=getattr(lookup_config, "online_lookup_gemini_model", ""),
            gemini_api_version=getattr(lookup_config, "online_lookup_gemini_api_version", ""),
            duck_ai_reuse_browser=bool(getattr(lookup_config, "online_ai_check_reuse_browser", True)),
            duck_ai_headless=bool(getattr(lookup_config, "online_ai_check_headless", False)),
            duck_ai_profile_dir=str(getattr(lookup_config, "online_ai_check_profile_dir", "")),
        )
        payload = self._online_lookup_payload_with_scope(
            result.to_context(),
            source=source,
            task_id=task_id,
            step_index=step_index,
            step_id=step_id,
        )
        event_details: dict[str, Any] = {
            "provider": payload.get("provider"),
            "query": str(payload.get("query") or "")[:500],
            "available": bool(payload.get("available")),
            "source_title": str(payload.get("source_title") or "")[:240],
            "source_url": str(payload.get("source_url") or "")[:2000],
            "fetched_at": payload.get("fetched_at"),
            "error": str(payload.get("error") or "")[:300],
            "answer_preview": str(payload.get("answer_text") or "")[:500],
            "source": source,
        }
        provider_results = payload.get("provider_results")
        if isinstance(provider_results, list):
            event_details["provider_results"] = provider_results[:4]
        if task_id:
            event_details["task_id"] = task_id
        if step_id:
            event_details["streaming_step_id"] = step_id
        if step_index is not None:
            event_details["streaming_step_index"] = step_index
        if result.available:
            observability.info(
                OPERATOR_STAGE,
                "operator.online_lookup.completed",
                "Online lookup completed",
                f"A compact online answer was attached to {summary_scope}.",
                event_details,
            )
        else:
            observability.warning(
                OPERATOR_STAGE,
                "operator.online_lookup.failed",
                "Online lookup unavailable",
                f"Online lookup failed; {summary_scope} will continue without claiming online context.",
                event_details,
            )
        return payload

    @staticmethod
    def _online_ai_check_event_details(
        payload: dict[str, Any],
        *,
        task_id: str = "",
        step_index: int | None = None,
        step_id: str = "",
        status: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {
            "provider": payload.get("provider") or ONLINE_AI_CHECK_PROVIDER,
            "query": str(payload.get("query") or "")[:500],
            "available": bool(payload.get("available")),
            "source_title": str(payload.get("source_title") or "")[:240],
            "source_url": str(payload.get("source_url") or payload.get("search_url") or "")[:2000],
            "search_url": str(payload.get("search_url") or "")[:2000],
            "fetched_at": payload.get("fetched_at"),
            "error": str(payload.get("error") or "")[:300],
            "answer_preview": str(payload.get("answer_text") or "")[:2400],
            "status": str(status or payload.get("status") or "")[:80],
            "source": "checkonlineai",
        }
        if message:
            details["message"] = str(message)[:300]
        if task_id:
            details["task_id"] = task_id
        if step_id:
            details["streaming_step_id"] = step_id
        if step_index is not None:
            details["streaming_step_index"] = step_index
        return details

    def _perform_online_ai_check_for_context(
        self,
        *,
        query: str,
        observability: ObservabilityContext,
        summary_scope: str,
        task_id: str = "",
        step_index: int | None = None,
        step_id: str = "",
    ) -> dict[str, Any]:
        lookup_config = self.execution_engine.safety_policy.config
        clean_query = sanitize_online_ai_check_query(query)
        seed_payload = {
            "provider": ONLINE_AI_CHECK_PROVIDER,
            "query": clean_query[:500],
            "available": False,
            "source": "checkonlineai",
        }
        observability.info(
            OPERATOR_STAGE,
            "operator.online_ai_lookup.started",
            "AI online check started",
            f"The runtime is fetching Duck.ai context for {summary_scope}.",
            self._online_ai_check_event_details(
                seed_payload,
                task_id=task_id,
                step_index=step_index,
                step_id=step_id,
                status="init",
                message="Initializing Duck.ai lookup.",
            ),
        )

        def emit_status(status: str, message: str) -> None:
            observability.info(
                OPERATOR_STAGE,
                "operator.online_ai_lookup.status",
                "AI online check status",
                str(message or status),
                self._online_ai_check_event_details(
                    seed_payload,
                    task_id=task_id,
                    step_index=step_index,
                    step_id=step_id,
                    status=status,
                    message=message,
                ),
            )

        facade = sys.modules.get("agent_runtime.core.orchestrator")
        duck_lookup = getattr(facade, "lookup_duck_ai_answer", lookup_duck_ai_answer)
        result = duck_lookup(
            clean_query,
            timeout_seconds=float(getattr(lookup_config, "online_ai_check_timeout_seconds", 120.0)),
            reuse_browser=bool(getattr(lookup_config, "online_ai_check_reuse_browser", True)),
            headless=bool(getattr(lookup_config, "online_ai_check_headless", False)),
            profile_dir=str(getattr(lookup_config, "online_ai_check_profile_dir", "")),
            status_callback=emit_status,
        )
        payload = {
            **result.to_context(),
            "source": "checkonlineai",
        }
        if task_id:
            payload["task_id"] = task_id
        if step_id:
            payload["streaming_step_id"] = step_id
        if step_index is not None:
            payload["streaming_step_index"] = step_index
        details = self._online_ai_check_event_details(
            payload,
            task_id=task_id,
            step_index=step_index,
            step_id=step_id,
            status="completed" if result.available else "failed",
        )
        if result.available:
            observability.info(
                OPERATOR_STAGE,
                "operator.online_ai_lookup.completed",
                "AI online check completed",
                f"Duck.ai context was attached to {summary_scope}.",
                details,
            )
        else:
            observability.warning(
                OPERATOR_STAGE,
                "operator.online_ai_lookup.failed",
                "AI online check unavailable",
                f"Duck.ai context was unavailable; {summary_scope} will continue without claiming it succeeded.",
                details,
            )
        return payload

    def _attach_online_ai_direct_lookup(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        observability: ObservabilityContext,
    ) -> None:
        if not (
            online_ai_check_requested_from_context(request_context)
            or online_ai_check_requested_from_context(user_request.session_context)
        ):
            return
        if user_request.session_context.get(ONLINE_AI_CHECK_CONTEXT_KEY):
            return
        explicit_query = self._explicit_online_ai_query_from_context(
            request_context,
            user_request.session_context,
        )
        payload = self._perform_online_ai_check_for_context(
            query=explicit_query or sanitize_online_ai_check_query(user_request.raw_prompt),
            observability=observability,
            summary_scope="this request",
        )
        request_context[ONLINE_AI_CHECK_CONTEXT_KEY] = payload
        request_context[ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY] = True
        user_request.session_context[ONLINE_AI_CHECK_CONTEXT_KEY] = payload
        user_request.session_context[ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY] = True

    def _attach_online_mode_direct_lookup(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        observability: ObservabilityContext,
    ) -> None:
        return
        if user_request.session_context.get(ONLINE_LOOKUP_CONTEXT_KEY):
            return
        payload = self._perform_online_lookup_for_context(
            query=sanitize_online_lookup_query(user_request.raw_prompt),
            observability=observability,
            summary_scope="this direct answer request",
            source="online_mode",
        )
        request_context[ONLINE_LOOKUP_CONTEXT_KEY] = payload
        request_context[ONLINE_LOOKUP_CONTEXTS_KEY] = [payload]
        request_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] = True
        user_request.session_context[ONLINE_LOOKUP_CONTEXT_KEY] = payload
        user_request.session_context[ONLINE_LOOKUP_CONTEXTS_KEY] = [payload]
        user_request.session_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] = True

    def _attach_online_mode_task_lookups(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        observability: ObservabilityContext,
        tasks: list[TaskFrame],
    ) -> None:
        return
        if not tasks:
            self._attach_online_mode_direct_lookup(user_request, request_context, observability)
            return
        existing = [
            item
            for item in list(user_request.session_context.get(ONLINE_LOOKUP_CONTEXTS_KEY) or [])
            if isinstance(item, dict)
        ]
        existing_single = user_request.session_context.get(ONLINE_LOOKUP_CONTEXT_KEY)
        if isinstance(existing_single, dict):
            existing.append(dict(existing_single))
        seen_queries = {str(item.get("query") or "").strip().lower() for item in existing}
        results = list(existing)
        for index, task in enumerate(tasks):
            query = sanitize_online_lookup_query(
                self._task_online_lookup_query(user_request.raw_prompt, task, index=index)
            )
            if not query or query.lower() in seen_queries:
                continue
            seen_queries.add(query.lower())
            results.append(
                self._perform_online_lookup_for_context(
                    query=query,
                    observability=observability,
                    summary_scope=f"decomposed task {index + 1}",
                    source="online_mode",
                    task_id=str(task.id or ""),
                    step_index=index,
                )
            )
        request_context[ONLINE_LOOKUP_CONTEXTS_KEY] = results
        request_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] = True
        user_request.session_context[ONLINE_LOOKUP_CONTEXTS_KEY] = results
        user_request.session_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] = True

    def _attach_streaming_online_lookup(
        self,
        *,
        state: dict[str, Any],
        task: dict[str, Any],
        step_request: UserRequest,
        step_context: dict[str, Any],
        observability: ObservabilityContext,
    ) -> None:
        if not self._streaming_task_should_lookup_online(state, task):
            return
        current_index = int(state.get("current_index") or 0)
        task_id = str(task.get("task_id") or "")
        step_id = str(task.get("streaming_step_id") or "").strip()
        query = self._streaming_online_lookup_query(state, task)
        source = "online_mode" if bool(state.get("online_lookup_mode_enabled")) else "checkonline"
        payload = self._perform_online_lookup_for_context(
            query=query,
            observability=observability,
            summary_scope="this streaming step",
            source=source,
            task_id=task_id,
            step_index=current_index,
            step_id=step_id,
        )
        step_context[ONLINE_LOOKUP_CONTEXT_KEY] = payload
        step_context[ONLINE_LOOKUP_CONTEXTS_KEY] = [payload]
        step_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] = True
        step_request.session_context[ONLINE_LOOKUP_CONTEXT_KEY] = payload
        step_request.session_context[ONLINE_LOOKUP_CONTEXTS_KEY] = [payload]
        step_request.session_context[ONLINE_LOOKUP_REQUESTED_CONTEXT_KEY] = True
        used_task_ids = list(state.get("online_lookup_task_ids") or [])
        if task_id and task_id not in used_task_ids:
            used_task_ids.append(task_id)
        state["online_lookup_task_ids"] = used_task_ids
        state.setdefault("online_lookup_results", [])
        state["online_lookup_results"] = [
            *list(state.get("online_lookup_results") or []),
            {
                "task_id": task_id,
                "streaming_step_id": step_id,
                "streaming_step_index": current_index,
                **payload,
            },
        ]

    def _attach_streaming_online_ai_check(
        self,
        *,
        state: dict[str, Any],
        task: dict[str, Any],
        step_request: UserRequest,
        step_context: dict[str, Any],
        observability: ObservabilityContext,
    ) -> None:
        if not self._streaming_task_should_lookup_online_ai(state, task):
            return
        current_index = int(state.get("current_index") or 0)
        task_id = str(task.get("task_id") or "")
        step_id = str(task.get("streaming_step_id") or "").strip()
        query = (
            str(state.get("online_ai_check_query") or "").strip()
            or self._streaming_online_lookup_query(state, task)
        )
        payload = self._perform_online_ai_check_for_context(
            query=query,
            observability=observability,
            summary_scope="this streaming step",
            task_id=task_id,
            step_index=current_index,
            step_id=step_id,
        )
        step_context[ONLINE_AI_CHECK_CONTEXT_KEY] = payload
        step_context[ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY] = True
        step_request.session_context[ONLINE_AI_CHECK_CONTEXT_KEY] = payload
        step_request.session_context[ONLINE_AI_CHECK_REQUESTED_CONTEXT_KEY] = True
        used_task_ids = list(state.get("online_ai_check_task_ids") or [])
        if task_id and task_id not in used_task_ids:
            used_task_ids.append(task_id)
        state["online_ai_check_task_ids"] = used_task_ids
