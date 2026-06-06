"""Orchestrator for the output composition pipeline."""

from __future__ import annotations

from typing import Any

from agent_runtime.core.config import RuntimeConfig
from agent_runtime.core.ids import new_id
from agent_runtime.core.types import ActionDAG, RenderedOutput, ResultBundle, UserRequest
from agent_runtime.llm.reproducibility import (
    PlanningTrace,
    PlanningTraceEntry,
    append_trace_entry,
    llm_client_metadata,
)
from agent_runtime.observability import (
    EVENT_OUTPUT_SHAPE_SELECTED,
    EVENT_RENDERING_COMPLETED,
    ObservabilityContext,
    STAGE_OUTPUT_PLANNING,
    STAGE_RENDERING,
)
from agent_runtime.operator.final_formatter import (
    FormatterSource,
    run_llm_final_formatter,
)
from agent_runtime.operator.pipeline import (
    OperatorPlanValidator,
    PythonTransformExecutor,
)
from agent_runtime.settings_consolidation import operator_profile_policy
from agent_runtime.output_pipeline.display_selection import (
    DisplaySelectionAdvisor,
    DisplaySelectionInput,
    DisplaySelector,
    DisplayValidationPhase,
)
from agent_runtime.output_pipeline.fallback_policy import (
    render_bundle_status_fallback,
    render_fallback_output,
)
from agent_runtime.output_pipeline.redaction import Redactor
from agent_runtime.output_pipeline.renderers import (
    LocalAgentUIRenderer,
    OpenWebUIRenderer,
    render_display_plan,
)
from agent_runtime.output_pipeline.result_shapes import normalize_execution_result
from agent_runtime.output_pipeline.stages import (
    OutputTraceEvent,
    ResultCollectionPhase,
    ShapeNormalizationPhase,
)
from agent_runtime.output_pipeline.summarizer import Summarizer


def _result_store_from_request(user_request: UserRequest):
    """Resolve a result store from request context when available."""

    for context in (
        user_request.safety_context,
        user_request.session_context,
        user_request.user_context,
    ):
        store = context.get("result_store")
        if store is not None:
            return store
    return None


def _observability_from_request(user_request: UserRequest) -> ObservabilityContext | None:
    """Resolve a request-scoped observability context when present."""

    for context in (
        user_request.safety_context,
        user_request.session_context,
        user_request.user_context,
    ):
        observability = context.get("observability")
        if isinstance(observability, ObservabilityContext):
            return observability
    return None


def _allow_full_output_access(user_request: UserRequest) -> bool:
    """Return whether full result dereferencing is permitted."""

    for context in (
        user_request.safety_context,
        user_request.session_context,
        user_request.user_context,
    ):
        if bool(context.get("allow_full_output_access", False)):
            return True
    return False


def _runtime_config_from_request(user_request: UserRequest) -> RuntimeConfig:
    """Build a minimal RuntimeConfig for final formatting."""

    for context in (
        user_request.safety_context,
        user_request.session_context,
        user_request.user_context,
    ):
        candidate = context.get("config")
        if isinstance(candidate, RuntimeConfig):
            return candidate
        if isinstance(candidate, dict):
            try:
                return RuntimeConfig.model_validate(candidate)
            except Exception:
                pass
    workspace_root = (
        user_request.session_context.get("workspace_root")
        or user_request.safety_context.get("workspace_root")
        or "."
    )
    return RuntimeConfig(workspace_root=str(workspace_root))


def _node_lookup(dag: ActionDAG) -> dict[str, Any]:
    """Return nodes keyed by node id for display/formatting metadata."""

    return {node.id: node for node in dag.nodes}


def _payload_for_result(
    result: Any,
    *,
    result_store: Any = None,
) -> Any:
    """Resolve the full local payload for formatting when available."""

    if result_store is not None and result.data_ref is not None:
        try:
            return result_store.get(result.data_ref.ref_id)
        except Exception:
            pass
    return result.data_preview


