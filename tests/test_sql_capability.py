from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_runtime.api.app import create_app
from agent_runtime.api.config import Settings
from agent_runtime.capabilities import build_default_registry
from agent_runtime.capabilities.sql import (
    DatabaseDiscoveryCommitRequest,
    SQL_AGENTIC_CONTEXT_KEY,
    SqlAgentService,
    SqlDatabaseDiscoveryService,
    looks_like_sql_intent,
    parse_discoverdb_macro,
    render_sql_agent_response,
)
from agent_runtime.core.config import RuntimeConfig
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.core.types import CapabilityRef, TaskFrame
from agent_runtime.execution.engine import ExecutionEngine
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.input_pipeline.argument_extraction import _validate_operator_arguments
from agent_runtime.input_pipeline.capability_fit import CapabilityFitDecision
from agent_runtime.input_pipeline.domain_selection import CapabilitySelectionResult
from agent_runtime.llm.proposals import CapabilitySelectionProposal
from agent_runtime.observability import InMemoryEventSink, ObservabilityContext
from agent_runtime.operator.user_macros import prompt_macro_registry
from agent_runtime.output_pipeline.orchestrator import OutputPipelineOrchestrator
from agent_runtime.parameters import AgentParameterCreate, AgentParameterStore


class QueueLLM:
    model = "fake-sql"
    temperature = 0.0

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.schemas: list[str] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.schemas.append(str(schema.get("title") or ""))
        if str(schema.get("title") or "") == "PromptRephraseProposal":
            if self.responses and "rephrased_prompt" in self.responses[0]:
                return self.responses.pop(0)
            return {
                "rephrased_prompt": self._prompt_user_text(prompt),
                "changed": False,
                "preserved_requirements": [],
                "removed_duplication": [],
                "reason": "The test prompt is already clear.",
                "confidence": 0.95,
            }
        if not self.responses:
            raise AssertionError("Unexpected SQL LLM call")
        return self.responses.pop(0)

    @staticmethod
    def _prompt_user_text(prompt: str) -> str:
        marker = "Original prompt:\n"
        if marker not in prompt:
            return ""
        return prompt.split(marker, 1)[1].strip()


def _sql_contract_accept(reason: str = "The result shape answers the request.") -> dict[str, Any]:
    return {
        "decision": "accept",
        "confidence": 0.95,
        "request_intent": "Return the requested SQL answer table.",
        "expected_result_shape": "Columns and rows match the requested answer shape.",
        "observed_result_shape": "The executed result contains answer rows.",
        "missing_requirements": [],
        "retry_instruction": "",
        "reason": reason,
    }


def _sql_contract_retry(
    instruction: str = "Regenerate SQL that computes the requested metric for every requested bucket.",
) -> dict[str, Any]:
    return {
        "decision": "retry",
        "confidence": 0.94,
        "request_intent": "Return an aggregate metric for each requested bucket.",
        "expected_result_shape": "One row per requested bucket with a bucket label and metric column.",
        "observed_result_shape": "Only the bucket labels were returned.",
        "missing_requirements": ["requested count or aggregate metric"],
        "retry_instruction": instruction,
        "reason": "The executed SQL produced preparatory bucket values instead of the requested aggregate answer.",
    }


