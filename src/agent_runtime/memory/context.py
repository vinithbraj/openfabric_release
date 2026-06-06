"""Helpers for building memory retrieval context."""

from dataclasses import dataclass

from agent_runtime.core.domain_hints import detect_domain_hints


@dataclass(frozen=True)
class MemoryRetrievalHints:
    """Small deterministic hints that improve memory retrieval targeting."""

    task_type: str
    tags: list[str]


def enrich_memory_retrieval_hints(
    prompt: str,
    *,
    task_type: str = "",
    tags: list[str] | tuple[str, ...] | None = None,
) -> MemoryRetrievalHints:
    """Infer conservative domain hints for memory retrieval.

    The memory store intentionally requires structured context or strong overlap
    before applying memories. This helper bridges generic LLM task labels such as
    ``simple_tool_task`` or ``other`` to obvious local domains like Git, without
    relaxing retrieval globally.
    """

    normalized_task_type = str(task_type or "").strip()
    detection = detect_domain_hints(prompt, existing_tags=tags)
    enriched_tags = detection.tags
    detected_domain = detection.single_domain
    if detected_domain and normalized_task_type.lower() != detected_domain:
        normalized_task_type = detected_domain

    return MemoryRetrievalHints(task_type=normalized_task_type, tags=enriched_tags)
