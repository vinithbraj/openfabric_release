"""PostgreSQL-first SQL agent capabilities backed by Parameter Store profiles."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from io import StringIO
from typing import Any, Literal

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from agent_runtime.capabilities.base import BaseCapability
from agent_runtime.capabilities.parameters import _parameter_store_from_context
from agent_runtime.capabilities.schemas import CapabilityManifest
from agent_runtime.clarification import (
    normalize_agent_clarification_mode,
    resolve_agent_clarification,
)
from agent_runtime.core.config import RuntimeConfig
from agent_runtime.core.types import ExecutionResult
from agent_runtime.execution.gateway_client import GatewayClient
from agent_runtime.execution.gateway_metadata import merge_gateway_metadata
from agent_runtime.memory import AgentMemoryStore, MemoryRetrievalContext
from agent_runtime.observability import STAGE_EXECUTION, observability_from_context
from agent_runtime.parameters import (
    AgentParameterCreate,
    AgentParameterRecord,
    AgentParameterStore,
    AgentParameterUpdate,
    default_parameter_context_json,
    masked_parameter_summary,
    normalize_parameter_key,
    parameter_context_summary,
    parameter_database_profile,
)

SQL_SCHEMA_CACHE_CONTEXT_KEY = "sql_schema_cache"
SQL_PENDING_CONTEXT_KEY = "sql_agent_pending"
SQL_AGENTIC_CONTEXT_KEY = "sql_agentic_context"
SQL_GATEWAY_REQUIRED_CONTEXT_KEY = "sql_gateway_required"
SQL_EXECUTION_SOURCE_CONTEXT_KEY = "sql_execution_source"

_SQL_GATEWAY_CONTEXT_KEYS = (
    "gateway_id",
    "gateway_node",
    "node",
    "target_node",
    "gateway_url",
    "gateway_endpoints",
    "gateway_execution_id",
    "request_id",
    "run_id",
)

_SQL_INTENT_RE = re.compile(
    r"\b(?:sql|query|queries|database|db|schema|schemas|table|tables|column|columns|"
    r"select|join|where|group\s+by|order\s+by|information_schema|count|counts|"
    r"row|rows|record|records|entry|entries|total)\b",
    re.IGNORECASE,
)
_SQL_COUNT_INTENT_RE = re.compile(
    r"\b(?:count|counts|how\s+many|number\s+of|total|records?|rows?|entries?)\b",
    re.IGNORECASE,
)
_SQL_PROFILE_HINT_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9_-]*_v\d+\b|\b(?:canonical|database|db)\b",
    re.IGNORECASE,
)
_PLURAL_DOMAIN_NOUN_RE = re.compile(r"\b[a-z][a-z0-9_]{3,}s\b", re.IGNORECASE)
_MUTATING_SQL_RE = re.compile(
    r"\b(?:alter|analyze|begin|call|commit|copy|create|delete|do|drop|execute|grant|insert|"
    r"lock|merge|refresh|reindex|replace|reset|revoke|rollback|set|truncate|update|vacuum)\b",
    re.IGNORECASE,
)
_TEMP_TABLE_MATERIALIZATION_RE = re.compile(
    r"\bcreate\s+(?:temporary|temp)\s+table\b|"
    r"\bselect\b.+\binto\s+(?:temporary|temp)\s+(?:table\s+)?[A-Za-z_]",
    re.IGNORECASE | re.DOTALL,
)
_TEMP_TABLE_REQUEST_RE = re.compile(
    r"\b(?:create|make|materiali[sz]e|persist|save)\b.{0,80}\b(?:temporary|temp)\s+table\b|"
    r"\b(?:temporary|temp)\s+table\b.{0,80}\b(?:create|make|materiali[sz]e|persist|save)\b|"
    r"\bselect\b.{0,80}\binto\s+(?:temporary|temp)\b",
    re.IGNORECASE | re.DOTALL,
)
_DISCOVERY_RE = re.compile(
    r"\b(?:schema|schemas|tables|columns)\b|"
    r"\b(?:discover|describe|inspect)\b.*\b(?:table|database|db)\b",
    re.IGNORECASE,
)
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DISCOVERDB_MACRO_RE = re.compile(r"^\s*/discoverdb\b(?P<body>.*)$", re.IGNORECASE | re.DOTALL)
_DISCOVERDB_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_DISCOVERDB_SYSTEM_DBS = frozenset({"postgres", "template0", "template1"})
_DISCOVERDB_ALLOWED_KEYS = frozenset(
    {"engine", "host", "port", "user", "password", "maintenance_db", "sslmode", "include_system"}
)
_DISCOVERDB_SSLMODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)
_DISCOVERDB_DATABASE_QUERY = (
    "SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY datname"
)
_SQL_SCHEMAS_QUERY = (
    "SELECT schema_name FROM information_schema.schemata "
    "WHERE schema_name NOT IN ('pg_catalog','information_schema') "
    "ORDER BY schema_name"
)
_SQL_COLUMNS_QUERY = (
    "SELECT"
    " table_schema,"
    " table_name,"
    " column_name,"
    " data_type,"
    " udt_name,"
    " is_nullable,"
    " column_default,"
    " character_maximum_length,"
    " numeric_precision,"
    " numeric_scale,"
    " ordinal_position"
    " FROM information_schema.columns"
    " WHERE table_schema NOT IN ('pg_catalog','information_schema')"
    " ORDER BY table_schema, table_name, ordinal_position"
)
_SQL_PRIMARY_KEYS_QUERY = (
    "SELECT"
    " tc.table_schema,"
    " tc.table_name,"
    " kcu.column_name,"
    " kcu.ordinal_position"
    " FROM information_schema.table_constraints tc"
    " JOIN information_schema.key_column_usage kcu"
    "   ON tc.constraint_catalog = kcu.constraint_catalog"
    "  AND tc.constraint_schema = kcu.constraint_schema"
    "  AND tc.constraint_name = kcu.constraint_name"
    "  AND tc.table_schema = kcu.table_schema"
    "  AND tc.table_name = kcu.table_name"
    " WHERE tc.constraint_type = 'PRIMARY KEY'"
    "   AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')"
    " ORDER BY tc.table_schema, tc.table_name, kcu.ordinal_position"
)
_SQL_FOREIGN_KEYS_QUERY = (
    "SELECT"
    " tc.table_schema AS child_schema,"
    " tc.table_name AS child_table,"
    " kcu.column_name AS child_column,"
    " pkcu.table_schema AS parent_schema,"
    " pkcu.table_name AS parent_table,"
    " pkcu.column_name AS parent_column,"
    " tc.constraint_name"
    " FROM information_schema.table_constraints tc"
    " JOIN information_schema.key_column_usage kcu"
    "   ON tc.constraint_catalog = kcu.constraint_catalog"
    "  AND tc.constraint_schema = kcu.constraint_schema"
    "  AND tc.constraint_name = kcu.constraint_name"
    "  AND tc.table_schema = kcu.table_schema"
    "  AND tc.table_name = kcu.table_name"
    " JOIN information_schema.referential_constraints rc"
    "   ON tc.constraint_catalog = rc.constraint_catalog"
    "  AND tc.constraint_schema = rc.constraint_schema"
    "  AND tc.constraint_name = rc.constraint_name"
    " JOIN information_schema.key_column_usage pkcu"
    "   ON pkcu.constraint_catalog = rc.unique_constraint_catalog"
    "  AND pkcu.constraint_schema = rc.unique_constraint_schema"
    "  AND pkcu.constraint_name = rc.unique_constraint_name"
    "  AND pkcu.ordinal_position = kcu.position_in_unique_constraint"
    " WHERE tc.constraint_type = 'FOREIGN KEY'"
    "   AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')"
    " ORDER BY child_schema, child_table, kcu.ordinal_position"
)
_SQL_INDEXES_QUERY = (
    "SELECT"
    " schemaname AS table_schema,"
    " tablename AS table_name,"
    " indexname AS index_name,"
    " indexdef"
    " FROM pg_indexes"
    " WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
    " ORDER BY schemaname, tablename, index_name"
)
_SQL_TABLE_STATS_QUERY = (
    "SELECT"
    " schemaname AS table_schema,"
    " relname AS table_name,"
    " n_live_tup AS approx_rows,"
    " n_dead_tup AS approx_dead_rows,"
    " last_analyze,"
    " last_autoanalyze"
    " FROM pg_stat_user_tables"
    " ORDER BY schemaname, relname"
)
_DISCOVERDB_GENERATED_VALUE_FIELDS = frozenset(
    {
        "engine",
        "dbname",
        "host",
        "port",
        "user",
        "password",
        "sslmode",
        "schema_catalog",
        "relation_foreign_scheme",
        "schema_discovery",
    }
)


class DatabaseDiscoveryRequest(BaseModel):
    """Request to discover PostgreSQL databases for Parameter Store profile drafting."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    engine: str = ""
    host: str = ""
    port: int | None = None
    user: str = ""
    password: str = ""
    maintenance_db: str = "postgres"
    sslmode: str = ""
    include_system: bool = False


