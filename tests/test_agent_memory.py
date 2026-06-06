from __future__ import annotations

from pathlib import Path

from agent_runtime.core.domain_hints import detect_domain_hints
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.core.types import UserRequest
from agent_runtime.llm.reproducibility import PlanningTrace
from agent_runtime.memory import (
    AgentMemoryStore,
    MemoryEntryCreate,
    MemoryRetrievalContext,
    evaluate_memory_guard_rules,
    enrich_memory_retrieval_hints,
    memory_directives_for_prompt_stage,
    memory_prompt_lines_from_context,
    record_scope_memory_question_skip,
    scope_observability_details,
    set_active_memory_scope,
    update_memory_scope_trace,
)


def test_agent_memory_store_persists_entries_and_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_memory.db"
    store = AgentMemoryStore(db_path)

    entry = store.create_entry(
        MemoryEntryCreate(
            instruction="Use stable machine-readable command output before parsing.",
            summary="Prefer parseable command output.",
            scope="global",
            tags=["git", "parsing"],
        ),
        actor="user",
    )

    reopened = AgentMemoryStore(db_path)
    loaded = reopened.get_entry(entry.memory_id)
    assert loaded is not None
    assert loaded.instruction == "Use stable machine-readable command output before parsing."
    assert loaded.tags == ["git", "parsing"]

    events = reopened.audit_events(entry.memory_id)
    assert [event.event_type for event in events] == ["memory.created"]


def test_agent_memory_retrieval_prioritizes_exact_model_and_excludes_retired(tmp_path: Path) -> None:
    store = AgentMemoryStore(tmp_path / "agent_memory.db")
    exact = store.create_entry(
        MemoryEntryCreate(
            instruction="Exact model hint for git staging.",
            summary="Exact model",
            scope="exact_model",
            model_name="qwen-test",
            tags=["git"],
        )
    )
    family = store.create_entry(
        MemoryEntryCreate(
            instruction="Family hint for git staging.",
            summary="Family",
            scope="model_family",
            model_family="qwen",
            tags=["git"],
        )
    )
    global_entry = store.create_entry(
        MemoryEntryCreate(
            instruction="Global hint for git staging.",
            summary="Global",
            scope="global",
            tags=["git"],
        )
    )
    retired = store.create_entry(
        MemoryEntryCreate(
            instruction="Retired hint should not appear.",
            summary="Retired",
            scope="global",
            tags=["git"],
        )
    )
    store.set_status(retired.memory_id, "retired")

    results = store.retrieve(
        MemoryRetrievalContext(
            prompt="stage git changes",
            model_name="qwen-test",
            model_family="qwen",
            tags=["git"],
            limit=5,
        )
    )

    assert [entry.memory_id for entry in results] == [
        exact.memory_id,
        family.memory_id,
        global_entry.memory_id,
    ]
    assert results[0].use_count == 1
    assert store.get_entry(exact.memory_id).use_count == 1  # type: ignore[union-attr]


def test_agent_memory_retrieval_excludes_unrelated_active_memories(tmp_path: Path) -> None:
    store = AgentMemoryStore(tmp_path / "agent_memory.db")
    git_entry = store.create_entry(
        MemoryEntryCreate(
            instruction="For git staging, verify staged files with git diff --cached.",
            summary="Git staging verification.",
            scope="global",
            task_type="git",
            tool_type="shell",
            intent_type="verify_state",
            tags=["git", "staging"],
        )
    )
    docker_entry = store.create_entry(
        MemoryEntryCreate(
            instruction="For Docker image sizes, use parseable size output before summing.",
            summary="Docker size parsing.",
            scope="global",
            task_type="docker",
            tool_type="shell",
            intent_type="calculate_size",
            tags=["docker", "images"],
        )
    )

    results = store.retrieve(
        MemoryRetrievalContext(
            prompt="stage all git changes",
            task_type="git",
            tool_type="shell",
            intent_type="verify_state",
            tags=["git", "staging"],
        )
    )

    assert [entry.memory_id for entry in results] == [git_entry.memory_id]
    assert results[0].use_count == 1
    assert store.get_entry(git_entry.memory_id).use_count == 1  # type: ignore[union-attr]
    assert store.get_entry(docker_entry.memory_id).use_count == 0  # type: ignore[union-attr]


def test_agent_memory_hints_promote_obvious_git_prompt_from_generic_task() -> None:
    hints = enrich_memory_retrieval_hints(
        "stage and commit all changes and push them using description",
        task_type="simple_tool_task",
        tags=[],
    )

    assert hints.task_type == "git"
    assert "git" in hints.tags
    assert "commit" in hints.tags
    assert "stage" in hints.tags
    assert "push" in hints.tags


