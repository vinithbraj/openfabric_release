"""Structured display document contract for UI/rendering targets."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.core.ids import new_id
from agent_runtime.core.types import DisplayPlan
from agent_runtime.output_pipeline.fallback_policy import build_fallback_document_sections
from agent_runtime.output_pipeline.renderers import render_result_shape
from agent_runtime.output_pipeline.result_shapes import ResultShape


class DisplaySection(BaseModel):
    """One structured display section emitted by the output pipeline."""

    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(default_factory=lambda: new_id("display_section"))
    title: str | None = None
    primitive_id: str
    display_type: str
    shape_type: str
    source_node_id: str | None = None
    content: Any = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    language: str | None = None
    truncated: bool = False
    preview_count: int | None = None
    total_count: int | None = None
    data_ref: str | None = None
    raw_available: bool = False


class DisplayDocument(BaseModel):
    """Backend/frontend display contract."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(default_factory=lambda: new_id("display_doc"))
    request_id: str | None = None
    target_ui: str = "openwebui"
    summary: str | None = None
    sections: list[DisplaySection] = Field(default_factory=list)
    raw_available: bool = False
    trace_refs: list[str] = Field(default_factory=list)


def _shape_rows(shape: ResultShape) -> list[dict[str, Any]]:
    """Extract repeated row records from a result shape without capability branching."""

    for attr in ("rows", "records", "entries", "processes", "capabilities", "nodes"):
        value = getattr(shape, attr, None)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return [dict(item) for item in value]
    return []


def _shape_columns(rows: list[dict[str, Any]], requested: list[str] | None = None) -> list[str]:
    """Return stable display columns."""

    if requested:
        return [column for column in requested if any(column in row for row in rows)]
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(str(key))
    return columns


def _shape_content(shape: ResultShape) -> Any:
    """Return structured scalar/text content from one shape."""

    for attr in ("text", "markdown", "content_preview", "value", "message", "data"):
        if hasattr(shape, attr):
            return getattr(shape, attr)
    rendered = render_result_shape(shape)
    if str(rendered or "").strip():
        return rendered
    return None


def _source_record(section: dict[str, Any], source_lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Resolve a display section source."""

    source_node_id = section.get("source_node_id")
    source_data_ref = section.get("source_data_ref")
    if source_node_id is not None:
        return source_lookup[f"node:{source_node_id}"]
    if source_data_ref is not None:
        return source_lookup[f"data:{source_data_ref}"]
    raise ValueError("Display plan section must reference source_node_id or source_data_ref.")


def build_display_document(
    *,
    display_plan: DisplayPlan,
    source_lookup: dict[str, dict[str, Any]],
    request_id: str | None = None,
    target_ui: str = "openwebui",
    summary: str | None = None,
    allow_raw_access: bool = False,
) -> DisplayDocument:
    """Build a structured DisplayDocument from a validated display plan."""

    sections: list[DisplaySection] = []
    plan_sections = display_plan.sections
    if not plan_sections and source_lookup:
        plan_sections = build_fallback_document_sections(display_plan, source_lookup)

    for section in plan_sections:
        record = _source_record(section, source_lookup)
        shape: ResultShape = record["shape"]
        rows = _shape_rows(shape)
        parameters = section.get("parameters") if isinstance(section.get("parameters"), dict) else {}
        requested_columns = parameters.get("columns") if isinstance(parameters.get("columns"), list) else None
        primitive_id = str(section.get("primitive_id") or display_plan.primitive_id or display_plan.display_type)
        display_type = str(section.get("display_type") or display_plan.display_type)
        sections.append(
            DisplaySection(
                title=section.get("title") or display_plan.title or getattr(shape, "title", None),
                primitive_id=primitive_id,
                display_type=display_type,
                shape_type=str(getattr(shape, "shape_type", "unknown")),
                source_node_id=str(record.get("node_id")) if record.get("node_id") is not None else None,
                content=_shape_content(shape),
                rows=rows,
                columns=_shape_columns(rows, requested_columns),
                metadata=dict(getattr(shape, "metadata", {}) or {}),
                language=str(parameters.get("language")) if parameters.get("language") else None,
                truncated=bool(getattr(shape, "truncated", False)),
                preview_count=getattr(shape, "preview_count", None),
                total_count=getattr(shape, "total_count", None),
                data_ref=getattr(shape, "data_ref", None) or record.get("data_ref"),
                raw_available=bool(
                    allow_raw_access
                    and (getattr(shape, "raw_available", False) or record.get("data_ref"))
                ),
            )
        )

    return DisplayDocument(
        request_id=request_id,
        target_ui=target_ui,
        summary=summary,
        sections=sections,
        raw_available=any(section.raw_available for section in sections),
    )


def render_display_document_markdown(document: DisplayDocument) -> str:
    """Render a DisplayDocument to OpenWebUI-compatible markdown/text."""

    blocks: list[str] = []
    for section in document.sections:
        pseudo_shape = _section_to_shape_text(section)
        blocks.append(pseudo_shape)
    return "\n\n".join(block for block in blocks if str(block).strip()) or "No results available."


def _section_to_shape_text(section: DisplaySection) -> str:
    """Render one structured section deterministically."""

    from agent_runtime.output_pipeline.result_shapes import (
        CommandOutputResult,
        JsonResult,
        RecordListResult,
        TextResult,
    )

    if section.rows:
        return render_result_shape(
            RecordListResult(
                node_id=section.source_node_id or section.section_id,
                title=section.title,
                records=section.rows,
                truncated=section.truncated,
                preview_count=section.preview_count,
                total_count=section.total_count,
                raw_available=section.raw_available,
                data_ref=section.data_ref,
            )
        )
    if isinstance(section.content, (dict, list)) or section.display_type == "json_view":
        return render_result_shape(
            JsonResult(
                node_id=section.source_node_id or section.section_id,
                title=section.title,
                data=section.content,
                truncated=section.truncated,
                preview_count=section.preview_count,
                total_count=section.total_count,
                raw_available=section.raw_available,
                data_ref=section.data_ref,
            )
        )
    if section.shape_type == "command_output":
        return render_result_shape(
            CommandOutputResult(
                node_id=section.source_node_id or section.section_id,
                title=section.title,
                text="" if section.content is None else str(section.content),
                stdout="" if section.content is None else str(section.content),
                stderr=str(section.metadata.get("stderr") or ""),
                exit_code=int(section.metadata.get("exit_code", 0) or 0),
                metadata=dict(section.metadata),
                truncated=section.truncated,
                preview_count=section.preview_count,
                total_count=section.total_count,
                raw_available=section.raw_available,
                data_ref=section.data_ref,
            ),
            display_type=section.display_type,
            parameters={"language": section.language} if section.language else {},
        )
    return render_result_shape(
        TextResult(
            node_id=section.source_node_id or section.section_id,
            title=section.title,
            text="" if section.content is None else str(section.content),
            truncated=section.truncated,
            preview_count=section.preview_count,
            total_count=section.total_count,
            raw_available=section.raw_available,
            data_ref=section.data_ref,
        ),
        display_type=section.display_type,
        parameters={"language": section.language} if section.language else {},
    )


__all__ = [
    "DisplayDocument",
    "DisplaySection",
    "build_display_document",
    "render_display_document_markdown",
]
