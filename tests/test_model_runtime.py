import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from subject_to_change.model_runtime import configure_model_runtime


class ModelRuntimeTests(unittest.TestCase):
    def runtime(self, initialized):
        original = Mock(return_value=True)
        utilities = ModuleType("transformers.utils")
        utilities.import_utils = SimpleNamespace(is_cuda_stream_capturing=original)
        torch = SimpleNamespace(cuda=SimpleNamespace(is_initialized=lambda: initialized))
        return original, utilities, {"torch": torch, "transformers.utils": utilities}

    def test_cpu_never_enters_native_cuda_capture_query(self):
        original, utilities, modules = self.runtime(False)
        with patch.dict("sys.modules", modules):
            configure_model_runtime()
            self.assertFalse(utilities.import_utils.is_cuda_stream_capturing())
        original.assert_not_called()

    def test_initialized_gpu_preserves_capture_detection(self):
        original, utilities, modules = self.runtime(True)
        with patch.dict("sys.modules", modules):
            configure_model_runtime()
            self.assertTrue(utilities.import_utils.is_cuda_stream_capturing())
        original.assert_called_once_with()

    def test_repeated_model_loads_keep_one_guard(self):
        _, utilities, modules = self.runtime(False)
        with patch.dict("sys.modules", modules):
            configure_model_runtime()
            guard = utilities.import_utils.is_cuda_stream_capturing
            configure_model_runtime()
            self.assertIs(utilities.import_utils.is_cuda_stream_capturing, guard)