class FakeSqlGateway:
    def __init__(self) -> None:
        self.raw_calls: list[dict[str, Any]] = []

    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(execution_context or {})
        self.raw_calls.append({"command": command, "cwd": cwd, "execution_context": context})
        sql = str((context.get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        if "bad_table" in sql:
            return {
                "stdout": "",
                "stderr": "ERROR: relation bad_table does not exist",
                "exit_code": 1,
                "gateway_node": "fake",
            }
        if "information_schema.schemata" in sql:
            return {
                "stdout": "schema_name\npublic\ncanonical\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "information_schema.columns" in sql:
            return {
                "stdout": "\n".join(
                    [
                        "table_schema,table_name,column_name,data_type,ordinal_position",
                        "canonical,patients,patient_id,integer,1",
                        "canonical,patients,name,text,2",
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        return {
            "stdout": "patient_id,name\n1,Ada\n",
            "stderr": "",
            "exit_code": 0,
            "gateway_node": "fake",
        }


class MixedCaseSqlGateway(FakeSqlGateway):
    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(execution_context or {})
        self.raw_calls.append({"command": command, "cwd": cwd, "execution_context": context})
        sql = str((context.get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        if "information_schema.schemata" in sql:
            return {
                "stdout": "schema_name\nflathr\npublic\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "information_schema.columns" in sql:
            return {
                "stdout": "\n".join(
                    [
                        "table_schema,table_name,column_name,data_type,ordinal_position",
                        "flathr,Patient,id,integer,1",
                        "flathr,Patient,PatientBirthDate,date,2",
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        return {
            "stdout": "count\n42\n",
            "stderr": "",
            "exit_code": 0,
            "gateway_node": "fake",
        }


class AgeBucketSqlGateway(MixedCaseSqlGateway):
    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(execution_context or {})
        sql = str((context.get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        if "information_schema" in sql:
            return super().execute_raw_command(
                command=command,
                cwd=cwd,
                execution_context=execution_context,
            )
        self.raw_calls.append({"command": command, "cwd": cwd, "execution_context": context})
        if "generate_series" in sql.lower() or "GENERATE_SERIES" in sql:
            return {
                "stdout": "age,patient_count\n1,0\n2,3\n3,0\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "SELECT 1 AS age" in sql:
            return {
                "stdout": "age,patient_count\n1,0\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "SELECT 2 AS age" in sql:
            return {
                "stdout": "age,patient_count\n2,3\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "SELECT 2 AS bucket" in sql:
            return {
                "stdout": "bucket,patient_count\n2,3\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        return {
            "stdout": "age,patient_count\n1,0\n",
            "stderr": "",
            "exit_code": 0,
            "gateway_node": "fake",
        }


class PatientAgeSqlGateway(FakeSqlGateway):
    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(execution_context or {})
        self.raw_calls.append({"command": command, "cwd": cwd, "execution_context": context})
        sql = str((context.get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        if "information_schema.schemata" in sql:
            return {
                "stdout": "schema_name\nflathr\npublic\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "information_schema.columns" in sql:
            return {
                "stdout": "\n".join(
                    [
                        "table_schema,table_name,column_name,data_type,ordinal_position",
                        "flathr,Patient,PatientID,text,1",
                        "flathr,Patient,Age,integer,2",
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'SELECT COUNT(*) AS "row_count" FROM "flathr"."Patient"' in sql:
            return {
                "stdout": "row_count\n28102\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'SELECT * FROM "flathr"."Patient" LIMIT 5' in sql:
            return {
                "stdout": "PatientID,Age\np1,46\np2,21\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'WHERE "Age" > 45' in sql:
            return {
                "stdout": "count\n9\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'COUNT(*) AS "count" FROM "flathr"."Patient"' in sql:
            return {
                "stdout": "count\n28102\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        return {
            "stdout": "count\n0\n",
            "stderr": "",
            "exit_code": 0,
            "gateway_node": "fake",
        }


class RelationshipSqlGateway(FakeSqlGateway):
    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(execution_context or {})
        self.raw_calls.append({"command": command, "cwd": cwd, "execution_context": context})
        sql = str((context.get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        if "information_schema.schemata" in sql:
            return {
                "stdout": "schema_name\nflathr\npublic\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "information_schema.columns" in sql:
            return {
                "stdout": "\n".join(
                    [
                        "table_schema,table_name,column_name,data_type,ordinal_position",
                        "flathr,Patient,PatientID,text,1",
                        "flathr,PatientSetupSequence,PatientID,text,1",
                        "flathr,RTRECORD,RTRecordID,text,1",
                        "flathr,RTRECORD,PatientKey,text,2",
                        "flathr,RTDOSE,PatientKey,text,1",
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if '"RTRECORD"' in sql and '"PatientID"' in sql:
            return {
                "stdout": "",
                "stderr": 'ERROR: column "PatientID" does not exist',
                "exit_code": 1,
                "gateway_node": "fake",
            }
        if '"RTRECORD"' in sql and '"PatientKey"' in sql and "HAVING COUNT(*) > 100" in sql:
            return {
                "stdout": "count\n4\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        return {
            "stdout": "count\n0\n",
            "stderr": "",
            "exit_code": 0,
            "gateway_node": "fake",
        }


class PatientRtrecordSqlGateway(FakeSqlGateway):
    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(execution_context or {})
        self.raw_calls.append({"command": command, "cwd": cwd, "execution_context": context})
        sql = str((context.get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        if "information_schema.schemata" in sql:
            return {
                "stdout": "schema_name\nflathr\npublic\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "information_schema.columns" in sql:
            return {
                "stdout": "\n".join(
                    [
                        "table_schema,table_name,column_name,data_type,ordinal_position",
                        "flathr,Patient,PatientKey,text,1",
                        "flathr,Patient,Age,integer,2",
                        "flathr,RTRECORD,RTRecordID,text,1",
                        "flathr,RTRECORD,PatientKey,text,2",
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if '"PatientID"' in sql:
            return {
                "stdout": "",
                "stderr": 'ERROR: column "PatientID" does not exist',
                "exit_code": 1,
                "gateway_node": "fake",
            }
        if (
            '"PatientKey"' in sql
            and '"Age" > 60' in sql
            and "HAVING COUNT(*) > 50" in sql
        ):
            return {
                "stdout": "count\n3\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        return {
            "stdout": "count\n0\n",
            "stderr": "",
            "exit_code": 0,
            "gateway_node": "fake",
        }


class EntitySqlGateway(FakeSqlGateway):
    def __init__(self, *, include_public_rtrecords: bool = False) -> None:
        super().__init__()
        self.include_public_rtrecords = include_public_rtrecords

    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(execution_context or {})
        self.raw_calls.append({"command": command, "cwd": cwd, "execution_context": context})
        sql = str((context.get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        if "information_schema.schemata" in sql:
            return {
                "stdout": "schema_name\nflathr\npublic\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "information_schema.columns" in sql:
            rows = [
                "table_schema,table_name,column_name,data_type,ordinal_position",
                "flathr,Patient,PatientID,text,1",
                "flathr,RTRecord,RTRecordID,text,1",
                "flathr,RTRecord,PatientID,text,2",
                "flathr,RTRecord,Dose,float,3",
                "flathr,RTPlan,PlanID,text,1",
                "flathr,RTPlan,PatientID,text,2",
            ]
            if self.include_public_rtrecords:
                rows.extend(
                    [
                        "public,rtrecords,id,text,1",
                        "public,rtrecords,payload,text,2",
                    ]
                )
            return {
                "stdout": "\n".join(rows) + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'COUNT(*) AS "row_count" FROM "flathr"."RTRecord"' in sql:
            return {
                "stdout": "row_count\n123\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'COUNT(*) AS "row_count" FROM "flathr"."RTPlan"' in sql:
            return {
                "stdout": "row_count\n7\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'COUNT(*) AS "row_count" FROM "public"."rtrecords"' in sql:
            return {
                "stdout": "row_count\n2\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'SELECT * FROM "flathr"."RTRecord" LIMIT 5' in sql:
            return {
                "stdout": "RTRecordID,PatientID,Dose\nrt1,p1,12.5\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'SELECT * FROM "flathr"."RTPlan" LIMIT 5' in sql:
            return {
                "stdout": "PlanID,PatientID\nplan1,p1\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'SELECT * FROM "public"."rtrecords" LIMIT 5' in sql:
            return {
                "stdout": "id,payload\nrt-public,legacy\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'COUNT(*) AS "count" FROM "flathr"."RTRecord"' in sql:
            return {
                "stdout": "count\n123\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if 'COUNT(*) AS "count" FROM "flathr"."RTPlan"' in sql:
            return {
                "stdout": "count\n7\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        return {
            "stdout": "count\n0\n",
            "stderr": "",
            "exit_code": 0,
            "gateway_node": "fake",
        }


class FakeDiscoverDbGateway:
    def __init__(self) -> None:
        self.raw_calls: list[dict[str, Any]] = []

    def execute_raw_command(
        self,
        *,
        command: str,
        cwd: str,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(execution_context or {})
        self.raw_calls.append({"command": command, "cwd": cwd, "execution_context": context})
        sql = str((context.get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        lowered = sql.lower()
        if "from information_schema.schemata" in lowered:
            return {
                "stdout": "schema_name\npublic\ncanonical\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "constraint_type = 'primary key'" in lowered:
            return {
                "stdout": "\n".join(
                    [
                        "table_schema,table_name,column_name,ordinal_position",
                        "canonical,patients,patient_id,1",
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "constraint_type = 'foreign key'" in lowered:
            return {
                "stdout": "\n".join(
                    [
                        "child_schema,child_table,child_column,parent_schema,parent_table,parent_column,constraint_name",
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "from pg_indexes" in lowered:
            return {
                "stdout": "\n".join(
                    [
                        "table_schema,table_name,index_name,indexdef",
                        "canonical,patients,patients_pkey,"
                        '"CREATE UNIQUE INDEX patients_pkey ON canonical.patients USING btree (patient_id)"',
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "from pg_stat_user_tables" in lowered:
            return {
                "stdout": "\n".join(
                    [
                        "table_schema,table_name,approx_rows,approx_dead_rows,last_analyze,last_autoanalyze",
                        "canonical,patients,42,0,2026-01-01 10:00:00,",
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        if "from information_schema.columns" in lowered:
            return {
                "stdout": "\n".join(
                    [
                        "table_schema,table_name,column_name,data_type,udt_name,is_nullable,column_default,character_maximum_length,numeric_precision,numeric_scale,ordinal_position",
                        "canonical,patients,patient_id,integer,int4,NO,,,32,0,1",
                        "canonical,patients,name,text,text,YES,,,,,2",
                    ]
                )
                + "\n",
                "stderr": "",
                "exit_code": 0,
                "gateway_node": "fake",
            }
        return {
            "stdout": "datname\npostgres\nCANONICAL_v1\nanalytics\ntemplate1\n",
            "stderr": "",
            "exit_code": 0,
            "gateway_node": "fake",
        }


def _create_store(tmp_path: Path) -> AgentParameterStore:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="canonica_v1",
            value_json={
                "dbname": "CANONICAL_v1",
                "host": "10.44.102.204",
                "password": "pass123",
                "port": 9501,
                "user": "jimSolomon@mednet.ucla.edu",
            },
            sensitive=True,
        )
    )
    return store


def _stored_schema_profile_value() -> dict[str, Any]:
    schema_catalog = {
        "version": 1,
        "parameter_key": "canonica_v1",
        "engine": "postgresql",
        "schemas": ["flathr"],
        "tables": [
            {
                "schema": "flathr",
                "table": "Patient",
                "qualified_name": "flathr.Patient",
                "columns": [
                    {"name": "PatientKey", "type": "text", "ordinal_position": 1},
                    {"name": "Age", "type": "integer", "ordinal_position": 2},
                ],
                "primary_keys": ["PatientKey"],
                "indexes": [],
            },
            {
                "schema": "flathr",
                "table": "RTRECORD",
                "qualified_name": "flathr.RTRECORD",
                "columns": [
                    {"name": "RTRecordID", "type": "text", "ordinal_position": 1},
                    {"name": "PatientKey", "type": "text", "ordinal_position": 2},
                ],
                "primary_keys": ["RTRecordID"],
                "indexes": [],
            },
        ],
        "approved_joins": [],
        "inferred_secondary_keys": [
            {
                "left": "flathr.Patient",
                "left_column": "PatientKey",
                "right": "flathr.RTRECORD",
                "right_column": "PatientKey",
                "match_reason": "same_secondary_key_name",
                "confidence": "inferred",
                "requires_validation": True,
                "status": "NOT_APPROVED_INFERRED",
            }
        ],
        "refreshed_at": "2026-01-01T00:00:00+00:00",
    }
    return {
        "engine": "postgresql",
        "dbname": "CANONICAL_v1",
        "host": "10.44.102.204",
        "password": "pass123",
        "port": 9501,
        "user": "jimSolomon@mednet.ucla.edu",
        "schema_catalog": schema_catalog,
        "relation_foreign_scheme": {
            "version": 1,
            "source": "postgresql_information_schema",
            "refreshed_at": "2026-01-01T00:00:00+00:00",
            "approved_foreign_keys": [],
            "inferred_secondary_keys": schema_catalog["inferred_secondary_keys"],
        },
        "schema_discovery": {
            "status": "success",
            "refreshed_at": "2026-01-01T00:00:00+00:00",
            "warnings": [],
            "errors": [],
        },
    }


def _stored_schema_profile_context(summary: str | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "user_provided_domain_context": {
            "summary": summary
            or "Patient.PatientKey semantically identifies patients across RT objects.",
            "prompt_guidance": "Use the stored schema and domain relationships as prompt-only guidance.",
            "clarification_guidance": "",
            "concepts": [],
            "metrics": [],
            "relationships": [],
        },
    }


def _empty_store(tmp_path: Path) -> AgentParameterStore:
    return AgentParameterStore(tmp_path / "empty-parameters.db")


def _service(
    tmp_path: Path,
    *,
    store: AgentParameterStore | None = None,
    gateway: FakeSqlGateway | None = None,
    llm: QueueLLM | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[SqlAgentService, FakeSqlGateway]:
    gateway = gateway or FakeSqlGateway()
    service = SqlAgentService(
        parameter_store=store or _create_store(tmp_path),
        gateway_client=gateway,
        llm_client=llm,
        memory_store=None,
        config=RuntimeConfig(workspace_root=str(tmp_path), sql_agent_max_repair_attempts=1),
        context=context if context is not None else {},
    )
    return service, gateway


def _runtime(
    tmp_path: Path,
    *,
    store: AgentParameterStore | None = None,
    gateway: FakeSqlGateway | None = None,
    llm: QueueLLM | None = None,
) -> tuple[AgentRuntime, FakeSqlGateway, QueueLLM]:
    registry = build_default_registry()
    gateway = gateway or FakeSqlGateway()
    llm = llm or QueueLLM(
        {
            "action": "execute_sql",
            "sql": "SELECT patient_id, name FROM canonical.patients ORDER BY patient_id LIMIT 100",
            "assumptions": [],
            "confidence": 0.92,
        },
        {"summary": "Found 1 patient.", "confidence": 0.9},
    )
    engine = ExecutionEngine(
        registry,
        RuntimeConfig(
            workspace_root=str(tmp_path),
            allow_shell_execution=True,
            gateway_url="http://gateway",
        ),
        InMemoryResultStore(),
        gateway_client=gateway,  # type: ignore[arg-type]
    )
    runtime = AgentRuntime(
        llm_client=llm,
        registry=registry,
        execution_engine=engine,
        output_orchestrator=OutputPipelineOrchestrator(),
        parameter_store=store or _create_store(tmp_path),
    )
    return runtime, gateway, llm


def _agentic_sql_llm(
    *,
    sql: str,
    summary: str,
    selected_table: str = "flathr.RTRecord",
    prompt_arguments: dict[str, Any] | None = None,
) -> QueueLLM:
    """Return LLM responses for SQL-agentic classification plus SQL planning."""

    _ = selected_table, prompt_arguments
    return QueueLLM(
        {
            "prompt_type": "simple_tool_task",
            "requires_tools": True,
            "likely_domains": ["sql"],
            "risk_level": "low",
            "needs_clarification": False,
            "clarification_question": None,
            "reason": "Database question over the selected SQL profile.",
            "confidence": 0.95,
            "assumptions": [],
            "domain_evaluations": [
                {
                    "domain": "sql",
                    "fits": True,
                    "confidence": 0.95,
                    "reason": "The request asks for a database count.",
                }
            ],
        },
        {
            "action": "execute_sql",
            "sql": sql,
            "assumptions": [],
            "confidence": 0.95,
        },
        {"summary": summary, "confidence": 0.9},
    )


def _discovery_service(
    tmp_path: Path,
    *,
    store: AgentParameterStore | None = None,
    gateway: FakeDiscoverDbGateway | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[SqlDatabaseDiscoveryService, FakeDiscoverDbGateway]:
    gateway = gateway or FakeDiscoverDbGateway()
    service = SqlDatabaseDiscoveryService(
        parameter_store=store or _empty_store(tmp_path),
        gateway_client=gateway,
        config=RuntimeConfig(workspace_root=str(tmp_path)),
        context=dict(context or {}),
    )
    return service, gateway


def test_discoverdb_macro_parser_accepts_and_rejects_expected_shapes() -> None:
    request = parse_discoverdb_macro(
        '/discoverdb engine=postgres host=10.44.102.204 port=9501 user="jim" password="pass123"'
    )

    assert request is not None
    assert request.engine == "postgresql"
    assert request.host == "10.44.102.204"
    assert request.port == 9501
    assert request.user == "jim"
    assert request.password == "pass123"
    assert request.maintenance_db == "postgres"
    assert request.include_system is False

    for prompt in (
        '/discoverdb host=10.44.102.204 port=9501 user="jim" password="pass123"',
        '/discoverdb engine=mysql host=10.44.102.204 port=9501 user="jim" password="pass123"',
        '/discoverdb engine=postgres host=10.44.102.204 port=9501 user="jim"',
        '/discoverdb engine=postgres host=10.44.102.204 port=99999 user="jim" password="pass123"',
        '/discoverdb engine=postgres host=10.44.102.204 port=9501 user="jim password="pass123"',
    ):
        with pytest.raises(Exception):
            parse_discoverdb_macro(prompt)


def test_discoverdb_preview_drafts_database_parameters_and_uses_env_vars(tmp_path: Path) -> None:
    service, gateway = _discovery_service(
        tmp_path,
        context={
            "gateway_id": "gw-discover",
            "gateway_node": "db-worker",
            "gateway_url": "http://db-worker:8787",
            "gateway_endpoints": {"db-worker": "http://db-worker:8787"},
        },
    )
    request = parse_discoverdb_macro(
        '/discoverdb engine=postgres host=10.44.102.204 port=9501 user="jim" password="pass123"'
    )

    payload = service.discover(request)

    assert payload["status"] == "preview"
    assert payload["engine"] == "postgresql"
    assert [draft["key"] for draft in payload["discovered"]] == ["canonical_v1", "analytics"]
    canonical = payload["discovered"][0]
    assert canonical["operation"] == "create"
    assert {key: canonical["value_json"][key] for key in ("engine", "dbname", "host", "port", "user", "password")} == {
        "engine": "postgresql",
        "dbname": "CANONICAL_v1",
        "host": "10.44.102.204",
        "port": 9501,
        "user": "jim",
        "password": "pass123",
    }
    assert "user_provided_data_organization" not in canonical["value_json"]
    assert canonical["context_json"]["user_provided_domain_context"]["summary"] == ""
    assert canonical["value_json"]["schema_discovery"]["status"] == "success"
    assert canonical["value_json"]["schema_catalog"]["tables"][0]["table"] == "patients"
    assert canonical["value_json"]["relation_foreign_scheme"]["source"] == (
        "postgresql_information_schema"
    )
    assert canonical["tags"] == ["database", "postgresql", "discoverdb"]
    assert canonical["aliases"] == ["CANONICAL_v1", "canonical_v1"]
    assert canonical["sensitive"] is True
    assert payload["skipped_existing"] == []
    assert payload["updated"] == []
    assert payload["errors"] == []
    call = gateway.raw_calls[0]
    assert "psql --no-psqlrc --csv" in call["command"]
    assert "OF_DISCOVERDB_PASSWORD" in call["command"]
    assert "pass123" not in call["command"]
    assert "jim" not in call["command"]
    assert "10.44.102.204" not in call["command"]
    assert call["execution_context"]["gateway_id"] == "gw-discover"
    assert call["execution_context"]["gateway_node"] == "db-worker"
    assert call["execution_context"]["gateway_url"] == "http://db-worker:8787"
    assert call["execution_context"]["sql_gateway_required"] is True
    assert call["execution_context"]["sql_execution_source"] == "sql_agent"
    shell_env = call["execution_context"]["shell_env"]
    assert shell_env["OF_DISCOVERDB_PASSWORD"] == "pass123"
    assert shell_env["OF_DISCOVERDB_QUERY"].startswith("SELECT datname FROM pg_database")


def test_discoverdb_updates_existing_and_commit_creates_profiles(tmp_path: Path) -> None:
    store = _empty_store(tmp_path)
    store.create(
        AgentParameterCreate(
            key="canonical_v1",
            value_json={
                "engine": "postgresql",
                "dbname": "CANONICAL_v1",
                "host": "existing",
                "port": 5432,
                "user": "existing",
                "password": "existing",
                "custom_context": {"owner": "radiation-oncology"},
            },
            context_json=_stored_schema_profile_context(
                "Patients relate to RT records semantically."
            ),
            aliases=["CANONICAL_v1", "canonical_v1"],
            tags=["database", "postgresql"],
            sensitive=True,
        )
    )
    service, _gateway = _discovery_service(tmp_path, store=store)
    request = parse_discoverdb_macro(
        '/discoverdb engine=postgres host=10.44.102.204 port=9501 user="jim" password="pass123"'
    )
    preview = service.discover(request)

    assert [draft["key"] for draft in preview["discovered"]] == ["canonical_v1", "analytics"]
    assert preview["discovered"][0]["operation"] == "update"
    assert [item["key"] for item in preview["updated"]] == ["canonical_v1"]
    assert preview["skipped_existing"] == []

    committed = service.commit(DatabaseDiscoveryCommitRequest(drafts=preview["discovered"]))

    assert committed["status"] == "success"
    assert [item["key"] for item in committed["created"]] == ["analytics"]
    assert [item["key"] for item in committed["updated"]] == ["canonical_v1"]
    existing_record = store.get("canonical_v1")
    assert existing_record is not None
    assert existing_record.value_json["host"] == "10.44.102.204"
    assert existing_record.value_json["port"] == 9501
    assert existing_record.value_json["user"] == "jim"
    assert existing_record.value_json["password"] == "pass123"
    assert existing_record.context_json["user_provided_domain_context"]["summary"] == (
        "Patients relate to RT records semantically."
    )
    assert existing_record.value_json["custom_context"] == {"owner": "radiation-oncology"}
    assert existing_record.value_json["schema_catalog"]["tables"][0]["table"] == "patients"
    record = store.get("analytics")
    assert record is not None
    assert record.value_json["dbname"] == "analytics"
    assert record.value_json["host"] == "10.44.102.204"
    assert record.value_json["port"] == 9501
    assert record.value_json["user"] == "jim"
    assert record.value_json["password"] == "pass123"
    assert record.sensitive is True
    assert record.tags == ["database", "postgresql", "discoverdb"]
    assert record.aliases == ["analytics"]

    repeated = service.commit(DatabaseDiscoveryCommitRequest(drafts=preview["discovered"]))
    assert repeated["status"] == "success"
    assert repeated["created"] == []
    assert [item["key"] for item in repeated["updated"]] == ["canonical_v1"]
    assert [item["key"] for item in repeated["skipped_existing"]] == ["analytics"]


def test_discoverdb_api_preview_and_commit_are_immediately_matchable(tmp_path: Path) -> None:
    store = _empty_store(tmp_path)
    runtime, gateway, _llm = _runtime(tmp_path, store=store, gateway=FakeDiscoverDbGateway())  # type: ignore[arg-type]
    client = TestClient(
        create_app(
            Settings(
                workspace_root=tmp_path,
                openai_compat_model_name="OpenFABRIC Echo",
                agent_parameters_db_path=tmp_path / "api-parameters.db",
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

    macro = (
        '/discoverdb engine=postgres host=10.44.102.204 port=9501 '
        'user="jim" password="pass123"'
    )
    preview_response = client.post(
        "/api/agent/databases/discover",
        json={
            "prompt": macro,
            "context": {
                "gateway_id": "gw-discover-api",
                "gateway_node": "discover-worker",
                "gateway_url": "http://discover-worker:8787",
                "gateway_endpoints": {"discover-worker": "http://discover-worker:8787"},
            },
        },
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["status"] == "preview"
    assert [draft["key"] for draft in preview["discovered"]] == ["canonical_v1", "analytics"]
    for call in gateway.raw_calls:
        execution_context = call["execution_context"]
        assert execution_context["gateway_id"] == "gw-discover-api"
        assert execution_context["gateway_node"] == "discover-worker"
        assert execution_context["gateway_url"] == "http://discover-worker:8787"
        assert execution_context["gateway_endpoints"] == {
            "discover-worker": "http://discover-worker:8787"
        }
        assert execution_context["sql_gateway_required"] is True
        assert execution_context["sql_execution_source"] == "sql_agent"

    commit_response = client.post(
        "/api/agent/databases/discover/commit",
        json={"drafts": preview["discovered"]},
    )

    assert commit_response.status_code == 200
    commit = commit_response.json()
    assert commit["status"] == "success"
    assert store.get("canonical_v1") is not None
    matches = store.retrieve_matches("list all schemas in canonical_v1", limit=3, record_use=False)
    assert any(match.record.key == "canonical_v1" for match in matches)


def test_discoverdb_macro_short_circuits_chat_planning(tmp_path: Path) -> None:
    store = _empty_store(tmp_path)
    runtime, gateway, llm = _runtime(tmp_path, store=store, gateway=FakeDiscoverDbGateway())  # type: ignore[arg-type]
    macro = (
        '/discoverdb engine=postgres host=10.44.102.204 port=9501 '
        'user="jim" password="pass123"'
    )

    response = runtime.handle_request(macro, {"workspace_root": str(tmp_path)})

    assert "Database discovery preview ready." in response
    assert "Profiles to save or refresh: canonical_v1, analytics" in response
    assert "pass123" not in response
    assert "jim" not in response
    assert len(gateway.raw_calls) == 13
    assert llm.prompts == []


def test_prompt_macro_registry_includes_discoverdb() -> None:
    macros = prompt_macro_registry()

    assert any(macro["id"] == "discoverdb" for macro in macros)


def test_sql_intent_detection_for_parameter_backed_database_request() -> None:
    assert looks_like_sql_intent("list all schemas in canonical_v1")
    assert looks_like_sql_intent("show patients by count in the canonical database")
    assert looks_like_sql_intent("in canoinical how many rtrecords do we have")
    assert not looks_like_sql_intent("summarize this README")


def test_sql_execute_readonly_is_not_agent_visible() -> None:
    registry = build_default_registry()
    runtime_ids = {manifest.capability_id for manifest in registry.list_manifests()}
    planning_ids = {
        manifest.capability_id for manifest in registry.planning_view().list_manifests()
    }

    assert "sql.query" in runtime_ids
    assert "sql.execute_readonly" not in runtime_ids
    assert "sql.execute_readonly" not in planning_ids


@pytest.mark.parametrize(
    "prompt",
    [
        "list schemas in canonical",
        "list schemas in canoinical",
        "list schemas in canonical_v1",
        "list schemas in CANONICAL_v1",
    ],
)
def test_sql_fuzzy_profile_resolution_matches_saved_database_identity(
    tmp_path: Path,
    prompt: str,
) -> None:
    service, _gateway = _service(tmp_path)

    payload = service.run(prompt=prompt, operation="discover")

    assert payload["status"] == "success"
    assert payload["parameter_key"] == "canonica_v1"


def test_sql_discovery_uses_psql_env_and_cache_without_credential_leaks(
    tmp_path: Path,
) -> None:
    context: dict[str, Any] = {
        "gateway_id": "gw-sql",
        "gateway_node": "worker-a",
        "gateway_url": "http://worker-a:8787",
        "gateway_endpoints": {"worker-a": "http://worker-a:8787"},
    }
    service, gateway = _service(tmp_path, context=context)

    payload = service.run(prompt="list all schemas in canonical_v1", operation="discover")
    cached_payload = service.run(prompt="list all schemas in canonical_v1", operation="discover")

    assert payload["status"] == "success"
    assert payload["parameter_key"] == "canonica_v1"
    assert payload["schema"]["schemas"] == ["public", "canonical"]
    assert payload["schema"]["tables"][0]["table"] == "patients"
    assert payload["columns"] == ["schema"]
    assert payload["rows"] == [{"schema": "public"}, {"schema": "canonical"}]
    assert cached_payload["warnings"] == ["schema_cache_hit"]
    assert len(gateway.raw_calls) == 6
    assert all("psql --no-psqlrc --csv" in call["command"] for call in gateway.raw_calls)
    assert all('OF_INPUT_DBNAME' not in call["command"] for call in gateway.raw_calls)
    assert all("sqlite3" not in call["command"] for call in gateway.raw_calls)
    rendered_calls = str(gateway.raw_calls)
    assert "pass123" in rendered_calls
    assert "jimSolomon@mednet.ucla.edu" in rendered_calls
    for call in gateway.raw_calls:
        command = call["command"]
        execution_context = call["execution_context"]
        assert execution_context["gateway_id"] == "gw-sql"
        assert execution_context["gateway_node"] == "worker-a"
        assert execution_context["gateway_url"] == "http://worker-a:8787"
        assert execution_context["gateway_endpoints"] == {"worker-a": "http://worker-a:8787"}
        assert execution_context["sql_gateway_required"] is True
        assert execution_context["sql_execution_source"] == "sql_agent"
        assert "gateway_client" not in execution_context
        assert "parameter_store" not in execution_context
        assert "pass123" not in command
        assert "jimSolomon@mednet.ucla.edu" not in command
        assert "10.44.102.204" not in command


def test_sql_query_prefers_stored_schema_and_prompts_with_private_context(
    tmp_path: Path,
) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="canonica_v1",
            value_json=_stored_schema_profile_value(),
            context_json=_stored_schema_profile_context(),
            aliases=["CANONICAL_v1", "canonical_v1"],
            tags=["database", "postgresql", "discoverdb"],
            sensitive=True,
        )
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": 'SELECT "PatientKey", "Age" FROM "flathr"."Patient" LIMIT 10',
            "assumptions": [],
            "confidence": 0.9,
        },
        {"summary": "Found stored-schema patients.", "confidence": 0.9},
    )
    sink = InMemoryEventSink()
    observability = ObservabilityContext(
        request_id="req-sql-stored-context",
        enabled=True,
        debug=True,
        sinks=[sink],
    )
    service, gateway = _service(
        tmp_path,
        store=store,
        gateway=PatientAgeSqlGateway(),
        llm=llm,
        context={"observability": observability},
    )

    payload = service.run(prompt="show patient ages in canonical_v1", operation="query")

    assert payload["status"] == "success"
    assert payload["summary"] == "Found stored-schema patients."
    executed_queries = [
        str((call["execution_context"].get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        for call in gateway.raw_calls
    ]
    assert executed_queries == ['SELECT "PatientKey", "Age" FROM "flathr"."Patient" LIMIT 10']
    prompt = llm.prompts[0]
    assert "Autodetected relation_foreign_scheme:" in prompt
    assert "Parameter context guidance" in prompt
    assert "Patient.PatientKey semantically identifies patients" in prompt
    context_events = [
        event for event in sink.events if event.event_type == "sql.context.loaded"
    ]
    assert context_events
    assert context_events[0].details["schema_source"] == "parameter_store.schema_catalog"
    assert context_events[0].details["domain_context_present"] is True
    prompt_events = [event for event in sink.events if event.event_type == "sql.llm.prompt"]
    assert prompt_events
    assert prompt_events[0].details["prompt_contains_domain_context"] is True
    assert "Patient.PatientKey semantically identifies patients" in str(
        prompt_events[0].details["domain_context_summary"]
    )
    for secret in ("pass123", "jimSolomon@mednet.ucla.edu", "10.44.102.204"):
        assert secret not in prompt
        assert secret not in str([event.model_dump(mode="json") for event in sink.events])


def test_sql_validation_warns_for_inferred_secondary_join_without_fk_approval(
    tmp_path: Path,
) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    store.create(
        AgentParameterCreate(
            key="canonica_v1",
            value_json=_stored_schema_profile_value(),
            aliases=["CANONICAL_v1", "canonical_v1"],
            tags=["database", "postgresql", "discoverdb"],
            sensitive=True,
        )
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": (
                'SELECT p."PatientKey" FROM "flathr"."Patient" p '
                'JOIN "flathr"."RTRECORD" r ON p."PatientKey" = r."PatientKey" LIMIT 10'
            ),
            "assumptions": ["Using the inferred secondary key from stored metadata."],
            "confidence": 0.8,
        },
        {"summary": "Joined patients to RT records with a semantic warning.", "confidence": 0.8},
    )
    gateway = PatientRtrecordSqlGateway()
    service = SqlAgentService(
        parameter_store=store,
        gateway_client=gateway,
        llm_client=llm,
        memory_store=None,
        config=RuntimeConfig(workspace_root=str(tmp_path), sql_agent_max_repair_attempts=0),
        context={},
    )

    payload = service.run(prompt="join patients to RT records in canonical_v1", operation="query")

    assert payload["status"] == "success"
    assert payload["warnings"] == ["sql_join_relationships_not_db_approved"]
    safety = payload["attempts"][0]["safety_classification"]
    assert safety["reason"] == "single_read_only_statement"
    assert safety["join_relationship_warnings"][0]["reason"] == (
        "inferred_join_not_db_approved"
    )
    assert gateway.raw_calls


def test_sql_validation_silently_approves_db_foreign_key_join(
    tmp_path: Path,
) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    value_json = _stored_schema_profile_value()
    approved_join = value_json["schema_catalog"]["inferred_secondary_keys"][0]
    value_json["schema_catalog"]["approved_joins"] = [approved_join]
    value_json["schema_catalog"]["inferred_secondary_keys"] = []
    value_json["relation_foreign_scheme"]["approved_foreign_keys"] = [approved_join]
    value_json["relation_foreign_scheme"]["inferred_secondary_keys"] = []
    store.create(
        AgentParameterCreate(
            key="canonica_v1",
            value_json=value_json,
            aliases=["CANONICAL_v1", "canonical_v1"],
            tags=["database", "postgresql", "discoverdb"],
            sensitive=True,
        )
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": (
                'SELECT p."PatientKey" FROM "flathr"."Patient" p '
                'JOIN "flathr"."RTRECORD" r ON p."PatientKey" = r."PatientKey" LIMIT 10'
            ),
            "assumptions": ["Using the database-declared foreign key."],
            "confidence": 0.8,
        },
        {"summary": "Joined patients to RT records.", "confidence": 0.8},
    )
    sink = InMemoryEventSink()
    observability = ObservabilityContext(
        request_id="req-sql-approved-fk",
        enabled=True,
        debug=True,
        sinks=[sink],
    )
    gateway = PatientRtrecordSqlGateway()
    service = SqlAgentService(
        parameter_store=store,
        gateway_client=gateway,
        llm_client=llm,
        memory_store=None,
        config=RuntimeConfig(workspace_root=str(tmp_path), sql_agent_max_repair_attempts=0),
        context={"observability": observability},
    )

    payload = service.run(prompt="join patients to RT records in canonical_v1", operation="query")

    assert payload["status"] == "success"
    assert payload["warnings"] == []
    safety = payload["attempts"][0]["safety_classification"]
    assert safety["join_relationship_warnings"] == []
    assert not [
        event for event in sink.events if event.event_type == "sql.validation.join_warning"
    ]
    assert gateway.raw_calls


def test_sql_validation_warns_for_user_note_join_without_fk_approval(
    tmp_path: Path,
) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    value_json = _stored_schema_profile_value()
    value_json["schema_catalog"]["inferred_secondary_keys"] = []
    value_json["relation_foreign_scheme"]["inferred_secondary_keys"] = []
    store.create(
        AgentParameterCreate(
            key="canonica_v1",
            value_json=value_json,
            context_json=_stored_schema_profile_context(
                "Patient semantically relates to RTRECORD through PatientKey."
            ),
            aliases=["CANONICAL_v1", "canonical_v1"],
            tags=["database", "postgresql", "discoverdb"],
            sensitive=True,
        )
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": (
                'SELECT p."PatientKey" FROM "flathr"."Patient" p '
                'JOIN "flathr"."RTRECORD" r ON p."PatientKey" = r."PatientKey" LIMIT 10'
            ),
            "assumptions": ["Using the user-provided semantic relationship note."],
            "confidence": 0.8,
        },
        {"summary": "Joined patients to RT records with a semantic warning.", "confidence": 0.8},
    )
    sink = InMemoryEventSink()
    observability = ObservabilityContext(
        request_id="req-sql-user-note-warning",
        enabled=True,
        debug=True,
        sinks=[sink],
    )
    gateway = PatientRtrecordSqlGateway()
    service = SqlAgentService(
        parameter_store=store,
        gateway_client=gateway,
        llm_client=llm,
        memory_store=None,
        config=RuntimeConfig(workspace_root=str(tmp_path), sql_agent_max_repair_attempts=0),
        context={"observability": observability},
    )

    payload = service.run(prompt="join patients to RT records in canonical_v1", operation="query")

    assert payload["status"] == "success"
    assert payload["warnings"] == ["sql_join_relationships_not_db_approved"]
    safety = payload["attempts"][0]["safety_classification"]
    assert safety["reason"] == "single_read_only_statement"
    assert safety["join_relationship_warnings"][0]["reason"] == "join_not_db_approved"
    warning_events = [
        event for event in sink.events if event.event_type == "sql.validation.join_warning"
    ]
    assert warning_events
    assert warning_events[0].details["join_relationship_warnings"][0]["reason"] == (
        "join_not_db_approved"
    )
    assert gateway.raw_calls


def test_sql_nlp_query_generates_executes_and_summarizes_without_prompt_leaks(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": "SELECT patient_id, name FROM canonical.patients ORDER BY patient_id LIMIT 100",
            "assumptions": ["Using canonical.patients as the patient table."],
            "confidence": 0.9,
        },
        {"summary": "Found 1 patient.", "confidence": 0.9},
    )
    store = _create_store(tmp_path)
    service, gateway = _service(tmp_path, store=store, llm=llm)

    payload = service.run(prompt="show patients in canonical_v1", operation="query")

    assert payload["status"] == "success"
    assert payload["summary"] == "Found 1 patient."
    assert payload["rows"] == [{"patient_id": "1", "name": "Ada"}]
    assert payload["generated_sql"] == (
        "SELECT patient_id, name FROM canonical.patients ORDER BY patient_id LIMIT 100"
    )
    assert payload["executed_sql"] == payload["generated_sql"]
    assert payload["attempts"][0]["action"] == "execute_sql"
    assert payload["safety_classification"]["classification"] == "read_only"
    assert len(gateway.raw_calls) == 7
    prompts = "\n".join(llm.prompts)
    response_text = str(payload)
    for secret in ("pass123", "jimSolomon@mednet.ucla.edu", "10.44.102.204"):
        assert secret not in prompts
        assert secret not in response_text


def test_sql_prompt_guides_bucket_queries_and_append_strategy(tmp_path: Path) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": (
                "WITH requested(age) AS (SELECT generate_series(1, 100)) "
                "SELECT requested.age, COUNT(p.\"PatientBirthDate\") AS patient_count "
                "FROM requested LEFT JOIN flathr.Patient p ON false "
                "GROUP BY requested.age ORDER BY requested.age LIMIT 100"
            ),
            "assumptions": [],
            "confidence": 0.9,
        },
        _sql_contract_accept(),
        {"summary": "Returned age buckets.", "confidence": 0.9},
    )
    service, _gateway = _service(tmp_path, gateway=AgeBucketSqlGateway(), llm=llm)

    payload = service.run(
        prompt="in canonical_v1 count patients at ages 1 through 100",
        operation="query",
    )

    assert payload["status"] == "success"
    prompt = llm.prompts[llm.schemas.index("SqlAgentAction")]
    assert "result_strategy to append_rows" in prompt
    assert "generate_series or VALUES plus LEFT JOIN" in prompt
    assert "including zero-count buckets" in prompt
    assert "Never write SELECT generate_series" in prompt
    assert "standalone generate_series or VALUES bucket list" in prompt
    assert "Each append_rows step must be a complete answer row" in prompt
    assert "prefer CTEs using WITH" in prompt
    assert "SELECT INTO TEMP" in prompt
    assert "birth-date or date-of-birth style column" in prompt
    assert "Do not use CAST('now' AS DATE)" in prompt


def test_sql_age_bucket_query_allows_generate_series_and_zero_counts(
    tmp_path: Path,
) -> None:
    age_bucket_sql = (
        "WITH requested(age) AS ("
        "SELECT generate_series(1, 100)"
        "), patient_ages AS ("
        "SELECT EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int AS age "
        "FROM flathr.Patient WHERE PatientBirthDate IS NOT NULL"
        ") "
        "SELECT requested.age, COUNT(patient_ages.age) AS patient_count "
        "FROM requested LEFT JOIN patient_ages ON patient_ages.age = requested.age "
        "GROUP BY requested.age ORDER BY requested.age LIMIT 100"
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": age_bucket_sql,
            "assumptions": ["Ages with no patients should appear with count 0."],
            "confidence": 0.93,
        },
        _sql_contract_accept("The result has age buckets and patient counts."),
        {"summary": "Returned 100 requested age buckets.", "confidence": 0.9},
    )
    service, _gateway = _service(tmp_path, gateway=AgeBucketSqlGateway(), llm=llm)

    payload = service.run(
        prompt="number of patients at ages 1, 2, 3 all the way up to 100",
        parameter_key="canonica_v1",
        operation="query",
    )

    assert payload["status"] == "success"
    assert payload["summary"] == "Returned 100 requested age buckets."
    assert payload["rows"] == [
        {"age": "1", "patient_count": "0"},
        {"age": "2", "patient_count": "3"},
        {"age": "3", "patient_count": "0"},
    ]
    assert payload["row_count"] == 3
    assert "GENERATE_SERIES" in payload["executed_sql"].upper()
    assert '"PatientBirthDate"' in payload["executed_sql"]


def test_sql_rejects_join_without_from_source_and_retries(
    tmp_path: Path,
) -> None:
    malformed_sql = (
        "SELECT generate_series(1, 100) AS age "
        "LEFT JOIN ("
        "SELECT EXTRACT(YEAR FROM AGE(CURRENT_DATE, PatientBirthDate)) AS age, "
        "COUNT(*) AS patient_count FROM flathr.Patient "
        "GROUP BY EXTRACT(YEAR FROM AGE(CURRENT_DATE, PatientBirthDate))"
        ") AS counts ON generate_series(1, 100) = counts.age ORDER BY age"
    )
    good_sql = (
        "WITH requested(age) AS (SELECT generate_series(1, 100)), patient_ages AS ("
        "SELECT EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int AS age "
        "FROM flathr.Patient WHERE PatientBirthDate IS NOT NULL"
        ") "
        "SELECT requested.age, COUNT(patient_ages.age) AS patient_count "
        "FROM requested LEFT JOIN patient_ages ON patient_ages.age = requested.age "
        "GROUP BY requested.age ORDER BY requested.age LIMIT 100"
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": malformed_sql,
            "assumptions": [],
            "confidence": 0.8,
        },
        {
            "action": "execute_sql",
            "sql": good_sql,
            "assumptions": ["Use a CTE as the generated bucket source before joining."],
            "confidence": 0.93,
        },
        _sql_contract_accept("The repaired SQL has the requested bucket and count columns."),
        {"summary": "Returned repaired age counts.", "confidence": 0.9},
    )
    service, gateway = _service(
        tmp_path,
        gateway=AgeBucketSqlGateway(),
        llm=llm,
        context={"agent_clarification_mode": "pedantic"},
    )

    payload = service.run(
        prompt="count patients at ages 1 through 100 in canonical_v1",
        parameter_key="canonica_v1",
        operation="query",
    )

    assert payload["status"] == "success"
    assert "llm_sql_retry_after_validation" in payload["warnings"]
    assert any(
        "join_without_from_source" in str(attempt.get("validation_error") or "")
        for attempt in payload["attempts"]
    )
    assert payload["columns"] == ["age", "patient_count"]
    assert not any(
        "SELECT generate_series(1, 100) AS age LEFT JOIN"
        in str((call.get("execution_context") or {}).get("shell_env", {}).get("OF_SQL_QUERY") or "")
        for call in gateway.raw_calls
    )


def test_sql_rejects_bucket_series_without_requested_metric_and_retries(
    tmp_path: Path,
) -> None:
    good_sql = (
        "WITH requested(age) AS (SELECT generate_series(1, 100)), patient_ages AS ("
        "SELECT EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int AS age "
        "FROM flathr.Patient WHERE PatientBirthDate IS NOT NULL"
        ") "
        "SELECT requested.age, COUNT(patient_ages.age) AS patient_count "
        "FROM requested LEFT JOIN patient_ages ON patient_ages.age = requested.age "
        "GROUP BY requested.age ORDER BY requested.age LIMIT 100"
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "result_strategy": "append_rows",
            "sql_steps": ["SELECT generate_series(1, 100) AS age LIMIT 100"],
            "assumptions": [],
            "confidence": 0.8,
        },
        _sql_contract_retry(),
        {
            "action": "execute_sql",
            "sql": good_sql,
            "assumptions": ["Use PatientBirthDate to count patients in each age bucket."],
            "confidence": 0.93,
        },
        _sql_contract_accept("The result has the requested bucket and count columns."),
        {"summary": "Returned repaired patient age counts.", "confidence": 0.9},
    )
    service, _gateway = _service(tmp_path, gateway=AgeBucketSqlGateway(), llm=llm)

    payload = service.run(
        prompt=(
            "number of patients at ages 1, 2, 3, 4, 5, 6, 7, "
            "all the way upto 100"
        ),
        parameter_key="canonica_v1",
        operation="query",
    )

    assert payload["status"] == "success"
    assert "llm_sql_retry_after_result_shape" in payload["warnings"]
    assert any(
        "sql_result_contract_review_retry"
        in str(attempt.get("validation_error") or "")
        for attempt in payload["attempts"]
    )
    assert payload["columns"] == ["age", "patient_count"]
    assert payload["rows"][1] == {"age": "2", "patient_count": "3"}


def test_sql_retries_temp_table_materialization_as_cte_when_not_requested(
    tmp_path: Path,
) -> None:
    good_sql = (
        "WITH requested(age) AS (SELECT generate_series(1, 100)), patient_ages AS ("
        "SELECT EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int AS age "
        "FROM flathr.Patient WHERE PatientBirthDate IS NOT NULL"
        ") "
        "SELECT requested.age, COUNT(patient_ages.age) AS patient_count "
        "FROM requested LEFT JOIN patient_ages ON patient_ages.age = requested.age "
        "GROUP BY requested.age ORDER BY requested.age LIMIT 100"
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "result_strategy": "append_rows",
            "sql_steps": [
                "SELECT generate_series(1, 100) AS age INTO TEMPORARY TABLE age_buckets",
                (
                    "SELECT ab.age, COUNT(p.PatientID) AS patient_count "
                    "FROM age_buckets ab LEFT JOIN flathr.Patient p "
                    "ON ab.age = EXTRACT(YEAR FROM AGE(CURRENT_DATE, p.PatientBirthDate)) "
                    "GROUP BY ab.age ORDER BY ab.age"
                ),
            ],
            "assumptions": [],
            "confidence": 0.8,
        },
        {
            "action": "execute_sql",
            "sql": good_sql,
            "assumptions": ["Use a CTE instead of a temporary intermediate table."],
            "confidence": 0.93,
        },
        _sql_contract_accept("The CTE result has the requested bucket and count columns."),
        {"summary": "Returned repaired age counts.", "confidence": 0.9},
    )
    service, gateway = _service(tmp_path, gateway=AgeBucketSqlGateway(), llm=llm)

    payload = service.run(
        prompt=(
            "number of patients at ages 1, 2, 3, 4, 5, 6, 7, "
            "all the way upto 100"
        ),
        parameter_key="canonica_v1",
        operation="query",
    )

    assert payload["status"] == "success"
    assert "llm_sql_retry_after_validation" in payload["warnings"]
    assert any(
        "cte_preferred_over_temp_table_materialization"
        in str(attempt.get("validation_error") or "")
        for attempt in payload["attempts"]
    )
    assert payload["columns"] == ["age", "patient_count"]
    assert not any(
        "TEMPORARY TABLE" in str(
            (call.get("execution_context") or {}).get("shell_env", {}).get("OF_SQL_QUERY")
            or ""
        )
        for call in gateway.raw_calls
    )


def test_sql_append_rows_strategy_combines_compatible_step_results(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "result_strategy": "append_rows",
            "sql_steps": [
                (
                    "SELECT 1 AS age, COUNT(*) AS patient_count FROM flathr.Patient "
                    "WHERE EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int = 1"
                ),
                (
                    "SELECT 2 AS age, COUNT(*) AS patient_count FROM flathr.Patient "
                    "WHERE EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int = 2"
                ),
            ],
            "assumptions": ["Independent age buckets can be appended."],
            "confidence": 0.9,
        },
        _sql_contract_accept("The appended rows are complete answer rows."),
        {"summary": "Returned two age buckets.", "confidence": 0.9},
    )
    service, _gateway = _service(tmp_path, gateway=AgeBucketSqlGateway(), llm=llm)

    payload = service.run(
        prompt="make multiple sql queries to count patients age 1 and age 2",
        parameter_key="canonica_v1",
        operation="query",
    )

    assert payload["status"] == "success"
    assert payload["columns"] == ["age", "patient_count"]
    assert payload["rows"] == [
        {"age": "1", "patient_count": "0"},
        {"age": "2", "patient_count": "3"},
    ]
    assert payload["row_count"] == 2
    assert payload["attempts"][0]["result_strategy"] == "append_rows"
    assert payload["attempts"][0]["step"] == 1
    assert payload["attempts"][1]["step"] == 2
    assert "\n\n" in payload["executed_sql"]
    rendered = render_sql_agent_response(payload)
    assert "| age | patient_count |" in rendered
    assert "| 2 | 3 |" in rendered
    assert "Generated SQL:" in rendered
    assert "Executed SQL:" in rendered


def test_sql_append_rows_rejects_incompatible_columns_and_retries(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "result_strategy": "append_rows",
            "sql_steps": [
                (
                    "SELECT 1 AS age, COUNT(*) AS patient_count FROM flathr.Patient "
                    "WHERE EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int = 1"
                ),
                (
                    "SELECT 2 AS bucket, COUNT(*) AS patient_count FROM flathr.Patient "
                    "WHERE EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int = 2"
                ),
            ],
            "assumptions": [],
            "confidence": 0.75,
        },
        {
            "action": "execute_sql",
            "result_strategy": "append_rows",
            "sql_steps": [
                (
                    "SELECT 1 AS age, COUNT(*) AS patient_count FROM flathr.Patient "
                    "WHERE EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int = 1"
                ),
                (
                    "SELECT 2 AS age, COUNT(*) AS patient_count FROM flathr.Patient "
                    "WHERE EXTRACT(YEAR FROM AGE(CURRENT_DATE, CAST(PatientBirthDate AS date)))::int = 2"
                ),
            ],
            "assumptions": ["Use the same result columns for every decomposed step."],
            "confidence": 0.9,
        },
        _sql_contract_accept("The repaired appended rows have compatible answer columns."),
        {"summary": "Returned repaired age buckets.", "confidence": 0.9},
    )
    service, _gateway = _service(tmp_path, gateway=AgeBucketSqlGateway(), llm=llm)

    payload = service.run(
        prompt="make multiple sql queries to count patients age 1 and age 2",
        parameter_key="canonica_v1",
        operation="query",
    )

    assert payload["status"] == "success"
    assert "llm_sql_retry_after_validation" in payload["warnings"]
    assert any(
        "incompatible_multi_step_result_columns" in str(attempt.get("validation_error") or "")
        for attempt in payload["attempts"]
    )
    assert payload["rows"] == [
        {"age": "1", "patient_count": "0"},
        {"age": "2", "patient_count": "3"},
    ]


def test_sql_loop_rejects_missing_limit_and_retries(tmp_path: Path) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": "SELECT patient_id, name FROM canonical.patients ORDER BY patient_id",
            "assumptions": [],
            "confidence": 0.7,
        },
        {
            "action": "execute_sql",
            "sql": "SELECT patient_id, name FROM canonical.patients ORDER BY patient_id LIMIT 10",
            "assumptions": ["Added the runtime-required limit."],
            "confidence": 0.9,
        },
        {"summary": "Found 1 patient after adding a limit.", "confidence": 0.9},
    )
    service, gateway = _service(tmp_path, llm=llm)

    payload = service.run(prompt="show patients in canonical_v1", operation="query")

    assert payload["status"] == "success"
    assert payload["warnings"] == ["llm_sql_retry_after_validation"]
    assert payload["attempts"][0]["validation_error"].startswith("Read-only row-returning SQL")
    assert payload["executed_sql"].endswith("LIMIT 10")
    assert len(gateway.raw_calls) == 7


def test_sql_gateway_execution_emits_command_capsule_events(tmp_path: Path) -> None:
    sink = InMemoryEventSink()
    observability = ObservabilityContext(
        request_id="req_sql_capsule",
        enabled=True,
        debug=True,
        sinks=[sink],
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": "SELECT patient_id, name FROM canonical.patients ORDER BY patient_id LIMIT 25",
            "assumptions": [],
            "confidence": 0.9,
        },
        {"summary": "Found 1 patient.", "confidence": 0.9},
    )
    service, _gateway = _service(
        tmp_path,
        llm=llm,
        context={
            "observability": observability,
            "gateway_id": "gw-sql",
            "gateway_node": "worker-a",
            "gateway_url": "http://worker-a:8787",
        },
    )

    payload = service.run(prompt="show patients in canonical_v1", operation="query")

    assert payload["status"] == "success"
    events = [
        event
        for event in sink.events
        if str(event.event_type).startswith("execution.command.")
    ]
    assert {event.event_type for event in events} >= {
        "execution.command.started",
        "execution.command.stdout",
        "execution.command.completed",
    }
    query_events = [
        event
        for event in events
        if "canonical" in str((event.details or {}).get("command") or "")
    ]
    assert query_events
    query_started = next(
        event for event in query_events if event.event_type == "execution.command.started"
    )
    details = query_started.details
    assert details["command_kind"] == "sql"
    assert details["language"] == "sql"
    assert details["db_profile"] == "canonica_v1"
    assert details["gateway_node"] == "worker-a"
    assert details["gateway_url"] == "http://worker-a:8787"
    assert "psql" not in details["command"]
    assert details["generated_sql"] == (
        "SELECT patient_id, name FROM canonical.patients ORDER BY patient_id LIMIT 25"
    )
    assert details["executed_sql"] == details["generated_sql"]
    assert any(event.event_type == "sql.llm.generated" for event in sink.events)
    rendered = str([event.model_dump(mode="json") for event in events])
    for secret in ("pass123", "jimSolomon@mednet.ucla.edu", "10.44.102.204"):
        assert secret not in rendered


def test_sql_discovery_lists_tables_for_table_requests(tmp_path: Path) -> None:
    service, _gateway = _service(tmp_path)

    payload = service.run(prompt="list all tables in canonical_v1", operation="query")

    assert payload["status"] == "success"
    assert payload["summary"] == "Found 1 table(s)."
    assert payload["columns"] == ["schema", "table", "column_count"]
    assert payload["rows"] == [{"schema": "canonical", "table": "patients", "column_count": 2}]


def test_sql_execution_quotes_schema_identifiers_without_hiding_generated_sql(
    tmp_path: Path,
) -> None:
    sink = InMemoryEventSink()
    observability = ObservabilityContext(
        request_id="req_sql_mixed_case",
        enabled=True,
        debug=True,
        sinks=[sink],
    )
    generated_sql = (
        "SELECT COUNT(*) FROM flathr.Patient "
        "WHERE EXTRACT(YEAR FROM AGE(CURRENT_DATE, PatientBirthDate)) > 30;"
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": generated_sql,
            "assumptions": [],
            "confidence": 0.95,
        },
        {"summary": "There are 42 patients.", "confidence": 0.9},
    )
    service, gateway = _service(
        tmp_path,
        gateway=MixedCaseSqlGateway(),
        llm=llm,
        context={"observability": observability},
    )

    payload = service.run(
        prompt="how many patients are more than 30 years of age in canonical_v1?",
        operation="query",
    )

    assert payload["status"] == "success"
    assert payload["rows"] == [{"count": "42"}]
    assert payload["generated_sql"] == generated_sql
    assert payload["executed_sql"] != generated_sql
    assert 'flathr."Patient"' in payload["executed_sql"]
    assert '"PatientBirthDate"' in payload["executed_sql"]
    assert payload["safety_classification"]["identifier_quoting_applied"] is True
    assert "SqlAgentAction" in llm.schemas
    executed_sql = gateway.raw_calls[-1]["execution_context"]["shell_env"]["OF_SQL_QUERY"]
    assert executed_sql == payload["executed_sql"]
    started = next(
        event
        for event in sink.events
        if event.event_type == "execution.command.started"
        and 'flathr."Patient"' in str((event.details or {}).get("command") or "")
    )
    assert started.details["generated_sql"] == generated_sql
    assert started.details["executed_sql"] == payload["executed_sql"]


def test_sql_execution_canonicalizes_quoted_schema_identifier_case(
    tmp_path: Path,
) -> None:
    generated_sql = (
        'SELECT COUNT(*) FROM "flathr"."PATIENT" '
        'WHERE "PATIENTBIRTHDATE" IS NOT NULL'
    )
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": generated_sql,
            "assumptions": [],
            "confidence": 0.95,
        },
        {"summary": "There are 42 patients.", "confidence": 0.9},
    )
    service, gateway = _service(
        tmp_path,
        gateway=MixedCaseSqlGateway(),
        llm=llm,
    )

    payload = service.run(
        prompt="how many patients have a birth date in canonical_v1?",
        operation="query",
    )

    assert payload["status"] == "success"
    assert payload["generated_sql"] == generated_sql
    assert '"flathr"."Patient"' in payload["executed_sql"]
    assert '"PatientBirthDate"' in payload["executed_sql"]
    assert '"PATIENT"' not in payload["executed_sql"]
    assert '"PATIENTBIRTHDATE"' not in payload["executed_sql"]
    executed_sql = gateway.raw_calls[-1]["execution_context"]["shell_env"]["OF_SQL_QUERY"]
    assert executed_sql == payload["executed_sql"]


def test_sql_loop_does_not_run_entity_ranking_or_auto_selection(
    tmp_path: Path,
) -> None:
    context: dict[str, Any] = {}
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": 'SELECT COUNT(*) AS "count" FROM "flathr"."RTRecord"',
            "assumptions": ["The SQL LLM chose flathr.RTRecord from schema context."],
            "confidence": 0.92,
        },
        {"summary": "There are 123 RT records.", "confidence": 0.9},
    )
    service, gateway = _service(
        tmp_path,
        gateway=EntitySqlGateway(),
        llm=llm,
        context=context,
    )

    payload = service.run(
        prompt="in canoinical how many rtrecords do we have",
        operation="query",
    )

    assert payload["status"] == "success"
    assert payload["parameter_key"] == "canonica_v1"
    assert payload["rows"] == [{"count": "123"}]
    assert payload["executed_sql"] == 'SELECT COUNT(*) AS "count" FROM "flathr"."RTRecord"'
    assert "SqlAgentAction" in llm.schemas
    assert "sqlite3" not in "\n".join(call["command"] for call in gateway.raw_calls)
    cache = context["sql_schema_cache"]["canonica_v1"]
    rt_record = next(table for table in cache["tables"] if table["table"] == "RTRecord")
    assert "row_count" not in rt_record
    assert "sample_rows" not in rt_record


def test_sql_filtered_count_is_authored_only_by_llm(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": 'SELECT COUNT(*) AS "count" FROM "flathr"."Patient" WHERE "Age" > 45',
            "assumptions": ["Age is interpreted using flathr.Patient.Age."],
            "confidence": 0.91,
        },
        {"summary": "There are 9 patients over age 45.", "confidence": 0.9},
    )
    service, gateway = _service(tmp_path, gateway=PatientAgeSqlGateway(), llm=llm)

    payload = service.run(
        prompt="in canonical list the count of patients over age 45",
        operation="query",
    )

    assert payload["status"] == "success"
    assert payload["rows"] == [{"count": "9"}]
    assert 'WHERE "Age" > 45' in payload["executed_sql"]
    assert "SqlAgentAction" in llm.schemas
    executed_sql = gateway.raw_calls[-1]["execution_context"]["shell_env"]["OF_SQL_QUERY"]
    assert 'WHERE "Age" > 45' in executed_sql


def test_sql_loop_retries_with_schema_after_validation_error(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": (
                'SELECT COUNT(*) AS "count" FROM ('
                'SELECT "PatientID" FROM "flathr"."RTRECORD" '
                'GROUP BY "PatientID" HAVING COUNT(*) > 100'
                ") AS patient_counts"
            ),
            "assumptions": ["RT records are grouped by patient."],
            "confidence": 0.82,
        },
        {
            "action": "execute_sql",
            "sql": (
                'SELECT COUNT(*) AS "count" FROM ('
                'SELECT "PatientKey" FROM "flathr"."RTRECORD" '
                'GROUP BY "PatientKey" HAVING COUNT(*) > 100'
                ") AS patient_counts"
            ),
            "assumptions": ["PatientKey is the patient key present on RTRECORD."],
            "confidence": 0.9,
        },
        {"summary": "There are 4 patients with more than 100 RT records.", "confidence": 0.9},
    )
    service, gateway = _service(tmp_path, gateway=RelationshipSqlGateway(), llm=llm)

    payload = service.run(
        prompt="In canocnial how many patients have more than 100 RTRECORDS",
        operation="query",
    )

    assert payload["status"] == "success"
    assert payload["rows"] == [{"count": "4"}]
    assert payload["warnings"] == ["llm_sql_retry_after_validation"]
    assert "SqlEntityChoice" not in llm.schemas
    assert llm.schemas[:2] == ["SqlAgentAction", "SqlAgentAction"]
    assert "Prior attempts" in llm.prompts[1]
    assert "PatientID" in llm.prompts[1]
    assert "RTRECORD" in llm.prompts[0]
    assert "Complete discovered schema, all tables and columns:" in llm.prompts[0]
    executed_sql = gateway.raw_calls[-1]["execution_context"]["shell_env"]["OF_SQL_QUERY"]
    assert '"PatientKey"' in executed_sql
    assert '"PatientID"' not in executed_sql


@pytest.mark.skip(reason="obsolete deterministic SQL rerouting behavior removed")
def test_sql_chat_agentic_nlp_ignores_decomposition_sql_and_repairs_with_schema(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "prompt_type": "simple_tool_task",
            "requires_tools": True,
            "likely_domains": ["sql"],
            "risk_level": "low",
            "needs_clarification": False,
            "clarification_question": None,
            "reason": "Database count request.",
            "confidence": 0.95,
            "assumptions": [],
            "domain_evaluations": [
                {
                    "domain": "sql",
                    "fits": True,
                    "confidence": 0.95,
                    "reason": "The request asks for a SQL count.",
                }
            ],
        },
        {
            "tasks": [
                {
                    "id": "task_1",
                    "description": "Count patients with more than 100 RTRECORDS",
                    "semantic_verb": "count",
                    "object_type": "sql",
                    "intent_confidence": 0.95,
                    "constraints": {
                        "sql.query": (
                            "SELECT COUNT(DISTINCT PatientID) FROM flathr.RTRECORD "
                            "GROUP BY PatientID HAVING COUNT(*) > 100"
                        )
                    },
                    "dependencies": [],
                    "raw_evidence": "SQL count request",
                    "requires_confirmation": False,
                    "risk_level": "low",
                }
            ],
            "global_constraints": {},
            "unresolved_references": [],
            "assumptions": [],
            "confidence": 0.95,
        },
        {
            "missing_user_intents": [],
            "hallucinated_tasks": [],
            "dependency_warnings": [],
            "unsafe_task_warnings": [],
            "unresolved_references": [],
            "recommended_repair": None,
            "confidence": 0.95,
        },
        {
            "assignments": [
                {
                    "task_id": "task_1",
                    "semantic_verb": "count",
                    "object_type": "sql.query",
                    "intent_confidence": 0.95,
                    "risk_level": "low",
                    "requires_confirmation": False,
                }
            ]
        },
        {
            "task_id": "task_1",
            "evaluations": [
                {
                    "capability_id": "sql.execute_readonly",
                    "operation_id": "execute_readonly",
                    "fits": True,
                    "confidence": 0.95,
                    "reason": "The decomposition supplied a SQL query.",
                    "domain_reason": "SQL domain.",
                    "object_type_reason": "SQL query object.",
                    "argument_reason": "The task has a SQL string.",
                    "risk_reason": "Read-only SELECT.",
                    "missing_arguments_likely": [],
                    "better_than_other_candidates": True,
                },
                {
                    "capability_id": "sql.query",
                    "operation_id": "query",
                    "fits": False,
                    "confidence": 0.85,
                    "reason": "Less direct than executing supplied SQL.",
                    "domain_reason": "SQL domain.",
                    "object_type_reason": "SQL query object.",
                    "argument_reason": "Requires a prompt.",
                    "risk_reason": "Read-only SELECT.",
                    "missing_arguments_likely": [],
                    "better_than_other_candidates": False,
                },
            ],
            "unresolved_reason": None,
        },
        {
            "task_id": "task_1",
            "candidate_capability_id": "sql.query",
            "candidate_operation_id": "query",
            "fits": True,
            "confidence": 0.95,
            "primary_failure_mode": None,
            "semantic_reason": "The SQL agent should plan from the NLP request.",
            "domain_reason": "SQL domain matches.",
            "object_type_reason": "SQL query object matches.",
            "argument_reason": "A natural-language prompt can be extracted.",
            "risk_reason": "Read-only capability.",
            "better_capability_id": None,
            "missing_capability_description": None,
            "suggested_domain": "sql",
            "suggested_object_type": "sql.query",
            "missing_arguments_likely": [],
            "requires_clarification": False,
            "clarification_question": None,
        },
        {
            "task_id": "task_1",
            "capability_id": "sql.query",
            "operation_id": "query",
            "arguments": {
                "sql": (
                    "SELECT COUNT(DISTINCT PatientID) FROM flathr.RTRECORD "
                    "GROUP BY PatientID HAVING COUNT(*) > 100"
                )
            },
            "generated_arguments": [],
            "missing_required_arguments": [],
            "assumptions": [],
            "confidence": 1.0,
        },
        {
            "intent": "query",
            "sql": (
                'SELECT COUNT(*) AS "count" FROM ('
                "SELECT PatientID FROM flathr.RTRECORD "
                "GROUP BY PatientID HAVING COUNT(*) > 100"
                ") AS patient_counts"
            ),
            "tables": ["flathr.RTRECORD"],
            "assumptions": ["RT records are grouped by patient."],
            "needs_discovery": False,
            "confidence": 0.82,
        },
        {
            "intent": "query",
            "sql": (
                'SELECT COUNT(*) AS "count" FROM ('
                "SELECT PatientKey FROM flathr.RTRECORD "
                "GROUP BY PatientKey HAVING COUNT(*) > 100"
                ") AS patient_counts"
            ),
            "tables": ["flathr.RTRECORD"],
            "assumptions": ["PatientKey is the patient key present on RTRECORD."],
            "needs_discovery": False,
            "confidence": 0.9,
        },
        {"summary": "There are 4 patients with more than 100 RT records.", "confidence": 0.9},
    )
    runtime, gateway, _llm = _runtime(
        tmp_path,
        gateway=RelationshipSqlGateway(),
        llm=llm,
    )

    response = runtime.handle_request(
        "In canocnial how many patients have more than 100 RTRECORDS",
        {
            "agent_mode": "llm_operator",
            "workspace_root": str(tmp_path),
            "llm_operator_max_completion_repair_attempts": 0,
        },
    )

    assert "| 4 |" in response
    trace = runtime.last_planning_trace
    assert trace is not None
    assert trace.metadata["sql_agentic_stripped_generated_sql_constraints"] == 1
    assert trace.metadata["sql_agentic_forced_query_capability_task_ids"] == ["task_1"]
    dag = trace.validated_dag or {}
    node = dag["nodes"][0]
    assert node["capability_id"] == "sql.query"
    assert "sql" not in node["arguments"]
    assert "prompt" in node["arguments"]
    assert any(
        "Complete discovered schema, all tables and columns:" in prompt
        and '"truncated": false' in prompt
        for prompt in llm.prompts
    )
    executed_sql = gateway.raw_calls[-1]["execution_context"]["shell_env"]["OF_SQL_QUERY"]
    assert '"PatientKey"' in executed_sql
    assert '"PatientID"' not in executed_sql


@pytest.mark.skip(reason="obsolete deterministic SQL decomposition normalization removed")
def test_sql_agentic_decomposition_collapses_database_predicates_to_one_query(
    tmp_path: Path,
) -> None:
    prompt = "In canonical, how many patients over age 60 have more than 50 RTRECORDS?"
    llm = QueueLLM(
        {
            "prompt_type": "simple_tool_task",
            "requires_tools": True,
            "likely_domains": ["sql"],
            "risk_level": "low",
            "needs_clarification": False,
            "clarification_question": None,
            "reason": "Database filter and count request.",
            "confidence": 0.95,
            "assumptions": [],
            "domain_evaluations": [
                {
                    "domain": "sql",
                    "fits": True,
                    "confidence": 0.95,
                    "reason": "The request asks for SQL database predicates.",
                }
            ],
        },
        {
            "tasks": [
                {
                    "id": "task_1",
                    "description": "Identify patients above the age of 60",
                    "semantic_verb": "filter",
                    "object_type": "operator",
                    "intent_confidence": 0.95,
                    "constraints": {"age": {"operator": ">", "value": 60}},
                    "dependencies": [],
                    "raw_evidence": "age predicate",
                    "requires_confirmation": False,
                    "risk_level": "low",
                },
                {
                    "id": "task_2",
                    "description": "Count the number of RTRECORDS per patient",
                    "semantic_verb": "count",
                    "object_type": "sql",
                    "intent_confidence": 0.9,
                    "constraints": {
                        "table": "flathr.RTRECORD",
                        "group_by": "SOPInstanceUID",
                    },
                    "dependencies": [],
                    "raw_evidence": "record count predicate",
                    "requires_confirmation": False,
                    "risk_level": "low",
                },
                {
                    "id": "task_3",
                    "description": "Filter patients with more than 50 RTRECORDS",
                    "semantic_verb": "filter",
                    "object_type": "operator",
                    "intent_confidence": 0.85,
                    "constraints": {"record_count": {"operator": ">", "value": 50}},
                    "dependencies": [],
                    "raw_evidence": "record threshold predicate",
                    "requires_confirmation": False,
                    "risk_level": "low",
                },
            ],
            "global_constraints": {},
            "unresolved_references": [],
            "assumptions": [],
            "confidence": 0.9,
        },
        {
            "missing_user_intents": [],
            "hallucinated_tasks": [],
            "dependency_warnings": [],
            "unsafe_task_warnings": [],
            "unresolved_references": [],
            "recommended_repair": None,
            "confidence": 0.95,
        },
        {
            "assignments": [
                {
                    "task_id": "task_1",
                    "semantic_verb": "filter",
                    "object_type": "sql.database",
                    "intent_confidence": 0.95,
                    "risk_level": "low",
                    "requires_confirmation": False,
                }
            ]
        },
        {
            "task_id": "task_1",
            "evaluations": [
                {
                    "capability_id": "operator.python_transform",
                    "operation_id": "python_transform",
                    "fits": True,
                    "confidence": 0.95,
                    "reason": "Incorrectly tries to filter database rows in Python.",
                    "domain_reason": "Operator transform can filter data.",
                    "object_type_reason": "The task says filter.",
                    "argument_reason": "Could write Python code.",
                    "risk_reason": "Medium risk.",
                    "missing_arguments_likely": [],
                    "better_than_other_candidates": True,
                },
                {
                    "capability_id": "sql.query",
                    "operation_id": "query",
                    "fits": True,
                    "confidence": 0.8,
                    "reason": "The SQL agent can answer the full database question.",
                    "domain_reason": "SQL domain.",
                    "object_type_reason": "SQL database task.",
                    "argument_reason": "Requires a natural-language prompt.",
                    "risk_reason": "Read-only SQL.",
                    "missing_arguments_likely": [],
                    "better_than_other_candidates": False,
                },
            ],
            "unresolved_reason": None,
        },
        {
            "task_id": "task_1",
            "candidate_capability_id": "sql.query",
            "candidate_operation_id": "query",
            "fits": True,
            "confidence": 0.95,
            "primary_failure_mode": None,
            "semantic_reason": "The SQL agent should plan from the full database request.",
            "domain_reason": "SQL domain matches.",
            "object_type_reason": "SQL database task matches.",
            "argument_reason": "A natural-language SQL prompt can be extracted.",
            "risk_reason": "Read-only SQL.",
            "better_capability_id": None,
            "missing_capability_description": None,
            "suggested_domain": "sql",
            "suggested_object_type": "sql.database",
            "missing_arguments_likely": [],
            "requires_clarification": False,
            "clarification_question": None,
        },
        {
            "task_id": "task_1",
            "capability_id": "sql.query",
            "operation_id": "query",
            "arguments": {"prompt": "Count RTRECORDS per patient"},
            "generated_arguments": [],
            "missing_required_arguments": [],
            "assumptions": [],
            "confidence": 0.95,
        },
        {
            "intent": "query",
            "sql": (
                'SELECT COUNT(*) AS "count" FROM ('
                "SELECT r.PatientKey "
                "FROM flathr.RTRECORD r "
                "JOIN flathr.Patient p ON p.PatientKey = r.PatientKey "
                "WHERE p.Age > 60 "
                "GROUP BY r.PatientKey HAVING COUNT(*) > 50"
                ") AS patient_counts"
            ),
            "tables": ["flathr.RTRECORD", "flathr.Patient"],
            "assumptions": ["PatientKey links RTRECORD to Patient."],
            "needs_discovery": False,
            "confidence": 0.95,
        },
        {
            "summary": "There are 3 patients over age 60 with more than 50 RT records.",
            "confidence": 0.9,
        },
    )
    runtime, gateway, _llm = _runtime(
        tmp_path,
        gateway=PatientRtrecordSqlGateway(),
        llm=llm,
    )

    response = runtime.handle_request(
        prompt,
        {
            "agent_mode": "llm_operator",
            "workspace_root": str(tmp_path),
            "llm_operator_max_completion_repair_attempts": 0,
        },
    )

    assert "| 3 |" in response
    trace = runtime.last_planning_trace
    assert trace is not None
    assert trace.metadata["sql_agentic_decomposition_normalized"][
        "collapsed_task_ids"
    ] == ["task_1", "task_2", "task_3"]
    dag = trace.validated_dag or {}
    assert len(dag["nodes"]) == 1
    node = dag["nodes"][0]
    assert node["capability_id"] == "sql.query"
    assert "operator.python_transform" not in str(dag)
    assert "patients over age 60" in node["arguments"]["prompt"]
    assert "more than 50 RTRECORDS" in node["arguments"]["prompt"]
    assert any(
        "patients over age 60" in prompt
        and "more than 50 RTRECORDS" in prompt
        and "Count RTRECORDS per patient" in prompt
        for prompt in llm.prompts
    )
    executed_sql = gateway.raw_calls[-1]["execution_context"]["shell_env"]["OF_SQL_QUERY"]
    assert '"Age" > 60' in executed_sql
    assert "HAVING COUNT(*) > 50" in executed_sql
    assert '"PatientKey"' in executed_sql
    assert '"PatientID"' not in executed_sql


def test_sql_agentic_force_query_helper_removed() -> None:
    assert not hasattr(AgentRuntime, "_force_sql_agentic_nlp_query_capability")


def test_capability_selection_accepts_common_missing_arguments_typo() -> None:
    proposal = CapabilitySelectionProposal.model_validate(
        {
            "task_id": "task_1",
            "evaluations": [
                {
                    "capability_id": "sql.query",
                    "operation_id": "query",
                    "fits": True,
                    "confidence": 0.9,
                    "reason": "The SQL agent can answer the database question.",
                    "missing_arguments_likley": ["prompt"],
                    "better_than_other_candidates": True,
                }
            ],
        }
    )

    assert proposal.evaluations[0].missing_arguments_likely == ["prompt"]


def test_capability_fit_reconciliation_promotes_fit_accepted_candidate() -> None:
    selection = CapabilitySelectionResult(
        task_id="task_1",
        selected=None,
        unresolved_reason="No shortlisted capability was accepted for task task_1.",
        candidates=[
            CapabilityRef(
                capability_id="sql.query",
                operation_id="query",
                confidence=0.9,
                reason="Candidate from shortlist.",
            )
        ],
    )
    decision = CapabilityFitDecision(
        task_id="task_1",
        candidate_capability_id="sql.query",
        candidate_operation_id="query",
        status="fit",
        confidence=0.95,
        reasons=["Fit accepted from fallback candidate."],
    )

    updated, reconciled = AgentRuntime._reconcile_fit_approved_capability_selections(
        [selection],
        [decision],
    )

    assert reconciled == ["task_1"]
    assert updated[0].selected is not None
    assert updated[0].selected.capability_id == "sql.query"
    assert updated[0].unresolved_reason is None


def test_sql_agentic_operator_binding_validation_rejects_self_and_undeclared_sources() -> None:
    manifest = build_default_registry().get("operator.python_transform").manifest
    self_bound_task = TaskFrame(
        id="task_1",
        description="Transform data",
        semantic_verb="transform",
        object_type="data",
        intent_confidence=0.9,
    )

    self_errors = _validate_operator_arguments(
        manifest,
        {
            "defer_code_generation": True,
            "input_bindings": [
                {
                    "input_name": "stdout",
                    "source_action_id": "task_1",
                    "source_field": "stdout",
                }
            ],
        },
        self_bound_task,
    )

    assert any("input_binding_self_reference" in error for error in self_errors)

    postprocess_task = TaskFrame(
        id="task_2",
        description="Use Python to transform the SQL result",
        semantic_verb="transform",
        object_type="data",
        intent_confidence=0.9,
        dependencies=["task_1"],
    )
    undeclared_errors = _validate_operator_arguments(
        manifest,
        {
            "defer_code_generation": True,
            "input_bindings": [
                {
                    "input_name": "stdout",
                    "source_action_id": "task_3",
                    "source_field": "stdout",
                }
            ],
        },
        postprocess_task,
        {SQL_AGENTIC_CONTEXT_KEY: {"parameter_key": "canonical_v1"}},
    )

    assert any(
        "sql_agentic_input_binding_source_not_declared_dependency" in error
        for error in undeclared_errors
    )

    non_sql_errors = _validate_operator_arguments(
        manifest,
        {
            "defer_code_generation": True,
            "input_bindings": [
                {
                    "input_name": "stdout",
                    "source_action_id": "task_3",
                    "source_field": "stdout",
                }
            ],
        },
        TaskFrame(
            id="task_4",
            description="Transform local data",
            semantic_verb="transform",
            object_type="data",
            intent_confidence=0.9,
        ),
    )

    assert not any("not_declared_dependency" in error for error in non_sql_errors)


@pytest.mark.skip(reason="obsolete deterministic entity hint behavior removed")
def test_sql_forced_table_selection_keeps_other_entity_hints_for_planner(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "intent": "query",
            "sql": (
                'SELECT COUNT(*) AS "count" FROM ('
                "SELECT PatientKey FROM flathr.RTRECORD "
                "GROUP BY PatientKey HAVING COUNT(*) > 100"
                ") AS patient_counts"
            ),
            "tables": ["flathr.RTRECORD"],
            "assumptions": ["PatientKey is the patient key present on RTRECORD."],
            "needs_discovery": False,
            "confidence": 0.9,
        },
        {"summary": "There are 4 patients with more than 100 RT records.", "confidence": 0.9},
    )
    service, _gateway = _service(tmp_path, gateway=RelationshipSqlGateway(), llm=llm)

    payload = service.run(
        prompt=(
            "In canocnial how many patients have more than 100 RTRECORDS\n"
            "User clarification: flathr.Patient"
        ),
        operation="query",
        parameter_key="canonica_v1",
        selected_table="flathr.Patient",
    )

    assert payload["status"] == "success"
    assert "SqlEntityChoice" not in llm.schemas
    assert "Entity candidate hints:" in llm.prompts[0]
    assert "RTRECORD" in llm.prompts[0]


@pytest.mark.skip(reason="obsolete deterministic entity clarification behavior removed")
def test_sql_candidate_resolution_low_confidence_returns_options(tmp_path: Path) -> None:
    service, gateway = _service(
        tmp_path,
        gateway=EntitySqlGateway(include_public_rtrecords=True),
        llm=None,
    )

    payload = service.run(
        prompt="in canonical how many rtrecords do we have",
        operation="query",
    )

    assert payload["status"] == "clarification_required"
    assert payload["error"] == "ambiguous_sql_entity"
    request = payload["clarification_request"]
    assert request["missing_information"] == "Table or view for rtrecords"
    labels = [option["label"] for option in request["options"]]
    assert "public.rtrecords" in labels
    assert "flathr.RTRecord" in labels
    assert gateway.raw_calls
    assert "sqlite3" not in "\n".join(call["command"] for call in gateway.raw_calls)


@pytest.mark.skip(reason="obsolete deterministic selected table behavior removed")
def test_sql_candidate_clarification_selection_resumes_with_selected_table(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "intent": "query",
            "sql": 'SELECT COUNT(*) AS "count" FROM flathr.RTPlan',
            "tables": ["flathr.RTPlan"],
            "assumptions": ["User clarification selected flathr.RTPlan."],
            "needs_discovery": False,
            "confidence": 0.95,
        },
        {"summary": "There are 7 RT plans.", "confidence": 0.9},
    )
    service, gateway = _service(
        tmp_path,
        gateway=EntitySqlGateway(include_public_rtrecords=True),
        llm=llm,
    )

    payload = service.run(
        prompt=(
            "in canonical how many rtrecords do we have\n"
            "User clarification: flathr.RTPlan"
        ),
        operation="query",
        parameter_key="canonica_v1",
        selected_table="flathr.RTPlan",
    )

    assert payload["status"] == "success"
    assert payload["rows"] == [{"count": "7"}]
    assert '"flathr"."RTPlan"' in payload["sql"]
    assert "SqlPlan" in llm.schemas
    assert "OperatorPlan" not in llm.schemas
    assert "sqlite3" not in "\n".join(call["command"] for call in gateway.raw_calls)


def test_sql_chat_agentic_path_keeps_typo_count_request_out_of_generic_operator(
    tmp_path: Path,
) -> None:
    sink = InMemoryEventSink()
    runtime, gateway, llm = _runtime(
        tmp_path,
        gateway=EntitySqlGateway(),
        llm=_agentic_sql_llm(
            sql='SELECT COUNT(*) AS "count" FROM "flathr"."RTRecord"',
            summary="There are 123 RT records.",
        ),
    )

    response = runtime.handle_request(
        "in canoinical how many rtrecords do we have",
        {
            "agent_mode": "llm_operator",
            "gateway_id": "gw-canonical",
            "gateway_node": "canonical-worker",
            "gateway_url": "http://canonical-worker:8787",
            "gateway_endpoints": {"canonical-worker": "http://canonical-worker:8787"},
            "observability": {
                "enabled": True,
                "debug": True,
                "sinks": [sink],
            },
            "workspace_root": str(tmp_path),
            "llm_operator_max_completion_repair_attempts": 0,
        },
    )

    assert "| 123 |" in response
    commands = "\n".join(call["command"] for call in gateway.raw_calls)
    executed_sql = "\n".join(
        str((call["execution_context"].get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        for call in gateway.raw_calls
    )
    assert '"flathr"."RTRecord"' in executed_sql
    assert "psql --no-psqlrc --csv" in commands
    assert "sqlite3" not in commands
    assert "OF_INPUT_DB_PATH" not in commands
    assert "find " not in commands
    assert "PromptRephraseProposal" in llm.schemas
    assert "PromptClassificationProposal" in llm.schemas
    assert "TaskDecompositionProposal" not in llm.schemas
    assert "VerbAssignmentProposal" not in llm.schemas
    assert "CapabilitySelectionProposal" not in llm.schemas
    assert "CapabilityFitProposal" not in llm.schemas
    assert "ArgumentExtractionProposal" not in llm.schemas
    assert "Complete discovered schema, all tables and columns:" in "\n".join(llm.prompts)
    assert "SqlAgentAction" in llm.schemas
    assert "OperatorPlan" not in llm.schemas
    schema_queries = [
        str((call["execution_context"].get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        for call in gateway.raw_calls
        if "information_schema." in str(
            (call["execution_context"].get("shell_env") or {}).get("OF_SQL_QUERY") or ""
        )
    ]
    assert sum("information_schema.schemata" in sql for sql in schema_queries) == 1
    assert sum("information_schema.columns" in sql for sql in schema_queries) == 1
    for call in gateway.raw_calls:
        execution_context = call["execution_context"]
        assert execution_context["gateway_id"] == "gw-canonical"
        assert execution_context["gateway_node"] == "canonical-worker"
        assert execution_context["gateway_url"] == "http://canonical-worker:8787"
        assert execution_context["gateway_endpoints"] == {
            "canonical-worker": "http://canonical-worker:8787"
        }
        assert execution_context["sql_gateway_required"] is True
        assert execution_context["sql_execution_source"] == "sql_agent"
    trace = runtime.last_planning_trace
    assert trace is not None
    assert trace.metadata["sql_agent"]["route_mode"] == "agentic"
    assert trace.metadata["sql_agent"]["agentic_context_attached"] is True
    assert trace.metadata["sql_agent_operator_route_suppressed"] is True
    assert trace.metadata["sql_agentic_decomposition_normalized"]["task_ids"] == ["task_1"]
    assert trace.metadata["sql_agentic_forced_query_capability_task_ids"] == ["task_1"]
    assert trace.metadata["sql_agentic_argument_extraction_forced"] == ["task_1"]
    dag = trace.validated_dag or {}
    assert len(dag["nodes"]) == 1
    assert dag["nodes"][0]["capability_id"] == "sql.query"
    assert "operator.python_transform" not in str(dag)
    command_events = [
        event
        for event in sink.events
        if str(event.event_type).startswith("execution.command.")
    ]
    assert any(
        (event.details or {}).get("command_kind") == "sql"
        and event.event_type == "execution.command.started"
        for event in command_events
    )


def test_sql_chat_agentic_path_traces_stored_user_organization_context(
    tmp_path: Path,
) -> None:
    store = AgentParameterStore(tmp_path / "parameters.db")
    value_json = _stored_schema_profile_value()
    context_json = _stored_schema_profile_context(
        "Core DICOM hierarchy use for SQL queries on canonical_v1 database:\n"
        "Patient.PatientID -> Study.PatientID\n"
        "Study.StudyInstanceUID -> Series.StudyInstanceUID"
    )
    store.create(
        AgentParameterCreate(
            key="canonical_v1",
            value_json=value_json,
            context_json=context_json,
            aliases=["CANONICAL_v1", "canonical_v1"],
            tags=["database", "postgresql", "discoverdb"],
            sensitive=True,
        )
    )
    sink = InMemoryEventSink()
    runtime, gateway, llm = _runtime(
        tmp_path,
        store=store,
        gateway=PatientAgeSqlGateway(),
        llm=_agentic_sql_llm(
            sql='SELECT "PatientKey", "Age" FROM "flathr"."Patient" LIMIT 10',
            summary="Found patients from stored SQL context.",
        ),
    )

    response = runtime.handle_request(
        "in canonical_v1 database list patient ages",
        {
            "agent_mode": "llm_operator",
            "observability": {
                "enabled": True,
                "debug": True,
                "sinks": [sink],
            },
            "workspace_root": str(tmp_path),
            "llm_operator_max_completion_repair_attempts": 0,
        },
    )

    assert response
    executed_queries = [
        str((call["execution_context"].get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        for call in gateway.raw_calls
    ]
    assert executed_queries == ['SELECT "PatientKey", "Age" FROM "flathr"."Patient" LIMIT 10']
    prompts = "\n".join(llm.prompts)
    assert "Core DICOM hierarchy use for SQL queries on canonical_v1 database" in prompts
    context_events = [
        event
        for event in sink.events
        if event.event_type in {"sql.context.prepared", "sql.context.loaded"}
    ]
    assert {event.details["schema_source"] for event in context_events} == {
        "parameter_store.schema_catalog",
        "session_schema_cache",
    }
    prompt_events = [event for event in sink.events if event.event_type == "sql.llm.prompt"]
    assert prompt_events
    assert prompt_events[0].details["prompt_contains_domain_context"] is True
    assert "Core DICOM hierarchy use for SQL queries" in str(
        prompt_events[0].details["domain_context_summary"]
    )
    command_events = [
        event
        for event in sink.events
        if str(event.event_type).startswith("execution.command.")
    ]
    assert any(
        event.event_type == "execution.command.started"
        and (event.details or {}).get("command_kind") == "sql"
        for event in command_events
    )
    rendered_events = str([event.model_dump(mode="json") for event in sink.events])
    for secret in ("pass123", "jimSolomon@mednet.ucla.edu", "10.44.102.204"):
        assert secret not in rendered_events


def test_sql_chat_preflight_records_sticky_sql_clarification_state(
    tmp_path: Path,
) -> None:
    runtime, gateway, _llm = _runtime(
        tmp_path,
        gateway=EntitySqlGateway(include_public_rtrecords=True),
        llm=QueueLLM(
            {
                "action": "ask_clarification",
                "question": "Which RT records table should I use?",
                "summary": "The SQL LLM needs one user choice.",
                "confidence": 0.8,
            }
        ),
    )

    response = runtime.handle_request(
        "in canonical how many rtrecords do we have",
        {
            "agent_mode": "llm_operator",
            "sql_agent_chat_route_mode": "direct",
            "workspace_root": str(tmp_path),
            "llm_operator_max_completion_repair_attempts": 0,
        },
    )

    assert response.startswith("## Clarification Required")
    trace = runtime.last_planning_trace
    assert trace is not None
    metadata = trace.metadata
    assert metadata["operator_clarification_phase"] == "sql_agent"
    assert metadata["operator_clarification_pending"] is True
    assert metadata["sql_agent_pending_state"]["parameter_key"] == "canonica_v1"
    request = metadata["operator_clarification_request"]
    assert request["question"] == "Which RT records table should I use?"
    commands = "\n".join(call["command"] for call in gateway.raw_calls)
    assert "sqlite3" not in commands
    assert "OF_INPUT_DB_PATH" not in commands


def test_sql_clarification_mode_auto_resolves_rtplans_schema_match(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "action": "ask_clarification",
            "question": "Which table represents rtplans?",
            "summary": "The table name is ambiguous.",
            "confidence": 0.8,
        },
        {
            "decision": "continue_with_assumption",
            "mode": "balanced",
            "confidence": 0.94,
            "selected_entities": [
                {
                    "entity_type": "sql_table",
                    "name": "flathr.RTPlan",
                    "value": "flathr.RTPlan",
                    "source": "schema_similarity",
                    "confidence": 0.94,
                }
            ],
            "assumptions": ["Treat rtplans as flathr.RTPlan."],
            "execution_directives": ["Use flathr.RTPlan for the user's rtplans term."],
            "user_question": "",
            "risk_flags": [],
            "reason": "The user term rtplans strongly matches the discovered RTPlan table.",
        },
        {
            "action": "execute_sql",
            "sql": 'SELECT COUNT(*) AS "count" FROM "flathr"."RTPlan"',
            "assumptions": ["Using flathr.RTPlan for rtplans."],
            "confidence": 0.94,
        },
        {"summary": "There are 7 RT plans.", "confidence": 0.9},
    )
    sink = InMemoryEventSink()
    observability = ObservabilityContext(
        request_id="req-sql-rtplans-auto-resolve",
        enabled=True,
        debug=True,
        sinks=[sink],
    )
    service, gateway = _service(
        tmp_path,
        gateway=EntitySqlGateway(),
        llm=llm,
        context={
            "agent_clarification_mode": "balanced",
            "observability": observability,
        },
    )

    payload = service.run(
        prompt="in canonical_v1 how many rtplans are there",
        operation="query",
    )

    assert payload["status"] == "success"
    assert payload["warnings"] == ["sql_clarification_auto_resolved"]
    assert payload["attempts"][0]["clarification_resolution"]["selected_entities"][0][
        "name"
    ] == "flathr.RTPlan"
    assert "AgentClarificationResolution" in llm.schemas
    sql_prompts = [
        prompt
        for prompt, schema in zip(llm.prompts, llm.schemas, strict=False)
        if schema == "SqlAgentAction"
    ]
    assert "Treat rtplans as flathr.RTPlan." in sql_prompts[-1]
    assert any(event.event_type == "clarification.auto_resolved" for event in sink.events)
    executed_queries = [
        str((call["execution_context"].get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        for call in gateway.raw_calls
    ]
    assert executed_queries[-1] == 'SELECT COUNT(*) AS "count" FROM "flathr"."RTPlan"'


def test_sql_clarification_mode_pedantic_surfaces_sql_clarification(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "action": "ask_clarification",
            "question": "Which table represents rtplans?",
            "summary": "The table name is ambiguous.",
            "confidence": 0.8,
        }
    )
    service, gateway = _service(
        tmp_path,
        gateway=EntitySqlGateway(),
        llm=llm,
        context={"agent_clarification_mode": "pedantic"},
    )

    payload = service.run(
        prompt="in canonical_v1 how many rtplans are there",
        operation="query",
    )

    assert payload["status"] == "clarification_required"
    assert payload["clarification_request"]["question"] == "Which table represents rtplans?"
    assert llm.schemas == ["SqlAgentAction"]
    executed_queries = [
        str((call["execution_context"].get("shell_env") or {}).get("OF_SQL_QUERY") or "")
        for call in gateway.raw_calls
    ]
    assert all('"flathr"."RTPlan"' not in query for query in executed_queries)


def test_sql_repair_loop_uses_sanitized_errors_and_retries(tmp_path: Path) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": "SELECT patient_id FROM canonical.bad_table LIMIT 100",
            "assumptions": [],
            "confidence": 0.8,
        },
        {
            "action": "execute_sql",
            "sql": "SELECT patient_id, name FROM canonical.patients LIMIT 100",
            "assumptions": ["Repaired to the discovered patients table."],
            "confidence": 0.86,
        },
        {"summary": "Found 1 patient after repair.", "confidence": 0.9},
    )
    service, gateway = _service(tmp_path, llm=llm)

    payload = service.run(prompt="show patients in canonical_v1", operation="query")

    assert payload["status"] == "success"
    assert payload["warnings"] == ["llm_sql_retry_after_validation"]
    assert payload["summary"] == "Found 1 patient after repair."
    assert len(gateway.raw_calls) == 7
    repair_prompt = llm.prompts[1]
    assert "bad_table" in repair_prompt
    for secret in ("pass123", "jimSolomon@mednet.ucla.edu", "10.44.102.204"):
        assert secret not in repair_prompt


def test_sql_mutation_requires_confirmation_before_gateway_execution(tmp_path: Path) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": "UPDATE canonical.patients SET name = name WHERE patient_id = 1",
            "assumptions": [],
            "confidence": 0.8,
        }
    )
    store = _create_store(tmp_path)
    service, gateway = _service(tmp_path, store=store, llm=llm)

    payload = service.run(
        prompt="touch one patient row in canonical_v1",
        operation="query",
        parameter_key="canonica_v1",
    )

    assert payload["status"] == "confirmation_required"
    assert payload["confirmation_required"] is True
    assert payload["generated_sql"].startswith("UPDATE canonical.patients")
    action = payload["confirmation_actions"][0]
    assert action["arguments"]["generated_sql"] == payload["generated_sql"]
    assert action["arguments"]["executed_sql"] == payload["executed_sql"]
    assert action["arguments"]["sql"] == payload["executed_sql"]
    assert payload["safety_classification"]["classification"] == "mutating"
    assert len(gateway.raw_calls) == 6


def test_sql_confirmation_replay_executes_same_approved_sql(tmp_path: Path) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": "UPDATE canonical.patients SET name = name WHERE patient_id = 1",
            "assumptions": [],
            "confidence": 0.8,
        }
    )
    store = _create_store(tmp_path)
    service, gateway = _service(tmp_path, store=store, llm=llm)
    pending_payload = service.run(
        prompt="touch one patient row in canonical_v1",
        operation="query",
        parameter_key="canonica_v1",
    )
    confirmed_service, _gateway = _service(
        tmp_path,
        store=store,
        gateway=gateway,
        llm=QueueLLM(),
        context={
            "confirmation": True,
            "sql_confirmed_action": pending_payload["sql_pending_confirmation"],
        },
    )

    executed = confirmed_service.run(
        prompt="touch one patient row in canonical_v1",
        operation="query",
        parameter_key="canonica_v1",
    )

    assert executed["status"] == "success"
    assert executed["executed_sql"] == pending_payload["executed_sql"]
    assert gateway.raw_calls[-1]["execution_context"]["shell_env"]["OF_SQL_QUERY"] == (
        pending_payload["executed_sql"]
    )


def test_sql_auto_approved_confirmation_replay_uses_confirmed_action(
    tmp_path: Path,
) -> None:
    llm = QueueLLM(
        {
            "action": "execute_sql",
            "sql": "UPDATE canonical.patients SET name = name WHERE patient_id = 1",
            "assumptions": [],
            "confidence": 0.8,
        }
    )
    store = _create_store(tmp_path)
    service, gateway = _service(tmp_path, store=store, llm=llm)
    pending_payload = service.run(
        prompt="touch one patient row in canonical_v1",
        operation="query",
        parameter_key="canonica_v1",
    )
    confirmed_service, _gateway = _service(
        tmp_path,
        store=store,
        gateway=gateway,
        llm=QueueLLM(),
        context={
            "confirmation": True,
            "auto_approved_confirmation": True,
            "sql_confirmed_action": pending_payload["sql_pending_confirmation"],
        },
    )

    executed = confirmed_service.run(
        prompt="touch one patient row in canonical_v1",
        operation="query",
        parameter_key="canonica_v1",
    )

    assert executed["status"] == "success"
    assert executed["executed_sql"] == pending_payload["executed_sql"]
    assert gateway.raw_calls[-1]["execution_context"]["shell_env"]["OF_SQL_QUERY"] == (
        pending_payload["executed_sql"]
    )


def test_sql_ambiguous_profiles_require_clarification(tmp_path: Path) -> None:
    store = _create_store(tmp_path)
    store.create(
        AgentParameterCreate(
            key="canonical_reporting",
            value_json={
                "dbname": "CANONICAL_REPORTING",
                "host": "10.44.102.205",
                "password": "reporting-secret",
                "port": 9501,
                "user": "reporting-user",
            },
            sensitive=True,
        )
    )
    service, gateway = _service(tmp_path, store=store)

    payload = service.run(prompt="list schemas in the canonical database", operation="discover")

    assert payload["status"] == "clarification_required"
    assert payload["error"] == "ambiguous_database_parameter"
    assert {choice["key"] for choice in payload["parameter_choices"]} >= {
        "canonica_v1",
        "canonical_reporting",
    }
    assert payload["clarification_request"]["question"] == "Which database parameter should I use?"
    assert payload["sql_pending_state"]["unresolved_term"] == "database_parameter"
    assert gateway.raw_calls == []
    assert "reporting-secret" not in str(payload)


def test_sql_chat_preflight_routes_before_generic_operator(tmp_path: Path) -> None:
    runtime, gateway, llm = _runtime(tmp_path)

    response = runtime.handle_request(
        "list all schemas in canonical_v1",
        {
            "agent_mode": "llm_operator",
            "sql_agent_chat_route_mode": "direct",
            "workspace_root": str(tmp_path),
            "llm_operator_max_completion_repair_attempts": 0,
        },
    )

    assert "Discovered 2 schema(s) and 1 table(s)." in response
    assert "| schema |" in response
    assert "| public |" in response
    assert "| canonical |" in response
    commands = "\n".join(call["command"] for call in gateway.raw_calls)
    assert "psql --no-psqlrc --csv" in commands
    assert "sqlite3" not in commands
    assert "OF_INPUT_DBNAME" not in commands
    assert "find " not in commands
    assert "SqlDiscoverySummary" in llm.schemas
    assert "SqlPlan" not in llm.schemas
    assert "OperatorPlan" not in llm.schemas


def test_sql_api_query_endpoint_uses_dedicated_agent(tmp_path: Path) -> None:
    runtime, gateway, _llm = _runtime(tmp_path)
    client = TestClient(
        create_app(
            Settings(
                workspace_root=tmp_path,
                openai_compat_model_name="OpenFABRIC Echo",
                agent_parameters_db_path=tmp_path / "api-parameters.db",
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

    response = client.post(
        "/api/agent/sql/query",
        json={
            "prompt": "show patients in canonical_v1",
            "operation": "query",
            "context": {
                "gateway_id": "gw-api",
                "gateway_node": "api-worker",
                "gateway_url": "http://api-worker:8787",
                "gateway_endpoints": {"api-worker": "http://api-worker:8787"},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["parameter_key"] == "canonica_v1"
    assert payload["rows"] == [{"patient_id": "1", "name": "Ada"}]
    for call in gateway.raw_calls:
        execution_context = call["execution_context"]
        assert execution_context["gateway_id"] == "gw-api"
        assert execution_context["gateway_node"] == "api-worker"
        assert execution_context["gateway_url"] == "http://api-worker:8787"
        assert execution_context["gateway_endpoints"] == {"api-worker": "http://api-worker:8787"}
        assert execution_context["sql_gateway_required"] is True
        assert execution_context["sql_execution_source"] == "sql_agent"
