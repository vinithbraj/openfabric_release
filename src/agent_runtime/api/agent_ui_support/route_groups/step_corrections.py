"""Learned artifact correction Agent UI routes."""

from __future__ import annotations

import ast

from agent_runtime.api.agent_ui_support.route_groups.shared import *
from agent_runtime.command_template_cache import CommandTemplateEntry, CommandTemplateWrite
from agent_runtime.computation_cache import ComputationCacheEntry, ComputationCacheWrite
from agent_runtime.core.types import TaskFrame
from agent_runtime.input_pipeline.decomposition import DecompositionResult
from agent_runtime.input_pipeline.validators import PlanningContractValidator
from agent_runtime.learning_ledger.models import (
    LearningCacheEventWrite,
    LearningLessonWrite,
)
from agent_runtime.lrn_total_tasks import LrnTotalTaskEntry, LrnTotalTaskWrite
from agent_runtime.operator.models import OperatorPlan
from agent_runtime.operator.validation_support.generated_python import (
    generated_python_side_effect_reasons,
)
from agent_runtime.plan_cache import PlanCacheEntry, PlanCacheWrite


_CANONICAL_ARTIFACT_KINDS = {
    "command": "command_template",
    "command_template": "command_template",
    "template": "command_template",
    "lr": "command_template",
    "lrd": "lr_d",
    "lr-d": "lr_d",
    "lr_d": "lr_d",
    "lrdirect": "lr_d",
    "lr-ex": "lr_ex",
    "lr_ex": "lr_ex",
    "lrex": "lr_ex",
    "lr-t": "lr_t",
    "lr_t": "lr_t",
    "lrt": "lr_t",
    "lrnt": "lr_t",
    "plan": "plan_cache",
    "plan_cache": "plan_cache",
    "computation": "computation_cache",
    "computation_cache": "computation_cache",
}

_COMMAND_ARTIFACT_KINDS = {"command_template", "lr_d", "lr_ex"}
_UNSAFE_CORRECTION_FRAGMENTS = (
    "skip approval",
    "skip-approval",
    "bypass approval",
    "bypass-approval",
    "disable approval",
    "skip validation",
    "skip-validation",
    "bypass validation",
    "bypass-validation",
    "disable validation",
    "ignore safety",
    "ignore policy",
    "disable sandbox",
    "bypass sandbox",
    "without confirmation",
    "never ask for approval",
)
_SECRET_SHAPED_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)\s*[:=]\s*\S+"
)


def _artifact_kind_or_error(value: str) -> str:
    kind = _CANONICAL_ARTIFACT_KINDS.get(str(value or "").strip().lower())
    if kind is None:
        raise HTTPException(status_code=404, detail="Unsupported learned artifact kind.")
    return kind


def _safe_text(value: Any, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").split()).strip()
    text = _SECRET_SHAPED_RE.sub("<redacted>", text)
    return text[:limit]


def _correction_text_is_unsafe(value: Any) -> bool:
    text = str(value or "")
    lowered = text.lower()
    return _SECRET_SHAPED_RE.search(text) is not None or any(
        fragment in lowered for fragment in _UNSAFE_CORRECTION_FRAGMENTS
    )


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    except Exception:
        return str(value or "")


def _replacement_prompt(prompt: str, prefix: str) -> str:
    suffix = f"User corrected auto-learnt action replacement {new_id(prefix)}."
    return "\n".join(part for part in (str(prompt or "").strip(), suffix) if part)


def _replace_text_fields(
    value: Any,
    *,
    field_names: set[str],
    new_text: str,
    old_text: str = "",
) -> Any:
    if isinstance(value, dict):
        updated: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key or "")
            if key_text in field_names and isinstance(item, str):
                updated[key] = new_text
            elif old_text and isinstance(item, str) and item == old_text:
                updated[key] = new_text
            else:
                updated[key] = _replace_text_fields(
                    item,
                    field_names=field_names,
                    new_text=new_text,
                    old_text=old_text,
                )
        return updated
    if isinstance(value, list):
        return [
            _replace_text_fields(
                item,
                field_names=field_names,
                new_text=new_text,
                old_text=old_text,
            )
            for item in value
        ]
    return value


def _cache_type_for_kind(kind: str) -> str:
    if kind in _COMMAND_ARTIFACT_KINDS:
        return "command_template"
    if kind == "plan_cache":
        return "plan"
    if kind == "computation_cache":
        return "computation"
    return "lr_t"


