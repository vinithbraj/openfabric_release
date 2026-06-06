"""Confirmation payload preview helpers."""

from __future__ import annotations

from .common import *

def _confirmation_record_field(record: OperatorExecutionRecord, source_field: str) -> Any:
    field = str(source_field or "stdout").strip()
    if field == "stdout":
        return record.stdout
    if field == "stderr":
        return record.stderr
    if field == "exit_code":
        return record.exit_code
    if field == "output":
        return record.output
    if field == "record":
        return record.model_dump(mode="json")
    return None

def _confirmation_input_binding_previews(
    action: OperatorAction,
    records: list[OperatorExecutionRecord] | None,
) -> list[dict[str, Any]]:
    if not action.input_bindings:
        return []
    records_by_action = {record.action_id: record for record in list(records or [])}
    previews: list[dict[str, Any]] = []
    for binding in action.input_bindings:
        source = records_by_action.get(binding.source_action_id)
        value = None
        resolved = False
        if source is not None and source.status == "success":
            value = _confirmation_record_field(source, binding.source_field)
            resolved = True
        text = value if isinstance(value, str) else _stable_json(value) if value is not None else ""
        previews.append(
            {
                "input_name": binding.input_name,
                "source_action_id": binding.source_action_id,
                "source_field": binding.source_field,
                "required": binding.required,
                "resolved": resolved,
                "value_length": len(str(text)),
                "value_preview": _truncate(text, 1200) if resolved else "",
                "source_kind": source.kind if source is not None else None,
            }
        )
    return previews

def _confirmation_stdin_preview(
    action: OperatorAction,
    binding_previews: list[dict[str, Any]],
) -> dict[str, Any]:
    preview = _action_stdin_preview(action)
    if preview.get("stdin_mode") != "input_binding":
        return preview
    input_name = str(preview.get("stdin_input_name") or "").strip()
    for item in binding_previews:
        if str(item.get("input_name") or "").strip() != input_name:
            continue
        if item.get("resolved"):
            preview = dict(preview)
            preview["stdin_length"] = int(item.get("value_length") or 0)
            preview["stdin_preview"] = str(item.get("value_preview") or "")
        break
    return preview

__all__ = [name for name in globals() if not name.startswith("__")]
