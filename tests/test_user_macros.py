from __future__ import annotations

import pytest

from agent_runtime.operator.user_macros import (
    USER_MACRO_PRIVATE_CONTEXT_KEY,
    UserMacroParseError,
    consume_typein_macro,
    parse_user_macros,
    prompt_requests_parameter_typein_terminal,
    prompt_macro_registry,
)
from agent_runtime.parameters import AgentParameterCreate, AgentParameterStore


def test_typein_macro_parses_and_sanitizes_value() -> None:
    parsed = parse_user_macros('run command and typein "abc"')

    assert parsed.sanitized_prompt == 'run command and typein "[provided]"'
    assert parsed.private_macros[0]["value"] == "abc"
    assert parsed.private_macros[0]["input_name"] == "typein_1"
    assert parsed.public_summaries[0]["preview"] == "abc"


def test_typein_sanitized_placeholder_is_not_reparsed_as_macro() -> None:
    parsed = parse_user_macros('run command and typein "[redacted]"')

    assert parsed.sanitized_prompt == 'run command and typein "[redacted]"'
    assert parsed.private_macros == []
    assert parsed.public_summaries == []


def test_prompt_macro_registry_exposes_typein_template() -> None:
    registry = prompt_macro_registry()

    by_id = {item["id"]: item for item in registry}
    assert by_id["typein"] == {
        "id": "typein",
        "label": "typein",
        "description": "Send deterministic input to terminal or stdin prompts",
        "template": '/typein "<your text>"',
        "placeholder": "<your text>",
        "aliases": [
            "typein",
            "terminal",
            "stdin",
            "input",
            "type in",
            "tpe in",
            "tpein",
            "type in begin end",
        ],
    }
    assert by_id["checkonline"]["template"] == "/checkonline"
    assert by_id["checkonline"]["placeholder"] == ""
    assert by_id["checkonlineai"]["template"] == '/checkonlineai "<your search query>"'
    assert by_id["checkonlineai"]["placeholder"] == "<your search query>"
    assert by_id["autoapprove"]["template"] == "/autoapprove"
    assert by_id["autoapprove"]["placeholder"] == ""
    assert by_id["addtomemory"]["template"] == "/addtomemory"
    assert by_id["addtomemory"]["placeholder"] == "<memory text>"
    assert by_id["runlater"]["template"] == "/runlater"
    assert by_id["runlater"]["placeholder"] == ""
    assert by_id["remind"]["template"] == "/remind"
    assert by_id["remind"]["placeholder"] == ""
    assert by_id["todo"]["template"] == "/todo"
    assert by_id["todo"]["placeholder"] == ""
    assert "to do" in by_id["todo"]["aliases"]
    assert by_id["restart"]["template"] == "/restart"
    assert by_id["restart"]["placeholder"] == ""
    assert "restart server and gateway" in by_id["restart"]["aliases"]


def test_addtomemory_slash_shortcut_is_known_to_macro_parser() -> None:
    parsed = parse_user_macros("/addtomemory remember DICOM joins")

    assert parsed.sanitized_prompt == "/addtomemory remember DICOM joins"
    assert parsed.public_summaries == []


def test_typein_macro_is_case_insensitive() -> None:
    parsed = parse_user_macros('TYPEIN "abc"')

    assert parsed.private_macros[0]["kind"] == "typein"


def test_spoken_type_in_begin_end_parses_and_sanitizes_value() -> None:
    parsed = parse_user_macros("run command and type in begin hello world end")

    assert parsed.sanitized_prompt == 'run command and typein "[provided]"'
    assert parsed.private_macros[0]["kind"] == "typein"
    assert parsed.private_macros[0]["value"] == "hello world"


def test_typein_typo_alias_parses_and_sanitizes_value() -> None:
    parsed = parse_user_macros('run command and tpe in "abc"')

    assert parsed.sanitized_prompt == 'run command and tpe in "[provided]"'
    assert parsed.private_macros[0]["kind"] == "typein"
    assert parsed.private_macros[0]["value"] == "abc"


def test_spoken_typein_typo_begin_end_parses_and_sanitizes_value() -> None:
    parsed = parse_user_macros("run command and tpe in begin hello world end")

    assert parsed.sanitized_prompt == 'run command and typein "[provided]"'
    assert parsed.private_macros[0]["kind"] == "typein"
    assert parsed.private_macros[0]["value"] == "hello world"


