"""Path scope, failure masking, and database profile helpers for operator plan validation."""

from __future__ import annotations

from agent_runtime.operator.validation_support.mixin_support.common import *
from agent_runtime.operator.validation_support.mixin_support.common import _ValidationConstantsMixin


class _ValidationPathDatabaseMixin(_ValidationConstantsMixin):
    @staticmethod
    def _prompt_named_existing_dirs(prompt: str, cwd: Path) -> set[str]:
        names: set[str] = set()
        for match in _PROMPT_PATH_TOKEN_RE.finditer(str(prompt or "")):
            raw = next((group for group in match.groups() if group), "")
            cleaned = str(raw or "").strip().strip("`'\".,:;()[]{}")
            if not cleaned or cleaned.startswith("-"):
                continue
            first_segment = cleaned.split("/", 1)[0]
            if (
                not first_segment
                or first_segment.lower() in _PROMPT_DIRECTORY_IGNORE_WORDS
                or first_segment.isdigit()
            ):
                continue
            try:
                candidate = (cwd / first_segment).resolve(strict=False)
                cwd_resolved = cwd.resolve(strict=False)
            except Exception:
                continue
            if candidate == cwd_resolved:
                continue
            try:
                if candidate.is_dir():
                    names.add(first_segment)
            except OSError:
                continue
        return names

    @staticmethod
    def _find_command_path_scope_errors(command: str, named_dirs: set[str]) -> list[dict[str, Any]]:
        normalized = str(command or "").strip()
        if not normalized:
            return []
        try:
            tokens = [str(token or "") for token in shlex.split(normalized, comments=False, posix=True)]
        except ValueError:
            return []
        errors: list[dict[str, Any]] = []
        for find_index, token in enumerate(tokens):
            if Path(token).name != "find":
                continue
            find_tokens = tokens[find_index:]
            path_predicate_indexes = [
                index
                for index, candidate in enumerate(find_tokens)
                if candidate in {"-path", "-wholename"}
            ]
            if (
                "-o" in find_tokens
                and len(path_predicate_indexes) >= 2
                and not any(candidate in {"(", ")"} for candidate in find_tokens)
            ):
                errors.append(
                    {
                        "error": "find_ungrouped_or_path_scope",
                        "message": (
                            "find uses -o with multiple path predicates without grouping, "
                            "so operator precedence may widen the search beyond the requested scope."
                        ),
                        "repair_hint": (
                            "Search exact roots separately, or group the -path predicates with "
                            "escaped parentheses such as `\\( -path '*/src/*' -o -path '*/tests/*' \\)`."
                        ),
                    }
                )
            roots: list[str] = []
            for candidate in tokens[find_index + 1 :]:
                if candidate in {"(", ")", "!", "-o", "-a", ","} or candidate.startswith("-"):
                    break
                roots.append(candidate)
            broad_root = any(root in {"$PWD", "${PWD}", ".", "./"} for root in roots)
            if not broad_root or not named_dirs:
                continue
            wildcard_dirs: set[str] = set()
            for index, candidate in enumerate(find_tokens[:-1]):
                if candidate not in {"-path", "-wholename"}:
                    continue
                pattern = find_tokens[index + 1]
                wildcard_dirs.update(_FIND_WILDCARD_PATH_DIR_RE.findall(pattern))
            matched = sorted(named_dirs & wildcard_dirs)
            if matched:
                errors.append(
                    {
                        "error": "find_broad_root_wildcard_scope",
                        "message": (
                            "find searches a broad workspace root while filtering for user-named "
                            f"directory scope(s) {', '.join(matched)!r}; this can include unrelated "
                            "nested directories outside the requested roots."
                        ),
                        "repair_hint": (
                            "Use exact search roots such as `find \"$PWD/src\" \"$PWD/tests\" ...` "
                            "for the directories the user named, or group predicates so the broad "
                            "root cannot leak unrelated paths."
                        ),
                        "matched_directories": matched,
                    }
                )
        return errors

    @staticmethod
    def _shell_failure_masking_reason(command: str) -> str:
        normalized = str(command or "").strip()
        if not normalized or not shell_command_looks_mutating(normalized):
            return ""
        lowered = normalized.lower()
        if _FAILURE_MASKING_PIPE_RE.search(normalized):
            tail = lowered.split("||", 1)[1]
            if "exit 1" not in tail and "false" not in tail:
                return (
                    "Mutating shell commands must not turn failures into successful "
                    "echo/true fallbacks."
                )
        if "&&" in normalized and "||" in normalized:
            tail = lowered.split("||", 1)[1]
            if ("echo" in tail or "printf" in tail or "true" in tail) and "exit 1" not in tail and "false" not in tail:
                return (
                    "Conditional mutating shell commands must fail nonzero when the "
                    "requested mutation did not happen."
                )
        if _FAILURE_MASKING_CONDITIONAL_RE.search(normalized):
            else_tail = lowered.split("else", 1)[1]
            condition_text = lowered.split("then", 1)[0]
            # Safety precondition guards are allowed to stop and report when the
            # read-only guard fails. They are distinct from mutating commands
            # that hide their own failure behind a successful echo.
            guard_text = re.sub(r"\d?>\s*/dev/null\b|\d?>&\d+\b", "", condition_text)
            if not shell_command_looks_mutating(guard_text):
                return ""
            if "exit 1" not in else_tail and "false" not in else_tail:
                return (
                    "Conditional mutating shell commands must not report successful "
                    "completion from an else echo/printf branch."
                )
        return ""

    @staticmethod
    def _failure_verifier_text(action: OperatorAction, task: OperatorTask | None) -> str:
        return "\n".join(
            str(part or "")
            for part in (
                action.command,
                action.reason,
                task.goal if task is not None else "",
                task.reason if task is not None else "",
            )
        )

    def _best_effort_allowed(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        task: OperatorTask | None,
    ) -> bool:
        text = "\n".join(
            str(part or "")
            for part in (
                user_request.raw_prompt,
                action.reason,
                task.goal if task is not None else "",
                task.reason if task is not None else "",
            )
        )
        return bool(_BEST_EFFORT_INTENT_RE.search(text))

    def _failure_masking_validation_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        if operator_policy_mode(self.config, "failure") == "llm":
            return []
        task_by_id = {task.task_id: task for task in plan.tasks}
        prompt = str(user_request.raw_prompt or "")
        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            task = task_by_id.get(action.task_id)
            command = str(action.command or "")
            masking_reason = self._shell_failure_masking_reason(command)
            if masking_reason and not self._best_effort_allowed(user_request, action, task):
                errors.append(
                    {
                        "error": "shell_failure_masking",
                        "message": masking_reason,
                        "action_id": action.action_id,
                        "repair_hint": (
                            "Let the mutating command fail naturally, or explicitly exit "
                            "nonzero when the requested postcondition is unmet. Use best-effort "
                            "fallbacks only when the user explicitly requested optional behavior."
                        ),
                    }
                )
            verifier_text = self._failure_verifier_text(action, task)
            if (
                _STALE_FAILURE_VERIFIER_RE.search(verifier_text)
                and not _USER_REQUESTS_FAILURE_VERIFICATION_RE.search(prompt)
            ):
                errors.append(
                    {
                        "error": "stale_failure_verifier",
                        "message": (
                            "Verification actions must check the user-requested final state, "
                            "not that an earlier command failed."
                        ),
                        "action_id": action.action_id,
                        "repair_hint": (
                            "Replace failure verification with a fresh check of the requested "
                            "successful end state, or remove the verifier if the failed action "
                            "will be resumed later."
                        ),
                    }
                )
        return errors

    def _path_scope_validation_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        prompt = str(user_request.raw_prompt or "")
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            try:
                cwd = self.validator.resolved_cwd(action)
            except Exception:
                cwd = Path(str(action.cwd or ".")).expanduser().resolve(strict=False)
            named_dirs = self._prompt_named_existing_dirs(prompt, cwd)
            for error in self._find_command_path_scope_errors(str(action.command or ""), named_dirs):
                errors.append(
                    {
                        **error,
                        "action_id": action.action_id,
                    }
                )
        return errors

    @staticmethod
    def _allow_agent_parameter_env_placeholders(
        user_request: UserRequest,
        errors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Treat matched OF_PARAM_* names as concrete runtime environment variables."""

        env_names = _request_agent_parameter_env_names(user_request)
        if not env_names:
            return errors
        filtered: list[dict[str, Any]] = []
        for error in errors:
            if error.get("error") != "unresolved_shell_placeholder":
                filtered.append(error)
                continue
            placeholders = [
                str(placeholder)
                for placeholder in error.get("placeholders") or []
                if str(placeholder).strip()
            ]
            remaining = [
                placeholder
                for placeholder in placeholders
                if _shell_variable_name(placeholder) not in env_names
            ]
            if not remaining:
                continue
            if len(remaining) == len(placeholders):
                filtered.append(error)
                continue
            updated = dict(error)
            updated["placeholders"] = remaining
            updated["message"] = (
                "Shell command contains unresolved placeholder(s): "
                f"{', '.join(remaining)}. Shell commands must be concrete at approval time. "
                "Use one complete shell command with any runtime lookup assigned and consumed inside "
                "that command, or use python_action/python_transform actions with input_bindings."
            )
            filtered.append(updated)
        return filtered

    def _agent_parameter_db_profile_validation_errors(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
    ) -> list[dict[str, Any]]:
        """Reject DB plans that ignore a matched Parameter Store DB profile."""

        profiles = _request_database_parameter_profiles(user_request)
        if not profiles:
            return []
        prompt = str(user_request.raw_prompt or "")
        db_intent = bool(_DB_PROMPT_INTENT_RE.search(prompt))
        sqlite_allowed = any(_profile_is_sqlite(profile) for profile in profiles)
        recipe = _profile_env_recipe(profiles[0].get("profile") or {})
        profile_keys = [
            str(profile.get("key") or profile.get("normalized_key") or "").strip()
            for profile in profiles
            if str(profile.get("key") or profile.get("normalized_key") or "").strip()
        ]
        errors: list[dict[str, Any]] = []
        for action in plan.actions:
            if action.kind != "shell_command":
                continue
            command = str(action.command or "")
            generic_vars = [
                str(match.group("braced") or match.group("bare") or "")
                for match in _DB_GENERIC_OF_INPUT_RE.finditer(command)
            ]
            generic_vars = sorted({name for name in generic_vars if name})
            if generic_vars:
                errors.append(
                    {
                        "error": "agent_parameter_db_generic_of_input",
                        "message": (
                            "The request matched a Parameter Store database profile, but the shell "
                            "command used generic OF_INPUT_* database variables. OF_INPUT_* variables "
                            "are only for action inputs; saved parameters are exposed as OF_PARAM_* env vars."
                        ),
                        "action_id": action.action_id,
                        "variables": generic_vars,
                        "parameter_keys": profile_keys,
                        "repair_hint": (
                            "Use the matched database profile env vars instead: "
                            f"{recipe}. For PostgreSQL, set PGPASSWORD from the password env var "
                            "and call psql with -h/-p/-U/-d from the OF_PARAM_* names."
                        ),
                    }
                )
            lowered = command.lower()
            filesystem_fallback = (
                db_intent
                and _DB_FILESYSTEM_FALLBACK_RE.search(lowered)
                and _command_mentions_profile_identifier(command, profiles)
                and not _DB_CLIENT_RE.search(lowered)
            )
            sqlite_fallback = (
                db_intent
                and "sqlite3" in lowered
                and not sqlite_allowed
                and ("of_input_db_path" in lowered or _command_mentions_profile_identifier(command, profiles))
            )
            if filesystem_fallback or sqlite_fallback:
                errors.append(
                    {
                        "error": "agent_parameter_db_filesystem_fallback",
                        "message": (
                            "The request matched a saved database connection profile, but the plan "
                            "treated the database/profile name as a workspace file or directory."
                        ),
                        "action_id": action.action_id,
                        "parameter_keys": profile_keys,
                        "repair_hint": (
                            "Use the matched Parameter Store database env vars for a DB client command "
                            f"instead of find/ls/sqlite path probing: {recipe}. Do not invent "
                            "OF_INPUT_DB_PATH unless the matched profile is explicitly SQLite/path-based."
                        ),
                    }
                )
        return errors



__all__ = ["_ValidationPathDatabaseMixin"]