def test_agent_memory_hints_promote_obvious_git_prompt_from_specific_goal() -> None:
    hints = enrich_memory_retrieval_hints(
        "stage all git changes",
        task_type="mutation_succeeded",
        tags=[],
    )

    assert hints.task_type == "git"
    assert "git" in hints.tags
    assert "stage" in hints.tags


def test_domain_hints_detect_docker_status_prompt() -> None:
    detection = detect_domain_hints("Are there any docker containers running?")

    assert detection.single_domain == "docker"
    assert detection.tags == ["docker"]


def test_domain_hints_detect_sql_and_dicom_relationship_prompt() -> None:
    detection = detect_domain_hints(
        "Which SQL join links RTPlan.SOPInstanceUID to BeamSequence in Orthanc?"
    )

    assert detection.single_domain == ""
    assert {"sql", "join", "dicom", "orthanc", "rtplan"} <= set(detection.tags)


def test_domain_hints_do_not_force_mixed_domain_prompt() -> None:
    detection = detect_domain_hints("stage git changes and list docker containers")
    hints = enrich_memory_retrieval_hints(
        "stage git changes and list docker containers",
        task_type="simple_tool_task",
    )

    assert detection.single_domain == ""
    assert {"git", "stage", "docker"} <= set(detection.tags)
    assert hints.task_type == "simple_tool_task"


def test_memory_guard_rules_block_git_init_unless_explicit() -> None:
    directives = [
        {
            "memory_id": "mem_git_no_init",
            "instruction": "Never run git init unless explicitly requested.",
            "summary": "Do not create git repositories implicitly.",
            "strength": "required_unless_conflict",
            "blocked_examples": [],
        }
    ]

    blocked = evaluate_memory_guard_rules(
        user_prompt="commit the current changes",
        command_text="git init && git add .",
        directives=directives,
    )
    allowed = evaluate_memory_guard_rules(
        user_prompt="create a new git repository here",
        command_text="git init",
        directives=directives,
    )

    assert blocked
    assert blocked[0]["rule_id"] == "git_repo_guard"
    assert allowed == []


def test_validation_policy_memory_retrieval_matches_error_and_intent(tmp_path: Path) -> None:
    store = AgentMemoryStore(tmp_path / "agent_memory.db")
    matching = store.create_entry(
        MemoryEntryCreate(
            instruction="Allow angle-bracket syntax when it is literal source payload in a heredoc file write.",
            summary="C++ include literal payload.",
            memory_kind="validation_policy",
            task_type="cpp",
            tool_type="shell_command",
            intent_type="create",
            validator_error_type="unresolved_shell_placeholder",
            safe_examples=["cat > main.cpp <<'CPP'\n#include <iostream>\nCPP"],
            blocked_examples=["echo '<calculated_value>'"],
            tags=["literal_payload", "cpp"],
        )
    )
    wrong_error = store.create_entry(
        MemoryEntryCreate(
            instruction="Unrelated policy.",
            summary="Wrong error.",
            memory_kind="validation_policy",
            task_type="cpp",
            tool_type="shell_command",
            intent_type="create",
            validator_error_type="python_action_contract_shape",
            tags=["literal_payload", "cpp"],
        )
    )
    wrong_intent = store.create_entry(
        MemoryEntryCreate(
            instruction="Same validator error but for a different task intent.",
            summary="Wrong intent.",
            memory_kind="validation_policy",
            task_type="docker",
            tool_type="shell_command",
            intent_type="inspect",
            validator_error_type="unresolved_shell_placeholder",
            tags=["docker"],
        )
    )

    normal_results = store.retrieve(
        MemoryRetrievalContext(
            prompt="create a C++ file",
            task_type="cpp",
            tool_type="shell_command",
            intent_type="create",
            tags=["literal_payload", "cpp"],
        ),
        record_use=False,
    )
    policy_results = store.retrieve(
        MemoryRetrievalContext(
            prompt="create a C++ file with #include <iostream>",
            memory_kind="validation_policy",
            task_type="cpp",
            tool_type="shell_command",
            intent_type="create",
            validator_error_type="unresolved_shell_placeholder",
            tags=["literal_payload", "cpp"],
        ),
        record_use=False,
    )

    assert [entry.memory_id for entry in policy_results] == [matching.memory_id]
    assert matching.memory_id not in [entry.memory_id for entry in normal_results]
    assert wrong_error.memory_id not in [entry.memory_id for entry in policy_results]
    assert wrong_intent.memory_id not in [entry.memory_id for entry in policy_results]


