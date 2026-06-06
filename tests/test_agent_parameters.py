from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_runtime.api.app import create_app
from agent_runtime.api.config import Settings
from agent_runtime.capabilities.parameters import (
    RuntimeInspectParameterStoreCapability,
    RuntimeManageParameterStoreCapability,
)
from agent_runtime.capabilities import build_default_registry
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.core.types import UserRequest
from agent_runtime.execution.engine import ExecutionEngine
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.operator.prompts import build_operator_plan_prompt
from agent_runtime.operator.user_macros import USER_MACRO_PRIVATE_CONTEXT_KEY
from agent_runtime.output_pipeline.orchestrator import OutputPipelineOrchestrator
from agent_runtime.parameters import (
    AgentParameterCreate,
    AgentParameterDraftRequest,
    AgentParameterStore,
    AgentParameterUpdate,
    PARAMETER_AGENT_CONTEXT_KEY,
    draft_parameter_from_prompt,
    masked_parameter_summary,
    parameter_clarification_choices,
    parameter_matches_context,
    parameter_prompt_lines_from_context,
    parameter_shell_env,
)


class _Runtime:
    def handle_request(self, raw_prompt: str, context: dict | None = None) -> str:
        self.last_context = dict(context or {})
        return f"handled: {raw_prompt}"


class _ParameterDraftLLM:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict) -> dict:  # type: ignore[no-untyped-def]
        self.prompts.append(prompt)
        return self.response


def _create_canonical_profile(store: AgentParameterStore, *, key: str = "canonica_v1") -> None:
    store.create(
        AgentParameterCreate(
            key=key,
            value_json={
                "dbname": "CANONICAL_v1",
                "user": "jimSolomon@mednet.ucla.edu",
                "password": "pass123",
                "host": "10.44.102.204",
                "port": 9501,
                "save_dir": "outputs",
                "batch_size": 5,
            },
            sensitive=True,
        )
    )