class DatabaseDiscoveryCommitRequest(BaseModel):
    """Commit reviewed discovery drafts into Parameter Store."""

    model_config = ConfigDict(extra="forbid")

    drafts: list[dict[str, Any]] = Field(default_factory=list)


class SqlAgentRequest(BaseModel):
    """Dedicated API request for the SQL agent."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    parameter_key: str = ""
    sql: str = ""
    operation: Literal["query", "discover", "execute_readonly"] = "query"
    limit: int | None = None
    refresh_schema: bool = False


class SqlAgentAction(BaseModel):
    """One LLM-authored step in the SQL agent loop."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["ask_clarification", "execute_sql", "finish", "abort"] = "execute_sql"
    sql: str = ""
    sql_steps: list[str] = Field(default_factory=list)
    result_strategy: Literal["final_step", "append_rows"] = "final_step"
    question: str = ""
    summary: str = ""
    decomposition: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SqlSummary(BaseModel):
    """LLM-authored result summary."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SqlResultContractReview(BaseModel):
    """Typed LLM review of whether an executed SQL result answers the request shape."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "retry"] = "accept"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    request_intent: str = ""
    expected_result_shape: str = ""
    observed_result_shape: str = ""
    missing_requirements: list[str] = Field(default_factory=list)
    retry_instruction: str = ""
    reason: str = ""


class SqlDiscoverySummary(BaseModel):
    """LLM-authored discovery summary."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


@dataclass
class _ResolvedProfile:
    record: AgentParameterRecord
    profile: dict[str, Any]
    values: dict[str, Any]

    @property
    def normalized_key(self) -> str:
        return self.record.normalized_key

    @property
    def key(self) -> str:
        return self.record.key



__all__ = [name for name in globals() if not name.startswith("__")]
