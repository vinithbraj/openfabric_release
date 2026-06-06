"""Scheduled event and reminder drafting helpers for Agent UI."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.common import *
from agent_runtime.api.agent_ui_support.directory import *
from agent_runtime.api.agent_ui_support.models import *
from agent_runtime.api.agent_ui_support.prompt_context import *
from agent_runtime.api.agent_ui_support.memory import *

_EVENT_DATE_VALUE_PATTERN = (
    r"(?:\d{4}[/-]\d{1,2}[/-]\d{1,2})"
    r"|(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    r"|(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?(?:\s+\d{4})?)"
    r"|(?:\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:,)?(?:\s+\d{4})?)"
)
_EVENT_TIME_VALUE_PATTERN = (
    r"(?:\d{1,2}(?:(?::|\.)\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|noon|midnight)"
)
_EVENT_ABSOLUTE_DATE_RE = re.compile(
    rf"\b(?:on|for|by|at)\s+(?P<date>{_EVENT_DATE_VALUE_PATTERN})",
    re.IGNORECASE,
)
def _event_timezone_name(
    payload: AgentEventDraftRequest | None = None,
    settings: Settings | None = None,
) -> str:
    context = dict(getattr(payload, "context", {}) or {})
    for key in ("timezone", "browser_timezone", "runtime_timezone"):
        value = str(context.get(key) or "").strip()
        if value:
            return value
    value = str(getattr(settings, "runtime_timezone", "") or "").strip()
    return value or "UTC"


def _event_zoneinfo(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(timezone_name or "UTC").strip() or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _event_absolute_phrase(next_run_at: str, timezone_name: str) -> str:
    timestamp = parse_utc_iso(next_run_at)
    if timestamp is None:
        return ""
    display_timezone = str(timezone_name or "UTC").strip() or "UTC"
    zone = _event_zoneinfo(display_timezone)
    local = timestamp.astimezone(zone)
    hour = local.hour % 12 or 12
    minute = f"{local.minute:02d}"
    ampm = "AM" if local.hour < 12 else "PM"
    return f"On {local.strftime('%B')} {local.day}, {local.year} at {hour}:{minute} {ampm} {display_timezone}"

def _event_title_from_prompt(prompt: str) -> str:
    """Return a compact event title."""

    text = " ".join(str(prompt or "").split()).strip()
    for prefix in ("please ", "can you ", "could you "):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    return text[:80] or "Scheduled task"


def _event_interval_phrase(seconds: int, *, schedule_type: str = "interval") -> str:
    """Return a readable interval summary."""

    seconds = max(1 if schedule_type == "once" else 60, int(seconds or 3600))
    if schedule_type == "once" and seconds < 60:
        value = seconds
        unit = "second" if value == 1 else "seconds"
    elif seconds % 86400 == 0:
        value = seconds // 86400
        unit = "day" if value == 1 else "days"
    elif seconds % 3600 == 0:
        value = seconds // 3600
        unit = "hour" if value == 1 else "hours"
    else:
        value = max(1, seconds // 60)
        unit = "minute" if value == 1 else "minutes"
    return f"After {value} {unit}" if schedule_type == "once" else f"Every {value} {unit}"


def _looks_like_notification_event_prompt(prompt: str) -> bool:
    text = str(prompt or "").lower()
    return bool(
        re.search(r"\b(remind(?:er)?|rmeind|timer|notify|notification|alert|todo|to\s+do)\b", text)
    )


def _looks_like_explicit_reminder_schedule(prompt: str) -> bool:
    text = str(prompt or "").lower()
    if _EVENT_ABSOLUTE_DATE_RE.search(text):
        return True
    if re.search(
        r"\b(?:today|tomorrow|tonight|"
        r"this\s+(?:morning|afternoon|evening|week|weekend|month|year)|"
        r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|weekend|month|year))\b",
        text,
    ):
        return True
    if re.search(r"\b(?:every|each)\s+(?:(?:\d+)\s+)?(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", text):
        return True
    if re.search(r"\b(?:after|in)\s+\d+\s+(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", text):
        return True
    if re.search(rf"\b(?:at|by|around)\s+{_EVENT_TIME_VALUE_PATTERN}\b", text, re.IGNORECASE):
        return True
    return False


def _looks_like_unscheduled_reminder_prompt(prompt: str) -> bool:
    return (
        _looks_like_notification_event_prompt(prompt)
        and not _looks_like_explicit_reminder_schedule(prompt)
    )


_EVENT_ACTIONABLE_PROMPT_RE = re.compile(
    r"\b("
    r"run|do|execute|check|list|find|search|inspect|verify|stage|commit|push|pull|merge|"
    r"rebase|checkout|switch|install|uninstall|create|delete|remove|update|edit|write|"
    r"copy|move|sync|transfer|start|stop|restart|build|test|deploy|fix|format|lint|"
    r"open|close|download|upload|send|email|message|summarize|analyze|compare|"
    r"generate|draft|refactor|query|fetch|clone|"
    r"git|docker|conda|kubectl|systemctl|service|rsync|scp|ssh|curl|wget|"
    r"python|pytest|npm|pnpm|yarn|pip|uv|brew|apt|apt-get|make|cmake"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_actionable_event_prompt(prompt: str) -> bool:
    """Return whether delayed text looks like work the agent should execute."""

    return bool(_EVENT_ACTIONABLE_PROMPT_RE.search(str(prompt or "")))


def _event_action_hint_from_context(context: dict[str, Any] | None) -> Literal["agent_prompt", "notification", ""]:
    raw_hint = str(dict(context or {}).get(SCHEDULED_EVENT_EXTRACTION_HINT_CONTEXT_KEY) or "").strip().lower()
    if raw_hint in {"agent_prompt", "notification"}:
        return raw_hint  # type: ignore[return-value]
    summaries = dict(context or {}).get(USER_MACRO_SUMMARY_CONTEXT_KEY)
    if not isinstance(summaries, list):
        return ""
    for item in summaries:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("event_action_type") or "").strip().lower()
        if action_type in {"agent_prompt", "notification"}:
            return action_type  # type: ignore[return-value]
        kind = str(item.get("kind") or "").strip().lower()
        if kind == RUNLATER_MACRO_KIND:
            return "agent_prompt"
        if kind in {REMIND_MACRO_KIND, TODO_MACRO_KIND}:
            return "notification"
    return ""


def _event_action_type_for_task(
    task_text: str,
    *,
    original_prompt: str,
    context: dict[str, Any] | None,
) -> Literal["agent_prompt", "notification"]:
    hint = _event_action_hint_from_context(context)
    if hint in {"agent_prompt", "notification"}:
        return hint
    if _looks_like_notification_event_prompt(original_prompt):
        return "notification"
    if _looks_like_actionable_event_prompt(task_text):
        return "agent_prompt"
    return "notification"


def _event_should_run_as_notification(event: AgentEventRecord) -> bool:
    """Return whether a stored event should complete as a notification-only reminder."""

    if event.event_kind == "todo" or event.action_type == "notification":
        return True
    if event.action_type == "agent_prompt":
        return False
    prompt = str(event.prompt or event.notification_message or event.title).strip()
    inferred = _event_action_type_for_task(
        prompt,
        original_prompt=prompt,
        context=event.context,
    )
    return inferred == "notification"


def _event_notification_message(prompt: str) -> str:
    """Return reminder text without the scheduling/request chrome."""

    text = " ".join(str(prompt or "").split()).strip(" ,.;:")
    text = re.sub(r"^\s*(?:please\s+)?", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^\s*(?:set\s+(?:a\s+)?)?(?:reminder|timer|notification|alert)\s*(?:for|to|that|with)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^\s*(?:(?:add|save\s+as)\s+(?:a\s+)?)?"
        r"(?:todo|to\s+do|task\s+reminder)\s*(?:for|to|that|with|about)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^\s*(?:remind|rmeind)\s+me\s*(?:to|that|for|with(?:\s+this)?(?:\s+message)?|about)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^\s*(?:notify|alert)\s+me\s*(?:to|that|with(?:\s+this)?(?:\s+message)?|about)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^\s*to\s+", "", text, flags=re.IGNORECASE)
    return text.strip(" ,.;:") or "Reminder"


def _event_minimum_interval_seconds(schedule_type: str, action_type: str) -> int:
    if schedule_type == "once" and action_type == "notification":
        return 1
    return 60


def _normalize_event_draft_response(
    draft_response: AgentEventDraftResponse,
    *,
    payload: AgentEventDraftRequest,
) -> AgentEventDraftResponse:
    """Normalize typed LLM drafts before exposing or persisting them."""

    if not draft_response.is_schedule_request:
        return draft_response
    normalized: list[AgentEventDraft] = []
    payload_context = dict(payload.context or {})
    payload_auto_approve = payload_context.get("auto_approve_commands") is True
    action_hint = _event_action_hint_from_context(payload.context)
    has_explicit_schedule = _looks_like_explicit_reminder_schedule(payload.prompt)
    looks_like_todo = _looks_like_unscheduled_reminder_prompt(payload.prompt) or (
        action_hint == "notification" and not has_explicit_schedule
    )
    for draft in draft_response.drafts:
        context = {**payload_context, **dict(draft.context or {})}
        for gateway_key in ("gateway_id", "gateway_node", "gateway_url", "gateway_endpoints"):
            if gateway_key in payload_context:
                context[gateway_key] = payload_context[gateway_key]
        for control_key in ("auto_approve_commands", "auto_approve_scope"):
            if control_key in payload_context:
                context[control_key] = payload_context[control_key]
        context.setdefault("agent_mode", payload.agent_mode)
        if payload.llm_model:
            context.setdefault("llm_model", payload.llm_model)
        task_text = " ".join(
            part
            for part in (
                str(payload.prompt or "").strip(),
                str(draft.prompt or "").strip(),
            )
            if part
        )
        action_type = _event_action_type_for_task(
            task_text,
            original_prompt=payload.prompt,
            context=context,
        )
        if looks_like_todo:
            action_type = "notification"
        minimum_interval = _event_minimum_interval_seconds(draft.schedule_type, action_type)
        interval_seconds = max(minimum_interval, int(draft.interval_seconds or 3600))
        next_run_at = str(draft.next_run_at or "").strip()
        schedule_summary = str(draft.schedule_summary or "").strip()
        event_kind = str(draft.event_kind or "scheduled").strip() or "scheduled"
        if looks_like_todo and action_type == "notification":
            event_kind = "todo"
            next_run_at = ""
            schedule_summary = ""
        elif event_kind == "todo" and (has_explicit_schedule or next_run_at):
            event_kind = "scheduled"
        elif not schedule_summary and draft.schedule_type == "once" and next_run_at:
            schedule_summary = _event_absolute_phrase(next_run_at, draft.timezone or "UTC")
        if not schedule_summary:
            schedule_summary = _event_interval_phrase(
                interval_seconds,
                schedule_type=draft.schedule_type,
            )
        if event_kind == "todo":
            schedule_summary = ""
        if next_run_at and schedule_summary:
            context.setdefault(SCHEDULED_EVENT_ABSOLUTE_AT_CONTEXT_KEY, next_run_at)
            context.setdefault(SCHEDULED_EVENT_SCHEDULE_SUMMARY_CONTEXT_KEY, schedule_summary)
        notification_message = draft.notification_message if action_type == "notification" else ""
        if action_type == "notification" and not str(notification_message or "").strip():
            notification_message = draft.prompt
        normalized.append(
            draft.model_copy(
                update={
                    "context": context,
                    "action_type": action_type,
                    "interval_seconds": interval_seconds,
                    "next_run_at": next_run_at,
                    "schedule_summary": schedule_summary,
                    "event_kind": event_kind,
                    "notification_message": notification_message,
                    "auto_approve_confirmations": True
                    if payload_auto_approve
                    else draft.auto_approve_confirmations,
                }
            )
        )
    return draft_response.model_copy(update={"drafts": normalized})


def _event_draft_prompt(payload: AgentEventDraftRequest, settings: Settings) -> str:
    """Build the LLM prompt for extracting scheduled events."""

    return "\n".join(
        [
            *prompt_lines("events.draft"),
            "AgentEventDraftResponse schema:",
            json.dumps(AgentEventDraftResponse.model_json_schema(), default=str),
            "Current UTC time:",
            utc_now_iso(),
            "Runtime timezone setting:",
            settings.runtime_timezone or "UTC",
            "User prompt:",
            payload.prompt,
            "Request context:",
            json.dumps(payload.context, default=str, ensure_ascii=True),
            "Agent mode:",
            payload.agent_mode,
            "LLM model:",
            payload.llm_model or "",
        ]
    )


def _draft_agent_events(
    *,
    settings: Settings,
    agent_runtime: Any,
    payload: AgentEventDraftRequest,
) -> AgentEventDraftResponse:
    """Return an event draft using the typed LLM extractor as the recognition authority."""

    complete_json = getattr(getattr(agent_runtime, "llm_client", None), "complete_json", None)
    if not callable(complete_json):
        return AgentEventDraftResponse(
            is_schedule_request=False,
            rationale="Event recognition LLM is unavailable.",
        )
    try:
        raw = complete_json(_event_draft_prompt(payload, settings), AgentEventDraftResponse.model_json_schema())
        drafted = AgentEventDraftResponse.model_validate(raw)
        return _normalize_event_draft_response(drafted, payload=payload)
    except Exception:
        return AgentEventDraftResponse(
            is_schedule_request=False,
            rationale="Event recognition LLM failed.",
        )

__all__ = [name for name in globals() if not name.startswith("__")]