def test_slash_typein_macro_parses_without_unknown_slash_candidate() -> None:
    parsed = parse_user_macros('/typein "abc"')

    assert parsed.sanitized_prompt == '/typein "[provided]"'
    assert parsed.private_macros[0]["kind"] == "typein"
    assert {item["kind"] for item in parsed.public_summaries} == {"typein"}


def test_slash_typein_spaced_alias_has_no_unknown_candidate() -> None:
    parsed = parse_user_macros('/type in "abc"')

    assert parsed.sanitized_prompt == '/type in "[provided]"'
    assert parsed.private_macros[0]["value"] == "abc"
    assert {item["kind"] for item in parsed.public_summaries} == {"typein"}


def test_typein_macro_supports_json_string_escapes() -> None:
    parsed = parse_user_macros(r'typein "a \"quote\" and \n newline"')

    assert parsed.private_macros[0]["value"] == 'a "quote" and \n newline'


def test_typein_macro_redacts_sensitive_context() -> None:
    parsed = parse_user_macros('use sudo and typein "abc"')

    assert parsed.sanitized_prompt == 'use sudo and typein "[redacted]"'
    assert parsed.private_macros[0]["value"] == "abc"
    assert parsed.public_summaries[0]["redacted"] is True
    assert "preview" not in parsed.public_summaries[0]


def test_typein_macro_can_use_parameter_store_field(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="ssh_key",
            value_json={"username": "git", "password": "raw-password"},
            description="Git SSH credential",
            sensitive=True,
        )
    )

    parsed = parse_user_macros(
        "git push, typein using password stored in ssh_key for the password prompt",
        parameter_store=store,
    )

    assert parsed.sanitized_prompt == 'git push, typein "[redacted]" for the password prompt'
    assert parsed.private_macros[0]["value"] == "raw-password"
    assert parsed.private_macros[0]["source"] == "parameter_store"
    assert parsed.private_macros[0]["parameter_key"] == "ssh_key"
    assert parsed.private_macros[0]["parameter_field"] == "password"
    assert parsed.public_summaries[0]["redacted"] is True
    assert "preview" not in parsed.public_summaries[0]
    assert store.get("ssh_key").use_count == 1


def test_typein_spaced_alias_can_use_parameter_store_query(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="ssh_key",
            value_json={"username": "git", "password": "raw-password"},
            description="Git SSH credential",
            sensitive=True,
        )
    )

    parsed = parse_user_macros(
        "git push, type in ssh password from store for the password prompt",
        parameter_store=store,
    )

    assert parsed.sanitized_prompt == 'git push, type in "[redacted]" for the password prompt'
    assert parsed.private_macros[0]["value"] == "raw-password"
    assert parsed.private_macros[0]["source"] == "parameter_store"
    assert parsed.private_macros[0]["parameter_key"] == "ssh_key"
    assert parsed.private_macros[0]["parameter_field"] == "password"
    assert store.get("ssh_key").use_count == 1


def test_use_secret_from_store_creates_parameter_typein(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="ssh_key",
            value_json={"username": "git", "password": "raw-password"},
            description="Git SSH credential",
            sensitive=True,
        )
    )

    parsed = parse_user_macros(
        "git push, use ssh password from store for the password prompt",
        parameter_store=store,
    )

    assert parsed.sanitized_prompt == 'git push, typein "[redacted]" for the password prompt'
    assert parsed.private_macros[0]["value"] == "raw-password"
    assert parsed.private_macros[0]["parameter_key"] == "ssh_key"
    assert parsed.private_macros[0]["parameter_field"] == "password"
    assert parsed.public_summaries[0]["source"] == "parameter_store"


