"""Capability registry for execution-time lookup and LLM-safe export."""

from __future__ import annotations

from agent_runtime.capabilities.base import BaseCapability
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.core.errors import CapabilityNotFoundError


class CapabilityRegistry:
    """In-memory registry of manifest-driven capabilities."""

    def __init__(self) -> None:
        self._capabilities: dict[str, BaseCapability] = {}
        self._planning_hidden_ids: set[str] = set()
        self._manifest_overlays: dict[str, dict[str, object]] = {}

    def register(self, capability: BaseCapability) -> None:
        """Register or replace a capability by capability id."""

        self._capabilities[capability.manifest.capability_id] = capability

    def apply_manifest_overlay(self, capability_id: str, overlay: dict[str, object]) -> None:
        """Apply additive planner-visible metadata for an existing capability."""

        normalized = str(capability_id or "").strip()
        if not normalized or normalized not in self._capabilities:
            return
        safe_overlay: dict[str, object] = {}
        for key in (
            "description_append",
            "semantic_verbs",
            "object_types",
            "semantic_tags",
            "output_object_types",
            "output_fields",
            "output_affordances",
            "examples",
            "safety_notes",
        ):
            value = overlay.get(key)
            if key == "description_append":
                text = str(value or "").strip()
                if text:
                    safe_overlay[key] = text[:1200]
                continue
            if key == "examples" and isinstance(value, list):
                examples: list[dict[str, object]] = []
                fingerprints: set[str] = set()
                for item in value:
                    if isinstance(item, dict):
                        candidate = {str(k)[:80]: v for k, v in item.items() if str(k).strip()}
                    else:
                        prompt = str(item or "").strip()
                        candidate = {"prompt": prompt[:1200]} if prompt else {}
                    fingerprint = repr(sorted(candidate.items()))
                    if candidate and fingerprint not in fingerprints:
                        examples.append(candidate)
                        fingerprints.add(fingerprint)
                    if len(examples) >= 80:
                        break
                if examples:
                    safe_overlay[key] = examples
                continue
            if isinstance(value, list):
                items: list[object] = []
                for item in value:
                    candidate = str(item)
                    if candidate not in items:
                        items.append(candidate)
                    if len(items) >= 80:
                        break
                if items:
                    safe_overlay[key] = items
        if not safe_overlay:
            return
        existing = dict(self._manifest_overlays.get(normalized) or {})
        for key, value in safe_overlay.items():
            if key == "description_append":
                parts = [str(existing.get(key) or "").strip(), str(value or "").strip()]
                existing[key] = "\n".join(part for part in parts if part)
            else:
                merged = list(existing.get(key) or [])
                for item in list(value or []):  # type: ignore[arg-type]
                    if item not in merged:
                        merged.append(item)
                existing[key] = merged
        self._manifest_overlays[normalized] = existing

    def apply_manifest_overlays(self, overlays: dict[str, dict[str, object]]) -> None:
        """Apply multiple planner-visible overlays."""

        for capability_id, overlay in dict(overlays or {}).items():
            self.apply_manifest_overlay(str(capability_id), dict(overlay or {}))

    def _manifest_with_overlay(self, manifest: CapabilityManifest) -> CapabilityManifest:
        overlay = dict(self._manifest_overlays.get(manifest.capability_id) or {})
        if not overlay:
            return manifest
        updates: dict[str, object] = {}
        description_append = str(overlay.get("description_append") or "").strip()
        if description_append:
            updates["description"] = f"{manifest.description}\n\n{description_append}".strip()
        for key in (
            "semantic_verbs",
            "object_types",
            "semantic_tags",
            "output_object_types",
            "output_fields",
            "output_affordances",
            "examples",
            "safety_notes",
        ):
            values = list(getattr(manifest, key) or [])
            for item in list(overlay.get(key) or []):
                if item not in values:
                    values.append(item)
            if values != list(getattr(manifest, key) or []):
                updates[key] = values
        return manifest.model_copy(update=updates) if updates else manifest

    def set_planning_visible(self, capability_id: str, visible: bool) -> None:
        """Control whether a capability is exposed to LLM planning stages."""

        normalized = str(capability_id or "").strip()
        if not normalized:
            return
        if visible:
            self._planning_hidden_ids.discard(normalized)
        else:
            self._planning_hidden_ids.add(normalized)

    def is_planning_visible(self, capability_id: str) -> bool:
        """Return whether one capability is visible to LLM planning stages."""

        return str(capability_id or "").strip() not in self._planning_hidden_ids

    def get(self, capability_id: str) -> BaseCapability:
        """Return a capability or raise a typed error."""

        try:
            return self._capabilities[capability_id]
        except KeyError as exc:
            raise CapabilityNotFoundError(f"capability not registered: {capability_id}") from exc

    def list_manifests(self) -> list[CapabilityManifest]:
        """Return all registered manifests."""

        manifests = [
            self._manifest_with_overlay(capability.manifest)
            for capability in self._capabilities.values()
        ]
        return sorted(manifests, key=lambda manifest: (manifest.domain.lower(), manifest.capability_id))

    def list_planning_manifests(self) -> list[CapabilityManifest]:
        """Return manifests exposed to LLM planning stages."""

        return [
            manifest
            for manifest in self.list_manifests()
            if self.is_planning_visible(manifest.capability_id)
        ]

    def planning_view(self) -> "CapabilityRegistry":
        """Return a shallow registry containing only LLM-visible capabilities."""

        registry = CapabilityRegistry()
        for manifest in self.list_planning_manifests():
            registry.register(self.get(manifest.capability_id))
        registry.apply_manifest_overlays(
            {
                capability_id: dict(overlay)
                for capability_id, overlay in self._manifest_overlays.items()
                if capability_id in registry._capabilities
            }
        )
        return registry

    def find_by_domain(self, domain: str) -> list[CapabilityManifest]:
        """Return manifests in the requested domain."""

        normalized = str(domain or "").strip().lower()
        return [manifest for manifest in self.list_manifests() if manifest.domain.lower() == normalized]

    def find_by_semantic_verb(self, verb: str) -> list[CapabilityManifest]:
        """Return manifests supporting the requested semantic verb."""

        normalized = str(verb or "").strip().lower()
        return [
            manifest
            for manifest in self.list_manifests()
            if normalized in {item.strip().lower() for item in manifest.semantic_verbs}
        ]

    def export_llm_manifest(self) -> list[dict[str, object]]:
        """Return a compact LLM-safe manifest without implementation details."""

        exported: list[dict[str, object]] = []
        for manifest in self.list_planning_manifests():
            exported.append(
                {
                    "capability_id": manifest.capability_id,
                    "operation_id": manifest.operation_id,
                    "domain": manifest.domain,
                    "description": manifest.description,
                    "semantic_verbs": list(manifest.semantic_verbs),
                    "semantic_tags": list(manifest.semantic_tags),
                    "object_types": list(manifest.object_types),
                    "output_object_types": list(manifest.output_object_types),
                    "output_fields": list(manifest.output_fields),
                    "output_affordances": list(manifest.output_affordances),
                    "required_arguments": list(manifest.required_arguments),
                    "optional_arguments": list(manifest.optional_arguments),
                    "risk_level": manifest.risk_level,
                    "read_only": manifest.read_only,
                    "examples": list(manifest.examples),
                }
            )
        return exported
