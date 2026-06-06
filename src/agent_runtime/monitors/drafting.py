"""Monitor planning, validation, and deterministic drafting helpers."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

from pydantic import ValidationError

from agent_runtime.monitors.models import (
    AgentMonitorCreate,
    AgentMonitorDraft,
    AgentMonitorDraftRequest,
    AgentMonitorDraftResponse,
    AgentMonitorMode,
    AgentMonitorPlan,
    AgentMonitorTriggerMode,
)
from agent_runtime.prompts import prompt_lines


DEFAULT_MONITOR_DURATION_SECONDS = 300
DEFAULT_JUDGE_INTERVAL_SECONDS = 5
DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS = 300

_MONITOR_INTENT_TOKENS = (
    "monitor",
    "watch",
    "alert",
    "notify me if",
    "tell me if",
    "let me know if",
    "wait for",
    "track",
)
_QUALITATIVE_CONDITION_TOKENS = (
    "abnormal",
    "anomal",
    "looks",
    "seems",
    "saturation",
    "saturated",
    "pressure",
    "high",
    "too many",
    "too much",
    "spike",
    "degraded",
    "unhealthy",
)
_DESTRUCTIVE_PATTERNS = (
    r"\brm\s+-[^;&|]*r[^;&|]*f\b",
    r"\bmkfs(?:\.[a-z0-9]+)?\b",
    r"\bdd\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bpoweroff\b",
    r"\bdocker\s+system\s+prune\b",
    r"\bkubectl\s+delete\b",
    r"\bdrop\s+database\b",
    r"\btruncate\s+table\b",
    r"\bdelete\s+from\b",
    r":\s*\(\s*\)\s*\{",
)
_INTERACTIVE_COMMANDS = {
    "bash",
    "fish",
    "ftp",
    "htop",
    "less",
    "man",
    "more",
    "mysql",
    "nano",
    "node",
    "psql",
    "python",
    "python3",
    "read",
    "ssh",
    "top",
    "vi",
    "vim",
    "zsh",
}


class MonitorSpecValidationError(ValueError):
    """Raised when a monitor spec cannot be made safe enough to run."""


def _compact(value: Any, *, limit: int = 1000) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit]


def _seconds_from_match(value: str, unit: str) -> int:
    number = max(1, int(float(value or "1")))
    normalized = str(unit or "").lower()
    if normalized.startswith("hour") or normalized in {"h", "hr", "hrs"}:
        return number * 3600
    if normalized.startswith("min") or normalized in {"m", "mins"}:
        return number * 60
    return number


def _monitor_intent_detected(prompt: str) -> bool:
    lower = str(prompt or "").strip().lower()
    return lower.startswith("/monitor") or any(token in lower for token in _MONITOR_INTENT_TOKENS)


def _first_backticked_command(prompt: str) -> str:
    match = re.search(r"`([^`]+)`", str(prompt or ""))
    return match.group(1).strip() if match else ""


def duration_seconds_from_prompt(prompt: str) -> int:
    text = str(prompt or "").lower()
    match = re.search(
        r"\bfor\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b",
        text,
    )
    if match:
        return _seconds_from_match(match.group(1), match.group(2))
    return DEFAULT_MONITOR_DURATION_SECONDS


def interval_seconds_from_prompt(prompt: str) -> int:
    text = str(prompt or "").lower()
    watch_match = re.search(r"\bwatch\s+-n\s+(\d+(?:\.\d+)?)\b", text)
    if watch_match:
        return max(1, int(float(watch_match.group(1))))
    if re.search(r"\bevery\s+second\b", text):
        return 1
    match = re.search(
        r"\bevery\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m)\b",
        text,
    )
    if match:
        return max(1, _seconds_from_match(match.group(1), match.group(2)))
    return 5


def _explicit_contains_condition(prompt: str) -> str:
    match = re.search(r"\b(?:contains?|matches?|mentions?|says?|shows?)\s+(.+)$", str(prompt or ""), re.IGNORECASE)
    if not match:
        return ""
    candidate = " ".join(match.group(1).split()).strip(" .")
    return f"contains:{candidate[:240]}" if candidate else ""


def _qualitative_condition(candidate: str) -> bool:
    lower = str(candidate or "").lower()
    if not lower:
        return False
    return any(token in lower for token in _QUALITATIVE_CONDITION_TOKENS)


def condition_from_prompt(prompt: str) -> str:
    text = str(prompt or "").lower()
    threshold = re.search(r"\b(?:below|under|less than)\s+(\d+(?:\.\d+)?)\s*(gb|gib|mb|mib)?\b", text)
    if threshold:
        value = float(threshold.group(1))
        unit = str(threshold.group(2) or "mb").lower()
        if unit in {"gb", "gib"}:
            value *= 1024
        return f"number_lt:{int(value)}"
    threshold = re.search(r"\b(?:above|over|greater than)\s+(\d+(?:\.\d+)?)\s*(gb|gib|mb|mib|%)?\b", text)
    if threshold:
        value = float(threshold.group(1))
        unit = str(threshold.group(2) or "").lower()
        if unit in {"gb", "gib"}:
            value *= 1024
        return f"number_gt:{int(value)}"
    explicit = _explicit_contains_condition(prompt)
    if explicit:
        return explicit
    match = re.search(r"\b(?:if|when|alert if|notify me if|tell me if)\s+(.+)$", str(prompt or ""), re.IGNORECASE)
    if match:
        candidate = " ".join(match.group(1).split()).strip(" .")
        if candidate and not _qualitative_condition(candidate):
            return f"contains:{candidate[:240]}"
    return ""


def natural_language_condition_from_prompt(prompt: str, *, deterministic_condition: str = "") -> str:
    match = re.search(r"\b(?:if|when|alert if|notify me if|tell me if|let me know if)\s+(.+)$", str(prompt or ""), re.IGNORECASE)
    if not match:
        return ""
    candidate = " ".join(match.group(1).split()).strip(" .")
    if not candidate:
        return ""
    if deterministic_condition and not _qualitative_condition(candidate):
        return ""
    return candidate[:1000]


def _parseable_command_for_prompt(command: str, prompt: str) -> str:
    lower_prompt = str(prompt or "").lower()
    lower_command = str(command or "").strip().lower()
    if any(token in lower_prompt for token in ("free ram", "free memory", "available ram", "memory pressure", "ram")):
        if lower_command in {"free", "free -h", "free -m"} or lower_command.startswith("free "):
            return "free -m | awk '/Mem:/ {print \"available_mb=\" $7 \" used_mb=\" $3 \" total_mb=\" $2}'"
    if "gpu" in lower_prompt or "nvidia-smi" in lower_prompt:
        if lower_command == "nvidia-smi" or (lower_command.startswith("nvidia-smi") and "--query-gpu" not in lower_command):
            return "nvidia-smi --query-gpu=memory.free,memory.used,utilization.gpu --format=csv,noheader,nounits"
    if "disk" in lower_prompt and (lower_command == "df -h" or lower_command == "df"):
        return (
            "df -P -k | awk 'NR>1 {print \"filesystem=\" $1 "
            "\" used_percent=\" $5 \" available_kb=\" $4 \" mount=\" $6}'"
        )
    return str(command or "").strip()


def command_from_prompt(prompt: str, *, interval_seconds: int) -> tuple[str, AgentMonitorMode, list[str]]:
    raw = str(prompt or "").strip()
    missing: list[str] = []
    code_command = _first_backticked_command(raw)
    lower = raw.lower()
    if code_command:
        mode: AgentMonitorMode = "raw_stream" if code_command.startswith("watch ") or "tail -f" in code_command else "sample_command"
        return code_command, mode, missing
    if "watch -n" in lower:
        command_match = re.search(r"\bwatch\s+-n\s+\d+(?:\.\d+)?\s+(.+)$", raw, re.IGNORECASE)
        command = (
            f"watch -n {max(1, int(interval_seconds or 1))} "
            f"{command_match.group(1).strip() if command_match else 'nvidia-smi'}"
        )
        return command, "raw_stream", missing
    if "nvidia-smi" in lower or "gpu" in lower:
        command = "nvidia-smi --query-gpu=memory.free,memory.used,utilization.gpu --format=csv,noheader,nounits"
        return command, "sample_command", missing
    if "free ram" in lower or "free memory" in lower or "available ram" in lower or "memory pressure" in lower:
        command = "free -m | awk '/Mem:/ {print \"available_mb=\" $7 \" used_mb=\" $3 \" total_mb=\" $2}'"
        return command, "sample_command", missing
    if "disk" in lower:
        return (
            "df -P -k | awk 'NR>1 {print \"filesystem=\" $1 "
            "\" used_percent=\" $5 \" available_kb=\" $4 \" mount=\" $6}'"
        ), "sample_command", missing
    missing.append("command")
    return "", "sample_command", missing


def _first_command_name(command: str) -> str:
    normalized = str(command or "").strip()
    watch_match = re.match(r"watch\s+-n\s+\d+(?:\.\d+)?\s+(.+)$", normalized, re.IGNORECASE)
    if watch_match:
        normalized = watch_match.group(1).strip()
    normalized = re.split(r"\s*(?:[|;&]|\|\|)\s*", normalized, maxsplit=1)[0].strip()
    try:
        parts = shlex.split(normalized)
    except ValueError:
        parts = normalized.split()
    return (parts[0] if parts else "").rsplit("/", 1)[-1].lower()


def monitor_command_rejection_reason(
    command: str,
    *,
    mode: AgentMonitorMode = "sample_command",
    duration_seconds: int | None = None,
) -> str:
    normalized = str(command or "").strip()
    if not normalized:
        return "Monitor command is empty."
    lower = normalized.lower()
    for pattern in _DESTRUCTIVE_PATTERNS:
        if re.search(pattern, lower):
            return "Monitor command looks destructive."
    command_name = _first_command_name(normalized)
    if command_name in _INTERACTIVE_COMMANDS:
        if command_name in {"mysql", "psql"} and re.search(r"\s-(?:c|e)\s+", lower):
            return ""
        if command_name in {"python", "python3", "node"} and re.search(r"\s-c\s+", lower):
            return ""
        if command_name in {"bash", "fish", "zsh"} and re.search(r"\s-(?:c|lc)\s+", lower):
            return ""
        return "Monitor command looks interactive."
    if mode == "raw_stream" and int(duration_seconds or 0) <= 0:
        return "Raw stream monitors must have a bounded duration."
    return ""


def _bounded_duration(value: Any, *, max_duration_seconds: int) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_MONITOR_DURATION_SECONDS
    if seconds <= 0:
        seconds = DEFAULT_MONITOR_DURATION_SECONDS
    return max(1, min(seconds, max(1, int(max_duration_seconds or DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS))))


def _positive_interval(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 5
    return max(1, seconds)


def _trigger_mode(
    requested: Any,
    *,
    deterministic_condition: str,
    natural_language_condition: str,
) -> AgentMonitorTriggerMode:
    raw = str(requested or "").strip().lower()
    if raw not in {"deterministic", "llm_judged", "hybrid"}:
        raw = "deterministic"
    if deterministic_condition and natural_language_condition:
        return "hybrid"
    if natural_language_condition and not deterministic_condition:
        return "llm_judged"
    return "deterministic" if raw == "llm_judged" and not natural_language_condition else raw  # type: ignore[return-value]


def _schedule_summary(interval_seconds: int, duration_seconds: int) -> str:
    return f"Every {max(1, interval_seconds)}s for {max(1, duration_seconds)}s"


def validate_monitor_draft(
    draft: AgentMonitorDraft,
    *,
    prompt: str = "",
    max_duration_seconds: int = DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS,
    judge_min_interval_seconds: int = DEFAULT_JUDGE_INTERVAL_SECONDS,
) -> AgentMonitorDraft:
    """Normalize and validate one drafted monitor spec."""

    command = _parseable_command_for_prompt(draft.command, prompt)
    duration_seconds = _bounded_duration(draft.duration_seconds, max_duration_seconds=max_duration_seconds)
    interval_seconds = _positive_interval(draft.interval_seconds)
    mode: AgentMonitorMode = draft.mode if draft.mode in {"sample_command", "raw_stream"} else "sample_command"  # type: ignore[assignment]
    if command.startswith("watch ") or "tail -f" in command:
        mode = "raw_stream"
    rejection = monitor_command_rejection_reason(command, mode=mode, duration_seconds=duration_seconds)
    if rejection:
        raise MonitorSpecValidationError(rejection)
    deterministic_condition = _compact(draft.condition)
    natural_condition = _compact(draft.natural_language_condition)
    trigger_mode = _trigger_mode(
        draft.trigger_mode,
        deterministic_condition=deterministic_condition,
        natural_language_condition=natural_condition,
    )
    return AgentMonitorDraft(
        title=_compact(draft.title, limit=160) or _compact(prompt, limit=80) or "Monitor",
        mode=mode,
        command=command,
        interval_seconds=interval_seconds,
        duration_seconds=duration_seconds,
        condition=deterministic_condition,
        natural_language_condition=natural_condition,
        trigger_mode=trigger_mode,
        action_prompt=str(draft.action_prompt or "").strip()[:24000],
        schedule_summary=draft.schedule_summary or _schedule_summary(interval_seconds, duration_seconds),
        confidence=max(0.0, min(float(draft.confidence or 0.0), 1.0)),
        missing_details=[_compact(item, limit=120) for item in draft.missing_details if _compact(item, limit=120)],
        planner_rationale=_compact(draft.planner_rationale, limit=1000),
        risk_notes=_compact(draft.risk_notes, limit=1000),
        judge_interval_seconds=max(int(judge_min_interval_seconds or 1), _positive_interval(draft.judge_interval_seconds)),
    )


def validate_monitor_create(
    payload: AgentMonitorCreate,
    *,
    max_duration_seconds: int = DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS,
    judge_min_interval_seconds: int = DEFAULT_JUDGE_INTERVAL_SECONDS,
) -> AgentMonitorCreate:
    """Normalize and validate one monitor create payload before persistence."""

    draft = validate_monitor_draft(
        AgentMonitorDraft(
            title=payload.title,
            mode=payload.mode,
            command=payload.command,
            interval_seconds=payload.interval_seconds,
            duration_seconds=payload.duration_seconds,
            condition=payload.condition,
            natural_language_condition=payload.natural_language_condition,
            trigger_mode=payload.trigger_mode,
            action_prompt=payload.action_prompt,
            confidence=1.0,
            planner_rationale=payload.planner_rationale,
            risk_notes=payload.risk_notes,
            judge_interval_seconds=payload.judge_interval_seconds,
        ),
        prompt=payload.prompt,
        max_duration_seconds=max_duration_seconds,
        judge_min_interval_seconds=judge_min_interval_seconds,
    )
    return AgentMonitorCreate(
        title=draft.title,
        prompt=payload.prompt,
        mode=draft.mode,
        command=draft.command,
        interval_seconds=draft.interval_seconds,
        duration_seconds=draft.duration_seconds,
        condition=draft.condition,
        natural_language_condition=draft.natural_language_condition,
        trigger_mode=draft.trigger_mode,
        action_prompt=draft.action_prompt,
        planner_rationale=draft.planner_rationale,
        risk_notes=draft.risk_notes,
        judge_interval_seconds=draft.judge_interval_seconds,
        status=payload.status,
        agent_mode=payload.agent_mode,
        conversation_id=payload.conversation_id,
        gateway_id=payload.gateway_id,
        context=payload.context,
    )


def _draft_from_components(
    *,
    prompt: str,
    title: str,
    command: str,
    mode: AgentMonitorMode,
    interval_seconds: int,
    duration_seconds: int,
    condition: str,
    natural_language_condition: str,
    action_prompt: str,
    confidence: float,
    missing_details: list[str],
    rationale: str,
    risk_notes: str = "",
    trigger_mode: AgentMonitorTriggerMode | str = "deterministic",
    judge_min_interval_seconds: int = DEFAULT_JUDGE_INTERVAL_SECONDS,
    max_duration_seconds: int = DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS,
) -> AgentMonitorDraft:
    draft = AgentMonitorDraft(
        title=title,
        mode=mode,
        command=command,
        interval_seconds=interval_seconds,
        duration_seconds=duration_seconds,
        condition=condition,
        natural_language_condition=natural_language_condition,
        trigger_mode=trigger_mode if trigger_mode in {"deterministic", "llm_judged", "hybrid"} else "deterministic",  # type: ignore[arg-type]
        action_prompt=action_prompt,
        schedule_summary=_schedule_summary(interval_seconds, duration_seconds),
        confidence=confidence,
        missing_details=missing_details,
        planner_rationale=rationale,
        risk_notes=risk_notes,
        judge_interval_seconds=judge_min_interval_seconds,
    )
    return validate_monitor_draft(
        draft,
        prompt=prompt,
        max_duration_seconds=max_duration_seconds,
        judge_min_interval_seconds=judge_min_interval_seconds,
    )


def deterministic_monitor_draft_response(
    payload: AgentMonitorDraftRequest,
    *,
    max_duration_seconds: int = DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS,
    judge_min_interval_seconds: int = DEFAULT_JUDGE_INTERVAL_SECONDS,
) -> AgentMonitorDraftResponse:
    """Draft monitor settings from local safe rules only."""

    prompt = str(payload.prompt or "").strip()
    lower = prompt.lower()
    forced = lower.startswith("/monitor")
    if not forced and not _monitor_intent_detected(prompt):
        return AgentMonitorDraftResponse(is_monitor_request=False, rationale="No monitor intent detected.")
    normalized_prompt = re.sub(r"^/monitor\s+", "", prompt, flags=re.IGNORECASE).strip() or prompt
    interval_seconds = interval_seconds_from_prompt(normalized_prompt)
    duration_seconds = duration_seconds_from_prompt(normalized_prompt)
    command, mode, missing = command_from_prompt(normalized_prompt, interval_seconds=interval_seconds)
    condition = condition_from_prompt(normalized_prompt)
    natural_condition = natural_language_condition_from_prompt(
        normalized_prompt,
        deterministic_condition=condition,
    )
    trigger_mode: AgentMonitorTriggerMode = "deterministic"
    if natural_condition and condition:
        trigger_mode = "hybrid"
    elif natural_condition:
        trigger_mode = "llm_judged"
    title = " ".join(normalized_prompt.split())[:80] or "Monitor"
    action_prompt = ""
    if condition or natural_condition:
        action_prompt = f"Review this monitor trigger and tell the user what happened: {normalized_prompt}"
    try:
        draft = _draft_from_components(
            prompt=normalized_prompt,
            title=title,
            command=command,
            mode=mode,
            interval_seconds=interval_seconds,
            duration_seconds=duration_seconds,
            condition=condition,
            natural_language_condition=natural_condition,
            trigger_mode=trigger_mode,
            action_prompt=action_prompt,
            confidence=0.72 if command else 0.35,
            missing_details=missing,
            rationale="Drafted monitor settings from local rules.",
            judge_min_interval_seconds=judge_min_interval_seconds,
            max_duration_seconds=max_duration_seconds,
        )
        drafts = [draft]
    except MonitorSpecValidationError as exc:
        missing = sorted({*missing, "safe_command"})
        drafts = [
            AgentMonitorDraft(
                title=title,
                command="",
                interval_seconds=interval_seconds,
                duration_seconds=min(duration_seconds, max_duration_seconds),
                condition=condition,
                natural_language_condition=natural_condition,
                trigger_mode=trigger_mode,
                action_prompt=action_prompt,
                confidence=0.0,
                missing_details=missing,
                planner_rationale="Local monitor rules rejected the proposed command.",
                risk_notes=str(exc),
                judge_interval_seconds=judge_min_interval_seconds,
            )
        ]
    return AgentMonitorDraftResponse(
        is_monitor_request=True,
        drafts=drafts,
        missing_details=missing,
        rationale="Drafted monitor settings from local rules.",
    )


def _planner_prompt(
    request: AgentMonitorDraftRequest,
    *,
    max_duration_seconds: int,
    judge_min_interval_seconds: int,
) -> str:
    return "\n".join(
        [
            *prompt_lines(
                "monitors.plan",
                {
                    "default_duration_seconds": DEFAULT_MONITOR_DURATION_SECONDS,
                    "max_duration_seconds": max_duration_seconds,
                    "judge_min_interval_seconds": judge_min_interval_seconds,
                },
            ),
            "AgentMonitorPlan schema:",
            json.dumps(AgentMonitorPlan.model_json_schema(), default=str, ensure_ascii=True),
            "User prompt:",
            request.prompt,
            "Request context:",
            json.dumps(request.context, default=str, ensure_ascii=True),
            "Agent mode:",
            request.agent_mode,
            "LLM model:",
            request.llm_model or "",
        ]
    )


def _draft_from_llm_plan(
    plan: AgentMonitorPlan,
    *,
    prompt: str,
    max_duration_seconds: int,
    judge_min_interval_seconds: int,
) -> AgentMonitorDraft | None:
    if not plan.is_monitor_request:
        return None
    command = str(plan.command or "").strip()
    if not command:
        return None
    deterministic_condition = _compact(plan.deterministic_condition)
    natural_condition = _compact(plan.natural_language_condition)
    try:
        return _draft_from_components(
            prompt=prompt,
            title=plan.title or plan.monitor_intent or prompt,
            command=command,
            mode=plan.mode,
            interval_seconds=_positive_interval(plan.interval_seconds),
            duration_seconds=_bounded_duration(plan.duration_seconds, max_duration_seconds=max_duration_seconds),
            condition=deterministic_condition,
            natural_language_condition=natural_condition,
            trigger_mode=plan.trigger_mode,
            action_prompt=str(plan.action_prompt or "").strip(),
            confidence=float(plan.confidence or 0.0),
            missing_details=plan.missing_details,
            rationale=plan.rationale,
            risk_notes=plan.risk_notes,
            judge_min_interval_seconds=judge_min_interval_seconds,
            max_duration_seconds=max_duration_seconds,
        )
    except MonitorSpecValidationError:
        return None


def llm_monitor_plan(
    payload: AgentMonitorDraftRequest,
    *,
    llm_client: Any,
    max_duration_seconds: int = DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS,
    judge_min_interval_seconds: int = DEFAULT_JUDGE_INTERVAL_SECONDS,
) -> AgentMonitorPlan:
    """Ask an LLM client for one structured monitor plan."""

    complete_json = getattr(llm_client, "complete_json", None)
    if not callable(complete_json):
        raise RuntimeError("LLM client is unavailable for monitor planning.")
    raw = complete_json(
        _planner_prompt(
            payload,
            max_duration_seconds=max_duration_seconds,
            judge_min_interval_seconds=judge_min_interval_seconds,
        ),
        AgentMonitorPlan.model_json_schema(),
    )
    return AgentMonitorPlan.model_validate(raw)


def draft_monitor_from_prompt(
    payload: AgentMonitorDraftRequest | dict[str, Any],
    *,
    llm_client: Any | None = None,
    planner_enabled: bool = True,
    max_duration_seconds: int = DEFAULT_MAX_INFERRED_MONITOR_DURATION_SECONDS,
    judge_min_interval_seconds: int = DEFAULT_JUDGE_INTERVAL_SECONDS,
) -> AgentMonitorDraftResponse:
    request = (
        payload
        if isinstance(payload, AgentMonitorDraftRequest)
        else AgentMonitorDraftRequest.model_validate(payload)
    )
    prompt = str(request.prompt or "").strip()
    forced_or_intent = _monitor_intent_detected(prompt)
    if not forced_or_intent:
        return AgentMonitorDraftResponse(is_monitor_request=False, rationale="No monitor intent detected.")

    normalized_prompt = re.sub(r"^/monitor\s+", "", prompt, flags=re.IGNORECASE).strip() or prompt
    if _first_backticked_command(normalized_prompt):
        return deterministic_monitor_draft_response(
            AgentMonitorDraftRequest(
                prompt=normalized_prompt,
                context=request.context,
                agent_mode=request.agent_mode,
                llm_model=request.llm_model,
            ),
            max_duration_seconds=max_duration_seconds,
            judge_min_interval_seconds=judge_min_interval_seconds,
        )

    if planner_enabled and llm_client is not None:
        try:
            plan = llm_monitor_plan(
                AgentMonitorDraftRequest(
                    prompt=normalized_prompt,
                    context=request.context,
                    agent_mode=request.agent_mode,
                    llm_model=request.llm_model,
                ),
                llm_client=llm_client,
                max_duration_seconds=max_duration_seconds,
                judge_min_interval_seconds=judge_min_interval_seconds,
            )
            draft = _draft_from_llm_plan(
                plan,
                prompt=normalized_prompt,
                max_duration_seconds=max_duration_seconds,
                judge_min_interval_seconds=judge_min_interval_seconds,
            )
            if draft is not None:
                return AgentMonitorDraftResponse(
                    is_monitor_request=True,
                    drafts=[draft],
                    missing_details=draft.missing_details,
                    rationale=plan.rationale or "Drafted monitor settings with the LLM planner.",
                )
        except (RuntimeError, ValidationError, ValueError):
            pass
        except Exception:
            pass

    return deterministic_monitor_draft_response(
        AgentMonitorDraftRequest(
            prompt=normalized_prompt,
            context=request.context,
            agent_mode=request.agent_mode,
            llm_model=request.llm_model,
        ),
        max_duration_seconds=max_duration_seconds,
        judge_min_interval_seconds=judge_min_interval_seconds,
    )
