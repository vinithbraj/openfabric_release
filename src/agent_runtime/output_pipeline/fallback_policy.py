"""Central deterministic fallback policy for the output pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.core.types import DisplayPlan, RenderedOutput, ResultBundle
from agent_runtime.output_pipeline.display_primitives import (
    DisplayPrimitiveManifest,
    DisplayPrimitiveRegistry,
    build_default_display_primitive_registry,
)
from agent_runtime.output_pipeline.renderers import render_result_shape
from agent_runtime.output_pipeline.result_shapes import (
    AggregateResult,
    DirectoryListingResult,
    MultiSectionResult,
    ResultShape,
    normalize_execution_result,
)


@dataclass(frozen=True)
class FallbackDisplaySource:
    """One source available to deterministic fallback planning."""

    source_ref: str
    source_node_id: str | None = None
    source_data_ref: str | None = None
    shape_type: str | None = None
    capability_id: str | None = None
    operation_id: str | None = None


FALLBACK_PRIMITIVE_PREFERENCES: dict[str, list[str]] = {
    "table": ["table", "record_list", "markdown"],
    "record_list": ["record_list", "table", "markdown"],
    "directory_listing": ["directory_listing", "table", "record_list", "markdown"],
    "file_tree": ["file_tree", "record_list", "table", "markdown"],
    "file_content": ["code_block", "markdown"],
    "json": ["json_view", "markdown"],
    "scalar": ["scalar", "markdown"],
    "aggregate": ["aggregate", "scalar", "markdown"],
    "error": ["error", "markdown"],
    "markdown": ["markdown"],
    "text": ["markdown", "code_block"],
    "command_output": ["code_block", "markdown"],
    "process_list": ["record_list", "table", "markdown"],
    "capability_list": ["record_list", "table", "markdown"],
    "multi_section": ["multi_section", "markdown"],
}


FALLBACK_TITLES_BY_SHAPE: dict[str, str] = {
    "directory_listing": "Directory Listing",
    "file_tree": "File Tree",
    "error": "Error",
}


def fallback_primitive_id_for_shape_type(shape_type: str | None) -> str:
    """Return the first-choice fallback primitive id for one shape type."""

    preference = FALLBACK_PRIMITIVE_PREFERENCES.get(str(shape_type or ""), ["markdown"])
    return preference[0]


def fallback_primitive_for_source(
    source: FallbackDisplaySource,
    manifests: list[DisplayPrimitiveManifest],
    *,
    registry: DisplayPrimitiveRegistry | None = None,
) -> DisplayPrimitiveManifest:
    """Return the policy-driven fallback primitive for one display source."""

    resolved_registry = registry or build_default_display_primitive_registry()
    by_id = {manifest.primitive_id: manifest for manifest in manifests}
    preference = FALLBACK_PRIMITIVE_PREFERENCES.get(str(source.shape_type or ""), ["markdown"])
    for primitive_id in preference:
        if primitive_id in by_id:
            return by_id[primitive_id]
    if manifests:
        return manifests[0]
    return resolved_registry.get("markdown")


def fallback_title_for_shape_type(shape_type: str | None) -> str | None:
    """Return a stable fallback title for a shape type."""

    return FALLBACK_TITLES_BY_SHAPE.get(str(shape_type or ""))


def fallback_title_for_shape(shape: ResultShape) -> str | None:
    """Return a stable fallback title for one normalized shape."""

    if getattr(shape, "title", None):
        return getattr(shape, "title")
    if isinstance(shape, AggregateResult):
        return shape.label or "Aggregate Result"
    if isinstance(shape, DirectoryListingResult):
        return "Directory Listing"
    return fallback_title_for_shape_type(getattr(shape, "shape_type", None))


def with_fallback_title(shape: ResultShape) -> ResultShape:
    """Return a shape with a deterministic title when the shape benefits from one."""

    if getattr(shape, "title", None):
        return shape
    title = fallback_title_for_shape(shape)
    if not title:
        return shape
    return shape.model_copy(update={"title": title})


def source_ref_for_preview(preview: dict[str, Any]) -> str | None:
    """Return the canonical source ref for a safe preview."""

    node_id = preview.get("node_id")
    if node_id is not None:
        return f"node:{node_id}"
    data_ref = preview.get("data_ref")
    if data_ref is not None:
        return f"data:{data_ref}"
    return None


def fallback_sources_from_previews(safe_previews: list[dict[str, Any]]) -> list[FallbackDisplaySource]:
    """Build deterministic fallback sources from safe preview records."""

    sources: list[FallbackDisplaySource] = []
    seen: set[str] = set()
    for preview in safe_previews:
        source_ref = source_ref_for_preview(preview)
        if source_ref is None or source_ref in seen:
            continue
        seen.add(source_ref)
        sources.append(
            FallbackDisplaySource(
                source_ref=source_ref,
                source_node_id=(
                    str(preview.get("node_id")) if preview.get("node_id") is not None else None
                ),
                source_data_ref=(
                    str(preview.get("data_ref")) if preview.get("data_ref") is not None else None
                ),
                shape_type=(
                    str(preview.get("shape_type"))
                    if preview.get("shape_type") is not None
                    else None
                ),
                capability_id=(
                    str(preview.get("capability_id"))
                    if preview.get("capability_id") is not None
                    else None
                ),
                operation_id=(
                    str(preview.get("operation_id"))
                    if preview.get("operation_id") is not None
                    else None
                ),
            )
        )
    return sources


def build_fallback_display_plan(
    selection_input: Any,
    *,
    registry: DisplayPrimitiveRegistry | None = None,
    reason: str = "deterministic_fallback",
) -> DisplayPlan:
    """Build a deterministic display plan when advisor planning is unavailable or rejected."""

    resolved_registry = registry or build_default_display_primitive_registry()
    sources = fallback_sources_from_previews(list(getattr(selection_input, "safe_previews", [])))
    sections: list[dict[str, Any]] = []
    for source in sources:
        manifests = resolved_registry.compatible_manifests(
            shape_type=source.shape_type,
            capability_id=source.capability_id,
        )
        primitive = fallback_primitive_for_source(
            source,
            manifests,
            registry=resolved_registry,
        )
        section: dict[str, Any] = {
            "primitive_id": primitive.primitive_id,
            "display_type": primitive.display_type,
            "parameters": {},
        }
        title = fallback_title_for_shape_type(source.shape_type)
        if title:
            section["title"] = title
        if source.source_node_id is not None:
            section["source_node_id"] = source.source_node_id
        elif source.source_data_ref is not None:
            section["source_data_ref"] = source.source_data_ref
        sections.append(section)

    if not sections:
        display_type = "markdown"
        primitive_id = "markdown"
    elif len(sections) == 1:
        display_type = str(sections[0]["display_type"])
        primitive_id = str(sections[0]["primitive_id"])
    else:
        display_type = "multi_section"
        primitive_id = "multi_section"

    result_summary = getattr(selection_input, "result_summary", {}) or {}
    return DisplayPlan(
        display_type=display_type,
        primitive_id=primitive_id,
        title=result_summary.get("title") if isinstance(result_summary, dict) else None,
        sections=sections,
        constraints={"selection_strategy": reason},
        redaction_policy="standard",
    )


def build_fallback_document_sections(
    display_plan: DisplayPlan,
    source_lookup: dict[str, dict[str, Any]],
    *,
    registry: DisplayPrimitiveRegistry | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic DisplayDocument sections when a display plan has no sections."""

    resolved_registry = registry or build_default_display_primitive_registry()
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in source_lookup.values():
        node_id = record.get("node_id")
        data_ref = record.get("data_ref")
        source_key = f"node:{node_id}" if node_id is not None else f"data:{data_ref}"
        if source_key in seen:
            continue
        seen.add(source_key)
        shape = record["shape"]
        primitive_id = fallback_primitive_id_for_shape_type(getattr(shape, "shape_type", None))
        try:
            display_type = resolved_registry.get(str(primitive_id)).display_type
        except Exception:
            primitive_id = "markdown"
            display_type = resolved_registry.get(primitive_id).display_type
        section: dict[str, Any] = {
            "primitive_id": primitive_id,
            "display_type": display_type,
            "title": display_plan.title or getattr(shape, "title", None),
            "parameters": {},
        }
        if node_id is not None:
            section["source_node_id"] = node_id
        elif data_ref is not None:
            section["source_data_ref"] = data_ref
        sections.append(section)
    return sections


