"""SQL result review and summary helpers."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.capabilities.sql_support.service_support.common import *

class SqlAgentReviewSummaryMixin:
    def _review_sql_result_contract(
        self,
        *,
        prompt: str,
        generated_sql: str,
        executed_sql: str,
        result_strategy: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        truncated: bool,
    ) -> dict[str, Any]:
        """Use a typed LLM review to catch result-shape mismatches without domain rules."""

        fallback_error = _fallback_sql_result_shape_error(
            prompt=prompt,
            sql=executed_sql,
            columns=columns,
            rows=rows,
        )
        complete_json = getattr(self.llm_client, "complete_json", None)
        if not callable(complete_json):
            if fallback_error:
                return {
                    "decision": "retry",
                    "confidence": 0.7,
                    "request_intent": "",
                    "expected_result_shape": "",
                    "observed_result_shape": "",
                    "missing_requirements": [fallback_error],
                    "retry_instruction": fallback_error,
                    "reason": "Typed LLM review unavailable; conservative fallback detected a result-shape mismatch.",
                    "error": fallback_error,
                    "source": "fallback",
                }
            return {
                "decision": "accept",
                "confidence": 0.0,
                "request_intent": "",
                "expected_result_shape": "",
                "observed_result_shape": "",
                "missing_requirements": [],
                "retry_instruction": "",
                "reason": "Typed LLM review unavailable and fallback found no result-shape mismatch.",
                "source": "fallback",
            }

        prompt_text = "\n".join(
            [
                *__prompt_lines__("sql.result_contract_review"),
                f"User request: {prompt}",
                f"Result strategy: {result_strategy}",
                "Generated SQL:",
                generated_sql[:12000],
                "Executed SQL:",
                executed_sql[:12000],
                "Result columns:",
                json.dumps(columns, ensure_ascii=True),
                f"Total row count before runtime truncation: {len(rows)}",
                f"Runtime truncated response: {bool(truncated)}",
                "Rows sample:",
                json.dumps(rows[:25], ensure_ascii=True, default=str),
            ]
        )
        try:
            raw = complete_json(prompt_text, SqlResultContractReview.model_json_schema())
            review = SqlResultContractReview.model_validate(raw)
        except Exception as exc:
            if fallback_error:
                return {
                    "decision": "retry",
                    "confidence": 0.7,
                    "request_intent": "",
                    "expected_result_shape": "",
                    "observed_result_shape": "",
                    "missing_requirements": [fallback_error],
                    "retry_instruction": fallback_error,
                    "reason": (
                        "Typed LLM result-contract review failed; conservative fallback "
                        f"detected a mismatch. Review error: {exc}"
                    ),
                    "error": fallback_error,
                    "source": "fallback_after_review_error",
                }
            return {
                "decision": "accept",
                "confidence": 0.0,
                "request_intent": "",
                "expected_result_shape": "",
                "observed_result_shape": "",
                "missing_requirements": [],
                "retry_instruction": "",
                "reason": f"Typed LLM result-contract review failed: {exc}",
                "source": "review_error",
            }

        payload = review.model_dump(mode="json")
        payload["source"] = "llm"
        if review.decision == "retry" and float(review.confidence or 0.0) >= 0.7:
            retry_instruction = str(review.retry_instruction or review.reason or "").strip()
            missing = [
                str(item).strip()
                for item in review.missing_requirements
                if str(item).strip()
            ]
            error_parts = [
                "sql_result_contract_review_retry",
                retry_instruction or "Executed SQL result shape does not answer the request.",
            ]
            if missing:
                error_parts.append("Missing " + "; ".join(missing) + ".")
            payload["error"] = ": ".join(error_parts)
        else:
            payload["decision"] = "accept"
            payload["error"] = ""
        return payload


    def _summarize_result(
        self,
        *,
        prompt: str,
        sql: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        assumptions: list[str],
    ) -> str:
        complete_json = getattr(self.llm_client, "complete_json", None)
        fallback = f"Query returned {len(rows)} row(s)."
        if not callable(complete_json):
            return fallback
        prompt_text = "\n".join(
            [
                *__prompt_lines__("sql.result_summary"),
                f"User request: {prompt}",
                f"SQL: {sql}",
                "Columns: " + ", ".join(columns),
                "Rows sample:",
                json.dumps(rows[:20], ensure_ascii=True, default=str),
                "Assumptions:",
                json.dumps(assumptions, ensure_ascii=True),
            ]
        )
        try:
            raw = complete_json(prompt_text, SqlSummary.model_json_schema())
            return SqlSummary.model_validate(raw).summary.strip() or fallback
        except Exception:
            return fallback


    def _summarize_discovery(
        self,
        schema: dict[str, Any],
        *,
        prompt: str,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> str:
        schemas = schema.get("schemas") or []
        tables = schema.get("tables") or []
        fallback = f"Discovered {len(schemas)} schema(s) and {len(tables)} table(s)."
        complete_json = getattr(self.llm_client, "complete_json", None)
        if not callable(complete_json):
            if columns == ["schema"]:
                return f"Found {len(rows)} schema(s)."
            if "table" in columns and "column" not in columns:
                return f"Found {len(rows)} table(s)."
            if "column" in columns:
                return f"Found {len(rows)} column(s)."
            return fallback
        prompt_text = "\n".join(
            [
                *__prompt_lines__("sql.discovery_summary"),
                f"User request: {prompt}",
                "Columns: " + ", ".join(columns),
                "Rows:",
                json.dumps(rows[:100], ensure_ascii=True, default=str),
            ]
        )
        try:
            raw = complete_json(prompt_text, SqlDiscoverySummary.model_json_schema())
            return SqlDiscoverySummary.model_validate(raw).summary.strip() or fallback
        except Exception:
            return fallback


__all__ = []
