"""Typed stages for the output display pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.core.ids import new_id
from agent_runtime.core.types import ExecutionResult, ResultBundle
from agent_runtime.output_pipeline.redaction import Redactor
from agent_runtime.output_pipeline.result_shapes import ResultShape, normalize_execution_result


def utc_now_iso() -> str:
    """Return a UTC ISO timestamp for output phase records."""

    return datetime.now(UTC).isoformat()


class PayloadStats(BaseModel):
    """Bounded payload statistics propagated through the output pipeline."""

    model_config = ConfigDict(extra="forbid")

    truncated: bool = False
    preview_count: int | None = None
    total_count: int | None = None
    bytes: int | None = None
    keys: list[str] = Field(default_factory=list)


class CollectedResult(BaseModel):
    """One execution result collected for display planning."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    status: str
    capability_id: str | None = None
    operation_id: str | None = None
    data_ref: str | None = None
    timestamp: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)
    safe_preview: Any = None
    error: str | None = None
    raw_available: bool = False
    payload_stats: PayloadStats = Field(default_factory=PayloadStats)

    def as_safe_preview_record(self) -> dict[str, Any]:
        """Return the LLM-facing safe preview record."""

        return {
            "node_id": self.node_id,
            "status": self.status,
            "data_ref": self.data_ref,
            "preview": self.safe_preview,
            "shape_type": self.metadata.get("shape_type"),
            "capability_id": self.capability_id,
            "operation_id": self.operation_id,
            "error": self.error,
            "timestamp": self.timestamp,
            "truncated": self.payload_stats.truncated,
            "preview_count": self.payload_stats.preview_count,
            "total_count": self.payload_stats.total_count,
            "bytes": self.payload_stats.bytes,
        }


class ResultCollectionOutput(BaseModel):
    """Output of the result collection phase."""

    model_config = ConfigDict(extra="forbid")

    phase: str = "result_collection"
    collected_at: str = Field(default_factory=utc_now_iso)
    results: list[CollectedResult] = Field(default_factory=list)

    def safe_previews(self) -> list[dict[str, Any]]:
        """Return safe preview dictionaries for advisor input."""

        return [result.as_safe_preview_record() for result in self.results]


class ShapeNormalizationOutput(BaseModel):
    """Output of the shape normalization phase."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    phase: str = "shape_normalization"
    normalized_at: str = Field(default_factory=utc_now_iso)
    shapes: list[ResultShape] = Field(default_factory=list)
    source_lookup: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def shape_summaries(self) -> list[dict[str, Any]]:
        """Return safe shape metadata for the display advisor."""

        summaries: list[dict[str, Any]] = []
        for shape in self.shapes:
            summaries.append(
                {
                    "node_id": shape.node_id,
                    "shape_type": shape.shape_type,
                    "capability_id": shape.capability_id,
                    "operation_id": shape.operation_id,
                    "metadata": dict(shape.metadata or {}),
                    "truncated": shape.truncated,
                    "preview_count": shape.preview_count,
                    "total_count": shape.total_count,
                    "raw_available": shape.raw_available,
                    "data_ref": shape.data_ref,
                }
            )
        return summaries


class OutputTraceEvent(BaseModel):
    """Structured output-pipeline event for operational debugging."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: new_id("output_evt"))
    request_id: str | None = None
    timestamp: str = Field(default_factory=utc_now_iso)
    phase: str
    title: str
    summary: str = ""
    detail: Any = None
    shape_type: str | None = None
    primitive_candidates: Any = None
    llm_prompt: str | None = None
    llm_response: Any = None
    validation_result: Any = None
    fallback_reason: str | None = None
    selected_primitive: str | None = None
    renderer: str | None = None
    payload_stats: Any = None