def _lesson_type_for_correction(kind: str, correction_kind: str) -> str:
    if kind in _COMMAND_ARTIFACT_KINDS or correction_kind in {"command", "code"}:
        return "command_correction"
    return "platform_guidance"


def _entry_context(entry: Any) -> dict[str, Any]:
    return {
        "model_name": str(getattr(entry, "model_name", "") or ""),
        "model_family": str(getattr(entry, "model_family", "") or ""),
        "task_type": str(getattr(entry, "task_type", "") or ""),
        "tool_type": str(getattr(entry, "tool_type", "") or ""),
        "intent_type": str(getattr(entry, "intent_type", "") or ""),
    }


def _artifact_prompt(entry: Any, trace: Any = None) -> str:
    for attr in (
        "prompt_excerpt",
        "exact_step_prompt_excerpt",
        "step_excerpt",
        "normalized_prompt",
    ):
        value = str(getattr(entry, attr, "") or "").strip()
        if value:
            return value
    return str(getattr(trace, "prompt", "") or "").strip()


def _artifact_editable_snapshot(kind: str, entry: Any) -> dict[str, Any]:
    if kind in _COMMAND_ARTIFACT_KINDS:
        return {
            "command": getattr(entry, "command_template", ""),
            "instruction": "",
        }
    if kind == "computation_cache":
        return {
            "code": getattr(entry, "code_template", ""),
            "instruction": "",
        }
    if kind == "plan_cache":
        return {
            "plan": getattr(entry, "plan", {}) or {},
            "instruction": "",
        }
    return {
        "tasks": list(getattr(entry, "tasks", []) or []),
        "global_constraints": dict(getattr(entry, "global_constraints", {}) or {}),
        "instruction": "",
    }


def _artifact_mode_labels(kind: str) -> list[str]:
    if kind in _COMMAND_ARTIFACT_KINDS:
        return ["command", "instruction"]
    if kind == "computation_cache":
        return ["code", "instruction"]
    if kind == "plan_cache":
        return ["plan", "instruction"]
    return ["lr_tasks", "instruction"]


def _command_store_entry(
    settings: Settings,
    agent_runtime: Any,
    artifact_id: str,
) -> CommandTemplateEntry | None:
    return _command_template_cache_store(settings, agent_runtime).get_entry(artifact_id)


def _resolve_artifact(
    settings: Settings,
    agent_runtime: Any,
    artifact_ref: AgentLearnedArtifactRef,
) -> tuple[str, Any]:
    kind = _artifact_kind_or_error(artifact_ref.artifact_kind)
    artifact_id = str(artifact_ref.artifact_id or "").strip()
    if kind in _COMMAND_ARTIFACT_KINDS:
        entry = _command_store_entry(settings, agent_runtime, artifact_id)
    elif kind == "lr_t":
        entry = _lrn_total_task_store(settings, agent_runtime).get_entry(artifact_id)
    elif kind == "plan_cache":
        entry = _plan_cache_store(settings, agent_runtime).get_entry(artifact_id)
    elif kind == "computation_cache":
        entry = _computation_cache_store(settings, agent_runtime).get_entry(artifact_id)
    else:  # pragma: no cover - guarded by _artifact_kind_or_error
        entry = None
    if entry is None:
        raise HTTPException(status_code=404, detail="Learned artifact was not found.")
    return kind, entry


def _artifact_ref_with_context(
    artifact_ref: AgentLearnedArtifactRef,
    kind: str,
    entry: Any,
) -> AgentLearnedArtifactRef:
    return artifact_ref.model_copy(
        update={
            "artifact_kind": kind,
            "artifact_id": str(
                getattr(
                    entry,
                    "template_id",
                    getattr(entry, "entry_id", getattr(entry, "cache_id", artifact_ref.artifact_id)),
                )
                or artifact_ref.artifact_id
            ),
            "exact_step_key": str(
                artifact_ref.exact_step_key or getattr(entry, "exact_step_key", "") or ""
            ),
        }
    )


