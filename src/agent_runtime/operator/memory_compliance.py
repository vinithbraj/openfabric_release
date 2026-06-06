"""Memory and cache compliance helpers for the operator pipeline."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator.memory_compliance_support.common import *
from agent_runtime.operator.memory_compliance_support.plan_cache import _MemoryCompliancePlanCacheMixin
from agent_runtime.operator.memory_compliance_support.command_template_cache import _MemoryComplianceCommandTemplateCacheMixin
from agent_runtime.operator.memory_compliance_support.streaming_tree_cache import _MemoryComplianceStreamingTreeCacheMixin
from agent_runtime.operator.memory_compliance_support.lrdirect import _MemoryComplianceLrDirectMixin
from agent_runtime.operator.memory_compliance_support.computation_cache import _MemoryComplianceComputationCacheMixin
from agent_runtime.operator.memory_compliance_support.memory_context import _MemoryComplianceContextMixin
from agent_runtime.operator.memory_compliance_support.memory_review import _MemoryComplianceReviewMixin


class MemoryComplianceMixin(
    _MemoryComplianceContextMixin,
    _MemoryCompliancePlanCacheMixin,
    _MemoryComplianceCommandTemplateCacheMixin,
    _MemoryComplianceStreamingTreeCacheMixin,
    _MemoryComplianceLrDirectMixin,
    _MemoryComplianceComputationCacheMixin,
    _MemoryComplianceReviewMixin,
):
    """Compatibility mixin composed from focused memory compliance support mixins."""


del _MemoryComplianceContextMixin
del _MemoryCompliancePlanCacheMixin
del _MemoryComplianceCommandTemplateCacheMixin
del _MemoryComplianceStreamingTreeCacheMixin
del _MemoryComplianceLrDirectMixin
del _MemoryComplianceComputationCacheMixin
del _MemoryComplianceReviewMixin

__all__ = [name for name in globals() if not name.startswith("__")]
