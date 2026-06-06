"""Declarative result-shape adapter registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.types import ExecutionResult
from agent_runtime.output_pipeline.result_shapes import (
    AggregateResult,
    CapabilityListResult,
    CommandOutputResult,
    ErrorResult,
    FileContentResult,
    FileTreeResult,
    JsonResult,
    ProcessListResult,
    RecordListResult,
    ResultShape,
    ScalarResult,
    TableResult,
    TextResult,
    _rows_from_payload,
    _sanitize_error_message,
    _shape_metadata,
)


@dataclass(frozen=True)
class ShapeAdapterContext:
    """Runtime context available to a shape adapter."""

    result: ExecutionResult
    payload: Any
    capability_id: str | None = None
    operation_id: str | None = None
    title: str | None = None


ShapeAdapterNormalizer = Callable[[ShapeAdapterContext], ResultShape]
ShapeAdapterMatcher = Callable[[ShapeAdapterContext], bool]


@dataclass(frozen=True)
class ShapeAdapter:
    """One declared rule for normalizing a payload into a result shape."""

    adapter_id: str
    output_shape_type: str
    normalize: ShapeAdapterNormalizer
    priority: int = 100
    accepted_capability_ids: tuple[str, ...] = ()
    accepted_operation_ids: tuple[str, ...] = ()
    required_payload_keys: tuple[str, ...] = ()
    accepted_statuses: tuple[str, ...] = ("success",)
    matcher: ShapeAdapterMatcher | None = None

    def matches(self, context: ShapeAdapterContext) -> bool:
        """Return whether this adapter accepts the provided context."""

        if self.accepted_statuses and context.result.status not in self.accepted_statuses:
            return False
        if self.accepted_capability_ids and context.capability_id not in self.accepted_capability_ids:
            return False
        if self.accepted_operation_ids and context.operation_id not in self.accepted_operation_ids:
            return False
        if self.required_payload_keys:
            if not isinstance(context.payload, dict):
                return False
            if not all(key in context.payload for key in self.required_payload_keys):
                return False
        if self.matcher is not None and not self.matcher(context):
            return False
        return True


@dataclass
class ShapeAdapterRegistry:
    """Ordered registry of result-shape adapters."""

    adapters: list[ShapeAdapter] = field(default_factory=list)

    def register(self, adapter: ShapeAdapter) -> None:
        """Register one adapter."""

        self.adapters.append(adapter)
        self.adapters.sort(key=lambda item: (item.priority, item.adapter_id))

    def list_adapters(self) -> list[ShapeAdapter]:
        """Return adapters in evaluation order."""

        return list(self.adapters)

    def select_adapter(self, context: ShapeAdapterContext) -> ShapeAdapter:
        """Return the first adapter matching a context."""

        for adapter in self.adapters:
            if adapter.matches(context):
                return adapter
        raise ValueError("No shape adapter matched the execution result.")

    def normalize(self, context: ShapeAdapterContext) -> ResultShape:
        """Normalize a context using the first matching adapter."""

        return self.select_adapter(context).normalize(context)


def _is_dict_payload(context: ShapeAdapterContext) -> bool:
    return isinstance(context.payload, dict)


def _is_scalar_payload(context: ShapeAdapterContext) -> bool:
    return isinstance(context.payload, (str, int, float, bool)) or context.payload is None


def _is_list_payload(context: ShapeAdapterContext) -> bool:
    return isinstance(context.payload, list)


def _is_list_of_records(context: ShapeAdapterContext) -> bool:
    return (
        isinstance(context.payload, list)
        and bool(context.payload)
        and all(isinstance(item, dict) for item in context.payload)
    )


def _has_list_key(key: str) -> ShapeAdapterMatcher:
    def _matches(context: ShapeAdapterContext) -> bool:
        return isinstance(context.payload, dict) and isinstance(context.payload.get(key), list)

    return _matches


def _has_value_without_operation(context: ShapeAdapterContext) -> bool:
    return (
        isinstance(context.payload, dict)
        and "value" in context.payload
        and "operation" not in context.payload
    )


def _error_result(context: ShapeAdapterContext) -> ResultShape:
    return ErrorResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        message=_sanitize_error_message(context.result.error),
    )


def _record_list_result(context: ShapeAdapterContext) -> ResultShape:
    rows = _rows_from_payload(context.payload)
    metadata: dict[str, Any] = {}
    if isinstance(context.payload, dict):
        for key in ("path", "pattern"):
            if key in context.payload:
                metadata[key] = context.payload.get(key)
    return RecordListResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        records=rows,
        metadata=metadata,
        **_shape_metadata(context.result, context.payload, rows),
    )


def _table_result(context: ShapeAdapterContext) -> ResultShape:
    rows = _rows_from_payload(context.payload)
    return TableResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        rows=rows,
        **_shape_metadata(context.result, context.payload, rows),
    )


def _file_content_result(context: ShapeAdapterContext) -> ResultShape:
    payload = context.payload if isinstance(context.payload, dict) else {}
    return FileContentResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        path=payload.get("path"),
        content_preview=str(payload.get("content_preview") or ""),
        **_shape_metadata(context.result, payload),
    )


def _aggregate_result(context: ShapeAdapterContext) -> ResultShape:
    payload = context.payload if isinstance(context.payload, dict) else {}
    return AggregateResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        operation=str(payload.get("operation") or context.result.metadata.get("operation") or "aggregate"),
        field=payload.get("field"),
        value=payload.get("value"),
        unit=payload.get("unit"),
        row_count=int(payload.get("row_count", 0) or 0),
        used_count=int(payload.get("used_count", 0) or 0),
        skipped_count=int(payload.get("skipped_count", 0) or 0),
        label=payload.get("label"),
    )


def _capability_list_result(context: ShapeAdapterContext) -> ResultShape:
    payload = context.payload if isinstance(context.payload, dict) else {}
    return CapabilityListResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        grouped_capabilities=dict(payload.get("grouped_capabilities") or {}),
        capabilities=_rows_from_payload(payload),
        capability_count=int(payload.get("capability_count", 0) or 0),
    )


def _process_list_result(context: ShapeAdapterContext) -> ResultShape:
    payload = context.payload if isinstance(context.payload, dict) else {}
    rows = _rows_from_payload(payload)
    return ProcessListResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        processes=rows,
        pattern=payload.get("pattern"),
        **_shape_metadata(context.result, payload, rows),
    )


def _scalar_result(context: ShapeAdapterContext) -> ResultShape:
    payload = context.payload
    value = payload.get("value") if isinstance(payload, dict) and "value" in payload else payload
    return ScalarResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        value=value,
        label=payload.get("label") if isinstance(payload, dict) else None,
        unit=payload.get("unit") if isinstance(payload, dict) else None,
    )


def _message_text_result(context: ShapeAdapterContext) -> ResultShape:
    payload = context.payload if isinstance(context.payload, dict) else {}
    return TextResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        text=str(payload.get("message") or ""),
    )


def _preview_text_result(context: ShapeAdapterContext) -> ResultShape:
    payload = context.payload if isinstance(context.payload, dict) else {}
    return TextResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        text=str(payload.get("preview_text") or ""),
        **_shape_metadata(context.result, payload),
    )


def _command_output_result(context: ShapeAdapterContext) -> ResultShape:
    payload = context.payload if isinstance(context.payload, dict) else {}
    stdout = str(payload.get("stdout") or "")
    stderr = str(payload.get("stderr") or "")
    output = str(payload.get("output") or "")
    text = stdout or output or stderr
    try:
        exit_code = int(payload.get("exit_code", 0) or 0)
    except (TypeError, ValueError):
        exit_code = 0
    return CommandOutputResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        text=text.rstrip("\n"),
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        metadata={"exit_code": exit_code, "stderr_present": bool(stderr.strip())},
        **_shape_metadata(context.result, payload),
    )


def _file_tree_result(context: ShapeAdapterContext) -> ResultShape:
    payload = context.payload if isinstance(context.payload, dict) else {}
    rows = [dict(item) for item in payload.get("tree", []) if isinstance(item, dict)]
    return FileTreeResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        path=payload.get("path"),
        nodes=rows,
        **_shape_metadata(context.result, payload, rows),
    )


def _json_result(context: ShapeAdapterContext) -> ResultShape:
    return JsonResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        data=context.payload,
        **_shape_metadata(context.result, context.payload),
    )


def _text_result(context: ShapeAdapterContext) -> ResultShape:
    return TextResult(
        node_id=context.result.node_id,
        capability_id=context.capability_id,
        operation_id=context.operation_id,
        title=context.title,
        text=str(context.payload if context.payload is not None else context.result.error or ""),
    )


def build_default_shape_adapter_registry() -> ShapeAdapterRegistry:
    """Return the default ordered registry of shape adapters."""

    registry = ShapeAdapterRegistry()
    for adapter in [
        ShapeAdapter(
            adapter_id="execution.error",
            output_shape_type="error",
            normalize=_error_result,
            priority=0,
            accepted_statuses=("error", "skipped"),
        ),
        ShapeAdapter(
            adapter_id="runtime.describe_capabilities",
            output_shape_type="capability_list",
            normalize=_capability_list_result,
            priority=10,
            accepted_capability_ids=("runtime.describe_capabilities",),
            matcher=_is_dict_payload,
        ),
        ShapeAdapter(
            adapter_id="payload.matches",
            output_shape_type="record_list",
            normalize=_record_list_result,
            priority=40,
            matcher=_has_list_key("matches"),
        ),
        ShapeAdapter(
            adapter_id="payload.rows",
            output_shape_type="table",
            normalize=_table_result,
            priority=40,
            matcher=_has_list_key("rows"),
        ),
        ShapeAdapter(
            adapter_id="payload.entries",
            output_shape_type="record_list",
            normalize=_record_list_result,
            priority=40,
            matcher=_has_list_key("entries"),
        ),
        ShapeAdapter(
            adapter_id="payload.processes",
            output_shape_type="process_list",
            normalize=_process_list_result,
            priority=40,
            matcher=_has_list_key("processes"),
        ),
        ShapeAdapter(
            adapter_id="payload.content_preview",
            output_shape_type="file_content",
            normalize=_file_content_result,
            priority=50,
            required_payload_keys=("content_preview",),
        ),
        ShapeAdapter(
            adapter_id="payload.aggregate_value",
            output_shape_type="aggregate",
            normalize=_aggregate_result,
            priority=50,
            required_payload_keys=("operation", "value"),
        ),
        ShapeAdapter(
            adapter_id="payload.scalar_value",
            output_shape_type="scalar",
            normalize=_scalar_result,
            priority=55,
            matcher=_has_value_without_operation,
        ),
        ShapeAdapter(
            adapter_id="payload.message",
            output_shape_type="text",
            normalize=_message_text_result,
            priority=60,
            required_payload_keys=("message",),
        ),
        ShapeAdapter(
            adapter_id="payload.preview_text",
            output_shape_type="text",
            normalize=_preview_text_result,
            priority=60,
            required_payload_keys=("preview_text",),
        ),
        ShapeAdapter(
            adapter_id="payload.command_output",
            output_shape_type="command_output",
            normalize=_command_output_result,
            priority=65,
            required_payload_keys=("stdout", "stderr", "exit_code"),
        ),
        ShapeAdapter(
            adapter_id="payload.file_tree",
            output_shape_type="file_tree",
            normalize=_file_tree_result,
            priority=60,
            matcher=_has_list_key("tree"),
        ),
        ShapeAdapter(
            adapter_id="payload.list_records",
            output_shape_type="record_list",
            normalize=_record_list_result,
            priority=70,
            matcher=_is_list_of_records,
        ),
        ShapeAdapter(
            adapter_id="payload.unknown_dict",
            output_shape_type="json",
            normalize=_json_result,
            priority=90,
            matcher=_is_dict_payload,
        ),
        ShapeAdapter(
            adapter_id="payload.scalar",
            output_shape_type="scalar",
            normalize=_scalar_result,
            priority=90,
            matcher=_is_scalar_payload,
        ),
        ShapeAdapter(
            adapter_id="payload.list",
            output_shape_type="json",
            normalize=_json_result,
            priority=90,
            matcher=_is_list_payload,
        ),
        ShapeAdapter(
            adapter_id="payload.any",
            output_shape_type="text",
            normalize=_text_result,
            priority=999,
            accepted_statuses=(),
        ),
    ]:
        registry.register(adapter)
    return registry


__all__ = [
    "ShapeAdapter",
    "ShapeAdapterContext",
    "ShapeAdapterRegistry",
    "build_default_shape_adapter_registry",
]
