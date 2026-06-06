from __future__ import annotations

from pathlib import Path

from agent_runtime.command_template_cache import (
    AgentCommandTemplateCacheStore,
    CommandTemplateLookupContext,
    CommandTemplateWrite,
    exact_step_key,
    extract_template_input_names,
    lrdirect_canonical_step_key,
    normalize_lrdirect_step_text,
    render_template_with_values,
)


def test_command_template_cache_persists_retrieves_and_renders(tmp_path: Path) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    entry = store.upsert_entry(
        CommandTemplateWrite(
            prompt="say hello to alice",
            step_description="print a greeting",
            mode="Conversational",
            model_name="fake-model",
            task_type="shell",
            tool_type="shell",
            intent_type="print",
            interaction_mode="may_prompt",
            tags=["greeting", "printf"],
            command_template='printf "hello %s\\n" "$OF_INPUT_NAME"',
            variables=[
                {
                    "name": "name",
                    "description": "Name to greet.",
                    "observed_value": "alice",
                }
            ],
            observed_command='printf "hello %s\\n" alice',
            risk="low",
            effect_intent="read_only",
            effect_summary="Prints a greeting.",
        )
    )

    assert entry.success_count == 1
    assert entry.interaction_mode == "may_prompt"
    assert extract_template_input_names(entry.command_template) == ["name"]
    assert render_template_with_values(entry.command_template, {"name": "bob"}) == (
        'printf "hello %s\\n" "bob"'
    )

    candidates = store.retrieve(
        CommandTemplateLookupContext(
            prompt="print hello to bob",
            step_description="print a greeting",
            mode="Conversational",
            model_name="fake-model",
            task_type="shell",
            tool_type="shell",
            intent_type="print",
            tags=["greeting"],
            similarity_threshold=0.1,
        )
    )

    assert len(candidates) == 1
    assert candidates[0].entry.template_id == entry.template_id
    assert candidates[0].entry.interaction_mode == "may_prompt"
    store.mark_used(entry.template_id)
    store.mark_failed(entry.template_id, failure_category="bad greeting")
    updated = store.get_entry(entry.template_id)
    assert updated is not None
    assert updated.use_count == 1
    assert updated.failure_count == 1


def test_command_template_cache_redacts_secret_shaped_values(tmp_path: Path) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    entry = store.upsert_entry(
        CommandTemplateWrite(
            prompt="call service",
            step_description="call service",
            command_template="curl -H '$OF_INPUT_HEADER' https://example.invalid",
            variables=[
                {
                    "name": "header",
                    "description": "Authorization header.",
                    "observed_value": "Authorization: Bearer abc123",
                }
            ],
            observed_command="curl -H 'Authorization: Bearer abc123' https://example.invalid",
        )
    )

    assert "abc123" not in str(entry.variables)
    assert "[redacted]" in str(entry.variables)


def test_command_template_cache_keeps_distinct_templates_for_same_step(
    tmp_path: Path,
) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    common = {
        "prompt": "inspect status",
        "step_description": "Inspect status",
        "mode": "Conversational",
        "model_name": "fake-model",
        "task_type": "shell",
        "tool_type": "shell",
        "intent_type": "inspect",
        "risk": "low",
        "effect_intent": "read_only",
    }

    first = store.upsert_entry(
        CommandTemplateWrite(
            **common,
            command_template="git status --short",
            observed_command="git status --short",
            tags=["git", "status"],
        )
    )
    second = store.upsert_entry(
        CommandTemplateWrite(
            **common,
            command_template="docker ps",
            observed_command="docker ps",
            tags=["docker", "status"],
        )
    )

    assert first.template_id != second.template_id
    assert store.stats().total_entries == 2


