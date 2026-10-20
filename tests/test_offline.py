from __future__ import annotations

import hashlib
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from subject_to_change.config import write_json
from subject_to_change.embeddings import (
    MARKER,
    MODEL,
    REQUIRED_FILES,
    LocalEmbedder,
    ModelUnavailable,
    model_ready,
    verify_model,
)


def fixture_model(folder: Path) -> None:
    files = {}
    for name in REQUIRED_FILES:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b"test fixture, not model weights"
        path.write_bytes(data)
        files[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    write_json(folder / MARKER, {"identity": MODEL.identity, "files": files})


class OfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "model"

    def test_missing_model_does_not_download(self) -> None:
        embedder = LocalEmbedder(self.folder)
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            with self.assertRaises(ModelUnavailable):
                embedder.encode(["a query"], query=True)
        self.assertFalse(self.folder.exists())

    def test_partial_download_is_not_ready(self) -> None:
        fixture_model(self.folder)
        (self.folder / "tokenizer.json").unlink()
        self.assertFalse(model_ready(self.folder))

    def test_modified_weights_are_detected_before_runtime_load(self) -> None:
        fixture_model(self.folder)
        path = self.folder / "model.safetensors"
        path.write_bytes(b"x" * path.stat().st_size)
        self.assertTrue(model_ready(self.folder))
        with self.assertRaisesRegex(ModelUnavailable, "damaged"):
            verify_model(self.folder)

    def test_runtime_is_local_only_and_uses_e5_prefixes(self) -> None:
        fixture_model(self.folder)
        calls = []

        class Row:
            def tolist(self):
                return [1.0] + [0.0] * (MODEL.dimensions - 1)

        class Runtime:
            def __init__(self, path, **kwargs):
                calls.append((path, kwargs))

            def encode(self, texts, **kwargs):
                calls.append((texts, kwargs))
                return [Row() for _ in texts]

        module = types.SimpleNamespace(SentenceTransformer=Runtime)
        with patch.dict("sys.modules", {"sentence_transformers": module}):
            with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
                model = LocalEmbedder(self.folder)
                model.encode(["rent"])
                model.encode(["lease"], query=True)
        self.assertEqual(calls[0][0], str(self.folder))
        self.assertTrue(calls[0][1]["local_files_only"])
        self.assertFalse(calls[0][1]["trust_remote_code"])
        self.assertTrue(calls[0][1]["model_kwargs"]["use_safetensors"])
        self.assertEqual(calls[1][0], ["passage: rent"])
        self.assertEqual(calls[2][0], ["query: lease"])


if __name__ == "__main__":
    unittest.main()
