from __future__ import annotations

import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from subject_to_change.device import ComputeDevice, detect_device
from subject_to_change.embeddings import MODEL, LocalEmbedder


def torch_fixture(cuda=False, mps=False):
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=Mock(return_value=cuda),
            get_device_name=Mock(return_value="Test GPU"),
        ),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)),
        version=types.SimpleNamespace(cuda="13.0", hip=None),
        ones=MagicMock(),
    )


class DeviceTests(unittest.TestCase):
    def test_compatible_cuda_gpu_is_preferred(self):
        torch = torch_fixture(cuda=True)
        with patch.dict("sys.modules", {"torch": torch}):
            result = detect_device()
        self.assertEqual(result, ComputeDevice("cuda", "GPU · Test GPU"))
        torch.ones.assert_called_once_with((2, 2), device="cuda")

    def test_cpu_is_used_without_gpu(self):
        with patch.dict("sys.modules", {"torch": torch_fixture()}):
            result = detect_device()
        self.assertEqual(result.name, "cpu")
        self.assertIn("no compatible GPU", result.label)

    def test_unusable_gpu_falls_back_to_cpu(self):
        torch = torch_fixture(cuda=True)
        torch.ones.side_effect = RuntimeError("CUDA driver unavailable")
        with patch.dict("sys.modules", {"torch": torch}):
            result = detect_device()
        self.assertEqual(result.name, "cpu")
        self.assertIn("CUDA driver unavailable", result.label)

    def test_apple_metal_is_used_when_available(self):
        with patch.dict("sys.modules", {"torch": torch_fixture(mps=True)}):
            self.assertEqual(detect_device().name, "mps")

    def test_missing_runtime_does_not_break_keyword_mode(self):
        with patch.dict("sys.modules", {"torch": None}):
            self.assertEqual(detect_device().name, "cpu")

    def test_out_of_memory_retries_on_cpu(self):
        embedder = LocalEmbedder(Path("unused"))
        embedder.device = ComputeDevice("cuda", "GPU")
        embedder._model = Mock()
        embedder._model.encode.side_effect = RuntimeError("CUDA out of memory")
        row = Mock()
        row.tolist.return_value = [1.0] + [0.0] * (MODEL.dimensions - 1)
        cpu_model = Mock()
        cpu_model.encode.return_value = [row]

        def fallback(reason):
            embedder.device = ComputeDevice("cpu", f"CPU · {reason}")
            embedder._model = cpu_model

        with patch.object(embedder, "_fallback_to_cpu", side_effect=fallback) as move:
            vectors = embedder.encode(["a query"], query=True)
        move.assert_called_once_with("GPU ran out of memory")
        self.assertEqual(embedder.device.name, "cpu")
        self.assertEqual(len(vectors[0]), 768)
        self.assertEqual(cpu_model.encode.call_args.kwargs["prompt_name"], "query")

    def test_unrelated_inference_error_is_not_hidden(self):
        embedder = LocalEmbedder(Path("unused"))
        embedder.device = ComputeDevice("cuda", "GPU")
        embedder._model = Mock()
        embedder._model.encode.side_effect = RuntimeError("Invalid model output")
        with patch.object(embedder, "_fallback_to_cpu") as fallback:
            with self.assertRaisesRegex(RuntimeError, "Invalid model output"):
                embedder.encode(["a query"])
        fallback.assert_not_called()