def test_command_template_cache_persists_payload_bindings_by_lr_mode(
    tmp_path: Path,
) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    common = {
        "prompt": "commit generated message",
        "step_description": "Commit with the generated message",
        "mode": "Conversational",
        "model_name": "fake-model",
        "task_type": "git.commit",
        "tool_type": "git.commit",
        "intent_type": "create",
        "tags": ["git", "commit", "message"],
        "risk": "medium",
        "effect_intent": "mutates_state",
    }
    standard = store.upsert_entry(
        CommandTemplateWrite(
            **common,
            command_template="git status --short",
            observed_command="git status --short",
        )
    )
    payload = store.upsert_entry(
        CommandTemplateWrite(
            **common,
            lr_mode="lr_ex",
            payload_bindings=[
                {
                    "input_name": "commit_message",
                    "source_role": "generated_text",
                    "source_field": "stdout",
                    "stdin_mode": "input_binding",
                    "generated_text": True,
                }
            ],
            command_template="git commit -F -",
            observed_command="git commit -F -",
            exact_step_key=exact_step_key("commit generated message"),
            direct_action={"kind": "shell_command", "command": "git commit -F -"},
        )
    )

    assert standard.lr_mode == "lr"
    assert standard.payload_bindings == []
    assert payload.lr_mode == "lr_ex"
    assert payload.payload_bindings == [
        {
            "input_name": "commit_message",
            "source_role": "generated_text",
            "source_field": "stdout",
            "stdin_mode": "input_binding",
            "generated_text": True,
        }
    ]
    assert standard.template_id != payload.template_id

    default_candidates = store.retrieve(
        CommandTemplateLookupContext(
            prompt="commit generated message",
            step_description="Commit with the generated message",
            mode="Conversational",
            model_name="fake-model",
            task_type="git.commit",
            tool_type="git.commit",
            intent_type="create",
            tags=["git", "commit"],
            similarity_threshold=0.1,
        )
    )
    payload_candidates = store.retrieve(
        CommandTemplateLookupContext(
            prompt="commit generated message",
            step_description="Commit with the generated message",
            mode="Conversational",
            model_name="fake-model",
            task_type="git.commit",
            tool_type="git.commit",
            intent_type="create",
            tags=["git", "commit"],
            lr_mode="lr_ex",
            similarity_threshold=0.1,
        )
    )

    assert [candidate.entry.template_id for candidate in default_candidates] == [
        standard.template_id
    ]
    assert [candidate.entry.template_id for candidate in payload_candidates] == [
        payload.template_id
    ]
    assert store.retrieve_exact_step(exact_step_key("commit generated message")) == []
    assert (
        store.retrieve_exact_step(
            exact_step_key("commit generated message"), lr_mode="lr_ex"
        )[0].entry.template_id
        == payload.template_id
    )


def test_command_template_cache_retrieves_lrex_shape_candidates(
    tmp_path: Path,
) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    payload = store.upsert_entry(
        CommandTemplateWrite(
            prompt="count rows from prior output",
            step_description="Count rows from prior output",
            mode="Conversational",
            model_name="fake-model",
            task_type="text.output",
            tool_type="text.output",
            intent_type="count",
            tags=["count", "rows"],
            lr_mode="lr_ex",
            payload_bindings=[
                {
                    "input_name": "rows_payload",
                    "source_role": "runtime_output",
                    "source_field": "stdout",
                    "stdin_mode": "input_binding",
                }
            ],
            command_template="wc -l",
            observed_command="wc -l",
            exact_step_key=exact_step_key("old count rows step"),
            direct_action={"kind": "shell_command", "command": "wc -l"},
            risk="low",
            effect_intent="read_only",
        )
    )
    store.upsert_entry(
        CommandTemplateWrite(
            prompt="count rows from prior output",
            step_description="Count rows from prior output",
            mode="Conversational",
            model_name="fake-model",
            task_type="text.output",
            tool_type="text.output",
            intent_type="count",
            tags=["count", "rows"],
            command_template="wc -l",
            observed_command="wc -l",
            exact_step_key=exact_step_key("standard lr row"),
            direct_action={"kind": "shell_command", "command": "wc -l"},
            risk="low",
            effect_intent="read_only",
        )
    )

    candidates = store.retrieve_lrex_shape_candidates(
        CommandTemplateLookupContext(
            prompt="calculate how many lines are in the previous output",
            step_description="Calculate how many lines are in the previous output",
            mode="Conversational",
            model_name="fake-model",
            task_type="text.output",
            tool_type="text.output",
            intent_type="count",
            tags=["lines", "count"],
        ),
        exact_key=exact_step_key("new count rows step"),
    )

    assert [candidate.entry.template_id for candidate in candidates] == [
        payload.template_id
    ]