def safe_error_text(message: str | None) -> str:
    """Strip traceback-like content from user-visible error text."""

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


def safe_summary_text(message: str | None) -> str:
    """Remove traceback text from summaries while preserving normal prose."""

    text = str(message or "").strip()
    if not text:
        return ""
    if "Traceback (most recent call last)" in text:
        return safe_error_text(text)
    return text


def render_error_bundle(result_bundle: ResultBundle) -> str:
    """Render an error bundle deterministically."""

    lines = ["Execution failed."]
    blocked_reasons = result_bundle.metadata.get("blocked_reasons", [])
    if blocked_reasons:
        lines.append("")
        lines.append("Blocked reasons:")
        lines.extend(f"- {reason}" for reason in blocked_reasons)
    safe_summary = safe_summary_text(result_bundle.safe_summary)
    if safe_summary:
        lines.append("")
        lines.append(safe_summary)
    return "\n".join(lines)


_EMPTY_CONFIRMATION_VALUES = {
    "",
    "<object with 0 fields>",
    "<list with 0 items>",
    "None",
    "none",
    "null",
}


def _confirmation_value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value or "").strip()


def _confirmation_arg_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (dict, list, tuple, set)) and not value:
        return True
    return _confirmation_value_text(value) in _EMPTY_CONFIRMATION_VALUES


