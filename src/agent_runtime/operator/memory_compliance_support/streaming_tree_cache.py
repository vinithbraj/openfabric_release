"""Streaming step tree cache helpers for operator memory compliance."""

from __future__ import annotations

from agent_runtime.operator.memory_compliance_support.common import *
from agent_runtime.operator.memory_compliance_support.common import _MemoryComplianceCommonMixin


class _MemoryComplianceStreamingTreeCacheMixin(_MemoryComplianceCommonMixin):
    @staticmethod
    def _streaming_step_prompt_for_cache(user_request: UserRequest) -> str:
        """Return the exact rendered streaming-step prompt when available."""

        context = dict(user_request.session_context or {})
        cached_prompt = str(context.get("operator_streaming_step_prompt") or "").strip()
        if cached_prompt:
            return cached_prompt
        raw_prompt = str(user_request.raw_prompt or "")
        if raw_prompt.lstrip().startswith("Streaming step "):
            return raw_prompt
        current_task = context.get("operator_streaming_current_task")
        if not isinstance(current_task, dict):
            return raw_prompt
        tasks = [
            dict(item)
            for item in list(context.get("operator_streaming_tasks") or [])
            if isinstance(item, dict)
        ]
        if not tasks and "operator_streaming_current_index" not in context:
            return raw_prompt
        current_index = int(context.get("operator_streaming_current_index") or 0)
        total = len(tasks) or 1
        intent_block = context.get("operator_intent_block")
        global_constraints: dict[str, Any] = {}
        if isinstance(intent_block, dict) and isinstance(intent_block.get("global_constraints"), dict):
            global_constraints = dict(intent_block.get("global_constraints") or {})
        original_prompt = str(
            context.get("operator_streaming_original_prompt")
            or global_constraints.get("streaming_original_prompt")
            or raw_prompt
            or ""
        )
        later_tasks = [
            str(candidate.get("description") or "")
            for candidate in tasks[current_index + 1 :]
            if isinstance(candidate, dict) and str(candidate.get("description") or "").strip()
        ]
        step_id = str(
            current_task.get("streaming_step_id")
            or context.get("operator_streaming_step_id")
            or ""
        ).strip()
        lines = [
            f"Streaming step {current_index + 1} of {total}: {current_task.get('description') or ''}",
            "Complete only this decomposed step. This step is the only executable scope.",
            "Use the original request only as background for constraints and intent; do not perform later steps yet.",
            (
                "If this step filters, selects, sorts, or transforms prior output, produce only "
                "that narrowed result and do not execute the selected targets."
            ),
            f"Original request background: {original_prompt}",
        ]
        if step_id:
            lines.append(f"Streaming step id: {step_id}")
        online_query = str(context.get("online_ai_check_query") or "").strip()
        if online_query:
            lines.append(f"Explicit /checkonlineai lookup query for this step: {online_query}")
        if later_tasks:
            lines.append("Later steps reserved for future calls: " + " | ".join(later_tasks))
        return "\n".join(lines)

    def _streaming_step_tree_cache_enabled(self, user_request: UserRequest) -> bool:
        """Return whether regular LR can use exact streaming-step action trees."""

        if self.plan_cache_store is None or not bool(self.config.agent_plan_cache_enabled):
            return False
        if self._step_validation_repair_retry_active(user_request):
            return False
        if not self._lrdirect_streaming_task(user_request):
            return False
        context = dict(user_request.session_context or {})
        if "agent_plan_cache_enabled" in context:
            return bool(context.get("agent_plan_cache_enabled"))
        return True

    @staticmethod
    def _canonical_intent_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): _MemoryComplianceStreamingTreeCacheMixin._canonical_intent_value(value[key])
                for key in sorted(value)
                if str(key or "").strip()
            }
        if isinstance(value, (list, tuple, set)):
            return [_MemoryComplianceStreamingTreeCacheMixin._canonical_intent_value(item) for item in value]
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return value
        return str(value or "").strip()

    def _streaming_step_dependency_descriptions(
        self,
        user_request: UserRequest,
        current_task: dict[str, Any],
    ) -> list[str]:
        context = dict(user_request.session_context or {})
        dependencies = [str(item or "").strip() for item in list(current_task.get("dependencies") or [])]
        if not dependencies:
            return []
        tasks = [
            dict(item)
            for item in list(context.get("operator_streaming_tasks") or [])
            if isinstance(item, dict)
        ]
        description_by_id: dict[str, str] = {}
        for task in tasks:
            for key in ("task_id", "id"):
                task_id = str(task.get(key) or "").strip()
                if task_id:
                    description_by_id[task_id] = str(task.get("description") or task_id).strip()
        return [description_by_id.get(item, item) for item in dependencies]

    def _streaming_step_intent_snapshot(
        self,
        user_request: UserRequest,
        *,
        lookup: PlanCacheLookupContext | None = None,
    ) -> dict[str, Any]:
        """Capture stable intent metadata for exact streaming-step cache compatibility."""

        context = dict(user_request.session_context or {})
        current_task = self._lrdirect_streaming_task(user_request)
        lookup = lookup or self._plan_cache_context(user_request)
        classification: dict[str, Any] = {}
        intent_block = context.get("operator_intent_block")
        if isinstance(intent_block, dict) and isinstance(intent_block.get("classification"), dict):
            classification = dict(intent_block.get("classification") or {})
        raw_tags = [
            *list(lookup.tags or []),
            *[
                str(item)
                for item in list(classification.get("likely_domains") or [])
                if str(item or "").strip()
            ],
        ]
        tasks = [
            dict(item)
            for item in list(context.get("operator_streaming_tasks") or [])
            if isinstance(item, dict)
        ]
        global_constraints: dict[str, Any] = {}
        if isinstance(intent_block, dict) and isinstance(intent_block.get("global_constraints"), dict):
            global_constraints = dict(intent_block.get("global_constraints") or {})
        original_prompt = str(
            context.get("operator_streaming_original_prompt")
            or global_constraints.get("streaming_original_prompt")
            or user_request.raw_prompt
            or ""
        ).strip()
        snapshot = {
            "schema_version": 1,
            "step_description": str(current_task.get("description") or user_request.raw_prompt or "").strip(),
            "streaming_step_index": int(context.get("operator_streaming_current_index") or 0),
            "streaming_step_count": len(tasks) or 1,
            "original_prompt_shape": self._stable_intent_text(original_prompt),
            "semantic_verb": str(current_task.get("semantic_verb") or "").strip(),
            "object_type": str(current_task.get("object_type") or "").strip(),
            "constraints": self._canonical_intent_value(current_task.get("constraints") or {}),
            "dependency_descriptions": self._streaming_step_dependency_descriptions(user_request, current_task),
            "risk_level": str(current_task.get("risk_level") or "").strip(),
            "requires_confirmation": bool(current_task.get("requires_confirmation", False)),
            "operation_intent": str(current_task.get("operation_intent") or "").strip(),
            "side_effect_type": str(current_task.get("side_effect_type") or "").strip(),
            "mode": str(lookup.mode or "").strip(),
            "model_family": str(lookup.model_family or "").strip(),
            "task_type": str(lookup.task_type or "").strip(),
            "tool_type": str(lookup.tool_type or "").strip(),
            "intent_type": str(lookup.intent_type or "").strip(),
            "prompt_type": str(classification.get("prompt_type") or "").strip(),
            "tags": sorted({str(tag).strip().lower() for tag in raw_tags if str(tag).strip()}),
        }
        return self._canonical_intent_value(snapshot)

    @staticmethod
    def _stable_intent_text(value: Any) -> str:
        text = str(value or "").lower()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _intent_family(value: Any) -> str:
        text = _MemoryComplianceStreamingTreeCacheMixin._stable_intent_text(value)
        if re.search(r"\b(list|show|read|check|verify|inspect|get|find|calculate|count)\b", text):
            return "read"
        if re.search(r"\b(stage|add|commit|create|update|write|delete|remove|push|apply|merge|execute|run)\b", text):
            return "mutate"
        return text

    @staticmethod
    def _object_family(*values: Any) -> str:
        text = " ".join(_MemoryComplianceStreamingTreeCacheMixin._stable_intent_text(value) for value in values)
        if "git" in text or "repository" in text or "commit" in text:
            return "git"
        if "docker" in text or "image" in text or "container" in text:
            return "docker"
        if "file" in text or "directory" in text:
            return "filesystem"
        tokens = [token for token in text.split() if token]
        return " ".join(tokens[:6])

    @staticmethod
    def _signature_nonempty_value(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned = {
                str(key): _MemoryComplianceStreamingTreeCacheMixin._signature_nonempty_value(item)
                for key, item in value.items()
            }
            return {
                key: item
                for key, item in cleaned.items()
                if item not in ({}, [], "", None)
            }
        if isinstance(value, list):
            cleaned = [_MemoryComplianceStreamingTreeCacheMixin._signature_nonempty_value(item) for item in value]
            return [item for item in cleaned if item not in ({}, [], "", None)]
        return value

    @staticmethod
    def _streaming_step_intent_signature(intent_snapshot: dict[str, Any]) -> str:
        step_description = str(intent_snapshot.get("step_description") or "")
        operation_intent = str(intent_snapshot.get("operation_intent") or "")
        signature_payload = {
            "schema_version": intent_snapshot.get("schema_version"),
            "streaming_step_index": intent_snapshot.get("streaming_step_index"),
            "streaming_step_count": intent_snapshot.get("streaming_step_count"),
            "original_prompt_shape": intent_snapshot.get("original_prompt_shape"),
            "intent_family": _MemoryComplianceStreamingTreeCacheMixin._intent_family(
                " ".join(
                    [
                        str(intent_snapshot.get("semantic_verb") or ""),
                        step_description,
                        operation_intent,
                    ]
                )
            ),
            "object_family": _MemoryComplianceStreamingTreeCacheMixin._object_family(
                intent_snapshot.get("object_type"),
                step_description,
                operation_intent,
                intent_snapshot.get("tags"),
            ),
            "constraints": _MemoryComplianceStreamingTreeCacheMixin._signature_nonempty_value(
                intent_snapshot.get("constraints") or {}
            ),
            "dependency_count": len(list(intent_snapshot.get("dependency_descriptions") or [])),
            "mode": intent_snapshot.get("mode"),
            "model_family": intent_snapshot.get("model_family"),
        }
        raw = json.dumps(signature_payload, sort_keys=True, ensure_ascii=True, default=str)
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:32]

    @staticmethod
    def _protected_literal_payloads_by_input(user_request: UserRequest) -> dict[str, dict[str, Any]]:
        payloads: dict[str, dict[str, Any]] = {}
        for context in (user_request.session_context, user_request.safety_context):
            for payload in literal_payloads_from_context(context):
                input_name = str(payload.get("input_name") or "").strip()
                value = str(payload.get("value") or "")
                if not input_name or not value:
                    continue
                payloads.setdefault(input_name, dict(payload))
        return payloads

    @staticmethod
    def _value_matches_protected_payload(value: Any, payload: dict[str, Any]) -> bool:
        value_text = str(value or "")
        payload_value = str(payload.get("value") or "")
        return value_text == payload_value or value_text.strip() == payload_value.strip()

    def _streaming_step_tree_payload(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> dict[str, Any]:
        """Return a sanitized replayable shell-command tree for one streaming step."""

        if not self._lrdirect_streaming_task(user_request):
            return {}
        actions = list(plan.actions or [])
        if not actions or any(action.kind != "shell_command" for action in actions):
            return {}
        if any(str(action.code or "").strip() for action in actions):
            return {}
        task_map: dict[str, str] = {}
        tasks: list[dict[str, Any]] = []
        for index, task in enumerate(list(plan.tasks or []), start=1):
            task_ref = f"t{index}"
            task_map[str(task.task_id)] = task_ref
            tasks.append(
                {
                    "task_ref": task_ref,
                    "goal": str(task.goal or ""),
                    "semantic_verb": str(task.semantic_verb or ""),
                    "object_type": str(task.object_type or ""),
                    "dependencies": [
                        task_map[item]
                        for item in list(task.dependencies or [])
                        if str(item or "") in task_map
                    ],
                }
            )
        if not tasks:
            current_task = self._lrdirect_streaming_task(user_request)
            task_map[""] = "t1"
            tasks.append(
                {
                    "task_ref": "t1",
                    "goal": str(current_task.get("description") or user_request.raw_prompt or ""),
                    "semantic_verb": str(current_task.get("semantic_verb") or "execute"),
                    "object_type": str(current_task.get("object_type") or "shell"),
                    "dependencies": [],
                }
            )
        protected_payloads = self._protected_literal_payloads_by_input(user_request)
        action_map = {str(action.action_id): f"a{index}" for index, action in enumerate(actions, start=1)}
        sanitized_actions: list[dict[str, Any]] = []
        for action in actions:
            command = str(action.command or "").strip()
            if not command:
                return {}
            if _execution_learning_has_sensitive_text(command, action.effect_summary, action.reason):
                return {}
            protected_inputs: list[str] = []
            for input_name, value in dict(action.inputs or {}).items():
                name = str(input_name or "").strip()
                payload = protected_payloads.get(name)
                if payload is None or not self._value_matches_protected_payload(value, payload):
                    return {}
                protected_inputs.append(name)
            if str(action.stdin_mode or "none") != "none" or action.stdin_text is not None or action.stdin_input_name is not None:
                return {}
            bindings: list[dict[str, Any]] = []
            for binding in list(action.input_bindings or []):
                source_local_id = action_map.get(str(binding.source_action_id or ""))
                if not source_local_id:
                    return {}
                bindings.append(
                    {
                        "input_name": str(binding.input_name or ""),
                        "source_action_ref": source_local_id,
                        "source_field": binding.source_field,
                        "required": bool(binding.required),
                        "fallback_value": binding.fallback_value,
                    }
                )
            depends_on: list[str] = []
            for dependency in list(action.depends_on or []):
                local_dependency = action_map.get(str(dependency or ""))
                if not local_dependency:
                    return {}
                depends_on.append(local_dependency)
            task_ref = task_map.get(str(action.task_id), tasks[0]["task_ref"])
            sanitized_actions.append(
                {
                    "action_ref": action_map[str(action.action_id)],
                    "task_ref": task_ref,
                    "kind": "shell_command",
                    "command": command,
                    "execution_mode": action.execution_mode,
                    "interaction_mode": action.interaction_mode,
                    "declared_output_shape": action.declared_output_shape,
                    "defer_code_generation": False,
                    "allow_zero_result": bool(action.allow_zero_result),
                    "risk": action.risk,
                    "effect_intent": action.effect_intent,
                    "effect_confidence": action.effect_confidence,
                    "effect_summary": _truncate(action.effect_summary, 500),
                    "timeout_seconds": action.timeout_seconds,
                    "reason": _truncate(action.reason, 500),
                    "depends_on": depends_on,
                    "input_bindings": bindings,
                    "protected_inputs": sorted(protected_inputs),
                    "cwd_policy": "current",
                }
            )
        dependencies: list[dict[str, Any]] = []
        for dependency in list(plan.dependencies or []):
            producer = action_map.get(str(dependency.producer_action_id or ""))
            consumer = action_map.get(str(dependency.consumer_action_id or ""))
            if not producer or not consumer:
                continue
            dependencies.append(
                {
                    "producer_action_ref": producer,
                    "consumer_action_ref": consumer,
                    "reason": _truncate(dependency.reason, 500),
                }
            )
        return {
            "schema_version": 1,
            "summary": _truncate(plan.summary, 1000),
            "tasks": tasks,
            "actions": sanitized_actions,
            "dependencies": dependencies,
            "expected_outputs": [str(item) for item in list(plan.expected_outputs or [])[:8]],
            "assumptions": [str(item) for item in list(plan.assumptions or [])[:8]],
        }

    def _streaming_step_tree_plan_from_payload(
        self,
        user_request: UserRequest,
        payload: dict[str, Any],
        *,
        cache_id: str,
    ) -> OperatorPlan:
        """Rebuild a fresh OperatorPlan from a sanitized exact-step action tree."""

        if int(payload.get("schema_version") or 0) != 1:
            raise ValueError("Unsupported streaming step action-tree schema.")
        current_task = self._lrdirect_streaming_task(user_request)
        if not current_task:
            raise ValueError("Streaming step action-tree replay requires a current streaming task.")
        current_task_id = str(current_task.get("task_id") or current_task.get("id") or new_id("task"))
        task_id_map: dict[str, str] = {}
        tasks: list[OperatorTask] = []
        for index, task_payload in enumerate(list(payload.get("tasks") or []), start=1):
            if not isinstance(task_payload, dict):
                continue
            task_ref = str(task_payload.get("task_ref") or f"t{index}")
            task_id = current_task_id if index == 1 else new_id("task")
            task_id_map[task_ref] = task_id
            tasks.append(
                OperatorTask(
                    task_id=task_id,
                    goal=str(
                        current_task.get("description")
                        if index == 1
                        else task_payload.get("goal")
                        or ""
                    ),
                    semantic_verb=str(
                        current_task.get("semantic_verb")
                        if index == 1 and current_task.get("semantic_verb")
                        else task_payload.get("semantic_verb")
                        or "execute"
                    ),
                    object_type=str(
                        current_task.get("object_type")
                        if index == 1 and current_task.get("object_type")
                        else task_payload.get("object_type")
                        or "shell"
                    ),
                    dependencies=[],
                    reason="Rebuilt from an exact streaming-step action-tree LR cache entry.",
                )
            )
        if not tasks:
            task_id_map["t1"] = current_task_id
            tasks.append(
                OperatorTask(
                    task_id=current_task_id,
                    goal=str(current_task.get("description") or user_request.raw_prompt or ""),
                    semantic_verb=str(current_task.get("semantic_verb") or "execute"),
                    object_type=str(current_task.get("object_type") or "shell"),
                    dependencies=[],
                    reason="Rebuilt from an exact streaming-step action-tree LR cache entry.",
                )
            )
        cwd = _terminal_cwd_from_request(user_request) or str(
            dict(user_request.session_context or {}).get("terminal_cwd") or "."
        )
        protected_payloads = self._protected_literal_payloads_by_input(user_request)
        action_id_map: dict[str, str] = {}
        for index, action_payload in enumerate(list(payload.get("actions") or []), start=1):
            if not isinstance(action_payload, dict):
                continue
            action_id_map[str(action_payload.get("action_ref") or f"a{index}")] = new_id("action")
        actions: list[OperatorAction] = []
        for index, action_payload in enumerate(list(payload.get("actions") or []), start=1):
            if not isinstance(action_payload, dict):
                continue
            action_ref = str(action_payload.get("action_ref") or f"a{index}")
            inputs: dict[str, Any] = {}
            for input_name in list(action_payload.get("protected_inputs") or []):
                name = str(input_name or "").strip()
                payload_for_input = protected_payloads.get(name)
                if payload_for_input is None:
                    raise ValueError(f"Missing current protected literal payload: {name}")
                inputs[name] = str(payload_for_input.get("value") or "")
            bindings: list[dict[str, Any]] = []
            for binding in list(action_payload.get("input_bindings") or []):
                if not isinstance(binding, dict):
                    continue
                source_local_id = str(binding.get("source_action_ref") or "")
                source_action_id = action_id_map.get(source_local_id)
                if not source_action_id:
                    raise ValueError("Cached action-tree binding references an unknown action.")
                bindings.append(
                    {
                        "input_name": str(binding.get("input_name") or "stdout"),
                        "source_action_id": source_action_id,
                        "source_field": str(binding.get("source_field") or "stdout"),
                        "required": bool(binding.get("required", True)),
                        "fallback_value": binding.get("fallback_value"),
                    }
                )
            depends_on: list[str] = []
            for dependency in list(action_payload.get("depends_on") or []):
                dependency_id = action_id_map.get(str(dependency or ""))
                if not dependency_id:
                    raise ValueError("Cached action-tree dependency references an unknown action.")
                depends_on.append(dependency_id)
            task_ref = str(action_payload.get("task_ref") or "t1")
            action = OperatorAction.model_validate(
                {
                    "action_id": action_id_map[action_ref],
                    "task_id": task_id_map.get(task_ref, tasks[0].task_id),
                    "kind": "shell_command",
                    "command": str(action_payload.get("command") or ""),
                    "execution_mode": str(action_payload.get("execution_mode") or "captured"),
                    "interaction_mode": str(action_payload.get("interaction_mode") or "non_interactive"),
                    "cwd": cwd,
                    "inputs": inputs,
                    "input_bindings": bindings,
                    "stdin_mode": "none",
                    "declared_output_shape": str(action_payload.get("declared_output_shape") or "text"),
                    "defer_code_generation": False,
                    "allow_zero_result": bool(action_payload.get("allow_zero_result", False)),
                    "risk": str(action_payload.get("risk") or "medium"),
                    "effect_intent": str(action_payload.get("effect_intent") or "unknown"),
                    "effect_confidence": float(action_payload.get("effect_confidence") or 0.0),
                    "effect_summary": str(action_payload.get("effect_summary") or ""),
                    "timeout_seconds": action_payload.get("timeout_seconds"),
                    "reason": (
                        str(action_payload.get("reason") or "")
                        or f"Replayed from streaming-step action-tree LR cache {cache_id}."
                    ),
                    "depends_on": depends_on,
                }
            )
            actions.append(action)
        if not actions:
            raise ValueError("Cached streaming step action-tree has no actions.")
        dependencies: list[OperatorDependency] = []
        for dependency in list(payload.get("dependencies") or []):
            if not isinstance(dependency, dict):
                continue
            producer = action_id_map.get(str(dependency.get("producer_action_ref") or ""))
            consumer = action_id_map.get(str(dependency.get("consumer_action_ref") or ""))
            if not producer or not consumer:
                continue
            dependencies.append(
                OperatorDependency(
                    producer_action_id=producer,
                    consumer_action_id=consumer,
                    reason=str(dependency.get("reason") or "Cached action-tree dependency."),
                )
            )
        return OperatorPlan(
            summary=str(payload.get("summary") or "Replay an exact successful streaming step from LR."),
            tasks=tasks,
            actions=actions,
            dependencies=dependencies,
            expected_outputs=[str(item) for item in list(payload.get("expected_outputs") or [])],
            assumptions=[str(item) for item in list(payload.get("assumptions") or [])],
            confidence=1.0,
        )

    def _try_streaming_step_action_tree_cache(
        self,
        user_request: UserRequest,
        observability: ObservabilityContext | None,
    ) -> tuple[OperatorPlan, PlanCacheCandidate] | None:
        """Try exact regular-LR replay of a successful streaming-step command tree."""

        if not self._streaming_step_tree_cache_enabled(user_request):
            return None
        if operator_request_requires_generated_text(user_request):
            self._emit_plan_cache_lookup(
                user_request,
                observability,
                title="Streaming step tree cache skipped",
                summary="Generated-text steps must plan an llm_text action instead of replaying cached shell-only evidence.",
                details={"enabled": True, "cache_type": "streaming_step_tree", "candidate_count": 0},
            )
            return None
        assert self.plan_cache_store is not None
        exact_key, prompt_excerpt = self._lrdirect_step_metadata(user_request)
        if not exact_key:
            return None
        lookup = self._plan_cache_context(user_request)
        intent_snapshot = self._streaming_step_intent_snapshot(user_request, lookup=lookup)
        intent_signature = self._streaming_step_intent_signature(intent_snapshot)
        candidates: list[PlanCacheCandidate] = []
        mismatch_candidates: list[PlanCacheCandidate] = []
        try:
            candidates = self.plan_cache_store.retrieve_exact_step_tree(
                exact_key,
                intent_signature=intent_signature,
                record_use=False,
            )
            if not candidates:
                mismatch_candidates = self.plan_cache_store.retrieve_exact_step_tree(
                    exact_key,
                    record_use=False,
                    limit=1,
                )
            if not candidates:
                candidates = self.plan_cache_store.retrieve_step_tree_by_intent_signature(
                    intent_signature,
                    record_use=False,
                    limit=3,
                )
        except Exception as exc:
            self._emit_plan_cache_lookup(
                user_request,
                observability,
                title="Streaming step tree cache lookup failed",
                summary="The exact streaming-step action-tree cache lookup failed; normal planning will continue.",
                details={
                    "enabled": True,
                    "cache_type": "streaming_step_tree",
                    "candidate_count": 0,
                    "exact_step_key": exact_key,
                    "intent_signature": intent_signature,
                    "error": str(exc),
                },
            )
            return None
        self._emit_plan_cache_lookup(
            user_request,
            observability,
            title="Streaming step tree cache lookup completed",
            summary="The runtime checked for an exact reusable command tree for this streaming step.",
            details={
                "enabled": True,
                "cache_type": "streaming_step_tree",
                "candidate_count": len(candidates),
                "exact_step_key": exact_key,
                "prompt_excerpt": prompt_excerpt,
                "intent_signature": intent_signature,
                "lookup_order": ["exact_step_key", "intent_signature"],
                "candidates": [
                    {
                        "cache_id": candidate.entry.cache_id,
                        "score": candidate.score,
                        "reason": candidate.reason,
                        "stored_exact_step_key": candidate.entry.exact_step_key,
                        "action_count": len(list(candidate.entry.direct_plan.get("actions") or [])),
                    }
                    for candidate in candidates
                ],
            },
        )
        if not candidates:
            if mismatch_candidates:
                self._emit(
                    observability,
                    level="info",
                    event_type=OPERATOR_PLAN_CACHE_REJECTED,
                    title="Streaming step tree cache rejected",
                    summary="The exact step key matched, but the stable intent signature did not.",
                    details={
                        "cache_type": "streaming_step_tree",
                        "cache_id": mismatch_candidates[0].entry.cache_id,
                        "exact_step_key": exact_key,
                        "current_intent_signature": intent_signature,
                        "stored_intent_signature": mismatch_candidates[0].entry.intent_signature,
                    },
                )
            return None
        for candidate in candidates:
            try:
                plan = self._streaming_step_tree_plan_from_payload(
                    user_request,
                    dict(candidate.entry.direct_plan or {}),
                    cache_id=candidate.entry.cache_id,
                )
            except (PydanticValidationError, ValueError) as exc:
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_PLAN_CACHE_REJECTED,
                    title="Streaming step tree cache rejected",
                    summary="The exact cached command tree was stale or invalid; normal planning will continue.",
                    details={
                        "cache_type": "streaming_step_tree",
                        "cache_id": candidate.entry.cache_id,
                        "exact_step_key": exact_key,
                        "stored_exact_step_key": candidate.entry.exact_step_key,
                        "intent_signature": intent_signature,
                        "error": str(exc),
                    },
                )
                continue
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_PLAN_CACHE_HIT,
                title="Streaming step tree cache hit",
                summary="An exact successful streaming-step command tree was rebuilt from regular LR.",
                details={
                    "cache_type": "streaming_step_tree",
                    "cache_id": candidate.entry.cache_id,
                    "exact_step_key": exact_key,
                    "stored_exact_step_key": candidate.entry.exact_step_key,
                    "intent_signature": intent_signature,
                    "action_count": len(plan.actions),
                    "task_count": len(plan.tasks),
                    "bypassed_llm": True,
                    "bypassed_approval": False,
                },
            )
            return plan, candidate
        return None



__all__ = ["_MemoryComplianceStreamingTreeCacheMixin"]
