"""Request context, database profile, and preview helpers for operator validation."""

from __future__ import annotations

from agent_runtime.operator.validation_support.common import *

def _request_agent_parameter_env_names(user_request: UserRequest) -> set[str]:
    env_names: set[str] = set()
    for context in (user_request.session_context, user_request.safety_context):
        payload = dict(context or {})
        direct_names = payload.get("agent_parameter_shell_env_names")
        if isinstance(direct_names, list):
            env_names.update(str(name) for name in direct_names if str(name).strip())
        matches = payload.get("agent_parameter_store_matches")
        if not isinstance(matches, list):
            matches = payload.get("agent_parameters")
        if not isinstance(matches, list):
            continue
        for item in matches:
            if not isinstance(item, dict):
                continue
            for name in item.get("env_names") or []:
                if str(name).strip():
                    env_names.add(str(name))
            env_payload = item.get("env")
            if isinstance(env_payload, dict):
                env_names.update(str(name) for name in env_payload if str(name).strip())
            profile = item.get("database_profile")
            if isinstance(profile, dict):
                profile_env = profile.get("env")
                if isinstance(profile_env, dict):
                    env_names.update(str(name) for name in profile_env.values() if str(name).strip())
    return env_names


def _request_database_parameter_profiles(user_request: UserRequest) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for context in (user_request.session_context, user_request.safety_context):
        payload = dict(context or {})
        matches = payload.get("agent_parameter_store_matches")
        if not isinstance(matches, list):
            matches = payload.get("agent_parameters")
        if not isinstance(matches, list):
            continue
        for item in matches:
            if not isinstance(item, dict):
                continue
            profile = item.get("database_profile")
            if not isinstance(profile, dict) or profile.get("profile_type") != "database_connection":
                continue
            key = str(item.get("normalized_key") or item.get("key") or "").strip()
            marker = key or json.dumps(profile.get("env") or {}, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            profiles.append(
                {
                    "key": str(item.get("key") or "").strip(),
                    "normalized_key": str(item.get("normalized_key") or "").strip(),
                    "exact": bool(item.get("exact")),
                    "profile": profile,
                }
            )
    return profiles


def _profile_env_recipe(profile: dict[str, Any]) -> str:
    env_payload = profile.get("env")
    if not isinstance(env_payload, dict):
        return ""
    preferred = [
        "host",
        "hostname",
        "port",
        "dbname",
        "database",
        "db",
        "user",
        "username",
        "password",
        "passwd",
        "pass",
        "path",
        "database_path",
        "db_path",
    ]
    ordered = [field for field in preferred if field in env_payload]
    ordered.extend(sorted(field for field in env_payload if field not in ordered))
    return ", ".join(
        f"{field}:${env_payload[field]}"
        for field in ordered
        if str(env_payload.get(field) or "").strip()
    )


def _profile_identifiers(profile_item: dict[str, Any]) -> set[str]:
    identifiers = {
        str(profile_item.get("key") or "").strip(),
        str(profile_item.get("normalized_key") or "").strip(),
    }
    profile = profile_item.get("profile")
    if isinstance(profile, dict):
        identity = profile.get("identity")
        if isinstance(identity, dict):
            identifiers.update(str(value).strip() for value in identity.values())
    normalized: set[str] = set()
    for identifier in identifiers:
        text = re.sub(r"[^a-z0-9]+", "_", identifier.lower()).strip("_")
        if len(text) < 3:
            continue
        normalized.update({text, text.replace("_", "-"), text.replace("_", " ")})
    return normalized


def _command_mentions_profile_identifier(command: str, profiles: list[dict[str, Any]]) -> bool:
    lowered = str(command or "").lower()
    for profile in profiles:
        for identifier in _profile_identifiers(profile):
            if not identifier:
                continue
            pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(identifier)}(?![A-Za-z0-9_.-])"
            if re.search(pattern, lowered):
                return True
    return False


def _profile_is_sqlite(profile_item: dict[str, Any]) -> bool:
    profile = profile_item.get("profile")
    if not isinstance(profile, dict):
        return False
    engine = str(profile.get("engine") or "").strip().lower()
    if engine == "sqlite":
        return True
    env_payload = profile.get("env")
    if not isinstance(env_payload, dict):
        return False
    return any(field in env_payload for field in ("database_path", "db_file", "db_path", "path", "sqlite_path"))

_STREAMING_OPERATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("stage", re.compile(r"\b(?:git\s+add|stage|staging)\b", re.IGNORECASE)),
    (
        "commit",
        re.compile(r"\b(?:git\s+commit|commit|committed|committing)\b", re.IGNORECASE),
    ),
    ("push", re.compile(r"\b(?:git\s+push|push|pushed|pushing)\b", re.IGNORECASE)),
    ("pull", re.compile(r"\b(?:git\s+pull|pull|pulled|pulling)\b", re.IGNORECASE)),
    (
        "start",
        re.compile(
            r"\b(?:docker\s+compose\s+up|systemctl\s+start|start|started|"
            r"starting|launch|bring\s+up)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "stop",
        re.compile(
            r"\b(?:docker\s+compose\s+(?:down|stop)|systemctl\s+stop|stop|"
            r"stopped|stopping|shut\s+down)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "restart",
        re.compile(r"\b(?:systemctl\s+restart|restart|restarted|restarting)\b", re.IGNORECASE),
    ),
    (
        "status",
        re.compile(
            r"\b(?:git\s+status|docker\s+ps|status|verify|verified|confirm|"
            r"confirmed|check|checked|inspect)\b",
            re.IGNORECASE,
        ),
    ),
    ("search", re.compile(r"\b(?:find|search|locate|discover|list|ls)\b", re.IGNORECASE)),
    (
        "delete",
        re.compile(
            r"\b(?:rm\s+|delete|deleted|deleting|remove|removed|removing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "install",
        re.compile(
            r"\b(?:pip\s+install|npm\s+install|uv\s+add|poetry\s+add|install|"
            r"installed|installing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "build",
        re.compile(
            r"\b(?:docker\s+build|npm\s+run\s+build|build|built|building)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "copy",
        re.compile(
            r"\b(?:cp\s+|rsync\b|copy|copied|copying|transfer|transferred|"
            r"transferring|sync|synced|syncing|synchroni[sz]e|synchroni[sz]ed|"
            r"synchroni[sz]ing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "move",
        re.compile(r"\b(?:mv\s+|move|moved|moving|rename|renamed|renaming)\b", re.IGNORECASE),
    ),
    (
        "write",
        re.compile(
            r"\b(?:touch\s+|printf\s+|cat\s+>|tee\s+|write|written|writing|"
            r"append|appended|appending)\b",
            re.IGNORECASE,
        ),
    ),
)


def _runtime_preview_text(value: Any) -> str:
    """Return only user/runtime sample data from a preview packet."""

    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("preview", "sample_value"):
            item = value.get(key)
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, (dict, list, tuple)):
                parts.append(_runtime_preview_text(item))
        if not parts:
            for item in value.values():
                parts.append(_runtime_preview_text(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, (list, tuple)):
        return "\n".join(_runtime_preview_text(item) for item in value)
    return ""

__all__ = [name for name in globals() if not name.startswith("__")]
