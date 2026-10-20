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

    @patch("subject_to_change.embeddings.configure_model_runtime")
    def test_model_load_on_gpu_failure_retries_on_cpu_and_reports_it(self, _configure) -> None:
        from unittest.mock import Mock

        from subject_to_change.device import ComputeDevice

        fixture_model(self.folder)
        runtime = Mock()
        labels = []
        attempts = []

        def create_model(path, **kwargs):
            attempts.append(kwargs["device"])
            if kwargs["device"] == "cuda":
                raise RuntimeError("CUDA out of memory")
            return runtime

        sentence_module = types.SimpleNamespace(SentenceTransformer=create_model)
        torch_module = types.SimpleNamespace(cuda=types.SimpleNamespace(empty_cache=Mock()))
        with patch.dict(
            "sys.modules", {"sentence_transformers": sentence_module, "torch": torch_module}
        ):
            with patch(
                "subject_to_change.embeddings.detect_device",
                return_value=ComputeDevice("cuda", "GPU"),
            ):
                embedder = LocalEmbedder(self.folder, on_device=labels.append)
                embedder._load()
        self.assertEqual(attempts, ["cuda", "cpu"])
        self.assertEqual(embedder.device.name, "cpu")
        self.assertIn("GPU could not load", labels[-1])

    @patch("subject_to_change.embeddings.configure_model_runtime")
    def test_runtime_is_local_only_and_uses_gemma_retrieval_prompts(self, _configure) -> None:
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
        self.assertEqual(calls[1][0], ["rent"])
        self.assertEqual(calls[1][1]["prompt_name"], "document")
        self.assertEqual(calls[2][0], ["lease"])
        self.assertEqual(calls[2][1]["prompt_name"], "query")
        self.assertNotIn("config_kwargs", calls[0][1])


if __name__ == "__main__":
    unittest.main()
