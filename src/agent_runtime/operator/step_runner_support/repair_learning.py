"""Execution repair and learning digest helpers for the operator step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class _StepRunnerRepairLearningMixin:
    def _repair_seed_records(
        self,
        original_plan: OperatorPlan,
        repaired_plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
    ) -> list[OperatorExecutionRecord]:
        """Return successful records that are still valid for a repaired plan."""

        original_actions = {action.action_id: action for action in original_plan.actions}
        successful_records = {
            record.action_id: record
            for record in records
            if record.status == "success"
        }
        repaired_action_ids = {action.action_id for action in repaired_plan.actions}
        referenced_action_ids: set[str] = set()
        for action in repaired_plan.actions:
            referenced_action_ids.update(str(action_id) for action_id in action.depends_on)
            referenced_action_ids.update(
                str(binding.source_action_id) for binding in action.input_bindings
            )
        referenced_action_ids.update(
            str(dependency.producer_action_id)
            for dependency in repaired_plan.dependencies
            if str(dependency.consumer_action_id) in repaired_action_ids
        )
        reusable: dict[str, OperatorExecutionRecord] = {}
        for action_id, record in successful_records.items():
            if action_id not in original_actions and action_id in referenced_action_ids:
                reusable[action_id] = record
        for action in self._execution_order(repaired_plan):
            record = successful_records.get(action.action_id)
            original_action = original_actions.get(action.action_id)
            if record is None or original_action is None:
                continue
            if (
                self._action_execution_fingerprint(original_action)
                != self._action_execution_fingerprint(action)
            ):
                continue
            dependency_ids = set(action.depends_on)
            dependency_ids.update(binding.source_action_id for binding in action.input_bindings)
            dependency_ids.update(
                dependency.producer_action_id
                for dependency in repaired_plan.dependencies
                if dependency.consumer_action_id == action.action_id
            )
            if any(dependency_id not in reusable for dependency_id in dependency_ids):
                continue
            reusable[action.action_id] = record
        return list(reusable.values())

    @staticmethod
    def _operator_seed_record_payloads(
        user_request: UserRequest,
        execution_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return deduped seed records from execution context plus validation-added aliases."""

        payloads: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        sources = [
            list(execution_context.get("operator_seed_records") or []),
            list(dict(user_request.session_context or {}).get("operator_seed_records") or []),
        ]
        for source in sources:
            for item in source:
                if isinstance(item, OperatorExecutionRecord):
                    payload = item.model_dump(mode="json")
                elif isinstance(item, dict):
                    payload = dict(item)
                else:
                    continue
                action_id = str(payload.get("action_id") or "").strip()
                if not action_id:
                    continue
                metadata = dict(payload.get("metadata") or {})
                key = (
                    action_id,
                    str(payload.get("status") or ""),
                    str(metadata.get("streaming_prior_alias") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                payloads.append(payload)
        return payloads

    @staticmethod
    def _action_mutation_replay_signature(action: OperatorAction) -> str:
        """Return a signature for detecting duplicated completed mutations."""

        return _stable_json(
            {
                "kind": action.kind,
                "command": action.command,
                "code": action.code,
                "cwd": action.cwd,
                "inputs": action.inputs,
                "input_bindings": [
                    binding.model_dump(mode="json") for binding in action.input_bindings
                ],
                "stdin_mode": getattr(action, "stdin_mode", "none"),
                "stdin_text": getattr(action, "stdin_text", None),
                "stdin_input_name": getattr(action, "stdin_input_name", None),
                "execution_mode": action.execution_mode,
                "interaction_mode": action.interaction_mode,
            }
        )

    def _execution_repair_completed_mutation_errors(
        self,
        original_plan: OperatorPlan,
        repaired_plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        seed_records: list[OperatorExecutionRecord],
    ) -> list[dict[str, Any]]:
        """Reject repaired plans that would silently repeat completed mutations."""

        seeded_ids = {record.action_id for record in seed_records if record.status == "success"}
        repaired_actions = {action.action_id: action for action in repaired_plan.actions}
        repaired_signatures = {
            action.action_id: self._action_mutation_replay_signature(action)
            for action in repaired_plan.actions
            if self._action_requires_confirmation(repaired_plan, action)
        }
        errors: list[dict[str, Any]] = []
        for original_action in original_plan.actions:
            if not self._action_requires_confirmation(original_plan, original_action):
                continue
            record = next(
                (
                    item
                    for item in records
                    if item.action_id == original_action.action_id and item.status == "success"
                ),
                None,
            )
            if record is None:
                continue
            repaired_same_id = repaired_actions.get(original_action.action_id)
            if repaired_same_id is not None and original_action.action_id not in seeded_ids:
                errors.append(
                    {
                        "error": "execution_repair_changes_completed_mutation",
                        "message": (
                            "Execution repair changed an already successful mutating action. "
                            "Preserve the completed action unchanged with the same action_id, "
                            "or continue only with later unfinished work."
                        ),
                        "action_id": original_action.action_id,
                    }
                )
            original_signature = self._action_mutation_replay_signature(original_action)
            for repaired_action in repaired_plan.actions:
                if repaired_action.action_id == original_action.action_id:
                    continue
                if repaired_signatures.get(repaired_action.action_id) != original_signature:
                    continue
                errors.append(
                    {
                        "error": "execution_repair_duplicates_completed_mutation",
                        "message": (
                            "Execution repair introduced a new action that duplicates an "
                            "already successful mutating action. Reuse the completed record "
                            "or continue only with unfinished work."
                        ),
                        "action_id": repaired_action.action_id,
                        "completed_action_id": original_action.action_id,
                    }
                )
        return errors

    @staticmethod
    def _record_has_repeat_blocking_shell_diagnostic(record: OperatorExecutionRecord) -> bool:
        """Return whether a failed shell record should not be replayed verbatim."""

        if record.status != "error":
            return False
        metadata = dict(record.metadata or {})
        if bool(metadata.get("cancelled")) or bool(metadata.get("timeout")):
            return False
        classifier = metadata.get("stdout_error_classifier")
        return bool(metadata.get("stdout_diagnostic_error")) or (
            isinstance(classifier, dict) and classifier.get("stdout_usable") is False
        ) or bool(record.error or record.stderr or record.stdout)

    def _execution_repair_repeated_failed_command_errors(
        self,
        original_plan: OperatorPlan,
        repaired_plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
    ) -> list[dict[str, Any]]:
        """Reject repairs that replay an invocation already classified as diagnostic failure."""

        original_actions = {
            action.action_id: action
            for action in original_plan.actions
            if action.kind == "shell_command"
        }
        failed_commands: dict[str, dict[str, Any]] = {}
        for record in records:
            if not self._record_has_repeat_blocking_shell_diagnostic(record):
                continue
            action = original_actions.get(record.action_id)
            if action is None:
                continue
            normalized = normalize_operator_command(str(action.command or ""))
            if not normalized:
                continue
            failed_commands[record.action_id] = {
                "normalized_command": normalized,
                "command_hash": operator_command_hash(str(action.command or "")),
                "classifier": dict(record.metadata.get("stdout_error_classifier") or {}),
            }
        if not failed_commands:
            return []

        errors: list[dict[str, Any]] = []
        for repaired_action in repaired_plan.actions:
            if repaired_action.kind != "shell_command":
                continue
            repaired_normalized = normalize_operator_command(str(repaired_action.command or ""))
            if not repaired_normalized:
                continue
            for failed_action_id, failed in failed_commands.items():
                if repaired_normalized != failed["normalized_command"]:
                    continue
                errors.append(
                    {
                        "error": "execution_repair_repeats_diagnostic_failed_command",
                        "message": (
                            "Execution repair repeats an exact shell command that already failed. "
                            "The repair must change the "
                            "command syntax/options or use a different safe implementation."
                        ),
                        "action_id": repaired_action.action_id,
                        "failed_action_id": failed_action_id,
                        "command_hash": failed["command_hash"],
                        "stdout_error_classifier": failed["classifier"],
                    }
                )
        return errors

    @staticmethod
    def _record_shell_command(
        record: OperatorExecutionRecord,
        actions_by_id: dict[str, OperatorAction],
    ) -> str:
        metadata = dict(record.metadata or {})
        command = str(metadata.get("shell_command") or metadata.get("command") or "").strip()
        if command:
            return command
        action = actions_by_id.get(record.action_id)
        if action is not None and action.kind == "shell_command":
            return str(action.command or "").strip()
        return ""

    @staticmethod
    def _diagnostic_text_from_record(record: OperatorExecutionRecord) -> str:
        return _execution_learning_safe_text(
            record.error or record.stdout or record.stderr or record.output or ""
        )

    @staticmethod
    def _record_has_execution_learning_diagnostic(record: OperatorExecutionRecord) -> bool:
        """Return whether a failed shell record carries a concrete tool diagnostic."""

        if record.status != "error":
            return False
        if _StepRunnerRepairLearningMixin._record_has_repeat_blocking_shell_diagnostic(record):
            return True
        metadata = dict(record.metadata or {})
        if bool(metadata.get("stdout_diagnostic_error")):
            return True
        diagnostic_text = _execution_learning_safe_text(
            record.error or record.stderr or record.stdout or ""
        )
        if not diagnostic_text:
            return False
        command = str(metadata.get("shell_command") or metadata.get("command") or "")
        exit_code = record.exit_code if record.exit_code is not None else 1
        classification = classify_shell_stdout_deterministic(
            command=command,
            exit_code=exit_code,
            stdout=diagnostic_text,
            stderr="",
        )
        return (
            not classification.stdout_usable
            and classification.classifier_source == "deterministic"
            and classification.confidence >= 0.7
        )

    def _execution_learning_existing_digest_keys(self, digest_key: str) -> bool:
        """Return whether an active/proposed memory already carries this digest key."""

        if self.memory_store is None:
            return False
        for entry in self.memory_store.list_entries(status=None):
            if digest_key in {str(tag or "") for tag in entry.tags}:
                return True
        for proposal in self.memory_store.list_proposals(status=None):
            draft = proposal.draft if isinstance(proposal.draft, dict) else {}
            tags = {str(tag or "") for tag in list(draft.get("tags") or [])}
            if digest_key in tags:
                return True
        return False

    def _execution_learning_digest_draft(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        *,
        task_id: str,
        failed_commands: list[str],
        successful_command: str,
        diagnostic: str,
    ) -> dict[str, Any] | None:
        """Build one conservative memory proposal draft from a corrected command."""

        if not failed_commands or not successful_command:
            return None
        if _execution_learning_has_sensitive_text(
            user_request.raw_prompt,
            successful_command,
            " ".join(failed_commands),
            diagnostic,
        ):
            return None
        normalized_failed = [normalize_operator_command(command) for command in failed_commands]
        normalized_success = normalize_operator_command(successful_command)
        if not normalized_success or normalized_success in normalized_failed:
            return None

        task = next((item for item in plan.tasks if item.task_id == task_id), None)
        task_goal = str(task.goal if task is not None else user_request.raw_prompt or "").strip()
        semantic_verb = str(task.semantic_verb if task is not None else "execute").strip()
        object_type = str(task.object_type if task is not None else "").strip()
        tool_name = _shell_tool_name(successful_command)
        digest_payload = {
            "failed": normalized_failed,
            "success": normalized_success,
            "task_goal": task_goal,
            "tool": tool_name,
        }
        digest_hash = hashlib.sha256(
            _stable_json(digest_payload).encode("utf-8", errors="replace")
        ).hexdigest()
        digest_key = f"execdigest{digest_hash[:16]}"
        if self._execution_learning_existing_digest_keys(digest_key):
            return None

        summary_subject = tool_name or object_type or "shell command"
        summary = f"Correct {summary_subject} command after diagnostic failure"
        failed_preview = failed_commands[0]
        instruction = (
            f"For future similar requests"
            f"{f' about {task_goal}' if task_goal else ''}, prefer `{successful_command}`. "
            f"Do not use `{failed_preview}` when it produces the diagnostic: {diagnostic[:240]}"
        ).strip()
        tags = _memory_tag_tokens(
            " ".join(
                [
                    user_request.raw_prompt,
                    task_goal,
                    object_type,
                    semantic_verb,
                    tool_name,
                    successful_command,
                    " ".join(failed_commands),
                    "execution learning shell command",
                ]
            ),
            limit=18,
        )
        tags = [
            tag
            for tag in [digest_key, "executionlearning", "shell", *tags]
            if tag
        ]
        tags = list(dict.fromkeys(tags))
        model_name = self._active_memory_model_name(user_request)
        return {
            "instruction": instruction,
            "summary": summary[:180],
            "memory_kind": "task_memory",
            "scope": "global",
            "model_name": model_name,
            "model_family": normalize_model_family(model_name),
            "task_type": _memory_tag_tokens(object_type or task_goal or "operator", limit=1)[0]
            if _memory_tag_tokens(object_type or task_goal or "operator", limit=1)
            else "operator",
            "tool_type": tool_name,
            "intent_type": _memory_tag_tokens(semantic_verb or "execute", limit=1)[0]
            if _memory_tag_tokens(semantic_verb or "execute", limit=1)
            else "execute",
            "validator_error_type": "",
            "safe_examples": [successful_command],
            "blocked_examples": failed_commands,
            "tags": tags[:20],
            "provenance": "feedback",
            "request_id": user_request.request_id,
            "rationale": (
                "A shell command failed with diagnostic execution evidence and a later command in the "
                "same task completed successfully, so this proposed memory captures the "
                "corrected invocation pattern for review."
            ),
        }

    def _propose_execution_learning_digest(
        self,
        user_request: UserRequest,
        plan: OperatorPlan | None,
        records: list[OperatorExecutionRecord],
        status: str,
        observability: ObservabilityContext | None,
    ) -> list[dict[str, Any]]:
        """Create proposed task memory from failed-then-successful command evidence."""

        if (
            status != "success"
            or plan is None
            or self.memory_store is None
            or not bool(self.config.agent_memory_enabled)
        ):
            return []
        actions_by_id = {action.action_id: action for action in plan.actions}
        shell_records = [
            (index, record, self._record_shell_command(record, actions_by_id))
            for index, record in enumerate(records)
            if record.kind == "shell_command"
        ]
        proposed: list[dict[str, Any]] = []
        for success_index, success_record, success_command in shell_records:
            if success_record.status != "success" or not success_command:
                continue
            failed: list[tuple[int, OperatorExecutionRecord, str]] = []
            for fail_index, fail_record, fail_command in shell_records:
                if fail_index >= success_index:
                    break
                if not fail_command or not self._record_has_execution_learning_diagnostic(fail_record):
                    continue
                same_task = (
                    bool(success_record.task_id)
                    and bool(fail_record.task_id)
                    and success_record.task_id == fail_record.task_id
                )
                single_task = len({str(record.task_id or "") for _, record, _ in shell_records}) <= 1
                if same_task or single_task:
                    failed.append((fail_index, fail_record, fail_command))
            if not failed:
                continue
            failed_commands: list[str] = []
            seen_failed: set[str] = set()
            for _, _, command in failed:
                normalized = normalize_operator_command(command)
                if normalized in seen_failed:
                    continue
                seen_failed.add(normalized)
                failed_commands.append(command)
            diagnostic = self._diagnostic_text_from_record(failed[-1][1])
            draft = self._execution_learning_digest_draft(
                user_request,
                plan,
                task_id=success_record.task_id or failed[-1][1].task_id,
                failed_commands=failed_commands,
                successful_command=success_command,
                diagnostic=diagnostic,
            )
            if draft is None:
                continue
            proposal = self.memory_store.create_proposal(
                proposal_type="create",
                draft=draft,
                rationale=draft["rationale"],
                provenance="feedback",
                actor="runtime",
            )
            payload = {
                "proposal_id": proposal.proposal_id,
                "proposal_type": proposal.proposal_type,
                "summary": draft["summary"],
                "instruction": draft["instruction"],
                "tags": list(draft["tags"]),
                "safe_examples": list(draft["safe_examples"]),
                "blocked_examples": list(draft["blocked_examples"]),
                "task_id": success_record.task_id,
            }
            proposed.append(payload)
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_MEMORY_DIGEST_PROPOSED,
                title="Memory digest proposed",
                summary="The runtime proposed a memory digest from a failed-then-corrected command.",
                details=payload,
            )
        if proposed:
            metadata = _operator_planning_trace_metadata(user_request)
            if metadata is not None:
                existing = list(metadata.get("operator_memory_digest_proposals") or [])
                metadata["operator_memory_digest_proposals"] = [*existing, *proposed]
        return proposed

    def _attach_execution_learning_digest(
        self,
        user_request: UserRequest,
        result: OperatorPipelineResult,
        observability: ObservabilityContext | None,
    ) -> OperatorPipelineResult:
        proposals = self._propose_execution_learning_digest(
            user_request,
            result.plan,
            list(result.execution_records),
            result.status,
            observability,
        )
        if not proposals:
            return result
        metadata = dict(result.metadata)
        existing = list(metadata.get("operator_memory_digest_proposals") or [])
        metadata["operator_memory_digest_proposals"] = [*existing, *proposals]
        return result.model_copy(update={"metadata": metadata})

    @staticmethod
    def _clarification_authorizes_elevated_privilege(user_request: UserRequest) -> bool:
        """Return whether the user explicitly allowed an elevated retry."""

        if request_authorizes_sudo(user_request):
            return True
        raw = dict(user_request.session_context or {}).get("clarifications")
        if not isinstance(raw, list) or not raw:
            return False
        elevation_markers = (
            "sudo",
            "elevated",
            "privilege",
            "privileges",
            "permission",
            "permissions",
            "root",
            "administrator",
            "admin",
        )
        positive_markers = ("yes", "use", "retry", "allow", "with", "grant", "approve", "ok")
        negative_markers = ("no", "without", "cancel", "do not", "don't")
        for entry in raw[-5:]:
            if not isinstance(entry, dict):
                continue
            answer_text = " ".join(
                str(entry.get(key) or "")
                for key in ("answer", "selected_option_id")
            ).lower()
            full_text = " ".join(
                str(entry.get(key) or "")
                for key in ("question", "answer", "missing_information", "reason", "selected_option_id")
            ).lower()
            if any(marker in answer_text for marker in negative_markers):
                continue
            if "sudo" in answer_text:
                return True
            if (
                any(marker in full_text for marker in elevation_markers)
                and any(marker in answer_text for marker in positive_markers)
            ):
                return True
        return False

    @staticmethod
    def _shell_command_uses_sudo(command: str | None) -> bool:
        return shell_command_uses_sudo(command)

    @staticmethod
    def _record_looks_permission_failure(record: OperatorExecutionRecord) -> bool:
        if record.status != "error" and (record.exit_code is None or record.exit_code == 0):
            return False
        text = " ".join(
            str(value or "")
            for value in (record.stdout, record.stderr, record.error, record.output)
        ).lower()
        return any(
            fragment in text
            for fragment in (
                "permission denied",
                "operation not permitted",
                "requires root",
                "must be root",
                "are you root",
                "not permitted",
                "could not open lock file",
                "unable to lock",
                "access denied",
            )
        )

    def _execution_repair_ignored_clarification_errors(
        self,
        user_request: UserRequest,
        original_plan: OperatorPlan,
        repaired_plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
    ) -> list[dict[str, Any]]:
        """Reject execution repairs that ignore explicit clarification answers."""

        if not self._clarification_authorizes_elevated_privilege(user_request):
            return []
        failed_ids = {
            record.action_id
            for record in records
            if self._record_looks_permission_failure(record)
        }
        if not failed_ids:
            return []

        original_actions = {
            action.action_id: action
            for action in original_plan.actions
            if action.kind == "shell_command"
        }
        failed_commands = {
            action_id: normalize_operator_command(str(action.command or ""))
            for action_id in failed_ids
            if (action := original_actions.get(action_id)) is not None
        }
        if not failed_commands:
            return []

        repaired_shell_actions = [
            action for action in repaired_plan.actions if action.kind == "shell_command"
        ]
        if not any(self._shell_command_uses_sudo(action.command) for action in repaired_shell_actions):
            return [
                {
                    "error": "execution_repair_ignored_elevation_clarification",
                    "message": (
                        "The user authorized an elevated retry after a permission failure, "
                        "but the repaired shell plan does not use sudo."
                    ),
                    "failed_action_ids": sorted(failed_commands),
                }
            ]

        repaired_by_id = {action.action_id: action for action in repaired_shell_actions}
        errors: list[dict[str, Any]] = []
        for action_id, failed_command in failed_commands.items():
            repaired_same_id = repaired_by_id.get(action_id)
            if (
                repaired_same_id is not None
                and not self._shell_command_uses_sudo(repaired_same_id.command)
            ):
                errors.append(
                    {
                        "error": "execution_repair_ignored_elevation_clarification",
                        "message": (
                            "The repaired action kept the failed non-elevated command "
                            "after the user authorized sudo."
                        ),
                        "action_id": action_id,
                    }
                )
                continue
            for repaired_action in repaired_shell_actions:
                if normalize_operator_command(str(repaired_action.command or "")) != failed_command:
                    continue
                errors.append(
                    {
                        "error": "execution_repair_repeats_failed_command_after_clarification",
                        "message": (
                            "The repaired plan repeats a command that already failed for "
                            "permission reasons after the user authorized sudo."
                        ),
                        "action_id": repaired_action.action_id,
                        "failed_action_id": action_id,
                    }
                )
        return errors

    @staticmethod
    def _execution_repair_internal_python_clarification_error(
        request: OperatorClarificationRequest,
        records: list[OperatorExecutionRecord],
    ) -> dict[str, Any] | None:
        """Reject user questions that ask how to fix internal generated Python bugs."""

        failed_python = [
            record
            for record in records
            if record.status == "error" and record.kind in {"python_action", "python_transform"}
        ]
        if not failed_python:
            return None
        question_text = " ".join(
            [
                str(request.question or ""),
                str(request.reason or ""),
                str(request.missing_information or ""),
            ]
        )
        if not re.search(
            r"\b(parse|parser|parsing|regex|regular expression|format|conversion|"
            r"strategy|traceback|exception|valueerror|typeerror|nameerror|delimiter|unit)\b",
            question_text,
            re.IGNORECASE,
        ):
            return None
        failure_text = " ".join(
            str(value or "")
            for record in failed_python
            for value in (record.error, record.stderr, record.stdout)
        )
        if failure_text and not re.search(
            r"\b(traceback|exception|valueerror|typeerror|nameerror|parse|parser|"
            r"invalid|failed|regex|regular expression|format|conversion)\b",
            failure_text,
            re.IGNORECASE,
        ):
            return None
        return {
            "error": "execution_repair_internal_python_clarification_rejected",
            "message": (
                "Execution repair asked the user to choose a parser/code repair strategy "
                "for an internal generated Python failure. Repair must use the traceback, "
                "bound inputs, and prior code instead of pausing the user."
            ),
            "missing_information": request.missing_information,
            "question": request.question,
            "failed_action_ids": [record.action_id for record in failed_python],
        }



__all__ = ["_StepRunnerRepairLearningMixin"]
