#!/usr/bin/env python3
"""Rebuild clean OpenFabric V1 seed SQLite databases."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_runtime.api.chat_store import AgentConversationStore
from agent_runtime.api.settings_store import AgentUiSettingsStore
from agent_runtime.command_template_cache import AgentCommandTemplateCacheStore
from agent_runtime.computation_cache import AgentComputationCacheStore
from agent_runtime.events import AgentEventStore
from agent_runtime.gateways import AgentGatewayStore
from agent_runtime.learning_ledger import AgentLearningLedgerStore
from agent_runtime.lrn_total_tasks import AgentLrnTotalTaskStore
from agent_runtime.memory import AgentMemoryStore
from agent_runtime.monitors import AgentMonitorStore
from agent_runtime.operator.command_exceptions import OperatorCommandAllowlistStore
from agent_runtime.parameters import AgentParameterStore
from agent_runtime.plan_cache import AgentPlanCacheStore
from agent_runtime.prompts import DEFAULT_PROMPT_TEMPLATES_PATH, PromptTemplateStore
from agent_runtime.reliability import AgentReliabilityStore
from agent_runtime.tasks import AgentTaskStore


FIXED_TIMESTAMP = "2026-06-04T00:00:00Z"

TRACKED_SEED_DBS = {
    "agent_command_allowlist.db": "command_allowlist",
    "agent_command_template_cache.db": "command_template_cache",
    "agent_computation_cache.db": "computation_cache",
    "agent_learning_ledger.db": "learning_ledger",
    "agent_lrn_total_tasks.db": "lrn_total_tasks",
    "agent_memory.db": "memory",
    "agent_monitors.db": "monitors",
    "agent_plan_cache.db": "plan_cache",
    "agent_plan_cache_live_test.db": "plan_cache",
    "agent_reliability.db": "reliability",
    "prompts.db": "prompts",
}

OPTIONAL_RUNTIME_HISTORY_DBS = {
    "agent_events.db": "events",
    "agent_tasks.db": "tasks",
    "agent_gateways.db": "gateways",
    "chats.db": "chats",
    "agent_ui_settings.db": "ui_settings",
    "agent_parameters.db": "parameters",
}


def _unlink(path: Path) -> None:
    if path.exists():
        path.unlink()


def _stamp_db(path: Path) -> None:
    if not path.exists():
        return
    with sqlite3.connect(str(path)) as conn:
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]
        for table in tables:
            columns = {
                str(row[1])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column in ("created_at", "updated_at", "timestamp", "last_used_at"):
                if column in columns:
                    conn.execute(f"UPDATE {table} SET {column} = ?", (FIXED_TIMESTAMP,))


def _seed_prompts(path: Path) -> None:
    _unlink(path)
    store = PromptTemplateStore(path)
    store.seed_defaults(DEFAULT_PROMPT_TEMPLATES_PATH)
    _stamp_db(path)


def _seed_memory(path: Path) -> None:
    _unlink(path)
    AgentMemoryStore(path)
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            INSERT INTO memory_entries (
                memory_id, instruction, summary, status, memory_kind, scope,
                model_name, model_family, task_type, tool_type, intent_type,
                validator_error_type, safe_examples_json, blocked_examples_json,
                tags_json, provenance, request_id, rationale, created_at, updated_at,
                use_count, last_used_at
            ) VALUES (?, ?, ?, 'active', 'task_memory', 'global', '', '', '', '', '',
                '', '[]', '[]', ?, 'manual', '', ?, ?, ?, 0, '')
            """,
            (
                "mem_v1_product_identity",
                (
                    "OpenFabric is a local-first typed agent runtime. Ground product "
                    "answers in safe local execution, approvals, trace evidence, memory, "
                    "tasks, schedules, audio, integration APIs, and local or "
                    "OpenAI-compatible models."
                ),
                "Product identity and V1 runtime positioning.",
                json.dumps(["v1_seed", "product_identity", "runtime_guidance"]),
                "Clean V1 seed guidance; non-personal.",
                FIXED_TIMESTAMP,
                FIXED_TIMESTAMP,
            ),
        )
    _stamp_db(path)


def _build_empty_store(path: Path, store_kind: str) -> None:
    _unlink(path)
    if store_kind == "command_allowlist":
        OperatorCommandAllowlistStore(path)
    elif store_kind == "command_template_cache":
        AgentCommandTemplateCacheStore(path)
    elif store_kind == "computation_cache":
        AgentComputationCacheStore(path)
    elif store_kind == "learning_ledger":
        AgentLearningLedgerStore(path)
    elif store_kind == "lrn_total_tasks":
        AgentLrnTotalTaskStore(path)
    elif store_kind == "monitors":
        AgentMonitorStore(path)
    elif store_kind == "plan_cache":
        AgentPlanCacheStore(path)
    elif store_kind == "reliability":
        AgentReliabilityStore(path)
    elif store_kind == "events":
        AgentEventStore(path)
    elif store_kind == "tasks":
        AgentTaskStore(path)
    elif store_kind == "gateways":
        AgentGatewayStore(path)
    elif store_kind == "chats":
        AgentConversationStore(path)
    elif store_kind == "ui_settings":
        AgentUiSettingsStore(path)
    elif store_kind == "parameters":
        AgentParameterStore(path)
    else:  # pragma: no cover - guarded by caller maps
        raise ValueError(f"Unknown seed store kind: {store_kind}")
    _stamp_db(path)


def build_seed_state(artifacts_dir: Path, *, include_runtime_history: bool = False) -> list[Path]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    built: list[Path] = []
    for filename, store_kind in TRACKED_SEED_DBS.items():
        path = artifacts_dir / filename
        if store_kind == "prompts":
            _seed_prompts(path)
        elif store_kind == "memory":
            _seed_memory(path)
        else:
            _build_empty_store(path, store_kind)
        built.append(path)
    if include_runtime_history:
        for filename, store_kind in OPTIONAL_RUNTIME_HISTORY_DBS.items():
            path = artifacts_dir / filename
            _build_empty_store(path, store_kind)
            built.append(path)
    return built


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=ROOT / "artifacts",
        help="Directory where seed DBs should be rebuilt.",
    )
    parser.add_argument(
        "--include-runtime-history",
        action="store_true",
        help="Also recreate clean non-tracked runtime-history DBs in the target directory.",
    )
    args = parser.parse_args(argv)

    built = build_seed_state(
        args.artifacts_dir.resolve(),
        include_runtime_history=bool(args.include_runtime_history),
    )
    for path in built:
        print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
