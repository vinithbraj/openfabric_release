"""Generated Python proofing and deferred-code helpers for the operator step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class _StepRunnerPythonCodeMixin:
    @staticmethod
    def _has_nonempty_required_inputs(action: OperatorAction, action_inputs: dict[str, Any]) -> bool:
        """Return true when Python received meaningful bound or literal input."""

        return operator_python_has_nonempty_inputs(action, action_inputs)

    @staticmethod
    def _is_zero_like_output(output: Any) -> bool:
        """Detect zero-shaped transform outputs that often mean parsing silently failed."""

        return operator_python_output_is_zero_like(output)

    def _validate_python_output(
        self,
        action: OperatorAction,
        action_inputs: dict[str, Any],
        output: Any,
    ) -> None:
        """Fail zero-like aggregate results unless the action explicitly allows them."""

        validate_operator_python_output(action, action_inputs, output)

    @staticmethod
    def _python_code_proof_already_accepted(
        user_request: UserRequest,
        *,
        action_id: str,
        code_hash: str,
    ) -> bool:
        entries = user_request.session_context.get("operator_python_code_proof_results")
        if not isinstance(entries, list):
            return False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("action_id") or "") != str(action_id or ""):
                continue
            if str(entry.get("code_hash") or "") != str(code_hash or ""):
                continue
            if str(entry.get("decision") or "") in {"accept", "skip_execution"}:
                return True
        return False

    def _proof_concrete_python_before_execution(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        action_inputs: dict[str, Any],
        observability: ObservabilityContext | None,
    ) -> None:
        """Prove concrete LLM-authored Python once real inputs are available."""

        if action.kind not in {"python_action", "python_transform"}:
            return
        if action.defer_code_generation or not str(action.code or "").strip():
            return
        code_hash = generated_python_code_hash(str(action.code or ""))
        if self._python_code_proof_already_accepted(
            user_request,
            action_id=action.action_id,
            code_hash=code_hash,
        ):
            return
        input_packet = {
            name: self._input_preview(value)
            for name, value in sorted(dict(action_inputs or {}).items())
        }
        runtime_contract = {
            name: self._runtime_input_contract(value)
            for name, value in sorted(dict(action_inputs or {}).items())
        }
        self._emit(
            observability,
            level="info",
            event_type="operator.python_code.proof_started",
            title="Concrete Python proof started",
            summary="The runtime is proving concrete operator Python before execution.",
            details={
                "action_id": action.action_id,
                "task_id": action.task_id,
                "code_hash": code_hash,
                "input_names": sorted(action_inputs),
                "deferred_code_generation": False,
            },
        )
        proof = generated_python_proof_result(
            action,
            str(action.code or ""),
            action_inputs=action_inputs,
            input_packet=input_packet,
            runtime_contract=runtime_contract,
        )
        self._record_generated_python_proof(
            user_request,
            proof,
            action_id=action.action_id,
            attempt=0,
        )
        if proof.decision in {"accept", "skip_execution"}:
            self._emit(
                observability,
                level="info",
                event_type=(
                    "operator.python_code.proof_accepted"
                    if proof.decision == "accept"
                    else "operator.python_code.proof_skipped"
                ),
                title=(
                    "Concrete Python proof accepted"
                    if proof.decision == "accept"
                    else "Concrete Python runtime proof skipped"
                ),
                summary=proof.reason,
                details=proof.model_dump(mode="json"),
            )
            return
        self._emit(
            observability,
            level="warning",
            event_type="operator.python_code.proof_rejected",
            title="Concrete Python proof rejected",
            summary=proof.reason,
            details=proof.model_dump(mode="json"),
        )
        raise GeneratedPythonRuntimeProofError(proof)

    def _record_generated_python_proof(
        self,
        user_request: UserRequest,
        result: GeneratedPythonProofResult,
        *,
        action_id: str,
        attempt: int,
    ) -> None:
        payload = {
            "action_id": action_id,
            "attempt": attempt,
            **result.model_dump(mode="json"),
        }
        context_entries = user_request.session_context.setdefault(
            "operator_python_code_proof_results",
            [],
        )
        if isinstance(context_entries, list):
            context_entries.append(payload)
        trace = user_request.safety_context.get("planning_trace")
        metadata = getattr(trace, "metadata", None)
        if isinstance(metadata, dict):
            trace_entries = metadata.setdefault("operator_python_code_proof_results", [])
            if isinstance(trace_entries, list):
                trace_entries.append(payload)

    def _proof_generated_python_proposal(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        proposal: OperatorPythonCodeProposal,
        *,
        action_inputs: dict[str, Any],
        input_packet: dict[str, Any],
        runtime_contract: dict[str, Any],
        seen_code_hashes: set[str],
        seen_failure_signatures: set[str],
        attempt: int,
        observability: ObservabilityContext | None,
        allow_seen_code_hash: bool = False,
    ) -> tuple[OperatorAction | None, list[dict[str, Any]], bool]:
        """Return generated action, validation/proof errors, and whether to stop retrying."""

        code_hash = generated_python_code_hash(proposal.code)
        if code_hash in seen_code_hashes and not allow_seen_code_hash:
            self._emit(
                observability,
                level="error",
                event_type="operator.python_code.repair_loop_stopped",
                title="Generated Python repair loop stopped",
                summary="Generated Python repeated the same code hash for this action.",
                details={
                    "action_id": action.action_id,
                    "attempt": attempt,
                    "code_hash": code_hash,
                },
            )
            return (
                None,
                [
                    {
                        "error": "generated_python_repeated_code",
                        "message": (
                            "Generated Python repeated code that already failed or was already "
                            "submitted for this action."
                        ),
                        "code_hash": code_hash,
                        "repair_hint": (
                            "Produce materially different code that addresses the latest failure."
                        ),
                    }
                ],
                True,
            )
        seen_code_hashes.add(code_hash)
        generated = action.model_copy(
            update={
                "code": proposal.code,
                "defer_code_generation": False,
                "declared_output_shape": proposal.declared_output_shape
                or action.declared_output_shape,
                "allow_zero_result": proposal.allow_zero_result,
                "reason": proposal.reason or action.reason,
            }
        )
        try:
            generated = OperatorAction.model_validate(generated.model_dump(mode="python"))
        except (PydanticValidationError, ValueError) as exc:
            return (
                None,
                [{"error": "generated_python_action_schema_error", "message": str(exc)}],
                False,
            )
        errors = (
            self.validator._validate_python_program_action(generated)
            if generated.kind == "python_action"
            else self.validator._validate_python_action(generated)
        )
        if errors:
            return None, errors, False
        self._emit(
            observability,
            level="info",
            event_type="operator.python_code.proof_started",
            title="Generated Python proof started",
            summary="The runtime is proving generated Python against deterministic checks.",
            details={
                "action_id": action.action_id,
                "task_id": action.task_id,
                "attempt": attempt,
                "code_hash": code_hash,
                "input_names": sorted(action_inputs),
            },
        )
        proof = generated_python_proof_result(
            action,
            proposal.code,
            action_inputs=action_inputs,
            input_packet=input_packet,
            runtime_contract=runtime_contract,
        )
        self._record_generated_python_proof(
            user_request,
            proof,
            action_id=action.action_id,
            attempt=attempt,
        )
        if proof.decision in {"accept", "skip_execution"}:
            self._emit(
                observability,
                level="info",
                event_type=(
                    "operator.python_code.proof_accepted"
                    if proof.decision == "accept"
                    else "operator.python_code.proof_skipped"
                ),
                title=(
                    "Generated Python proof accepted"
                    if proof.decision == "accept"
                    else "Generated Python runtime proof skipped"
                ),
                summary=proof.reason,
                details=proof.model_dump(mode="json"),
            )
            return generated, [], False
        proof_errors = [
            {
                "error": "generated_python_proof_rejected",
                "message": proof.reason,
                "proof": proof.model_dump(mode="json"),
                "repair_hint": (
                    "Repair the generated Python against the exact proof error and runtime input sample."
                ),
            }
        ]
        repeated_signature = bool(
            proof.failure_signature and proof.failure_signature in seen_failure_signatures
        )
        if proof.failure_signature:
            seen_failure_signatures.add(proof.failure_signature)
        if repeated_signature:
            proof_errors.append(
                {
                    "error": "generated_python_repeated_failure_signature",
                    "message": (
                        "Generated Python repeated the same proof failure signature; "
                        "stopping this local repair loop."
                    ),
                    "failure_signature": proof.failure_signature,
                }
            )
            self._emit(
                observability,
                level="error",
                event_type="operator.python_code.repair_loop_stopped",
                title="Generated Python repair loop stopped",
                summary="The generated Python repair loop repeated the same failure signature.",
                details={
                    "action_id": action.action_id,
                    "attempt": attempt,
                    "failure_signature": proof.failure_signature,
                    "code_hash": proof.code_hash,
                },
            )
        else:
            self._emit(
                observability,
                level="warning",
                event_type="operator.python_code.proof_rejected",
                title="Generated Python proof rejected",
                summary=proof.reason,
                details=proof.model_dump(mode="json"),
            )
        return None, proof_errors, repeated_signature

    def _review_and_prove_generated_python_proposal(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        proposal: OperatorPythonCodeProposal,
        *,
        action_inputs: dict[str, Any],
        input_packet: dict[str, Any],
        runtime_contract: dict[str, Any],
        seen_code_hashes: set[str],
        seen_failure_signatures: set[str],
        attempt: int,
        observability: ObservabilityContext | None,
    ) -> tuple[OperatorAction | None, list[dict[str, Any]], bool, OperatorPythonCodeProposal]:
        """Prove generated Python before and after optional LLM review."""

        original_proposal = proposal
        generated, errors, stop_repair_loop = self._proof_generated_python_proposal(
            user_request,
            action,
            proposal,
            action_inputs=action_inputs,
            input_packet=input_packet,
            runtime_contract=runtime_contract,
            seen_code_hashes=seen_code_hashes,
            seen_failure_signatures=seen_failure_signatures,
            attempt=attempt,
            observability=observability,
        )
        if errors or generated is None:
            return generated, errors, stop_repair_loop, proposal
        reviewed = self._review_generated_python_code(
            user_request,
            action,
            proposal,
            input_packet=input_packet,
            runtime_contract=runtime_contract,
            observability=observability,
        )
        reviewed = self._critique_generated_python_code(
            user_request,
            action,
            reviewed,
            input_packet=input_packet,
            runtime_contract=runtime_contract,
            observability=observability,
        )
        if reviewed.model_dump(mode="json") == original_proposal.model_dump(mode="json"):
            return generated, [], False, reviewed
        generated, errors, stop_repair_loop = self._proof_generated_python_proposal(
            user_request,
            action,
            reviewed,
            action_inputs=action_inputs,
            input_packet=input_packet,
            runtime_contract=runtime_contract,
            seen_code_hashes=seen_code_hashes,
            seen_failure_signatures=seen_failure_signatures,
            attempt=attempt,
            observability=observability,
            allow_seen_code_hash=True,
        )
        return generated, errors, stop_repair_loop, reviewed

    def _complete_deferred_python_code(
        self,
        user_request: UserRequest,
        action: OperatorAction,
        action_inputs: dict[str, Any],
        observability: ObservabilityContext | None,
        *,
        execution_feedback: dict[str, Any] | None = None,
        previous_code: str | None = None,
    ) -> OperatorAction:
        """Generate Python code after upstream inputs have real runtime shape."""

        function_name = "main" if action.kind == "python_action" else "transform"
        input_packet = {
            name: self._input_preview(value)
            for name, value in sorted(action_inputs.items())
        }
        runtime_contract = {
            name: self._runtime_input_contract(value)
            for name, value in sorted(action_inputs.items())
        }
        self._attach_memory_for_stage(
            user_request,
            observability,
            stage="code_generation",
            source="code_generation",
        )
        prompt_parts = [
            *prompt_lines("operator.python_code_generation"),
            "This is the code-authoring stage; planning intentionally deferred Python because upstream data was not available yet.",
            "Use the runtime input contract below. It shows the exact shape your code receives in inputs.",
            "The authoring preview packet is metadata for you only; it is not passed into inputs at execution time.",
            *_operator_discoverability_lines(),
            *memory_prompt_lines_from_context(user_request, stage="code_generation"),
            *parameter_prompt_lines_from_context(user_request, stage="code_generation"),
            *_operator_clarification_lines(user_request),
            *_operator_policy_note_lines(user_request),
            *_operator_self_brief_lines(user_request),
            *_guided_deliberation_lines(user_request),
            *_operator_verification_mode_lines(
                verification_enforced=_operator_verification_enforced_from_request(user_request)
            ),
            "If the runtime contract says inputs['stdout'] is a string, write code that parses inputs['stdout'] directly.",
            "Do not write inputs['stdout']['preview'], inputs['stdout'].get('preview'), inputs['stdout'].get('chars'), or similar metadata access unless the runtime contract says that input is an object.",
            f"The code must start with def {function_name}(inputs): and read only from the provided inputs dict.",
            f"The JSON field code must be one complete Python function string whose first non-whitespace characters are exactly: def {function_name}(inputs):",
            "This is a hard structural contract. If the code field starts with anything else, including imports, comments, markdown, JSON, shell text, or explanatory prose, it will be rejected before execution.",
            "The code field should look like this shape, with your real logic inside the function body:",
            f"def {function_name}(inputs):\n    import json\n    # parse inputs and perform the requested work here\n    return {{\"summary\": \"result\"}}",
            "Put imports, helper code, subprocess calls, filesystem access, parsing, mutation, verification, and returns inside that function body.",
            "If your code uses a module name such as re, json, math, os, pathlib, glob, subprocess, pandas, numpy, or yaml, import it inside the function before first use.",
            "Do not put imports, comments, assignments, markdown, shell text, or helper functions before the required def line.",
            "When the action computes rows, records, totals, summaries, or parsed fields, prefer returning a JSON-serializable dict/list with named fields such as rows, items, total, summary, and errors, and set declared_output_shape to json.",
            "When invoking a subprocess tool that supports JSON, prefer its JSON output mode or JSON template and parse that structured output.",
            "When iterating over a list or discovered entities, wrap each item iteration in its own try/except, append failures to an errors list with the item identifier and exception message, and continue processing later items; after the loop, raise ValueError only if no requested successful rows/items were produced or required fields are missing.",
            (
                "If this Python action mutates state, it must capture command return codes/stdout/stderr, verify the postcondition with an independent read, and raise RuntimeError if the mutation is not verified."
                if _operator_verification_enforced_from_request(user_request)
                else "If this Python action mutates state, it must capture command return codes/stdout/stderr and raise RuntimeError on command failure; add independent postcondition verification only when the user asked for it or needed to avoid false success."
            ),
            "A mutating Python action must not report success solely because a target name was parsed from input.",
            "If this is a standalone verification action, read fresh current state inside the action or consume output from a read action that runs after the mutation; do not verify using old pre-mutation input alone.",
            "For tabular text input, parse rows and columns using the delimiter actually visible in the authoring input preview.",
            "For parsing or aggregation, verify the code against at least two concrete sample values from the authoring preview before returning it.",
            "Do not invent placeholder tokens, ellipses, or regex fragments that are not present in the preview. If a regex would be fragile, use simple string parsing instead.",
            "For compact numeric tokens, preserve their exact shape. Do not insert spaces into regex numeric patterns, and do not add literal placeholder words that are not visible in the preview.",
            "If a compact token combines a number and unit without a delimiter, parse it by splitting the numeric prefix from the alphabetic suffix; do not add invented regex words or letters absent from the sample token.",
            "For aggregation from rows, maintain a parsed_value_count. If the input has rows but parsed_value_count is zero, raise ValueError instead of returning a zero total.",
            (
                "This is a python_action, so stdlib subprocess/os/pathlib/glob are allowed when needed. "
                "Prefer parsing provided inputs when they contain the needed data."
                if action.kind == "python_action"
                else "This is a python_transform. Python imports, subprocess, os, pathlib, glob, and data-processing libraries are permitted, but prefer parsing provided inputs when they contain the needed data."
            ),
            "If required input is missing, empty, or unparsable, raise ValueError with a precise message.",
            "Do not return zero, an empty table, or a success string from non-empty required inputs unless zero is semantically valid; if so set allow_zero_result true and explain why.",
            "Schema for OperatorPythonCodeProposal:",
            _stable_json(OperatorPythonCodeProposal.model_json_schema()),
            "User prompt:",
            user_request.raw_prompt,
            "Action needing code:",
            action.model_dump_json(indent=2),
            "Runtime input contract:",
            _stable_json(runtime_contract),
            "Authoring input preview packet:",
            _stable_json(input_packet),
        ]
        if execution_feedback:
            prompt_parts.extend(
                [
                    "Previous generated Python execution feedback:",
                    _stable_json(execution_feedback),
                    "Previous generated code:",
                    str(previous_code or ""),
                    "Repair only this Python code for the same action. Do not change the action goal, dependencies, or surrounding plan.",
                    "Use the exact exception, traceback, stdout/stderr, runtime input contract, and prior code above to fix the code contract or parsing logic.",
                    "Do not repeat the failed parsing strategy. Re-check the authoring input preview and write the simplest code that parses those exact sample rows truthfully.",
                    "If prior code failed to parse a compact number+unit token that is visibly present in the preview, stop using that regex shape and split the token into numeric and alphabetic characters directly.",
                    "If the prior failure was a zero-like aggregate from non-empty input, assume the parser matched no values; replace that parser, count parsed values, and raise ValueError if the count remains zero.",
                ]
            )
        self._emit(
            observability,
            level="info",
            event_type="operator.python_code.generation_started",
            title="Deferred Python code generation started",
            summary="The runtime is asking the LLM for Python code using real upstream input shape.",
            details={
                "action_id": action.action_id,
                "task_id": action.task_id,
                "kind": action.kind,
                "input_packet": input_packet,
                "runtime_input_contract": runtime_contract,
                "execution_repair": bool(execution_feedback),
            },
        )
        last_errors: list[dict[str, Any]] = []
        rejected_proposal: dict[str, Any] | None = None
        generated: OperatorAction | None = None
        total_attempts = self._attempt_count(
            self._max_deferred_code_repair_attempts()
        )
        seen_code_hashes: set[str] = set()
        if previous_code:
            seen_code_hashes.add(generated_python_code_hash(previous_code))
        seen_failure_signatures: set[str] = set()
        if not execution_feedback:
            cached = self._try_computation_cache_adaptation(
                user_request,
                action,
                action_inputs,
                runtime_contract=runtime_contract,
                input_packet=input_packet,
                observability=observability,
            )
            if cached is not None:
                proposal, candidate = cached
                direct_codegen = dict(user_request.session_context or {}).get("operator_lrdirect_computation_codegen")
                direct_codegen = direct_codegen if isinstance(direct_codegen, dict) else {}
                direct_codegen_matches = (
                    str(direct_codegen.get("cache_id") or "") == candidate.entry.cache_id
                    and str(direct_codegen.get("action_id") or "") == action.action_id
                )
                try:
                    rejected_proposal = proposal.model_dump(mode="json")
                    if direct_codegen_matches:
                        generated, errors, _ = self._proof_generated_python_proposal(
                            user_request,
                            action,
                            proposal,
                            action_inputs=action_inputs,
                            input_packet=input_packet,
                            runtime_contract=runtime_contract,
                            seen_code_hashes=seen_code_hashes,
                            seen_failure_signatures=seen_failure_signatures,
                            attempt=0,
                            observability=observability,
                            allow_seen_code_hash=True,
                        )
                    else:
                        generated, errors, _, proposal = self._review_and_prove_generated_python_proposal(
                            user_request,
                            action,
                            proposal,
                            action_inputs=action_inputs,
                            input_packet=input_packet,
                            runtime_contract=runtime_contract,
                            seen_code_hashes=seen_code_hashes,
                            seen_failure_signatures=seen_failure_signatures,
                            attempt=0,
                            observability=observability,
                        )
                    rejected_proposal = proposal.model_dump(mode="json")
                except (PydanticValidationError, ValueError) as exc:
                    errors = [{"error": "computation_cache_schema_error", "message": str(exc)}]
                    generated = None
                if not errors and generated is not None:
                    assert self.computation_cache_store is not None
                    self.computation_cache_store.mark_used(candidate.entry.cache_id)
                    self._emit_computation_cache(
                        user_request,
                        observability,
                        event_type=OPERATOR_COMPUTATION_CACHE_HIT,
                        title="Computation cache template accepted",
                        summary="The adapted cached computation template passed normal Python validation.",
                        details={
                            "cache_id": candidate.entry.cache_id,
                            "score": candidate.score,
                            "action_id": action.action_id,
                        },
                    )
                    self._emit(
                        observability,
                        level="info",
                        event_type="operator.python_code.generated",
                        title="Deferred Python code generated",
                        summary="The runtime generated Python code from a cached computation template.",
                        details={
                            "action_id": action.action_id,
                            "task_id": action.task_id,
                            "kind": action.kind,
                            "input_names": sorted(action_inputs),
                            "declared_output_shape": generated.declared_output_shape,
                            "computation_cache_id": candidate.entry.cache_id,
                        },
                    )
                    return generated
                last_errors = errors
                if direct_codegen_matches:
                    assert self.computation_cache_store is not None
                    self.computation_cache_store.mark_failed(
                        candidate.entry.cache_id,
                        failure_category="lrdirect_python_proof_failed",
                        repair_notes=_stable_json(errors),
                    )
                    self._emit_lrdirect(
                        user_request,
                        observability,
                        event_type=OPERATOR_LRDIRECT_REJECTED,
                        title="LR Direct Python code rejected",
                        summary="The exact cached Python code failed deterministic proof; fresh code generation will continue.",
                        details={
                            "cache_type": "computation",
                            "cache_id": candidate.entry.cache_id,
                            "exact_step_key": str(direct_codegen.get("exact_step_key") or ""),
                            "action_id": action.action_id,
                            "action_kind": action.kind,
                            "errors": errors,
                        },
                        level="warning",
                    )
                self._emit_computation_cache(
                    user_request,
                    observability,
                    event_type=OPERATOR_COMPUTATION_CACHE_HIT,
                    title="Computation cache template rejected",
                    summary="The adapted cached computation template failed normal Python validation; fresh code generation will continue.",
                    details={
                        "cache_id": candidate.entry.cache_id,
                        "action_id": action.action_id,
                        "errors": errors,
                    },
                    level="warning",
                )
        for attempt in range(total_attempts):
            stop_repair_loop = False
            prompt = "\n".join(
                [
                    *prompt_parts,
                    *(
                        [
                            "Previous deferred Python code validation feedback:",
                            _stable_json(last_errors),
                            *(
                                [
                                    "Rejected OperatorPythonCodeProposal:",
                                    _stable_json(rejected_proposal),
                                ]
                                if rejected_proposal is not None
                                else []
                            ),
                            f"Repair only the OperatorPythonCodeProposal code. The next code field must start exactly with def {function_name}(inputs):.",
                            "If you need imports, place them indented inside the function body.",
                            "Do not repeat the same shape error. Return a JSON object whose code string begins with the required def line and nothing before it.",
                        ]
                        if last_errors
                        else []
                    ),
                ]
            )
            try:
                proposal = structured_call(self.llm_client, prompt, OperatorPythonCodeProposal)
                rejected_proposal = proposal.model_dump(mode="json")
                generated, errors, stop_repair_loop, proposal = self._review_and_prove_generated_python_proposal(
                    user_request,
                    action,
                    proposal,
                    action_inputs=action_inputs,
                    input_packet=input_packet,
                    runtime_contract=runtime_contract,
                    seen_code_hashes=seen_code_hashes,
                    seen_failure_signatures=seen_failure_signatures,
                    attempt=attempt + 1,
                    observability=observability,
                )
                rejected_proposal = proposal.model_dump(mode="json")
            except (PydanticValidationError, StructuredCallError, ValueError) as exc:
                errors = [{"error": "deferred_python_schema_error", "message": str(exc)}]
                rejected_proposal = None
            if not errors and generated is not None:
                break
            last_errors = errors
            self._emit(
                observability,
                level="warning",
                event_type="operator.python_code.rejected",
                title="Deferred Python code rejected",
                summary="Generated deferred Python code failed validation and will be repaired.",
                details={"attempt": attempt + 1, "errors": errors},
            )
            if stop_repair_loop:
                break
        if generated is None or last_errors and errors:
            raise DeferredPythonCodeGenerationError(
                f"Deferred Python code failed validation: {_stable_json(last_errors)}",
                errors=last_errors,
                rejected_proposal=rejected_proposal,
                function_name=function_name,
            )
        self._emit(
            observability,
            level="info",
            event_type="operator.python_code.generated",
            title="Deferred Python code generated",
            summary="The runtime generated Python code after upstream input shape was known.",
            details={
                "action_id": action.action_id,
                "task_id": action.task_id,
                "kind": action.kind,
                "input_names": sorted(action_inputs),
                "declared_output_shape": generated.declared_output_shape,
            },
        )
        return generated

    @staticmethod
    def _plan_with_replaced_action(plan: OperatorPlan, generated_action: OperatorAction) -> OperatorPlan:
        """Return a copy of plan with one action replaced by generated concrete code."""

        return plan.model_copy(
            update={
                "actions": [
                    generated_action if action.action_id == generated_action.action_id else action
                    for action in plan.actions
                ]
            }
        )

    def _action_requires_confirmation(self, plan: OperatorPlan, action: OperatorAction) -> bool:
        """Return whether one action needs explicit approval in this plan."""

        task_by_id = {task.task_id: task for task in plan.tasks}
        effect_policy_mode = operator_policy_mode(self.config, "effect")
        return (
            str(action.risk or "").strip().lower() in {"high", "critical"}
            or classify_action_effect(
                action,
                task=task_by_id.get(action.task_id),
                policy_mode=effect_policy_mode,
            ).mutates_state
        )

    def _deferred_python_code_confirmation_result(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        generated_action: OperatorAction,
        records: list[OperatorExecutionRecord],
        observability: ObservabilityContext | None,
    ) -> OperatorPipelineResult:
        """Pause after generating mutating Python so the user can approve actual code."""

        result = self.require_confirmation(user_request, plan, observability, records=records)
        metadata = dict(result.metadata)
        metadata.update(
            {
                "deferred_python_code_confirmation_pending": True,
                "generated_action_id": generated_action.action_id,
                "seed_record_count": len(records),
            }
        )
        self._emit(
            observability,
            level="warning",
            event_type="operator.python_code.approval_required",
            title="Generated Python code requires approval",
            summary=(
                "A deferred mutating Python action was generated from upstream data "
                "and must be approved before execution."
            ),
            details={
                "action_id": generated_action.action_id,
                "task_id": generated_action.task_id,
                "risk": generated_action.risk,
                "seed_record_count": len(records),
            },
        )
        return result.model_copy(
            update={
                "execution_records": list(records),
                "metadata": metadata,
            }
        )



__all__ = ["_StepRunnerPythonCodeMixin"]
