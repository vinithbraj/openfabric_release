"""SQL agent orchestration service and response rendering."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.service_support.common import *
from agent_runtime.capabilities.sql_support.service_support.context import (
    SqlAgentContextMixin as __SqlAgentContextMixin__,
    _service_from_context,
)
from agent_runtime.capabilities.sql_support.service_support.schema_discovery import (
    SqlAgentSchemaDiscoveryMixin as __SqlAgentSchemaDiscoveryMixin__,
)
from agent_runtime.capabilities.sql_support.service_support.prompting import (
    SqlAgentPromptingMixin as __SqlAgentPromptingMixin__,
)
from agent_runtime.capabilities.sql_support.service_support.classification import (
    SqlAgentClassificationMixin as __SqlAgentClassificationMixin__,
)
from agent_runtime.capabilities.sql_support.service_support.execution import (
    SqlAgentExecutionMixin as __SqlAgentExecutionMixin__,
)
from agent_runtime.capabilities.sql_support.service_support.agent_loop import (
    SqlAgentLoopMixin as __SqlAgentLoopMixin__,
)
from agent_runtime.capabilities.sql_support.service_support.review_summary import (
    SqlAgentReviewSummaryMixin as __SqlAgentReviewSummaryMixin__,
)
from agent_runtime.capabilities.sql_support.service_support.rendering import (
    render_sql_agent_response,
)


class SqlAgentService(
    __SqlAgentContextMixin__,
    __SqlAgentSchemaDiscoveryMixin__,
    __SqlAgentPromptingMixin__,
    __SqlAgentClassificationMixin__,
    __SqlAgentExecutionMixin__,
    __SqlAgentLoopMixin__,
    __SqlAgentReviewSummaryMixin__,
):
    """Small SQL agent service shared by capabilities, chat preflight, and API."""

    def __init__(
        self,
        *,
        parameter_store: AgentParameterStore | None,
        gateway_client: Any,
        llm_client: Any | None,
        memory_store: AgentMemoryStore | None,
        config: RuntimeConfig,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.parameter_store = parameter_store
        self.gateway_client = gateway_client
        self.llm_client = llm_client
        self.memory_store = memory_store
        self.config = config
        self.context = context if isinstance(context, dict) else {}


    def _error(
        self,
        message: str,
        *,
        parameter_key: str = "",
        engine: str = "",
        sql: str = "",
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "error",
            "parameter_key": parameter_key,
            "engine": engine,
            "sql": sql,
            "summary": "",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "schema": {},
            "warnings": list(warnings or []),
            "error": str(message or "SQL agent failed."),
        }


__all__ = [name for name in globals() if not name.startswith("__")]
