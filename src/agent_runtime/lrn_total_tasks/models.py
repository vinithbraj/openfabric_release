"""Typed models for LRN-T/LR-T total-task structure learning."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


LrnTotalTaskStatus = Literal["active", "quarantined"]


class LrnTotalTaskLookupContext(BaseModel):
    """Request shape used to find reusable typed task structures."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    classification_context: dict[str, Any] = Field(default_factory=dict)
    model_name: str = ""
    model_family: str = ""
    workflow_mode: str = ""
    registry_contract_hash: str = ""
    similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    limit: int = Field(default=3, ge=1)


class LrnTotalTaskEntry(BaseModel):
    """One persisted top-level learned task structure."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    entry_key: str
    prompt_signature: str
    normalized_prompt: str
    prompt_excerpt: str = ""
    classification_context: dict[str, Any] = Field(default_factory=dict)
    model_name: str = ""
    model_family: str = ""
    workflow_mode: str = ""
    registry_contract_hash: str = ""
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    global_constraints: dict[str, Any] = Field(default_factory=dict)
    routing_metadata: dict[str, Any] = Field(default_factory=dict)
    status: LrnTotalTaskStatus = "active"
    success_count: int = 0
    failure_count: int = 0
    use_count: int = 0
    created_at: str
    updated_at: str
    last_used_at: str = ""
    quarantined_at: str = ""
    quarantine_reason: str = ""
    schema_version: int = 1


class LrnTotalTaskCandidate(BaseModel):
    """A total-task entry plus deterministic match details."""

    model_config = ConfigDict(extra="forbid")

    entry: LrnTotalTaskEntry
    score: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class LrnTotalTaskWrite(BaseModel):
    """Payload written after a successful request teaches LRN-T."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    classification_context: dict[str, Any] = Field(default_factory=dict)
    model_name: str = ""
    model_family: str = ""
    workflow_mode: str = ""
    registry_contract_hash: str = ""
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    global_constraints: dict[str, Any] = Field(default_factory=dict)
    routing_metadata: dict[str, Any] = Field(default_factory=dict)


class LrnTotalTaskStats(BaseModel):
    """Small API summary for total-task learning."""

    model_config = ConfigDict(extra="forbid")

    total_entries: int = 0
    active_entries: int = 0
    quarantined_entries: int = 0
    total_uses: int = 0
    last_used_at: str = ""
    last_updated_at: str = ""
    last_cleared_at: str = ""