def test_agent_memory_retrieval_does_not_rank_by_use_count(tmp_path: Path) -> None:
    store = AgentMemoryStore(tmp_path / "agent_memory.db")
    git_entry = store.create_entry(
        MemoryEntryCreate(
            instruction="For git status, verify staged files with git diff --cached.",
            summary="Git verification.",
            scope="global",
            task_type="git",
            tool_type="shell",
            intent_type="verify_state",
            tags=["git", "staging"],
        )
    )
    docker_entry = store.create_entry(
        MemoryEntryCreate(
            instruction="For Docker image size totals, parse each image size and sum units carefully.",
            summary="Docker image size parsing.",
            scope="global",
            task_type="docker",
            tool_type="shell",
            intent_type="calculate_size",
            tags=["docker", "images", "size"],
        )
    )

    for _ in range(8):
        store.retrieve(
            MemoryRetrievalContext(
                prompt="stage git changes and verify status",
                task_type="git",
                tool_type="shell",
                intent_type="verify_state",
                tags=["git", "staging"],
            )
        )

    results = store.retrieve(
        MemoryRetrievalContext(
            prompt="list docker images and calculate total size in GB",
            task_type="docker",
            tool_type="shell",
            intent_type="calculate_size",
            tags=["docker", "images", "size"],
            limit=5,
        )
    )

    assert [entry.memory_id for entry in results] == [docker_entry.memory_id]
    assert store.get_entry(git_entry.memory_id).use_count == 8  # type: ignore[union-attr]


def test_agent_memory_preview_retrieval_does_not_increment_use_count(tmp_path: Path) -> None:
    store = AgentMemoryStore(tmp_path / "agent_memory.db")
    entry = store.create_entry(
        MemoryEntryCreate(
            instruction="For Docker image size totals, parse each image size and sum units carefully.",
            summary="Docker image size parsing.",
            scope="global",
            task_type="docker",
            tool_type="shell",
            intent_type="calculate_size",
            tags=["docker", "images", "size"],
        )
    )

    results = store.retrieve(
        MemoryRetrievalContext(
            prompt="list docker images and calculate total size in GB",
            task_type="docker",
            tool_type="shell",
            intent_type="calculate_size",
            tags=["docker", "images", "size"],
        ),
        record_use=False,
    )

    assert [result.memory_id for result in results] == [entry.memory_id]
    assert results[0].use_count == 0
    assert store.get_entry(entry.memory_id).use_count == 0  # type: ignore[union-attr]


def test_agent_memory_proposals_apply_only_after_user_action(tmp_path: Path) -> None:
    store = AgentMemoryStore(tmp_path / "agent_memory.db")
    proposal = store.create_proposal(
        proposal_type="create",
        draft={
            "instruction": "When a requested value is discoverable, inspect it before asking.",
            "summary": "Discover before asking.",
            "scope": "global",
            "tags": ["clarification"],
        },
        provenance="feedback",
    )

    assert store.list_entries(status="active") == []

    entry = store.apply_proposal(proposal.proposal_id)

    assert entry is not None
    assert entry.provenance == "feedback"
    assert store.get_proposal(proposal.proposal_id).status == "applied"  # type: ignore[union-attr]


def test_agent_memory_prompt_helper_marks_memory_as_constraints() -> None:
    request = UserRequest(
        raw_prompt="stage changes",
        session_context={
            "agent_memory": [
                {
                    "memory_id": "mem_1",
                    "instruction": "Prefer verification commands that match the exact postcondition.",
                    "summary": "Verify the requested postcondition.",
                    "scope": "global",
                    "tags": ["git"],
                }
            ]
        },
    )

    prompt = "\n".join(memory_prompt_lines_from_context(request))

    assert "User-approved persistent memory constraints for this request" in prompt
    assert "must_consider" in prompt
    assert "current user request" in prompt
    assert "Prefer verification commands" in prompt


