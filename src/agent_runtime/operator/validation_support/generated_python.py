"""Generated Python static checks and runtime proof helpers."""

from __future__ import annotations

from agent_runtime.operator.validation_support.common import *
from agent_runtime.operator.validation_support.context import *

def _regex_literal_words(pattern: str) -> list[str]:
    """Return alphabetic literal runs from a regex pattern, excluding regex syntax."""

    words: list[str] = []
    index = 0
    in_class = False
    escaped = False
    while index < len(pattern):
        char = pattern[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "[":
            in_class = True
            index += 1
            continue
        if char == "]" and in_class:
            in_class = False
            index += 1
            continue
        if in_class:
            index += 1
            continue
        if pattern.startswith("(?P<", index):
            end = pattern.find(">", index + 4)
            index = len(pattern) if end < 0 else end + 1
            continue
        if pattern.startswith("(?", index):
            end = pattern.find(")", index + 2)
            modifier = pattern[index + 2 : end if end >= 0 else index + 2]
            if end >= 0 and modifier and all(part in "aiLmsux-:" for part in modifier):
                index = end + 1
                continue
        if char.isalpha():
            start = index
            while index < len(pattern) and pattern[index].isalpha():
                index += 1
            word = pattern[start:index]
            if len(word) >= 2:
                words.append(word)
            continue
        index += 1
    return words


def _regex_suspicious_literal_fragments(pattern: str) -> list[str]:
    """Return non-ASCII literal fragments in a regex pattern outside character classes."""

    fragments: list[str] = []
    index = 0
    in_class = False
    escaped = False
    while index < len(pattern):
        char = pattern[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "[":
            in_class = True
            index += 1
            continue
        if char == "]" and in_class:
            in_class = False
            index += 1
            continue
        if in_class:
            index += 1
            continue
        if ord(char) > 127 and not char.isspace():
            start = index
            while index < len(pattern) and ord(pattern[index]) > 127:
                index += 1
            fragments.append(pattern[start:index])
            continue
        index += 1
    return fragments


def generated_python_regex_literal_errors(
    code: str,
    *,
    input_packet: dict[str, Any],
    runtime_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reject generated regexes that contain literal words absent from runtime data."""

    preview_text = "\n".join(
        [
            _runtime_preview_text(input_packet),
            _runtime_preview_text(runtime_contract),
        ]
    )
    preview_text_lower = preview_text.lower()
    if not preview_text.strip():
        return []
    compact_units = {
        match.group(1).upper()
        for match in _COMPACT_NUMERIC_TOKEN_RE.finditer(preview_text)
    }
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "re"
            and func.attr in _PYTHON_REGEX_FUNCTIONS
        ):
            continue
        if not node.args:
            continue
        pattern_node = node.args[0]
        if not (isinstance(pattern_node, ast.Constant) and isinstance(pattern_node.value, str)):
            continue
        pattern = pattern_node.value
        literal_words = _regex_literal_words(pattern)
        literal_counts: dict[str, int] = {}
        for literal_word in literal_words:
            normalized_word = literal_word.lower()
            literal_counts[normalized_word] = literal_counts.get(normalized_word, 0) + 1
        repeated_literal_words = [
            word for word, count in literal_counts.items() if count >= 8 and word not in preview_text_lower
        ]
        if pattern.count("|") > 40 or len(pattern) > 2000 or repeated_literal_words:
            errors.append(
                {
                    "error": "generated_python_excessive_regex_literal_pattern",
                    "message": (
                        f"Regex pattern {pattern[:240]!r} is excessively large or repeats "
                        "literal alternatives that are not grounded in the runtime input preview."
                    ),
                    "repair_hint": (
                        "Replace the oversized regex with simple parsing over the exact input rows, "
                        "or use compact generic character classes without repeated invented literals."
                    ),
                }
            )
        if compact_units and (
            " " in pattern or "\\s" in pattern
        ) and any(unit in pattern.upper() for unit in compact_units):
            errors.append(
                {
                    "error": "generated_python_compact_token_spacing",
                    "message": (
                        f"Regex pattern {pattern!r} appears to require whitespace "
                        "around a compact number/unit token, but the runtime input "
                        "preview contains compact tokens such as '633MB'."
                    ),
                    "repair_hint": (
                        "Parse compact number/unit tokens without requiring spaces; "
                        "split the numeric prefix from the alphabetic suffix."
                    ),
                }
            )
        for word in literal_words:
            normalized = word.lower()
            if normalized in preview_text_lower:
                continue
            key = (pattern, word)
            if key in seen:
                continue
            seen.add(key)
            errors.append(
                {
                    "error": "generated_python_invented_regex_literal",
                    "message": (
                        f"Regex pattern {pattern!r} contains literal {word!r}, "
                        "but that literal does not appear in the runtime input preview."
                    ),
                    "repair_hint": (
                        "Remove invented literal regex fragments. Preserve exact input "
                        "literals/delimiters, or parse compact tokens by splitting numeric "
                        "and alphabetic characters."
                    ),
                }
            )
        for fragment in _regex_suspicious_literal_fragments(pattern):
            if fragment and fragment in preview_text:
                continue
            key = (pattern, fragment)
            if key in seen:
                continue
            seen.add(key)
            errors.append(
                {
                    "error": "generated_python_suspicious_regex_literal",
                    "message": (
                        f"Regex pattern {pattern!r} contains suspicious literal "
                        f"{fragment!r}, but that literal does not appear in the "
                        "runtime input preview."
                    ),
                    "repair_hint": (
                        "Remove corrupted or invented regex fragments and parse the "
                        "exact preview tokens using simple string logic or generic "
                        "character classes."
                    ),
                }
            )
    if compact_units and _SIZE_TOKEN_SPLIT_RE.search(code):
        errors.append(
            {
                "error": "generated_python_compact_token_split",
                "message": (
                    "Generated code splits a size/unit token on whitespace, but the "
                    "runtime input preview contains compact tokens such as '633MB'."
                ),
                "repair_hint": (
                    "Do not use size_str.split() for compact tokens. Split the numeric "
                    "prefix from the alphabetic suffix character by character."
                ),
            }
        )
    return errors


def generated_python_regex_escape_errors(code: str) -> list[dict[str, Any]]:
    """Reject likely double-escaped regex character classes in generated Python."""

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "re"
            and func.attr in _PYTHON_REGEX_FUNCTIONS
        ):
            continue
        if not node.args:
            continue
        pattern_node = node.args[0]
        if not (isinstance(pattern_node, ast.Constant) and isinstance(pattern_node.value, str)):
            continue
        pattern = pattern_node.value
        matches = sorted(
            {match.group(0) for match in _REGEX_DOUBLE_ESCAPED_CLASS_RE.finditer(pattern)}
        )
        if matches:
            key = f"{pattern}\0{','.join(matches)}"
            if key not in seen:
                seen.add(key)
                errors.append(
                    {
                        "error": "generated_python_double_escaped_regex_class",
                        "message": (
                            f"Regex pattern {pattern!r} contains double-escaped regex class tokens "
                            f"{', '.join(matches)}. These usually match literal backslash text instead of data."
                        ),
                        "repair_hint": (
                            "Use a single backslash in raw regex character classes, for example r'\\d+' not "
                            "r'\\\\d+', or avoid regex and split the numeric/text portions directly."
                        ),
                    }
                )
        pattern_upper = pattern.upper()
        code_looks_like_size_parser = bool(_SIZE_PARSER_TEXT_RE.search(code))
        pattern_has_number = "\\d" in pattern or "[0-9" in pattern
        pattern_has_unit = (
            "[A-Z" in pattern_upper
            or any(
                unit in pattern_upper
                for unit in ("KB", "MB", "GB", "TB", "KIB", "MIB", "GIB", "TIB")
            )
        )
        if code_looks_like_size_parser and pattern_has_number and pattern_has_unit and "\\s+" in pattern:
            key = f"{pattern}\0compact-size-space"
            if key not in seen:
                seen.add(key)
                errors.append(
                    {
                        "error": "generated_python_compact_size_regex_requires_space",
                        "message": (
                            f"Regex pattern {pattern!r} appears to parse size values but requires "
                            "whitespace between the number and unit. Compact values such as 119MB "
                            "or 2.43GB would not match."
                        ),
                        "repair_hint": (
                            "Handle both compact and spaced number/unit tokens. Prefer splitting the "
                            "numeric prefix from the alphabetic suffix, or use optional whitespace."
                        ),
                    }
                )
    return errors


_PYTHON_FILE_WRITE_METHODS = {
    "write",
    "writelines",
    "write_text",
    "write_bytes",
    "touch",
    "mkdir",
    "unlink",
    "rename",
    "rmdir",
}

_PYTHON_MUTATING_OS_CALLS = {
    "remove",
    "unlink",
    "rename",
    "replace",
    "rmdir",
    "removedirs",
    "mkdir",
    "makedirs",
    "chmod",
    "chown",
    "truncate",
    "utime",
    "system",
    "popen",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
}

_PYTHON_SUBPROCESS_CALLS = {"run", "call", "check_call", "check_output", "Popen"}

_ACTION_FILE_WRITE_CONTRACT_RE = re.compile(
    r"\b(?:write|save|persist|create|append|overwrite|update|touch|mkdir|"
    r"rename|move|copy|delete|remove|file|filesystem|output\s+file)\b",
    re.IGNORECASE,
)


def generated_python_code_hash(code: str) -> str:
    return hashlib.sha256(str(code or "").encode("utf-8")).hexdigest()[:16]


def generated_python_failure_signature(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip().lower()
    text = re.sub(r'File "[^"]+", line \d+', "file <path>, line <n>", text)
    text = re.sub(r"\bline \d+\b", "line <n>", text)
    text = re.sub(r"0x[0-9a-f]+", "0x<addr>", text)
    return text[:240]


def _ast_call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _open_call_mode(node: ast.Call) -> str:
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return str(node.args[1].value or "")
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value or "")
    return "r"


def generated_python_side_effect_reasons(code: str) -> list[dict[str, Any]]:
    """Return static reasons a generated Python candidate is not pure."""

    try:
        tree = ast.parse(str(code or ""))
    except SyntaxError:
        return []
    reasons: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _ast_call_name(node)
        call_tail = call_name.rsplit(".", 1)[-1]
        if call_name == "open":
            mode = _open_call_mode(node)
            mutation = any(char in mode for char in ("w", "a", "x", "+"))
            reasons.append(
                {
                    "kind": "filesystem_write" if mutation else "filesystem_read",
                    "call": call_name,
                    "mode": mode,
                    "mutation": mutation,
                }
            )
            continue
        if call_tail in _PYTHON_FILE_WRITE_METHODS:
            reasons.append(
                {
                    "kind": "filesystem_write",
                    "call": call_name,
                    "mutation": True,
                }
            )
            continue
        if call_name.startswith("subprocess.") and call_tail in _PYTHON_SUBPROCESS_CALLS:
            reasons.append(
                {
                    "kind": "subprocess",
                    "call": call_name,
                    "mutation": False,
                }
            )
            continue
        if call_name.startswith("os.") and call_tail in _PYTHON_MUTATING_OS_CALLS:
            reasons.append(
                {
                    "kind": "os_mutation",
                    "call": call_name,
                    "mutation": True,
                }
            )
            continue
        if call_name.startswith("shutil."):
            reasons.append(
                {
                    "kind": "filesystem_mutation",
                    "call": call_name,
                    "mutation": True,
                }
            )
            continue
        if call_name in {"eval", "exec", "input", "__import__"}:
            reasons.append(
                {
                    "kind": "unsafe_runtime",
                    "call": call_name,
                    "mutation": False,
                }
            )
    return reasons


def generated_python_action_boundary_errors(
    action: OperatorAction,
    code: str,
) -> list[dict[str, Any]]:
    """Reject side effects that the original action contract does not own."""

    reasons = generated_python_side_effect_reasons(code)
    mutating_reasons = [reason for reason in reasons if bool(reason.get("mutation"))]
    if not mutating_reasons:
        return []
    action_text = " ".join(
        [
            str(action.reason or ""),
            str(action.effect_summary or ""),
            str(action.declared_output_shape or ""),
        ]
    )
    errors: list[dict[str, Any]] = []
    if str(action.effect_intent or "").strip() == "read_only":
        errors.append(
            {
                "error": "generated_python_side_effect_in_read_only_action",
                "message": (
                    "Generated Python mutates state, but the action contract is read-only."
                ),
                "action_id": action.action_id,
                "side_effects": mutating_reasons,
                "repair_hint": (
                    "Keep parsing/calculation actions side-effect free. Move file writes or "
                    "other mutations into the action whose goal explicitly owns that mutation."
                ),
            }
        )
    file_write_reasons = [
        reason for reason in mutating_reasons if "filesystem" in str(reason.get("kind") or "")
    ]
    if file_write_reasons and not _ACTION_FILE_WRITE_CONTRACT_RE.search(action_text):
        errors.append(
            {
                "error": "generated_python_unowned_filesystem_write",
                "message": (
                    "Generated Python writes to the filesystem, but the original action "
                    "contract does not explicitly own a file write."
                ),
                "action_id": action.action_id,
                "side_effects": file_write_reasons,
                "repair_hint": (
                    "Do not add file writes to compute/parse actions. Return the computed "
                    "value and let the separate save/write action persist it."
                ),
            }
        )
    return errors


def _python_proof_runner(function_name: str, code: str, inputs: dict[str, Any]) -> str:
    code_b64 = base64.b64encode(str(code or "").encode("utf-8")).decode("ascii")
    inputs_b64 = base64.b64encode(
        json.dumps(dict(inputs or {}), default=str).encode("utf-8")
    ).decode("ascii")
    runner = f"""
import base64
import importlib
import json

inputs = json.loads(base64.b64decode({inputs_b64!r}).decode("utf-8"))
code = base64.b64decode({code_b64!r}).decode("utf-8")
namespace = {{}}
for _module_name in {_COMMON_OPERATOR_PYTHON_MODULES!r}:
    try:
        namespace[_module_name] = importlib.import_module(_module_name)
    except Exception:
        pass
if "pathlib" in namespace:
    namespace["Path"] = namespace["pathlib"].Path
exec(compile(code, "<operator_python_proof>", "exec"), namespace, namespace)
fn = namespace.get({function_name!r})
if not callable(fn):
    raise RuntimeError("generated Python code must define {function_name}(inputs).")
result = fn(inputs)
if result is not None:
    if isinstance(result, (dict, list, tuple)):
        print(json.dumps(result, default=str))
    else:
        print(result)
""".strip()
    return runner


def generated_python_proof_result(
    action: OperatorAction,
    code: str,
    *,
    action_inputs: dict[str, Any],
    input_packet: dict[str, Any],
    runtime_contract: dict[str, Any],
    timeout_seconds: float = 3.0,
) -> GeneratedPythonProofResult:
    """Deterministically prove or reject generated Python before real execution."""

    code_hash = generated_python_code_hash(code)
    input_names = sorted(str(name) for name in dict(action_inputs or {}))
    static_errors = [
        *generated_python_regex_escape_errors(code),
        *generated_python_regex_literal_errors(
            code,
            input_packet=input_packet,
            runtime_contract=runtime_contract,
        ),
        *generated_python_action_boundary_errors(action, code),
    ]
    if static_errors:
        signature = generated_python_failure_signature(static_errors[0].get("message"))
        return GeneratedPythonProofResult(
            decision="reject",
            reason="Generated Python failed deterministic static proof.",
            proof_mode="static_only",
            failure_signature=signature,
            code_hash=code_hash,
            input_names=input_names,
            errors=static_errors,
        )
    side_effects = generated_python_side_effect_reasons(code)
    if side_effects:
        return GeneratedPythonProofResult(
            decision="skip_execution",
            reason="Generated Python is not side-effect-free, so runtime sample proof was skipped.",
            proof_mode="static_only",
            failure_signature="",
            code_hash=code_hash,
            input_names=input_names,
            errors=[
                {
                    "error": "generated_python_runtime_sample_skipped",
                    "message": "Generated Python has side-effect or external-runtime calls.",
                    "side_effects": side_effects,
                }
            ],
        )
    function_name = "main" if action.kind == "python_action" else "transform"
    runner = _python_proof_runner(function_name, code, action_inputs)
    try:
        with tempfile.TemporaryDirectory(prefix="operator-python-proof-") as temp_dir:
            completed = subprocess.run(
                ["python3", "-c", runner],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        signature = generated_python_failure_signature("timeout")
        return GeneratedPythonProofResult(
            decision="reject",
            reason="Generated Python timed out during pure runtime sample proof.",
            proof_mode="pure_runtime_sample",
            failure_signature=signature,
            code_hash=code_hash,
            input_names=input_names,
            errors=[
                {
                    "error": "generated_python_proof_timeout",
                    "message": f"Python proof timed out after {timeout_seconds:.1f}s.",
                }
            ],
            stdout_preview=str(exc.stdout or "")[:1000],
            stderr_preview=str(exc.stderr or "")[:1000],
        )
    stdout_preview = str(completed.stdout or "")[:1000]
    stderr_preview = str(completed.stderr or "")[:1000]
    if completed.returncode != 0:
        message = stderr_preview or stdout_preview or f"exit_code={completed.returncode}"
        signature = generated_python_failure_signature(message)
        return GeneratedPythonProofResult(
            decision="reject",
            reason="Generated Python failed against the actual bound input sample.",
            proof_mode="pure_runtime_sample",
            failure_signature=signature,
            code_hash=code_hash,
            input_names=input_names,
            errors=[
                {
                    "error": "generated_python_sample_execution_failed",
                    "message": message,
                    "exit_code": completed.returncode,
                }
            ],
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
        )
    try:
        validate_operator_python_output(action, action_inputs, completed.stdout)
    except ValueError as exc:
        message = str(exc)
        signature = generated_python_failure_signature(message)
        return GeneratedPythonProofResult(
            decision="reject",
            reason=f"Generated Python produced invalid sample output: {message}",
            proof_mode="pure_runtime_sample",
            failure_signature=signature,
            code_hash=code_hash,
            input_names=input_names,
            errors=[
                {
                    "error": "generated_python_sample_output_invalid",
                    "message": message,
                }
            ],
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
        )
    return GeneratedPythonProofResult(
        decision="accept",
        reason="Generated Python passed pure runtime sample proof.",
        proof_mode="pure_runtime_sample",
        failure_signature="",
        code_hash=code_hash,
        input_names=input_names,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
    )

__all__ = [name for name in globals() if not name.startswith("__")]