def test_use_non_secret_from_store_does_not_create_typein_macro(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(AgentParameterCreate(key="dicom_db", value_json={"host": "10.4.4.4"}))

    parsed = parse_user_macros("use dicom db from store to run a query", parameter_store=store)

    assert parsed.private_macros == []
    assert parsed.public_summaries == []
    assert parsed.sanitized_prompt == "use dicom db from store to run a query"


def test_typein_parameter_store_requires_matching_field(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(AgentParameterCreate(key="ssh_key", value_json={"token": "abc"}))

    with pytest.raises(UserMacroParseError, match='Parameter field "password" was not found'):
        parse_user_macros(
            "typein using password stored in ssh_key",
            parameter_store=store,
        )


def test_typein_macro_can_be_reused_for_terminal_prompt_delivery() -> None:
    context = {
        USER_MACRO_PRIVATE_CONTEXT_KEY: [
            {
                "macro_id": "macro_typein_1",
                "kind": "typein",
                "input_name": "typein_1",
                "value": "abc",
                "consumed": False,
            }
        ]
    }

    first = consume_typein_macro(
        context,
        delivery="terminal_prompt",
        action_id="action_1",
        reusable=True,
    )
    second = consume_typein_macro(
        context,
        delivery="terminal_prompt",
        action_id="action_2",
        reusable=True,
    )
    stdin = consume_typein_macro(
        context,
        input_name="typein_1",
        delivery="shell_stdin",
        action_id="action_3",
    )

    assert first is not None
    assert second is not None
    assert stdin is None
    macro = context[USER_MACRO_PRIVATE_CONTEXT_KEY][0]
    assert macro["consumed"] is True
    assert macro["delivery_count"] == 2
    assert [item["action_id"] for item in macro["deliveries"]] == ["action_1", "action_2"]


def test_sudo_parameter_phrase_creates_typein_macro(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="local_sudo_pass",
            value_json={"password": "sudo-secret"},
            sensitive=True,
        )
    )

    parsed = parse_user_macros(
        "mount /dev/sdc2; use sudo with local_sudo_pass",
        parameter_store=store,
    )

    assert parsed.sanitized_prompt == "mount /dev/sdc2; use sudo with local_sudo_pass"
    assert parsed.private_macros[0]["kind"] == "typein"
    assert parsed.private_macros[0]["value"] == "sudo-secret"
    assert parsed.private_macros[0]["parameter_key"] == "local_sudo_pass"
    assert "sudo-secret" not in str(parsed.public_summaries)


@pytest.mark.parametrize(
    "prompt",
    [
        "sudo password parameter local_sudo_pass",
        "what is sudo password parameter local_sudo_pass?",
        "what happens when sudo with local_sudo_pass fails?",
    ],
)
def test_sudo_parameter_mentions_without_use_do_not_create_typein_macro(
    tmp_path,
    prompt: str,
) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="local_sudo_pass",
            value_json={"password": "sudo-secret"},
            sensitive=True,
        )
    )

    parsed = parse_user_macros(prompt, parameter_store=store)

    assert parsed.private_macros == []
    assert prompt_requests_parameter_typein_terminal(prompt, store) is False


def test_parameter_prompt_terminal_hint_matches_exact_credential_use(tmp_path) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="sshgit_key",
            value_json={"password": "ssh-secret"},
            description="Git SSH password",
            sensitive=True,
        )
    )

    assert prompt_requests_parameter_typein_terminal("git push using sshgit_key", store) is True
    assert prompt_requests_parameter_typein_terminal("what is the password for sshgit_key?", store) is False
    assert prompt_requests_parameter_typein_terminal("summarize sshgit_key", store) is False


def test_typein_macro_rejects_missing_closing_quote() -> None:
    with pytest.raises(UserMacroParseError, match="missing closing quote"):
        parse_user_macros('typein "abc')


def test_typein_macro_rejects_multiple_macros() -> None:
    with pytest.raises(UserMacroParseError, match="Only one typein macro"):
        parse_user_macros('typein "a" then typein "b"')


def test_checkonline_macro_sanitizes_prompt() -> None:
    parsed = parse_user_macros("/checkonline how to use nvidia-smi")

    assert parsed.sanitized_prompt == "how to use nvidia-smi"
    assert parsed.private_macros == []
    assert parsed.public_summaries[0]["kind"] == "checkonline"


def test_autoapprove_macro_sanitizes_leading_intent() -> None:
    parsed = parse_user_macros("/autoapprove write the file")

    assert parsed.sanitized_prompt == "write the file"
    assert parsed.private_macros == []
    assert parsed.public_summaries[0]["kind"] == "autoapprove"
    assert parsed.public_summaries[0]["delivery_scope"] == "request_confirmation_policy"


def test_autoapprove_macro_sanitizes_trailing_intent() -> None:
    parsed = parse_user_macros('write the file with message "ship it" /autoapprove')

    assert parsed.sanitized_prompt == 'write the file with message "ship it"'
    assert parsed.private_macros == []
    assert parsed.public_summaries[0]["kind"] == "autoapprove"
    assert parsed.public_summaries[0]["delivery_scope"] == "request_confirmation_policy"