def test_scoped_memory_prompt_uses_only_active_matching_stage_and_task() -> None:
    scoped_directive = {
        "memory_id": "mem_scoped",
        "instruction": "Use the scoped plan memory.",
        "applies_to": ["operator_plan"],
        "strength": "must_consider",
    }
    stale_directive = {
        "memory_id": "mem_stale",
        "instruction": "This stale memory must not render.",
        "applies_to": ["operator_plan"],
        "strength": "must_consider",
    }
    request = UserRequest(
        raw_prompt="run task two",
        session_context={
            "operator_streaming_current_task": {"task_id": "task_1"},
            "agent_memory_active_scope": {
                "scope_id": "scope_task_1_plan",
                "stage": "operator_plan",
                "task_id": "task_1",
                "directives": [scoped_directive],
            },
            "agent_memory_scoped_cache": {
                "scope_task_1_plan": {"directives": [scoped_directive]}
            },
            "agent_memory_directives": [stale_directive],
        },
    )

    plan_directives = memory_directives_for_prompt_stage(request, stage="operator_plan")
    final_directives = memory_directives_for_prompt_stage(request, stage="final_answer")

    assert [directive["memory_id"] for directive in plan_directives] == ["mem_scoped"]
    assert final_directives == []

    request.session_context["operator_streaming_current_task"] = {"task_id": "task_2"}

    assert memory_directives_for_prompt_stage(request, stage="operator_plan") == []


def test_scoped_memory_cache_reuse_keeps_task_scopes_isolated() -> None:
    task_1_directive = {
        "memory_id": "mem_task_1",
        "instruction": "Use task 1 memory.",
        "applies_to": ["operator_plan"],
        "strength": "must_consider",
    }
    task_2_directive = {
        "memory_id": "mem_task_2",
        "instruction": "Use task 2 memory.",
        "applies_to": ["operator_plan"],
        "strength": "must_consider",
    }
    request = UserRequest(
        raw_prompt="run scoped tasks",
        session_context={"operator_streaming_current_task": {"task_id": "task-1"}},
    )
    scope_1 = {
        "scope_id": "scope_task_1_plan",
        "stage": "operator_plan",
        "task_id": "task-1",
        "directives": [task_1_directive],
    }
    scope_2 = {
        "scope_id": "scope_task_2_plan",
        "stage": "operator_plan",
        "task_id": "task-2",
        "directives": [task_2_directive],
    }

    set_active_memory_scope(request.session_context, scope=scope_1)
    assert [item["memory_id"] for item in memory_directives_for_prompt_stage(
        request,
        stage="operator_plan",
    )] == ["mem_task_1"]

    request.session_context["operator_streaming_current_task"] = {"task_id": "task-2"}
    set_active_memory_scope(request.session_context, scope=scope_2)
    assert [item["memory_id"] for item in memory_directives_for_prompt_stage(
        request,
        stage="operator_plan",
    )] == ["mem_task_2"]

    request.session_context["operator_streaming_current_task"] = {"task_id": "task-1"}
    cached_scope_1 = request.session_context["agent_memory_scoped_cache"]["scope_task_1_plan"]
    set_active_memory_scope(request.session_context, scope=cached_scope_1)

    assert [item["memory_id"] for item in memory_directives_for_prompt_stage(
        request,
        stage="operator_plan",
    )] == ["mem_task_1"]
    assert set(request.session_context["agent_memory_scoped_cache"]) == {
        "scope_task_1_plan",
        "scope_task_2_plan",
    }


def test_scoped_memory_trace_records_retrieved_rendered_filtered_and_skipped_ids() -> None:
    rendered_directive = {
        "memory_id": "mem_rendered",
        "instruction": "Use this during planning.",
        "applies_to": ["operator_plan"],
        "strength": "must_consider",
    }
    filtered_directive = {
        "memory_id": "mem_filtered",
        "instruction": "Use this for final answers only.",
        "applies_to": ["final_answer"],
        "strength": "must_consider",
    }
    trace = PlanningTrace(request_id="req-memory-scope-trace", raw_prompt="plan task 1")
    request = UserRequest(
        raw_prompt="plan task 1",
        session_context={"operator_streaming_current_task": {"task_id": "task-1"}},
        safety_context={"planning_trace": trace},
    )
    scope = {
        "scope_id": "scope_task_1_plan",
        "stage": "operator_plan",
        "task_id": "task-1",
        "source": "test",
        "retrieval_context": {"prompt": "task 1"},
        "memory_ids": ["mem_rendered", "mem_filtered"],
        "retrieved_memory_ids": ["mem_rendered", "mem_filtered"],
        "retrieved_candidate_ids": ["mem_rendered", "mem_filtered"],
        "directives": [rendered_directive, filtered_directive],
    }
    set_active_memory_scope(request.session_context, scope=scope)
    update_memory_scope_trace(
        trace.metadata,
        scope_observability_details(scope),
        request.session_context,
    )

    rendered = memory_directives_for_prompt_stage(request, stage="operator_plan")
    record_scope_memory_question_skip(
        request,
        scope_id="scope_task_1_plan",
        stage="operator_plan",
        task_id="task-1",
        memory_id="mem_filtered",
    )

    assert [item["memory_id"] for item in rendered] == ["mem_rendered"]
    diagnostics = trace.metadata["agent_memory_scopes"]["scope_task_1_plan"]
    assert diagnostics["retrieved_candidate_ids"] == ["mem_rendered", "mem_filtered"]
    assert diagnostics["retrieved_memory_ids"] == ["mem_rendered", "mem_filtered"]
    assert diagnostics["rendered_directive_ids"] == ["mem_rendered"]
    assert diagnostics["filtered_directive_ids"] == ["mem_filtered"]
    assert diagnostics["skipped_memory_question_ids"] == ["mem_filtered"]
    assert diagnostics["task_id"] == "task-1"


