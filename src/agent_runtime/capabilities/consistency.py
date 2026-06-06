"""Capability manifest contract consistency helpers."""

from __future__ import annotations

from typing import Any

from agent_runtime.capabilities.registry import CapabilityRegistry
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.core.types import CapabilityRef
from agent_runtime.llm.reproducibility import hash_capability_manifest


def manifest_contract(manifest: CapabilityManifest) -> dict[str, Any]:
    """Return the canonical contract used by planning, validation, and execution."""

    return {
        "capability_id": manifest.capability_id,
        "operation_id": manifest.operation_id,
        "domain": manifest.domain,
        "semantic_verbs": list(manifest.semantic_verbs),
        "object_types": list(manifest.object_types),
        "argument_schema": dict(manifest.argument_schema),
        "required_arguments": list(manifest.required_arguments),
        "optional_arguments": list(manifest.optional_arguments),
        "output_schema": dict(manifest.output_schema),
        "output_object_types": list(manifest.output_object_types),
        "output_fields": list(manifest.output_fields),
        "output_affordances": list(manifest.output_affordances),
        "execution_backend": manifest.execution_backend,
        "backend_operation": manifest.backend_operation,
        "risk_level": manifest.risk_level,
        "read_only": manifest.read_only,
        "mutates_state": manifest.mutates_state,
        "requires_confirmation": manifest.requires_confirmation,
    }


def manifest_contract_hash(manifest: CapabilityManifest) -> str:
    """Return a stable hash for one canonical capability contract."""

    return hash_capability_manifest(manifest_contract(manifest))


def registry_contracts(registry: CapabilityRegistry) -> list[dict[str, Any]]:
    """Return every canonical capability contract from one registry."""

    return [manifest_contract(manifest) for manifest in registry.list_manifests()]


def registry_contract_hash(registry: CapabilityRegistry) -> str:
    """Return a stable hash for all canonical capability contracts in a registry."""

    return hash_capability_manifest(registry_contracts(registry))


def registry_capability_ids(registry: CapabilityRegistry) -> list[str]:
    """Return stable registered capability ids."""

    return [manifest.capability_id for manifest in registry.list_manifests()]


def _contracts_by_id(registry: CapabilityRegistry) -> dict[str, dict[str, Any]]:
    """Return canonical contracts keyed by capability id."""

    return {
        manifest.capability_id: manifest_contract(manifest)
        for manifest in registry.list_manifests()
    }


def validate_registry_consistency(
    planner_registry: CapabilityRegistry,
    execution_registry: CapabilityRegistry,
) -> list[str]:
    """Return consistency errors between planning and execution capability registries."""

    planner_contracts = _contracts_by_id(planner_registry)
    execution_contracts = _contracts_by_id(execution_registry)
    errors: list[str] = []

    planner_ids = set(planner_contracts)
    execution_ids = set(execution_contracts)
    for capability_id in sorted(planner_ids - execution_ids):
        errors.append(
            f"capability {capability_id} is available to planning but missing from execution registry"
        )
    for capability_id in sorted(execution_ids - planner_ids):
        errors.append(
            f"capability {capability_id} is available to execution but missing from planning registry"
        )

    comparable_fields = (
        "operation_id",
        "argument_schema",
        "required_arguments",
        "optional_arguments",
        "output_schema",
        "output_object_types",
        "output_fields",
        "output_affordances",
        "execution_backend",
        "backend_operation",
        "read_only",
        "mutates_state",
        "requires_confirmation",
    )
    for capability_id in sorted(planner_ids & execution_ids):
        planner_contract = planner_contracts[capability_id]
        execution_contract = execution_contracts[capability_id]
        for field in comparable_fields:
            if planner_contract.get(field) != execution_contract.get(field):
                errors.append(
                    f"capability {capability_id} contract mismatch for {field}"
                )
    return errors


def validate_selected_capability_contracts(
    selections: list[Any],
    registry: CapabilityRegistry,
) -> list[str]:
    """Return errors when selected capabilities no longer match canonical contracts."""

    errors: list[str] = []
    for selection in selections:
        selected: CapabilityRef | None = getattr(selection, "selected", None)
        task_id = str(getattr(selection, "task_id", "unknown"))
        if selected is None:
            continue
        try:
            manifest = registry.get(selected.capability_id).manifest
        except Exception:
            errors.append(
                f"task {task_id} selected capability {selected.capability_id}, but it is not registered"
            )
            continue
        if selected.operation_id != manifest.operation_id:
            errors.append(
                f"task {task_id} selected operation {selected.operation_id} for "
                f"{selected.capability_id}, but canonical manifest declares {manifest.operation_id}"
            )
    return errors


__all__ = [
    "manifest_contract",
    "manifest_contract_hash",
    "registry_capability_ids",
    "registry_contract_hash",
    "registry_contracts",
    "validate_registry_consistency",
    "validate_selected_capability_contracts",
]
