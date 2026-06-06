"""Shared memory categorization helpers for Agent UI support modules."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.common import *


def _memory_slug(value: Any) -> str:
    """Return a compact lowercase token suitable for memory categorization."""

    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    chars = [ch if ch.isalnum() else "_" for ch in raw]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:80]


def _memory_tags(values: Any) -> list[str]:
    """Normalize memory tags while preserving user/LLM intent."""

    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    tags: list[str] = []
    for value in values:
        tag = _memory_slug(value)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags[:20]


def _memory_kind(value: Any, *, fallback: str = "task_memory") -> str:
    """Return one supported memory kind."""

    raw = _memory_slug(value)
    if raw in {"task_memory", "preference_memory", "validation_policy"}:
        return raw
    return fallback


def _memory_examples(values: Any) -> list[str]:
    """Normalize example lists for validation-policy memory."""

    if not isinstance(values, list):
        return []
    examples: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text:
            examples.append(text[:300])
        if len(examples) >= 8:
            break
    return examples


def _active_agent_model_for_memory(settings: Any) -> str:
    """Return the active model label for memory metadata when available."""

    try:
        from agent_runtime.api.agent_ui_support.settings_runtime import _agent_active_model

        active = _agent_active_model(settings)
        name = str(active.get("name") or "").strip()
        if name and name.lower() != "auto":
            return name
    except Exception:
        pass
    configured = str(getattr(settings, "default_model", "") or "").strip()
    return "" if configured.lower() == "auto" else configured


__all__ = [name for name in globals() if not name.startswith("__")]
