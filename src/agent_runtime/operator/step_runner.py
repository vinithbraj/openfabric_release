"""Operator action execution and approved-run control flow."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator.step_runner_support.common import *
from agent_runtime.operator.step_runner_support.executors import PythonTransformExecutor
from agent_runtime.operator.step_runner_support.metadata import _StepRunnerMetadataMixin
from agent_runtime.operator.step_runner_support.repair_learning import _StepRunnerRepairLearningMixin
from agent_runtime.operator.step_runner_support.inputs import _StepRunnerInputsMixin
from agent_runtime.operator.step_runner_support.python_code import _StepRunnerPythonCodeMixin
from agent_runtime.operator.step_runner_support.action_execution import _StepRunnerActionExecutionMixin
from agent_runtime.operator.step_runner_support.cache_writes import _StepRunnerCacheWritesMixin
from agent_runtime.operator.step_runner_support.formatting import _StepRunnerFormattingMixin
from agent_runtime.operator.step_runner_support.step_validation import _StepRunnerStepValidationMixin
from agent_runtime.operator.step_runner_support.approved_runner import _StepRunnerApprovedRunnerMixin


class StepRunnerMixin(
    _StepRunnerMetadataMixin,
    _StepRunnerRepairLearningMixin,
    _StepRunnerInputsMixin,
    _StepRunnerPythonCodeMixin,
    _StepRunnerActionExecutionMixin,
    _StepRunnerCacheWritesMixin,
    _StepRunnerFormattingMixin,
    _StepRunnerStepValidationMixin,
    _StepRunnerApprovedRunnerMixin,
):
    """Compatibility mixin composed from focused step-runner support mixins."""


# Keep internal composition classes out of the facade namespace.
del _StepRunnerMetadataMixin
del _StepRunnerRepairLearningMixin
del _StepRunnerInputsMixin
del _StepRunnerPythonCodeMixin
del _StepRunnerActionExecutionMixin
del _StepRunnerCacheWritesMixin
del _StepRunnerFormattingMixin
del _StepRunnerStepValidationMixin
del _StepRunnerApprovedRunnerMixin