def test_restart_macro_expands_to_server_and_gateway_restart_prompt() -> None:
    parsed = parse_user_macros("/restart")

    assert parsed.sanitized_prompt == "restart the Agent UI server and selected gateway"
    assert parsed.private_macros == []
    assert parsed.public_summaries == [
        {
            "macro_id": "macro_restart_1",
            "kind": "restart",
            "input_name": "",
            "redacted": False,
            "value_length": 0,
            "delivery_scope": "agent_ui_restart",
            "consumed": False,
            "restart_targets": ["server", "gateway"],
        }
    ]


def test_restart_slash_shortcut_is_known_to_macro_parser() -> None:
    parsed = parse_user_macros("/restart please")

    assert parsed.sanitized_prompt == "restart the Agent UI server and selected gateway. please"
    assert [item["kind"] for item in parsed.public_summaries] == ["restart"]


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("auto approve write the file", "write the file"),
        ("auto approved write the file", "write the file"),
        ("auto-approved write the file", "write the file"),
        ("auto approval write the file", "write the file"),
        ("auto confirm write the file", "write the file"),
        ("slash auto approve write the file", "write the file"),
        ("slash auto-approved write the file", "write the file"),
        ("write the file auto approve", "write the file"),
        ("write the file auto approved", "write the file"),
        ("write the file Auto-Approved.", "write the file"),
        ("write the file auto approval.", "write the file"),
        ("write the file /autoapprove.", "write the file"),
        ("write the file slash auto approve.", "write the file"),
        ("write the file approve automatically", "write the file"),
        ("write the file automatically approve", "write the file"),
    ],
)
def test_autoapprove_voice_aliases_are_request_scoped(prompt: str, expected: str) -> None:
    parsed = parse_user_macros(prompt)

    assert parsed.sanitized_prompt == expected
    assert parsed.private_macros == []
    assert [item["kind"] for item in parsed.public_summaries] == ["autoapprove"]


def test_autoapprove_git_commit_prompt_is_not_search_macro() -> None:
    parsed = parse_user_macros(
        'git stage all changes and commit with msg '
        '"add more context to the event ui to resolve any ambiguity" /autoapprove'
    )

    assert parsed.sanitized_prompt == (
        'git stage all changes and commit with msg '
        '"add more context to the event ui to resolve any ambiguity"'
    )
    assert parsed.private_macros == []
    assert [item["kind"] for item in parsed.public_summaries] == ["autoapprove"]


def test_event_extraction_macros_sanitize_prompt_and_emit_hints() -> None:
    runlater = parse_user_macros('/runlater after 25 mins git commit -m "x"')
    remind = parse_user_macros('/remind after 25 mins git commit -m "x"')
    todo = parse_user_macros('/todo after 25 mins git commit -m "x"')

    assert runlater.sanitized_prompt == 'after 25 mins git commit -m "x"'
    assert runlater.public_summaries[0]["kind"] == "runlater"
    assert runlater.public_summaries[0]["event_action_type"] == "agent_prompt"
    assert remind.sanitized_prompt == 'after 25 mins git commit -m "x"'
    assert remind.public_summaries[0]["kind"] == "remind"
    assert remind.public_summaries[0]["event_action_type"] == "notification"
    assert todo.sanitized_prompt == 'after 25 mins git commit -m "x"'
    assert todo.public_summaries[0]["kind"] == "todo"
    assert todo.public_summaries[0]["event_action_type"] == "notification"


