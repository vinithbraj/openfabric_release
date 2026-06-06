"""Operator-native dataflow planning.

The standard runtime now represents ordinary work as operator actions. Data
dependencies for those actions live in ``operator.python_action`` or ``operator.python_transform``
``input_bindings`` and are validated by the operator contract. This module
keeps the small ``ValidatedDataflowPlan`` compatibility shape consumed by DAG
construction, but deliberately no longer runs the legacy product/selector
planner.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.capabilities.registry import CapabilityRegistry
from agent_runtime.core.types import InputRef, TaskFrame
from agent_runtime.input_pipeline.domain_selection import CapabilitySelectionResult
from agent_runtime.llm.reproducibility import (
    PlanningTrace,
    PlanningTraceEntry,
    append_trace_entry,
    llm_client_metadata,
)


class ValidatedDataflowRef(BaseModel):
    """Compatibility wrapper for an already validated upstream input reference."""

    model_config = ConfigDict(extra="forbid")

    consumer_task_id: str
    consumer_argument_name: str
    producer_task_id: str
    producer_node_id: str
    producer_output_key: str | None = None
    expected_data_type: str | None = None
    input_ref: InputRef
    reasons: list[str] = Field(default_factory=list)


class ValidatedDerivedTask(BaseModel):
    """Compatibility wrapper for a planner-inserted task.

    The operator-native planner no longer inserts derived tasks; the shape is
    retained so existing DAG-construction code can continue to accept an empty
    plan without special cases.
    """

    model_config = ConfigDict(extra="forbid")

    task: TaskFrame
    capability_id: str
    operation_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ValidatedDataflowPlan(BaseModel):
    """Trusted dataflow plan consumed by DAG construction.

    In the operator-backed pipeline this is intentionally empty: ordering comes
    from task dependencies, and value flow comes from operator ``input_bindings``.
    """

    model_config = ConfigDict(extra="forbid")

    refs: list[ValidatedDataflowRef] = Field(default_factory=list)
    bindings: list[dict[str, Any]] = Field(default_factory=list)
    derived_tasks: list[ValidatedDerivedTask] = Field(default_factory=list)
    dependency_edges: list[tuple[str, str]] = Field(default_factory=list)
    rejected_refs: list[dict[str, Any]] = Field(default_factory=list)
    rejected_bindings: list[dict[str, Any]] = Field(default_factory=list)
    rejected_derived_tasks: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_dataflows: list[str] = Field(default_factory=list)


def _empty_validated_plan() -> ValidatedDataflowPlan:
    """Return the operator-native no-op dataflow plan."""

    return ValidatedDataflowPlan()


def _selected_manifest_domains(
    *,
    capability_selections: list[CapabilitySelectionResult],
    registry: CapabilityRegistry,
) -> list[str]:
    """Return selected capability domains for trace diagnostics."""

    domains: list[str] = []
    for selection in capability_selections:
        if selection.selected is None:
            continue
        try:
            manifest = registry.get(selection.selected.capability_id).manifest
        except Exception:
            domains.append("unknown")
            continue
        domains.append(manifest.domain.strip().lower())
    return domains


def plan_dataflow(
    *,
    original_prompt: str,
    tasks: list[TaskFrame],
    capability_selections: list[CapabilitySelectionResult],
    registry: CapabilityRegistry,
    llm_client,
    trace: PlanningTrace | None = None,
) -> ValidatedDataflowPlan:
    """Return an empty operator-native dataflow plan.

    Legacy dataflow planning used capability product contracts, selectors, and
    transform proposals. That path is intentionally removed from the standard
    operator-backed pipeline; Python transform inputs are now authored as
    operator ``input_bindings`` and validated in the operator contract.
    """

    _ = original_prompt, tasks
    validated = _empty_validated_plan()
    domains = _selected_manifest_domains(
        capability_selections=capability_selections,
        registry=registry,
    )
    if isinstance(trace, PlanningTrace):
        model_name, temperature = llm_client_metadata(llm_client)
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="dataflow_planning",
                request_id=str(trace.request_id),
                model_name=model_name,
                llm_temperature=temperature,
                prompt_template_id="operator_native_dataflow",
                parsed_proposal=validated.model_dump(mode="json"),
                selected_candidate=validated.model_dump(mode="json"),
                deterministic_normalizations=[
                    "legacy product/selector dataflow planning removed",
                    "operator value flow is represented by OperatorAction.input_bindings",
                ],
                rejection_reasons=[
                    (
                        "dataflow planning is a no-op in the operator-backed standard "
                        "pipeline; selected capability domains: "
                        f"{domains or ['none']}"
                    )
                ],
            ),
        )
    return validated


__all__ = [
    "ValidatedDataflowPlan",
    "ValidatedDataflowRef",
    "ValidatedDerivedTask",
    "plan_dataflow",
]