def test_command_template_cache_treats_unknown_structured_fields_as_wildcards(
    tmp_path: Path,
) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    payload = store.upsert_entry(
        CommandTemplateWrite(
            prompt=(
                "Stage all files, generate a detailed Conventional Commit message. "
                "Use git diff --cached --stat to see the changes and use that "
                "information to generate the description, then commit using that message."
            ),
            step_description="Commit the staged changes with the generated Conventional Commit message.",
            mode="llm_operator",
            gateway_platform="linux",
            task_type="unknown",
            tool_type="unknown",
            intent_type="execute",
            tags=["git", "commit", "message"],
            lr_mode="lr_ex",
            payload_bindings=[
                {
                    "input_name": "commit_message",
                    "source_role": "generated_text",
                    "source_field": "stdout",
                    "stdin_mode": "input_binding",
                    "generated_text": True,
                }
            ],
            command_template="git commit -F -",
            observed_command="git commit -F -",
            risk="medium",
            effect_intent="mutates_state",
        )
    )

    candidates = store.retrieve(
        CommandTemplateLookupContext(
            prompt=(
                "Stage all files, generate a detailed Conventional Commit message. "
                "Use git diff --cached --stat to see what changed and use that "
                "to generate the description, then commit using that message."
            ),
            step_description="Commit the staged files with the generated Conventional Commit message.",
            mode="llm_operator",
            gateway_platform="linux",
            task_type="git",
            tool_type="git",
            intent_type="execute",
            tags=["git", "commit"],
            lr_mode="lr_ex",
        )
    )

    assert [candidate.entry.template_id for candidate in candidates] == [payload.template_id]


def test_command_template_cache_scores_current_step_before_noisy_prompt(
    tmp_path: Path,
) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    entry = store.upsert_entry(
        CommandTemplateWrite(
            prompt="archive logs",
            step_description="Archive the log directory",
            mode="llm_operator",
            model_name="fake-model",
            model_family="fake-model",
            gateway_platform="linux",
            task_type="unknown",
            tool_type="unknown",
            intent_type="execute",
            tags=["archive", "logs"],
            command_template="tar -czf logs.tgz logs",
            observed_command="tar -czf logs.tgz logs",
            risk="low",
            effect_intent="read_only",
        )
    )

    candidates = store.retrieve(
        CommandTemplateLookupContext(
            prompt=(
                "Streaming step 1 of 4: Archive logs. "
                "Original request background: archive logs, inspect the archive, "
                "generate a summary message, upload the report, and notify the team. "
                "Later steps reserved for future calls: inspect archive | generate "
                "summary message | notify the team."
            ),
            step_description="Archive logs.",
            mode="llm_operator",
            model_name="fake-model",
            model_family="fake-model",
            gateway_platform="linux",
            task_type="unknown",
            tool_type="unknown",
            intent_type="execute",
            tags=["archive", "logs"],
            similarity_threshold=0.7,
            secondary_similarity_threshold=0.15,
        )
    )

    assert [candidate.entry.template_id for candidate in candidates] == [entry.template_id]
    assert candidates[0].score >= 0.7


def test_command_template_cache_secondary_threshold_blocks_weak_background(
    tmp_path: Path,
) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    store.upsert_entry(
        CommandTemplateWrite(
            prompt="archive logs",
            step_description="Archive logs",
            mode="llm_operator",
            model_name="fake-model",
            model_family="fake-model",
            gateway_platform="linux",
            task_type="unknown",
            tool_type="unknown",
            intent_type="execute",
            tags=["archive", "logs"],
            command_template="tar -czf logs.tgz logs",
            observed_command="tar -czf logs.tgz logs",
            risk="low",
            effect_intent="read_only",
        )
    )

    candidates = store.retrieve(
        CommandTemplateLookupContext(
            prompt=(
                "Streaming step 1 of 4: Archive logs. "
                "Unrelated background: alpha beta gamma delta epsilon zeta eta "
                "theta iota kappa lambda mu nu xi omicron pi rho sigma tau."
            ),
            step_description="Archive logs.",
            mode="llm_operator",
            model_name="fake-model",
            model_family="fake-model",
            gateway_platform="linux",
            task_type="unknown",
            tool_type="unknown",
            intent_type="execute",
            tags=["archive", "logs"],
            similarity_threshold=0.7,
            secondary_similarity_threshold=0.9,
        )
    )

    assert candidates == []


