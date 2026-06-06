"""Output composition pipeline package."""

from agent_runtime.output_pipeline.display_selection import (
    DisplaySelectionAdvisor,
    DisplaySelectionInput,
    DisplayValidationPhase,
    select_display_plan,
)
from agent_runtime.output_pipeline.orchestrator import (
    OutputPipelineOrchestrator,
    compose_output,
)
from agent_runtime.output_pipeline.renderers import LocalAgentUIRenderer, OpenWebUIRenderer
from agent_runtime.output_pipeline.stages import (
    CollectedResult,
    OutputTraceEvent,
    PayloadStats,
    ResultCollectionOutput,
    ResultCollectionPhase,
    ShapeNormalizationOutput,
    ShapeNormalizationPhase,
)
from agent_runtime.output_pipeline.display_primitives import (
    DisplayPrimitiveManifest,
    DisplayPrimitiveRegistry,
    build_default_display_primitive_registry,
)
from agent_runtime.output_pipeline.display_document import (
    DisplayDocument,
    DisplaySection,
    build_display_document,
    render_display_document_markdown,
)
from agent_runtime.output_pipeline.result_shapes import (
    AggregateResult,
    CapabilityListResult,
    CommandOutputResult,
    DirectoryListingResult,
    ErrorResult,
    FileContentResult,
    FileTreeResult,
    JsonResult,
    MarkdownResult,
    MultiSectionResult,
    ProcessListResult,
    RecordListResult,
    ResultShape,
    ScalarResult,
    TableResult,
    TextResult,
    normalize_execution_result,
)
from agent_runtime.output_pipeline.shape_adapters import (
    ShapeAdapter,
    ShapeAdapterContext,
    ShapeAdapterRegistry,
    build_default_shape_adapter_registry,
)

__all__ = [
    "AggregateResult",
    "CapabilityListResult",
    "CommandOutputResult",
    "CollectedResult",
    "DisplaySelectionInput",
    "DisplayPrimitiveManifest",
    "DisplayPrimitiveRegistry",
    "DisplayDocument",
    "DisplaySection",
    "DisplaySelectionAdvisor",
    "DisplayValidationPhase",
    "DirectoryListingResult",
    "ErrorResult",
    "FileContentResult",
    "FileTreeResult",
    "JsonResult",
    "MarkdownResult",
    "MultiSectionResult",
    "LocalAgentUIRenderer",
    "OpenWebUIRenderer",
    "OutputTraceEvent",
    "OutputPipelineOrchestrator",
    "PayloadStats",
    "ProcessListResult",
    "RecordListResult",
    "ResultShape",
    "ResultCollectionOutput",
    "ResultCollectionPhase",
    "ScalarResult",
    "ShapeNormalizationOutput",
    "ShapeNormalizationPhase",
    "ShapeAdapter",
    "ShapeAdapterContext",
    "ShapeAdapterRegistry",
    "TableResult",
    "TextResult",
    "compose_output",
    "build_display_document",
    "build_default_display_primitive_registry",
    "build_default_shape_adapter_registry",
    "normalize_execution_result",
    "render_display_document_markdown",
    "select_display_plan",
]
