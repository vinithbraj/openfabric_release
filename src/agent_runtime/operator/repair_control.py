"""Operator planning, repair, review, and follow-up control flow."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator._shared import *
from agent_runtime.operator.events import *
from agent_runtime.operator.utils import *
from agent_runtime.operator.stdout_classification import *
from agent_runtime.operator.shell_bindings import *
from agent_runtime.operator.prompts import *
from agent_runtime.operator.validation import *
from agent_runtime.operator.memory_compliance import *
from agent_runtime.operator.clarification import *
from agent_runtime.settings_consolidation import (
    normalize_cardinality_judge_mode,
    normalize_reasoning_profile,
    operator_legacy_override,
    operator_profile_policy,
    reasoning_settings_from_profile,
    repair_settings_from_profile,
)

_SINGLE_REPORT_COMPILER_TRIGGER_ERRORS = {
    "execution_shape_action_count_exceeded",
    "execution_shape_over_decomposed_report",
    "execution_shape_deferred_report_action",
}
_SINGLE_REPORT_ERROR_PREFIX = "execution_shape_"
_SINGLE_REPORT_COMPILER_FAILURE = "execution_shape_single_report_compiler_failed"

from agent_runtime.operator.repair_control_support.common import *
from agent_runtime.operator.repair_control_support.profiles import _RepairProfilesMixin
from agent_runtime.operator.repair_control_support.tryout import _RepairTryoutMixin
from agent_runtime.operator.repair_control_support.plan_repair import _PlanRepairMixin
from agent_runtime.operator.repair_control_support.execution_reviews import _ExecutionReviewsMixin
from agent_runtime.operator.repair_control_support.followup import _RepairFollowupMixin
from agent_runtime.operator.repair_control_support.propose import _RepairProposeMixin


class RepairControlMixin(
    _RepairProfilesMixin,
    _RepairTryoutMixin,
    _PlanRepairMixin,
    _ExecutionReviewsMixin,
    _RepairFollowupMixin,
    _RepairProposeMixin,
):
    """Operator planning, repair, review, and follow-up control flow."""

    pass


del _RepairProfilesMixin, _RepairTryoutMixin, _PlanRepairMixin, _ExecutionReviewsMixin, _RepairFollowupMixin, _RepairProposeMixin