def test_parameter_store_crud_masking_retrieval_env_and_audit(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    record = store.create(
        AgentParameterCreate(
            key="DICOM DB",
            value_json={"host": "10.4.4.4", "port": 9045, "database": "dicom", "password": "p@ssw0rd-raw"},
            context_json={
                "version": 1,
                "user_provided_domain_context": {
                    "summary": "DICOM database for imaging tasks.",
                    "prompt_guidance": "Use this for DICOM read-only queries.",
                    "clarification_guidance": "",
                    "concepts": [],
                    "metrics": [],
                    "relationships": [],
                },
            },
            description="DICOM database connection",
            aliases=["imaging database"],
            tags=["dicom", "database"],
            sensitive=True,
        )
    )

    assert record.normalized_key == "dicom_db"
    assert store.get("dicom db").key == "DICOM DB"
    with pytest.raises(sqlite3.IntegrityError):
        store.create(AgentParameterCreate(key="dicom_db", value_json={"host": "other"}))

    summary = masked_parameter_summary(record)
    dumped = summary.model_dump(mode="json")
    assert "secret" not in str(dumped)
    assert dumped["masked_value_json"]["password"] == "••••"
    assert dumped["context_json"]["summary"] == "DICOM database for imaging tasks."
    assert "OF_PARAM_DICOM_DB_HOST" in dumped["env"]
    assert parameter_shell_env(record)["OF_PARAM_DICOM_DB_PASSWORD"] == "p@ssw0rd-raw"
    assert all("CONTEXT" not in key for key in parameter_shell_env(record))

    matches = store.retrieve_matches("run a query against the DICOM DB", record_use=True)
    assert matches and matches[0].record.normalized_key == "dicom_db"
    used = store.get("DICOM DB")
    assert used.use_count == 1
    stored_key_matches = store.retrieve_matches("typein using password stored in dicom_db", record_use=False)
    assert stored_key_matches and stored_key_matches[0].exact is True
    lines = parameter_prompt_lines_from_context({"agent_parameter_store_matches": [matches[0].summary.model_dump(mode="json")]})
    assert "DICOM DB" in "\n".join(lines)
    assert "DICOM database for imaging tasks" in "\n".join(lines)
    assert "p@ssw0rd-raw" not in "\n".join(lines)

    ssh_key = store.create(
        AgentParameterCreate(
            key="sshgit_key",
            value_json={"private_key_path": "/home/user/.ssh/id_ed25519_git"},
            description="Git SSH key used for pushes",
            aliases=["git ssh key"],
            tags=["git", "ssh"],
            sensitive=True,
        )
    )
    bare_key_matches = store.retrieve_matches(
        'commit all changes with description "macro tweaks for parameter store" and push using the sshgit_key',
        record_use=False,
    )
    assert bare_key_matches
    assert bare_key_matches[0].record.normalized_key == ssh_key.normalized_key
    assert bare_key_matches[0].exact is True
    for spelling in ("sshgit-key", "sshgit key"):
        variant_matches = store.retrieve_matches(f"git push using {spelling}", record_use=False)
        assert variant_matches
        assert variant_matches[0].record.normalized_key == ssh_key.normalized_key
        assert variant_matches[0].exact is True
    bare_key_lines = parameter_prompt_lines_from_context(
        {
            "agent_parameter_store_matches": [
                {
                    **bare_key_matches[0].summary.model_dump(mode="json"),
                    "exact": bare_key_matches[0].exact,
                }
            ]
        }
    )
    assert "match=exact" in "\n".join(bare_key_lines)

    revealed = store.reveal("dicom_db")
    assert revealed.value_json["password"] == "p@ssw0rd-raw"
    updated = store.update("dicom_db", AgentParameterUpdate(description="Updated"), actor="test")
    assert updated.description == "Updated"
    assert store.delete("dicom_db")
    assert store.get("dicom_db") is None
    audit_text = str([event.model_dump(mode="json") for event in store.audit_events("dicom_db")])
    assert "p@ssw0rd-raw" not in audit_text
    assert "parameter.created" in audit_text
    assert "parameter.deleted" in audit_text


def test_database_parameter_matches_identity_values_and_near_miss_key(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    _create_canonical_profile(store)

    prompts = [
        "list all schema in canonical_v1",
        "list schemas in CANONICAL_v1",
        "list schemas in the canonical database",
    ]
    for prompt in prompts:
        matches = store.retrieve_matches(prompt, record_use=False)
        assert matches
        assert matches[0].record.normalized_key == "canonica_v1"

    exact_matches = store.retrieve_matches("list all schema in canonical_v1", record_use=False)
    assert exact_matches[0].exact is True
    reason_text = " ".join(exact_matches[0].match_reasons)
    assert "explicit_key_or_identity" in reason_text


def test_database_parameter_prompt_context_masks_and_renders_env_recipes(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    _create_canonical_profile(store)

    matches = store.retrieve_matches("list all schema in canonical_v1", record_use=False)
    context = parameter_matches_context(matches)
    text = "\n".join(parameter_prompt_lines_from_context(context, stage="operator_plan"))

    assert "profile=database_connection" in text
    assert "engine=postgresql" in text
    assert "identity=dbname=CANONICAL_v1" in text
    assert "OF_PARAM_CANONICA_V1_HOST" in text
    assert "OF_PARAM_CANONICA_V1_PORT" in text
    assert "OF_PARAM_CANONICA_V1_DBNAME" in text
    assert "OF_PARAM_CANONICA_V1_USER" in text
    assert "OF_PARAM_CANONICA_V1_PASSWORD" in text
    assert "information_schema.schemata" in text
    assert "do not invent OF_INPUT_DB_PATH" in text
    assert "sqlite3 unless" in text
    assert "pass123" not in text
    assert "jimSolomon@mednet.ucla.edu" not in text
    assert "10.44.102.204" not in text


def test_operator_plan_prompt_guides_db_profile_use_without_sqlite_input_path(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    _create_canonical_profile(store)
    matches = store.retrieve_matches("list all schema in canonical_v1", record_use=False)
    context = parameter_matches_context(matches)
    request = UserRequest(
        raw_prompt="list all schema in canonical_v1",
        session_context={
            "agent_parameter_store_matches": context["agent_parameter_store_matches"],
        },
    )

    prompt = build_operator_plan_prompt(request)

    assert "OF_PARAM_CANONICA_V1_DBNAME" in prompt
    assert "OF_PARAM_CANONICA_V1_PASSWORD" in prompt
    assert "PGPASSWORD" in prompt
    assert "information_schema.schemata" in prompt
    assert "do not invent OF_INPUT_DB_PATH" in prompt
    assert 'sqlite3 "$OF_INPUT_DB_PATH"' not in prompt
    assert "pass123" not in prompt
    assert "jimSolomon@mednet.ucla.edu" not in prompt


def test_database_parameter_ambiguity_prompts_clarification_guidance(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    _create_canonical_profile(store, key="canonical_primary")
    store.create(
        AgentParameterCreate(
            key="canonical_reporting",
            value_json={
                "dbname": "CANONICAL_REPORTING",
                "user": "reporting-user",
                "password": "reporting-password",
                "host": "10.44.102.205",
                "port": 9501,
            },
            sensitive=True,
        )
    )

    matches = store.retrieve_matches("list schemas in the canonical database", record_use=False)
    assert len(matches) >= 2
    context = parameter_matches_context(matches)
    text = "\n".join(parameter_prompt_lines_from_context(context, stage="operator_plan"))

    assert "DB ambiguity guidance" in text
    assert "ask the user which parameter key to use" in text
    assert "reporting-password" not in text
    assert "reporting-user" not in text


def test_runtime_uses_llm_adjudication_for_ambiguous_database_profiles(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    _create_canonical_profile(store, key="canonical_primary")
    store.create(
        AgentParameterCreate(
            key="canonical_reporting",
            value_json={
                "dbname": "CANONICAL_REPORTING",
                "user": "reporting-user",
                "password": "reporting-password",
                "host": "10.44.102.205",
                "port": 9501,
            },
            sensitive=True,
        )
    )
    registry = build_default_registry()
    engine = ExecutionEngine(
        registry,
        {"workspace_root": str(tmp_path), "allow_shell_execution": False},
        InMemoryResultStore(),
    )
    llm = _ParameterDraftLLM(
        {
            "decision": "select",
            "selected_normalized_key": "canonical_reporting",
            "confidence": 0.92,
            "reason": "The reporting profile is the best masked candidate.",
        }
    )
    runtime = AgentRuntime(
        llm_client=llm,
        registry=registry,
        execution_engine=engine,
        output_orchestrator=OutputPipelineOrchestrator(),
        parameter_store=store,
    )
    request = runtime._build_user_request("list schemas in the canonical database", {})

    runtime._attach_agent_parameters(request, {})

    keys = [item["normalized_key"] for item in request.session_context["agent_parameters"]]
    assert keys == ["canonical_reporting"]
    assert request.session_context["agent_parameter_db_match_adjudication"]["decision"] == "select"
    assert request.session_context["agent_parameter_db_match_adjudication"]["confidence"] == 0.92
    adjudication_prompt = "\n".join(llm.prompts)
    assert "Adjudicate which masked Agent Parameter Store database profile should be used" in adjudication_prompt
    assert "pass123" not in adjudication_prompt
    assert "reporting-password" not in adjudication_prompt
    assert "jimSolomon@mednet.ucla.edu" not in adjudication_prompt
    assert "reporting-user" not in adjudication_prompt


def test_parameter_draft_fallback_for_dicom_and_password() -> None:
    dicom = draft_parameter_from_prompt(
        AgentParameterDraftRequest(
            prompt="store this DICOM DB connection info 10.4.4.4:9045/dicom_archive",
        ),
        planner_enabled=False,
    )
    assert dicom.is_parameter_request
    draft = dicom.drafts[0]
    assert draft.key == "dicom_db"
    assert draft.value["host"] == "10.4.4.4"
    assert draft.value["port"] == 9045
    assert draft.value["database"] == "dicom_archive"

    password = draft_parameter_from_prompt(
        AgentParameterDraftRequest(prompt="store git ssh password with key git_ssh_password password is s3cr3t"),
        planner_enabled=False,
    )
    assert password.drafts[0].key == "git_ssh_password"
    assert password.drafts[0].value["password"] == "s3cr3t"
    assert password.drafts[0].sensitive is True


def test_parameter_draft_fallback_extracts_pasted_config_block() -> None:
    prompt = """add this to the parameter store
dbname: "CANONICAL_v1"
user: "jimSolomon@mednet.ucla.edu"
password: "pass123"
host: "10.44.102.204"
port: 9501
save_dir: "outputs"
batch_size: 5"""

    response = draft_parameter_from_prompt(
        AgentParameterDraftRequest(prompt=prompt),
        planner_enabled=False,
    )

    assert response.is_parameter_request
    draft = response.drafts[0]
    assert draft.key == "canonical_v1"
    assert draft.value == {
        "dbname": "CANONICAL_v1",
        "user": "jimSolomon@mednet.ucla.edu",
        "password": "pass123",
        "host": "10.44.102.204",
        "port": 9501,
        "save_dir": "outputs",
        "batch_size": 5,
    }
    assert draft.sensitive is True
    assert draft.missing_details == []


def test_parameter_draft_fallback_does_not_parse_port_field_as_host() -> None:
    response = draft_parameter_from_prompt(
        AgentParameterDraftRequest(prompt="add this to the parameter store\nport: 9501"),
        planner_enabled=False,
    )

    assert response.is_parameter_request
    draft = response.drafts[0]
    assert draft.value == {"port": 9501}
    assert "host" not in draft.value
    assert draft.missing_details == ["key"]


def test_parameter_draft_repairs_bad_llm_connection_from_pasted_config_block() -> None:
    prompt = """add this to the parameter store
dbname: "CANONICAL_v1"
user: "jimSolomon@mednet.ucla.edu"
password: "pass123"
host: "10.44.102.204"
port: 9501
save_dir: "outputs"
batch_size: 5"""
    bad_llm = _ParameterDraftLLM(
        {
            "is_parameter_request": True,
            "drafts": [
                {
                    "operation": "create",
                    "key": "store",
                    "value": {
                        "host": "port",
                        "port": 9501,
                        "connection_string": "port:9501",
                    },
                    "sensitive": True,
                    "confidence": 0.88,
                    "rationale": "Bad extraction.",
                }
            ],
            "rationale": "The user wants to save a parameter.",
        }
    )

    response = draft_parameter_from_prompt(
        AgentParameterDraftRequest(prompt=prompt),
        llm_client=bad_llm,
        planner_enabled=True,
    )

    assert bad_llm.prompts
    assert response.is_parameter_request
    draft = response.drafts[0]
    assert draft.key == "canonical_v1"
    assert draft.value["host"] == "10.44.102.204"
    assert draft.value["port"] == 9501
    assert draft.value["dbname"] == "CANONICAL_v1"
    assert draft.value["batch_size"] == 5
    assert draft.value.get("connection_string") is None


def test_parameter_draft_distinguishes_get_from_set_intent() -> None:
    retrieval_prompt = (
        'commit all changes with description "implement macro tweaks for parameter store and other ui tweaks" '
        "and push all changes using the git ssh key from store"
    )
    fallback = draft_parameter_from_prompt(
        AgentParameterDraftRequest(prompt=retrieval_prompt),
        planner_enabled=False,
    )
    assert fallback.is_parameter_request is False

    get_llm = _ParameterDraftLLM(
        {
            "is_parameter_request": False,
            "drafts": [],
            "rationale": "The user wants to use an existing stored key, not save one.",
        }
    )
    planned_get = draft_parameter_from_prompt(
        AgentParameterDraftRequest(prompt=retrieval_prompt),
        llm_client=get_llm,
        planner_enabled=True,
    )
    assert get_llm.prompts
    assert planned_get.is_parameter_request is False
    assert planned_get.drafts == []

    set_llm = _ParameterDraftLLM(
        {
            "is_parameter_request": True,
            "drafts": [
                {
                    "operation": "create",
                    "key": "deploy_password",
                    "value": {"password": "abc123"},
                    "sensitive": True,
                    "confidence": 0.91,
                    "rationale": "The user is saving a credential.",
                }
            ],
            "rationale": "The user wants to save a new parameter.",
        }
    )
    planned_set = draft_parameter_from_prompt(
        AgentParameterDraftRequest(prompt="save this deploy password with key deploy_password password is abc123"),
        llm_client=set_llm,
        planner_enabled=True,
    )
    assert planned_set.is_parameter_request is True
    assert planned_set.drafts[0].key == "deploy_password"
    assert planned_set.drafts[0].value["password"] == "abc123"


def test_parameter_capabilities_mask_and_mutate(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    context = {"execution_context": {"parameter_store": store}, "node_id": "node-test"}

    manage = RuntimeManageParameterStoreCapability()
    create = manage.execute(
        {
            "operation": "create",
            "key": "api_token",
            "value_json": {"token": "abc"},
            "tags": ["credential"],
            "sensitive": True,
        },
        context,
    )
    assert create.status == "success"
    assert "abc" not in str(create.data_preview)

    inspect = RuntimeInspectParameterStoreCapability()
    listed = inspect.execute({"query": "token"}, context)
    assert listed.status == "success"
    assert listed.data_preview["count"] == 1
    assert "abc" not in str(listed.data_preview)

    updated = manage.execute(
        {"operation": "update", "key": "api_token", "description": "API token"},
        context,
    )
    assert updated.status == "success"
    assert updated.data_preview["parameter"]["description"] == "API token"

    deleted = manage.execute({"operation": "delete", "key": "api_token"}, context)
    assert deleted.status == "success"
    assert deleted.data_preview["deleted"] is True


def test_runtime_attaches_parameters_as_masked_context_and_raw_execution_env(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="dicom_db",
            value_json={"host": "10.4.4.4", "password": "runtime-secret"},
            context_json={
                "version": 1,
                "user_provided_domain_context": {
                    "summary": "Runtime DICOM profile context.",
                    "prompt_guidance": "",
                    "clarification_guidance": "",
                    "concepts": [],
                    "metrics": [],
                    "relationships": [],
                },
            },
            aliases=["DICOM DB"],
            tags=["dicom"],
            sensitive=True,
        )
    )
    registry = build_default_registry()
    engine = ExecutionEngine(
        registry,
        {"workspace_root": str(tmp_path), "allow_shell_execution": False},
        InMemoryResultStore(),
    )
    runtime = AgentRuntime(
        llm_client=object(),
        registry=registry,
        execution_engine=engine,
        output_orchestrator=OutputPipelineOrchestrator(),
        parameter_store=store,
    )
    request = runtime._build_user_request("run a query against the DICOM DB", {})
    runtime._attach_agent_parameters(request, {})

    session_text = str(request.session_context)
    assert "runtime-secret" not in session_text
    assert request.session_context["agent_parameter_shell_env_names"] == [
        "OF_PARAM_DICOM_DB_HOST",
        "OF_PARAM_DICOM_DB_PASSWORD",
    ]
    assert request.session_context[PARAMETER_AGENT_CONTEXT_KEY]["dicom_db"]["summary"] == (
        "Runtime DICOM profile context."
    )
    execution_context = runtime._execution_context_with_agent_parameters(request, request.session_context)
    assert execution_context["shell_env"]["OF_PARAM_DICOM_DB_PASSWORD"] == "runtime-secret"
    assert all("CONTEXT" not in key for key in execution_context["shell_env"])


def test_parameter_clarification_choices_are_masked_and_grouped(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="sshgit_key",
            value_json={"password": "raw-password"},
            description="Git SSH password",
            aliases=["git ssh key"],
            tags=["git", "ssh"],
            sensitive=True,
        )
    )
    store.create(
        AgentParameterCreate(
            key="dicom_db",
            value_json={"host": "10.4.4.4"},
            description="DICOM database",
            sensitive=False,
        )
    )

    choices = parameter_clarification_choices(store, "git push needs sshgit_key password")

    assert choices[0]["key"] == "sshgit_key"
    assert choices[0]["exact"] is True
    assert choices[0]["group"] == "relevant"
    assert choices[0]["field_paths"] == ["password"]
    assert "raw-password" not in str(choices)
    assert any(choice["key"] == "dicom_db" and choice["group"] == "all" for choice in choices)


def test_runtime_exact_credential_parameter_adds_private_typein_macro(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="sshgit_key",
            value_json={"password": "raw-password"},
            description="Git SSH password",
            sensitive=True,
        )
    )
    registry = build_default_registry()
    engine = ExecutionEngine(
        registry,
        {"workspace_root": str(tmp_path), "allow_shell_execution": False},
        InMemoryResultStore(),
    )
    runtime = AgentRuntime(
        llm_client=object(),
        registry=registry,
        execution_engine=engine,
        output_orchestrator=OutputPipelineOrchestrator(),
        parameter_store=store,
    )

    request = runtime._build_user_request("git push using sshgit_key", {})
    runtime._attach_agent_parameters(request, {})

    macros = request.safety_context[USER_MACRO_PRIVATE_CONTEXT_KEY]
    assert macros[0]["value"] == "raw-password"
    assert macros[0]["source"] == "parameter_store"
    assert macros[0]["parameter_key"] == "sshgit_key"
    assert "raw-password" not in str(request.session_context)


@pytest.mark.parametrize(
    "prompt",
    [
        "mount the usb device; use sudo with local_sudo_pass",
        "mount the usb device; use sudo password parameter local_sudo_pass",
    ],
)
def test_runtime_sudo_parameter_phrase_adds_private_typein_macro(
    tmp_path,
    prompt: str,
) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="local_sudo_pass",
            value_json={"password": "raw-sudo-password"},
            description="Local sudo password",
            sensitive=True,
        )
    )
    registry = build_default_registry()
    engine = ExecutionEngine(
        registry,
        {"workspace_root": str(tmp_path), "allow_shell_execution": False},
        InMemoryResultStore(),
    )
    runtime = AgentRuntime(
        llm_client=object(),
        registry=registry,
        execution_engine=engine,
        output_orchestrator=OutputPipelineOrchestrator(),
        parameter_store=store,
    )

    request = runtime._build_user_request(prompt, {})
    runtime._attach_agent_parameters(request, {})

    macros = request.safety_context[USER_MACRO_PRIVATE_CONTEXT_KEY]
    assert macros[0]["kind"] == "typein"
    assert macros[0]["input_name"] == "typein_1"
    assert macros[0]["value"] == "raw-sudo-password"
    assert macros[0]["source"] == "parameter_store"
    assert macros[0]["parameter_key"] == "local_sudo_pass"
    planner_prompt = build_operator_plan_prompt(request)
    assert "raw-sudo-password" not in planner_prompt
    assert "raw-sudo-password" not in str(request.session_context)
    assert "typein_1" in planner_prompt
    assert "author sudo work as shell_command" in planner_prompt


@pytest.mark.parametrize(
    "prompt",
    [
        "mount the usb device; sudo password parameter local_sudo_pass",
        "what is sudo password parameter local_sudo_pass?",
        "what happens when sudo with local_sudo_pass fails?",
    ],
)
def test_runtime_sudo_parameter_mentions_without_use_do_not_add_typein_macro(
    tmp_path,
    prompt: str,
) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="local_sudo_pass",
            value_json={"password": "raw-sudo-password"},
            description="Local sudo password",
            sensitive=True,
        )
    )
    registry = build_default_registry()
    engine = ExecutionEngine(
        registry,
        {"workspace_root": str(tmp_path), "allow_shell_execution": False},
        InMemoryResultStore(),
    )
    runtime = AgentRuntime(
        llm_client=object(),
        registry=registry,
        execution_engine=engine,
        output_orchestrator=OutputPipelineOrchestrator(),
        parameter_store=store,
    )

    request = runtime._build_user_request(prompt, {})
    runtime._attach_agent_parameters(request, {})

    assert USER_MACRO_PRIVATE_CONTEXT_KEY not in request.safety_context
    assert "raw-sudo-password" not in str(request.session_context)


