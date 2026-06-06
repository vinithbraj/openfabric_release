"""Plan validation mixin for the composed operator pipeline."""

from __future__ import annotations

from agent_runtime.operator.validation_support.mixin_support.common import *
from agent_runtime.operator.validation_support.mixin_support.streaming_prior import _ValidationStreamingPriorMixin
from agent_runtime.operator.validation_support.mixin_support.shell_dataflow import _ValidationShellDataflowMixin
from agent_runtime.operator.validation_support.mixin_support.path_database import _ValidationPathDatabaseMixin
from agent_runtime.operator.validation_support.mixin_support.streaming_scope import _ValidationStreamingScopeMixin
from agent_runtime.operator.validation_support.mixin_support.memory_adjudication import _ValidationMemoryAdjudicationMixin
from agent_runtime.operator.validation_support.mixin_support.generated_text import _ValidationGeneratedTextMixin
from agent_runtime.operator.validation_support.mixin_support.plan_core import _ValidationPlanCoreMixin


class PlanValidationMixin(
    _ValidationStreamingPriorMixin,
    _ValidationShellDataflowMixin,
    _ValidationPathDatabaseMixin,
    _ValidationStreamingScopeMixin,
    _ValidationMemoryAdjudicationMixin,
    _ValidationGeneratedTextMixin,
    _ValidationPlanCoreMixin,
):
    """Compatibility mixin composed from focused validation support mixins."""


del _ValidationStreamingPriorMixin
del _ValidationShellDataflowMixin
del _ValidationPathDatabaseMixin
del _ValidationStreamingScopeMixin
del _ValidationMemoryAdjudicationMixin
del _ValidationGeneratedTextMixin
del _ValidationPlanCoreMixin
