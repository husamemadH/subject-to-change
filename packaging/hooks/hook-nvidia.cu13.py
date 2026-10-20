"""Collect CUDA 13 toolkit wheels, whose layout differs from nvidia-*-cu12 wheels."""

from PyInstaller.utils.hooks import PY_DYLIB_PATTERNS, collect_dynamic_libs

binaries = collect_dynamic_libs("nvidia.cu13", search_patterns=PY_DYLIB_PATTERNS + ["lib*.so.*"])
bindepend_symlink_suppression = ["**/nvidia/cu13/lib/lib*.so*"]