def _formatter_sources_from_bundle(
    dag: ActionDAG,
    result_bundle: ResultBundle,
    *,
    result_store: Any = None,
) -> list[FormatterSource]:
    """Build final-formatter sources from standard pipeline execution results."""

    nodes = _node_lookup(dag)
    sources: list[FormatterSource] = []
    for result in result_bundle.results:
        node = nodes.get(result.node_id)
        metadata = dict(result.metadata or {})
        capability_id = str(metadata.get("capability_id") or getattr(node, "capability_id", "") or "")
        if capability_id not in {
            "operator.shell_command",
            "operator.python_action",
            "operator.python_transform",
        }:
            continue
        payload = _payload_for_result(result, result_store=result_store)
        if not isinstance(payload, dict):
            payload = {"output": payload}
        sources.append(
            FormatterSource(
                source_id=result.node_id,
                label=str(getattr(node, "description", "") or result.node_id),
                kind={
                    "operator.shell_command": "shell_command",
                    "operator.python_action": "python_action",
                    "operator.python_transform": "python_transform",
                }[capability_id],
                status=result.status,
                command=str((getattr(node, "arguments", {}) or {}).get("command") or ""),
                declared_output_shape=str(
                    metadata.get("declared_output_shape")
                    or (getattr(node, "arguments", {}) or {}).get("declared_output_shape")
                    or "text"
                ),
                stdout=str(payload.get("stdout") or ""),
                stderr=str(payload.get("stderr") or ""),
                exit_code=payload.get("exit_code") if isinstance(payload.get("exit_code"), int) else None,
                output=payload.get("output"),
            )
        )
    return sources


def _formatter_display_document(
    user_request: UserRequest,
    content: str,
    *,
    summary: str | None = None,
) -> dict[str, Any]:
    """Return a minimal DisplayDocument for LLM-formatted final output."""

    return {
        "document_id": new_id("display-doc"),
        "request_id": user_request.request_id,
        "target_ui": str(user_request.safety_context.get("target_ui") or "agent_ui"),
        "summary": summary or "Formatted operator output.",
        "sections": [
            {
                "section_id": new_id("display-section"),
                "title": "Final Answer",
                "primitive_id": "markdown",
                "display_type": "markdown",
                "shape_type": "markdown",
                "source_node_id": None,
                "content": content,
                "rows": [],
                "columns": [],
                "metadata": {"formatted": True},
                "language": None,
                "truncated": False,
                "preview_count": None,
                "total_count": None,
                "data_ref": None,
                "raw_available": False,
            }
        ],
        "raw_available": False,
        "trace_refs": [],
    }


def _try_final_formatter(
    user_request: UserRequest,
    dag: ActionDAG,
    result_bundle: ResultBundle,
    llm_client: Any,
    *,
    observability: ObservabilityContext | None = None,
    result_store: Any = None,
) -> str | None:
    """Run a shared final formatter for successful operator-backed results."""

    if result_bundle.status != "success":
        return None
    target_ui = str(
        user_request.safety_context.get("target_ui")
        or user_request.session_context.get("target_ui")
        or "openwebui"
    )
    if target_ui != "agent_ui":
        return None
    sources = _formatter_sources_from_bundle(dag, result_bundle, result_store=result_store)
    if not sources:
        return None
    config = _runtime_config_from_request(user_request)
    profile_policy = operator_profile_policy(
        config.reasoning_profile,
        llm_operator_verbose_enabled=config.llm_operator_verbose_enabled,
    )
    if not profile_policy.run_final_formatter:
        return None
    formatter_result = run_llm_final_formatter(
        user_request=user_request,
        sources=sources,
        llm_client=llm_client,
        validator=OperatorPlanValidator(config),
        python_executor=PythonTransformExecutor(),
        observability=observability,
        stage=STAGE_RENDERING,
    )
    if formatter_result is None:
        return None
    document = _formatter_display_document(
        user_request,
        formatter_result.content,
        summary=result_bundle.safe_summary,
    )
    user_request.safety_context["display_document"] = document
    return formatter_result.content


def _summarize_dag(dag: ActionDAG) -> dict[str, Any]:
    """Build a compact DAG summary for display planning."""

    return {
        "dag_id": dag.dag_id,
        "node_count": len(dag.nodes),
        "requires_confirmation": dag.requires_confirmation,
        "nodes": [
            {
                "node_id": node.id,
                "task_id": node.task_id,
                "semantic_verb": node.semantic_verb,
                "capability_id": node.capability_id,
                "operation_id": node.operation_id,
            }
            for node in dag.nodes
        ],
    }


