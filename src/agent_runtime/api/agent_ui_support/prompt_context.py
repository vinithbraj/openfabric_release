"""Prompt macro, literal payload, parameter, and shortcut helpers for Agent UI."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.common import *
from agent_runtime.api.agent_ui_support.directory import *
from agent_runtime.api.agent_ui_support.memory_utils import *
from agent_runtime.api.agent_ui_support.models import *

def json_dumps(payload: Any) -> str:
    """Serialize one API or SSE payload."""

    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return json.dumps(payload, default=str, separators=(",", ":"))


def _prepare_literal_payload_prompt(prompt: str) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Return a decomposition-safe prompt plus private literal payload context."""

    parsed = extract_literal_payloads(prompt)
    if not parsed.has_payloads:
        return str(prompt or ""), {}, None
    context = {
        OPERATOR_LITERAL_PAYLOADS_CONTEXT_KEY: [dict(item) for item in parsed.payloads],
    }
    event_detail = {
        "payload_count": len(parsed.payloads),
        "payloads": [
            {
                "payload_id": str(item.get("payload_id") or ""),
                "kind": str(item.get("kind") or ""),
                "label": str(item.get("label") or ""),
                "input_name": str(item.get("input_name") or ""),
                "placeholder": str(item.get("placeholder") or ""),
                "value_length": int(item.get("value_length") or 0),
                "preview": str(item.get("preview") or "")[:240],
            }
            for item in parsed.payloads
        ],
        "sanitized_prompt_preview": parsed.sanitized_prompt[:240],
    }
    return parsed.sanitized_prompt, context, event_detail


