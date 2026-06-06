"""Typed result-shape normalization for deterministic rendering."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.core.types import ExecutionResult


class _BaseResultShape(BaseModel):
    """Shared shape metadata."""

    model_config = ConfigDict(extra="forbid")

    shape_type: str
    node_id: str
    capability_id: str | None = None
    operation_id: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    preview_count: int | None = None
    total_count: int | None = None
    raw_available: bool = False
    data_ref: str | None = None


class TextResult(_BaseResultShape):
    shape_type: Literal["text"] = "text"
    text: str


class CommandOutputResult(_BaseResultShape):
    shape_type: Literal["command_output"] = "command_output"
    text: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


class MarkdownResult(_BaseResultShape):
    shape_type: Literal["markdown"] = "markdown"
    markdown: str


class TableResult(_BaseResultShape):
    shape_type: Literal["table"] = "table"
    rows: list[dict[str, Any]] = Field(default_factory=list)


class RecordListResult(_BaseResultShape):
    shape_type: Literal["record_list"] = "record_list"
    records: list[dict[str, Any]] = Field(default_factory=list)


class ScalarResult(_BaseResultShape):
    shape_type: Literal["scalar"] = "scalar"
    value: Any = None
    label: str | None = None
    unit: str | None = None


class AggregateResult(_BaseResultShape):
    shape_type: Literal["aggregate"] = "aggregate"
    operation: str
    field: str | None = None
    value: Any = None
    unit: str | None = None
    row_count: int = 0
    used_count: int = 0
    skipped_count: int = 0
    label: str | None = None


class FileContentResult(_BaseResultShape):
    shape_type: Literal["file_content"] = "file_content"
    path: str | None = None
    content_preview: str


class DirectoryListingResult(_BaseResultShape):
    shape_type: Literal["directory_listing"] = "directory_listing"
    path: str | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)


class FileTreeResult(_BaseResultShape):
    shape_type: Literal["file_tree"] = "file_tree"
    path: str | None = None
    nodes: list[dict[str, Any]] = Field(default_factory=list)


class ProcessListResult(_BaseResultShape):
    shape_type: Literal["process_list"] = "process_list"
    processes: list[dict[str, Any]] = Field(default_factory=list)
    pattern: str | None = None


class CapabilityListResult(_BaseResultShape):
    shape_type: Literal["capability_list"] = "capability_list"
    grouped_capabilities: dict[str, list[Any]] = Field(default_factory=dict)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    capability_count: int = 0


class ErrorResult(_BaseResultShape):
    shape_type: Literal["error"] = "error"
    message: str


class JsonResult(_BaseResultShape):
    shape_type: Literal["json"] = "json"
    data: Any = None


class MultiSectionResult(_BaseResultShape):
    shape_type: Literal["multi_section"] = "multi_section"
    sections: list["_RenderableResultShape"] = Field(default_factory=list)


_RenderableResultShape: TypeAlias = (
    TextResult
    | CommandOutputResult
    | MarkdownResult
    | TableResult
    | RecordListResult
    | ScalarResult
    | AggregateResult
    | FileContentResult
    | DirectoryListingResult
    | FileTreeResult
    | ProcessListResult
    | CapabilityListResult
    | ErrorResult
    | JsonResult
)

ResultShape: TypeAlias = _RenderableResultShape | MultiSectionResult


def _sanitize_error_message(message: str | None) -> str:
    """Collapse raw traceback-like text into a safe user-facing message."""

    text = str(message or "").strip()
    if not text:
        return "Execution failed."
    if "Traceback (most recent call last)" in text:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            if ":" in line and not line.startswith("Traceback"):
                return line
        return "Execution failed."
    return text.splitlines()[0].strip()


def _payload_for_normalization(result: ExecutionResult, result_store=None) -> Any:
    """Resolve the most useful payload available for normalization."""

    if result_store is not None and result.data_ref is not None:
        try:
            return result_store.get(result.data_ref.ref_id)
        except Exception:
            pass
    if result.data_preview is not None:
        return result.data_preview
    return None


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Extract row-like dictionaries from supported payload families."""

    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return [dict(item) for item in payload]
    if not isinstance(payload, dict):
        return []
    for key in ("rows", "entries", "processes", "listeners"):
        value = payload.get(key)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return [dict(item) for item in value]
    matches = payload.get("matches")
    if isinstance(matches, list):
        rows: list[dict[str, Any]] = []
        for item in matches:
            if isinstance(item, dict):
                rows.append(dict(item))
            else:
                rows.append({"path": item})
        return rows
    return []


def _count_metadata(payload: Any, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return shared truncation/count metadata from a normalized payload."""

    if not isinstance(payload, dict):
        return {}
    row_count = len(rows or [])
    total_count = None
    for key in (
        "total_count",
        "total_matches",
        "total_rows",
        "total_entries",
        "total_records",
        "total_processes",
        "total_listeners",
    ):
        if isinstance(payload.get(key), int):
            total_count = int(payload[key])
            break
    if total_count is None and row_count:
        total_count = row_count
    preview_count = payload.get("preview_count")
    if not isinstance(preview_count, int):
        preview_count = row_count if row_count else None
    return {
        "truncated": bool(payload.get("truncated", False)),
        "preview_count": preview_count,
        "total_count": total_count,
    }


def _shape_metadata(result: ExecutionResult, payload: Any, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return shared shape metadata fields."""

    return {
        **_count_metadata(payload, rows),
        "raw_available": result.data_ref is not None,
        "data_ref": result.data_ref.ref_id if result.data_ref is not None else None,
    }


def normalize_execution_result(result: ExecutionResult, result_store=None) -> ResultShape:
    """Normalize one execution result into a semantic result shape."""

    capability_id = str(result.metadata.get("capability_id") or "").strip() or None
    operation_id = str(result.metadata.get("operation_id") or "").strip() or None
    payload = _payload_for_normalization(result, result_store)
    title = None
    if isinstance(payload, dict):
        title = str(payload.get("title") or "").strip() or None

    from agent_runtime.output_pipeline.shape_adapters import (
        ShapeAdapterContext,
        build_default_shape_adapter_registry,
    )

    return build_default_shape_adapter_registry().normalize(
        ShapeAdapterContext(
            result=result,
            payload=payload,
            capability_id=capability_id,
            operation_id=operation_id,
            title=title,
        )
    )


__all__ = [
    "AggregateResult",
    "CapabilityListResult",
    "CommandOutputResult",
    "DirectoryListingResult",
    "ErrorResult",
    "FileContentResult",
    "FileTreeResult",
    "JsonResult",
    "MarkdownResult",
    "MultiSectionResult",
    "ProcessListResult",
    "RecordListResult",
    "ResultShape",
    "ScalarResult",
    "TableResult",
    "TextResult",
    "normalize_execution_result",
]


MultiSectionResult.model_rebuild()
