"""Reusable monitor lifecycle manager for Agent UI and runtime capabilities."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_runtime.core.ids import new_id
from agent_runtime.events import AgentNotificationCreate
from agent_runtime.monitors.drafting import (
    DEFAULT_JUDGE_INTERVAL_SECONDS,
    DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS,
    MonitorSpecValidationError,
    draft_monitor_from_prompt,
    validate_monitor_create,
)
from agent_runtime.monitors.models import (
    AgentMonitorCreate,
    AgentMonitorDraftRequest,
    AgentMonitorDraftResponse,
    AgentMonitorJudgement,
    AgentMonitorRecord,
    AgentMonitorUpdate,
)
from agent_runtime.monitors.store import AgentMonitorStore
from agent_runtime.prompts import prompt_lines
from agent_runtime.tasks import AgentTaskCreate


class AgentMonitorManagerError(RuntimeError):
    """Base error for monitor manager operations."""


class AgentMonitorNotFoundError(AgentMonitorManagerError):
    """Raised when a monitor id does not exist."""


class AgentMonitorConflictError(AgentMonitorManagerError):
    """Raised when a monitor operation conflicts with current state."""


def monitor_output_text(*, stdout: str = "", stderr: str = "", output: str = "") -> str:
    return "\n".join(part for part in (output, stdout, stderr) if str(part or "").strip())


def monitor_condition_match(condition: str, text: str, *, exit_code: int | None = None) -> tuple[bool, str]:
    raw = str(condition or "").strip()
    if not raw:
        return False, ""
    output = str(text or "")
    lower_output = output.lower()
    lower_condition = raw.lower()
    if lower_condition.startswith("contains:"):
        needle = raw.split(":", 1)[1].strip()
        if needle and needle.lower() in lower_output:
            return True, f"Output contained `{needle}`."
        return False, ""
    if lower_condition.startswith("regex:"):
        pattern = raw.split(":", 1)[1].strip()
        if pattern and re.search(pattern, output, re.IGNORECASE | re.MULTILINE):
            return True, f"Output matched /{pattern}/."
        return False, ""
    if lower_condition.startswith("number_lt:") or lower_condition.startswith("number_gt:"):
        operator = "lt" if lower_condition.startswith("number_lt:") else "gt"
        try:
            threshold = float(raw.split(":", 1)[1].strip())
        except ValueError:
            return False, ""
        number_match = re.search(r"-?\d+(?:\.\d+)?", output)
        if not number_match:
            return False, ""
        value = float(number_match.group(0))
        if operator == "lt" and value < threshold:
            return True, f"Observed {value:g}, below {threshold:g}."
        if operator == "gt" and value > threshold:
            return True, f"Observed {value:g}, above {threshold:g}."
        return False, ""
    if lower_condition == "exit_code_nonzero" and exit_code not in {None, 0}:
        return True, f"Command exited with {exit_code}."
    if lower_condition in lower_output:
        return True, f"Output contained `{raw}`."
    return False, ""


@dataclass
class AgentMonitorManager:
    """Coordinate persistent monitors and their background terminal workers."""

    monitor_store: AgentMonitorStore
    event_store: Any
    task_store: Any | None = None
    gateway_store: Any | None = None
    gateway_client_provider: Callable[[], Any] | None = None
    gateway_context_resolver: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None
    terminal_cwd_resolver: Callable[[Any], str] | None = None
    task_starter: Callable[[str], dict[str, Any]] | None = None
    llm_client_provider: Callable[[], Any] | None = None
    llm_monitor_planner_enabled: bool = True
    llm_trigger_judge_enabled: bool = True
    judge_min_interval_seconds: int = DEFAULT_JUDGE_INTERVAL_SECONDS
    judge_output_preview_cap: int = 4000
    max_inferred_monitor_duration_seconds: int = DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS
    lock: threading.RLock = field(default_factory=threading.RLock)
    cancel_events: dict[str, threading.Event] = field(default_factory=dict)
    threads: dict[str, threading.Thread] = field(default_factory=dict)
    judge_last_called_at: dict[str, float] = field(default_factory=dict)

    def interrupt_active_monitors(self) -> int:
        return self.monitor_store.interrupt_active_monitors()

    def draft_monitor(self, payload: AgentMonitorDraftRequest | dict[str, Any]) -> AgentMonitorDraftResponse:
        return draft_monitor_from_prompt(
            payload,
            llm_client=self._llm_client() if self.llm_monitor_planner_enabled else None,
            planner_enabled=self.llm_monitor_planner_enabled,
            max_duration_seconds=self.max_inferred_monitor_duration_seconds,
            judge_min_interval_seconds=self.judge_min_interval_seconds,
        )

    def record_payload(self, monitor: Any, *, include_latest_observation: bool = True) -> dict[str, Any]:
        payload = monitor.model_dump(mode="json") if hasattr(monitor, "model_dump") else dict(monitor or {})
        if include_latest_observation:
            observation_id = str(payload.get("latest_observation_id") or "").strip()
            observation = self.monitor_store.get_observation(observation_id) if observation_id else None
            payload["latest_observation"] = (
                observation.model_dump(mode="json") if observation is not None else None
            )
        triggers = self.monitor_store.list_triggers(str(payload.get("monitor_id") or ""), limit=20)
        payload["triggers"] = [trigger.model_dump(mode="json") for trigger in triggers]
        return payload

    @staticmethod
    def observation_payload(observation: Any) -> dict[str, Any]:
        return observation.model_dump(mode="json") if hasattr(observation, "model_dump") else dict(observation or {})

    def list_monitors(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        monitors = self.monitor_store.list_monitors(
            status=status,
            limit=max(1, min(int(limit or 50), 500)),
            include_archived=include_archived,
        )
        return {
            "monitors": [self.record_payload(monitor) for monitor in monitors],
            "counts": self.monitor_store.counts(),
        }

    def create_monitor(self, payload: AgentMonitorCreate, *, start_now: bool = True) -> dict[str, Any]:
        try:
            validated_payload = validate_monitor_create(
                payload,
                max_duration_seconds=self.max_inferred_monitor_duration_seconds,
                judge_min_interval_seconds=self.judge_min_interval_seconds,
            )
        except MonitorSpecValidationError as exc:
            raise AgentMonitorConflictError(str(exc)) from exc
        monitor = self.monitor_store.create_monitor(validated_payload)
        if start_now:
            result = self.start_monitor(monitor.monitor_id)
            result["created"] = True
            return result
        return {"status": "queued", "created": True, "monitor": self.record_payload(monitor)}

    def get_monitor(self, monitor_id: str) -> dict[str, Any]:
        monitor = self.monitor_store.get_monitor(monitor_id)
        if monitor is None:
            raise AgentMonitorNotFoundError("Monitor not found.")
        return {"monitor": self.record_payload(monitor)}

    def list_observations(
        self,
        monitor_id: str,
        *,
        limit: int = 200,
        after_sequence: int = 0,
    ) -> dict[str, Any]:
        if self.monitor_store.get_monitor(monitor_id) is None:
            raise AgentMonitorNotFoundError("Monitor not found.")
        observations = self.monitor_store.list_observations(
            monitor_id,
            limit=max(1, min(int(limit or 200), 1000)),
            after_sequence=max(0, int(after_sequence or 0)),
        )
        triggers = self.monitor_store.list_triggers(monitor_id, limit=100)
        return {
            "observations": [self.observation_payload(item) for item in observations],
            "triggers": [trigger.model_dump(mode="json") for trigger in triggers],
        }

    def stream_events(self, monitor_id: str, *, after_sequence: int = 0) -> Any:
        if self.monitor_store.get_monitor(monitor_id) is None:
            raise AgentMonitorNotFoundError("Monitor not found.")

        def event_stream() -> Any:
            cursor = max(0, int(after_sequence or 0))
            last_status = ""
            idle_terminal_cycles = 0
            while True:
                monitor = self.monitor_store.get_monitor(monitor_id)
                if monitor is None:
                    yield "event: error\ndata: {\"error\": \"Monitor not found.\"}\n\n"
                    return
                if monitor.status != last_status:
                    last_status = monitor.status
                    yield f"event: status\ndata: {json.dumps(self.record_payload(monitor), default=str)}\n\n"
                observations = self.monitor_store.list_observations(monitor_id, limit=100, after_sequence=cursor)
                for observation in observations:
                    cursor = max(cursor, int(observation.sequence or 0))
                    yield f"event: observation\ndata: {json.dumps(self.observation_payload(observation), default=str)}\n\n"
                if monitor.status in {"triggered", "completed", "failed", "cancelled", "interrupted", "archived"}:
                    idle_terminal_cycles += 1
                    if idle_terminal_cycles >= 2:
                        return
                else:
                    idle_terminal_cycles = 0
                time.sleep(0.5)

        return event_stream()

    def start_monitor(self, monitor_id: str) -> dict[str, Any]:
        monitor = self.monitor_store.get_monitor(monitor_id)
        if monitor is None:
            raise AgentMonitorNotFoundError("Monitor not found.")
        if monitor.archived_at:
            raise AgentMonitorConflictError("Archived monitors cannot be started.")
        if monitor.status == "running":
            return {"status": "running", "monitor": self.record_payload(monitor)}
        with self.lock:
            existing = self.threads.get(monitor_id)
            if existing is not None and existing.is_alive():
                return {"status": "running", "monitor": self.record_payload(monitor)}
            cancel_event = threading.Event()
            self.cancel_events[monitor_id] = cancel_event
            self.monitor_store.update_monitor(
                monitor_id,
                AgentMonitorUpdate(status="queued", current_execution_id="", error_preview="", final_summary=""),
            )
            target = self._run_raw_stream if monitor.mode == "raw_stream" else self._run_sample_loop
            thread = threading.Thread(
                target=target,
                args=(monitor_id, cancel_event),
                daemon=True,
                name=f"agent-monitor-{monitor_id}",
            )
            self.threads[monitor_id] = thread
            thread.start()
        return {"status": "queued", "monitor": self.record_payload(self.monitor_store.get_monitor(monitor_id))}

    def cancel_monitor(self, monitor_id: str, *, status: str = "cancelled") -> dict[str, Any]:
        monitor = self.monitor_store.get_monitor(monitor_id)
        if monitor is None:
            raise AgentMonitorNotFoundError("Monitor not found.")
        final_status = "paused" if status == "paused" else "cancelled"
        with self.lock:
            cancel_event = self.cancel_events.get(monitor_id)
            if cancel_event is not None:
                cancel_event.set()
        execution_id = str(monitor.current_execution_id or "").strip()
        if execution_id:
            try:
                context, _cwd = self._gateway_context(
                    monitor,
                    monitor.terminal_session_id or new_id("mon_term"),
                )
                context["gateway_execution_id"] = execution_id
                self._gateway_client().cancel_raw_command(
                    execution_id=execution_id,
                    execution_context=context,
                )
            except Exception:
                pass
        updated = self.monitor_store.update_monitor(
            monitor_id,
            AgentMonitorUpdate(
                status=final_status,  # type: ignore[arg-type]
                current_execution_id="",
                error_preview="Monitor paused." if final_status == "paused" else "Monitor cancelled by the user.",
            ),
        )
        return {"status": final_status, "monitor": self.record_payload(updated)}

    def archive_monitor(self, monitor_id: str) -> dict[str, Any]:
        monitor = self.monitor_store.get_monitor(monitor_id)
        if monitor is None:
            raise AgentMonitorNotFoundError("Monitor not found.")
        if monitor.status == "running":
            self.cancel_monitor(monitor_id, status="cancelled")
        archived = self.monitor_store.archive_monitor(monitor_id)
        return {"status": "archived", "monitor": self.record_payload(archived)}

    def _next_sequence(self, monitor_id: str) -> int:
        observations = self.monitor_store.list_observations(monitor_id, limit=1000)
        return max((int(item.sequence or 0) for item in observations), default=0) + 1

    def _gateway_client(self) -> Any:
        if self.gateway_client_provider is None:
            raise RuntimeError("Gateway client is unavailable for monitors.")
        gateway_client = self.gateway_client_provider()
        if gateway_client is None:
            raise RuntimeError("Gateway client is unavailable for monitors.")
        return gateway_client

    def _llm_client(self) -> Any | None:
        if self.llm_client_provider is None:
            return None
        try:
            return self.llm_client_provider()
        except Exception:
            return None

    def _gateway_context(self, monitor: AgentMonitorRecord, terminal_session_id: str) -> tuple[dict[str, Any], str]:
        context = dict(monitor.context or {})
        if monitor.gateway_id and not str(context.get("gateway_id") or "").strip():
            context["gateway_id"] = monitor.gateway_id
        if self.gateway_context_resolver is not None:
            gateway_context = self.gateway_context_resolver(context)
            if gateway_context:
                context.update(gateway_context)
        gateway_record = None
        if self.gateway_store is not None:
            gateway_record = self.gateway_store.get(str(context.get("gateway_id") or "").strip())
        terminal_cwd = (
            self.terminal_cwd_resolver(gateway_record)
            if self.terminal_cwd_resolver is not None
            else ""
        )
        context.update(
            {
                "monitor_id": monitor.monitor_id,
                "terminal_session_id": terminal_session_id,
                "terminal_cwd": terminal_cwd,
                "execute_in_terminal": True,
            }
        )
        return context, terminal_cwd

    def _ensure_terminal(
        self,
        monitor: AgentMonitorRecord,
        terminal_session_id: str,
        context: dict[str, Any],
        cwd: str,
    ) -> None:
        create_terminal_session = getattr(self._gateway_client(), "create_terminal_session", None)
        if not callable(create_terminal_session):
            raise RuntimeError("Gateway terminal sessions are unavailable for monitors.")
        create_terminal_session(
            session_id=terminal_session_id,
            initial_cwd=cwd,
            rows=24,
            cols=120,
            execution_context=context,
        )

    def _notification(
        self,
        monitor: AgentMonitorRecord,
        *,
        level: str,
        title: str,
        message: str,
        observation_id: str = "",
        task_id: str = "",
    ) -> str:
        notification = self.event_store.create_notification(
            AgentNotificationCreate(
                level=level,  # type: ignore[arg-type]
                title=title,
                message=message or title,
                source_type="monitor",
                source_id=f"{monitor.monitor_id}:{observation_id or monitor.status}",
                metadata={
                    "monitor_id": monitor.monitor_id,
                    "monitor_status": monitor.status,
                    "observation_id": observation_id,
                    "task_id": task_id,
                    "terminal_session_id": monitor.terminal_session_id,
                },
            )
        )
        return notification.notification_id

    def _launch_task(
        self,
        monitor: AgentMonitorRecord,
        *,
        observation_id: str,
        reason: str,
        output_preview: str,
        task_context: dict[str, Any] | None = None,
    ) -> str:
        prompt = str(monitor.action_prompt or "").strip()
        if not prompt or self.task_store is None:
            return ""
        context = dict(monitor.context or {})
        context.update(
            {
                "monitor_id": monitor.monitor_id,
                "monitor_observation_id": observation_id,
                "monitor_trigger_reason": reason,
                "monitor_output_preview": output_preview[:4000],
                "monitor_original_prompt": monitor.prompt,
                "monitor_background_terminal_id": monitor.terminal_session_id,
            }
        )
        if task_context:
            context["monitor_judge_task_context"] = dict(task_context)
        if monitor.gateway_id:
            context["gateway_id"] = monitor.gateway_id
        task = self.task_store.create_task(
            AgentTaskCreate(
                prompt=prompt,
                title=f"Monitor action: {monitor.title or monitor.monitor_id}",
                source="task_sheet",
                status="queued",
                agent_mode=monitor.agent_mode,
                conversation_id=monitor.conversation_id,
                gateway_id=monitor.gateway_id,
                context=context,
            )
        )
        if self.task_starter is None:
            return task.task_id
        result = self.task_starter(task.task_id)
        task_payload = result.get("task") if isinstance(result, dict) else None
        return str((task_payload or {}).get("task_id") or task.task_id)

    def _handle_trigger(
        self,
        monitor_id: str,
        *,
        observation_id: str,
        reason: str,
        output_preview: str,
        notification_summary: str = "",
        trigger_detail: dict[str, Any] | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> None:
        monitor = self.monitor_store.get_monitor(monitor_id)
        if monitor is None:
            return
        task_id = self._launch_task(
            monitor,
            observation_id=observation_id,
            reason=reason,
            output_preview=output_preview,
            task_context=task_context,
        )
        updated = self.monitor_store.update_monitor(
            monitor_id,
            AgentMonitorUpdate(
                status="triggered",
                current_execution_id="",
                trigger_reason=reason,
                triggered_task_id=task_id,
                final_summary=output_preview,
            ),
        )
        monitor_for_notification = updated or monitor
        notification_id = self._notification(
            monitor_for_notification,
            level="warning",
            title=f"Monitor triggered: {monitor_for_notification.title}",
            message=notification_summary or reason or output_preview or "Monitor condition was met.",
            observation_id=observation_id,
            task_id=task_id,
        )
        detail = {"output_preview": output_preview[:4000]}
        if trigger_detail:
            detail.update(trigger_detail)
        self.monitor_store.add_trigger(
            monitor_id=monitor_id,
            observation_id=observation_id,
            reason=reason,
            notification_id=notification_id,
            task_id=task_id,
            detail=detail,
        )

    def _judge_prompt(self, monitor: AgentMonitorRecord, *, recent_output: str, exit_code: int | None) -> str:
        return "\n".join(
            [
                *prompt_lines("monitors.judge"),
                "Monitor:",
                json.dumps(
                    {
                        "monitor_id": monitor.monitor_id,
                        "title": monitor.title,
                        "prompt": monitor.prompt,
                        "command": monitor.command,
                        "deterministic_condition": monitor.condition,
                        "natural_language_condition": monitor.natural_language_condition,
                        "trigger_mode": monitor.trigger_mode,
                        "exit_code": exit_code,
                    },
                    default=str,
                    ensure_ascii=True,
                ),
                "Recent output:",
                recent_output,
            ]
        )

    def _recent_output_for_judge(self, monitor_id: str, latest_text: str) -> str:
        cap = max(500, int(self.judge_output_preview_cap or 4000))
        observations = self.monitor_store.list_observations(monitor_id, limit=8)
        parts = [str(item.output_preview or item.stdout or item.stderr or "").strip() for item in observations]
        if latest_text.strip():
            parts.append(latest_text.strip())
        text = "\n\n".join(part for part in parts if part)
        return text[-cap:]

    def _maybe_llm_judge(
        self,
        monitor: AgentMonitorRecord,
        *,
        latest_text: str,
        exit_code: int | None = None,
    ) -> tuple[AgentMonitorJudgement | None, dict[str, Any]]:
        if not self.llm_trigger_judge_enabled or monitor.trigger_mode not in {"llm_judged", "hybrid"}:
            return None, {}
        if not str(monitor.natural_language_condition or "").strip():
            return None, {}
        cadence = max(
            int(self.judge_min_interval_seconds or DEFAULT_JUDGE_INTERVAL_SECONDS),
            int(monitor.judge_interval_seconds or DEFAULT_JUDGE_INTERVAL_SECONDS),
            1,
        )
        now = time.monotonic()
        last_called = float(self.judge_last_called_at.get(monitor.monitor_id, 0.0))
        if last_called and now - last_called < cadence:
            return None, {"judge_rate_limited": True}
        self.judge_last_called_at[monitor.monitor_id] = now
        client = self._llm_client()
        complete_json = getattr(client, "complete_json", None)
        if not callable(complete_json):
            summary = "LLM judge unavailable; collecting observations."
            self.monitor_store.update_monitor(monitor.monitor_id, AgentMonitorUpdate(latest_judge_summary=summary))
            return None, {"judge_unavailable": True, "judge_summary": summary}
        recent_output = self._recent_output_for_judge(monitor.monitor_id, latest_text)
        try:
            raw = complete_json(
                self._judge_prompt(monitor, recent_output=recent_output, exit_code=exit_code),
                AgentMonitorJudgement.model_json_schema(),
            )
            judgement = AgentMonitorJudgement.model_validate(raw)
        except Exception as exc:
            summary = "LLM judge failed; collecting observations."
            self.monitor_store.update_monitor(monitor.monitor_id, AgentMonitorUpdate(latest_judge_summary=summary))
            return None, {
                "judge_unavailable": True,
                "judge_summary": summary,
                "judge_error": str(exc)[:400],
            }
        state = "trigger" if judgement.should_trigger else "no trigger"
        summary_reason = judgement.reason or judgement.matched_excerpt or "No reason supplied."
        summary = f"LLM judge {state} ({judgement.confidence:.2f}): {summary_reason}"
        self.monitor_store.update_monitor(monitor.monitor_id, AgentMonitorUpdate(latest_judge_summary=summary))
        return judgement, {
            "judge_summary": summary,
            "judgement": judgement.model_dump(mode="json"),
        }

    def _evaluate_monitor_output(
        self,
        monitor: AgentMonitorRecord,
        *,
        output: str,
        exit_code: int | None = None,
    ) -> tuple[bool, str, dict[str, Any], str, dict[str, Any]]:
        matched = False
        reason = ""
        detail: dict[str, Any] = {}
        notification_summary = ""
        task_context: dict[str, Any] = {}
        if monitor.trigger_mode in {"deterministic", "hybrid"}:
            matched, reason = monitor_condition_match(monitor.condition, output, exit_code=exit_code)
        if not matched and monitor.trigger_mode in {"llm_judged", "hybrid"}:
            judgement, judge_detail = self._maybe_llm_judge(monitor, latest_text=output, exit_code=exit_code)
            if judge_detail:
                detail["llm_judge"] = judge_detail
            if judgement is not None and judgement.should_trigger and judgement.confidence >= 0.5:
                matched = True
                reason = judgement.reason or "LLM judge matched the monitor condition."
                notification_summary = judgement.recommended_notification_summary
                task_context = dict(judgement.task_context or {})
        return matched, reason, detail, notification_summary, task_context

    def _run_sample_loop(self, monitor_id: str, cancel_event: threading.Event) -> None:
        monitor = self.monitor_store.get_monitor(monitor_id)
        if monitor is None:
            return
        terminal_session_id = new_id("mon_term")
        context, cwd = self._gateway_context(monitor, terminal_session_id)
        try:
            self._ensure_terminal(monitor, terminal_session_id, context, cwd)
            self.monitor_store.update_monitor(
                monitor_id,
                AgentMonitorUpdate(status="running", terminal_session_id=terminal_session_id),
            )
            deadline = time.monotonic() + max(1, int(monitor.duration_seconds or 300))
            while not cancel_event.is_set() and time.monotonic() < deadline:
                sequence = self._next_sequence(monitor_id)
                execution_id = f"{monitor_id}:sample:{sequence}"
                self.monitor_store.update_monitor(monitor_id, AgentMonitorUpdate(current_execution_id=execution_id))
                command_context = dict(context)
                command_context["request_id"] = execution_id
                command_context["gateway_execution_id"] = execution_id
                stdout_parts: list[str] = []
                stderr_parts: list[str] = []
                exit_code: int | None = None
                for event in self._gateway_client().stream_terminal_command(
                    command=monitor.command,
                    cwd=cwd,
                    execution_context=command_context,
                    display_command=monitor.command,
                ):
                    event_type = str(event.get("type") or "")
                    text = str(event.get("text") or event.get("data") or "")
                    if event_type == "stdout":
                        stdout_parts.append(text)
                    elif event_type in {"stderr", "error", "terminal_input_required", "cancelled"}:
                        stderr_parts.append(text)
                    if "exit_code" in event:
                        try:
                            exit_code = int(event.get("exit_code"))
                        except (TypeError, ValueError):
                            exit_code = None
                    if cancel_event.is_set():
                        break
                stdout = "".join(stdout_parts)
                stderr = "".join(stderr_parts)
                output = monitor_output_text(stdout=stdout, stderr=stderr)
                matched, reason, evaluation_detail, notification_summary, task_context = self._evaluate_monitor_output(
                    monitor,
                    output=output,
                    exit_code=exit_code,
                )
                observation = self.monitor_store.add_observation(
                    monitor_id=monitor_id,
                    sequence=sequence,
                    kind="sample",
                    status="matched" if matched else "ok",
                    stdout=stdout,
                    stderr=stderr,
                    output_preview=output[:4000],
                    exit_code=exit_code,
                    matched=matched,
                    match_reason=reason,
                    detail={
                        "terminal_session_id": terminal_session_id,
                        "execution_id": execution_id,
                        **evaluation_detail,
                    },
                )
                if matched:
                    self._handle_trigger(
                        monitor_id,
                        observation_id=observation.observation_id,
                        reason=reason,
                        output_preview=output,
                        notification_summary=notification_summary,
                        trigger_detail=evaluation_detail,
                        task_context=task_context,
                    )
                    return
                self.monitor_store.update_monitor(monitor_id, AgentMonitorUpdate(current_execution_id=""))
                sleep_until = min(deadline, time.monotonic() + max(1, int(monitor.interval_seconds or 5)))
                while not cancel_event.is_set() and time.monotonic() < sleep_until:
                    time.sleep(0.1)
            self._finish_monitor_after_loop(monitor_id, cancel_event=cancel_event)
        except Exception as exc:
            self._fail_monitor(monitor_id, str(exc))
        finally:
            with self.lock:
                self.threads.pop(monitor_id, None)
                self.cancel_events.pop(monitor_id, None)

    def _run_raw_stream(self, monitor_id: str, cancel_event: threading.Event) -> None:
        monitor = self.monitor_store.get_monitor(monitor_id)
        if monitor is None:
            return
        terminal_session_id = new_id("mon_term")
        context, cwd = self._gateway_context(monitor, terminal_session_id)
        execution_id = f"{monitor_id}:raw"
        command_context = dict(context)
        try:
            self._ensure_terminal(monitor, terminal_session_id, context, cwd)
            self.monitor_store.update_monitor(
                monitor_id,
                AgentMonitorUpdate(status="running", terminal_session_id=terminal_session_id),
            )
            deadline = time.monotonic() + max(1, int(monitor.duration_seconds or 300))
            self.monitor_store.update_monitor(monitor_id, AgentMonitorUpdate(current_execution_id=execution_id))
            command_context["request_id"] = execution_id
            command_context["gateway_execution_id"] = execution_id
            for event in self._gateway_client().stream_terminal_command(
                command=monitor.command,
                cwd=cwd,
                execution_context=command_context,
                display_command=monitor.command,
            ):
                if cancel_event.is_set() or time.monotonic() >= deadline:
                    try:
                        self._gateway_client().cancel_raw_command(
                            execution_id=execution_id,
                            execution_context=command_context,
                        )
                    except Exception:
                        pass
                    break
                event_type = str(event.get("type") or "output")
                text = str(event.get("text") or event.get("data") or "")
                if not text and event_type == "completed":
                    continue
                exit_code = event.get("exit_code") if isinstance(event.get("exit_code"), int) else None
                matched, reason, evaluation_detail, notification_summary, task_context = self._evaluate_monitor_output(
                    monitor,
                    output=text,
                    exit_code=exit_code,
                )
                observation = self.monitor_store.add_observation(
                    monitor_id=monitor_id,
                    sequence=self._next_sequence(monitor_id),
                    kind="raw_chunk" if event_type in {"stdout", "stderr", "output"} else event_type,
                    status="matched" if matched else event_type or "ok",
                    stdout=text if event_type in {"stdout", "output"} else "",
                    stderr=text if event_type in {"stderr", "error", "cancelled"} else "",
                    output_preview=text[:4000],
                    exit_code=exit_code,
                    matched=matched,
                    match_reason=reason,
                    detail={
                        "terminal_session_id": terminal_session_id,
                        "execution_id": execution_id,
                        **evaluation_detail,
                    },
                )
                if matched:
                    self._handle_trigger(
                        monitor_id,
                        observation_id=observation.observation_id,
                        reason=reason,
                        output_preview=text,
                        notification_summary=notification_summary,
                        trigger_detail=evaluation_detail,
                        task_context=task_context,
                    )
                    try:
                        self._gateway_client().cancel_raw_command(
                            execution_id=execution_id,
                            execution_context=command_context,
                        )
                    except Exception:
                        pass
                    return
            self._finish_monitor_after_loop(monitor_id, cancel_event=cancel_event)
        except Exception as exc:
            self._fail_monitor(monitor_id, str(exc))
        finally:
            with self.lock:
                self.threads.pop(monitor_id, None)
                self.cancel_events.pop(monitor_id, None)

    def _finish_monitor_after_loop(self, monitor_id: str, *, cancel_event: threading.Event) -> None:
        if cancel_event.is_set():
            current_monitor = self.monitor_store.get_monitor(monitor_id)
            final_status = "paused" if current_monitor is not None and current_monitor.status == "paused" else "cancelled"
            updated = self.monitor_store.update_monitor(
                monitor_id,
                AgentMonitorUpdate(status=final_status, current_execution_id=""),
            )
            if updated is not None:
                self._notification(
                    updated,
                    level="warning" if final_status == "paused" else "error",
                    title=f"Monitor {final_status}: {updated.title}",
                    message="Monitor paused." if final_status == "paused" else "Monitor cancelled by the user.",
                )
            return
        latest_observations = self.monitor_store.list_observations(monitor_id, limit=5)
        latest = latest_observations[-1].output_preview if latest_observations else ""
        updated = self.monitor_store.update_monitor(
            monitor_id,
            AgentMonitorUpdate(
                status="completed",
                current_execution_id="",
                final_summary=latest or "Monitor completed without matching its trigger condition.",
            ),
        )
        if updated is not None:
            self._notification(
                updated,
                level="success",
                title=f"Monitor completed: {updated.title}",
                message=updated.final_summary or "Monitor completed.",
                observation_id=updated.latest_observation_id,
            )

    def _fail_monitor(self, monitor_id: str, error_preview: str) -> None:
        updated = self.monitor_store.update_monitor(
            monitor_id,
            AgentMonitorUpdate(status="failed", current_execution_id="", error_preview=error_preview),
        )
        if updated is not None:
            self._notification(
                updated,
                level="error",
                title=f"Monitor failed: {updated.title}",
                message=error_preview,
            )