def _validate_correction_payload(
    payload: AgentStepCorrectionPayload,
    *,
    artifact_kind: str,
    entry: Any,
) -> dict[str, Any]:
    correction_kind = str(payload.correction_kind or "").strip()
    instruction = str(payload.instruction or "").strip()
    rationale = str(payload.rationale or "").strip()
    text_to_scan = "\n".join(
        [
            instruction,
            rationale,
            str(payload.corrected_command or ""),
            str(payload.corrected_code or ""),
            _json_text(payload.plan or {}),
            _json_text(payload.lr_tasks or []),
            _json_text(payload.global_constraints or {}),
        ]
    )
    if _correction_text_is_unsafe(text_to_scan):
        raise HTTPException(
            status_code=400,
            detail=(
                "Correction text appears to include a secret or safety/approval bypass. "
                "Corrections can teach behavior, but cannot weaken validation, approval, or sandbox policy."
            ),
        )
    if correction_kind == "instruction":
        if not instruction:
            raise HTTPException(status_code=400, detail="Instruction-only corrections require guidance.")
        return {"instruction": instruction}
    if correction_kind == "command":
        if artifact_kind not in _COMMAND_ARTIFACT_KINDS:
            raise HTTPException(status_code=400, detail="Command corrections apply only to command artifacts.")
        command = str(payload.corrected_command or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="A corrected command is required.")
        if command == str(getattr(entry, "command_template", "") or "").strip():
            raise HTTPException(status_code=400, detail="Corrected command must differ from the existing command.")
        return {"command": command, "instruction": instruction}
    if correction_kind == "code":
        if artifact_kind != "computation_cache":
            raise HTTPException(status_code=400, detail="Code corrections apply only to computation cache artifacts.")
        code = str(payload.corrected_code or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="Corrected Python code is required.")
        try:
            ast.parse(code)
        except SyntaxError as exc:
            raise HTTPException(status_code=400, detail=f"Corrected Python code is invalid: {exc.msg}.") from exc
        side_effects = generated_python_side_effect_reasons(code)
        if side_effects:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Corrected computation code must be side-effect free.",
                    "side_effects": side_effects,
                },
            )
        return {"code": code, "instruction": instruction}
    if correction_kind == "plan":
        if artifact_kind != "plan_cache":
            raise HTTPException(status_code=400, detail="Plan corrections apply only to plan cache artifacts.")
        plan_payload = payload.plan or {}
        if not isinstance(plan_payload, dict) or not plan_payload:
            raise HTTPException(status_code=400, detail="A corrected OperatorPlan JSON object is required.")
        try:
            plan = OperatorPlan.model_validate(plan_payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Corrected plan is invalid: {exc}") from exc
        return {"plan": plan.model_dump(mode="json"), "instruction": instruction}
    if correction_kind == "lr_tasks":
        if artifact_kind != "lr_t":
            raise HTTPException(status_code=400, detail="LR-T task corrections apply only to LR-T artifacts.")
        raw_tasks = list(payload.lr_tasks or [])
        if not raw_tasks:
            raise HTTPException(status_code=400, detail="At least one corrected LR-T task is required.")
        try:
            tasks = [TaskFrame.model_validate(item) for item in raw_tasks]
            decomposition = DecompositionResult(
                tasks=tasks,
                global_constraints=dict(payload.global_constraints or {}),
            )
            contract = PlanningContractValidator().validate_tasks(
                decomposition.tasks,
                original_prompt=_artifact_prompt(entry),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Corrected LR-T task graph is invalid: {exc}") from exc
        if not contract.accepted:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Corrected LR-T task graph violates the planning contract.",
                    "issues": contract.feedback(),
                },
            )
        return {
            "lr_tasks": [task.model_dump(mode="json") for task in decomposition.tasks],
            "global_constraints": decomposition.global_constraints,
            "instruction": instruction,
        }
    raise HTTPException(status_code=400, detail="Unsupported correction kind.")


def _retry_notes(
    artifact_ref: AgentLearnedArtifactRef,
    correction_kind: str,
    validated: dict[str, Any],
) -> str:
    lines = [
        (
            "User corrected auto-learnt artifact "
            f"{artifact_ref.artifact_kind}:{artifact_ref.artifact_id}."
        ),
        "Keep normal validation, approval, confirmation, and sandbox behavior in force.",
    ]
    if validated.get("instruction"):
        lines.append(f"Instruction: {_safe_text(validated.get('instruction'), limit=1000)}")
    if correction_kind == "command":
        lines.append(f"Corrected command: {_safe_text(validated.get('command'), limit=1000)}")
    elif correction_kind == "code":
        lines.append("Corrected computation code was saved to the learned cache.")
    elif correction_kind == "plan":
        lines.append("Corrected OperatorPlan JSON was saved to the plan cache.")
    elif correction_kind == "lr_tasks":
        lines.append("Corrected LR-T task graph was saved as an active learned task structure.")
    return "\n".join(lines)


