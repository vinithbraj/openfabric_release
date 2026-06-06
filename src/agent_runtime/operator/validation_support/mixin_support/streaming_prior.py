"""Streaming prior-result binding helpers for operator plan validation."""

from __future__ import annotations

from agent_runtime.operator.validation_support.mixin_support.common import *
from agent_runtime.operator.validation_support.mixin_support.common import _ValidationConstantsMixin


class _ValidationStreamingPriorMixin(_ValidationConstantsMixin):
    @staticmethod
    def _execution_record_value(record: Any, field: str) -> Any:
        if isinstance(record, OperatorExecutionRecord):
            return getattr(record, field, None)
        if isinstance(record, dict):
            return record.get(field)
        return getattr(record, field, None)

    @classmethod
    def _streaming_prior_record_identity(
        cls,
        record: Any,
        task_result: dict[str, Any] | None = None,
    ) -> tuple[Any, ...]:
        """Return a stable identity for one durable prior record occurrence."""

        metadata = cls._execution_record_value(record, "metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        action_id = str(cls._execution_record_value(record, "action_id") or "")
        aliased_from = str(metadata.get("aliased_from_action_id") or "")
        if bool(metadata.get("streaming_prior_alias")) and aliased_from:
            action_id = aliased_from
        return (
            action_id,
            str(cls._execution_record_value(record, "task_id") or ""),
            str(cls._execution_record_value(record, "kind") or ""),
            str(cls._execution_record_value(record, "status") or ""),
            str(cls._execution_record_value(record, "exit_code") or ""),
            str(metadata.get("streaming_step_id") or ""),
            str(metadata.get("streaming_task_id") or ""),
            cls._streaming_prior_record_value_signature(
                cls._execution_record_value(record, "stdout")
            ),
            cls._streaming_prior_record_value_signature(
                cls._execution_record_value(record, "output")
            ),
        )

    @staticmethod
    def _streaming_prior_record_value_signature(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _python_referenced_input_names(cls, code: str | None) -> set[str]:
        text = str(code or "")
        if not text.strip():
            return set()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return set()
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                if node.value.id == "inputs":
                    slice_node = node.slice
                    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                        referenced.add(slice_node.value)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "inputs"
                    and node.func.attr == "get"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    referenced.add(node.args[0].value)
        return referenced

    @classmethod
    def _streaming_match_tokens(cls, text: Any) -> set[str]:
        tokens: set[str] = set()
        for raw_token in re.findall(r"[A-Za-z0-9]+", str(text or "").lower()):
            if len(raw_token) < 2 or raw_token in cls._STREAMING_PRIOR_MATCH_STOPWORDS:
                continue
            tokens.add(raw_token)
            if raw_token.endswith("ies") and len(raw_token) > 4:
                tokens.add(raw_token[:-3] + "y")
            elif raw_token.endswith("s") and len(raw_token) > 3:
                tokens.add(raw_token[:-1])
        return tokens

    @classmethod
    def _streaming_prior_records(
        cls,
        user_request: UserRequest,
    ) -> list[tuple[Any, dict[str, Any]]]:
        """Return prior streaming and seed records with their task payload."""

        context = dict(user_request.session_context or {})
        records: list[tuple[Any, dict[str, Any]]] = []
        prior_results = context.get("operator_streaming_prior_results")
        if isinstance(prior_results, dict):
            for task_result in list(prior_results.get("completed_tasks") or []):
                if not isinstance(task_result, dict):
                    continue
                for record in list(task_result.get("records") or []):
                    records.append((record, task_result))
        records.extend((record, {}) for record in list(context.get("operator_seed_records") or []))
        return records

    @classmethod
    def _streaming_prior_output_candidates(
        cls,
        user_request: UserRequest,
    ) -> list[dict[str, Any]]:
        """Return successful prior streaming outputs that can be bound by action id."""

        candidates: list[dict[str, Any]] = []
        seen_record_fields: set[tuple[tuple[Any, ...], str]] = set()
        for record, task_result in cls._streaming_prior_records(user_request):
            action_id = str(cls._execution_record_value(record, "action_id") or "").strip()
            if not action_id:
                continue
            status = str(cls._execution_record_value(record, "status") or "").strip().lower()
            if status and status != "success":
                continue
            exit_code = cls._execution_record_value(record, "exit_code")
            if exit_code not in (None, "", 0):
                continue
            for source_field in ("stdout", "output"):
                raw_value = cls._execution_record_value(record, source_field)
                if raw_value in (None, ""):
                    continue
                if isinstance(raw_value, str):
                    value = raw_value
                else:
                    try:
                        value = json.dumps(raw_value, sort_keys=True)
                    except (TypeError, ValueError):
                        value = str(raw_value)
                if not value.strip():
                    continue
                record_identity = cls._streaming_prior_record_identity(record, task_result)
                identity_key = (record_identity, source_field)
                if identity_key in seen_record_fields:
                    continue
                seen_record_fields.add(identity_key)
                metadata = cls._execution_record_value(record, "metadata")
                candidate = {
                    "action_id": action_id,
                    "task_id": str(cls._execution_record_value(record, "task_id") or ""),
                    "kind": str(cls._execution_record_value(record, "kind") or ""),
                    "source_field": source_field,
                    "value": value,
                    "task_description": (
                        str(task_result.get("description") or "") if isinstance(task_result, dict) else ""
                    ),
                    "final_response": (
                        str(task_result.get("final_response") or "") if isinstance(task_result, dict) else ""
                    ),
                    "metadata": dict(metadata or {}) if isinstance(metadata, dict) else {},
                    "record": record.model_dump(mode="json")
                    if isinstance(record, OperatorExecutionRecord)
                    else dict(record)
                    if isinstance(record, dict)
                    else {},
                    "index": len(candidates),
                }
                candidates.append(candidate)
        raw_counts: dict[tuple[str, str], int] = {}
        for candidate in candidates:
            key = (
                str(candidate.get("action_id") or ""),
                str(candidate.get("source_field") or "stdout"),
            )
            raw_counts[key] = raw_counts.get(key, 0) + 1
        for candidate in candidates:
            key = (
                str(candidate.get("action_id") or ""),
                str(candidate.get("source_field") or "stdout"),
            )
            candidate["raw_action_id_ambiguous"] = raw_counts.get(key, 0) > 1
        return candidates

    @classmethod
    def _streaming_prior_record_source_aliases(
        cls,
        record: Any,
        task_result: dict[str, Any] | None = None,
    ) -> set[str]:
        """Return source ids the planner may use for a prior record, including empty records."""

        aliases: set[str] = set()
        action_id = str(cls._execution_record_value(record, "action_id") or "").strip()
        task_id = str(cls._execution_record_value(record, "task_id") or "").strip()
        if action_id:
            aliases.add(action_id)
        if task_id:
            aliases.add(task_id)
        if action_id and task_id:
            aliases.add(f"{task_id}_{action_id}")
        if isinstance(task_result, dict):
            task_result_id = str(task_result.get("task_id") or "").strip()
            if task_result_id:
                aliases.add(task_result_id)
                if action_id:
                    aliases.add(f"{task_result_id}_{action_id}")
        metadata = cls._execution_record_value(record, "metadata")
        if isinstance(metadata, dict):
            for key in (
                "aliased_from_action_id",
                "streaming_task_id",
                "streaming_step_id",
            ):
                value = str(metadata.get(key) or "").strip()
                if value:
                    aliases.add(value)
            streaming_task_id = str(metadata.get("streaming_task_id") or "").strip()
            if action_id and streaming_task_id:
                aliases.add(f"{streaming_task_id}_{action_id}")
        normalized: set[str] = set()
        for alias in aliases:
            alias_text = str(alias or "").strip()
            if not alias_text:
                continue
            normalized.add(alias_text)
            normalized.add(re.sub(r"[^A-Za-z0-9_]+", "_", alias_text).strip("_"))
        return {alias for alias in normalized if alias}

    @classmethod
    def _streaming_prior_record_for_source(
        cls,
        user_request: UserRequest,
        source_action_id: str,
    ) -> tuple[Any, dict[str, Any]] | None:
        source = str(source_action_id or "").strip()
        if not source:
            return None
        matches: list[tuple[Any, dict[str, Any]]] = []
        seen: set[tuple[Any, ...]] = set()
        for record, task_result in cls._streaming_prior_records(user_request):
            if source in cls._streaming_prior_record_source_aliases(record, task_result):
                identity = cls._streaming_prior_record_identity(record, task_result)
                if identity in seen:
                    continue
                seen.add(identity)
                matches.append((record, task_result))
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def _streaming_prior_source_aliases(cls, candidate: dict[str, Any]) -> set[str]:
        """Return source ids the planner may use for a prior streaming record."""

        aliases: set[str] = set()
        action_id = str(candidate.get("action_id") or "").strip()
        task_id = str(candidate.get("task_id") or "").strip()
        if action_id:
            aliases.add(action_id)
        if task_id:
            aliases.add(task_id)
        if action_id and task_id:
            aliases.add(f"{task_id}_{action_id}")
        aliases.add(cls._streaming_prior_alias_for_candidate(candidate))

        metadata = candidate.get("metadata")
        if isinstance(metadata, dict):
            for key in (
                "aliased_from_action_id",
                "streaming_task_id",
                "streaming_step_id",
            ):
                value = str(metadata.get(key) or "").strip()
                if value:
                    aliases.add(value)
            streaming_task_id = str(metadata.get("streaming_task_id") or "").strip()
            if action_id and streaming_task_id:
                aliases.add(f"{streaming_task_id}_{action_id}")

        normalized: set[str] = set()
        for alias in aliases:
            alias_text = str(alias or "").strip()
            if not alias_text:
                continue
            normalized.add(alias_text)
            normalized.add(re.sub(r"[^A-Za-z0-9_]+", "_", alias_text).strip("_"))
        return {alias for alias in normalized if alias}

    @classmethod
    def _input_name_looks_generated_text(cls, input_name: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(input_name or "").strip().lower()).strip("_")
        if not normalized:
            return False
        parts = set(normalized.split("_"))
        return bool(
            normalized
            in {
                "body",
                "commit_message",
                "description",
                "message",
                "summary",
                "subject",
                "text",
                "title",
            }
            or parts
            & {
                "body",
                "description",
                "message",
                "summary",
                "subject",
                "text",
                "title",
            }
        )

    @classmethod
    def _streaming_task_can_consume_generated_text(cls, user_request: UserRequest) -> bool:
        context = dict(user_request.session_context or {})
        task = context.get("operator_streaming_current_task")
        if not isinstance(task, dict):
            return False
        text = " ".join(
            str(item or "")
            for item in (
                task.get("id"),
                task.get("task_id"),
                task.get("description"),
                task.get("goal"),
                task.get("semantic_verb"),
                task.get("object_type"),
            )
        ).lower()
        return bool(
            re.search(
                r"\b(commit|send|post|publish|write|save|use|apply|"
                r"generated|drafted|composed|message|description|summary|body|title|text)\b",
                text,
            )
        )

    @classmethod
    def _streaming_task_can_transform_prior_output(cls, user_request: UserRequest) -> bool:
        context = dict(user_request.session_context or {})
        task = context.get("operator_streaming_current_task")
        if not isinstance(task, dict):
            return False
        semantic_verb = str(task.get("semantic_verb") or "").strip().lower()
        if semantic_verb in cls._STREAMING_PRIOR_TRANSFORM_VERBS:
            return True
        description = str(task.get("description") or task.get("goal") or "").lower()
        return any(
            word in description
            for word in (
                "calculate",
                "extract",
                "total",
                "sum",
                "summarize",
                "filter",
                "sort",
                "select",
                "count",
                "transform",
            )
        )

    @classmethod
    def _streaming_task_can_consume_prior_output(cls, user_request: UserRequest) -> bool:
        context = dict(user_request.session_context or {})
        task = context.get("operator_streaming_current_task")
        if not isinstance(task, dict):
            return False
        semantic_verb = str(task.get("semantic_verb") or "").strip().lower()
        if (
            semantic_verb in cls._STREAMING_PRIOR_CONSUMER_VERBS
            or semantic_verb in cls._STREAMING_PRIOR_TRANSFORM_VERBS
        ):
            return True
        description = str(task.get("description") or task.get("goal") or "").lower()
        return bool(
            re.search(
                r"\b(save|write|append|store|persist|export|send|upload|post|publish|"
                r"create\s+(?:a\s+)?(?:file|artifact|report)|to\s+(?:a\s+)?file|"
                r"previous|prior|earlier|upstream|stdout|output|result|results|"
                r"calculated|computed|generated|found|discovered|selected|extracted|listed)\b",
                description,
            )
        )

    @classmethod
    def _streaming_task_can_persist_prior_output(cls, user_request: UserRequest) -> bool:
        context = dict(user_request.session_context or {})
        task = context.get("operator_streaming_current_task")
        if not isinstance(task, dict):
            return False
        semantic_verb = str(task.get("semantic_verb") or "").strip().lower()
        if semantic_verb in {
            "append",
            "create",
            "deliver",
            "export",
            "persist",
            "post",
            "publish",
            "save",
            "send",
            "store",
            "upload",
            "write",
        }:
            return True
        description = str(task.get("description") or task.get("goal") or "").lower()
        return bool(
            re.search(
                r"\b(save|write|append|store|persist|export|send|upload|post|publish|"
                r"create\s+(?:a\s+)?(?:file|artifact|report)|to\s+(?:a\s+)?file)\b",
                description,
            )
        )

    @classmethod
    def _streaming_current_task_text(cls, user_request: UserRequest) -> str:
        context = dict(user_request.session_context or {})
        task = context.get("operator_streaming_current_task")
        if not isinstance(task, dict):
            return ""
        return " ".join(
            str(item or "")
            for item in (
                task.get("id"),
                task.get("task_id"),
                task.get("description"),
                task.get("goal"),
                task.get("semantic_verb"),
                task.get("object_type"),
                task.get("operation_intent"),
            )
        )

    @classmethod
    def _streaming_task_requires_per_entity_scope(cls, user_request: UserRequest) -> bool:
        text = cls._streaming_current_task_text(user_request)
        if not text.strip():
            return False
        if cls._PER_ENTITY_SCOPE_RE.search(text):
            return True
        if not cls._ALL_ENTITY_SCOPE_RE.search(text):
            return False
        if cls._PER_ENTITY_AGGREGATE_RE.search(text):
            return False
        return bool(cls._PER_ENTITY_REPORT_RE.search(text))

    @classmethod
    def _action_uses_streaming_prior_output(
        cls,
        action: OperatorAction,
        candidates: list[dict[str, Any]],
    ) -> bool:
        if not action.input_bindings:
            return False
        for binding in action.input_bindings:
            source_action_id = str(binding.source_action_id or "").strip()
            if not source_action_id:
                continue
            for candidate in candidates:
                if source_action_id in cls._streaming_prior_source_aliases(candidate):
                    return True
        return False

    @classmethod
    def _action_text_for_per_entity_scope(cls, action: OperatorAction) -> str:
        return "\n".join(
            str(item or "")
            for item in (
                action.command,
                action.code,
                action.llm_prompt,
            )
            if item not in (None, "")
        )

    @classmethod
    def _action_enumerates_per_entity_scope(cls, action: OperatorAction) -> bool:
        return bool(cls._PER_ENTITY_ENUMERATION_RE.search(cls._action_text_for_per_entity_scope(action)))

    @classmethod
    def _action_uses_global_scalar_selector(cls, action: OperatorAction) -> bool:
        text = cls._action_text_for_per_entity_scope(action)
        return bool(
            cls._PER_ENTITY_SCALAR_SELECTOR_RE.search(text)
            or cls._PER_ENTITY_CURRENT_ONLY_SELECTOR_RE.search(text)
        )

    @classmethod
    def _looks_like_streaming_prior_literal(cls, value: Any, candidate_value: str) -> bool:
        if not isinstance(value, str):
            return False
        text = value.strip()
        if not text:
            return False
        candidate = str(candidate_value or "").strip()
        if candidate:
            prefix = candidate[: min(160, len(candidate))]
            if prefix and (text.startswith(prefix) or prefix in text):
                return True
            text_prefix = text[: min(160, len(text))]
            if text_prefix and text_prefix in candidate:
                return True
        if len(text) < cls._STREAMING_PRIOR_LITERAL_MIN_CHARS:
            return False
        if len(candidate) < cls._STREAMING_PRIOR_LITERAL_MIN_CHARS:
            return False
        text_structured = any(separator in text for separator in ("\n", "\t", "\r"))
        candidate_structured = any(separator in candidate for separator in ("\n", "\t", "\r"))
        if not (text_structured and candidate_structured):
            return False
        return bool(cls._streaming_match_tokens(text) & cls._streaming_match_tokens(candidate))

    @staticmethod
    def _value_is_multiline_payload(value: Any) -> bool:
        text = str(value if value is not None else "").strip()
        return "\n" in text or "\r" in text

    @classmethod
    def _streaming_prior_binding_target(
        cls,
        action: OperatorAction,
        candidates: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]] | None:
        if action.kind not in {"python_action", "python_transform"}:
            return None
        if action.input_bindings or action.defer_code_generation:
            return None
        if not isinstance(action.inputs, dict) or not action.inputs:
            return None

        for input_name in ("stdout", "output", "records", "rows", "data", "text"):
            if input_name not in action.inputs:
                continue
            preferred_field = "output" if input_name == "output" else "stdout"
            ordered_candidates = [
                *[item for item in candidates if item.get("source_field") == preferred_field],
                *[item for item in candidates if item.get("source_field") != preferred_field],
            ]
            for candidate in ordered_candidates:
                if cls._looks_like_streaming_prior_literal(action.inputs.get(input_name), candidate["value"]):
                    return input_name, candidate
        return None

    @classmethod
    def _streaming_prior_candidate_score(
        cls,
        user_request: UserRequest,
        action: OperatorAction,
        candidate: dict[str, Any],
    ) -> int:
        context = dict(user_request.session_context or {})
        current_task = context.get("operator_streaming_current_task")
        current_task = current_task if isinstance(current_task, dict) else {}
        current_text = " ".join(
            str(item or "")
            for item in (
                current_task.get("description"),
                current_task.get("semantic_verb"),
                current_task.get("object_type"),
                action.task_id,
                action.reason,
                action.effect_summary,
                action.declared_output_shape,
            )
        )
        candidate_text = " ".join(
            str(item or "")
            for item in (
                candidate.get("task_id"),
                candidate.get("task_description"),
                candidate.get("final_response"),
                candidate.get("action_id"),
            )
        )
        overlap = cls._streaming_match_tokens(current_text) & cls._streaming_match_tokens(candidate_text)
        score = len(overlap) * 10
        if str(candidate.get("action_id") or "") in set(action.depends_on or []):
            score += 100
        if str(candidate.get("source_field") or "") == "stdout":
            score += 2
        return score

    @classmethod
    def _best_streaming_prior_candidate(
        cls,
        user_request: UserRequest,
        action: OperatorAction,
        candidates: list[dict[str, Any]],
        *,
        preferred_field: str,
    ) -> dict[str, Any] | None:
        ordered_candidates = [
            *[item for item in candidates if item.get("source_field") == preferred_field],
            *[item for item in candidates if item.get("source_field") != preferred_field],
        ]
        if not ordered_candidates:
            return None
        scored = [
            (cls._streaming_prior_candidate_score(user_request, action, item), int(item.get("index") or 0), item)
            for item in ordered_candidates
        ]
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_score, _, best = scored[0]
        if len(scored) == 1:
            return best
        second_score = scored[1][0]
        if best_score >= 15 and best_score >= second_score + 5:
            return best
        return None

    @classmethod
    def _streaming_prior_missing_binding_target(
        cls,
        user_request: UserRequest,
        action: OperatorAction,
        candidates: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]] | None:
        if action.kind not in {"python_action", "python_transform"}:
            return None
        if action.input_bindings:
            return None
        supplied_inputs = {str(name or "").strip() for name in dict(action.inputs or {})}
        if action.defer_code_generation:
            referenced = {"stdout"}
        else:
            referenced = cls._python_referenced_input_names(action.code)
        for input_name in cls._STREAMING_PRIOR_BINDING_INPUT_NAMES:
            if input_name not in referenced or input_name in supplied_inputs:
                continue
            preferred_field = "output" if input_name == "output" else "stdout"
            candidate = cls._best_streaming_prior_candidate(
                user_request,
                action,
                candidates,
                preferred_field=preferred_field,
            )
            if candidate is not None:
                return input_name, candidate
        return None

    @staticmethod
    def _streaming_prior_alias_for_candidate(candidate: dict[str, Any]) -> str:
        """Return a stable in-plan alias for a prior streaming action id."""

        action_id = re.sub(r"[^A-Za-z0-9_]+", "_", str(candidate.get("action_id") or "action")).strip("_")
        task_id = re.sub(r"[^A-Za-z0-9_]+", "_", str(candidate.get("task_id") or "task")).strip("_")
        index = int(candidate.get("index") or 0)
        return f"streaming_prior_{index + 1}_{task_id or 'task'}_{action_id or 'action'}"

    @staticmethod
    def _ensure_streaming_prior_alias_seed(
        user_request: UserRequest,
        *,
        candidate: dict[str, Any],
        alias_action_id: str,
    ) -> None:
        """Expose an aliased prior record so execution can resolve deconflicted bindings."""

        if not alias_action_id:
            return
        record = candidate.get("record")
        if isinstance(record, OperatorExecutionRecord):
            payload = record.model_dump(mode="json")
        elif isinstance(record, dict):
            payload = dict(record)
        else:
            payload = {}
        if not payload:
            return
        original_action_id = str(payload.get("action_id") or candidate.get("action_id") or "").strip()
        payload["action_id"] = alias_action_id
        metadata = dict(payload.get("metadata") or {})
        metadata["streaming_prior_alias"] = True
        metadata["aliased_from_action_id"] = original_action_id
        payload["metadata"] = metadata
        seed_records = user_request.session_context.setdefault("operator_seed_records", [])
        if not isinstance(seed_records, list):
            seed_records = []
            user_request.session_context["operator_seed_records"] = seed_records
        if any(
            isinstance(item, dict) and str(item.get("action_id") or "") == alias_action_id
            for item in seed_records
        ):
            return
        seed_records.append(payload)

    def _streaming_prior_binding_source_action_id(
        self,
        user_request: UserRequest,
        *,
        action: OperatorAction,
        candidate: dict[str, Any],
        preferred_source_action_id: str | None = None,
    ) -> str:
        """Return a safe binding source id for a prior record in the current step plan."""

        source_action_id = str(candidate.get("action_id") or "").strip()
        preferred = str(preferred_source_action_id or "").strip()
        if bool(candidate.get("raw_action_id_ambiguous")):
            alias = self._streaming_prior_alias_for_candidate(candidate)
            self._ensure_streaming_prior_alias_seed(
                user_request,
                candidate=candidate,
                alias_action_id=alias,
            )
            return alias
        if (
            preferred
            and preferred != source_action_id
            and preferred in self._streaming_prior_source_aliases(candidate)
        ):
            self._ensure_streaming_prior_alias_seed(
                user_request,
                candidate=candidate,
                alias_action_id=preferred,
            )
            return preferred
        if source_action_id and source_action_id == str(action.action_id or "").strip():
            alias = self._streaming_prior_alias_for_candidate(candidate)
            self._ensure_streaming_prior_alias_seed(
                user_request,
                candidate=candidate,
                alias_action_id=alias,
            )
            return alias
        return source_action_id

    @classmethod
    def _streaming_prior_candidates_for_binding_source(
        cls,
        *,
        source_action_id: str,
        source_field: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source = str(source_action_id or "").strip()
        field = str(source_field or "stdout").strip() or "stdout"
        if not source:
            return []
        field_matches = [
            candidate
            for candidate in candidates
            if str(candidate.get("source_field") or "stdout") == field
            and source in cls._streaming_prior_source_aliases(candidate)
        ]
        if field_matches:
            return field_matches
        return [
            candidate
            for candidate in candidates
            if source in cls._streaming_prior_source_aliases(candidate)
        ]

    def _deconflict_streaming_current_action_ids(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
    ) -> None:
        """Give current-step actions fresh ids when they collide with prior records."""

        context = dict(user_request.session_context or {})
        if not isinstance(context.get("operator_streaming_current_task"), dict):
            return
        prior_action_ids = {
            str(self._execution_record_value(record, "action_id") or "").strip()
            for record, _task_result in self._streaming_prior_records(user_request)
        }
        prior_action_ids.discard("")
        if not prior_action_ids:
            return
        current_counts: dict[str, int] = {}
        for action in list(plan.actions or []):
            action_id = str(action.action_id or "").strip()
            current_counts[action_id] = current_counts.get(action_id, 0) + 1
        current_ids = {
            str(action.action_id or "").strip()
            for action in list(plan.actions or [])
            if str(action.action_id or "").strip()
        }
        rename_map: dict[str, str] = {}
        renamed_by_object: dict[int, tuple[str, str]] = {}
        changes: list[dict[str, Any]] = []
        for action in list(plan.actions or []):
            original_action_id = str(action.action_id or "").strip()
            if (
                not original_action_id
                or original_action_id not in prior_action_ids
                or current_counts.get(original_action_id, 0) != 1
            ):
                continue
            new_action_id = new_id("action")
            while new_action_id in prior_action_ids or new_action_id in current_ids:
                new_action_id = new_id("action")
            action.action_id = new_action_id
            rename_map[original_action_id] = new_action_id
            renamed_by_object[id(action)] = (original_action_id, new_action_id)
            current_ids.add(new_action_id)
            changes.append(
                {
                    "task_id": action.task_id,
                    "original_action_id": original_action_id,
                    "new_action_id": new_action_id,
                    "reason": "current_action_id_collided_with_prior_streaming_record",
                }
            )
        if not rename_map:
            return

        candidates = self._streaming_prior_output_candidates(user_request)
        for action in list(plan.actions or []):
            own_original_id, _own_new_id = renamed_by_object.get(id(action), ("", ""))
            rewritten_bindings: list[OperatorInputBinding] = []
            bindings_changed = False
            for binding in list(action.input_bindings or []):
                source_action_id = str(binding.source_action_id or "").strip()
                if source_action_id not in rename_map:
                    rewritten_bindings.append(binding)
                    continue
                if own_original_id and source_action_id == own_original_id:
                    matches = self._streaming_prior_candidates_for_binding_source(
                        source_action_id=source_action_id,
                        source_field=str(binding.source_field or "stdout"),
                        candidates=candidates,
                    )
                    candidate = matches[0] if len(matches) == 1 else None
                    if candidate is not None:
                        alias_action_id = self._streaming_prior_alias_for_candidate(candidate)
                        self._ensure_streaming_prior_alias_seed(
                            user_request,
                            candidate=candidate,
                            alias_action_id=alias_action_id,
                        )
                        rewritten_bindings.append(
                            binding.model_copy(update={"source_action_id": alias_action_id})
                        )
                        self._rewrite_streaming_prior_dependency_references(
                            plan,
                            action=action,
                            original_source_action_id=source_action_id,
                            alias_action_id=alias_action_id,
                        )
                        bindings_changed = True
                        changes.append(
                            {
                                "task_id": action.task_id,
                                "action_id": action.action_id,
                                "input_name": binding.input_name,
                                "original_source_action_id": source_action_id,
                                "source_action_id": alias_action_id,
                                "reason": "self_binding_rewritten_to_prior_alias",
                            }
                        )
                        continue
                rewritten_bindings.append(
                    binding.model_copy(update={"source_action_id": rename_map[source_action_id]})
                )
                bindings_changed = True
            if bindings_changed:
                action.input_bindings = rewritten_bindings

            rewritten_depends_on: list[str] = []
            depends_changed = False
            for dependency in list(action.depends_on or []):
                dependency_id = str(dependency or "").strip()
                if dependency_id not in rename_map:
                    rewritten_depends_on.append(dependency)
                    continue
                if own_original_id and dependency_id == own_original_id:
                    matches = self._streaming_prior_candidates_for_binding_source(
                        source_action_id=dependency_id,
                        source_field="stdout",
                        candidates=candidates,
                    )
                    if len(matches) == 1:
                        alias_action_id = self._streaming_prior_alias_for_candidate(matches[0])
                        self._ensure_streaming_prior_alias_seed(
                            user_request,
                            candidate=matches[0],
                            alias_action_id=alias_action_id,
                        )
                        rewritten_depends_on.append(alias_action_id)
                        depends_changed = True
                        continue
                rewritten_depends_on.append(rename_map[dependency_id])
                depends_changed = True
            if depends_changed:
                action.depends_on = rewritten_depends_on

        for dependency in list(plan.dependencies or []):
            producer_id = str(dependency.producer_action_id or "").strip()
            consumer_id = str(dependency.consumer_action_id or "").strip()
            if consumer_id in rename_map:
                dependency.consumer_action_id = rename_map[consumer_id]
            if producer_id in rename_map:
                if producer_id == consumer_id:
                    matches = self._streaming_prior_candidates_for_binding_source(
                        source_action_id=producer_id,
                        source_field="stdout",
                        candidates=candidates,
                    )
                    consumer_action = next(
                        (
                            action
                            for action in list(plan.actions or [])
                            if str(action.action_id or "") == dependency.consumer_action_id
                        ),
                        None,
                    )
                    if len(matches) == 1 and consumer_action is not None:
                        alias_action_id = self._streaming_prior_alias_for_candidate(matches[0])
                        self._ensure_streaming_prior_alias_seed(
                            user_request,
                            candidate=matches[0],
                            alias_action_id=alias_action_id,
                        )
                        dependency.producer_action_id = alias_action_id
                    else:
                        dependency.producer_action_id = rename_map[producer_id]
                else:
                    dependency.producer_action_id = rename_map[producer_id]

        self._emit(
            observability,
            level="info",
            event_type="operator.streaming_action_ids.deconflicted",
            title="Streaming action ids deconflicted",
            summary="The runtime assigned fresh ids to current-step actions that collided with prior records.",
            details={"actions": changes},
        )

    @staticmethod
    def _rewrite_streaming_prior_dependency_references(
        plan: OperatorPlan,
        *,
        action: OperatorAction,
        original_source_action_id: str,
        alias_action_id: str,
    ) -> None:
        if not original_source_action_id or not alias_action_id:
            return
        if action.depends_on:
            action.depends_on = [
                alias_action_id if str(item) == original_source_action_id else item
                for item in action.depends_on
            ]
        for dependency in plan.dependencies:
            if (
                dependency.consumer_action_id == action.action_id
                and dependency.producer_action_id == original_source_action_id
            ):
                dependency.producer_action_id = alias_action_id

    @classmethod
    def _streaming_prior_candidate_for_binding(
        cls,
        user_request: UserRequest,
        *,
        action: OperatorAction,
        binding: OperatorInputBinding,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Resolve a planner-authored binding to a prior streaming candidate."""

        source_action_id = str(binding.source_action_id or "").strip()
        source_field = str(binding.source_field or "stdout").strip() or "stdout"
        generated_text_input = cls._input_name_looks_generated_text(binding.input_name)

        matched_candidates = cls._streaming_prior_candidates_for_binding_source(
            source_action_id=source_action_id,
            source_field=source_field,
            candidates=candidates,
        )
        if len(matched_candidates) > 1:
            return None
        if len(matched_candidates) == 1:
            candidate = matched_candidates[0]
            if generated_text_input and str(candidate.get("kind") or "") != "llm_text":
                return None
            return candidate

        llm_text_candidates = [
            item for item in candidates if str(item.get("kind") or "") == "llm_text"
        ]
        if not llm_text_candidates:
            return None
        if generated_text_input or cls._streaming_task_can_consume_generated_text(user_request):
            if len(llm_text_candidates) == 1:
                return llm_text_candidates[0]
            return cls._best_streaming_prior_candidate(
                user_request,
                action,
                llm_text_candidates,
                preferred_field=source_field,
            )
        return None

    def _streaming_prior_ambiguous_binding_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject raw prior ids that can name more than one prior record."""

        candidates = self._streaming_prior_output_candidates(user_request)
        if not candidates:
            return []
        errors: list[dict[str, Any]] = []
        for action in list(plan.actions or []):
            for binding in list(action.input_bindings or []):
                source_action_id = str(binding.source_action_id or "").strip()
                if not source_action_id:
                    continue
                matches = self._streaming_prior_candidates_for_binding_source(
                    source_action_id=source_action_id,
                    source_field=str(binding.source_field or "stdout"),
                    candidates=candidates,
                )
                if len(matches) <= 1:
                    continue
                errors.append(
                    {
                        "error": "ambiguous_streaming_prior_binding",
                        "message": (
                            "This input binding references a prior streaming id that "
                            "matches multiple prior records."
                        ),
                        "action_id": action.action_id,
                        "task_id": action.task_id,
                        "input_name": binding.input_name,
                        "source_action_id": binding.source_action_id,
                        "source_field": binding.source_field,
                        "candidate_action_ids": [
                            str(candidate.get("action_id") or "") for candidate in matches
                        ],
                        "candidate_aliases": [
                            self._streaming_prior_alias_for_candidate(candidate)
                            for candidate in matches
                        ],
                        "repair_hint": (
                            "Bind the exact scoped streaming_prior_* alias for the intended "
                            "prior record instead of the raw repeated action id."
                        ),
                    }
                )
        return errors

    def _normalize_streaming_prior_python_inputs(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability: ObservabilityContext | None = None,
    ) -> None:
        """Replace pasted prior streaming stdout with a real binding and deferred code."""

        if not self._streaming_task_can_transform_prior_output(user_request):
            return
        candidates = self._streaming_prior_output_candidates(user_request)
        if not candidates:
            return

        changes: list[dict[str, Any]] = []
        candidates_by_action_field = {
            (str(item.get("action_id") or ""), str(item.get("source_field") or "")): item
            for item in candidates
        }
        for action in plan.actions:
            rewritten_bindings: list[OperatorInputBinding] = []
            bindings_changed = False
            for binding in action.input_bindings:
                source_action_id = str(binding.source_action_id or "").strip()
                source_field = str(binding.source_field or "stdout").strip()
                candidate = candidates_by_action_field.get((source_action_id, source_field))
                if candidate is None and source_field != "stdout":
                    candidate = candidates_by_action_field.get((source_action_id, "stdout"))
                if candidate is not None and source_action_id == str(action.action_id or "").strip():
                    alias_action_id = self._streaming_prior_binding_source_action_id(
                        user_request,
                        action=action,
                        candidate=candidate,
                    )
                    rewritten_bindings.append(
                        binding.model_copy(update={"source_action_id": alias_action_id})
                    )
                    self._rewrite_streaming_prior_dependency_references(
                        plan,
                        action=action,
                        original_source_action_id=source_action_id,
                        alias_action_id=alias_action_id,
                    )
                    bindings_changed = True
                    changes.append(
                        {
                            "action_id": action.action_id,
                            "task_id": action.task_id,
                            "input_name": binding.input_name,
                            "source_action_id": alias_action_id,
                            "source_field": binding.source_field,
                            "original_source_action_id": source_action_id,
                            "normalization": "streaming_prior_action_id_alias",
                        }
                    )
                else:
                    rewritten_bindings.append(binding)
            if bindings_changed:
                action.input_bindings = rewritten_bindings
            target = self._streaming_prior_binding_target(action, candidates)
            target_kind = "literal_prior_output"
            if target is None:
                target = self._streaming_prior_missing_binding_target(
                    user_request,
                    action,
                    candidates,
                )
                target_kind = "missing_prior_output_binding"
            if target is None:
                continue
            input_name, candidate = target
            source_action_id = self._streaming_prior_binding_source_action_id(
                user_request,
                action=action,
                candidate=candidate,
            )
            cleaned_inputs = dict(action.inputs)
            literal_preview = str(cleaned_inputs.pop(input_name, "") or "")[:200]
            action.inputs = cleaned_inputs
            action.input_bindings = [
                OperatorInputBinding(
                    input_name=input_name,
                    source_action_id=source_action_id,
                    source_field=candidate["source_field"],  # type: ignore[arg-type]
                    required=True,
                    fallback_value=None,
                )
            ]
            self._rewrite_streaming_prior_dependency_references(
                plan,
                action=action,
                original_source_action_id=str(candidate.get("action_id") or ""),
                alias_action_id=source_action_id,
            )
            action.code = None
            action.defer_code_generation = True
            changes.append(
                {
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "input_name": input_name,
                    "source_action_id": source_action_id,
                    "source_field": candidate["source_field"],
                    "original_source_action_id": candidate["action_id"],
                    "removed_literal_preview": literal_preview,
                    "normalization": target_kind,
                }
            )

        if changes:
            self._emit(
                observability,
                level="info",
                event_type="operator.streaming_prior_input_bound",
                title="Streaming prior output bound",
                summary=(
                    "The runtime replaced pasted prior streaming output with "
                    "input_bindings and deferred Python code generation."
                ),
                details={"actions": changes},
            )



__all__ = ["_ValidationStreamingPriorMixin"]
