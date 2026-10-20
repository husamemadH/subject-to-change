from __future__ import annotations

import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from subject_to_change.config import AppPaths
        from subject_to_change.ui import MainWindow

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = MainWindow(AppPaths(Path(self.temp.name) / "data"))
        self.addCleanup(self.window.close)

    def wait_for_task(self) -> None:
        deadline = time.monotonic() + 10
        while self.window.task is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNone(self.window.task, "Background task did not finish")

    def test_first_launch_is_keyword_only_without_downloads(self) -> None:
        self.assertTrue(self.window.keyword_only.isChecked())
        self.assertFalse(self.window.paths.model.exists())
        self.assertFalse(self.window.open_button.isEnabled())
        self.assertTrue(self.window.download_button.isEnabled())

    def test_index_and_search_through_background_workers(self) -> None:
        root = Path(self.temp.name) / "files"
        root.mkdir()
        (root / "invoice.txt").write_text("Invoice REF-456 is due.")
        self.window.settings.folders = [str(root)]
        self.window._index()
        self.wait_for_task()
        self.window.query.setText("REF-456")
        self.window._search()
        self.wait_for_task()
        self.assertEqual(self.window.result_list.count(), 1)
        self.assertIn("Invoice REF-456", self.window.preview.toPlainText())
        self.assertTrue(self.window.open_button.isEnabled())
        self.assertFalse(self.window.paths.model.exists())
