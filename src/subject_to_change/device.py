"""Choose a working accelerator without loading the embedding model."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ComputeDevice:
    name: str
    label: str


def detect_device() -> ComputeDevice:
    try:
        import torch
    except (ImportError, OSError):
        return ComputeDevice("cpu", "CPU · model runtime not installed")
    try:
        if torch.cuda.is_available():
            # Availability alone does not guarantee this wheel supports the installed GPU.
            sample = torch.ones((2, 2), device="cuda")
            (sample @ sample).sum().item()
            return ComputeDevice("cuda", f"GPU · {torch.cuda.get_device_name(0)}")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            sample = torch.ones((2, 2), device="mps")
            (sample @ sample).sum().item()
            return ComputeDevice("mps", "GPU · Apple Metal")
    except RuntimeError as error:
        return ComputeDevice("cpu", f"CPU · GPU unavailable: {str(error).splitlines()[0]}")
    if torch.version.cuda is None and torch.version.hip is None:
        return ComputeDevice("cpu", "CPU · GPU support unavailable in this PyTorch installation")
    return ComputeDevice("cpu", "CPU · no compatible GPU detected")


if __name__ == "__main__":
    print(json.dumps(asdict(detect_device())))