@pytest.mark.parametrize(
    ("prompt", "expected", "kind", "action_type"),
    [
        ("run later after 25 mins git commit -m \"x\"", 'after 25 mins git commit -m "x"', "runlater", "agent_prompt"),
        ("after 25 mins git commit -m \"x\" scheduled action", 'after 25 mins git commit -m "x"', "runlater", "agent_prompt"),
        ("reminder only after 25 mins git commit -m \"x\"", 'after 25 mins git commit -m "x"', "remind", "notification"),
        ("after 25 mins git commit -m \"x\" notification only", 'after 25 mins git commit -m "x"', "remind", "notification"),
        ("save as reminder after 25 mins git commit -m \"x\"", 'after 25 mins git commit -m "x"', "remind", "notification"),
        ("todo after 25 mins git commit -m \"x\"", 'after 25 mins git commit -m "x"', "todo", "notification"),
        ("to do after 25 mins git commit -m \"x\"", 'after 25 mins git commit -m "x"', "todo", "notification"),
        ("add todo after 25 mins git commit -m \"x\"", 'after 25 mins git commit -m "x"', "todo", "notification"),
        ("save as todo after 25 mins git commit -m \"x\"", 'after 25 mins git commit -m "x"', "todo", "notification"),
    ],
)
def test_event_extraction_voice_aliases_emit_hints(
    prompt: str,
    expected: str,
    kind: str,
    action_type: str,
) -> None:
    parsed = parse_user_macros(prompt)

    assert parsed.sanitized_prompt == expected
    assert parsed.public_summaries[0]["kind"] == kind
    assert parsed.public_summaries[0]["event_action_type"] == action_type


def test_todo_macro_does_not_consume_trailing_plain_noun() -> None:
    parsed = parse_user_macros("show my todo")

    assert parsed.sanitized_prompt == "show my todo"
    assert parsed.public_summaries == []


def test_autoapprove_middle_text_is_literal_text() -> None:
    parsed = parse_user_macros("commit message mentions /autoapprove literally")

    assert parsed.sanitized_prompt == "commit message mentions /autoapprove literally"
    assert parsed.public_summaries == []


def test_standalone_unknown_slash_token_is_macro_candidate() -> None:
    parsed = parse_user_macros("run the task /dryrun")

    assert parsed.sanitized_prompt == "run the task /dryrun"
    assert parsed.public_summaries[0]["kind"] == "slash_macro_candidate"
    assert parsed.public_summaries[0]["token"] == "/dryrun"
    assert parsed.public_summaries[0]["delivery_scope"] == "slash_macro_candidate"


def test_slash_macro_candidate_does_not_match_paths_urls_or_quoted_text() -> None:
    parsed = parse_user_macros(
        'inspect /home/vinith/project and https://example.test/path then say "/dryrun"'
    )

    assert parsed.sanitized_prompt == (
        'inspect /home/vinith/project and https://example.test/path then say "/dryrun"'
    )
    assert parsed.public_summaries == []


def test_checkonlineai_macro_sanitizes_prompt() -> None:
    parsed = parse_user_macros("/checkonlineai how to use nvidia-smi")

    assert parsed.sanitized_prompt == "how to use nvidia-smi"
    assert parsed.private_macros == []
    assert parsed.public_summaries[0]["kind"] == "checkonlineai"


def test_checkonlineai_macro_uses_quoted_query_payload() -> None:
    parsed = parse_user_macros(
        '/checkonlineai "what are postgres docker image names" and install them'
    )

    assert parsed.sanitized_prompt == "and install them"
    assert parsed.private_macros == []
    assert parsed.public_summaries[0]["kind"] == "checkonlineai"
    assert parsed.public_summaries[0]["query"] == "what are postgres docker image names"
    assert parsed.public_summaries[0]["preview"] == "what are postgres docker image names"
    assert parsed.public_summaries[0]["value_length"] == len(
        "what are postgres docker image names"
    )


def test_spoken_checkonlineai_begin_end_uses_query_payload() -> None:
    parsed = parse_user_macros(
        "check online ai begin postgres docker image names end and install them"
    )

    assert parsed.sanitized_prompt == "and install them"
    assert parsed.public_summaries[0]["kind"] == "checkonlineai"
    assert parsed.public_summaries[0]["query"] == "postgres docker image names"


def test_spoken_slash_checkonline_maps_to_compact_online_lookup() -> None:
    parsed = parse_user_macros("slash check online how to use nvidia-smi")

    assert parsed.sanitized_prompt == "how to use nvidia-smi"
    assert parsed.public_summaries[0]["kind"] == "checkonline"


def test_checkonline_macro_can_preserve_streaming_hint() -> None:
    parsed = parse_user_macros(
        "/checkonline how to use nvidia-smi",
        preserve_checkonline_hint=True,
    )

    assert parsed.sanitized_prompt == "check online how to use nvidia-smi"


