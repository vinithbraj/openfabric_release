from agent_runtime.core.orchestrator import _safe_failure_response, _validation_errors_for_user


def test_safe_failure_response_formats_raw_system_error_as_markdown_code_block():
    message = (
        "1 validation error for TaskVerbAssignmentResult Value error, Task verb assignments "
        "must reference unique task ids. [type=value_error, input_value={'assignments': []}, "
        "input_type=dict] For further information visit https://errors.pydantic.dev/2.13/v/value_error"
    )

    rendered = _safe_failure_response("runtime", message)

    assert rendered.startswith("## Runtime validation failed")
    assert "**Stage:** `runtime`" in rendered
    assert "**How to fix:**" in rendered
    assert "TaskVerbAssignmentResult" in rendered
    assert "https://errors.pydantic.dev/2.13/v/value_error" in rendered
    assert "I hit an internal error" not in rendered


def test_validation_errors_for_user_formats_raw_detail_as_markdown_code_block():
    rendered = _validation_errors_for_user(
        [
            {
                "error": "schema_validation_error",
                "message": "Traceback (most recent call last):\n  File \"tool.py\", line 1\nValueError: bad output",
            }
        ]
    )

    assert "Validation details:" in rendered
    assert "- [schema_validation_error]" in rendered
    assert "**Raw Validation Detail**" in rendered
    assert "```text\nTraceback (most recent call last):" in rendered
    assert "ValueError: bad output\n```" in rendered
