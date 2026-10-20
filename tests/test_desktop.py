from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from subject_to_change.desktop import reveal_file


class DesktopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "فاتورة with spaces, comma.txt"
        self.path.write_text("invoice")

    def test_linux_uses_standard_file_manager_interface(self) -> None:
        fallback = Mock(return_value=True)
        with patch("subject_to_change.desktop.sys.platform", "linux"):
            with patch("subject_to_change.desktop.subprocess.run") as run:
                self.assertTrue(reveal_file(self.path, fallback))
        args = run.call_args.args[0]
        self.assertIn("org.freedesktop.FileManager1.ShowItems", args)
        self.assertIn(f"array:string:{self.path.resolve().as_uri()}", args)
        self.assertNotIn("shell", run.call_args.kwargs)
        fallback.assert_not_called()

    def test_missing_dbus_falls_back_to_containing_folder(self) -> None:
        fallback = Mock(return_value=True)
        with patch("subject_to_change.desktop.sys.platform", "linux"):
            with patch("subject_to_change.desktop.subprocess.run", side_effect=FileNotFoundError):
                self.assertTrue(reveal_file(self.path, fallback))
        fallback.assert_called_once_with(self.path.resolve().parent)

    def test_file_manager_timeout_falls_back(self) -> None:
        fallback = Mock(return_value=False)
        with patch("subject_to_change.desktop.sys.platform", "linux"):
            with patch(
                "subject_to_change.desktop.subprocess.run",
                side_effect=subprocess.TimeoutExpired("dbus-send", 3),
            ):
                self.assertFalse(reveal_file(self.path, fallback))

    def test_windows_uses_explorer(self) -> None:
        fallback = Mock()
        with patch("subject_to_change.desktop.sys.platform", "win32"):
            with patch("subject_to_change.desktop.subprocess.Popen") as launch:
                self.assertTrue(reveal_file(self.path, fallback))
        self.assertEqual(launch.call_args.args[0][0], "explorer.exe")
        self.assertTrue(launch.call_args.args[0][1].startswith("/select,"))
        fallback.assert_not_called()