def test_checkonline_phrase_is_treated_as_checkonlineai_request() -> None:
    parsed = parse_user_macros("check online how to use nvidia-smi")

    assert parsed.sanitized_prompt == "how to use nvidia-smi"
    assert parsed.public_summaries[0]["kind"] == "checkonlineai"
    assert parsed.public_summaries[0]["query"] == "check online how to use nvidia-smi"


def test_checkonline_phrase_is_case_insensitive_and_handles_checked_online() -> None:
    parsed = parse_user_macros("Checked Online how to use nvidia-smi")

    assert parsed.sanitized_prompt == "how to use nvidia-smi"
    assert parsed.public_summaries[0]["kind"] == "checkonlineai"
    assert parsed.public_summaries[0]["query"] == "Checked Online how to use nvidia-smi"


def test_checkonline_phrase_handles_checking_online_variation() -> None:
    parsed = parse_user_macros("checking online the latest documentation for python")

    assert parsed.sanitized_prompt == "the latest documentation for python"
    assert parsed.public_summaries[0]["kind"] == "checkonlineai"
    assert parsed.public_summaries[0]["query"] == "checking online the latest documentation for python"


@pytest.mark.parametrize("phrase", ["search online", "look online", "look up online"])
def test_online_lookup_voice_phrases_are_treated_as_checkonlineai(phrase: str) -> None:
    parsed = parse_user_macros(f"{phrase} the latest documentation for python")

    assert parsed.sanitized_prompt == "the latest documentation for python"
    assert parsed.public_summaries[0]["kind"] == "checkonlineai"


def test_checkonlineai_macro_can_preserve_streaming_hint() -> None:
    parsed = parse_user_macros(
        "/checkonlineai how to use nvidia-smi",
        preserve_checkonline_hint=True,
    )

    assert parsed.sanitized_prompt == "using online AI context how to use nvidia-smi"


def test_checkonlineai_quoted_query_preserves_streaming_hint_without_query_text() -> None:
    parsed = parse_user_macros(
        '/checkonlineai "what are postgres docker image names" and install them',
        preserve_checkonline_hint=True,
    )

    assert (
        parsed.sanitized_prompt
        == "using online AI context for what are postgres docker image names and install them"
    )
    assert parsed.public_summaries[0]["query"] == "what are postgres docker image names"


def test_checkonline_and_typein_do_not_leak_typein_value() -> None:
    parsed = parse_user_macros('/checkonline use sudo and typein "abc"')

    assert parsed.sanitized_prompt == 'use sudo and typein "[redacted]"'
    assert {item["kind"] for item in parsed.public_summaries} == {"checkonline", "typein"}
    assert "abc" not in parsed.sanitized_prompt


def test_checkonlineai_and_typein_do_not_leak_typein_value() -> None:
    parsed = parse_user_macros('/checkonlineai use sudo and typein "abc"')

    assert parsed.sanitized_prompt == 'use sudo and typein "[redacted]"'
    assert {item["kind"] for item in parsed.public_summaries} == {"checkonlineai", "typein"}
    assert "abc" not in parsed.sanitized_prompt


def test_checkonline_inside_backticks_is_literal_text() -> None:
    parsed = parse_user_macros("commit message mentions `/checkonline` literally")

    assert parsed.sanitized_prompt == "commit message mentions `/checkonline` literally"
    assert parsed.public_summaries == []


def test_checkonlineai_inside_backticks_is_literal_text() -> None:
    parsed = parse_user_macros("commit message mentions `/checkonlineai` literally")

    assert parsed.sanitized_prompt == "commit message mentions `/checkonlineai` literally"
    assert parsed.public_summaries == []


def test_checkonline_inside_quotes_is_literal_text() -> None:
    parsed = parse_user_macros('commit using description "/checkonline is documented"')

    assert parsed.sanitized_prompt == 'commit using description "/checkonline is documented"'
    assert parsed.public_summaries == []


def test_checkonlineai_inside_quotes_is_literal_text() -> None:
    parsed = parse_user_macros('commit using description "/checkonlineai is documented"')

    assert parsed.sanitized_prompt == 'commit using description "/checkonlineai is documented"'
    assert parsed.public_summaries == []


def test_typein_inside_quotes_is_literal_text() -> None:
    parsed = parse_user_macros(r'commit using description "literal typein \"abc\" text"')

    assert parsed.sanitized_prompt == r'commit using description "literal typein \"abc\" text"'
    assert parsed.private_macros == []
    assert parsed.public_summaries == []