def test_trace_restore_restores_scoped_cache_without_activating_old_scope() -> None:
    stale_directive = {
        "memory_id": "mem_old_task",
        "instruction": "This old task memory must not become active.",
        "applies_to": ["operator_plan"],
        "strength": "must_consider",
    }
    trace = PlanningTrace(
        request_id="req-memory-restore",
        raw_prompt="continue new task",
        metadata={
            "agent_memory_active_scope": {
                "scope_id": "scope_old_task",
                "stage": "operator_plan",
                "task_id": "task-old",
                "directives": [stale_directive],
            },
            "agent_memory_scoped_cache": {
                "scope_old_task": {
                    "scope_id": "scope_old_task",
                    "stage": "operator_plan",
                    "task_id": "task-old",
                    "directives": [stale_directive],
                }
            },
            "agent_memory_scopes": {
                "scope_old_task": {
                    "scope_id": "scope_old_task",
                    "stage": "operator_plan",
                    "task_id": "task-old",
                    "retrieved_memory_ids": ["mem_old_task"],
                }
            },
            "agent_memory_directives": [stale_directive],
        },
    )
    request = UserRequest(
        raw_prompt="continue new task",
        session_context={"operator_streaming_current_task": {"task_id": "task-new"}},
    )

    restored = AgentRuntime._restore_agent_memory_from_trace(request, trace, {})

    assert "agent_memory_scoped_cache" in restored
    assert "agent_memory_active_scope" not in request.session_context
    assert "agent_memory_directives" not in request.session_context
    assert memory_directives_for_prompt_stage(request, stage="operator_plan") == []


def test_agent_memory_retrieve_matches_explains_score(tmp_path: Path) -> None:
    store = AgentMemoryStore(tmp_path / "agent_memory.db")
    entry = store.create_entry(
        MemoryEntryCreate(
            instruction="For git commits, prove the directory is a repo before staging.",
            summary="Check git repository before commit.",
            task_type="git",
            tool_type="shell",
            intent_type="planning",
            tags=["git", "repo"],
        )
    )

    matches = store.retrieve_matches(
        MemoryRetrievalContext(
            prompt="commit my changes",
            task_type="git",
            tool_type="shell",
            intent_type="planning",
            tags=["git", "repo"],
        )
    )

    assert [match.entry.memory_id for match in matches] == [entry.memory_id]
    assert matches[0].score > 0
    assert "task_type:git" in matches[0].match_reasons
    assert "tool_type:shell" in matches[0].match_reasons
    assert "tags:git,repo" in matches[0].match_reasons


def test_agent_memory_retrieval_excludes_unrelated_unstructured_global_memory(tmp_path: Path) -> None:
    store = AgentMemoryStore(tmp_path / "agent_memory.db")
    marker = store.create_entry(
        MemoryEntryCreate(
            instruction="When user asks marker OF_MEM_IMPACT_ABC, answer exactly MEMORY_HELPED_ABC.",
            summary="Simple marker response.",
            scope="global",
        )
    )
    git_entry = store.create_entry(
        MemoryEntryCreate(
            instruction="Before git commit, prove the directory is an existing git repository.",
            summary="Check repo before commit.",
            task_type="git",
            tool_type="shell",
            intent_type="planning",
            tags=["git", "repo"],
        )
    )

    results = store.retrieve(
        MemoryRetrievalContext(
            prompt="commit current changes",
            task_type="git",
            tool_type="shell",
            intent_type="planning",
            tags=["git", "repo"],
        )
    )

    assert [entry.memory_id for entry in results] == [git_entry.memory_id]
    assert store.get_entry(marker.memory_id).use_count == 0  # type: ignore[union-attr]
