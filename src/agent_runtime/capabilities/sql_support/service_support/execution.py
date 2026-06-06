"""SQL gateway execution helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.service_support.common import *

class SqlAgentExecutionMixin:
    def _run_psql(
        self,
        resolved: _ResolvedProfile,
        sql: str,
        *,
        operation: str = "query",
        generated_sql: str = "",
        executed_sql: str = "",
    ) -> dict[str, Any]:
        values = resolved.values
        dbname = _get_scalar(values, "dbname", "database", "db", "database_name")
        host = _get_scalar(values, "host", "hostname", "server")
        port = _get_scalar(values, "port")
        user = _get_scalar(values, "user", "username")
        password = _get_scalar(values, "password", "passwd", "pass")
        missing = [
            name
            for name, value in {
                "dbname": dbname,
                "host": host,
                "port": port,
                "user": user,
                "password": password,
            }.items()
            if not value
        ]
        if missing:
            return self._error(
                "Database profile is missing required PostgreSQL field(s): " + ", ".join(missing),
                parameter_key=resolved.key,
            )
        command = (
            'PGPASSWORD="$OF_SQL_PASSWORD" psql --no-psqlrc --csv -v ON_ERROR_STOP=1 '
            '-h "$OF_SQL_HOST" -p "$OF_SQL_PORT" -U "$OF_SQL_USER" -d "$OF_SQL_DBNAME" '
            '-c "$OF_SQL_QUERY"'
        )
        shell_env = {
            "OF_SQL_DBNAME": dbname,
            "OF_SQL_HOST": host,
            "OF_SQL_PORT": port,
            "OF_SQL_USER": user,
            "OF_SQL_PASSWORD": password,
            "OF_SQL_QUERY": sql,
        }
        secrets = [dbname, host, port, user, password]
        result = _execute_gateway_sql_command(
            gateway_client=self.gateway_client,
            command=command,
            cwd=str(getattr(self.config, "workspace_root", ".") or "."),
            execution_context=_sql_gateway_execution_context(
                self.context,
                shell_env=shell_env,
            ),
            observability_context=self.context,
            sql=sql,
            profile_label=resolved.key,
            operation=operation,
            secrets=secrets,
            generated_sql=generated_sql or sql,
            executed_sql=executed_sql or sql,
        )
        exit_code = int(result.get("exit_code") or 0)
        stdout = _sanitize_text(result.get("stdout") or "", secrets)
        stderr = _sanitize_text(result.get("stderr") or "", secrets)
        if exit_code != 0:
            payload = self._error(
                stderr or f"psql exited with code {exit_code}",
                parameter_key=resolved.key,
                sql=sql,
            )
            payload.update({"stdout": stdout, "stderr": stderr, "exit_code": exit_code})
            return payload
        return {"status": "success", "stdout": stdout, "stderr": stderr, "exit_code": exit_code}


__all__ = []
