"""Install the verified desktop runtime, then launch it. No user files are indexed here."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import traceback
import urllib.request
from pathlib import Path

from PySide6.QtCore import QLockFile, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

BUNDLE = Path(__file__).resolve().parent


class Cancelled(Exception):
    pass


class RuntimeSetup:
    def __init__(self, manifest, cache: Path, *, assets: Path | None = None, progress=None):
        self.manifest = manifest
        self.cache = cache
        self.assets = assets
        self.progress = progress or (lambda *args: None)
        self.cancel = threading.Event()
        identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
        self.target = cache / f"{manifest['version']}-{identity}"

    @property
    def executable(self) -> Path:
        return self.target / self.manifest["executable"]

    def check_cancelled(self):
        if self.cancel.is_set():
            raise Cancelled()

    def ready(self):
        return (self.target / ".ready").is_file() and self.executable.is_file()

    def install(self) -> Path:
        self.cache.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.target) + ".lock")
        lock.setStaleLockTime(0)
        while not lock.tryLock(0):
            self.progress("Waiting for the other setup window…", 0, 0)
            if self.cancel.wait(0.2):
                raise Cancelled()
        try:
            if self.ready():
                return self.executable
            needed = (
                self.manifest["unpacked_size"]
                + max(part["size"] for part in self.manifest["parts"])
                + 256 * 1024**2
            )
            if shutil.disk_usage(self.cache).free < needed:
                raise OSError(f"Setup needs {needed / 1024**3:.1f} GB of free disk space.")
            with tempfile.TemporaryDirectory(prefix=".install-", dir=self.cache) as directory:
                stage = Path(directory)
                application = stage / "application"
                application.mkdir()
                with PartReader(self, stage) as reader:
                    with tarfile.open(fileobj=reader, mode="r|gz") as archive:
                        done = 0
                        for member in archive:
                            self.check_cancelled()
                            archive.extract(member, application, filter="data")
                            done += member.size
                            self.progress(
                                "Installing local search runtime…",
                                done,
                                self.manifest["unpacked_size"],
                            )
                    while reader.read(64 * 1024):
                        pass
                if not (application / self.manifest["executable"]).is_file():
                    raise RuntimeError("The runtime archive is incomplete.")
                self.check_cancelled()
                (application / ".ready").write_text("verified\n", encoding="utf-8")
                if self.target.exists():
                    shutil.rmtree(self.target)
                application.rename(self.target)
            return self.executable
        finally:
            lock.unlock()


class PartReader(io.RawIOBase):
    """Verify each downloaded part before exposing it to the streaming extractor."""

    def __init__(self, setup: RuntimeSetup, stage: Path):
        self.setup = setup
        self.stage = stage
        self.parts = iter(enumerate(setup.manifest["parts"], 1))
        self.stream = None
        self.download = stage / "runtime.part"

    def next_part(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
            self.download.unlink(missing_ok=True)
        item = next(self.parts, None)
        if item is None:
            return False
        index, part = item
        count = len(self.setup.manifest["parts"])
        self.setup.check_cancelled()
        source = (
            (self.setup.assets / part["name"]).resolve().as_uri()
            if self.setup.assets is not None
            else part["url"]
        )
        request = urllib.request.Request(source, headers={"User-Agent": "Subject-to-Change-Setup"})
        digest = hashlib.sha256()
        done = 0
        with urllib.request.urlopen(request, timeout=5) as incoming:
            with self.download.open("wb") as outgoing:
                read = getattr(incoming, "read1", incoming.read)
                while block := read(64 * 1024):
                    self.setup.check_cancelled()
                    done += len(block)
                    if done > part["size"]:
                        raise RuntimeError("Runtime download has an unexpected size.")
                    digest.update(block)
                    outgoing.write(block)
                    self.setup.progress(
                        f"Downloading runtime · part {index} of {count}…", done, part["size"]
                    )
        if done != part["size"] or digest.hexdigest() != part["sha256"]:
            raise RuntimeError("Runtime checksum verification failed. Please retry setup.")
        self.stream = self.download.open("rb")
        return True

    def read(self, size=-1):
        if size < 0:
            raise ValueError("Streaming extraction requires a bounded read")
        blocks = []
        while size:
            self.setup.check_cancelled()
            if self.stream is None and not self.next_part():
                break
            block = self.stream.read(size)
            if block:
                blocks.append(block)
                size -= len(block)
            elif not self.next_part():
                break
        return b"".join(blocks)

    def close(self):
        if self.stream is not None:
            self.stream.close()
        super().close()


def cache_folder() -> Path:
    if override := os.environ.get("SUBJECT_TO_CHANGE_RUNTIME_CACHE"):
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "subject-to-change/runtime"


def launch(executable: Path, arguments: list[str], *, wait=False) -> int:
    environment = dict(os.environ)
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if "LD_LIBRARY_PATH_ORIG" in environment:
        environment["LD_LIBRARY_PATH"] = environment["LD_LIBRARY_PATH_ORIG"]
    else:
        environment.pop("LD_LIBRARY_PATH", None)
    command = [str(executable), *arguments]
    if wait:
        return subprocess.run(command, env=environment).returncode
    subprocess.Popen(command, env=environment, close_fds=True)
    return 0


class SetupTask(QThread):
    progress = Signal(str, int, int)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, setup):
        super().__init__()
        self.setup = setup
        # Qt's int signals are 32-bit; use percentages rather than multi-GB byte counts.
        self.setup.progress = lambda text, done, total: self.progress.emit(
            text, int(100 * done / total) if total else 0, 100 if total else 0
        )

    def run(self):
        try:
            self.succeeded.emit(str(self.setup.install()))
        except Cancelled:
            pass
        except Exception as error:
            self.failed.emit(str(error))


class SetupWindow(QWidget):
    def __init__(self, setup, arguments):
        super().__init__()
        self.setup = setup
        self.arguments = arguments
        self.task = None
        self.closing = False
        self.setWindowTitle("Set up Subject to Change")
        self.resize(490, 220)
        layout = QVBoxLayout(self)
        amount = sum(part["size"] for part in setup.manifest["parts"]) / 1024**3
        self.message = QLabel(
            f"Download the local search runtime ({amount:.1f} GB) once. It includes GPU support, "
            "CPU fallback, and media codecs. Your existing model and files stay on this PC."
        )
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.install = QPushButton("Download and set up")
        self.install.clicked.connect(self.start)
        layout.addWidget(self.install)
        self.cancel = QPushButton("Close")
        self.cancel.clicked.connect(self.close)
        layout.addWidget(self.cancel)

    def start(self):
        self.setup.cancel.clear()
        self.task = SetupTask(self.setup)
        self.task.progress.connect(self.update_progress)
        self.task.succeeded.connect(self.complete)
        self.task.failed.connect(self.failed)
        self.task.finished.connect(self.finished)
        self.install.setEnabled(False)
        self.cancel.setText("Cancel setup")
        self.message.setText("Connecting to the runtime download…")
        self.progress.setRange(0, 0)
        self.progress.show()
        self.task.start()

    def update_progress(self, text, done, total):
        self.message.setText(text)
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def complete(self, executable):
        try:
            launch(Path(executable), self.arguments)
            self.closing = True
        except OSError as error:
            self.failed(str(error))

    def failed(self, message):
        self.message.setText(message)
        self.install.setText("Retry setup")

    def finished(self):
        self.task.deleteLater()
        self.task = None
        if self.closing:
            self.close()
        else:
            self.install.setEnabled(True)
            self.cancel.setText("Close")

    def closeEvent(self, event):
        if self.task is not None:
            self.closing = True
            self.setup.cancel.set()
            self.message.setText("Stopping setup…")
            self.cancel.setEnabled(False)
            event.ignore()
            return
        event.accept()


def main():
    manifest = json.loads((BUNDLE / "runtime.json").read_text(encoding="utf-8"))
    assets = os.environ.get("SUBJECT_TO_CHANGE_RUNTIME_ASSETS")
    setup = RuntimeSetup(manifest, cache_folder(), assets=Path(assets) if assets else None)
    arguments = sys.argv[1:]
    if "--smoke-test" in arguments:
        try:
            return launch(setup.install(), arguments, wait=True)
        except Exception:
            report = Path(arguments[arguments.index("--smoke-test") + 1])
            report.write_text(
                json.dumps({"ok": False, "setup_error": traceback.format_exc()}, indent=2),
                encoding="utf-8",
            )
            return 1
    if setup.ready():
        return launch(setup.executable, arguments)
    app = QApplication(sys.argv)
    app.setApplicationName("Subject to Change")
    app.setWindowIcon(QIcon(str(BUNDLE / "icon.svg")))
    window = SetupWindow(setup, arguments)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
