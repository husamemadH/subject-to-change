"""Compatibility guards for local inference runtimes."""

from __future__ import annotations

from functools import wraps


def configure_model_runtime() -> None:
    import torch
    from transformers.utils import import_utils

    original = import_utils.is_cuda_stream_capturing
    if getattr(original, "_subject_to_change_guard", False) is True:
        return

    @wraps(original)
    def is_cuda_stream_capturing():
        # PyTorch's CUDA 13 Windows wheel can access-violate on CPU-only machines.
        # Python exception handling cannot recover from that native crash.
        return torch.cuda.is_initialized() and original()

    is_cuda_stream_capturing._subject_to_change_guard = True
    import_utils.is_cuda_stream_capturing = is_cuda_stream_capturing