def _mark_artifact_failed(
    settings: Settings,
    agent_runtime: Any,
    kind: str,
    entry: Any,
    notes: str,
) -> tuple[list[str], list[str]]:
    marked_failed: list[str] = []
    quarantined: list[str] = []
    if kind in _COMMAND_ARTIFACT_KINDS:
        _command_template_cache_store(settings, agent_runtime).mark_failed(
            entry.template_id,
            failure_category="user_corrected",
            repair_notes=notes,
        )
        marked_failed.append(entry.template_id)
    elif kind == "lr_t":
        updated = _lrn_total_task_store(settings, agent_runtime).mark_failed(
            entry.entry_id,
            failure_category="user_corrected",
            quarantine=True,
        )
        if updated is not None:
            quarantined.append(entry.entry_id)
    elif kind == "plan_cache":
        _plan_cache_store(settings, agent_runtime).mark_failed(
            entry.cache_id,
            failure_category="user_corrected",
            repair_notes=notes,
        )
        marked_failed.append(entry.cache_id)
    elif kind == "computation_cache":
        _computation_cache_store(settings, agent_runtime).mark_failed(
            entry.cache_id,
            failure_category="user_corrected",
            repair_notes=notes,
        )
        marked_failed.append(entry.cache_id)
    return marked_failed, quarantined


def _delete_artifact_entry(
    settings: Settings,
    agent_runtime: Any,
    kind: str,
    entry: Any,
) -> list[str]:
    """Delete only the resolved learned artifact row."""

    if kind in _COMMAND_ARTIFACT_KINDS:
        deleted_id = str(entry.template_id or "")
        deleted = _command_template_cache_store(settings, agent_runtime).delete_entry(deleted_id)
    elif kind == "lr_t":
        deleted_id = str(entry.entry_id or "")
        deleted = _lrn_total_task_store(settings, agent_runtime).delete_entry(deleted_id)
    elif kind == "plan_cache":
        deleted_id = str(entry.cache_id or "")
        deleted = _plan_cache_store(settings, agent_runtime).delete_entry(deleted_id)
    elif kind == "computation_cache":
        deleted_id = str(entry.cache_id or "")
        deleted = _computation_cache_store(settings, agent_runtime).delete_entry(deleted_id)
    else:  # pragma: no cover - guarded by _artifact_kind_or_error
        deleted_id = ""
        deleted = False
    if not deleted or not deleted_id:
        raise HTTPException(status_code=404, detail="Learned artifact was not found.")
    return [deleted_id]


def _write_command_replacement(
    settings: Settings,
    agent_runtime: Any,
    entry: CommandTemplateEntry,
    command: str,
    instruction: str,
) -> CommandTemplateEntry:
    direct_action = _replace_text_fields(
        dict(entry.direct_action or {}),
        field_names={"command", "shell_command", "command_template"},
        new_text=command,
        old_text=entry.command_template,
    )
    return _command_template_cache_store(settings, agent_runtime).upsert_entry(
        CommandTemplateWrite(
            prompt=_artifact_prompt(entry),
            step_description=entry.step_excerpt or entry.exact_step_prompt_excerpt,
            mode=entry.mode,
            model_name=entry.model_name,
            model_family=entry.model_family,
            cwd=entry.cwd,
            gateway_platform=entry.gateway_platform,
            task_type=entry.task_type,
            tool_type=entry.tool_type,
            intent_type=entry.intent_type,
            interaction_mode=entry.interaction_mode,
            tags=list(dict.fromkeys([*entry.tags, "user_corrected"])),
            lr_mode=entry.lr_mode,
            payload_bindings=list(entry.payload_bindings or []),
            exact_step_key=entry.exact_step_key,
            exact_step_prompt_excerpt=entry.exact_step_prompt_excerpt,
            command_template=command,
            variables=list(entry.variables or []),
            direct_action=direct_action,
            observed_command=command,
            risk=entry.risk,
            effect_intent=entry.effect_intent,
            effect_summary=entry.effect_summary,
            requires_confirmation=entry.requires_confirmation,
            approval_observed=entry.approval_observed,
            status="success",
            repair_notes=instruction or "User corrected auto-learnt command.",
        )
    )


