from __future__ import annotations

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


@dataclass(frozen=True)
class ModelSpec:
    repository: str = "intfloat/multilingual-e5-small"
    revision: str = "fd1525a9fd15316a2d503bf26ab031a61d056e98"
    dimensions: int = 384

    @property
    def identity(self) -> str:
        return f"{self.repository}@{self.revision}:e5-prefixes:v1"


MODEL = ModelSpec()
REQUIRED_FILES = (
    "config.json",
    "modules.json",
    "1_Pooling/config.json",
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
    progress("Downloading the text model (about 500 MB). An interrupted download can be retried.")
    snapshot_download(
        repo_id=MODEL.repository,
        revision=MODEL.revision,
        local_dir=folder,
        allow_patterns=[
            "*.json",
            "*.safetensors",
            "sentencepiece.bpe.model",
            "1_Pooling/*.json",
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

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._model = None
        self._lock = Lock()

    def _load(self) -> None:
        verify_model(self.folder)
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["DO_NOT_TRACK"] = "1"
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise ModelUnavailable(
                "Install model support with: pip install -e '.[model]'"
            ) from error
        self._model = SentenceTransformer(
            str(self.folder),
            device="cpu",
            local_files_only=True,
            trust_remote_code=False,
            model_kwargs={"use_safetensors": True},
        )
        self._model.max_seq_length = 512

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            if self._model is None:
                self._load()
            prefix = "query: " if query else "passage: "
            vectors = self._model.encode(
                [prefix + text for text in texts],
                batch_size=16,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return [normalize(vector.tolist(), self.dimensions) for vector in vectors]
