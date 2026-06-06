"""Executor helpers for operator step runner."""

from __future__ import annotations

from agent_runtime.operator.step_runner_support.common import *


class PythonTransformExecutor:
    """Executor for trusted local operator Python transforms."""

    def execute(self, action: OperatorAction, inputs: dict[str, Any] | None = None) -> Any:
        namespace: dict[str, Any] = operator_python_runtime_namespace()
        compiled = compile(str(action.code or ""), f"<operator:{action.action_id}>", "exec")
        exec(compiled, namespace, namespace)
        transform = namespace.get("transform")
        if not callable(transform):
            raise RuntimeError("python_transform did not define callable transform(inputs).")
        return transform(dict(inputs or {}))


__all__ = ["PythonTransformExecutor"]
