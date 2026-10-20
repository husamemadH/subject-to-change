from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from subject_to_change.runtime import helper_command, process_options


class RuntimeTests(unittest.TestCase):
    def test_frozen_helpers_relaunch_executable_without_python_flags(self):
        executable = r"C:\Program Files\Subject to Change\subject-to-change.exe"
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", executable),
        ):
            command = helper_command(
                "index", Path("folder with spaces/request.json"), "events", "errors"
            )
        self.assertEqual(command[0], executable)
        self.assertEqual(command[1], "--index-worker")
        self.assertNotIn("-m", command)
        self.assertEqual(command[2], str(Path("folder with spaces/request.json")))

    def test_source_helpers_use_dispatcher(self):
        with (
            patch.object(sys, "frozen", False, create=True),
            patch.object(sys, "platform", "linux"),
        ):
            command = helper_command("device", "result.json")
        self.assertEqual(
            command,
            [sys.executable, "-m", "subject_to_change.launch", "--device-worker", "result.json"],
        )

    def test_windows_helpers_have_no_console_window(self):
        with (
            patch.object(sys, "platform", "win32"),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
        ):
            self.assertEqual(process_options(), {"creationflags": 0x08000000})

    def test_indexing_helper_works_without_standard_streams(self):
        from subject_to_change.config import AppPaths
        from subject_to_change.engine import Engine

        with tempfile.TemporaryDirectory(prefix="subject to change ") as directory:
            base = Path(directory)
            inputs = base / "資料 with spaces"
            inputs.mkdir()
            (inputs / "فاتورة.txt").write_text("Invoice WINDOWS-123", encoding="utf-8")
            paths = AppPaths(base / "data")
            request, events, errors = (
                base / name for name in ("request.json", "events.jsonl", "errors.log")
            )
            request.write_text(
                json.dumps({"data": str(paths.data), "folders": [str(inputs)], "semantic": False}),
                encoding="utf-8",
            )
            code = (
                "import sys; from subject_to_change.launch import main; "
                "sys.stdin = sys.stdout = sys.stderr = None; "
                "raise SystemExit(main(sys.argv[1:]))"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    code,
                    "--index-worker",
                    str(request),
                    str(events),
                    str(errors),
                ],
                timeout=10,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            messages = [
                json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(messages[-1]["kind"], "succeeded")
            self.assertEqual(messages[-1]["value"]["indexed"], 1)
            self.assertEqual(len(Engine(paths).search("WINDOWS-123", semantic=False)), 1)

    def test_device_helper_has_no_console_or_gui_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "device.json"
            code = (
                "import sys; from subject_to_change.launch import main; "
                "sys.modules['torch'] = None; "
                "sys.stdin = sys.stdout = sys.stderr = None; "
                "result = main(sys.argv[1:]); "
                "assert 'subject_to_change.ui' not in sys.modules; "
                "raise SystemExit(result)"
            )
            result = subprocess.run(
                [sys.executable, "-c", code, "--device-worker", str(output)],
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["name"], "cpu")

    def test_windows_keeps_bundled_dll_directory_registered(self):
        import os
        from unittest.mock import Mock

        from subject_to_change import runtime

        with tempfile.TemporaryDirectory() as directory:
            libraries = Path(directory) / "ffmpeg"
            libraries.mkdir()
            (libraries / "avutil-61.dll").touch()
            handle = Mock()
            loaded = Mock()
            with (
                patch.object(sys, "platform", "win32"),
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "_MEIPASS", directory, create=True),
                patch.object(runtime, "_DLL_HANDLES", []),
                patch.object(runtime, "_DLL_PATHS", set()),
                patch.object(runtime.shutil, "which", return_value=None),
                patch.object(runtime.ctypes, "CDLL", return_value=loaded) as load,
                patch.dict(os.environ, {"PATH": "old-path", "SUBJECT_TO_CHANGE_FFMPEG_DIR": ""}),
                patch.object(os, "add_dll_directory", return_value=handle, create=True) as register,
            ):
                runtime.configure_native_libraries()
                runtime.configure_native_libraries()
                register.assert_called_once_with(str(libraries.resolve()))
                load.assert_called_once_with(str((libraries / "avutil-61.dll").resolve()))
                self.assertEqual(runtime._DLL_HANDLES, [handle, loaded])
                self.assertTrue(
                    os.environ["PATH"].startswith(str(libraries.resolve()) + os.pathsep)
                )
                handle.close.assert_not_called()
