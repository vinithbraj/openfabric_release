"""Runtime vLLM compatibility patch for Qwen3-Next CPU offload.

vLLM 0.19.0 initializes the input batch before Qwen3-Next's hybrid attention
and Mamba KV-cache layout is fully resolved. The resolved layout then needs the
input batch to be rebuilt, but vLLM blocks that path when UVA CPU weight offload
is enabled. This shim keeps the original rebuild logic and only bypasses that
guard while the rebuild decision runs.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import sys


_TARGET_MODULE = "vllm.v1.worker.gpu_model_runner"
_PATCH_ENV = "QWEN3NEXT_CPU_OFFLOAD_REINIT_PATCH"
_LEGACY_PATCH_ENV = "VLLM_QWEN3NEXT_CPU_OFFLOAD_REINIT_PATCH"
_PATCH_MARKER = "_qwen3next_cpu_offload_reinit_patch"
_ORIGINAL_ATTR = "_qwen3next_original_may_reinitialize_input_batch"


def _patch_enabled() -> bool:
    value = os.environ.get(_PATCH_ENV, os.environ.get(_LEGACY_PATCH_ENV, "1"))
    value = value.strip().lower()
    return value not in {"0", "false", "no", "off"}


def _patch_gpu_model_runner(module: object) -> None:
    if not _patch_enabled():
        return

    runner_cls = getattr(module, "GPUModelRunner", None)
    if runner_cls is None:
        return

    original = getattr(runner_cls, "may_reinitialize_input_batch", None)
    if original is None or getattr(original, _PATCH_MARKER, False):
        return

    def patched_may_reinitialize_input_batch(
        self: object,
        kv_cache_config: object,
        kernel_block_sizes: list[int],
    ) -> None:
        offload_config = getattr(self, "offload_config", None)
        uva_config = getattr(offload_config, "uva", None)
        cpu_offload_gb = getattr(uva_config, "cpu_offload_gb", 0) or 0

        if cpu_offload_gb <= 0:
            return original(self, kv_cache_config, kernel_block_sizes)

        old_cpu_offload_gb = uva_config.cpu_offload_gb
        uva_config.cpu_offload_gb = 0
        try:
            return original(self, kv_cache_config, kernel_block_sizes)
        finally:
            uva_config.cpu_offload_gb = old_cpu_offload_gb

    setattr(patched_may_reinitialize_input_batch, _PATCH_MARKER, True)
    setattr(runner_cls, _ORIGINAL_ATTR, original)
    runner_cls.may_reinitialize_input_batch = patched_may_reinitialize_input_batch


class _PatchLoader(importlib.abc.Loader):
    def __init__(self, wrapped_loader: importlib.abc.Loader) -> None:
        self._wrapped_loader = wrapped_loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> object | None:
        create_module = getattr(self._wrapped_loader, "create_module", None)
        if create_module is None:
            return None
        return create_module(spec)

    def exec_module(self, module: object) -> None:
        self._wrapped_loader.exec_module(module)
        _patch_gpu_model_runner(module)


class _PatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: object | None,
        target: object | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname != _TARGET_MODULE:
            return None

        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec

        spec.loader = _PatchLoader(spec.loader)
        return spec


if _TARGET_MODULE in sys.modules:
    _patch_gpu_model_runner(sys.modules[_TARGET_MODULE])
else:
    sys.meta_path.insert(0, _PatchFinder())
