"""Exercise the packaged launcher and its actual helper processes without model downloads."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import traceback
import wave
from pathlib import Path


def main(result_path: Path, *, media: bool = False) -> int:
    result = {}
    window = None
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QApplication

        from .config import AppPaths
        from .storage import Store
        from .ui import MainWindow

        app = QApplication.instance() or QApplication([])

        def wait_for(predicate, timeout=60):
            deadline = time.monotonic() + timeout
            while not predicate():
                app.processEvents()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Packaged helper did not finish")
                time.sleep(0.01)
            app.processEvents()

        with tempfile.TemporaryDirectory(prefix="subject-to-change-package-") as directory:
            root = Path(directory)
            inputs = root / "資料 and spaces"
            inputs.mkdir()
            (inputs / "فاتورة.txt").write_text("Invoice PACKAGE-456 is due.", encoding="utf-8")
            paths = AppPaths(root / "data")
            window = MainWindow(paths)
            window.settings.folders = [str(inputs)]
            window._index()
            wait_for(lambda: window.task is None)
            assert window._last_report is not None, window.error.text()
            assert window._last_report.indexed == 1, window.status.text()
            result["indexing"] = True
            window.query.setText("PACKAGE-456")
            window._search()
            wait_for(lambda: window.task is None)
            assert window.result_list.count() == 1, window.error.text()
            result["search"] = True
            (inputs / "new.txt").write_text("A second document to index", encoding="utf-8")
            with Store(paths.database) as store:
                store.connection.execute("BEGIN IMMEDIATE")
                window._index()
                # The child is blocked on the database; Stop must not wait for its timeout.
                until = time.monotonic() + 0.5
                while time.monotonic() < until:
                    app.processEvents()
                    time.sleep(0.01)
                assert window.task is not None
                started = time.monotonic()
                window._cancel()
                wait_for(lambda: window.task is None, timeout=3)
                result["stop_seconds"] = round(time.monotonic() - started, 3)
                assert window._last_report.cancelled
                store.connection.rollback()
            window._index()
            wait_for(lambda: window.task is None)
            assert window._last_report.unchanged == 1, window.status.text()
            result["resume"] = True
            wait_for(lambda: window.device_probe.state() == QProcess.ProcessState.NotRunning)
            result["device"] = window.device_label.text()
            assert result["device"].startswith(("CPU", "GPU")), result["device"]
            if media:
                from PIL import Image

                from .runtime import helper_command, process_options

                image_path = root / "picture.png"
                Image.new("RGB", (64, 32), "blue").save(image_path)
                audio = root / "sound.wav"
                with wave.open(str(audio), "wb") as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(16000)
                    stream.writeframes(b"\0\0" * 32000)
                video = root / "clip.mp4"
                external_environment = dict(os.environ)
                if "LD_LIBRARY_PATH_ORIG" in external_environment:
                    external_environment["LD_LIBRARY_PATH"] = external_environment[
                        "LD_LIBRARY_PATH_ORIG"
                    ]
                else:
                    external_environment.pop("LD_LIBRARY_PATH", None)
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "color=c=blue:s=64x32:r=1:d=2",
                        "-c:v",
                        "mpeg4",
                        "-y",
                        str(video),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    env=external_environment,
                    **process_options(),
                )
                request, response = root / "media.json", root / "media-result.json"
                request.write_text(
                    json.dumps([str(image_path), str(audio), str(video)]), encoding="utf-8"
                )
                checked = subprocess.run(
                    helper_command("media", request, response),
                    timeout=60,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    **process_options(),
                )
                decoded = json.loads(response.read_text(encoding="utf-8"))
                assert checked.returncode == 0 and decoded["ok"], decoded
                assert all(decoded[kind] == 1 for kind in ("image", "audio", "video")), decoded
                result["media_decoding"] = True
                if model_folder := os.environ.get("SUBJECT_TO_CHANGE_SMOKE_MODEL"):
                    from .embeddings import MARKER, REQUIRED_FILES

                    for filename in (*REQUIRED_FILES, MARKER):
                        source = Path(model_folder) / filename
                        target = paths.model / filename
                        target.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            os.link(source.resolve(), target)
                        except OSError:
                            shutil.copyfile(source, target)
                    for source in (image_path, audio, video):
                        shutil.copyfile(source, inputs / source.name)
                    window.keyword_only.setChecked(False)
                    window._last_report = None
                    window._index()
                    wait_for(lambda: window.task is None, timeout=600)
                    report = window._last_report
                    assert report is not None, window.error.text()
                    assert report.indexed == 5 and report.skipped == 0, report
                    result["semantic_indexing"] = True
                    window.query.setText("An invoice that is due for payment")
                    window._search()
                    wait_for(lambda: window.task is None, timeout=180)
                    assert window.result_list.count() > 0, window.error.text()
                    result["semantic_search"] = True
                    result["inference_device"] = window.device_label.text()
            window.close()
            window = None
        result["ok"] = True
        code = 0
    except Exception:
        result["ok"] = False
        result["error"] = traceback.format_exc()
        code = 1
    finally:
        if window is not None:
            window.close()
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return code