def _confirmation_arg_label(key: str) -> str:
    return safe_summary_text(str(key).replace("_", " ").title())


def _confirmation_kind_label(capability_id: str, operation_id: str) -> str:
    raw = operation_id or capability_id.rsplit(".", 1)[-1] or "action"
    return raw.replace("_", " ")


def _confirmation_action_aliases(actions: list[dict[str, Any]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for index, action in enumerate(actions, start=1):
        label = f"Action {index}"
        for key in ("node_id", "action_id", "task_id"):
            value = str(action.get(key) or "").strip()
            if value:
                aliases[value] = label
                if value.startswith("node::"):
                    aliases[value.removeprefix("node::")] = label
                if value.startswith("task_"):
                    suffix = value.removeprefix("task_")
                    aliases[f"action_{suffix}"] = label
                if value.startswith("action_"):
                    suffix = value.removeprefix("action_")
                    aliases[f"task_{suffix}"] = label
                    aliases[f"node::task_{suffix}"] = label
    return aliases


def _confirmation_flow_lines(actions: list[dict[str, Any]]) -> list[str]:
    aliases = _confirmation_action_aliases(actions)
    flow_lines: list[str] = []
    for index, action in enumerate(actions, start=1):
        arguments = action.get("arguments")
        if not isinstance(arguments, dict):
            continue
        bindings = arguments.get("input_bindings")
        if not isinstance(bindings, list):
            continue
        consumer = f"Action {index}"
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            source_id = str(binding.get("source_action_id") or "").strip()
            input_name = str(binding.get("input_name") or "").strip()
            source_field = str(binding.get("source_field") or "stdout").strip()
            if not source_id or not input_name:
                continue
            source = aliases.get(source_id, f"`{safe_summary_text(source_id)}`")
            required = bool(binding.get("required", True))
            optional = "" if required else " (optional)"
            flow_lines.append(
                f"- {source} `{safe_summary_text(source_field)}` -> "
                f"{consumer} input `{safe_summary_text(input_name)}`{optional}"
            )
    return flow_lines


def _confirmation_fenced_block(value: Any, language: str) -> list[str]:
    text = _confirmation_value_text(value)
    fence = "```"
    while fence in text:
        fence += "`"
    return [f"{fence}{language}", text, fence]


def _confirmation_visible_argument_items(arguments: dict[str, Any]) -> list[tuple[str, str]]:
    hidden_or_promoted = {
        "cwd",
        "risk",
        "command",
        "code",
        "generated_sql",
        "executed_sql",
        "sql",
        "safety_classification",
        "input_bindings",
        "declared_output_shape",
        "reason",
    }
    default_values = {
        ("declared_output_shape", "text"),
        ("reason", "LLM-authored operator action."),
        ("format", "text"),
    }
    visible: list[tuple[str, str]] = []
    for key, value in arguments.items():
        key_text = str(key)
        lowered = key_text.strip().lower()
        value_text = _confirmation_value_text(value)
        if _confirmation_arg_is_empty(value):
            continue
        if lowered in hidden_or_promoted:
            continue
        if (lowered, value_text) in default_values:
            continue
        visible.append((_confirmation_arg_label(key_text), value_text))
    return visible


def render_confirmation_bundle(result_bundle: ResultBundle) -> str:
    """Render a confirmation-required bundle as a pause/resume prompt."""

    lines = [
        "## Confirmation Required",
        "",
        "Review these planned actions. Approval is required before execution.",
    ]
    actions = result_bundle.metadata.get("confirmation_actions")
    if isinstance(actions, list) and actions:
        lines.extend(["", "### Actions To Run", ""])
        for index, action in enumerate(actions, start=1):
            if not isinstance(action, dict):
                continue
            capability_id = str(action.get("capability_id") or "unknown")
            operation_id = str(action.get("operation_id") or "unknown")
            description = str(action.get("description") or "").strip()
            title = description or _confirmation_kind_label(capability_id, operation_id).title()
            arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
            risk = _confirmation_value_text(arguments.get("risk"))
            cwd = _confirmation_value_text(arguments.get("cwd"))
            kind = _confirmation_kind_label(capability_id, operation_id)
            meta = [f"`{capability_id}`", kind]
            if risk and not _confirmation_arg_is_empty(risk):
                meta.append(f"risk `{risk}`")
            if cwd and not _confirmation_arg_is_empty(cwd):
                meta.append(f"cwd `{cwd}`")

            lines.append(f"#### Action {index}: {safe_summary_text(title)}")
            lines.append(" · ".join(meta))

            command_preview = arguments.get("command")
            code_preview = arguments.get("code")
            generated_sql_preview = arguments.get("generated_sql")
            executed_sql_preview = arguments.get("executed_sql") or arguments.get("sql")
            if not _confirmation_arg_is_empty(command_preview):
                lines.append("")
                lines.append("Command:")
                lines.extend(_confirmation_fenced_block(command_preview, "bash"))
            if not _confirmation_arg_is_empty(code_preview):
                lines.append("")
                lines.append("Code:")
                lines.extend(_confirmation_fenced_block(code_preview, "python"))
            if not _confirmation_arg_is_empty(generated_sql_preview):
                lines.append("")
                lines.append("Generated SQL:")
                lines.extend(_confirmation_fenced_block(generated_sql_preview, "sql"))
            if (
                not _confirmation_arg_is_empty(executed_sql_preview)
                and _confirmation_value_text(executed_sql_preview)
                != _confirmation_value_text(generated_sql_preview)
            ):
                lines.append("")
                lines.append("Executed SQL:")
                lines.extend(_confirmation_fenced_block(executed_sql_preview, "sql"))

            for label, value_text in _confirmation_visible_argument_items(arguments):
                lines.append(f"{label}: `{value_text}`")
            lines.append("")

        flow_lines = _confirmation_flow_lines(
            [action for action in actions if isinstance(action, dict)]
        )
        if flow_lines:
            lines.extend(["", "### Output Flow"])
            lines.extend(flow_lines)

    safe_summary = safe_summary_text(result_bundle.safe_summary)
    if safe_summary:
        lines.extend(["", safe_summary])
    return "\n".join(lines)


def render_partial_bundle(
    result_bundle: ResultBundle,
    *,
    result_store=None,
    allow_full_output_access: bool = False,
) -> str:
    """Render a partial bundle deterministically."""

    lines = ["Partial results"]
    safe_summary = safe_summary_text(result_bundle.safe_summary)
    if safe_summary:
        lines.extend(["", safe_summary])

    successes = [result for result in result_bundle.results if result.status == "success"]
    skipped = [result for result in result_bundle.results if result.status == "skipped"]
    errors = [result for result in result_bundle.results if result.status == "error"]

    if successes:
        lines.extend(["", "Completed:"])
        success_bundle = ResultBundle(
            dag_id=result_bundle.dag_id,
            status="success",
            results=successes,
            safe_summary="",
            metadata=dict(result_bundle.metadata),
        )
        lines.append(
            render_success_bundle(
                success_bundle,
                result_store=result_store,
                allow_full_output_access=allow_full_output_access,
            )
        )
    if skipped:
        lines.extend(["", "Skipped:"])
        for result in skipped:
            lines.append(f"- `{result.node_id}`: {safe_error_text(result.error or 'Skipped.')}")
    if errors:
        lines.extend(["", "Errors:"])
        for result in errors:
            lines.append(f"- `{result.node_id}`: {safe_error_text(result.error)}")
    return "\n".join(lines)


def render_empty_bundle() -> str:
    """Render a successful but empty result bundle."""

    return "No results available."


def render_bundle_status_fallback(
    result_bundle: ResultBundle,
    *,
    result_store=None,
    allow_full_output_access: bool = False,
) -> str | None:
    """Render status-level fallback content, or return None for normal success rendering."""

    if result_bundle.status == "confirmation_required" or bool(
        result_bundle.metadata.get("confirmation_required", False)
    ):
        return render_confirmation_bundle(result_bundle)
    if result_bundle.status == "error":
        return render_error_bundle(result_bundle)
    if result_bundle.status == "partial":
        return render_partial_bundle(
            result_bundle,
            result_store=result_store,
            allow_full_output_access=allow_full_output_access,
        )
    if result_bundle.status == "success" and not result_bundle.results:
        return render_empty_bundle()
    return None


def render_success_bundle(
    result_bundle: ResultBundle,
    *,
    result_store=None,
    allow_full_output_access: bool = False,
) -> str:
    """Render successful bundles deterministically from normalized result shapes."""

    store_for_shapes = result_store if allow_full_output_access else None
    shapes = [
        normalize_execution_result(result, store_for_shapes)
        for result in result_bundle.results
        if result.status == "success"
    ]
    if not shapes:
        return render_empty_bundle()

    ordered_shapes = [with_fallback_title(shape) for shape in shapes]
    if len(ordered_shapes) == 1:
        shape = ordered_shapes[0]
        return render_result_shape(shape, title=fallback_title_for_shape(shape))

    multi = MultiSectionResult(
        node_id="multi-section",
        title=None,
        sections=ordered_shapes,
    )
    return render_result_shape(multi)


def render_fallback_output(
    *,
    result_bundle: ResultBundle,
    selection_input: Any | None = None,
    result_store=None,
    allow_full_output_access: bool = False,
    reason: str = "deterministic_fallback",
) -> RenderedOutput:
    """Build a deterministic rendered output when planning or rendering fails."""

    status_content = render_bundle_status_fallback(
        result_bundle,
        result_store=result_store,
        allow_full_output_access=allow_full_output_access,
    )
    try:
        content = status_content or render_success_bundle(
            result_bundle,
            result_store=result_store,
            allow_full_output_access=allow_full_output_access,
        )
    except Exception as exc:
        content = "Unable to render structured output safely."
        safe_error = safe_error_text(str(exc))
        if safe_error:
            content = f"{content}\n\n{safe_error}"
    display_plan = (
        build_fallback_display_plan(selection_input, reason=reason)
        if selection_input is not None
        else DisplayPlan(
            display_type="markdown",
            primitive_id="markdown",
            constraints={"selection_strategy": reason},
            redaction_policy="standard",
        )
    )
    return RenderedOutput(content=content, display_plan=display_plan, metadata={"fallback": True})
