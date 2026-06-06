"""SQL agent response rendering."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.service_support.common import *

def render_sql_agent_response(payload: dict[str, Any]) -> str:
    """Render a SQL-agent payload for chat responses."""

    status = str(payload.get("status") or "")
    if status == "clarification_required":
        request = payload.get("clarification_request")
        if isinstance(request, dict):
            options = request.get("options") if isinstance(request.get("options"), list) else []
            suffix = ""
            if options:
                suffix = "\n\nOptions: " + ", ".join(
                    str(item.get("label") or "") for item in options if isinstance(item, dict)
                )
            question = str(request.get("question") or payload.get("summary") or "Input needed.")
            return question + suffix
        choices = payload.get("parameter_choices") or []
        suffix = ""
        if choices:
            suffix = "\n\nChoices: " + ", ".join(
                str(item.get("key") or "") for item in choices if isinstance(item, dict)
            )
        return str(payload.get("summary") or "Please choose a database parameter.") + suffix
    if status == "confirmation_required":
        generated_sql = str(payload.get("generated_sql") or payload.get("sql") or "").strip()
        executed_sql = str(payload.get("executed_sql") or payload.get("sql") or "").strip()
        lines = [
            "## Confirmation Required",
            "",
            str(payload.get("summary") or "SQL execution requires explicit confirmation."),
        ]
        if generated_sql:
            lines.extend(["", "Generated SQL:", f"```sql\n{generated_sql}\n```"])
        if executed_sql and executed_sql != generated_sql:
            lines.extend(["", "Executed SQL:", f"```sql\n{executed_sql}\n```"])
        return "\n".join(lines)
    if status != "success":
        return "SQL agent error: " + str(payload.get("error") or "Unknown error.")
    summary = str(payload.get("summary") or "SQL query completed.").strip()
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    columns = payload.get("columns") if isinstance(payload.get("columns"), list) else []
    schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else {}
    lines = [summary]
    if rows and columns:
        lines.append("")
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        render_limit = 100 if schema else 20
        for row in rows[:render_limit]:
            if not isinstance(row, dict):
                continue
            lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
        if len(rows) > render_limit:
            lines.append(f"\nShowing {render_limit} of {len(rows)} row(s).")
    elif schema:
        schemas = schema.get("schemas") or []
        if schemas:
            lines.append("")
            lines.append("Schemas: " + ", ".join(str(item) for item in schemas[:50]))
    generated_sql = str(payload.get("generated_sql") or "").strip()
    executed_sql = str(payload.get("executed_sql") or payload.get("sql") or "").strip()
    if generated_sql:
        lines.append("")
        lines.append("Generated SQL:")
        lines.append(f"```sql\n{generated_sql}\n```")
    if executed_sql and executed_sql != generated_sql:
        lines.append("")
        lines.append("Executed SQL:")
        lines.append(f"```sql\n{executed_sql}\n```")
    return "\n".join(lines)


__all__ = ["render_sql_agent_response"]