def _write_lr_t_replacement(
    settings: Settings,
    agent_runtime: Any,
    entry: LrnTotalTaskEntry,
    tasks: list[dict[str, Any]],
    global_constraints: dict[str, Any],
) -> LrnTotalTaskEntry:
    routing_metadata = dict(entry.routing_metadata or {})
    routing_metadata["user_corrected"] = True
    routing_metadata["corrected_from_entry_id"] = entry.entry_id
    return _lrn_total_task_store(settings, agent_runtime).upsert_entry(
        LrnTotalTaskWrite(
            prompt=_replacement_prompt(entry.prompt_excerpt or entry.normalized_prompt, "lrntcorr"),
            classification_context=dict(entry.classification_context or {}),
            model_name=entry.model_name,
            model_family=entry.model_family,
            workflow_mode=entry.workflow_mode,
            registry_contract_hash=entry.registry_contract_hash,
            tasks=tasks,
            global_constraints=global_constraints,
            routing_metadata=routing_metadata,
        )
    )


def _write_plan_replacement(
    settings: Settings,
    agent_runtime: Any,
    entry: PlanCacheEntry,
    plan: dict[str, Any],
) -> PlanCacheEntry:
    prompt = _replacement_prompt(entry.prompt_excerpt, "plancorr")
    intent_signature = entry.intent_signature
    if entry.exact_step_key and entry.direct_plan:
        intent_signature = f"{entry.intent_signature}:user_corrected:{new_id('planrev')}"
    return _plan_cache_store(settings, agent_runtime).upsert_entry(
        PlanCacheWrite(
            prompt=prompt,
            mode=entry.mode,
            model_name=entry.model_name,
            model_family=entry.model_family,
            cwd=entry.cwd,
            task_type=entry.task_type,
            tool_type=entry.tool_type,
            intent_type=entry.intent_type,
            tags=list(dict.fromkeys([*entry.tags, "user_corrected"])),
            self_brief=dict(entry.self_brief or {}),
            plan=plan,
            records=list(entry.records_summary or []),
            final_response=entry.final_response_preview,
            exact_step_key=entry.exact_step_key,
            exact_step_prompt_excerpt=entry.exact_step_prompt_excerpt,
            intent_snapshot=dict(entry.intent_snapshot or {}),
            intent_signature=intent_signature,
            direct_plan={},
            status="success",
            repair_notes="User corrected auto-learnt plan cache entry.",
        )
    )


def _write_computation_replacement(
    settings: Settings,
    agent_runtime: Any,
    entry: ComputationCacheEntry,
    code: str,
    instruction: str,
) -> ComputationCacheEntry:
    direct_action = _replace_text_fields(
        dict(entry.direct_action or {}),
        field_names={"code", "python_code", "code_template"},
        new_text=code,
        old_text=entry.code_template,
    )
    return _computation_cache_store(settings, agent_runtime).upsert_entry(
        ComputationCacheWrite(
            prompt=_replacement_prompt(entry.prompt_excerpt or entry.exact_step_prompt_excerpt, "compcorr"),
            mode=entry.mode,
            model_name=entry.model_name,
            model_family=entry.model_family,
            task_type=entry.task_type,
            tool_type=entry.tool_type,
            intent_type=entry.intent_type,
            tags=list(dict.fromkeys([*entry.tags, "user_corrected"])),
            action_kind=entry.action_kind,
            action_reason=entry.action_reason,
            input_profile=dict(entry.input_profile or {}),
            input_signature=entry.input_signature,
            exact_step_key=entry.exact_step_key,
            exact_step_prompt_excerpt=entry.exact_step_prompt_excerpt,
            code_template=code,
            direct_action=direct_action,
            declared_output_shape=entry.declared_output_shape,
            allow_zero_result=entry.allow_zero_result,
            template_reason=instruction or entry.template_reason,
            output=entry.output_preview,
            status="success",
            repair_notes=instruction or "User corrected auto-learnt computation.",
        )
    )


