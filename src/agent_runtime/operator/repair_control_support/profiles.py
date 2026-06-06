"""Profile, policy, and repair-budget helpers."""

from __future__ import annotations

from .common import *


class _RepairProfilesMixin:
    """Profile, policy, and repair-budget helpers."""

    def operator_tryout_enabled(self) -> bool:
        return False

    def operator_fast_trout_enabled(self) -> bool:
        return False

    def _profile_repair_settings(self):
        return repair_settings_from_profile(getattr(self.config, "repair_profile", "balanced"))

    def _profile_reasoning_settings(self):
        return reasoning_settings_from_profile(getattr(self.config, "reasoning_profile", "fast"))

    def _operator_profile_policy(self):
        return operator_profile_policy(
            getattr(self.config, "reasoning_profile", "fast"),
            llm_operator_verbose_enabled=getattr(
                self.config,
                "llm_operator_verbose_enabled",
                True,
            ),
        )

    def _max_validation_repair_attempts(self) -> int:
        override = operator_legacy_override(
            self.config,
            "llm_operator_max_validation_repair_attempts",
        )
        if override is not None:
            return max(0, int(override))
        return max(0, int(self._profile_repair_settings().max_validation_repairs))

    def _max_deferred_code_repair_attempts(self) -> int:
        override = operator_legacy_override(
            self.config,
            "llm_operator_max_deferred_code_repair_attempts",
        )
        if override is not None:
            return max(0, int(override))
        return max(0, int(self._profile_repair_settings().max_deferred_code_repairs))

    def _max_answer_judge_repair_attempts(self) -> int:
        override = operator_legacy_override(
            self.config,
            "llm_operator_max_answer_judge_repair_attempts",
        )
        if override is not None:
            return max(0, int(override))
        return max(0, int(self._profile_repair_settings().max_answer_judge_repairs))

    def _max_execution_repair_attempts(self) -> int:
        override = operator_legacy_override(
            self.config,
            "llm_operator_max_execution_repair_attempts",
        )
        if override is not None:
            return max(0, int(override))
        return max(0, int(self._profile_repair_settings().max_execution_repairs))

    def _max_completion_repair_attempts(self) -> int:
        override = operator_legacy_override(
            self.config,
            "llm_operator_max_completion_repair_attempts",
        )
        if override is not None:
            return max(0, int(override))
        return max(0, int(self._profile_repair_settings().max_completion_repairs))

    def _operator_plan_review_enabled(self) -> bool:
        override = operator_legacy_override(self.config, "llm_operator_plan_review_enabled")
        if override is not None:
            return bool(override)
        if not self._operator_profile_policy().run_plan_review:
            return False
        return bool(self._profile_reasoning_settings().plan_review_enabled)

    def _operator_answer_judge_enabled(self) -> bool:
        override = operator_legacy_override(self.config, "llm_operator_answer_judge_enabled")
        if override is not None:
            return bool(override)
        if not self._operator_profile_policy().run_answer_coverage:
            return False
        return bool(self._profile_reasoning_settings().answer_judge_enabled)

    def _operator_cardinality_judge_enabled(self) -> bool:
        mode = normalize_cardinality_judge_mode(
            getattr(self.config, "llm_operator_cardinality_judge_mode", "auto")
        )
        if mode == "off":
            return False
        if mode == "on":
            return True
        if not self._operator_profile_policy().run_answer_coverage:
            return False
        final_response_mode = str(
            getattr(self.config, "llm_operator_final_response_mode", "detailed") or ""
        ).strip().lower()
        if final_response_mode == "simple":
            return False
        if normalize_reasoning_profile(getattr(self.config, "reasoning_profile", "fast")) == "fast":
            return False
        return True

    def _tryout_repair_attempt_budget(self) -> int:
        return self._max_execution_repair_attempts()
