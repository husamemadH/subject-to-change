from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.assertIn("1 / 1 files · 100% · Finished", self.window.indexing_status.text())
        self.assertEqual(self.window.progress_bar.value(), 1)
        self.window.query.setText("REF-456")
        self.window._search()
        self.wait_for_task()
        self.assertEqual(self.window.result_list.count(), 1)
        self.assertIn("Invoice REF-456", self.window.preview.toPlainText())
        self.assertTrue(self.window.open_button.isEnabled())
        self.assertFalse(self.window.paths.model.exists())

    def test_stop_interrupts_blocked_worker_and_preserves_committed_files(self) -> None:
        from subject_to_change.engine import Engine

        root = Path(self.temp.name) / "files"
        root.mkdir()
        (root / "saved.txt").write_text("Invoice REF-456 is due.")
        self.window.settings.folders = [str(root)]
        # Real child process: save a file, then block inside an uncommitted transaction.
        script = r"""
import json, sys, time
from pathlib import Path
from subject_to_change.config import AppPaths
from subject_to_change.engine import Engine
from subject_to_change.storage import Store
request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
paths = AppPaths(Path(request['data']))
Engine(paths).index(request['folders'], semantic=False)
with Store(paths.database) as store:
    store.connection.execute("UPDATE documents SET digest = 'uncommitted'")
    with Path(sys.argv[2]).open('a', encoding='utf-8') as output:
        output.write(json.dumps({'kind': 'progress', 'value': 'Blocked in model'}) + '\n')
        output.flush()
    time.sleep(60)
"""
        real_popen = subprocess.Popen

        def start_worker(command, **kwargs):
            return real_popen([sys.executable, "-c", script, *command[-3:-1]], **kwargs)

        with patch("subject_to_change.ui.subprocess.Popen", side_effect=start_worker):
            self.window._index()
            deadline = time.monotonic() + 5
            while self.window.status.text() != "Blocked in model":
                self.app.processEvents()
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            started = time.monotonic()
            self.window._cancel()
            self.wait_for_task()
            self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(self.window.index_button.isEnabled())
        self.assertIn("Indexing stopped", self.window.status.text())
        self.assertEqual(Engine(self.window.paths).stats()["files"], 1)
        results = Engine(self.window.paths).search("REF-456", semantic=False)
        self.assertEqual(len(results), 1)
        import sqlite3

        with sqlite3.connect(self.window.paths.database) as connection:
            self.assertNotEqual(
                connection.execute("SELECT digest FROM documents").fetchone()[0], "uncommitted"
            )
        # The UI can immediately start another indexing job.
        self.window._index()
        self.wait_for_task()
        self.assertIn("1 unchanged", self.window.status.text())

    def test_image_result_has_visual_preview(self) -> None:
        from PySide6.QtGui import QImage

        from subject_to_change.storage import SearchResult

        path = Path(self.temp.name) / "photo.png"
        image = QImage(64, 32, QImage.Format.Format_RGB32)
        image.fill(0x0000FF)
        image.save(str(path))
        self.window._show_results([SearchResult(str(path), "photo", "Image", 1.0, "Meaning")])
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.image_preview)
        self.assertFalse(self.window.image_preview.pixmap().isNull())