def _record_learning_evidence(
    settings: Settings,
    agent_runtime: Any,
    artifact_ref: AgentLearnedArtifactRef,
    kind: str,
    entry: Any,
    correction_kind: str,
    validated: dict[str, Any],
    replacement_ids: dict[str, str],
    marked_failed: list[str],
    quarantined: list[str],
    warnings: list[str],
) -> tuple[str, str]:
    try:
        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
    except Exception as exc:
        warnings.append(f"Learning ledger was not updated: {exc}")
        return "", ""
    scope = {
        **_entry_context(entry),
        "memory_scope": "model_family" if getattr(entry, "model_family", "") else "global",
        "artifact_kind": kind,
        "artifact_id": artifact_ref.artifact_id,
        "exact_step_key": artifact_ref.exact_step_key,
    }
    instruction = str(validated.get("instruction") or "").strip()
    if not instruction:
        if correction_kind == "command":
            instruction = (
                "For similar future steps, prefer the user-corrected command pattern. "
                "Keep normal validation and approval checks active."
            )
        elif correction_kind == "code":
            instruction = (
                "For similar future computation steps, prefer the user-corrected Python code pattern. "
                "Keep generated-code safety review active."
            )
        elif correction_kind == "plan":
            instruction = (
                "For similar future requests, prefer the user-corrected operator plan structure. "
                "Keep plan validation and approval checks active."
            )
        elif correction_kind == "lr_tasks":
            instruction = (
                "For similar future requests, prefer the user-corrected LR-T task decomposition. "
                "Keep task validation and approval checks active."
            )
    evidence = {
        "artifact_ref": artifact_ref.model_dump(mode="json"),
        "correction_kind": correction_kind,
        "replacement_ids": replacement_ids,
        "marked_failed_ids": marked_failed,
        "quarantined_ids": quarantined,
        "safe_examples": [],
        "blocked_examples": [],
    }
    if correction_kind == "command":
        evidence["safe_examples"] = [_safe_text(validated.get("command"), limit=1000)]
        evidence["blocked_examples"] = [_safe_text(getattr(entry, "command_template", ""), limit=1000)]
    elif correction_kind == "code":
        evidence["safe_examples"] = [_safe_text(validated.get("code"), limit=1000)]
    lesson = ledger.create_lesson(
        LearningLessonWrite(
            lesson_type=_lesson_type_for_correction(kind, correction_kind),  # type: ignore[arg-type]
            title="User corrected auto-learnt action",
            instruction=instruction,
            summary=(
                f"User corrected {kind} artifact {artifact_ref.artifact_id} "
                f"with {correction_kind} guidance."
            ),
            scope=scope,
            evidence=evidence,
            confidence=0.95,
            source_request_id=artifact_ref.request_id,
            tags=["learning-ledger", "user-corrected", "auto-learnt", kind, correction_kind],
            rationale=str(validated.get("instruction") or ""),
            dedupe_key=(
                f"user-corrected:{kind}:{artifact_ref.artifact_id}:"
                f"{correction_kind}:{_safe_text(validated, limit=300)}"
            ),
        )
    )
    lesson_id = ""
    memory_id = ""
    if lesson is None:
        warnings.append("Learning lesson was rejected as empty or unsafe.")
    else:
        lesson_id = lesson.lesson_id
        try:
            memory_store = _memory_store_or_error(settings, agent_runtime)
            approved = ledger.approve_lesson(lesson.lesson_id, memory_store=memory_store, actor="user")
            if approved is not None:
                memory_id = approved.mirrored_memory_id
        except Exception as exc:
            warnings.append(f"Learning lesson was saved but not mirrored to active memory: {exc}")
    if artifact_ref.request_id:
        for cache_id in marked_failed + quarantined:
            ledger.record_cache_event(
                LearningCacheEventWrite(
                    request_id=artifact_ref.request_id,
                    cache_type=_cache_type_for_kind(kind),  # type: ignore[arg-type]
                    cache_id=cache_id,
                    event="user_corrected",
                    status="quarantined",
                    outcome="user_corrected",
                    reason="User corrected this auto-learnt artifact.",
                    evidence=evidence,
                )
            )
        for cache_id in replacement_ids.values():
            ledger.record_cache_event(
                LearningCacheEventWrite(
                    request_id=artifact_ref.request_id,
                    cache_type=_cache_type_for_kind(kind),  # type: ignore[arg-type]
                    cache_id=cache_id,
                    event="user_corrected_replacement",
                    status="strengthened",
                    outcome="user_corrected",
                    reason="User-provided correction created this replacement artifact.",
                    evidence=evidence,
                )
            )
    return lesson_id, memory_id


