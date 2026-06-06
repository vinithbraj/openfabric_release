"""Install reliability profile and report helpers."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.route_groups.shared_imports import *


def install_reliability_helpers(ctx: Any) -> None:
    """Install reliability profile and report helpers."""


    app = ctx.app
    settings = ctx.settings
    agent_runtime = ctx.agent_runtime
    trace_store = ctx.trace_store
    prompt_fetcher = ctx.prompt_fetcher
    prompt_template_store = ctx.prompt_template_store
    state_store = ctx.state_store
    conversation_store = ctx.conversation_store
    terminal_store = ctx.terminal_store
    gateway_store = ctx.gateway_store
    task_store = ctx.task_store
    monitor_store = ctx.monitor_store
    event_store = ctx.event_store
    command_allowlist_store = ctx.command_allowlist_store
    ui_settings_store = ctx.ui_settings_store
    learning_ledger_store = ctx.learning_ledger_store
    reliability_store = ctx.reliability_store
    cancel_events = ctx.cancel_events
    cancel_events_lock = ctx.cancel_events_lock
    task_queue_lock = ctx.task_queue_lock
    active_non_task_requests_lock = ctx.active_non_task_requests_lock
    monitored_task_requests = ctx.monitored_task_requests
    active_non_task_request_ids = ctx.active_non_task_request_ids
    llm_endpoint_defaults = ctx.llm_endpoint_defaults
    runtime_controls = ctx.runtime_controls
    runtime_controls_lock = ctx.runtime_controls_lock
    pending_event_macro_contexts = ctx.pending_event_macro_contexts
    pending_event_macro_contexts_lock = ctx.pending_event_macro_contexts_lock

    default_gateway_node = getattr(ctx, 'default_gateway_node', None)
    monitor_manager = getattr(ctx, 'monitor_manager', None)
    _submit_agent_request = getattr(ctx, '_submit_agent_request', None)
    _normalize_prompt_history_items = getattr(ctx, '_normalize_prompt_history_items', None)
    _prompt_history_items = getattr(ctx, '_prompt_history_items', None)
    _record_prompt_history_item = getattr(ctx, '_record_prompt_history_item', None)
    _event_macro_context_from = getattr(ctx, '_event_macro_context_from', None)
    _apply_event_extraction_hint_context = getattr(ctx, '_apply_event_extraction_hint_context', None)
    _strip_event_macro_context = getattr(ctx, '_strip_event_macro_context', None)
    _event_context_for_api = getattr(ctx, '_event_context_for_api', None)
    _stash_event_macro_context_for_draft = getattr(ctx, '_stash_event_macro_context_for_draft', None)
    _resolve_pending_event_macro_context = getattr(ctx, '_resolve_pending_event_macro_context', None)
    _prompt_has_sanitized_typein_marker = getattr(ctx, '_prompt_has_sanitized_typein_marker', None)
    _prepare_event_prompt_and_context = getattr(ctx, '_prepare_event_prompt_and_context', None)
    _event_record_api_payload = getattr(ctx, '_event_record_api_payload', None)
    _event_draft_response_api_payload = getattr(ctx, '_event_draft_response_api_payload', None)
    _event_create_payload_from_draft = getattr(ctx, '_event_create_payload_from_draft', None)
    _event_schedule_phrase = getattr(ctx, '_event_schedule_phrase', None)
    _saved_events_message = getattr(ctx, '_saved_events_message', None)
    _event_needs_detail_message = getattr(ctx, '_event_needs_detail_message', None)
    _event_has_typein_macro = getattr(ctx, '_event_has_typein_macro', None)
    _confirmation_actions_require_terminal = getattr(ctx, '_confirmation_actions_require_terminal', None)
    _attach_background_terminal = getattr(ctx, '_attach_background_terminal', None)
    _attach_background_terminal_for_typein = getattr(ctx, '_attach_background_terminal_for_typein', None)
    _background_terminal_context_hint = getattr(ctx, '_background_terminal_context_hint', None)
    _command_allowlist_context = getattr(ctx, '_command_allowlist_context', None)
    _apply_command_allowlist_context = getattr(ctx, '_apply_command_allowlist_context', None)
    _runtime_controls_snapshot = getattr(ctx, '_runtime_controls_snapshot', None)
    _public_runtime_controls_snapshot = getattr(ctx, '_public_runtime_controls_snapshot', None)
    _apply_backend_runtime_snapshot = getattr(ctx, '_apply_backend_runtime_snapshot', None)
    _apply_runtime_control_values = getattr(ctx, '_apply_runtime_control_values', None)
    _events_enabled = getattr(ctx, '_events_enabled', None)
    _apply_runtime_repair_attempt_context = getattr(ctx, '_apply_runtime_repair_attempt_context', None)
    _apply_sql_agent_route_context = getattr(ctx, '_apply_sql_agent_route_context', None)
    _reliability_run_payload = getattr(ctx, '_reliability_run_payload', None)
    _reliability_trace_summary = getattr(ctx, '_reliability_trace_summary', None)
    _reliability_markdown_report = getattr(ctx, '_reliability_markdown_report', None)
    _event_run_status_from_trace = getattr(ctx, '_event_run_status_from_trace', None)
    _event_context = getattr(ctx, '_event_context', None)
    _finalize_event_run = getattr(ctx, '_finalize_event_run', None)
    _append_scheduled_event_completed_trace_event = getattr(ctx, '_append_scheduled_event_completed_trace_event', None)
    _ensure_scheduled_event_trace_enriched = getattr(ctx, '_ensure_scheduled_event_trace_enriched', None)
    _trace_parent_request_id = getattr(ctx, '_trace_parent_request_id', None)
    _relink_handled_event_run = getattr(ctx, '_relink_handled_event_run', None)
    _reconcile_event_run = getattr(ctx, '_reconcile_event_run', None)
    _reconcile_event_runs = getattr(ctx, '_reconcile_event_runs', None)
    _reconcile_all_event_runs = getattr(ctx, '_reconcile_all_event_runs', None)
    _event_run_api_payload = getattr(ctx, '_event_run_api_payload', None)
    _notification_api_payload = getattr(ctx, '_notification_api_payload', None)
    _execution_error_code = getattr(ctx, '_execution_error_code', None)
    _execution_error_message = getattr(ctx, '_execution_error_message', None)
    _execution_error_detail_from_source = getattr(ctx, '_execution_error_detail_from_source', None)
    _trace_execution_error_detail = getattr(ctx, '_trace_execution_error_detail', None)
    _execution_error_preview = getattr(ctx, '_execution_error_preview', None)
    _notification_level_with_execution_error = getattr(ctx, '_notification_level_with_execution_error', None)
    _event_notification_level = getattr(ctx, '_event_notification_level', None)
    _plural_count = getattr(ctx, '_plural_count', None)
    _trace_auto_approval_detail = getattr(ctx, '_trace_auto_approval_detail', None)
    _event_run_notification_message = getattr(ctx, '_event_run_notification_message', None)
    _maybe_create_event_notification = getattr(ctx, '_maybe_create_event_notification', None)
    _create_scheduled_event_auto_approved_notification = getattr(ctx, '_create_scheduled_event_auto_approved_notification', None)
    _create_direct_event_notification = getattr(ctx, '_create_direct_event_notification', None)
    _cancel_agent_request = getattr(ctx, '_cancel_agent_request', None)
    _task_status_from_trace = getattr(ctx, '_task_status_from_trace', None)
    _task_preview_from_trace = getattr(ctx, '_task_preview_from_trace', None)
    _task_blocker_from_trace = getattr(ctx, '_task_blocker_from_trace', None)
    _task_checkpoint_detail = getattr(ctx, '_task_checkpoint_detail', None)
    _persist_task_checkpoints = getattr(ctx, '_persist_task_checkpoints', None)
    _context_is_durable_task = getattr(ctx, '_context_is_durable_task', None)
    _has_active_non_task_request = getattr(ctx, '_has_active_non_task_request', None)
    _track_active_non_task_request = getattr(ctx, '_track_active_non_task_request', None)
    _task_notification_level = getattr(ctx, '_task_notification_level', None)
    _task_notification_body = getattr(ctx, '_task_notification_body', None)
    _maybe_create_task_notification = getattr(ctx, '_maybe_create_task_notification', None)
    _finalize_task_attempt_from_trace = getattr(ctx, '_finalize_task_attempt_from_trace', None)
    _task_record_payload = getattr(ctx, '_task_record_payload', None)
    _task_attempt_payload = getattr(ctx, '_task_attempt_payload', None)
    _task_checkpoint_payload = getattr(ctx, '_task_checkpoint_payload', None)
    _mark_task_attempt_waiting_for_auto_approval = getattr(ctx, '_mark_task_attempt_waiting_for_auto_approval', None)
    _task_auto_approve_enabled = getattr(ctx, '_task_auto_approve_enabled', None)
    _auto_approve_durable_task_confirmation = getattr(ctx, '_auto_approve_durable_task_confirmation', None)
    _monitor_task_attempt = getattr(ctx, '_monitor_task_attempt', None)
    _worker = getattr(ctx, '_worker', None)
    _start_next_queued_task = getattr(ctx, '_start_next_queued_task', None)
    _monitor_active_non_task_request = getattr(ctx, '_monitor_active_non_task_request', None)
    _worker = getattr(ctx, '_worker', None)
    _task_context = getattr(ctx, '_task_context', None)
    _start_task_now = getattr(ctx, '_start_task_now', None)
    _queue_or_start_task = getattr(ctx, '_queue_or_start_task', None)
    _link_task_followup_request = getattr(ctx, '_link_task_followup_request', None)
    _reconcile_open_tasks = getattr(ctx, '_reconcile_open_tasks', None)
    _monitor_record_payload = getattr(ctx, '_monitor_record_payload', None)
    _monitor_observation_payload = getattr(ctx, '_monitor_observation_payload', None)
    _monitor_draft_payload = getattr(ctx, '_monitor_draft_payload', None)
    _start_monitor = getattr(ctx, '_start_monitor', None)
    _cancel_monitor = getattr(ctx, '_cancel_monitor', None)
    _run_scheduled_event_request = getattr(ctx, '_run_scheduled_event_request', None)
    _approve_confirmation_request = getattr(ctx, '_approve_confirmation_request', None)
    _run_confirmed_request_worker = getattr(ctx, '_run_confirmed_request_worker', None)
    _auto_approve_scheduled_event = getattr(ctx, '_auto_approve_scheduled_event', None)
    _run_scheduled_event_worker = getattr(ctx, '_run_scheduled_event_worker', None)
    _launch_scheduled_event = getattr(ctx, '_launch_scheduled_event', None)

    def _reliability_run_payload(request_id: str) -> dict[str, Any]:
        events = list(reversed(reliability_store.list_events(request_id=request_id, limit=500)))
        envelope = reliability_store.get_latest_envelope(request_id)
        failure_counts: dict[str, int] = {}
        recovery_counts: dict[str, int] = {}
        verification: dict[str, Any] | None = None
        for event in events:
            if event.failure_kind:
                failure_counts[event.failure_kind] = failure_counts.get(event.failure_kind, 0) + 1
            if event.recovery_action:
                recovery_counts[event.recovery_action] = recovery_counts.get(event.recovery_action, 0) + 1
            if event.event_kind == "verification_completed":
                verification = dict(event.evidence or {})
        return {
            "request_id": request_id,
            "events": [event.model_dump(mode="json") for event in events],
            "event_count": len(events),
            "failure_counts": failure_counts,
            "recovery_counts": recovery_counts,
            "verification": verification,
            "approval_envelope": envelope.model_dump(mode="json") if envelope else None,
            "trace_url": f"/api/agent/trace/{request_id}",
            "report_json_url": f"/api/agent/reliability/runs/{request_id}/report.json",
            "report_markdown_url": f"/api/agent/reliability/runs/{request_id}/report.md",
        }

    def _reliability_trace_summary(request_id: str) -> dict[str, Any]:
        events = reliability_store.list_events(request_id=request_id, limit=200)
        if not events:
            return {
                "event_count": 0,
                "failure_count": 0,
                "recovery_count": 0,
                "verification": None,
            }
        verification = next(
            (
                dict(event.evidence or {})
                for event in events
                if event.event_kind == "verification_completed"
            ),
            None,
        )
        verification_obligation_counts: dict[str, int] | None = None
        if isinstance(verification, dict):
            obligations = list(verification.get("obligations") or [])
            coverage_review = verification.get("coverage_review") or {}
            verification_obligation_counts = {
                "total": len(obligations),
                "missing": len(list(verification.get("missing_obligations") or [])),
                "covered": len(list(coverage_review.get("covered_obligations") or []))
                if isinstance(coverage_review, dict)
                else 0,
                "unsupported_claims": len(list(coverage_review.get("unsupported_claims") or []))
                if isinstance(coverage_review, dict)
                else 0,
            }
        return {
            "event_count": len(events),
            "failure_count": sum(1 for event in events if event.event_kind == "failure_detected"),
            "recovery_count": sum(
                1
                for event in events
                if event.event_kind in {"recovery_decided", "recovery_accepted"}
            ),
            "last_event_at": events[0].created_at,
            "verification": verification,
            "verification_obligation_counts": verification_obligation_counts,
            "timeline": [
                {
                    "event_kind": event.event_kind,
                    "failure_kind": event.failure_kind,
                    "recovery_action": event.recovery_action,
                    "title": event.title,
                    "summary": event.summary,
                    "status": event.status,
                    "created_at": event.created_at,
                }
                for event in reversed(events[:50])
            ],
        }

    def _reliability_markdown_report(request_id: str) -> str:
        payload = _reliability_run_payload(request_id)
        lines = [
            f"# Reliability Recovery Report: {request_id}",
            "",
            f"- Events: {payload['event_count']}",
            f"- Failures: {sum(payload['failure_counts'].values())}",
            f"- Recoveries: {sum(payload['recovery_counts'].values())}",
        ]
        verification = payload.get("verification") or {}
        if verification:
            lines.append(f"- Verification: {verification.get('status', 'unknown')}")
            lines.append(f"- Verification reason: {verification.get('reason', '')}")
            obligations = list(verification.get("obligations") or [])
            missing_obligations = list(verification.get("missing_obligations") or [])
            coverage_review = verification.get("coverage_review") or {}
            if obligations or coverage_review:
                lines.append(f"- Evidence obligations: {len(obligations)}")
                lines.append(f"- Missing obligations: {len(missing_obligations)}")
                unsupported = list(coverage_review.get("unsupported_claims") or [])
                contradicted = list(coverage_review.get("contradicted_obligations") or [])
                if contradicted:
                    lines.append(f"- Contradicted obligations: {len(contradicted)}")
                if unsupported:
                    lines.append(f"- Unsupported claims: {len(unsupported)}")
                if missing_obligations:
                    lines.extend(["", "## Missing Evidence Obligations"])
                    by_id = {
                        str(item.get("obligation_id") or ""): item
                        for item in obligations
                        if isinstance(item, dict)
                    }
                    for obligation_id in missing_obligations[:20]:
                        obligation = by_id.get(str(obligation_id)) or {}
                        label = obligation.get("label") or obligation_id
                        summary = obligation.get("value_summary") or ""
                        lines.append(f"- `{obligation_id}` {label}: {summary}")
        envelope = payload.get("approval_envelope") or {}
        if envelope:
            lines.extend(
                [
                    "",
                    "## Approval Envelope",
                    f"- Status: {envelope.get('status', '')}",
                    f"- Max risk: {envelope.get('max_risk', '')}",
                    f"- Mutation budget: {envelope.get('used_mutations', 0)}/{envelope.get('mutation_budget', 0)}",
                    f"- CWD: {', '.join(envelope.get('cwd_values') or [])}",
                ]
            )
        lines.extend(["", "## Timeline"])
        for event in payload["events"]:
            labels = [
                str(event.get("event_kind") or ""),
                str(event.get("failure_kind") or "").strip(),
                str(event.get("recovery_action") or "").strip(),
            ]
            label = " / ".join(item for item in labels if item)
            lines.append(f"- {event.get('created_at', '')} `{label}`: {event.get('title', '')}")
            summary = str(event.get("summary") or "").strip()
            if summary:
                lines.append(f"  {summary}")
        return "\n".join(lines).strip() + "\n"

    for _name in (
        '_reliability_run_payload',
        '_reliability_trace_summary',
        '_reliability_markdown_report'
    ):
        setattr(ctx, _name, locals()[_name])


__all__ = [name for name in globals() if not name.startswith("__")]
