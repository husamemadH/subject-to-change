from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from subject_to_change.cli import main
from subject_to_change.config import Settings


class ConfigAndCLITests(unittest.TestCase):
    def test_settings_preserve_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            Settings(["/home/example/وثائق"]).save(path)
            self.assertEqual(Settings.load(path).folders, ["/home/example/وثائق"])

    def test_corrupt_settings_are_reported_instead_of_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text('{"folders": false}')
            with self.assertRaisesRegex(ValueError, "Cannot read settings"):
                Settings.load(path)
            self.assertEqual(path.read_text(), '{"folders": false}')

    def test_cli_end_to_end_without_qt_or_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "data"
            root = Path(temp) / "files"
            root.mkdir()
            (root / "invoice.txt").write_text("Invoice REF-456 is due.")
            args = ["--data-dir", str(data)]
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([*args, "index", "--keyword-only", str(root)]), 0)
            self.assertEqual(json.loads(output.getvalue())["indexed"], 1)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([*args, "search", "--keyword-only", "REF-456"]), 0)
            self.assertEqual(len(json.loads(output.getvalue())), 1)
            self.assertFalse((data / "models").exists())
