"""Execution formatting and continuation payload helpers for the operator step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class _StepRunnerFormattingMixin:
    def _formatter_sources(
        self,
        records: list[OperatorExecutionRecord],
        plan: OperatorPlan | None,
    ) -> list[FormatterSource]:
        """Return completed operator records in the shared final-formatter shape."""

        action_labels = self._operator_action_labels(plan)
        actions_by_id = {
            action.action_id: action
            for action in list(plan.actions if plan is not None else [])
        }
        sources: list[FormatterSource] = []
        for record in records:
            action = actions_by_id.get(record.action_id)
            sources.append(
                FormatterSource(
                    source_id=record.action_id,
                    label=action_labels.get(record.action_id, record.action_id),
                    kind=record.kind,
                    status=record.status,
                    command=str(action.command or "") if action is not None else "",
                    declared_output_shape=(
                        str(action.declared_output_shape)
                        if action is not None
                        else str(record.metadata.get("declared_output_shape") or "text")
                    ),
                    stdout=record.stdout,
                    stderr=record.stderr,
                    exit_code=record.exit_code,
                    output=record.output,
                )
            )
        return sources

    def _formatted_execution_markdown(
        self,
        user_request: UserRequest,
        records: list[OperatorExecutionRecord],
        plan: OperatorPlan | None,
        observability: ObservabilityContext | None,
        obligation_set: AnswerObligationSet | None = None,
    ) -> FormatterResult | None:
        """Return an LLM-authored final formatter result when available."""

        if not self._operator_profile_policy().run_final_formatter:
            return None
        formatter_result = run_llm_final_formatter(
            user_request=user_request,
            sources=self._formatter_sources(records, plan),
            llm_client=self.llm_client,
            validator=self.validator,
            python_executor=self.python_executor,
            observability=observability,
            stage=OPERATOR_STAGE,
            include_source_previews=self._operator_answer_judge_enabled(),
            answer_judge_enabled=self._operator_answer_judge_enabled(),
            cardinality_judge_enabled=self._operator_cardinality_judge_enabled(),
            source_preview_chars=self.config.llm_operator_formatter_source_preview_chars,
            max_repair_attempts=self._max_answer_judge_repair_attempts(),
            obligation_set=obligation_set,
            coverage_review_callback=(
                (
                    lambda content: self.reliability.review_answer_coverage(
                        request_id=user_request.request_id,
                        llm_client=self.llm_client,
                        user_prompt=user_request.raw_prompt,
                        final_response=content,
                        obligation_set=obligation_set,
                        plan=plan,
                        records=records,
                        source_preview_chars=self.config.llm_operator_formatter_source_preview_chars,
                    )
                )
                if obligation_set is not None and obligation_set.audit_status == "complete"
                else None
            ),
            coverage_retry_callback=(
                (
                    lambda review, attempt: self.reliability.record_formatter_retry(
                        request_id=user_request.request_id,
                        review=review,
                        attempt=attempt,
                    )
                )
                if obligation_set is not None and obligation_set.audit_status == "complete"
                else None
            ),
        )
        return formatter_result

    def _final_response_mode(self) -> str:
        mode = str(getattr(self.config, "llm_operator_final_response_mode", "detailed") or "").strip().lower()
        return mode if mode in {"detailed", "simple"} else "detailed"

    @staticmethod
    def _simple_execution_markdown() -> str:
        return "Task completed. Detailed output is available in the terminal or command output capsule."

    def _compact_execution_markdown(self, records: list[OperatorExecutionRecord]) -> str:
        report_markdown = report_markdown_from_records(records)
        if report_markdown:
            return report_markdown
        return self._simple_execution_markdown()

    @staticmethod
    def _single_report_execution_markdown(records: list[OperatorExecutionRecord]) -> str:
        report_markdown = report_markdown_from_records(records)
        if not report_markdown:
            return "Report completed, but no output was captured."
        return report_markdown

    @staticmethod
    def _report_validation_blocked_markdown(output_errors: list[dict[str, Any]]) -> str:
        return "\n\n".join(
            [
                "## Report Validation Blocked",
                "The operator action completed, but the report output did not satisfy the requested report contract.",
                _stable_json(output_errors),
            ]
        )

    @staticmethod
    def _single_report_execution_repair_feedback(
        records: list[OperatorExecutionRecord],
        repair_feedback: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        failed = [record for record in records if record.status == "error"]
        return [
            {
                "error": "execution_shape_single_report_runtime_failure",
                "message": (
                    "The report action failed at runtime. Regenerate a scoped replacement "
                    "action from the original user request and "
                    "the concrete runtime diagnostics."
                ),
                "failed_records": [
                    {
                        "action_id": record.action_id,
                        "task_id": record.task_id,
                        "kind": record.kind,
                        "exit_code": record.exit_code,
                        "stderr": _truncate(str(record.stderr or ""), 2000),
                        "stdout": _truncate(str(record.stdout or ""), 1000),
                        "output": _truncate(_stable_json(record.output), 1000)
                        if record.output is not None
                        else "",
                        "error": _truncate(str(record.error or ""), 2000),
                    }
                    for record in failed
                ],
                "prior_repair_feedback": list(repair_feedback[-2:]),
                "repair_hint": (
                    "Do not repeat a source query that produced no data or a traceback. "
                    "Use fresh read-only discovery inside the one replacement action, validate "
                    "required fields, and print the final report."
                ),
            }
        ]

    def _execution_markdown(
        self,
        records: list[OperatorExecutionRecord],
        plan: OperatorPlan | None = None,
        *,
        display_label: str = "Conversational",
    ) -> str:
        failed = [record for record in records if record.status == "error"]
        action_labels = self._operator_action_labels(plan)
        lines = [
            "## Results",
            "",
            "Execution completed." if not failed else "Execution completed with one or more errors.",
        ]
        for record in records:
            label = action_labels.get(record.action_id, record.action_id)
            lines.extend(["", f"### {label}", "", f"- Status: `{record.status}`"])
            if record.exit_code is not None:
                lines.append(f"- Exit code: `{record.exit_code}`")
            if record.stdout and record.kind != "shell_command":
                lines.extend(["", "Stdout:", "", f"```text\n{record.stdout}\n```"])
            if record.stderr and record.kind != "shell_command":
                lines.extend(["", "Stderr:", "", f"```text\n{record.stderr}\n```"])
            if record.output is not None:
                lines.extend(["", "Output:", "", f"```json\n{_stable_json(record.output)}\n```"])
            if record.error:
                lines.extend(["", f"Error: `{record.error}`"])
        return "\n".join(lines)

    def _partial_results_confirmation_markdown(
        self,
        plan: OperatorPlan,
        record: OperatorExecutionRecord,
    ) -> str:
        """Return markdown asking whether downstream work may consume partial stdout."""

        action_labels = self._operator_action_labels(plan)
        label = action_labels.get(record.action_id, record.action_id)
        stderr_preview = _truncate(record.stderr.strip(), 1200) if record.stderr else ""
        stdout_preview = _truncate(record.stdout.strip(), 1200) if record.stdout else ""
        lines = [
            "## Partial Results Available",
            "",
            "A command produced usable stdout, but it exited with a non-zero status. "
            "This commonly happens when a filesystem search finds some files but hits denied folders.",
            "",
            f"### {label}",
            "",
            f"- Exit code: `{record.exit_code}`",
            "- Downstream actions are paused so they do not silently consume incomplete data.",
        ]
        if stderr_preview:
            lines.extend(["", "Stderr preview:", "", f"```text\n{stderr_preview}\n```"])
        if stdout_preview:
            lines.extend(["", "Stdout preview:", "", f"```text\n{stdout_preview}\n```"])
        lines.extend(
            [
                "",
                "Approve to continue with the partial stdout. Deny to stop here.",
            ]
        )
        return "\n".join(lines)

    def _partial_results_confirmation_result(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        record: OperatorExecutionRecord,
        observability: ObservabilityContext | None,
    ) -> OperatorPipelineResult:
        """Pause execution when useful partial stdout needs user consent."""

        action_labels = self._operator_action_labels(plan)
        label = action_labels.get(record.action_id, record.action_id)
        action = {
            "capability_id": "operator.partial_results",
            "operation_id": "continue_with_partial_stdout",
            "details": label,
            "risk": "medium",
            "reason": (
                "The command produced partial stdout but exited non-zero. "
                "Approve only if using incomplete results is acceptable."
            ),
            "action_id": record.action_id,
            "exit_code": record.exit_code,
            "stderr_preview": _truncate(record.stderr, 500),
            "stdout_preview": _truncate(record.stdout, 500),
        }
        markdown = self._partial_results_confirmation_markdown(plan, record)
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_APPROVAL_REQUIRED,
            title="Partial results require confirmation",
            summary="A command produced usable stdout but exited non-zero.",
            details={"confirmation_actions": [action], "record": record.model_dump(mode="json")},
        )
        return OperatorPipelineResult(
            status="confirmation_required",
            final_response=markdown,
            plan=plan,
            execution_records=records,
            confirmation_required=True,
            confirmation_actions=[action],
            display_document=self._display_document(
                user_request=user_request,
                status="confirmation_required",
                markdown=markdown,
                plan=plan,
                records=records,
            ),
            metadata={
                "partial_results_pending": True,
                "partial_action_id": record.action_id,
                "partial_exit_code": record.exit_code,
            },
        )

    def _completion_continuation_confirmation_result(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        records: list[OperatorExecutionRecord],
        review: OperatorCompletionReview,
        attempt_count: int,
        observability: ObservabilityContext | None,
    ) -> OperatorPipelineResult:
        """Pause before executing LLM-proposed actions needed to finish the goal."""

        result = self.require_confirmation(user_request, plan, observability, records=records)
        metadata = dict(result.metadata)
        metadata.update(
            {
                "completion_review_pending": True,
                "completion_review_reason": review.reason,
                "completion_review_issues": list(review.issues),
                "completion_review_attempt_count": attempt_count,
                "seed_record_count": len(records),
            }
        )
        return result.model_copy(
            update={
                "execution_records": list(records),
                "metadata": metadata,
            }
        )



__all__ = ["_StepRunnerFormattingMixin"]