def test_parameter_store_api_routes_mask_and_reveal(tmp_path) -> None:
    runtime = _Runtime()
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_parameters_db_path=tmp_path / "parameters.db",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_tasks_db_path=tmp_path / "tasks.db",
                agent_monitors_db_path=tmp_path / "monitors.db",
                agent_gateways_db_path=tmp_path / "gateways.db",
                agent_ui_settings_db_path=tmp_path / "ui_settings.db",
                agent_events_poll_seconds=60,
            ),
            agent_runtime=runtime,
        )
    )

    draft = client.post(
        "/api/agent/parameters/draft",
        json={"prompt": "store this DICOM DB connection info 10.4.4.4:9045/dicom"},
    )
    assert draft.status_code == 200
    assert draft.json()["drafts"][0]["key"] == "dicom_db"

    created = client.post(
        "/api/agent/parameters",
        json={
            "key": "dicom_db",
            "value_json": {"host": "10.4.4.4", "password": "secret"},
            "context_json": {
                "version": 1,
                "user_provided_domain_context": {
                    "summary": "API-created DICOM context.",
                    "prompt_guidance": "",
                    "clarification_guidance": "",
                    "concepts": [],
                    "metrics": [],
                    "relationships": [],
                },
            },
            "description": "DICOM DB",
            "aliases": ["DICOM DB"],
            "tags": ["dicom"],
            "sensitive": True,
        },
    )
    assert created.status_code == 200
    assert "secret" not in str(created.json())

    submitted = client.post(
        "/api/agent/request",
        json={"prompt": "connect to DICOM DB and typein using password stored in dicom_db"},
    )
    assert submitted.status_code == 200
    with client.stream("GET", submitted.json()["stream_url"]) as response:
        _ = response.read()
    assert runtime.last_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "secret"
    assert runtime.last_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["source"] == "parameter_store"
    assert "secret" not in submitted.text

    submitted_natural = client.post(
        "/api/agent/request",
        json={"prompt": "connect to DICOM DB and use DICOM DB password from store"},
    )
    assert submitted_natural.status_code == 200
    with client.stream("GET", submitted_natural.json()["stream_url"]) as response:
        _ = response.read()
    assert runtime.last_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["value"] == "secret"
    assert runtime.last_context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]["parameter_field"] == "password"

    listed = client.get("/api/agent/parameters?q=dicom")
    assert listed.status_code == 200
    assert listed.json()["stats"]["total"] == 1
    assert listed.json()["entries"][0]["context_json"]["summary"] == "API-created DICOM context."
    assert "secret" not in str(listed.json())

    detail = client.get("/api/agent/parameters/dicom_db")
    assert detail.status_code == 200
    assert detail.json()["context_json"]["user_provided_domain_context"]["summary"] == (
        "API-created DICOM context."
    )

    revealed = client.post("/api/agent/parameters/dicom_db/reveal")
    assert revealed.status_code == 200
    assert revealed.json()["value_json"]["password"] == "secret"
    assert revealed.json()["context_json"]["user_provided_domain_context"]["summary"] == (
        "API-created DICOM context."
    )

    patched = client.patch("/api/agent/parameters/DICOM DB", json={"description": "updated"})
    assert patched.status_code == 200
    assert patched.json()["entry"]["description"] == "updated"

    deleted = client.delete("/api/agent/parameters/dicom_db")
    assert deleted.status_code == 200
    assert client.get("/api/agent/parameters/dicom_db").status_code == 404