def _record_deletion_evidence(
    settings: Settings,
    agent_runtime: Any,
    artifact_ref: AgentLearnedArtifactRef,
    kind: str,
    deleted_ids: list[str],
    reason: str,
    warnings: list[str],
) -> None:
    try:
        ledger = _learning_ledger_store_or_error(settings, agent_runtime)
    except Exception as exc:
        warnings.append(f"Learning ledger was not updated: {exc}")
        return
    evidence = {
        "artifact_ref": artifact_ref.model_dump(mode="json"),
        "deleted_ids": deleted_ids,
        "delete_scope": "single_artifact_row",
    }
    safe_reason = _safe_text(reason or "User deleted this auto-learnt artifact.", limit=1000)
    for cache_id in deleted_ids:
        ledger.record_cache_event(
            LearningCacheEventWrite(
                request_id=artifact_ref.request_id,
                cache_type=_cache_type_for_kind(kind),  # type: ignore[arg-type]
                cache_id=cache_id,
                event="user_deleted",
                status="deleted",
                outcome="user_deleted",
                reason=safe_reason,
                evidence=evidence,
            )
        )


def _append_correction_trace_event(
    trace_store: AgentTraceStore,
    artifact_ref: AgentLearnedArtifactRef,
    correction_kind: str,
    response: AgentStepCorrectionResponse,
) -> dict[str, Any]:
    if not artifact_ref.request_id:
        return {}
    try:
        event = trace_store.append_event(
            AgentTraceEvent(
                request_id=artifact_ref.request_id,
                stage="learning",
                level="info",
                event_type="learning.step_correction.saved",
                title="Auto-learnt action corrected",
                summary=(
                    f"Saved user correction for {artifact_ref.artifact_kind} "
                    f"{artifact_ref.artifact_id}."
                ),
                detail={
                    "artifact_ref": artifact_ref.model_dump(mode="json"),
                    "correction_kind": correction_kind,
                    "replacement_ids": response.replacement_ids,
                    "marked_failed_ids": response.marked_failed_ids,
                    "quarantined_ids": response.quarantined_ids,
                    "lesson_id": response.lesson_id,
                    "memory_id": response.memory_id,
                    "retry_available": response.retry_available,
                    "retry_notes": response.retry_notes,
                },
            )
        )
        return event.model_dump(mode="json")
    except Exception:
        return {}


def _append_deletion_trace_event(
    trace_store: AgentTraceStore,
    artifact_ref: AgentLearnedArtifactRef,
    deleted_ids: list[str],
    reason: str,
) -> dict[str, Any]:
    if not artifact_ref.request_id:
        return {}
    try:
        event = trace_store.append_event(
            AgentTraceEvent(
                request_id=artifact_ref.request_id,
                stage="learning",
                level="info",
                event_type="learning.step_correction.deleted",
                title="Auto-learnt action deleted",
                summary=(
                    f"Deleted selected auto-learnt {artifact_ref.artifact_kind} "
                    f"{artifact_ref.artifact_id}."
                ),
                detail={
                    "artifact_ref": artifact_ref.model_dump(mode="json"),
                    "deleted_ids": deleted_ids,
                    "delete_scope": "single_artifact_row",
                    "reason": _safe_text(reason, limit=1000),
                },
            )
        )
        return event.model_dump(mode="json")
    except Exception:
        return {}


