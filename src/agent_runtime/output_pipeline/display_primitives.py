"""Internal display primitive registry for output planning."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.core.errors import ValidationError


class DisplayPrimitiveManifest(BaseModel):
    """Presentation-only primitive contract exposed to display planning."""

    model_config = ConfigDict(extra="forbid")

    primitive_id: str
    description: str
    accepted_shape_types: list[str] = Field(default_factory=list)
    accepted_capability_ids: list[str] = Field(default_factory=list)
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    renderer: str
    display_type: str
    required_fields: list[str] = Field(default_factory=list)
    optional_fields: list[str] = Field(default_factory=list)
    max_preview_rows: int | None = None
    safety_policy: dict[str, Any] = Field(default_factory=dict)
    examples: list[dict[str, Any]] = Field(default_factory=list)

    def accepts(self, *, shape_type: str | None, capability_id: str | None) -> bool:
        """Return whether this primitive can render one source."""

        if shape_type is not None and shape_type in set(self.accepted_shape_types):
            return True
        if capability_id is not None and capability_id in set(self.accepted_capability_ids):
            return True
        return False


class DisplayPrimitiveRegistry:
    """Registry of local display primitives, separate from execution capabilities."""

    def __init__(self, manifests: list[DisplayPrimitiveManifest] | None = None) -> None:
        self._manifests: dict[str, DisplayPrimitiveManifest] = {}
        for manifest in manifests or []:
            self.register(manifest)

    def register(self, manifest: DisplayPrimitiveManifest) -> None:
        """Register one primitive manifest."""

        if manifest.primitive_id in self._manifests:
            raise ValidationError(f"Duplicate display primitive id: {manifest.primitive_id}")
        self._manifests[manifest.primitive_id] = manifest

    def get(self, primitive_id: str) -> DisplayPrimitiveManifest:
        """Return one primitive manifest by id."""

        try:
            return self._manifests[primitive_id]
        except KeyError as exc:
            raise ValidationError(f"Unknown display primitive id: {primitive_id}") from exc

    def list_manifests(self) -> list[DisplayPrimitiveManifest]:
        """Return manifests in deterministic registration order."""

        return list(self._manifests.values())

    def compatible_manifests(
        self,
        *,
        shape_type: str | None,
        capability_id: str | None,
        include_multi_section: bool = False,
    ) -> list[DisplayPrimitiveManifest]:
        """Return primitives that can render one source."""

        manifests: list[DisplayPrimitiveManifest] = []
        for manifest in self.list_manifests():
            if manifest.primitive_id == "multi_section" and not include_multi_section:
                continue
            if manifest.accepts(shape_type=shape_type, capability_id=capability_id):
                manifests.append(manifest)
        return manifests


def build_default_display_primitive_registry() -> DisplayPrimitiveRegistry:
    """Build the default presentation primitive registry."""

    return DisplayPrimitiveRegistry(
        [
            DisplayPrimitiveManifest(
                primitive_id="markdown",
                description="Render safe user-facing markdown or prose.",
                accepted_shape_types=[
                    "markdown",
                    "text",
                    "table",
                    "record_list",
                    "scalar",
                    "aggregate",
                    "file_content",
                    "directory_listing",
                    "process_list",
                    "capability_list",
                    "error",
                ],
                accepted_capability_ids=[],
                parameters_schema={},
                renderer="markdown",
                display_type="markdown",
                safety_policy={"full_payload": False},
                examples=[{"shape_type": "markdown", "display_type": "markdown"}],
            ),
            DisplayPrimitiveManifest(
                primitive_id="table",
                description="Render tabular data as a compact table.",
                accepted_shape_types=[
                    "table",
                    "record_list",
                    "directory_listing",
                    "process_list",
                    "capability_list",
                ],
                accepted_capability_ids=[],
                parameters_schema={"columns": {"type": "array", "items": {"type": "string"}}},
                renderer="table",
                display_type="table",
                required_fields=["rows"],
                max_preview_rows=100,
                safety_policy={"full_payload": False},
                examples=[{"shape_type": "table", "parameters": {"columns": ["name", "path"]}}],
            ),
            DisplayPrimitiveManifest(
                primitive_id="record_list",
                description="Render repeated record data with stable columns.",
                accepted_shape_types=["record_list", "process_list", "capability_list"],
                accepted_capability_ids=[],
                parameters_schema={"columns": {"type": "array", "items": {"type": "string"}}},
                renderer="record_table",
                display_type="table",
                required_fields=["records"],
                max_preview_rows=100,
                safety_policy={"full_payload": False},
                examples=[{"shape_type": "record_list", "display_type": "table"}],
            ),
            DisplayPrimitiveManifest(
                primitive_id="code_block",
                description="Render text or file content in a fenced code block.",
                accepted_shape_types=["text", "file_content", "command_output"],
                accepted_capability_ids=[],
                parameters_schema={"language": {"type": "string"}},
                renderer="code_block",
                display_type="code_block",
                required_fields=["content"],
                safety_policy={"full_payload": False},
            ),
            DisplayPrimitiveManifest(
                primitive_id="json_view",
                description="Render arbitrary structured JSON for inspection.",
                accepted_shape_types=["json"],
                accepted_capability_ids=[],
                parameters_schema={},
                renderer="json",
                display_type="json_view",
                required_fields=["data"],
                safety_policy={"full_payload": False},
                examples=[{"shape_type": "json", "display_type": "json_view"}],
            ),
            DisplayPrimitiveManifest(
                primitive_id="file_tree",
                description="Render hierarchical file tree nodes.",
                accepted_shape_types=["file_tree"],
                accepted_capability_ids=[],
                parameters_schema={},
                renderer="file_tree",
                display_type="table",
                required_fields=["nodes"],
                max_preview_rows=200,
                safety_policy={"full_payload": False},
            ),
            DisplayPrimitiveManifest(
                primitive_id="directory_listing",
                description="Render directory entries with file metadata columns.",
                accepted_shape_types=["directory_listing"],
                accepted_capability_ids=[],
                parameters_schema={},
                renderer="directory_table",
                display_type="table",
                required_fields=["entries"],
                optional_fields=["name", "path", "type", "size", "modified_time"],
                max_preview_rows=100,
                safety_policy={"full_payload": False},
            ),
            DisplayPrimitiveManifest(
                primitive_id="scalar",
                description="Render a single scalar value with optional label and unit.",
                accepted_shape_types=["scalar"],
                accepted_capability_ids=[],
                parameters_schema={},
                renderer="scalar",
                display_type="plain_text",
                required_fields=["value"],
                safety_policy={"full_payload": False},
            ),
            DisplayPrimitiveManifest(
                primitive_id="aggregate",
                description="Render an aggregate value with compact calculation metadata.",
                accepted_shape_types=["aggregate"],
                accepted_capability_ids=[],
                parameters_schema={},
                renderer="aggregate",
                display_type="plain_text",
                required_fields=["value"],
                safety_policy={"full_payload": False},
            ),
            DisplayPrimitiveManifest(
                primitive_id="error",
                description="Render a safe error message.",
                accepted_shape_types=["error"],
                accepted_capability_ids=[],
                parameters_schema={},
                renderer="error",
                display_type="plain_text",
                required_fields=["message"],
                safety_policy={"full_payload": False},
            ),
            DisplayPrimitiveManifest(
                primitive_id="raw_payload",
                description="Render a raw payload preview for local operational debugging when policy permits it.",
                accepted_shape_types=[
                    "markdown",
                    "text",
                    "table",
                    "record_list",
                    "directory_listing",
                    "file_tree",
                    "file_content",
                    "json",
                    "scalar",
                    "aggregate",
                    "error",
                    "multi_section",
                    "process_list",
                    "capability_list",
                ],
                accepted_capability_ids=[],
                parameters_schema={},
                renderer="raw_payload",
                display_type="json_view",
                safety_policy={"full_payload": True, "debug_only": True},
            ),
            DisplayPrimitiveManifest(
                primitive_id="multi_section",
                description="Render multiple validated source sections in one response.",
                accepted_shape_types=["multi_section"],
                accepted_capability_ids=[],
                parameters_schema={},
                renderer="multi_section",
                display_type="multi_section",
                safety_policy={"full_payload": False},
            ),
        ]
    )


__all__ = [
    "DisplayPrimitiveManifest",
    "DisplayPrimitiveRegistry",
    "build_default_display_primitive_registry",
]