def test_parameter_store_api_list_limit_clamps_and_masks(tmp_path) -> None:
    client = TestClient(
        create_app(
            Settings(
                openai_compat_model_name="OpenFABRIC Echo",
                agent_parameters_db_path=tmp_path / "parameters.db",
                agent_chats_db_path=tmp_path / "chats.db",
                agent_events_db_path=tmp_path / "events.db",
                agent_tasks_db_path=tmp_path / "tasks.db",
                agent_monitors_db_path=tmp_path / "monitors.db",
                agent_gateways_db_path=tmp_path / "gateways.db",
                agent_ui_settings_db_path=tmp_path / "ui_settings.db",
                agent_events_poll_seconds=60,
            ),
            agent_runtime=_Runtime(),
        )
    )
    for index in range(3):
        created = client.post(
            "/api/agent/parameters",
            json={
                "key": f"param_{index}",
                "value_json": {"password": f"secret-{index}"},
                "description": f"Parameter {index}",
                "sensitive": True,
            },
        )
        assert created.status_code == 200

    default_listed = client.get("/api/agent/parameters")
    assert default_listed.status_code == 200
    assert len(default_listed.json()["entries"]) == 3
    assert "secret-" not in str(default_listed.json())

    limited = client.get("/api/agent/parameters?limit=1")
    assert limited.status_code == 200
    assert len(limited.json()["entries"]) == 1
    assert limited.json()["stats"]["total"] == 3
    assert limited.json()["stats"]["filtered"] == 1
    assert "secret-" not in str(limited.json())

    clamped_low = client.get("/api/agent/parameters?limit=0")
    assert clamped_low.status_code == 200
    assert len(clamped_low.json()["entries"]) == 1

    clamped_high = client.get("/api/agent/parameters?limit=1000")
    assert clamped_high.status_code == 200
    assert len(clamped_high.json()["entries"]) == 3
    assert "secret-" not in str(clamped_high.json())