def _task_intent(dag: ActionDAG) -> dict[str, Any]:
    """Return compact task intent metadata for display planning."""

    return {
        "semantic_verbs": sorted({node.semantic_verb for node in dag.nodes}),
        "capability_ids": [node.capability_id for node in dag.nodes],
        "operation_ids": [node.operation_id for node in dag.nodes],
        "node_count": len(dag.nodes),
    }


def _output_trace(
    *,
    request_id: str | None,
    phase: str,
    title: str,
    summary: str = "",
    detail: Any = None,
    shape_type: str | None = None,
    validation_result: Any = None,
    fallback_reason: str | None = None,
    selected_primitive: str | None = None,
    renderer: str | None = None,
    payload_stats: Any = None,
) -> dict[str, Any]:
    """Return a structured output-pipeline trace event payload."""

    return OutputTraceEvent(
        request_id=request_id,
        phase=phase,
        title=title,
        summary=summary,
        detail=detail,
        shape_type=shape_type,
        validation_result=validation_result,
        fallback_reason=fallback_reason,
        selected_primitive=selected_primitive,
        renderer=renderer,
        payload_stats=payload_stats,
    ).model_dump(mode="json")


def _summarize_results(result_bundle: ResultBundle) -> dict[str, Any]:
    """Build a compact result summary for display planning."""

    status_counts: dict[str, int] = {}
    for result in result_bundle.results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
    return {
        "dag_id": result_bundle.dag_id,
        "status": result_bundle.status,
        "safe_summary": result_bundle.safe_summary,
        "status_counts": status_counts,
        "result_count": len(result_bundle.results),
    }


def _build_safe_previews(result_bundle: ResultBundle, redactor: Redactor) -> list[dict[str, Any]]:
    """Build safe previews for LLM display selection."""

    safe_previews: list[dict[str, Any]] = []
    for result in result_bundle.results:
        shape = normalize_execution_result(result)
        safe_previews.append(
            {
                "node_id": result.node_id,
                "status": result.status,
                "data_ref": result.data_ref.ref_id if result.data_ref is not None else None,
                "preview": redactor.redact(result.data_preview) if result.data_preview is not None else None,
                "shape_type": shape.shape_type,
                "capability_id": shape.capability_id,
                "operation_id": shape.operation_id,
                "error": result.error,
            }
        )
    return safe_previews


def _source_lookup(
    result_bundle: ResultBundle,
    safe_previews: list[dict[str, Any]],
    user_request: UserRequest,
) -> dict[str, dict[str, Any]]:
    """Build source lookup entries from safe previews and optional full data."""

    lookup: dict[str, dict[str, Any]] = {}
    result_store = _result_store_from_request(user_request)
    allow_full = _allow_full_output_access(user_request)

    for preview in safe_previews:
        payload = preview.get("preview")
        data_ref = preview.get("data_ref")
        if allow_full and data_ref and result_store is not None:
            try:
                payload = result_store.get(str(data_ref))
            except Exception:
                payload = preview.get("preview")
        record = {
            "node_id": preview.get("node_id"),
            "data_ref": data_ref,
            "payload": payload,
            "shape": normalize_execution_result(
                next(
                    result
                    for result in result_bundle.results
                    if result.node_id == preview.get("node_id")
                ),
                result_store if allow_full else None,
            ),
            "status": preview.get("status"),
            "error": preview.get("error"),
        }
        node_id = preview.get("node_id")
        if node_id is not None:
            lookup[f"node:{node_id}"] = record
        if data_ref is not None:
            lookup[f"data:{data_ref}"] = record
    return lookup


