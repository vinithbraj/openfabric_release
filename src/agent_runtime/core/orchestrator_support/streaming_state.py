"""Streaming operator state and validation helpers."""

from __future__ import annotations

from .common import *
from .formatting import *


class _StreamingStateMixin:
    """Streaming operator state and validation helpers."""

    @staticmethod
    def _operator_mode_requested(context: dict[str, Any]) -> bool:
        """Return whether this request explicitly asks for Conversational mode."""

        mode = str(context.get("agent_mode") or context.get("mode") or "").strip().lower()
        return mode == "llm_operator"

    def _streaming_operator_requested(self, context: dict[str, Any] | None = None) -> bool:
        """Return whether decomposition-driven operator streaming is enabled."""

        return workflow_uses_streaming(
            self._runtime_config_for_context(context).workflow_execution_mode
        )

    @staticmethod
    def _execution_shape_hint(
        prompt: str,
        *,
        classification_context: dict[str, Any] | None = None,
        tasks: list[TaskFrame] | None = None,
        global_constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return deterministic execution-shape guidance for operator planning."""

        return classify_execution_shape(
            prompt,
            classification_context=classification_context,
            tasks=tasks,
            global_constraints=global_constraints,
        )

    def _attach_execution_shape_hint(
        self,
        user_request: UserRequest,
        request_context: dict[str, Any],
        trace: PlanningTrace,
        observability: ObservabilityContext | None = None,
        *,
        classification_context: dict[str, Any] | None = None,
        tasks: list[TaskFrame] | None = None,
        global_constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hint = self._execution_shape_hint(
            user_request.raw_prompt,
            classification_context=classification_context,
            tasks=tasks,
            global_constraints=global_constraints,
        )
        request_context["execution_shape"] = dict(hint)
        user_request.safety_context["execution_shape"] = dict(hint)
        trace.metadata["execution_shape"] = dict(hint)
        trace.metadata["execution_shape_fresh"] = True
        if observability is not None:
            observability.info(
                STAGE_PROMPT_CLASSIFICATION,
                EVENT_VALIDATION_ACCEPTED,
                "Execution shape evaluated",
                "The runtime evaluated diagnostic execution-shape metadata.",
                details=hint,
                debug_only=True,
            )
        return hint

    @staticmethod
    def _standard_operator_route_requested(
        tasks: list[TaskFrame],
        classification_context: dict[str, Any],
    ) -> bool:
        """Return whether a standard-mode request should use the operator-native planner."""

        if not tasks:
            return False
        likely_domains = {
            str(domain or "").strip().lower()
            for domain in classification_context.get("likely_domains", [])
            if str(domain or "").strip()
        }
        structured_task_prefixes = ("sql.", "database.")
        structured_task_types = {"sql", "database"}
        for task in tasks:
            object_type = str(task.object_type or "").strip().lower()
            if object_type in structured_task_types or object_type.startswith(structured_task_prefixes):
                return False
        return bool(likely_domains) and likely_domains <= _STANDARD_OPERATOR_ORDINARY_DOMAINS

    @staticmethod
    def _standard_operator_classification_route_requested(
        classification_context: dict[str, Any],
    ) -> bool:
        """Return whether classification alone is enough to use the operator planner.

        Ordinary local work should not pass through legacy decomposition/dataflow first:
        that path can invent intermediate artifacts before the operator planner sees the
        user's actual goal. Structured domains stay on the typed capability path.
        """

        likely_domains = {
            str(domain or "").strip().lower()
            for domain in classification_context.get("likely_domains", [])
            if str(domain or "").strip()
        }
        prompt_type = str(classification_context.get("prompt_type") or "").strip().lower()
        if prompt_type not in {"simple_tool_task", "compound_tool_task", "complex_workflow"}:
            return False
        return bool(likely_domains) and likely_domains <= _STANDARD_OPERATOR_ORDINARY_DOMAINS

    @staticmethod
    def _single_report_operator_route_requested(
        execution_shape: dict[str, Any],
        classification_context: dict[str, Any],
    ) -> bool:
        """Execution shape no longer selects an operator fast lane."""

        del execution_shape, classification_context
        return False

    @staticmethod
    def _operator_intent_block(
        *,
        tasks: list[TaskFrame],
        global_constraints: dict[str, Any],
        classification_context: dict[str, Any],
        execution_shape: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a safe semantic intent block for operator-native planning."""

        block = {
            "mode": "standard_agent_operator_block",
            "classification": {
                "prompt_type": classification_context.get("prompt_type"),
                "requires_tools": classification_context.get("requires_tools"),
                "likely_domains": list(classification_context.get("likely_domains") or []),
                "risk_level": classification_context.get("risk_level"),
            },
            "global_constraints": dict(global_constraints or {}),
            "tasks": [
                {
                    "task_id": task.id,
                    "description": task.description,
                    "semantic_verb": task.semantic_verb,
                    "object_type": task.object_type,
                    "constraints": dict(task.constraints),
                    "dependencies": list(task.dependencies),
                    "risk_level": task.risk_level,
                    "requires_confirmation": task.requires_confirmation,
                }
                for task in tasks
            ],
        }
        if isinstance(execution_shape, dict) and execution_shape:
            block["execution_shape"] = dict(execution_shape)
        return block

    @staticmethod
    def _ordered_streaming_tasks(tasks: list[TaskFrame]) -> list[TaskFrame]:
        """Return tasks in dependency order while preserving decomposition order for ties."""

        by_id = {task.id: task for task in tasks}
        original_index = {task.id: index for index, task in enumerate(tasks)}
        remaining = set(by_id)
        ordered: list[TaskFrame] = []
        while remaining:
            ready = [
                task_id
                for task_id in remaining
                if all(dep not in by_id or dep not in remaining for dep in by_id[task_id].dependencies)
            ]
            if not ready:
                ready = sorted(remaining, key=lambda task_id: original_index.get(task_id, 0))
            ready.sort(key=lambda task_id: original_index.get(task_id, 0))
            task_id = ready[0]
            ordered.append(by_id[task_id])
            remaining.remove(task_id)
        return ordered

    @staticmethod
    def _streaming_step_id_for_task(task: dict[str, Any], index: int) -> str:
        raw_task_id = str(task.get("task_id") or task.get("id") or "").strip()
        slug = "".join(
            character.lower() if character.isalnum() else "-"
            for character in (raw_task_id or "task")
        ).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        return f"stream-step-{index + 1}-{(slug or 'task')[:48]}"

    @classmethod
    def _ensure_streaming_step_ids(cls, state: dict[str, Any]) -> dict[str, Any]:
        updated = dict(state)
        tasks: list[Any] = []
        for index, raw_task in enumerate(list(updated.get("tasks") or [])):
            if not isinstance(raw_task, dict):
                tasks.append(raw_task)
                continue
            task = dict(raw_task)
            if not str(task.get("streaming_step_id") or "").strip():
                task["streaming_step_id"] = cls._streaming_step_id_for_task(task, index)
            tasks.append(task)
        updated["tasks"] = tasks
        return updated

    @classmethod
    def _streaming_task_payload(cls, task: TaskFrame, *, index: int = 0) -> dict[str, Any]:
        payload = {
            "task_id": task.id,
            "description": task.description,
            "semantic_verb": task.semantic_verb,
            "object_type": task.object_type,
            "constraints": dict(task.constraints),
            "dependencies": list(task.dependencies),
            "risk_level": task.risk_level,
            "requires_confirmation": task.requires_confirmation,
        }
        payload["streaming_step_id"] = cls._streaming_step_id_for_task(payload, index)
        return payload

    def _new_streaming_state(
        self,
        *,
        original_prompt: str,
        tasks: list[TaskFrame],
        global_constraints: dict[str, Any],
        classification_context: dict[str, Any],
        execution_shape: dict[str, Any] | None = None,
        agent_mode: str,
    ) -> dict[str, Any]:
        ordered = self._ordered_streaming_tasks(tasks)
        normalized_agent_mode = "llm_operator" if agent_mode == "llm_operator" else "standard_operator"
        return {
            "original_prompt": original_prompt,
            "agent_mode": normalized_agent_mode,
            "global_constraints": dict(global_constraints or {}),
            "classification": dict(classification_context or {}),
            "execution_shape": dict(execution_shape or {}),
            "tasks": [
                self._streaming_task_payload(task, index=index)
                for index, task in enumerate(ordered)
            ],
            "current_index": 0,
            "completed_task_ids": [],
            "prior_results": [],
        }

    def _streaming_step_prompt(self, state: dict[str, Any], task: dict[str, Any]) -> str:
        index = int(state.get("current_index") or 0) + 1
        total = len(state.get("tasks") or [])
        step_id = str(task.get("streaming_step_id") or "").strip()
        later_tasks = [
            str(candidate.get("description") or "")
            for candidate in list(state.get("tasks") or [])[index:]
            if isinstance(candidate, dict) and str(candidate.get("description") or "").strip()
        ]
        lines = [
            f"Streaming step {index} of {total}: {task.get('description') or ''}",
            "Complete only this decomposed step. This step is the only executable scope.",
            "Use the original request only as background for constraints and intent; do not perform later steps yet.",
            (
                "If this step filters, selects, sorts, or transforms prior output, produce only "
                "that narrowed result and do not execute the selected targets."
            ),
            f"Original request background: {state.get('original_prompt') or ''}",
        ]
        if step_id:
            lines.append(f"Streaming step id: {step_id}")
        explicit_online_ai_query = str(state.get("online_ai_check_query") or "").strip()
        if explicit_online_ai_query and self._streaming_task_should_lookup_online_ai(state, task):
            lines.append(
                "Explicit /checkonlineai lookup query for this step: "
                f"{explicit_online_ai_query}"
            )
        if later_tasks:
            lines.append("Later steps reserved for future calls: " + " | ".join(later_tasks))
        return "\n".join(lines)

    def _streaming_step_request(
        self,
        base_request: UserRequest,
        state: dict[str, Any],
        task: dict[str, Any],
    ) -> UserRequest:
        classification_context = dict(state.get("classification") or {})
        intent_block = self._operator_intent_block(
            tasks=[],
            global_constraints={
                **dict(state.get("global_constraints") or {}),
                "streaming_original_prompt": str(state.get("original_prompt") or ""),
                "streaming_prior_results": list(state.get("prior_results") or []),
            },
            classification_context=classification_context,
            execution_shape=dict(state.get("execution_shape") or {}),
        )
        intent_block["mode"] = "decomposition_streaming_step"
        intent_block["tasks"] = [dict(task)]
        session_context = dict(base_request.session_context or {})
        seed_records = self._streaming_seed_record_payloads(state)
        session_context["operator_intent_block"] = intent_block
        session_context["operator_streaming_current_task"] = dict(task)
        session_context["operator_streaming_tasks"] = list(state.get("tasks") or [])
        session_context["operator_streaming_current_index"] = int(state.get("current_index") or 0)
        step_id = str(task.get("streaming_step_id") or "").strip()
        if step_id:
            session_context["operator_streaming_step_id"] = step_id
            session_context["operator_execution_step_id"] = step_id
        session_context["operator_streaming_completed_task_ids"] = list(
            state.get("completed_task_ids") or []
        )
        session_context["operator_streaming_prior_results"] = {
            "completed_tasks": list(state.get("prior_results") or [])
        }
        if seed_records:
            session_context["operator_seed_records"] = seed_records
        session_context.setdefault("operator_mode_label", _agent_display_name_from_context(session_context))
        session_context.setdefault("operator_display_label", _agent_display_name_from_context(session_context))
        safety_context = dict(base_request.safety_context or {})
        safety_context["operator_intent_block"] = intent_block
        step_prompt = self._streaming_step_prompt(state, task)
        session_context["operator_streaming_original_prompt"] = str(state.get("original_prompt") or "")
        session_context["operator_streaming_step_prompt"] = step_prompt
        safety_context["operator_streaming_step_prompt"] = step_prompt
        return base_request.model_copy(
            update={
                "raw_prompt": step_prompt,
                "session_context": session_context,
                "safety_context": safety_context,
            }
        )

    @staticmethod
    def _streaming_state_after_step(
        state: dict[str, Any],
        *,
        task: dict[str, Any],
        result: OperatorPipelineResult,
    ) -> dict[str, Any]:
        updated = dict(state)
        task_id = str(task.get("task_id") or "")
        step_id = str(task.get("streaming_step_id") or "").strip()
        current_index = int(updated.get("current_index") or 0)

        def _streaming_record_key(record: dict[str, Any], default_task_id: str) -> tuple[str, str]:
            metadata = dict(record.get("metadata") or {})
            scoped_step_id = str(metadata.get("streaming_step_id") or "").strip()
            scoped_task_id = str(metadata.get("streaming_task_id") or default_task_id or "").strip()
            action_id = str(record.get("action_id") or "").strip()
            return scoped_step_id or scoped_task_id, action_id

        existing_record_keys = {
            _streaming_record_key(record, str(record.get("task_id") or ""))
            for record in _StreamingStateMixin._streaming_flat_record_payloads(updated)
            if isinstance(record, dict)
            and str(record.get("status") or "").strip().lower() == "success"
        }
        records: list[dict[str, Any]] = []
        for record in result.execution_records:
            record_payload = record.model_dump(mode="json")
            if _streaming_record_key(record_payload, task_id) in existing_record_keys:
                continue
            payload = record_payload
            metadata = dict(payload.get("metadata") or {})
            original_task_id = str(payload.get("task_id") or "")
            if original_task_id and original_task_id != task_id:
                metadata.setdefault("original_task_id", original_task_id)
            metadata["streaming_task_id"] = task_id
            if step_id:
                metadata["streaming_step_id"] = step_id
            metadata["streaming_step_index"] = current_index
            payload["metadata"] = metadata
            # A streaming operator call is already scoped to the current
            # decomposed task. Preserve that scope for durable replay even when
            # an inner operator plan reused its own task ids.
            payload["task_id"] = task_id
            records.append(payload)
        updated.setdefault("prior_results", [])
        updated["prior_results"] = [
            *list(updated.get("prior_results") or []),
            {
                "task_id": task_id,
                "description": str(task.get("description") or ""),
                "status": result.status,
                "final_response": str(result.final_response or "")[:4000],
                "records": records,
            },
        ]
        if result.status == "success":
            completed = list(updated.get("completed_task_ids") or [])
            task_id = str(task.get("task_id") or "")
            if task_id and task_id not in completed:
                completed.append(task_id)
            updated["completed_task_ids"] = completed
            updated["current_index"] = int(updated.get("current_index") or 0) + 1
        return updated

    @staticmethod
    def _streaming_state_with_validation_failure(
        state: dict[str, Any],
        *,
        task: dict[str, Any],
        errors: list[dict[str, Any]],
        message: str,
    ) -> dict[str, Any]:
        """Record a planning/validation failure as a retryable streaming attempt."""

        updated = dict(state)
        task_id = str(task.get("task_id") or "")
        step_id = str(task.get("streaming_step_id") or "").strip()
        current_index = int(updated.get("current_index") or 0)
        action_id = f"validation_{task_id or current_index + 1}"
        record = {
            "action_id": action_id,
            "task_id": task_id,
            "kind": "operator_validation",
            "status": "error",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "output": None,
            "error": message,
            "metadata": {
                "validation_errors": list(errors),
                "streaming_task_id": task_id,
                "streaming_step_index": current_index,
                **({"streaming_step_id": step_id} if step_id else {}),
            },
        }
        updated.setdefault("prior_results", [])
        updated["prior_results"] = [
            *list(updated.get("prior_results") or []),
            {
                "task_id": task_id,
                "description": str(task.get("description") or ""),
                "status": "error",
                "final_response": message,
                "records": [record],
            },
        ]
        return updated

    @staticmethod
    def _streaming_completion_markdown(state: dict[str, Any]) -> str:
        prior_results = list(state.get("prior_results") or [])
        lines = ["## Streaming Complete", ""]
        for index, item in enumerate(prior_results, start=1):
            description = _StreamingStateMixin._streaming_result_description(item, index=index)
            status = str(item.get("status") or "unknown")
            lines.append(f"- {description} · `{status}`")
        result_sections = _StreamingStateMixin._streaming_completion_result_sections(prior_results)
        if result_sections:
            lines.extend(["", "## Results", "", *result_sections])
        return "\n".join(lines).strip()

    @staticmethod
    def _streaming_result_description(item: Any, *, index: int) -> str:
        if isinstance(item, dict):
            description = str(item.get("description") or item.get("task_id") or "").strip()
            if description:
                return description
        return "Completed task"

    @staticmethod
    def _streaming_flat_record_payloads(state: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in list(state.get("prior_results") or []):
            for record in list(dict(item).get("records") or []):
                if isinstance(record, dict):
                    records.append(dict(record))
        return records

    @staticmethod
    def _streaming_seed_record_payloads(state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            record
            for record in _StreamingStateMixin._streaming_flat_record_payloads(state)
            if str(record.get("status") or "").strip().lower() == "success"
        ]

    @staticmethod
    def _streaming_record_output_excerpt(record: dict[str, Any], *, limit: int = 1200) -> str:
        values: list[str] = []
        for key in ("stdout", "output"):
            value = record.get(key)
            text = _raw_value_markdown(value, limit=limit)
            if text:
                values.append(text)
        if str(record.get("status") or "") == "error":
            for key in ("stderr", "error"):
                text = _raw_value_markdown(record.get(key), limit=limit)
                if text and text not in values:
                    values.append(text)
        output = "\n".join(values).strip()
        return output

    @staticmethod
    def _streaming_step_final_response_excerpt(value: Any, *, limit: int = 1000) -> str:
        if isinstance(value, (dict, list, tuple)):
            return _raw_value_markdown(value, limit=limit)
        text = "\n".join(line.rstrip() for line in str(value or "").splitlines()).strip()
        if not text:
            return ""
        generic_responses = {
            "Task completed. Detailed output is available in the terminal or command output capsule."
        }
        if text in generic_responses or text.startswith("## Confirmation Required"):
            return ""
        parsed = _parse_json_display_value(text)
        if parsed is not None:
            return _raw_value_markdown(text, limit=limit)
        if len(text) > limit:
            return text[:limit].rstrip() + "\n...[truncated]"
        return text

    @staticmethod
    def _streaming_completion_content_key(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        fence_match = re.fullmatch(
            r"```+[a-zA-Z0-9_-]*\n([\s\S]*?)\n```+",
            text,
        )
        if fence_match:
            text = fence_match.group(1).strip()
        parsed = _parse_json_display_value(text)
        if parsed is not None:
            text = _pretty_json(parsed)
        return re.sub(r"[^a-z0-9]+", "", text.lower())

    @staticmethod
    def _streaming_completion_duplicates_record_output(
        final_response: str,
        record_excerpts: list[str],
    ) -> bool:
        final_key = _StreamingStateMixin._streaming_completion_content_key(final_response)
        if not final_key or len(final_key) < 24:
            return False
        for excerpt in record_excerpts:
            excerpt_key = _StreamingStateMixin._streaming_completion_content_key(excerpt)
            if not excerpt_key:
                continue
            if final_key == excerpt_key:
                return True
            smaller = min(len(final_key), len(excerpt_key))
            larger = max(len(final_key), len(excerpt_key))
            if smaller >= 24 and larger > 0 and smaller / larger >= 0.95:
                if final_key in excerpt_key or excerpt_key in final_key:
                    return True
        return False

    @staticmethod
    def _streaming_completion_result_sections(prior_results: list[Any]) -> list[str]:
        sections: list[str] = []
        for index, raw_item in enumerate(prior_results, start=1):
            if not isinstance(raw_item, dict):
                continue
            description = _StreamingStateMixin._streaming_result_description(raw_item, index=index)
            record_excerpts: list[str] = []
            for record in list(raw_item.get("records") or []):
                if not isinstance(record, dict):
                    continue
                excerpt = _StreamingStateMixin._streaming_record_output_excerpt(record)
                if excerpt:
                    record_excerpts.append(excerpt)
            body_parts: list[str] = []
            final_response = _StreamingStateMixin._streaming_step_final_response_excerpt(
                raw_item.get("final_response")
            )
            if final_response and not _StreamingStateMixin._streaming_completion_duplicates_record_output(
                final_response,
                record_excerpts,
            ):
                body_parts.append(final_response)
            body_parts.extend(record_excerpts)
            deduped: list[str] = []
            seen: set[str] = set()
            for part in body_parts:
                key = part.strip()
                if key and key not in seen:
                    seen.add(key)
                    deduped.append(key)
            if not deduped:
                continue
            sections.append(f"### {description}\n\n" + "\n\n".join(deduped))
        return sections

    @staticmethod
    def _bounded_step_validation_text(value: Any, *, limit: int = 4000) -> str:
        text = "\n".join(line.rstrip() for line in str(value or "").splitlines()).strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "\n...[truncated]"

    @staticmethod
    def _step_validation_candidate_artifacts(*values: Any) -> list[str]:
        """Return likely artifact path tokens as evidence candidates, not verdicts."""

        candidates: list[str] = []
        seen: set[str] = set()
        pattern = re.compile(
            r"(?<![\w./-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+(?![\w./-])"
        )
        for value in values:
            text = str(value or "")
            for match in pattern.finditer(text):
                candidate = match.group(0).strip("`'\".,;:()[]{}")
                name = Path(candidate).name
                if re.match(r"^\d+(?:\.\d+)?[A-Za-z]+$", name):
                    continue
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                candidates.append(candidate)
        return candidates[:20]

    @staticmethod
    def _shell_input_env_preview_name(input_name: Any) -> str:
        raw = str(input_name or "").strip()
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").upper()
        if not safe:
            safe = "VALUE"
        if not re.match(r"^[A-Z_]", safe):
            safe = f"VALUE_{safe}"
        return f"OF_INPUT_{safe}"

    def _streaming_step_validation_contract(
        self,
        user_request: UserRequest,
        state: dict[str, Any],
        task: dict[str, Any],
    ) -> OperatorStepValidationContract:
        original_request = str(state.get("original_prompt") or user_request.raw_prompt or "").strip()
        description = str(task.get("description") or "").strip()
        semantic_verb = str(task.get("semantic_verb") or "").strip()
        object_type = str(task.get("object_type") or "").strip()
        constraints = task.get("constraints") if isinstance(task.get("constraints"), dict) else {}
        current_constraints = {
            key: value
            for key, value in dict(constraints).items()
            if str(key) != "global_constraints"
        }
        current_task_text = " ".join(
            [
                description,
                semantic_verb,
                object_type,
                json.dumps(current_constraints, ensure_ascii=True, default=str),
            ]
        )
        artifacts = self._step_validation_candidate_artifacts(description, current_constraints)
        mutation_terms = {
            "add",
            "append",
            "commit",
            "create",
            "delete",
            "edit",
            "move",
            "remove",
            "rename",
            "save",
            "stage",
            "update",
            "write",
        }
        requested_mutations = []
        if semantic_verb.lower() in mutation_terms or any(
            re.search(rf"\b{re.escape(term)}\b", current_task_text, re.IGNORECASE)
            for term in mutation_terms
        ):
            requested_mutations.append(description or semantic_verb or "mutation requested")
        execution_shape = execution_shape_from_request(user_request)
        report_contract = (
            execution_shape.get("report_contract")
            if isinstance(execution_shape.get("report_contract"), dict)
            else {}
        )
        required_fields = [
            str(field)
            for field in list(report_contract.get("required_fields") or [])
            if str(field).strip()
        ]
        field_types = report_contract.get("field_types") if isinstance(report_contract.get("field_types"), dict) else {}
        requested_values = [
            f"{field}: {field_types.get(field, 'text')}"
            for field in required_fields
        ]
        success_conditions = [
            "The concrete execution evidence satisfies the current decomposed step exactly.",
            "The step must not rely on final-answer wording as proof when runtime evidence is required.",
        ]
        evidence_expectations = [
            "Use command exit codes, stdout/stderr, typed outputs, bound inputs, and artifact evidence.",
            "For file output steps, require exact artifact path/name evidence.",
        ]
        if requested_values:
            success_conditions.append(
                "Any output values inherited from the original request must satisfy the requested field types."
            )
            evidence_expectations.append(
                "For typed report fields, reject placeholder or prose values when dates, numbers, sizes, or concrete statuses were requested."
            )
        return OperatorStepValidationContract(
            original_request=original_request,
            step_id=str(task.get("streaming_step_id") or "").strip(),
            task_id=str(task.get("task_id") or "").strip(),
            step_index=int(state.get("current_index") or 0),
            step_description=description,
            semantic_verb=semantic_verb,
            object_type=object_type,
            exact_requested_outputs=[description] if description else [],
            requested_artifacts=artifacts,
            requested_values=requested_values,
            requested_mutations=requested_mutations,
            success_conditions=success_conditions,
            evidence_expectations=evidence_expectations,
        )

    def _streaming_step_artifact_evidence(
        self,
        *,
        contract: OperatorStepValidationContract,
        plan: OperatorPlan,
        result: OperatorPipelineResult,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Collect safe facts for the LLM judge without deciding satisfaction."""

        runtime_config = self._runtime_config_for_context(context)
        workspace_root = Path(runtime_config.workspace_root or ".").expanduser()
        try:
            workspace_root = workspace_root.resolve()
        except OSError:
            workspace_root = Path(runtime_config.workspace_root or ".").absolute()

        artifacts: list[dict[str, Any]] = []
        for raw_path in contract.requested_artifacts:
            evidence: dict[str, Any] = {"requested_path": raw_path}
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = workspace_root / candidate
            try:
                resolved = candidate.resolve()
                evidence["resolved_path"] = str(resolved)
                try:
                    resolved.relative_to(workspace_root)
                    inside_workspace = True
                except ValueError:
                    inside_workspace = False
                evidence["inside_workspace"] = inside_workspace
                exists = resolved.exists()
                evidence["exists"] = exists
                evidence["is_file"] = resolved.is_file() if exists else False
                evidence["is_dir"] = resolved.is_dir() if exists else False
                if exists and resolved.is_file():
                    stat = resolved.stat()
                    evidence["size_bytes"] = stat.st_size
                    if not inside_workspace:
                        evidence["content_preview_omitted"] = "outside_workspace"
                    else:
                        try:
                            preview = resolved.read_text(encoding="utf-8", errors="replace")[:2000]
                        except OSError as exc:
                            evidence["preview_error"] = str(exc)
                        else:
                            evidence["content_preview"] = self._bounded_step_validation_text(
                                preview,
                                limit=2000,
                            )
            except OSError as exc:
                evidence["error"] = str(exc)
            artifacts.append(evidence)

        action_inputs: list[dict[str, Any]] = []
        for action in plan.actions:
            inputs = dict(action.inputs or {})
            if not inputs:
                continue
            previews = []
            for input_name, value in inputs.items():
                previews.append(
                    {
                        "input_name": str(input_name),
                        "env_name": self._shell_input_env_preview_name(input_name),
                        "value_preview": self._bounded_step_validation_text(value, limit=1000),
                    }
                )
            action_inputs.append({"action_id": action.action_id, "inputs": previews})

        record_evidence: list[dict[str, Any]] = []
        for record in result.execution_records:
            metadata = dict(record.metadata or {})
            record_evidence.append(
                {
                    "action_id": record.action_id,
                    "task_id": record.task_id,
                    "status": record.status,
                    "exit_code": record.exit_code,
                    "bound_inputs_preview": metadata.get("bound_inputs_preview"),
                    "shell_input_env_names": metadata.get("shell_input_env_names"),
                    "cwd": metadata.get("cwd"),
                }
            )

        return {
            "workspace_root": str(workspace_root),
            "requested_artifacts": artifacts,
            "action_literal_inputs": action_inputs,
            "execution_record_facts": record_evidence,
        }

    @staticmethod
    def _streaming_step_validation_feedback_for_task(
        state: dict[str, Any],
        task: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            return {}
        feedback_by_task = state.get("step_validation_feedback_by_task")
        if not isinstance(feedback_by_task, dict):
            return {}
        feedback = feedback_by_task.get(task_id)
        return dict(feedback) if isinstance(feedback, dict) else {}

    @staticmethod
    def _attach_step_validation_feedback_context(
        *,
        step_context: dict[str, Any],
        step_request: UserRequest,
        feedback: dict[str, Any],
    ) -> None:
        if not feedback:
            return
        note = {
            "phase": "step_validation",
            "reason": str(feedback.get("reason") or "Previous step validation did not accept the evidence."),
            "instruction": str(
                feedback.get("instruction")
                or feedback.get("repair_guidance")
                or "Repair the current step so concrete evidence satisfies the exact step contract."
            ),
            "missing_information": ", ".join(
                str(item) for item in list(feedback.get("missing_evidence") or [])[:5]
            ),
        }
        for context in (step_context, step_request.session_context):
            context["operator_step_validation_feedback"] = dict(feedback)
            context["operator_bypass_lr_for_step_validation_retry"] = True
            notes = context.get("operator_policy_notes")
            if not isinstance(notes, list):
                notes = []
            context["operator_policy_notes"] = [*notes, note][-5:]

    @staticmethod
    def _streaming_state_with_step_validation_feedback(
        state: dict[str, Any],
        *,
        task: dict[str, Any],
        result: OperatorPipelineResult,
    ) -> dict[str, Any]:
        updated = dict(state)
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            return updated
        attempts = dict(updated.get("step_validation_attempts_by_task") or {})
        attempts[task_id] = int(attempts.get(task_id) or 0) + 1
        updated["step_validation_attempts_by_task"] = attempts
        feedback_by_task = dict(updated.get("step_validation_feedback_by_task") or {})
        feedback = dict(result.metadata.get("operator_step_validation_feedback") or {})
        feedback["attempt"] = attempts[task_id]
        feedback["decision"] = str(result.metadata.get("operator_step_validation_decision") or "")
        feedback_by_task[task_id] = feedback
        updated["step_validation_feedback_by_task"] = feedback_by_task
        return updated

    def _run_streaming_step_validation(
        self,
        *,
        user_request: UserRequest,
        step_request: UserRequest,
        state: dict[str, Any],
        task: dict[str, Any],
        plan: OperatorPlan,
        result: OperatorPipelineResult,
        context: dict[str, Any],
        observability: ObservabilityContext,
        trace: PlanningTrace,
    ) -> OperatorPipelineResult:
        config = self._runtime_config_for_context(context)
        if not config.llm_operator_step_validation_enabled:
            return result
        contract = self._streaming_step_validation_contract(user_request, state, task)
        evidence = self._streaming_step_artifact_evidence(
            contract=contract,
            plan=plan,
            result=result,
            context=context,
        )
        metadata = dict(result.metadata or {})
        validations = list(metadata.get("operator_step_validations") or [])
        trace_validations = list(trace.metadata.get("operator_step_validations") or [])
        observability.info(
            OPERATOR_STAGE,
            OPERATOR_STEP_VALIDATION_PROPOSED,
            "Step validation started",
            "The LLM is checking whether this streaming step satisfied its exact contract.",
            details={
                "task_id": contract.task_id,
                "step_id": contract.step_id,
                "step_index": contract.step_index,
                "requested_artifacts": list(contract.requested_artifacts),
            },
            debug_only=True,
        )
        try:
            prompt = build_operator_step_validation_prompt(
                step_request,
                plan,
                result.execution_records,
                contract,
                evidence,
            )
            review = structured_call(self.llm_client, prompt, OperatorStepValidationReview)
        except Exception as exc:
            diagnostics = getattr(exc, "diagnostics", None)
            payload = {
                "contract": contract.model_dump(mode="json"),
                "artifact_evidence": evidence,
                "error": str(exc),
                "diagnostics": diagnostics.model_dump(mode="json") if diagnostics is not None else None,
                "decision": "block",
            }
            validations.append(payload)
            trace_validations.append(payload)
            metadata["operator_step_validations"] = validations
            metadata["operator_step_validation_decision"] = "block"
            metadata["operator_step_validation_feedback"] = {
                "phase": "step_validation",
                "reason": "The step validation LLM response was unavailable or invalid.",
                "instruction": (
                    "Do not claim this step is complete until fresh evidence satisfies the exact step contract."
                ),
            }
            trace.metadata["operator_step_validations"] = trace_validations
            observability.error(
                OPERATOR_STAGE,
                OPERATOR_STEP_VALIDATION_REJECTED,
                "Step validation unavailable",
                "The typed LLM step validation failed, so the runtime will not mark the step complete.",
                details=payload,
            )
            return result.model_copy(
                update={
                    "status": "error",
                    "final_response": (
                        "## Step Validation Blocked\n\n"
                        "I could not validate that the current step satisfied the exact request, "
                        "so I stopped before claiming completion."
                    ),
                    "metadata": metadata,
                }
            )

        review_payload = review.model_dump(mode="json")
        payload = {
            "contract": contract.model_dump(mode="json"),
            "artifact_evidence": evidence,
            "review": review_payload,
            "decision": review.decision,
        }
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage=OPERATOR_STAGE,
                request_id=step_request.request_id,
                prompt_template_id="operator.step_validation",
                model_name=llm_client_metadata(self.llm_client)[0],
                raw_llm_response=None,
                parsed_proposal=review_payload,
                selected_candidate=payload,
            ),
        )
        validations.append(payload)
        trace_validations.append(payload)
        metadata["operator_step_validations"] = validations
        metadata["operator_step_validation_decision"] = review.decision
        metadata["operator_step_validation_feedback"] = {
            "phase": "step_validation",
            "reason": review.reason,
            "instruction": review.repair_guidance
            or "Revise the current step so concrete evidence satisfies its exact contract.",
            "missing_evidence": list(review.missing_evidence),
            "contract_violations": list(review.contract_violations),
        }
        trace.metadata["operator_step_validations"] = trace_validations
        if review.decision == "accept" and review.satisfied:
            observability.info(
                OPERATOR_STAGE,
                OPERATOR_STEP_VALIDATION_ACCEPTED,
                "Step validation accepted",
                "The LLM accepted that the current step satisfied its exact contract.",
                details={
                    "task_id": contract.task_id,
                    "step_id": contract.step_id,
                    "confidence": review.confidence,
                    "reason": review.reason,
                },
                debug_only=True,
            )
            return result.model_copy(update={"metadata": metadata})

        observability.warning(
            OPERATOR_STAGE,
            OPERATOR_STEP_VALIDATION_REJECTED,
            "Step validation rejected",
            "The LLM did not accept that the current step satisfied its exact contract.",
            details=payload,
        )
        guidance = review.repair_guidance or review.reason or "The step evidence did not satisfy the contract."
        markdown = "\n\n".join(
            [
                "## Step Validation Blocked",
                f"Decision: `{review.decision}`",
                self._bounded_step_validation_text(guidance, limit=2000),
            ]
        ).strip()
        return result.model_copy(
            update={
                "status": "error",
                "final_response": markdown,
                "metadata": metadata,
            }
        )