def _prepare_user_macro_prompt(
    prompt: str,
    *,
    preserve_checkonline_hint: bool = False,
    parameter_store: AgentParameterStore | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Return a macro-sanitized prompt plus private/public request context."""

    try:
        parsed = parse_user_macros(
            prompt,
            preserve_checkonline_hint=preserve_checkonline_hint,
            parameter_store=parameter_store,
        )
    except UserMacroParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not parsed.has_macros:
        return str(prompt or ""), {}, None
    context = {
        USER_MACRO_PRIVATE_CONTEXT_KEY: [dict(item) for item in parsed.private_macros],
        USER_MACRO_SUMMARY_CONTEXT_KEY: [dict(item) for item in parsed.public_summaries],
    }
    if any(str(item.get("kind") or "") == AUTOAPPROVE_MACRO_KIND for item in parsed.public_summaries):
        context["auto_approve_commands"] = True
        context["auto_approve_scope"] = "request"
    event_detail = {
        "macro_count": len(parsed.public_summaries),
        "macros": [dict(item) for item in parsed.public_summaries],
        "sanitized_prompt_preview": parsed.sanitized_prompt[:240],
    }
    return parsed.sanitized_prompt, context, event_detail


_ADD_TO_MEMORY_SHORTCUT_RE = re.compile(
    r"^\s*(?:/\s*add\s*to\s*memory|/\s*addtomemory|slash\s+add\s+to\s+memory|slash\s+addtomemory)\b"
    r"[\s:;-]*",
    re.IGNORECASE,
)


def _add_to_memory_shortcut_payload(prompt: str) -> str | None:
    """Return text following the /addtomemory shortcut, or None when absent."""

    match = _ADD_TO_MEMORY_SHORTCUT_RE.match(str(prompt or ""))
    if match is None:
        return None
    return str(prompt or "")[match.end() :].strip()


def _relationship_edges_from_memory_text(text: str) -> list[tuple[str, str]]:
    """Extract simple source -> target relationship edges from pasted note text."""

    edges: list[tuple[str, str]] = []
    current_source = ""
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith(":") and "->" not in line:
            continue
        if "->" in line:
            left, right = line.split("->", 1)
            source = left.strip() or current_source
            target = right.strip()
            if source and target:
                edges.append((source, target))
            if left.strip():
                current_source = left.strip()
            continue
        current_source = line
    return edges


def _compact_relationship_map(text: str, *, max_chars: int = 1800) -> str:
    """Return a compact exact relationship map suitable for memory instructions."""

    grouped: OrderedDict[str, list[str]] = OrderedDict()
    for source, target in _relationship_edges_from_memory_text(text):
        targets = grouped.setdefault(source, [])
        if target not in targets:
            targets.append(target)
    if not grouped:
        return ""
    parts = [
        f"{source} -> {', '.join(targets)}"
        for source, targets in grouped.items()
    ]
    compact = "; ".join(parts)
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 18)].rstrip(" ;,") + "; [truncated]"


def _add_to_memory_tags(text: str) -> list[str]:
    """Infer durable retrieval tags for explicit add-to-memory text."""

    lowered = str(text or "").lower()
    tags: list[str] = ["user_memory"]
    marker_tags = (
        (
            "dicom",
            (
                "dicom",
                "sopinstanceuid",
                "studyinstanceuid",
                "seriesinstanceuid",
                "rtplan",
                "rtstruct",
            ),
        ),
        ("sql", ("sql", "join", "database", "table", "resources.", "orthanc")),
        ("relationship_map", ("->", "relationship", "relationships", "join")),
        ("orthanc", ("orthanc", "resources.internalid", "resources.parent")),
        ("rtplan", ("rtplan", "beamsequence")),
        ("rtstruct", ("rtstruct", "roicontour", "structuresetroi")),
        ("rtdose", ("rtdose",)),
        ("rtrecord", ("rtrecord", "treatmentsessionbeamsequence")),
        ("registration", ("registrationsequence", "matrixregistrationsequence", "reg.")),
        ("roi", ("roi", "roinumber")),
        ("patient", ("patientid", "patient.patientid")),
        ("study", ("studyinstanceuid", "study.studyinstanceuid")),
        ("series", ("seriesinstanceuid", "series.seriesinstanceuid")),
    )
    for tag, markers in marker_tags:
        if any(marker in lowered for marker in markers):
            tags.append(tag)
    return _memory_tags(tags)


def _add_to_memory_summary(text: str, tags: list[str]) -> str:
    """Build a concise deterministic digest for explicit memory text."""

    if {"dicom", "sql"} <= set(tags):
        categories = []
        category_markers = (
            (
                "patient/study/series/instance",
                ("patient.patientid", "studyinstanceuid", "seriesinstanceuid"),
            ),
            ("RTPlan objects", ("rtplan.sopinstanceuid", "beamsequence.sopinstanceuid")),
            ("beam planning", ("controlpointsequence", "beamsequenceid")),
            ("fraction/beam dose", ("fractiongroupsequence", "referencedbeamsequence")),
            ("RTSTRUCT/ROI", ("rtstruct", "roicontour", "roinumber")),
            ("RTDOSE", ("rtdose",)),
            ("treatment record", ("rtrecord", "treatmentsessionbeamsequence")),
            ("REG", ("registrationsequence", "matrixregistrationsequence")),
            ("Orthanc hierarchy", ("resources.internalid", "resources.parent")),
        )
        lowered = text.lower()
        for label, markers in category_markers:
            if any(marker in lowered for marker in markers):
                categories.append(label)
        suffix = ", ".join(categories[:9]) if categories else "DICOM and SQL joins"
        return f"DICOM/SQL relationship map covering {suffix}."
    compact = " ".join(str(text or "").split())
    if len(compact) <= 160:
        return compact
    return compact[:157].rstrip() + "..."


def _fallback_add_to_memory_draft_payload(text: str, settings: Settings) -> dict[str, Any]:
    """Create one categorized memory entry when LLM digestion is unavailable."""

    source_text = str(text or "").strip()
    tags = _add_to_memory_tags(source_text)
    summary = _add_to_memory_summary(source_text, tags)
    relationship_map = _compact_relationship_map(source_text)
    model_name = _active_agent_model_for_memory(settings)
    if relationship_map:
        instruction = (
            "Remember this user-provided relationship map for future planning, SQL querying, "
            "DICOM data modeling, and join selection. Prefer these exact relationships when "
            f"the relevant entities are present: {relationship_map}\n\n"
            "Source memory text:\n"
            f"{source_text}"
        )
    else:
        instruction = (
            "Remember this user-provided guidance for future relevant requests:\n"
            f"{source_text}"
        )
    return {
        "instruction": instruction.strip(),
        "summary": summary,
        "memory_kind": "task_memory",
        "scope": "global",
        "model_name": model_name,
        "model_family": normalize_model_family(model_name),
        "task_type": "",
        "tool_type": "",
        "intent_type": "",
        "validator_error_type": "",
        "safe_examples": [],
        "blocked_examples": [],
        "tags": tags,
        "rationale": (
            "Created from an explicit /addtomemory shortcut with deterministic categorization."
        ),
    }


def _add_to_memory_shortcut_prompt(text: str, settings: Settings) -> str:
    """Build an LLM prompt that digests explicit /addtomemory text."""

    model_name = _active_agent_model_for_memory(settings)
    return "\n".join(
        [
            *prompt_lines("memory.feedback"),
            (
                "The user explicitly invoked /addtomemory. Convert the supplied text into "
                "active memory entries to apply immediately."
            ),
            "Create durable guidance only; do not create proposals.",
            (
                "Categorize each create draft for targeted retrieval using memory_kind, scope, "
                "task_type, tool_type, intent_type, and tags."
            ),
            (
                "For cross-domain reference facts or relationship maps, prefer rich domain tags "
                "and leave task_type/tool_type/intent_type blank when a narrow structured value "
                "would make retrieval miss related prompts."
            ),
            (
                "For SQL, DICOM, Orthanc, or schema relationship maps, preserve every exact "
                "source and target field in the instruction. Do not omit join keys."
            ),
            (
                "Put a concise digest first, then include the authoritative source text or "
                "exact mapping block so future prompts have the full context."
            ),
            (
                "Use lowercase stable slugs for tags such as sql, dicom, orthanc, "
                "relationship_map, rtplan, rtstruct, rtdose, rtrecord, registration, roi."
            ),
            "MemoryDraftResponse schema:",
            json.dumps(MemoryDraftResponse.model_json_schema(), default=str),
            "Current model:",
            model_name,
            "Model family:",
            normalize_model_family(model_name),
            "Text to remember:",
            text,
        ]
    )


def _normalize_add_to_memory_draft_payload(
    draft: Any,
    *,
    source_text: str,
    settings: Settings,
) -> dict[str, Any]:
    """Normalize one /addtomemory draft into a MemoryEntryCreate payload."""

    draft_payload = draft.model_dump(
        mode="json",
        exclude={"proposal_type", "memory_id", "confidence"},
    )
    model_name = str(
        draft_payload.get("model_name") or _active_agent_model_for_memory(settings) or ""
    ).strip()
    model_family = str(
        draft_payload.get("model_family") or normalize_model_family(model_name)
    ).strip()
    tags = _memory_tags(draft_payload.get("tags"))
    fallback_tags = _add_to_memory_tags(source_text)
    for tag in fallback_tags:
        if tag not in tags:
            tags.append(tag)
    is_cross_domain_map = {"dicom", "sql"} <= set(tags) or "relationship_map" in tags
    task_type = _memory_slug(draft_payload.get("task_type"))
    tool_type = _memory_slug(draft_payload.get("tool_type"))
    intent_type = _memory_slug(draft_payload.get("intent_type"))
    if is_cross_domain_map:
        task_type = ""
        tool_type = ""
        intent_type = ""
    instruction = str(draft_payload.get("instruction") or "").strip()
    if not instruction:
        instruction = _fallback_add_to_memory_draft_payload(source_text, settings)["instruction"]
    source_text_clean = str(source_text or "").strip()
    if source_text_clean and source_text_clean not in instruction:
        instruction = f"{instruction}\n\nSource memory text:\n{source_text_clean}".strip()
    summary = str(draft_payload.get("summary") or "").strip() or _add_to_memory_summary(
        source_text,
        tags,
    )
    return {
        "instruction": instruction,
        "summary": summary,
        "status": "active",
        "memory_kind": _memory_kind(draft_payload.get("memory_kind"), fallback="task_memory"),
        "scope": "global",
        "model_name": model_name,
        "model_family": model_family,
        "task_type": task_type,
        "tool_type": tool_type,
        "intent_type": intent_type,
        "validator_error_type": _memory_slug(draft_payload.get("validator_error_type")),
        "safe_examples": _memory_examples(draft_payload.get("safe_examples")),
        "blocked_examples": _memory_examples(draft_payload.get("blocked_examples")),
        "tags": tags[:20],
        "provenance": "manual",
        "request_id": "",
        "rationale": str(draft_payload.get("rationale") or "Created from /addtomemory.").strip(),
    }


def _add_to_memory_shortcut_entries(
    *,
    memory_store: AgentMemoryStore,
    source_text: str,
    settings: Settings,
    agent_runtime: Any,
) -> tuple[list[MemoryEntry], str]:
    """Digest explicit /addtomemory text and commit active memory entries."""

    response = MemoryDraftResponse()
    complete_json = getattr(getattr(agent_runtime, "llm_client", None), "complete_json", None)
    if callable(complete_json):
        try:
            from agent_runtime.api.agent_ui_support.memory import _call_memory_feedback_llm

            raw = _call_memory_feedback_llm(
                complete_json,
                _add_to_memory_shortcut_prompt(source_text, settings),
                MemoryDraftResponse.model_json_schema(),
                settings,
            )
            response = MemoryDraftResponse.model_validate(raw)
        except Exception:
            response = MemoryDraftResponse(
                rationale="LLM /addtomemory digestion failed; using deterministic fallback."
            )
    payloads: list[dict[str, Any]] = []
    for draft in response.drafts:
        if draft.proposal_type != "create":
            continue
        payload = _normalize_add_to_memory_draft_payload(
            draft,
            source_text=source_text,
            settings=settings,
        )
        if payload["instruction"]:
            payloads.append(payload)
    if not payloads:
        payloads.append(_fallback_add_to_memory_draft_payload(source_text, settings))
    entries = [
        memory_store.create_entry(
            MemoryEntryCreate.model_validate(payload),
            actor=ADDTOMEMORY_MACRO_KIND,
        )
        for payload in payloads
    ]
    return entries, response.rationale


def _online_lookup_trace_detail(
    lookup_payload: dict[str, Any],
    *,
    task_id: str | None = None,
    step_index: int | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    detail = {
        "provider": str(lookup_payload.get("provider") or COMBINED_ONLINE_LOOKUP_PROVIDER),
        "query": str(lookup_payload.get("query") or "")[:500],
        "available": bool(lookup_payload.get("available")),
        "source_title": str(lookup_payload.get("source_title") or "")[:240],
        "source_url": str(lookup_payload.get("source_url") or "")[:2000],
        "fetched_at": str(lookup_payload.get("fetched_at") or ""),
        "error": str(lookup_payload.get("error") or "")[:300],
        "answer_preview": str(lookup_payload.get("answer_text") or "")[:500],
    }
    provider_results = lookup_payload.get("provider_results")
    if isinstance(provider_results, list):
        detail["provider_results"] = provider_results[:4]
    if task_id:
        detail["task_id"] = task_id
    step_id = str(lookup_payload.get("streaming_step_id") or "").strip()
    if step_id:
        detail["streaming_step_id"] = step_id
    if step_index is not None:
        detail["streaming_step_index"] = step_index
    if source:
        detail["source"] = source
    return detail


def _memory_store_or_error(settings: Settings, agent_runtime: Any) -> AgentMemoryStore:
    """Return the configured persistent memory store or raise an HTTP error."""

    if not bool(settings.agent_memory_enabled):
        raise HTTPException(status_code=404, detail="Agent memory is disabled.")
    store = getattr(agent_runtime, "memory_store", None)
    if isinstance(store, AgentMemoryStore):
        return store
    store = AgentMemoryStore(settings.agent_memory_db_path)
    try:
        setattr(agent_runtime, "memory_store", store)
    except Exception:
        pass
    return store


def _memory_store_for_learning(settings: Settings, agent_runtime: Any) -> AgentMemoryStore | None:
    """Return memory storage for learning auto-approval when memory is available."""

    if not bool(settings.agent_memory_enabled):
        return None
    store = getattr(agent_runtime, "memory_store", None)
    if isinstance(store, AgentMemoryStore):
        return store
    store = AgentMemoryStore(settings.agent_memory_db_path)
    try:
        setattr(agent_runtime, "memory_store", store)
    except Exception:
        pass
    return store


def _parameter_store_or_error(settings: Settings, agent_runtime: Any) -> AgentParameterStore:
    """Return the configured local parameter store or raise an HTTP error."""

    if not bool(getattr(settings, "agent_parameter_store_enabled", True)):
        raise HTTPException(status_code=404, detail="Agent parameter store is disabled.")
    store = getattr(agent_runtime, "parameter_store", None)
    if isinstance(store, AgentParameterStore):
        return store
    store = AgentParameterStore(settings.agent_parameters_db_path)
    try:
        setattr(agent_runtime, "parameter_store", store)
    except Exception:
        pass
    return store


def _parameter_store_for_macros(settings: Settings, agent_runtime: Any) -> AgentParameterStore | None:
    """Return parameter storage for prompt macros when the feature is enabled."""

    if not bool(getattr(settings, "agent_parameter_store_enabled", True)):
        return None
    try:
        return _parameter_store_or_error(settings, agent_runtime)
    except HTTPException:
        return None


def _credential_clarification_request(request: dict[str, Any] | None) -> bool:
    """Return whether a clarification expects credential-style private input."""

    if not isinstance(request, dict):
        return False
    input_kind = str(request.get("input_kind") or "unknown").strip().lower()
    primary_text = " ".join(
        str(request.get(key) or "")
        for key in ("question", "missing_information")
    ).lower()
    text = " ".join(
        str(request.get(key) or "")
        for key in ("question", "reason", "missing_information")
    ).lower()
    secret_match = re.search(
        r"\b(password|passphrase|pass\s+phrase|token|api\s*key|private\s+key|ssh\s+key|credential|secret|pin)\b",
        text,
        flags=re.IGNORECASE,
    )
    plain_answer_match = re.search(
        r"\b(commit\s+message|commit\s+title|message\s+for\s+(?:the\s+)?commit|branch\s+name|python\s+version|version|file\s+path|path|directory|filename|file\s+name|device\s+label|disk\s+label|drive\s+label|usb\s+label|volume\s+label|mount\s*point|mountpoint)\b",
        primary_text,
        flags=re.IGNORECASE,
    )
    primary_secret_match = re.search(
        r"\b(password|passphrase|pass\s+phrase|token|api\s*key|private\s+key|ssh\s+key|credential|secret|pin)\b",
        primary_text,
        flags=re.IGNORECASE,
    )
    if plain_answer_match is not None and primary_secret_match is None:
        return False
    if input_kind in {"password", "passphrase", "private_key", "token", "credential"}:
        return True
    if bool(request.get("secret_input")):
        return secret_match is not None
    return secret_match is not None


_CLARIFICATION_DECLINE_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|no|without|cancel|decline|skip|avoid)\b",
    flags=re.IGNORECASE,
)


def _clarification_option_for_payload(
    request: dict[str, Any] | None,
    payload: AgentClarificationPayload,
    answer: str,
) -> dict[str, Any] | None:
    if not isinstance(request, dict):
        return None
    options = request.get("options")
    if not isinstance(options, list):
        return None
    selected = str(payload.selected_option_id or "").strip()
    answer_text = " ".join(str(answer or "").split()).strip().lower()
    selected_text = selected.lower()
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = str(option.get("option_id") or "").strip()
        label = str(option.get("label") or "").strip()
        if selected and selected_text in {option_id.lower(), label.lower()}:
            return option
        if answer_text and answer_text in {
            option_id.lower(),
            " ".join(label.split()).strip().lower(),
        }:
            return option
    return None


def _credential_clarification_declined(
    request: dict[str, Any] | None,
    payload: AgentClarificationPayload,
    answer: str,
) -> bool:
    if not _credential_clarification_request(request):
        return False
    selected = str(payload.selected_option_id or "").strip()
    if not selected:
        return False
    option = _clarification_option_for_payload(request, payload, answer)
    text = " ".join(
        str(value or "")
        for value in (
            selected,
            option.get("label") if isinstance(option, dict) else "",
            option.get("description") if isinstance(option, dict) else "",
        )
    )
    return bool(
        selected.lower() == "do_not_use_sudo"
        or _CLARIFICATION_DECLINE_RE.search(text)
    )


def _credential_clarification_needs_secret_answer(
    request: dict[str, Any] | None,
    payload: AgentClarificationPayload,
    answer: str,
) -> bool:
    if not _credential_clarification_request(request):
        return False
    if bool(payload.answer_is_secret):
        return False
    if _credential_clarification_declined(request, payload, answer):
        return False
    option = _clarification_option_for_payload(request, payload, answer)
    if not isinstance(option, dict):
        return False
    answer_text = " ".join(str(answer or "").split()).strip().lower()
    option_id = str(option.get("option_id") or "").strip().lower()
    label = " ".join(str(option.get("label") or "").split()).strip().lower()
    return bool(answer_text in {option_id, label})


def _credential_parameter_field_hint(request: dict[str, Any] | None) -> str:
    if not isinstance(request, dict):
        return ""
    input_kind = str(request.get("input_kind") or "unknown").strip().lower()
    if input_kind in {"password", "passphrase", "token"}:
        return input_kind
    return ""


def _next_typein_macro_index(context: dict[str, Any]) -> int:
    raw = context.get(USER_MACRO_PRIVATE_CONTEXT_KEY)
    count = 0
    if isinstance(raw, list):
        count = sum(
            1
            for item in raw
            if isinstance(item, dict)
            and str(item.get("kind") or "").strip().lower() == TYPEIN_MACRO_KIND
        )
    return count + 1


def _append_typein_macro_context(
    context: dict[str, Any],
    private_payload: dict[str, Any],
    public_summary: dict[str, Any],
) -> None:
    private_macros = context.get(USER_MACRO_PRIVATE_CONTEXT_KEY)
    if not isinstance(private_macros, list):
        private_macros = []
    context[USER_MACRO_PRIVATE_CONTEXT_KEY] = [
        *[dict(item) for item in private_macros if isinstance(item, dict)],
        dict(private_payload),
    ]
    summaries = context.get(USER_MACRO_SUMMARY_CONTEXT_KEY)
    if not isinstance(summaries, list):
        summaries = []
    context[USER_MACRO_SUMMARY_CONTEXT_KEY] = [
        *[dict(item) for item in summaries if isinstance(item, dict)],
        dict(public_summary),
    ]


def _parameter_key_from_choice_payload(payload: AgentClarificationPayload) -> str:
    key = str(payload.parameter_key or "").strip()
    if key:
        return key
    choice_id = str(payload.parameter_choice_id or payload.selected_option_id or "").strip()
    if choice_id.lower().startswith("param:"):
        return choice_id.split(":", 1)[1].strip()
    return ""


def _parameter_key_from_clarification_answer(answer: str) -> str:
    text = " ".join(str(answer or "").strip().split())
    if not text:
        return ""
    patterns = (
        r"^use\s+parameter\s+([A-Za-z0-9_.:\- ]{1,160})$",
        r"^use\s+(?:the\s+)?(?:parameter\s+)?key\s+([A-Za-z0-9_.:\- ]{1,160})$",
        (
            r"^use\s+(?:the\s+)?(?:password|passphrase|credential|secret|token)\s+"
            r"(?:stored\s+)?(?:in|under)\s+(?:key\s+)?([A-Za-z0-9_.:\- ]{1,160})$"
        ),
    )
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            return str(match.group(1) or "").strip(" `'\").,;:")
    return ""


def _attach_clarification_typein_macro(
    *,
    settings: Settings,
    agent_runtime: Any,
    replay_context: dict[str, Any],
    clarification_request: dict[str, Any],
    payload: AgentClarificationPayload,
    answer: str,
) -> dict[str, Any]:
    """Attach private terminal input for credential clarification answers."""

    parameter_key = (
        _parameter_key_from_choice_payload(payload)
        or _parameter_key_from_clarification_answer(answer)
    )
    selected_option_id = str(payload.selected_option_id or payload.parameter_choice_id or "").strip()
    if parameter_key:
        parameter_store = _parameter_store_or_error(settings, agent_runtime)
        record = parameter_store.get(parameter_key)
        if record is None:
            raise HTTPException(status_code=404, detail=f'Parameter "{parameter_key}" was not found.')
        index = _next_typein_macro_index(replay_context)
        try:
            private_payload, public_summary = typein_macro_payloads_from_parameter_record(
                record,
                field=str(payload.parameter_field or _credential_parameter_field_hint(clarification_request)),
                parameter_store=parameter_store,
                record_use=True,
                macro_id=f"macro_typein_{index}",
                input_name=f"{TYPEIN_INPUT_PREFIX}{index}",
                actor="clarification",
            )
        except UserMacroParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _append_typein_macro_context(replay_context, private_payload, public_summary)
        return {
            "answer": f"Use parameter {record.key}",
            "answer_redacted": True,
            "answer_is_secret": True,
            "selected_option_id": selected_option_id or f"param:{record.normalized_key}",
            "parameter_choice_id": str(payload.parameter_choice_id or f"param:{record.normalized_key}"),
            "parameter_key": record.key,
            "parameter_field": str(private_payload.get("parameter_field") or ""),
            "source": "parameter_store",
        }

    if _credential_clarification_declined(clarification_request, payload, answer):
        return {
            "answer": answer,
            "answer_redacted": False,
            "answer_is_secret": False,
            "selected_option_id": selected_option_id,
            "parameter_choice_id": "",
            "parameter_key": "",
            "parameter_field": "",
            "source": "freeform",
        }

    if _credential_clarification_needs_secret_answer(clarification_request, payload, answer):
        raise HTTPException(
            status_code=400,
            detail="Enter a password or choose a saved credential to retry with sudo.",
        )

    secret_answer = bool(payload.answer_is_secret) or _credential_clarification_request(clarification_request)
    if not secret_answer:
        return {
            "answer": answer,
            "answer_redacted": False,
            "answer_is_secret": False,
            "selected_option_id": selected_option_id,
            "parameter_choice_id": "",
            "parameter_key": "",
            "parameter_field": "",
            "source": "freeform",
        }

    index = _next_typein_macro_index(replay_context)
    private_payload, public_summary = typein_macro_payloads(
        answer,
        macro_id=f"macro_typein_{index}",
        input_name=f"{TYPEIN_INPUT_PREFIX}{index}",
        source="clarification",
        redacted=True,
    )
    _append_typein_macro_context(replay_context, private_payload, public_summary)
    return {
        "answer": "[redacted]",
        "answer_redacted": True,
        "answer_is_secret": True,
        "selected_option_id": selected_option_id,
        "parameter_choice_id": "",
        "parameter_key": "",
        "parameter_field": "",
        "source": "freeform_secret",
    }

__all__ = [name for name in globals() if not name.startswith("__")]