def compose_output(
    user_request: UserRequest,
    dag: ActionDAG,
    result_bundle: ResultBundle,
    llm_client,
) -> str:
    """Compose final user-facing output from a DAG and result bundle."""

    observability = _observability_from_request(user_request)
    result_store = _result_store_from_request(user_request)
    allow_full = _allow_full_output_access(user_request)
    status_content = render_bundle_status_fallback(
        result_bundle,
        result_store=result_store,
        allow_full_output_access=allow_full,
    )
    if status_content is not None:
        if observability is not None:
            observability.stage_started(
                STAGE_RENDERING,
                "Rendering started",
                "The runtime is rendering a status-level fallback deterministically.",
            )
        if observability is not None:
            observability.info(
                STAGE_RENDERING,
                EVENT_RENDERING_COMPLETED,
                "Rendering completed",
                "The runtime rendered a status-level fallback response deterministically.",
                details={"content_length": len(status_content), "bundle_status": result_bundle.status},
            )
            observability.stage_completed(
                STAGE_RENDERING,
                "Rendering completed",
                "Rendering finished with a status-level fallback view.",
                details={"content_length": len(status_content), "bundle_status": result_bundle.status},
            )
        return status_content

    formatted_content = _try_final_formatter(
        user_request,
        dag,
        result_bundle,
        llm_client,
        observability=observability,
        result_store=result_store,
    )
    if formatted_content is not None:
        if observability is not None:
            observability.stage_started(
                STAGE_RENDERING,
                "Rendering started",
                "The runtime is rendering a formatted operator response.",
            )
            observability.info(
                STAGE_RENDERING,
                EVENT_RENDERING_COMPLETED,
                "Rendering completed",
                "The final response was rendered from a validated local formatter.",
                details={"content_length": len(formatted_content), "formatter": True},
            )
            observability.stage_completed(
                STAGE_RENDERING,
                "Rendering completed",
                "Rendering finished with a formatted operator response.",
                details={"content_length": len(formatted_content), "formatter": True},
            )
        return formatted_content

    redactor = Redactor()
    collection = ResultCollectionPhase(redactor).collect(result_bundle)
    safe_previews = collection.safe_previews()
    shape_output = ShapeNormalizationPhase().normalize(
        result_bundle=result_bundle,
        collection=collection,
        result_store=result_store,
        allow_full_output_access=allow_full,
    )
    if observability is not None:
        observability.info(
            STAGE_OUTPUT_PLANNING,
            "output.result_collection.completed",
            "Result collection completed",
            "Execution results were collected with safe previews for output planning.",
            details={
                "result_count": len(result_bundle.results),
                "status": result_bundle.status,
                "output_trace": _output_trace(
                    request_id=user_request.request_id,
                    phase="result_collection",
                    title="Result collection completed",
                    summary="Execution results were collected with safe previews.",
                    payload_stats=[item.payload_stats.model_dump(mode="json") for item in collection.results],
                ),
            },
        )
        observability.stage_started(
            STAGE_OUTPUT_PLANNING,
            "Output planning started",
            "The runtime is selecting result shapes and a display plan.",
            details={
                "safe_preview_count": len(safe_previews),
                "shape_count": len(shape_output.shapes),
            },
        )
    selection_input = DisplaySelectionInput(
        original_prompt=user_request.raw_prompt,
        task_intent=_task_intent(dag),
        dag_summary=_summarize_dag(dag),
        result_summary=_summarize_results(result_bundle),
        normalized_shapes=shape_output.shape_summaries(),
        safe_previews=safe_previews,
        available_display_types=[
            "plain_text",
            "markdown",
            "table",
            "json",
            "code_block",
            "multi_section",
        ],
        target_ui=str(user_request.safety_context.get("target_ui") or "openwebui"),
        allow_raw_preview=bool(user_request.safety_context.get("allow_raw_preview", False)),
    )
    display_plan = None
    trace = user_request.safety_context.get("planning_trace")
    try:
        if observability is not None:
            shape_types = [shape.shape_type for shape in shape_output.shapes]
            observability.info(
                STAGE_OUTPUT_PLANNING,
                EVENT_OUTPUT_SHAPE_SELECTED,
                "Result shapes selected",
                "Execution results were normalized into semantic result shapes.",
                details={
                    "shape_types": shape_types,
                    "normalized_shapes": shape_output.shape_summaries(),
                    "output_trace": _output_trace(
                        request_id=user_request.request_id,
                        phase="shape_normalization",
                        title="Result shapes selected",
                        summary="Execution results were normalized into semantic result shapes.",
                        detail={"shape_types": shape_types},
                    ),
                },
            )
        display_plan = DisplaySelectionAdvisor().advise(selection_input, llm_client)
        display_plan = DisplayValidationPhase().validate(display_plan, selection_input)
        if isinstance(trace, PlanningTrace):
            model_name, temperature = llm_client_metadata(llm_client)
            append_trace_entry(
                trace,
                PlanningTraceEntry(
                    stage="display_plan_selection",
                    request_id=user_request.request_id,
                    model_name=model_name,
                    llm_temperature=temperature,
                    prompt_template_id="display_plan_selection",
                    parsed_proposal=display_plan.model_dump(mode="json"),
                    selected_candidate=display_plan.model_dump(mode="json"),
                )
            )
        if observability is not None:
            observability.info(
                STAGE_OUTPUT_PLANNING,
                "display.validation.accepted",
                "Display plan validated",
                "The advisor output was validated against declared primitives and sources.",
                details={
                    "display_type": display_plan.display_type,
                    "primitive_id": display_plan.primitive_id,
                    "section_count": len(display_plan.sections),
                    "output_trace": _output_trace(
                        request_id=user_request.request_id,
                        phase="display_validation",
                        title="Display plan validated",
                        summary="The advisor output was validated against declared primitives and sources.",
                        validation_result={"accepted": True},
                        selected_primitive=display_plan.primitive_id,
                    ),
                },
            )
        if observability is not None:
            observability.stage_completed(
                STAGE_OUTPUT_PLANNING,
                "Output planning completed",
                "A display plan was selected for rendering.",
                details={
                    "display_type": display_plan.display_type,
                    "primitive_id": display_plan.primitive_id,
                    "section_count": len(display_plan.sections),
                },
            )
            observability.stage_started(
                STAGE_RENDERING,
                "Rendering started",
                "The runtime is rendering the validated display plan.",
                details={
                    "display_type": display_plan.display_type,
                    "primitive_id": display_plan.primitive_id,
                },
            )
        lookup = shape_output.source_lookup
        rendered = OpenWebUIRenderer().render(display_plan, lookup)
        document = LocalAgentUIRenderer().render(
            display_plan,
            lookup,
            request_id=user_request.request_id,
            target_ui=str(user_request.safety_context.get("target_ui") or "openwebui"),
            summary=result_bundle.safe_summary,
            allow_raw_access=selection_input.allow_raw_preview,
        )
        rendered.metadata["display_document"] = document.model_dump(mode="json")
        user_request.safety_context["display_document"] = document.model_dump(mode="json")
        if observability is not None:
            observability.info(
                STAGE_RENDERING,
                "display_document.generated",
                "DisplayDocument generated",
                "The runtime generated the structured backend/frontend display contract.",
                details={
                    "section_count": len(document.sections),
                    "raw_available": document.raw_available,
                    "target_ui": document.target_ui,
                    "output_trace": _output_trace(
                        request_id=user_request.request_id,
                        phase="display_document_generation",
                        title="DisplayDocument generated",
                        summary="The runtime generated the structured backend/frontend display contract.",
                        detail={"section_count": len(document.sections)},
                    ),
                },
            )
            observability.info(
                STAGE_RENDERING,
                EVENT_RENDERING_COMPLETED,
                "Rendering completed",
                "The final response was rendered successfully.",
                details={
                    "display_type": display_plan.display_type,
                    "primitive_id": display_plan.primitive_id,
                    "content_length": len(rendered.content),
                    "output_trace": _output_trace(
                        request_id=user_request.request_id,
                        phase="rendering",
                        title="Rendering completed",
                        summary="The OpenWebUI renderer produced markdown/text output.",
                        selected_primitive=display_plan.primitive_id,
                        renderer="openwebui_markdown",
                    ),
                },
            )
            observability.stage_completed(
                STAGE_RENDERING,
                "Rendering completed",
                "Rendering finished successfully.",
                details={"content_length": len(rendered.content)},
            )
        return rendered.content
    except Exception as exc:
        if observability is not None:
            observability.warning(
                STAGE_OUTPUT_PLANNING,
                "validation.rejected",
                "Display plan rejected",
                "The runtime fell back to deterministic result-shape rendering.",
                details={
                    "error": str(exc),
                    "output_trace": _output_trace(
                        request_id=user_request.request_id,
                        phase="display_validation",
                        title="Display plan rejected",
                        summary="The runtime fell back to deterministic result-shape rendering.",
                        validation_result={"accepted": False, "error": str(exc)},
                        fallback_reason="advisor_validation_failed",
                    ),
                },
            )
        fallback_rendered = render_fallback_output(
            result_bundle=result_bundle,
            selection_input=selection_input,
            result_store=result_store,
            allow_full_output_access=allow_full,
            reason="advisor_validation_failed",
        )
        content = fallback_rendered.content
        try:
            document = LocalAgentUIRenderer().render(
                fallback_rendered.display_plan,
                shape_output.source_lookup,
                request_id=user_request.request_id,
                target_ui=str(user_request.safety_context.get("target_ui") or "openwebui"),
                summary=result_bundle.safe_summary,
                allow_raw_access=selection_input.allow_raw_preview,
            )
            user_request.safety_context["display_document"] = document.model_dump(mode="json")
        except Exception:
            document = None
        if observability is not None and document is not None:
            observability.info(
                STAGE_RENDERING,
                "display_document.generated",
                "Fallback DisplayDocument generated",
                "The runtime generated a structured display document from deterministic fallback rules.",
                details={
                    "section_count": len(document.sections),
                    "raw_available": document.raw_available,
                    "fallback": True,
                    "output_trace": _output_trace(
                        request_id=user_request.request_id,
                        phase="display_document_generation",
                        title="Fallback DisplayDocument generated",
                        summary="The runtime generated a structured document from fallback rules.",
                        fallback_reason="advisor_validation_failed",
                    ),
                },
            )
        if observability is not None:
            observability.stage_started(
                STAGE_RENDERING,
                "Rendering started",
                "The runtime is using deterministic result-shape rendering.",
            )
            observability.info(
                STAGE_RENDERING,
                EVENT_RENDERING_COMPLETED,
                "Rendering completed",
                "The runtime rendered the response using the deterministic fallback.",
                details={"content_length": len(content), "fallback": True},
            )
            observability.stage_completed(
                STAGE_RENDERING,
                "Rendering completed",
                "Deterministic fallback rendering finished successfully.",
                details={"content_length": len(content)},
            )
        return content


