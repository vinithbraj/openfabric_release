from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _count(db_path: Path, table: str) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _scalar(db_path: Path, sql: str) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(sql).fetchone()
    return "" if row is None else str(row[0] or "")


def test_v1_seed_builder_creates_clean_non_personal_state(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_v1_seed_state.py"),
            "--artifacts-dir",
            str(artifacts_dir),
            "--include-runtime-history",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert _count(artifacts_dir / "prompts.db", "prompt_templates") > 0
    advisory_body = _scalar(
        artifacts_dir / "prompts.db",
        "SELECT body FROM prompt_templates WHERE prompt_key = 'advisory.answer'",
    )
    assert "local-first typed agent runtime" in advisory_body

    assert _count(artifacts_dir / "agent_memory.db", "memory_entries") == 1
    memory_instruction = _scalar(
        artifacts_dir / "agent_memory.db",
        "SELECT instruction FROM memory_entries WHERE memory_id = 'mem_v1_product_identity'",
    )
    assert "local-first typed agent runtime" in memory_instruction
    assert "sudo" not in memory_instruction.lower()
    assert "device" not in memory_instruction.lower()

    empty_tables = {
        "agent_command_allowlist.db": ["operator_command_allowlist"],
        "agent_command_template_cache.db": ["command_template_entries"],
        "agent_computation_cache.db": ["computation_cache_entries"],
        "agent_learning_ledger.db": [
            "learning_runs",
            "learning_actions",
            "learning_cache_events",
            "learning_lessons",
            "capability_insights",
            "capability_proposals",
        ],
        "agent_lrn_total_tasks.db": ["lrn_total_task_entries"],
        "agent_monitors.db": [
            "agent_monitors",
            "agent_monitor_observations",
            "agent_monitor_triggers",
        ],
        "agent_plan_cache.db": ["plan_cache_entries"],
        "agent_plan_cache_live_test.db": ["plan_cache_entries"],
        "agent_reliability.db": [
            "reliability_events",
            "model_profiles",
            "approval_envelopes",
            "reliability_evals",
        ],
        "agent_events.db": [
            "agent_events",
            "agent_event_runs",
            "agent_notifications",
        ],
        "agent_tasks.db": [
            "agent_tasks",
            "agent_task_attempts",
            "agent_task_checkpoints",
        ],
        "agent_gateways.db": ["gateways"],
        "chats.db": ["chats", "chat_turns"],
        "agent_ui_settings.db": ["agent_ui_settings"],
        "agent_parameters.db": ["parameter_entries", "parameter_audit_events"],
    }
    for filename, tables in empty_tables.items():
        for table in tables:
            assert _count(artifacts_dir / filename, table) == 0