def register_step_correction_routes(ctx: AgentUiRouteContext) -> None:
    """Register learned artifact correction routes."""

    app = ctx.app
    settings = ctx.settings
    agent_runtime = ctx.agent_runtime
    trace_store = ctx.trace_store

    @app.get("/api/agent/corrections/artifact/{artifact_kind}/{artifact_id}")
    def get_learned_artifact_snapshot(artifact_kind: str, artifact_id: str) -> dict[str, Any]:
        kind = _artifact_kind_or_error(artifact_kind)
        artifact_ref = AgentLearnedArtifactRef(
            artifact_kind=kind,  # type: ignore[arg-type]
            artifact_id=artifact_id,
        )
        resolved_kind, entry = _resolve_artifact(settings, agent_runtime, artifact_ref)
        artifact_ref = _artifact_ref_with_context(artifact_ref, resolved_kind, entry)
        return {
            "artifact_ref": artifact_ref.model_dump(mode="json"),
            "artifact": entry.model_dump(mode="json"),
            "editable": _artifact_editable_snapshot(resolved_kind, entry),
            "correction_modes": _artifact_mode_labels(resolved_kind),
            "title": "Modify auto-learnt action",
        }

    @app.post(
        "/api/agent/corrections/step/validate",
        response_model=AgentStepCorrectionResponse,
    )
    def validate_step_correction(payload: AgentStepCorrectionPayload) -> AgentStepCorrectionResponse:
        kind, entry = _resolve_artifact(settings, agent_runtime, payload.artifact_ref)
        artifact_ref = _artifact_ref_with_context(payload.artifact_ref, kind, entry)
        _validate_correction_payload(payload, artifact_kind=kind, entry=entry)
        return AgentStepCorrectionResponse(
            artifact_ref=artifact_ref,
            status="validated",
            retry_available=bool(artifact_ref.request_id),
        )

    @app.post(
        "/api/agent/corrections/step",
        response_model=AgentStepCorrectionResponse,
    )
    def save_step_correction(payload: AgentStepCorrectionPayload) -> AgentStepCorrectionResponse:
        kind, entry = _resolve_artifact(settings, agent_runtime, payload.artifact_ref)
        artifact_ref = _artifact_ref_with_context(payload.artifact_ref, kind, entry)
        validated = _validate_correction_payload(payload, artifact_kind=kind, entry=entry)
        correction_kind = str(payload.correction_kind or "").strip()
        notes = _retry_notes(artifact_ref, correction_kind, validated)
        marked_failed, quarantined = _mark_artifact_failed(
            settings,
            agent_runtime,
            kind,
            entry,
            notes,
        )
        replacement_ids: dict[str, str] = {}
        warnings: list[str] = []
        if correction_kind == "command":
            replacement = _write_command_replacement(
                settings,
                agent_runtime,
                entry,
                str(validated["command"]),
                str(validated.get("instruction") or ""),
            )
            replacement_ids["command_template"] = replacement.template_id
        elif correction_kind == "code":
            replacement = _write_computation_replacement(
                settings,
                agent_runtime,
                entry,
                str(validated["code"]),
                str(validated.get("instruction") or ""),
            )
            replacement_ids["computation_cache"] = replacement.cache_id
        elif correction_kind == "plan":
            replacement = _write_plan_replacement(
                settings,
                agent_runtime,
                entry,
                dict(validated["plan"]),
            )
            replacement_ids["plan_cache"] = replacement.cache_id
            if getattr(entry, "direct_plan", None):
                warnings.append("Direct step-tree replay metadata was not preserved for the edited OperatorPlan.")
        elif correction_kind == "lr_tasks":
            replacement = _write_lr_t_replacement(
                settings,
                agent_runtime,
                entry,
                list(validated["lr_tasks"]),
                dict(validated["global_constraints"]),
            )
            replacement_ids["lr_t"] = replacement.entry_id
        lesson_id, memory_id = _record_learning_evidence(
            settings,
            agent_runtime,
            artifact_ref,
            kind,
            entry,
            correction_kind,
            validated,
            replacement_ids,
            marked_failed,
            quarantined,
            warnings,
        )
        response = AgentStepCorrectionResponse(
            artifact_ref=artifact_ref,
            status="saved",
            saved_ids=[item for item in [lesson_id, memory_id, *replacement_ids.values()] if item],
            quarantined_ids=quarantined,
            marked_failed_ids=marked_failed,
            replacement_ids=replacement_ids,
            lesson_id=lesson_id,
            memory_id=memory_id,
            retry_available=bool(artifact_ref.request_id),
            retry_notes=notes if artifact_ref.request_id else "",
            warnings=warnings,
        )
        trace_event = _append_correction_trace_event(
            trace_store,
            artifact_ref,
            correction_kind,
            response,
        )
        return response.model_copy(update={"trace_event": trace_event})

    @app.post(
        "/api/agent/corrections/step/delete",
        response_model=AgentStepCorrectionResponse,
    )
    def delete_step_correction(payload: AgentStepDeletionPayload) -> AgentStepCorrectionResponse:
        kind, entry = _resolve_artifact(settings, agent_runtime, payload.artifact_ref)
        artifact_ref = _artifact_ref_with_context(payload.artifact_ref, kind, entry)
        reason = str(payload.reason or "").strip() or "User deleted this auto-learnt artifact."
        deleted_ids = _delete_artifact_entry(settings, agent_runtime, kind, entry)
        warnings: list[str] = []
        _record_deletion_evidence(
            settings,
            agent_runtime,
            artifact_ref,
            kind,
            deleted_ids,
            reason,
            warnings,
        )
        response = AgentStepCorrectionResponse(
            artifact_ref=artifact_ref,
            status="deleted",
            deleted_ids=deleted_ids,
            retry_available=False,
            warnings=warnings,
        )
        trace_event = _append_deletion_trace_event(
            trace_store,
            artifact_ref,
            deleted_ids,
            reason,
        )
        return response.model_copy(update={"trace_event": trace_event})


__all__ = ["register_step_correction_routes"]
