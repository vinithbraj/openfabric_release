"""Capability contracts and default operator-backed runtime registry."""

from agent_runtime.capabilities.base import BaseCapability
from agent_runtime.capabilities.monitors import (
    RuntimeInspectMonitorsCapability,
    RuntimeStartMonitorCapability,
    RuntimeStopMonitorCapability,
)
from agent_runtime.capabilities.parameters import (
    RuntimeInspectParameterStoreCapability,
    RuntimeManageParameterStoreCapability,
)
from agent_runtime.capabilities.operator import (
    OperatorPythonActionCapability,
    OperatorPythonTransformCapability,
    OperatorShellCommandCapability,
)
from agent_runtime.capabilities.registry import CapabilityRegistry
from agent_runtime.capabilities.runtime import (
    RuntimeDescribeCapabilitiesCapability,
    RuntimeDescribePipelineCapability,
    RuntimeExplainLastFailureCapability,
    RuntimeShowLastPlanCapability,
)
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.capabilities.sql import (
    RuntimeSqlDiscoverCapability,
    RuntimeSqlQueryCapability,
)


def build_default_registry() -> CapabilityRegistry:
    """Build the default safe capability registry for the agent runtime."""

    registry = CapabilityRegistry()
    registry.register(RuntimeDescribeCapabilitiesCapability(registry))
    registry.register(RuntimeDescribePipelineCapability())
    registry.register(RuntimeShowLastPlanCapability())
    registry.register(RuntimeExplainLastFailureCapability())
    registry.register(RuntimeStartMonitorCapability())
    registry.register(RuntimeStopMonitorCapability())
    registry.register(RuntimeInspectMonitorsCapability())
    registry.register(RuntimeInspectParameterStoreCapability())
    registry.register(RuntimeManageParameterStoreCapability())
    registry.register(RuntimeSqlDiscoverCapability())
    registry.register(RuntimeSqlQueryCapability())
    registry.register(OperatorShellCommandCapability())
    registry.register(OperatorPythonActionCapability())
    registry.register(OperatorPythonTransformCapability())

    llm_visible_domains = {"runtime", "operator", "sql"}
    for manifest in registry.list_manifests():
        registry.set_planning_visible(
            manifest.capability_id,
            manifest.domain.strip().lower() in llm_visible_domains,
        )
    return registry

__all__ = [
    "BaseCapability",
    "build_default_registry",
    "CapabilityManifest",
    "CapabilityRegistry",
    "OperatorPythonActionCapability",
    "OperatorPythonTransformCapability",
    "OperatorShellCommandCapability",
    "RuntimeDescribeCapabilitiesCapability",
    "RuntimeDescribePipelineCapability",
    "RuntimeExplainLastFailureCapability",
    "RuntimeInspectMonitorsCapability",
    "RuntimeInspectParameterStoreCapability",
    "RuntimeManageParameterStoreCapability",
    "RuntimeShowLastPlanCapability",
    "RuntimeSqlDiscoverCapability",
    "RuntimeSqlQueryCapability",
    "RuntimeStartMonitorCapability",
    "RuntimeStopMonitorCapability",
]
