"""Helpers for user-visible gateway execution metadata."""

from __future__ import annotations

from typing import Any


GATEWAY_METADATA_KEYS = (
    "mode",
    "gateway_display_name",
    "gateway_nickname",
    "gateway_node",
    "gateway_url",
    "gateway_id",
    "gateway_platform_label",
    "gateway_platform",
    "gateway_platform_version",
    "gateway_architecture",
    "gateway_shell",
    "gateway_command_profile",
)

_OVERRIDABLE_KEYS = {"gateway_node", "gateway_url"}


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    return value


def _nested_gateway_sources(source: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for key in ("gateway_routing", "gateway_metadata", "target_gateway", "gateway"):
        nested = source.get(key)
        if isinstance(nested, dict):
            sources.append(nested)
    sources.append(source)
    return sources


def merge_gateway_metadata(*sources: Any) -> dict[str, Any]:
    """Return normalized gateway metadata from routing context, chunks, or actions.

    The first friendly display values win, while live gateway node/url values may be
    filled or refreshed by later execution results.
    """

    metadata: dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for candidate in _nested_gateway_sources(source):
            for key in GATEWAY_METADATA_KEYS:
                value = _clean_value(candidate.get(key))
                if value in (None, [], {}):
                    continue
                if key in _OVERRIDABLE_KEYS:
                    metadata[key] = value
                elif key not in metadata:
                    metadata[key] = value
    if "gateway_display_name" not in metadata:
        fallback = metadata.get("gateway_nickname") or metadata.get("gateway_node")
        if fallback:
            metadata["gateway_display_name"] = fallback
    return metadata


def action_gateway_metadata(action: dict[str, Any], request_context: dict[str, Any]) -> dict[str, Any]:
    """Resolve gateway metadata for one confirmation action.

    Per-action gateway fields are authoritative. Request-level routing only fills
    actions that do not target another gateway explicitly.
    """

    arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    action_metadata = merge_gateway_metadata(action, arguments)
    if action_metadata:
        return action_metadata
    return merge_gateway_metadata(request_context)
