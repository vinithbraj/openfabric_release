"""Operator step validation helpers for the operator step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class _StepRunnerStepValidationMixin:
    @staticmethod
    def _step_validation_artifact_candidates(*values: Any) -> list[str]:
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
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    candidates.append(candidate)
        return candidates[:20]

    def _operator_step_validation_contract_for_task(
        self,
        user_request: UserRequest,
        task: OperatorTask,
        *,
        index: int,
    ) -> OperatorStepValidationContract:
        original_request = str(user_request.raw_prompt or "")
        intent_block = dict(user_request.session_context or {}).get("operator_intent_block")
        if isinstance(intent_block, dict):
            global_constraints = intent_block.get("global_constraints")
            if isinstance(global_constraints, dict):
                streaming_original = str(
                    global_constraints.get("streaming_original_prompt") or ""
                ).strip()
                if streaming_original:
                    original_request = streaming_original
        current_task_text = " ".join(
            [
                task.goal,
                str(task.semantic_verb or ""),
                str(task.object_type or ""),
                str(task.reason or ""),
            ]
        )
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
        if str(task.semantic_verb or "").lower() in mutation_terms or any(
            re.search(rf"\b{re.escape(term)}\b", current_task_text, re.IGNORECASE)
            for term in mutation_terms
        ):
            requested_mutations.append(task.goal)
        return OperatorStepValidationContract(
            original_request=original_request,
            step_id=task.task_id,
            task_id=task.task_id,
            step_index=index,
            step_description=task.goal,
            semantic_verb=str(task.semantic_verb or ""),
            object_type=str(task.object_type or ""),
            exact_requested_outputs=[task.goal],
            requested_artifacts=self._step_validation_artifact_candidates(task.goal, task.reason),
            requested_values=[],
            requested_mutations=requested_mutations,
            success_conditions=[
                "The concrete execution evidence satisfies this task exactly.",
                "The final response cannot substitute for missing runtime evidence.",
            ],
            evidence_expectations=[
                "Use action outputs, exit codes, bound inputs, and artifact evidence.",
                "For file output tasks, require exact artifact path/name evidence.",
            ],
        )

    def _operator_step_validation_artifact_evidence(
        self,
        contract: OperatorStepValidationContract,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
    ) -> dict[str, Any]:
        workspace_root = Path(self.config.workspace_root or ".").expanduser()
        try:
            workspace_root = workspace_root.resolve()
        except OSError:
            workspace_root = Path(self.config.workspace_root or ".").absolute()
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
                if exists and resolved.is_file():
                    evidence["size_bytes"] = resolved.stat().st_size
                    if inside_workspace:
                        try:
                            preview = resolved.read_text(encoding="utf-8", errors="replace")[:2000]
                        except OSError as exc:
                            evidence["preview_error"] = str(exc)
                        else:
                            evidence["content_preview"] = _truncate(preview, 2000)
                    else:
                        evidence["content_preview_omitted"] = "outside_workspace"
            except OSError as exc:
                evidence["error"] = str(exc)
            artifacts.append(evidence)
        task_action_ids = {
            action.action_id for action in plan.actions if action.task_id == contract.task_id
        }
        record_by_action_id = {record.action_id: record for record in records}
        all_plan_action_ids = [action.action_id for action in plan.actions]
        current_task_action_ids = [
            action.action_id for action in plan.actions if action.task_id == contract.task_id
        ]

        def action_completion(action_ids: list[str]) -> dict[str, Any]:
            successful_action_ids = [
                action_id
                for action_id in action_ids
                if record_by_action_id.get(action_id) is not None
                and record_by_action_id[action_id].status == "success"
            ]
            failed_action_ids = [
                action_id
                for action_id in action_ids
                if record_by_action_id.get(action_id) is not None
                and record_by_action_id[action_id].status != "success"
            ]
            missing_action_ids = [
                action_id for action_id in action_ids if action_id not in record_by_action_id
            ]
            return {
                "expected_action_ids": list(action_ids),
                "successful_action_ids": successful_action_ids,
                "failed_action_ids": failed_action_ids,
                "missing_action_ids": missing_action_ids,
                "all_expected_actions_completed_successfully": (
                    bool(action_ids) and not missing_action_ids and not failed_action_ids
                ),
            }

        return {
            "workspace_root": str(workspace_root),
            "requested_artifacts": artifacts,
            "current_task_action_completion": action_completion(current_task_action_ids),
            "plan_action_completion": action_completion(all_plan_action_ids),
            "execution_record_facts": [
                {
                    "action_id": record.action_id,
                    "task_id": record.task_id,
                    "status": record.status,
                    "exit_code": record.exit_code,
                    "bound_inputs_preview": dict(record.metadata or {}).get("bound_inputs_preview"),
                    "shell_input_env_names": dict(record.metadata or {}).get("shell_input_env_names"),
                    "cwd": dict(record.metadata or {}).get("cwd"),
                }
                for record in records
                if not task_action_ids or record.action_id in task_action_ids or record.task_id == contract.task_id
            ],
        }

    @staticmethod
    def _step_validation_missing_action_evidence_false_positive(
        review: OperatorStepValidationReview,
        contract: OperatorStepValidationContract,
        artifact_evidence: dict[str, Any],
    ) -> bool:
        """Return true when the judge asks for action evidence already present."""

        if review.decision != "continue_with_more_evidence" or review.contract_violations:
            return False
        if contract.requested_artifacts:
            return False
        completion = dict(artifact_evidence.get("plan_action_completion") or {})
        if not completion.get("all_expected_actions_completed_successfully"):
            return False
        text = " ".join(
            str(value or "")
            for value in [
                review.reason,
                review.repair_guidance,
                *list(review.missing_evidence or []),
            ]
        ).lower()
        if not text:
            return False
        mentions_missing = any(
            phrase in text
            for phrase in (
                "missing",
                "not confirmed",
                "not present",
                "not shown",
                "lack",
                "lacks",
                "need more evidence",
                "requires more evidence",
            )
        )
        mentions_action_records = any(
            phrase in text
            for phrase in (
                "action",
                "execution record",
                "execution records",
                "record",
                "stdout",
                "stderr",
                "exit code",
                "exit_code",
            )
        )
        return mentions_missing and mentions_action_records

    def _validate_operator_plan_steps(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        observability: ObservabilityContext | None,
    ) -> OperatorPipelineResult | None:
        if not bool(getattr(self.config, "llm_operator_step_validation_enabled", False)):
            return None
        validations: list[dict[str, Any]] = []
        trace_metadata = _operator_planning_trace_metadata(user_request)
        streaming_step_scope = isinstance(
            dict(user_request.session_context or {}).get("operator_streaming_current_task"),
            dict,
        )
        for index, task in enumerate(plan.tasks):
            contract = self._operator_step_validation_contract_for_task(
                user_request,
                task,
                index=index,
            )
            task_action_ids = {
                action.action_id for action in plan.actions if action.task_id == task.task_id
            }
            task_records = (
                [
                    record
                    for record in records
                    if (
                        not task_action_ids
                        or record.action_id in task_action_ids
                        or record.task_id == task.task_id
                    )
                ]
                if streaming_step_scope
                else list(records)
            )
            evidence = self._operator_step_validation_artifact_evidence(contract, plan, task_records)
            if observability is not None:
                self._emit(
                    observability,
                    level="info",
                    event_type=OPERATOR_STEP_VALIDATION_PROPOSED,
                    title="Step validation started",
                    summary="The LLM is checking whether an operator task satisfied its exact contract.",
                    details={
                        "task_id": contract.task_id,
                        "step_index": contract.step_index,
                        "requested_artifacts": list(contract.requested_artifacts),
                    },
                )
            try:
                prompt = build_operator_step_validation_prompt(
                    user_request,
                    plan,
                    task_records,
                    contract,
                    evidence,
                )
                review = structured_call(self.llm_client, prompt, OperatorStepValidationReview)
            except Exception as exc:
                payload = {
                    "contract": contract.model_dump(mode="json"),
                    "artifact_evidence": evidence,
                    "decision": "block",
                    "error": str(exc),
                }
                validations.append(payload)
                if trace_metadata is not None:
                    trace_metadata["operator_step_validations"] = list(validations)
                    trace_metadata["operator_step_validation_checked"] = True
                if observability is not None:
                    self._emit(
                        observability,
                        level="error",
                        event_type=OPERATOR_STEP_VALIDATION_REJECTED,
                        title="Step validation unavailable",
                        summary="The typed LLM step validation failed, so completion is blocked.",
                        details=payload,
                    )
                markdown = (
                    "## Step Validation Blocked\n\n"
                    "I could not validate that each step satisfied the exact request, "
                    "so I stopped before claiming completion."
                )
                feedback = {
                    "phase": "step_validation",
                    "decision": "block",
                    "repair_guidance": "Step validation did not produce a valid typed response.",
                    "reason": str(exc),
                    "contract": contract.model_dump(mode="json"),
                    "artifact_evidence": evidence,
                }
                return OperatorPipelineResult(
                    status="error",
                    final_response=markdown,
                    plan=plan,
                    execution_records=records,
                    display_document=self._display_document(
                        user_request=user_request,
                        status="error",
                        markdown=markdown,
                        plan=plan,
                        records=records,
                    ),
                    metadata={
                        "operator_step_validation_checked": True,
                        "operator_step_validation_decision": "block",
                        "operator_step_validation_feedback": feedback,
                        "operator_step_validations": validations,
                    },
                )
            review_payload = review.model_dump(mode="json")
            payload = {
                "contract": contract.model_dump(mode="json"),
                "artifact_evidence": evidence,
                "review": review_payload,
                "decision": review.decision,
            }
            validations.append(payload)
            if trace_metadata is not None:
                trace_metadata["operator_step_validations"] = list(validations)
                trace_metadata["operator_step_validation_checked"] = True
            if review.decision == "accept" and review.satisfied:
                if observability is not None:
                    self._emit(
                        observability,
                        level="info",
                        event_type=OPERATOR_STEP_VALIDATION_ACCEPTED,
                        title="Step validation accepted",
                        summary="The LLM accepted that this operator task satisfied its exact contract.",
                        details={
                            "task_id": contract.task_id,
                            "confidence": review.confidence,
                            "reason": review.reason,
                        },
                    )
                continue
            if self._step_validation_missing_action_evidence_false_positive(
                review,
                contract,
                evidence,
            ):
                payload["decision"] = "accept"
                payload["deterministic_override"] = "all_planned_actions_completed_successfully"
                validations[-1] = payload
                if trace_metadata is not None:
                    trace_metadata["operator_step_validations"] = list(validations)
                    trace_metadata["operator_step_validation_checked"] = True
                if observability is not None:
                    self._emit(
                        observability,
                        level="info",
                        event_type=OPERATOR_STEP_VALIDATION_ACCEPTED,
                        title="Step validation accepted from action records",
                        summary=(
                            "The LLM requested more action evidence, but every planned action "
                            "had a successful execution record."
                        ),
                        details={
                            "task_id": contract.task_id,
                            "review_decision": review.decision,
                            "action_completion": evidence.get("plan_action_completion"),
                        },
                    )
                continue
            if observability is not None:
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_STEP_VALIDATION_REJECTED,
                    title="Step validation rejected",
                    summary="The LLM did not accept that this operator task satisfied its exact contract.",
                    details=payload,
                )
            guidance = review.repair_guidance or review.reason or "The step evidence did not satisfy the contract."
            feedback = {
                "phase": "step_validation",
                "decision": review.decision,
                "repair_guidance": guidance,
                "reason": review.reason,
                "missing_evidence": list(review.missing_evidence),
                "contract_violations": list(review.contract_violations),
                "contract": contract.model_dump(mode="json"),
                "artifact_evidence": evidence,
            }
            markdown = "\n\n".join(
                [
                    "## Step Validation Blocked",
                    f"Decision: `{review.decision}`",
                    _truncate(guidance, 2000),
                ]
            ).strip()
            return OperatorPipelineResult(
                status="error",
                final_response=markdown,
                plan=plan,
                execution_records=records,
                display_document=self._display_document(
                    user_request=user_request,
                    status="error",
                    markdown=markdown,
                    plan=plan,
                    records=records,
                ),
                metadata={
                    "operator_step_validation_checked": True,
                    "operator_step_validation_decision": review.decision,
                    "operator_step_validation_feedback": feedback,
                    "operator_step_validations": validations,
                },
            )
        if trace_metadata is not None:
            trace_metadata["operator_step_validation_checked"] = True
            trace_metadata["operator_step_validations"] = list(validations)
        return None



__all__ = ["_StepRunnerStepValidationMixin"]
