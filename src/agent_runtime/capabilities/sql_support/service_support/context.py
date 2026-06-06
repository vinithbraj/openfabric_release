"""SQL service profile/context helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.service_support.common import *

class SqlAgentContextMixin:
    def _resolve_profile(self, *, prompt: str, parameter_key: str = "") -> dict[str, Any]:
        if self.parameter_store is None:
            return self._error("Parameter store is not available.")
        if parameter_key:
            record = self.parameter_store.get(parameter_key)
            if record is None:
                return self._error(f"Parameter {parameter_key!r} was not found.")
            profile = parameter_database_profile(record)
            if profile.get("profile_type") != "database_connection":
                return self._error(
                    f"Parameter {record.key!r} is not a database connection profile."
                )
            return {
                "status": "success",
                "profile": _ResolvedProfile(
                    record,
                    profile,
                    _iter_scalar_values(record.value_json),
                ),
            }
        matches = self.parameter_store.retrieve_matches(prompt, limit=6, record_use=True)
        db_matches: list[_ResolvedProfile] = []
        for match in matches:
            profile = parameter_database_profile(match.record)
            if profile.get("profile_type") == "database_connection":
                db_matches.append(
                    _ResolvedProfile(
                        match.record,
                        profile,
                        _iter_scalar_values(match.record.value_json),
                    )
                )
        if not db_matches:
            fuzzy_matches: list[tuple[float, str, _ResolvedProfile]] = []
            for record in self.parameter_store.list(limit=1000):
                profile = parameter_database_profile(record)
                if profile.get("profile_type") != "database_connection":
                    continue
                score, reason = _fuzzy_profile_score(prompt, record)
                if score < 0.74:
                    continue
                fuzzy_matches.append(
                    (
                        score,
                        reason,
                        _ResolvedProfile(record, profile, _iter_scalar_values(record.value_json)),
                    )
                )
            fuzzy_matches.sort(key=lambda item: item[0], reverse=True)
            if not fuzzy_matches:
                return self._error("No matching database connection parameter was found.")
            top_score = fuzzy_matches[0][0]
            close_matches = [
                item for item in fuzzy_matches if top_score - item[0] < 0.08
            ]
            if len(close_matches) > 1 and top_score < 0.94:
                choices = [
                    {"key": item[2].key, "normalized_key": item[2].normalized_key}
                    for item in close_matches[:6]
                ]
                return _database_parameter_clarification(
                    prompt=prompt,
                    choices=choices,
                    summary=(
                        "Multiple database parameters may match. "
                        "Please choose one parameter key."
                    ),
                )
            return {"status": "success", "profile": fuzzy_matches[0][2]}
        exact_matches = [
            resolved
            for resolved in db_matches
            if any(
                item.get("normalized_key") == resolved.normalized_key and bool(item.get("exact"))
                for item in self.context.get("agent_parameter_matches") or []
                if isinstance(item, dict)
            )
        ]
        if len(db_matches) > 1 and not exact_matches:
            return _database_parameter_clarification(
                prompt=prompt,
                choices=[
                    {"key": item.key, "normalized_key": item.normalized_key}
                    for item in db_matches
                ],
                summary="Multiple database parameters match. Please choose one parameter key.",
            )
        return {"status": "success", "profile": (exact_matches or db_matches)[0]}


def _service_from_context(context: dict[str, Any]) -> SqlAgentService:
    from agent_runtime.capabilities.sql_support.service import SqlAgentService

    nested = _context_payload(context)
    return SqlAgentService(
        parameter_store=_parameter_store_from_context(context),
        gateway_client=_gateway_from_context(context),
        llm_client=_llm_from_context(context),
        memory_store=_memory_store_from_context(context),
        config=_config_from_context(context),
        context=nested if nested else context,
    )


__all__ = ['_service_from_context']
