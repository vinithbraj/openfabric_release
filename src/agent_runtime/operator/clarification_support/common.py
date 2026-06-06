"""Clarification policy and interaction result helpers for the operator pipeline."""
from __future__ import annotations
from agent_runtime.operator._shared import *
from agent_runtime.operator.events import *
from agent_runtime.operator.utils import *
from agent_runtime.operator.prompts import *
from agent_runtime.memory import (
    memory_directives_for_prompt_stage,
    memory_scope_tuple,
    record_scope_memory_question_skip,
)
from agent_runtime.clarification import (
    AgentClarificationResolution,
    normalize_agent_clarification_mode,
    resolve_agent_clarification,
)
from agent_runtime.settings_consolidation import (
    operator_legacy_override,
    operator_profile_policy,
    reasoning_settings_from_profile,
    repair_settings_from_profile,
)
_RISKY_LEVELS = {"medium", "high", "critical"}
@dataclass(frozen=True)
class _ClarificationPolicyReview:
    allowed: bool
    reason: str = ""
    instruction: str = ""

_DISCOVERABLE_CLARIFICATION_PHRASES = (
    "which branch",
    "what branch",
    "current branch",
    "current git branch",
    "branch to push",
    "branch should be pushed",
    "branch do you want to push",
    "branch do you want to stage",
    "current working directory",
    "working directory",
    "pending changes",
    "staged changes",
    "unstaged changes",
    "cpu make",
    "cpu model",
    "gpu make",
    "gpu model",
    "hardware make",
    "hardware model",
    "make of the cpu",
    "make of the gpu",
    "model of the cpu",
    "model of the gpu",
    "processor model",
    "graphics card",
)

_NEW_RESOURCE_INTENT_WORDS = {
    "add",
    "configure",
    "create",
    "generate",
    "init",
    "initialize",
    "install",
    "make",
    "new",
    "scaffold",
    "setup",
}

_USER_CHOICE_CLARIFICATION_WORDS = {
    "configuration",
    "configure",
    "desired",
    "identifier",
    "label",
    "name",
    "option",
    "preference",
    "profile",
    "setting",
    "template",
    "version",
}

_DISCOVERABLE_EXISTING_TARGET_WORDS = {
    "attached",
    "available",
    "connected",
    "current",
    "detected",
    "existing",
    "found",
    "installed",
    "inserted",
    "local",
    "mounted",
    "plugged",
    "present",
    "removable",
    "runtime",
}

_DISCOVERABLE_RESOURCE_WORDS = {
    "block",
    "branch",
    "container",
    "device",
    "devices",
    "disk",
    "drive",
    "drives",
    "file",
    "filesystem",
    "folder",
    "hardware",
    "host",
    "mount",
    "mountpoint",
    "partition",
    "path",
    "process",
    "repository",
    "service",
    "storage",
    "usb",
    "volume",
}

_DISCOVERABLE_IDENTITY_WORDS = {
    "id",
    "identifier",
    "label",
    "name",
    "path",
    "target",
    "uuid",
}

_USER_MACRO_COVERED_CLARIFICATION_WORDS = {
    "auth",
    "authentication",
    "credential",
    "credentials",
    "input",
    "otp",
    "passcode",
    "passphrase",
    "password",
    "pin",
    "secret",
    "ssh",
    "sudo",
    "token",
}

_HARDWARE_TARGET_CLARIFICATION_WORDS = {
    "card",
    "cards",
    "cpu",
    "cpus",
    "device",
    "devices",
    "gpu",
    "gpus",
    "graphics",
    "hardware",
    "processor",
    "processors",
}

_HARDWARE_IDENTITY_CLARIFICATION_WORDS = {
    "make",
    "manufacturer",
    "model",
    "name",
    "vendor",
}

_SPELLING_CLARIFICATION_WORDS = {
    "misspelled",
    "misspelling",
    "spelling",
    "typo",
}

_MEMORY_USER_ASK_MARKERS = (
    "ask the user",
    "ask user",
    "ask for user",
    "clarify with the user",
    "confirm with the user",
    "get user confirmation",
    "prompt the user",
    "request clarification",
    "user must provide",
)

_MEMORY_USER_ASK_GATING_MARKERS = (
    "before",
    "prior to",
    "unless",
    "if missing",
    "if not provided",
    "if no",
    "missing",
    "not provided",
    "not specified",
    "not included",
    "not given",
    "without",
)

_MEMORY_NEGATED_ASK_MARKERS = (
    "do not ask",
    "don't ask",
    "never ask",
)

__all__ = [name for name in globals() if not name.startswith("__")]