def test_parameter_store_static_assets_are_wired() -> None:
    static_root = Path("src/agent_runtime/api/static/agent_ui")
    desktop_html = (static_root / "index.html").read_text(encoding="utf-8")
    desktop_js = (static_root / "app.js").read_text(encoding="utf-8")
    mobile_html = (static_root / "mobile.html").read_text(encoding="utf-8")
    mobile_js = (static_root / "mobile.js").read_text(encoding="utf-8")

    assert 'id="parameters-toggle"' in desktop_html
    assert 'id="parameters-drawer"' in desktop_html
    assert "maybeDraftParameterStore" in desktop_js
    assert "looksLikeParameterStoreSetPrompt" in desktop_js
    assert "promptParameterShortcutTriggerAtCursor" in desktop_js
    assert 'fetch("/api/agent/parameters?limit=1000"' in desktop_js
    assert "/parameter-editor" in desktop_js
    assert 'id="parameter-open-editor-button"' in desktop_html
    assert 'name.textContent = macro ? `/${macro.label}` : `:p ${entry?.key || entry?.normalized_key || "parameter"}`;' in desktop_js
    assert "input.value = value.slice(0, trigger.start) + key + value.slice(trigger.end)" in mobile_js
    assert "/api/agent/parameters/draft" in desktop_js
    assert ".prompt-macro-option[data-shortcut-kind=\"parameter\"]" in (static_root / "app.css").read_text(encoding="utf-8")
    assert 'id="mobile-parameters-button"' in mobile_html
    assert 'id="mobile-parameter-sheet"' in mobile_html
    assert 'id="mobile-parameter-shortcut-menu"' in mobile_html
    assert "maybeDraftParameterFromChat" in mobile_js
    assert "looksLikeParameterSetPrompt" in mobile_js
    assert "mobileParameterShortcutTriggerAtCursor" in mobile_js
    assert "loadMobilePromptParameterShortcuts" in mobile_js