class OutputPipelineOrchestrator:
    """Select, redact, render, and summarize a result bundle."""

    def __init__(self) -> None:
        self.display_selector = DisplaySelector()
        self.redactor = Redactor()
        self.summarizer = Summarizer()

    def render(
        self,
        bundle: ResultBundle,
        user_request: UserRequest | None = None,
        dag: ActionDAG | None = None,
        llm_client=None,
    ) -> RenderedOutput:
        """Render a result bundle through the deterministic output pipeline."""

        if user_request is not None and dag is not None and llm_client is not None:
            content = compose_output(user_request, dag, bundle, llm_client)
            rendered = RenderedOutput(
                content=content,
                display_plan=self.display_selector.select(
                    DisplaySelectionInput(
                        original_prompt=user_request.raw_prompt,
                        dag_summary=_summarize_dag(dag),
                        result_summary=_summarize_results(bundle),
                        safe_previews=_build_safe_previews(bundle, self.redactor),
                        available_display_types=[
                            "plain_text",
                            "markdown",
                            "table",
                            "json",
                            "code_block",
                            "multi_section",
                        ],
                    )
                ),
                metadata={
                    "display_document": user_request.safety_context.get("display_document"),
                },
            )
            return self.summarizer.summarize(rendered)

        if bundle.status == "confirmation_required" or bool(
            bundle.metadata.get("confirmation_required", False)
        ):
            display_plan = self.display_selector.select(
                DisplaySelectionInput(
                    original_prompt="",
                    dag_summary={},
                    result_summary=_summarize_results(bundle),
                    safe_previews=[],
                    available_display_types=[
                        "plain_text",
                        "markdown",
                        "table",
                        "json",
                        "code_block",
                        "multi_section",
                    ],
                )
            )
            return RenderedOutput(
                content=render_bundle_status_fallback(bundle) or "",
                display_plan=display_plan,
                metadata={},
            )

        safe_previews = _build_safe_previews(bundle, self.redactor)
        selection_input = DisplaySelectionInput(
            original_prompt="",
            dag_summary={},
            result_summary=_summarize_results(bundle),
            safe_previews=safe_previews,
            available_display_types=[
                "plain_text",
                "markdown",
                "table",
                "json",
                "code_block",
                "multi_section",
            ],
        )
        display_plan = self.display_selector.select(selection_input)
        lookup = {
            f"node:{preview['node_id']}": {
                "node_id": preview["node_id"],
                "data_ref": preview.get("data_ref"),
                "payload": preview.get("preview"),
                "shape": normalize_execution_result(result),
            }
            for result, preview in zip(bundle.results, safe_previews)
        }
        try:
            rendered = render_display_plan(display_plan, lookup)
        except Exception:
            rendered = render_fallback_output(
                result_bundle=bundle,
                selection_input=selection_input,
                reason="renderer_failed",
            )
        return self.summarizer.summarize(rendered)
