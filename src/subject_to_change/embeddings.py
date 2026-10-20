from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

from .config import write_json
from .device import ComputeDevice, detect_device


@dataclass(frozen=True)
class ModelSpec:
    repository: str = "google/embeddinggemma-2"
    revision: str = "914f7f89142e33e77833254d9c9b90c3cef7303b"
    dimensions: int = 768

    @property
    def identity(self) -> str:
        return f"{self.repository}@{self.revision}:retrieval-prompts:v1"


MODEL = ModelSpec()
REQUIRED_FILES = (
    "config.json",
    "modules.json",
    "1_Pooling/config.json",
    "2_Normalize/config.json",
    "config_sentence_transformers.json",
    "preprocessor_config.json",
    "processor_config.json",
    "chat_template.jinja",
    "tokenizer.model",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "sentence_bert_config.json",
)
MARKER = "subject-to-change-model.json"


class ModelUnavailable(RuntimeError):
    pass


class Embedder(Protocol):
    identity: str
    dimensions: int

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...

    def encode_media(self, inputs: dict) -> list[float]: ...


def normalize(vector: list[float], dimensions: int) -> list[float]:
    if len(vector) != dimensions or not all(math.isfinite(value) for value in vector):
        raise ValueError("The model returned an invalid embedding")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("The model returned an empty embedding")
    return [value / norm for value in vector]


def model_ready(folder: Path) -> bool:
    try:
        info = json.loads((folder / MARKER).read_text(encoding="utf-8"))
        return info["identity"] == MODEL.identity and all(
            (folder / name).is_file()
            and (folder / name).stat().st_size > 0
            and (folder / name).stat().st_size == info["files"][name]["size"]
            and re.fullmatch(r"[0-9a-f]{64}", info["files"][name]["sha256"]) is not None
            for name in REQUIRED_FILES
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify_model(folder: Path) -> None:
    if not model_ready(folder):
        raise ModelUnavailable("Model not installed. Choose Download model in the app first.")
    info = json.loads((folder / MARKER).read_text(encoding="utf-8"))
    for name, expected in info["files"].items():
        # Only files explicitly included by this application can be verified or loaded.
        if name not in REQUIRED_FILES:
            continue
        with (folder / name).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected["sha256"]:
            raise ModelUnavailable(
                "A model file is damaged. Download the model again to repair it."
            )


def download_model(folder: Path, progress: Callable[[str], None] = lambda _: None) -> None:
    """The only application operation allowed to access the network. Explicit user action only."""
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    try:
        from huggingface_hub import snapshot_download
    except ImportError as error:
        raise ModelUnavailable("Install model support with: pip install -e '.[model]'") from error
    folder.mkdir(parents=True, exist_ok=True)
    repair = (folder / MARKER).exists()
    (folder / MARKER).unlink(missing_ok=True)
    progress("Downloading the text model (about 1.5 GB). An interrupted download can be retried.")
    snapshot_download(
        repo_id=MODEL.repository,
        revision=MODEL.revision,
        local_dir=folder,
        allow_patterns=[
            "*.json",
            "*.safetensors",
            "tokenizer.model",
            "chat_template.jinja",
            "1_Pooling/*.json",
            "2_Normalize/*.json",
        ],
        token=False,
        max_workers=2,
        force_download=repair,
    )
    progress("Checking model files…")
    files = {}
    for name in REQUIRED_FILES:
        path = folder / name
        with path.open("rb") as stream:
            files[name] = {
                "size": path.stat().st_size,
                "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
            }
    write_json(folder / MARKER, {"identity": MODEL.identity, "files": files})
    progress("Model saved. Indexing and search now work offline.")


class LocalEmbedder:
    identity = MODEL.identity
    dimensions = MODEL.dimensions

    def __init__(self, folder: Path, on_device: Callable[[str], None] = lambda _: None) -> None:
        self.folder = folder
        self.on_device = on_device
        self.device: ComputeDevice | None = None
        self._model = None
        self._lock = Lock()

    def _load(self) -> None:
        verify_model(self.folder)
        self.device = self.device or detect_device()
        self.on_device(self.device.label)
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["DO_NOT_TRACK"] = "1"
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise ModelUnavailable(
                "Install model support with: pip install -e '.[model]'"
            ) from error
        try:
            self._model = SentenceTransformer(
                str(self.folder),
                device=self.device.name,
                local_files_only=True,
                trust_remote_code=False,
                model_kwargs={"use_safetensors": True, "dtype": "float32"},
            )
        except RuntimeError:
            if self.device.name == "cpu":
                raise
            self._fallback_to_cpu("GPU could not load the model")
            return
        self._model.max_seq_length = 8192

    def _fallback_to_cpu(self, reason: str) -> None:
        self._model = None
        gc.collect()
        import torch

        if self.device is not None and self.device.name == "cuda":
            try:
                torch.cuda.empty_cache()
            except RuntimeError:
                pass  # A failed GPU must not prevent the CPU retry.
        self.device = ComputeDevice("cpu", f"CPU · {reason}")
        self._load()

    def _encode(self, inputs, **kwargs):
        try:
            return self._model.encode(inputs, **kwargs)
        except RuntimeError as error:
            if self.device is None or self.device.name == "cpu":
                raise
            message = str(error).lower()
            if not any(
                term in message for term in ("out of memory", "cuda", "cudnn", "hip", "mps")
            ):
                raise
            reason = (
                "GPU ran out of memory" if "out of memory" in message else "GPU inference failed"
            )
            self._fallback_to_cpu(reason)
            return self._model.encode(inputs, **kwargs)

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            if self._model is None:
                self._load()
            vectors = self._encode(
                texts,
                prompt_name="query" if query else "document",
                batch_size=16,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return [normalize(vector.tolist(), self.dimensions) for vector in vectors]

    def encode_media(self, inputs: dict) -> list[float]:
        with self._lock:
            if self._model is None:
                self._load()
            vectors = self._encode(
                [inputs],
                prompt="",
                batch_size=1,
                normalize_embeddings=True,
                show_progress_bar=False,
                processing_kwargs={
                    "video": {"max_frames": 10, "fps": None, "max_soft_tokens": 140},
                },
            )
            return normalize(vectors[0].tolist(), self.dimensions)
