from __future__ import annotations

from agent_runtime.operator.literal_payloads import (
    OPERATOR_LITERAL_PAYLOADS_CONTEXT_KEY,
    extract_literal_payloads,
    literal_payload_prompt_lines,
)


def test_extract_literal_payloads_shields_multiline_description_generically() -> None:
    prompt = (
        'stage all changes and commit using description "Add explicit `/checkonline` support\n\n'
        "- Add onlinelinelookup module\n"
        '- Register `/checkonline` macro" and push'
    )

    parsed = extract_literal_payloads(prompt)

    assert parsed.sanitized_prompt == (
        "stage all changes and commit using description [provided description_payload payload] and push"
    )
    assert len(parsed.payloads) == 1
    payload = parsed.payloads[0]
    assert payload["kind"] == "description_payload"
    assert payload["input_name"] == "description_payload"
    assert "Add explicit `/checkonline` support" in payload["value"]
    assert "- Register `/checkonline` macro" in payload["value"]
    assert "onlinelinelookup" not in parsed.sanitized_prompt


def test_extract_literal_payloads_accepts_spoken_begin_end_description() -> None:
    parsed = extract_literal_payloads(
        "stage all changes and commit using description begin Add explicit slash macro support end and push"
    )

    assert parsed.sanitized_prompt == (
        "stage all changes and commit using description [provided description_payload payload] and push"
    )
    assert parsed.payloads[0]["kind"] == "description_payload"
    assert parsed.payloads[0]["value"] == "Add explicit slash macro support"


def test_literal_payload_prompt_lines_include_full_message() -> None:
    parsed = extract_literal_payloads('git commit with message "subject\n\nbody"')

    lines = literal_payload_prompt_lines(
        {OPERATOR_LITERAL_PAYLOADS_CONTEXT_KEY: parsed.payloads}
    )
    joined = "\n".join(lines)

    assert "Protected literal payloads" in joined
    assert "message_payload" in joined
    assert "subject\n\nbody" in joined


def test_literal_payload_with_typein_text_is_not_a_macro_source() -> None:
    parsed = extract_literal_payloads(r'commit using description "literal typein \"abc\" text"')

    assert parsed.sanitized_prompt == (
        "commit using description [provided description_payload payload]"
    )
    assert parsed.payloads[0]["value"] == 'literal typein "abc" text'
