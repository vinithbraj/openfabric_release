"""Finalization, safety, and failure rendering helpers."""

from __future__ import annotations

from agent_runtime.core.user_errors import user_error_detail, user_error_message

from .common import *
from .formatting import *


class _FinalizationMixin:
    """Finalization, safety, and failure rendering helpers."""

    def _mark_dag_execution_ready(self, dag: ActionDAG, safety_decision) -> ActionDAG:
        """Return a trusted DAG annotated for execution."""

        trusted_dag = safety_decision.sanitized_dag or dag
        prepared = trusted_dag.model_copy(
            update={
                "execution_ready": bool(safety_decision.allowed),
                "safety_decision": safety_decision.model_dump(mode="json"),
            }
        )
        final_hash = hash_action_dag(prepared)
        return prepared.model_copy(update={"final_dag_hash": final_hash})

    def _record_dag_review(
        self,
        trace: PlanningTrace,
        request_id: str,
        review,
        dag: ActionDAG,
        llm_client=None,
    ) -> None:
        """Append one DAG review entry to the planning trace."""

        model_name, temperature = llm_client_metadata(llm_client or self.llm_client)
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="dag_review",
                request_id=request_id,
                model_name=model_name,
                llm_temperature=temperature,
                prompt_template_id="dag_review",
                raw_llm_response=review.model_dump(mode="json"),
                parsed_proposal=review.model_dump(mode="json"),
                selected_candidate={
                    "dag_id": dag.dag_id,
                    "node_count": len(dag.nodes),
                },
                rejection_reasons=(
                    ["recommended_repair_advisory_only"] if review.recommended_repair else []
                ),
            ),
        )

    def _record_dag_review_skipped(
        self,
        trace: PlanningTrace,
        request_id: str,
        dag: ActionDAG,
        reason: str,
    ) -> None:
        """Append one deterministic skip record for advisory DAG review."""

        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="dag_review",
                request_id=request_id,
                prompt_template_id="dag_review",
                raw_llm_response=None,
                parsed_proposal=None,
                selected_candidate={
                    "dag_id": dag.dag_id,
                    "node_count": len(dag.nodes),
                    "skipped": True,
                    "reason": reason,
                },
                deterministic_normalizations=[f"skipped_advisory_review:{reason}"],
            ),
        )

    def _record_safety(self, trace: PlanningTrace, request_id: str, safety_decision, dag_hash: str | None) -> None:
        """Append deterministic safety information to the planning trace."""

        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="safety_evaluation",
                request_id=request_id,
                prompt_template_id="safety_evaluation",
                raw_llm_response=None,
                parsed_proposal=None,
                safety_decision=safety_decision.model_dump(mode="json"),
                final_dag_hash=dag_hash,
            ),
        )

    def _finalize_profile(
        self,
        user_request: UserRequest,
        *,
        final_status: str,
        failed_stage: str | None = None,
    ) -> dict[str, Any] | None:
        """Store and emit the request profile summary when profiling is active."""

        profiler = user_request.safety_context.get("runtime_profiler")
        if not isinstance(profiler, RuntimeProfiler):
            return None
        profiler.close_open_stages(status="aborted" if failed_stage else "completed")
        summary = profiler.summary()
        summary["final_status"] = final_status
        if failed_stage:
            summary["failed_stage"] = failed_stage
        try:
            context_window_tokens = int(user_request.session_context.get("llm_context_window_tokens") or 0)
        except (TypeError, ValueError):
            context_window_tokens = 0
        if context_window_tokens > 0:
            llm_calls = list(summary.get("llm_calls") or [])
            largest_context_call = None
            context_used_tokens = 0
            for call in llm_calls:
                if not isinstance(call, dict):
                    continue
                used = int(
                    call.get("total_tokens_estimate")
                    or call.get("input_tokens_estimate")
                    or 0
                )
                if used > context_used_tokens:
                    context_used_tokens = used
                    largest_context_call = call
            remaining_tokens = max(0, context_window_tokens - context_used_tokens)
            summary["llm_context_usage"] = {
                "context_window_tokens": context_window_tokens,
                "used_tokens_estimate": context_used_tokens,
                "remaining_tokens_estimate": remaining_tokens,
                "used_percent": round((context_used_tokens / context_window_tokens) * 100.0, 1),
                "remaining_percent": round((remaining_tokens / context_window_tokens) * 100.0, 1),
                "largest_call": largest_context_call,
            }
        self.last_profile_summary = summary
        trace = user_request.safety_context.get("planning_trace")
        if isinstance(trace, PlanningTrace):
            trace.metadata["runtime_profile"] = summary
        observability = self._observability(user_request)
        if observability is not None:
            visualization_profile = {
                "final_status": final_status,
                "failed_stage": failed_stage,
                "total_duration_ms": summary.get("total_duration_ms"),
                "stage_timings": summary.get("stage_timings"),
                "stage_totals": summary.get("stage_totals"),
                "longest_stage": summary.get("longest_stage"),
                "llm_call_count": summary.get("llm_call_count"),
                "llm_retry_count": summary.get("llm_retry_count"),
                "llm_retry_like_count": summary.get("llm_retry_like_count"),
                "llm_retry_groups": summary.get("llm_retry_groups"),
                "llm_calls_by_stage": summary.get("llm_calls_by_stage"),
                "longest_llm_call": summary.get("longest_llm_call"),
                "llm_latency_distribution": summary.get("llm_latency_distribution"),
                "stage_latency_distribution": summary.get("stage_latency_distribution"),
                "llm_context_usage": summary.get("llm_context_usage"),
            }
            observability.info(
                STAGE_COMPLETED,
                "profiling.completed",
                "Runtime profile completed",
                "The runtime collected per-stage timings and LLM usage for this request.",
                details={
                    "final_status": final_status,
                    "failed_stage": failed_stage,
                    "total_duration_ms": summary.get("total_duration_ms"),
                    "llm_call_count": summary.get("llm_call_count"),
                    "llm_retry_like_count": summary.get("llm_retry_like_count"),
                    "longest_stage": summary.get("longest_stage"),
                    "longest_llm_call": summary.get("longest_llm_call"),
                    "llm_context_usage": summary.get("llm_context_usage"),
                    "runtime_profile": visualization_profile,
                },
                debug_only=True,
            )
        return summary

    def _should_skip_dag_review(self, dag: ActionDAG) -> tuple[bool, str]:
        """Return whether advisory DAG review can be skipped for trivial read-only DAGs."""

        if len(dag.nodes) != 1:
            return False, "DAG has more than one node."
        if dag.edges:
            return False, "DAG has dependency edges."
        node = dag.nodes[0]
        labels = {label.strip().lower() for label in node.safety_labels}
        if "requires-confirmation" in labels or "unresolved" in labels:
            return False, "DAG node requires confirmation or is unresolved."
        try:
            manifest = self.registry.get(node.capability_id).manifest
        except Exception:
            return False, "Capability manifest was unavailable."
        if not bool(manifest.read_only):
            return False, "Capability is not read-only."
        return True, "Single-node read-only DAG has no dependencies to review."

    def _render_capability_gaps(self, gaps) -> str:
        """Render one or more capability-gap descriptions safely for the user."""

        if not gaps:
            return "I understood the request, but this runtime does not currently have a compatible capability for it."
        if len(gaps) == 1:
            return gaps[0].user_facing_message
        lines = ["Some parts of that request are not currently supported by this runtime:"]
        lines.extend(f"- {gap.user_facing_message}" for gap in gaps)
        return "\n".join(lines)

    def _safe_failure(self, user_request: UserRequest, stage: str, message: str) -> str:
        """Log and return a user-safe failure message."""

        detail = user_error_detail(
            message,
            stage=stage,
            category="runtime_error",
            context=user_request.session_context,
            request_id=user_request.request_id,
        )
        safe_message = user_error_message(detail, message)
        observability = self._observability(user_request)
        if observability is not None:
            observability.stage_failed(
                stage,
                str(detail.get("title") or "Request failed"),
                str(detail.get("likely_cause") or message),
                details={"request_id": user_request.request_id, "error_detail": detail},
            )
            observability.stage_completed(
                STAGE_COMPLETED,
                "Request completed with error",
                str(detail.get("fix_hint") or "The request ended with a user-safe runtime error."),
                details={"final_status": "error", "failed_stage": stage, "error_detail": detail},
            )
        log_event(
            self.logger,
            "agent_runtime.failure",
            request_id=user_request.request_id,
            stage=stage,
            message=message,
        )
        self._record_last_failure(
            request_id=user_request.request_id,
            prompt=user_request.raw_prompt,
            category="runtime_error",
            stage=stage,
            reason=message,
            metadata={"error_detail": detail},
        )
        trace = user_request.safety_context.get("planning_trace")
        if isinstance(trace, PlanningTrace) and safe_message not in trace.user_facing_errors:
            trace.user_facing_errors.append(safe_message)
        self._finalize_profile(user_request, final_status="error", failed_stage=stage)
        return safe_message

    @staticmethod
    def _final_request_status(result_bundle: ResultBundle, capability_gap_count: int) -> str:
        """Return the user-facing final request status.

        The execution bundle can still be a technical success when only a supported
        subset of the user's request ran. In that case we surface the overall
        request as partial without forcing the output pipeline into the partial
        fallback renderer.
        """

        if bool(result_bundle.metadata.get("confirmation_required", False)):
            return "confirmation_required"
        if result_bundle.status == "confirmation_required":
            return "confirmation_required"
        if result_bundle.status == "error":
            return "error"
        if result_bundle.status == "partial":
            return "partial"
        if capability_gap_count > 0:
            return "partial"
        return "success"