def test_command_template_cache_retrieves_exact_step_direct_action(
    tmp_path: Path,
) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    key = exact_step_key("list all docker images")
    entry = store.upsert_entry(
        CommandTemplateWrite(
            prompt="list all docker images",
            step_description="List Docker images",
            command_template="docker images --format json",
            observed_command="docker images --format json",
            exact_step_key=key,
            exact_step_prompt_excerpt="list all docker images",
            direct_action={
                "action_id": "action_cached",
                "task_id": "task_cached",
                "kind": "shell_command",
                "command": "docker images --format json",
                "reason": "List Docker images.",
            },
            risk="low",
            effect_intent="read_only",
            status="success",
        )
    )

    candidates = store.retrieve_exact_step(key)

    assert len(candidates) == 1
    assert candidates[0].entry.template_id == entry.template_id
    assert candidates[0].entry.direct_action["command"] == "docker images --format json"


def test_command_template_exact_step_respects_replay_environment(
    tmp_path: Path,
) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    key = exact_step_key("show current branch")
    entry = store.upsert_entry(
        CommandTemplateWrite(
            prompt="show current branch",
            step_description="Show current branch",
            cwd=str(tmp_path / "repo-a"),
            gateway_platform="linux",
            command_template="git branch --show-current",
            observed_command="git branch --show-current",
            exact_step_key=key,
            direct_action={
                "kind": "shell_command",
                "command": "git branch --show-current",
                "cwd": str(tmp_path / "repo-a"),
                "inputs": {},
                "input_bindings": [],
                "stdin_mode": "none",
            },
            status="success",
        )
    )

    assert store.retrieve_exact_step(
        key,
        cwd=str(tmp_path / "repo-b"),
        gateway_platform="linux",
    ) == []
    assert store.retrieve_exact_step(
        key,
        cwd=str(tmp_path / "repo-a"),
        gateway_platform="linux",
    )[0].entry.template_id == entry.template_id


def test_command_template_exact_step_failed_reuse_is_suppressed(tmp_path: Path) -> None:
    store = AgentCommandTemplateCacheStore(tmp_path / "commands.db")
    key = exact_step_key("list all docker images")
    entry = store.upsert_entry(
        CommandTemplateWrite(
            prompt="list all docker images",
            command_template="docker images --format json",
            observed_command="docker images --format json",
            exact_step_key=key,
            direct_action={
                "action_id": "action_cached",
                "task_id": "task_cached",
                "kind": "shell_command",
                "command": "docker images --format json",
                "reason": "List Docker images.",
            },
            status="success",
        )
    )

    store.mark_failed(entry.template_id, failure_category="direct_replay_failed")

    assert store.retrieve_exact_step(key) == []


def test_lrdirect_canonical_key_ignores_prompt_context_drift() -> None:
    first = lrdirect_canonical_step_key(
        step_description="Stage all files using git add.",
        semantic_verb="execute",
        object_type="unknown",
        step_locator="index=1",
    )
    rewritten_prompt_variant = lrdirect_canonical_step_key(
        step_description="Stage all files using git add.",
        semantic_verb="execute",
        object_type="",
        step_locator="index=1",
    )
    later_steps_variant = lrdirect_canonical_step_key(
        step_description="Stage all files using git add.",
        semantic_verb="execute",
        object_type="none",
        step_locator="index=1",
    )

    assert first == rewritten_prompt_variant == later_steps_variant


def test_lrdirect_canonical_step_normalizes_only_generic_text_formatting() -> None:
    assert normalize_lrdirect_step_text(
        "  Stage   ALL files using `git add`!!! "
    ) == normalize_lrdirect_step_text(
        "stage all files using git add"
    )