def _payload_stats(preview: Any) -> PayloadStats:
    """Extract common preview statistics."""

    if not isinstance(preview, dict):
        return PayloadStats()
    preview_count = preview.get("preview_count")
    total_count = preview.get("total_count")
    if not isinstance(preview_count, int):
        for key in ("matches", "rows", "entries", "records", "processes", "listeners"):
            value = preview.get(key)
            if isinstance(value, list):
                preview_count = len(value)
                break
    if not isinstance(total_count, int):
        for key in (
            "total_matches",
            "total_rows",
            "total_entries",
            "total_records",
            "total_processes",
            "total_listeners",
        ):
            value = preview.get(key)
            if isinstance(value, int):
                total_count = value
                break
    byte_count = preview.get("bytes")
    return PayloadStats(
        truncated=bool(preview.get("truncated", False)),
        preview_count=preview_count if isinstance(preview_count, int) else None,
        total_count=total_count if isinstance(total_count, int) else None,
        bytes=byte_count if isinstance(byte_count, int) else None,
        keys=sorted(str(key) for key in preview.keys()),
    )


def _result_timestamp(result: ExecutionResult) -> str:
    """Return the best available result timestamp."""

    for key in ("completed_at", "timestamp", "created_at"):
        value = result.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return utc_now_iso()


class ResultCollectionPhase:
    """Collect execution results without losing safe structured previews."""

    def __init__(self, redactor: Redactor | None = None) -> None:
        self.redactor = redactor or Redactor()

    def collect(self, result_bundle: ResultBundle) -> ResultCollectionOutput:
        """Collect execution results into safe output-pipeline records."""

        collected: list[CollectedResult] = []
        for result in result_bundle.results:
            shape = normalize_execution_result(result)
            preview = self.redactor.redact(result.data_preview) if result.data_preview is not None else None
            metadata = dict(result.metadata or {})
            metadata["shape_type"] = shape.shape_type
            collected.append(
                CollectedResult(
                    node_id=result.node_id,
                    status=result.status,
                    capability_id=shape.capability_id,
                    operation_id=shape.operation_id,
                    data_ref=result.data_ref.ref_id if result.data_ref is not None else None,
                    timestamp=_result_timestamp(result),
                    metadata=metadata,
                    safe_preview=preview,
                    error=result.error,
                    raw_available=result.data_ref is not None,
                    payload_stats=_payload_stats(preview),
                )
            )
        return ResultCollectionOutput(results=collected)


class ShapeNormalizationPhase:
    """Normalize collected results into semantic ResultShape records."""

    def normalize(
        self,
        *,
        result_bundle: ResultBundle,
        collection: ResultCollectionOutput,
        result_store=None,
        allow_full_output_access: bool = False,
    ) -> ShapeNormalizationOutput:
        """Return shapes and lookup records for rendering."""

        result_by_node = {result.node_id: result for result in result_bundle.results}
        store_for_shapes = result_store if allow_full_output_access else None
        shapes: list[ResultShape] = []
        lookup: dict[str, dict[str, Any]] = {}
        for collected in collection.results:
            result = result_by_node.get(collected.node_id)
            if result is None:
                continue
            payload = collected.safe_preview
            if allow_full_output_access and collected.data_ref and result_store is not None:
                try:
                    payload = result_store.get(collected.data_ref)
                except Exception:
                    payload = collected.safe_preview
            shape = normalize_execution_result(result, store_for_shapes)
            shapes.append(shape)
            record = {
                "node_id": collected.node_id,
                "data_ref": collected.data_ref,
                "payload": payload,
                "shape": shape,
                "status": collected.status,
                "error": collected.error,
                "metadata": dict(collected.metadata),
                "collection": collected.model_dump(mode="json"),
            }
            lookup[f"node:{collected.node_id}"] = record
            if collected.data_ref is not None:
                lookup[f"data:{collected.data_ref}"] = record
        return ShapeNormalizationOutput(shapes=shapes, source_lookup=lookup)


__all__ = [
    "CollectedResult",
    "OutputTraceEvent",
    "PayloadStats",
    "ResultCollectionOutput",
    "ResultCollectionPhase",
    "ShapeNormalizationOutput",
    "ShapeNormalizationPhase",
]
