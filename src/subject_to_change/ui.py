from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import Event

from PySide6.QtCore import QProcess, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QFont,
    QIcon,
    QImageReader,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .config import APP_NAME, AppPaths, Settings, canonical_folders
from .desktop import reveal_file
from .embeddings import LocalEmbedder, download_model, model_ready
from .engine import Engine, IndexProgress, IndexReport
from .readers import media_kind
from .runtime import (
    configure_native_libraries,
    ensure_standard_streams,
    helper_command,
    process_options,
)
from .storage import SearchResult

STYLE = """
QWidget { background: #f7f8f4; color: #202d28; font-size: 14px; }
QMainWindow { background: #f7f8f4; }
QLabel#brand { font-size: 14px; font-weight: 700; color: #345f49; }
QLabel#heading { font-size: 30px; font-weight: 600; }
QLabel#muted { color: #64736a; }
QLabel#status { color: #52645a; }
QLabel#error { color: #a43131; background: #fff0eb; padding: 12px; border-radius: 6px; }
QLabel#model { background: #e8eee6; padding: 10px; border-radius: 6px; }
QFrame#sidebar { background: #edf0e9; border-radius: 12px; }
QFrame#sidebar QLabel { background: transparent; }
QLineEdit { background: white; border: 1px solid #c6d1c6; padding: 14px; border-radius: 8px; }
QLineEdit:focus { border: 1px solid #39754e; }
QPushButton { background: #e5eadf; border: 1px solid #ccd6c8; border-radius: 6px;
              padding: 9px 13px; }
QPushButton:hover { background: #d9e3d3; }
QPushButton#primary { background: #315e43; color: white; border: none; }
QPushButton#primary:hover { background: #254d35; }
QPushButton:disabled { color: #8a958b; background: #e8ece5; }
QListWidget { background: white; border: 1px solid #dbe2d7; border-radius: 8px; padding: 5px; }
QListWidget::item { padding: 13px 8px; border-bottom: 1px solid #edf0e9; }
QListWidget::item:selected { background: #e2eddd; color: #1a3c25; border-radius: 4px; }
QPlainTextEdit { background: white; border: 1px solid #dbe2d7; border-radius: 8px; padding: 12px; }
QProgressBar { border: none; background: #e2e9de; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #527c4d; }
QSplitter::handle { background: transparent; width: 12px; }
"""


class ImagePreview(QLabel):
    def __init__(self):
        super().__init__()
        self.original = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)

    def set_image(self, image):
        self.original = QPixmap.fromImage(image)
        self._fit()

    def _fit(self):
        self.setPixmap(
            self.original.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.original.isNull():
            self._fit()


class Task(QThread):
    progress = Signal(str)
    indexing_progress = Signal(object)
    device_changed = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable, parent=None) -> None:
        super().__init__(parent)
        self.operation = operation
        self.cancel = Event()

    def run(self) -> None:
        try:
            self.succeeded.emit(
                self.operation(self.progress.emit, self.cancel, self.indexing_progress.emit)
            )
        except Exception as error:
            self.failed.emit(str(error))


class IndexTask(Task):
    def __init__(self, paths: AppPaths, folders: list[str], semantic: bool, parent=None):
        super().__init__(lambda *args: None, parent)
        self.request = {"data": str(paths.data), "folders": folders, "semantic": semantic}

    def run(self) -> None:
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix="subject-to-change-worker-") as directory:
                base = Path(directory)
                request, events, errors = (
                    base / name for name in ("request.json", "events.jsonl", "errors.log")
                )
                request.write_text(json.dumps(self.request), encoding="utf-8")
                events.touch()
                with events.open("rb") as output, errors.open("w+b") as diagnostics:
                    try:
                        process = subprocess.Popen(
                            helper_command("index", request, events, errors),
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=diagnostics,
                            **process_options(),
                        )
                        buffer = b""
                        outcome = False
                        while True:
                            if self.cancel.is_set():
                                process.terminate()
                                try:
                                    process.wait(timeout=0.5)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.wait()
                                self.succeeded.emit(IndexReport(cancelled=True))
                                return
                            chunk = output.read(65536)
                            buffer += chunk
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                message = json.loads(line)
                                kind, value = message["kind"], message["value"]
                                if kind == "device":
                                    self.device_changed.emit(value)
                                elif kind == "progress":
                                    self.progress.emit(value)
                                elif kind == "indexing_progress":
                                    self.indexing_progress.emit(IndexProgress(**value))
                                elif kind == "succeeded":
                                    self.succeeded.emit(IndexReport(**value))
                                    outcome = True
                                elif kind == "failed":
                                    self.failed.emit(value)
                                    outcome = True
                            if not chunk:
                                if process.poll() is not None:
                                    # The child may have appended its final event after our read.
                                    chunk = output.read(65536)
                                    if chunk:
                                        output.seek(-len(chunk), 1)
                                        continue
                                    break
                                self.cancel.wait(0.05)
                        if not outcome:
                            diagnostics.seek(0)
                            detail = diagnostics.read().decode(errors="replace")
                            if len(detail) > 12000:
                                detail = detail[:9000] + "\n…\n" + detail[-3000:]
                            self.failed.emit(
                                f"Indexing process exited ({process.returncode})\n{detail}".strip()
                            )
                    finally:
                        if process is not None:
                            if process.poll() is None:
                                process.kill()
                            process.wait()
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    device_changed = Signal(str)

    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self.engine = Engine(paths, LocalEmbedder(paths.model, on_device=self.device_changed.emit))
        self.settings = Settings.load(paths.settings)
        self.task: Task | None = None
        self.task_kind = ""
        self.results: list[SearchResult] = []
        self._closing = False
        self._last_report: IndexReport | None = None
        self.setWindowTitle(APP_NAME)
        self.resize(1120, 760)
        self.setMinimumSize(850, 580)
        self._build()
        self._refresh()
        self._runtime_device_known = False
        self.device_changed.connect(self._set_device)
        self._device_directory = tempfile.TemporaryDirectory(prefix="subject-to-change-device-")
        self._device_result = Path(self._device_directory.name) / "device.json"
        self.device_probe = QProcess(self)
        self.device_probe.finished.connect(self._device_detected)
        command = helper_command("device", self._device_result)
        self.device_probe.start(command[0], command[1:])
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.query.setFocus)
        self.query.setFocus()

    def _build(self) -> None:
        container = QWidget()
        self.setCentralWidget(container)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(16)
        brand = QLabel("SUBJECT TO CHANGE  /  LOCAL FILE SEARCH")
        brand.setObjectName("brand")
        outer.addWidget(brand)
        title = QLabel("Find the thought. Find the file.")
        title.setObjectName("heading")
        outer.addWidget(title)
        subtitle = QLabel(
            "Choose your folders. Search their contents. Everything stays on this computer."
        )
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(22)
        outer.addLayout(body, 1)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(282)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 18, 16, 18)
        side.setSpacing(12)
        side.addWidget(QLabel("YOUR FOLDERS"))
        self.folders = QListWidget()
        self.folders.setToolTip(
            "Only these folders are indexed. Hidden folders and symlinks are skipped."
        )
        side.addWidget(self.folders, 1)
        folder_actions = QHBoxLayout()
        self.add_folder = QPushButton("+ Add folder")
        self.remove_folder = QPushButton("Remove")
        self.add_folder.clicked.connect(self._add_folder)
        self.remove_folder.clicked.connect(self._remove_folder)
        folder_actions.addWidget(self.add_folder)
        folder_actions.addWidget(self.remove_folder)
        side.addLayout(folder_actions)
        self.index_button = QPushButton("Index folders")
        self.index_button.setObjectName("primary")
        self.index_button.clicked.connect(self._index)
        side.addWidget(self.index_button)
        self.clear_button = QPushButton("Clear local index")
        self.clear_button.clicked.connect(self._clear)
        side.addWidget(self.clear_button)
        scope = QLabel(
            "Search images, audio, video, documents and code.\n\n"
            "Audio and video are indexed in clips. Exact words searches media filenames only."
        )
        scope.setWordWrap(True)
        scope.setObjectName("muted")
        side.addWidget(scope)
        body.addWidget(sidebar)

        main = QVBoxLayout()
        main.setSpacing(12)
        body.addLayout(main, 1)
        model_row = QHBoxLayout()
        self.model_label = QLabel()
        self.model_label.setObjectName("model")
        self.model_label.setWordWrap(True)
        self.download_button = QPushButton("Download model…")
        self.download_button.clicked.connect(self._download)
        model_row.addWidget(self.model_label, 1)
        model_row.addWidget(self.download_button)
        main.addLayout(model_row)
        self.device_label = QLabel("Compute · detecting GPU…")
        self.device_label.setObjectName("muted")
        self.device_label.setWordWrap(True)
        main.addWidget(self.device_label)

        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Describe an image, recording or document…")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self._search)
        self.search_button = QPushButton("Search")
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self._search)
        search_row.addWidget(self.query, 1)
        search_row.addWidget(self.search_button)
        main.addLayout(search_row)
        self.keyword_only = QCheckBox("Exact words only — works without a model")
        self.keyword_only.setChecked(not model_ready(self.paths.model))
        main.addWidget(self.keyword_only)

        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.hide()
        main.addWidget(self.error)
        self.summary = QLabel("Add a folder, then click Index folders to get started.")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        main.addWidget(self.summary)
        split = QSplitter(Qt.Orientation.Vertical)
        self.result_list = QListWidget()
        self.result_list.currentRowChanged.connect(self._select_result)
        self.result_list.itemActivated.connect(lambda _: self._open())
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Select a result to read its matching passage.")
        self.preview_stack = QStackedWidget()
        self.preview_stack.addWidget(self.preview)
        self.image_preview = ImagePreview()
        self.preview_stack.addWidget(self.image_preview)
        self.media_preview = QWidget()
        media_layout = QVBoxLayout(self.media_preview)
        self.video_preview = QVideoWidget()
        media_layout.addWidget(self.video_preview, 1)
        self.media_caption = QLabel()
        self.media_caption.setTextFormat(Qt.TextFormat.PlainText)
        self.media_caption.setWordWrap(True)
        media_layout.addWidget(self.media_caption)
        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self.play_button)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        controls.addWidget(self.seek_slider, 1)
        self.playback_time = QLabel("00:00:00")
        controls.addWidget(self.playback_time)
        media_layout.addLayout(controls)
        self.preview_stack.addWidget(self.media_preview)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_preview)
        self.player.durationChanged.connect(lambda duration: self.seek_slider.setRange(0, duration))
        self.player.positionChanged.connect(self._playback_position)
        self.seek_slider.sliderMoved.connect(self.player.setPosition)
        self.player.playbackStateChanged.connect(
            lambda state: self.play_button.setText(
                "Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play"
            )
        )
        self.player.errorOccurred.connect(
            lambda error, message: self.status.setText(f"Preview unavailable: {message}")
        )
        self._preview_start_ms = 0
        self.player.mediaStatusChanged.connect(self._media_loaded)
        split.addWidget(self.result_list)
        split.addWidget(self.preview_stack)
        split.setSizes([300, 160])
        main.addWidget(split, 1)
        actions = QHBoxLayout()
        self.open_button = QPushButton("Open file")
        self.reveal_button = QPushButton("Show in folder")
        self.copy_button = QPushButton("Copy path")
        self.open_button.clicked.connect(self._open)
        self.reveal_button.clicked.connect(self._reveal)
        self.copy_button.clicked.connect(self._copy)
        for button in (self.open_button, self.reveal_button, self.copy_button):
            actions.addWidget(button)
            button.setEnabled(False)
        actions.addStretch()
        main.addLayout(actions)
        self.indexing_status = QLabel()
        self.indexing_status.setTextFormat(Qt.TextFormat.PlainText)
        self.indexing_status.setWordWrap(True)
        self.indexing_status.hide()
        outer.addWidget(self.indexing_status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        outer.addWidget(self.progress_bar)
        footer = QHBoxLayout()
        self.status = QLabel("Ready · no automatic downloads or update checks")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("status")
        footer.addWidget(self.status, 1)
        self.cancel_button = QPushButton("Stop indexing")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        self.details_button = QPushButton("Indexing details")
        self.details_button.clicked.connect(self._details)
        self.details_button.hide()
        footer.addWidget(self.details_button)
        outer.addLayout(footer)

    def _set_device(self, label: str) -> None:
        self._runtime_device_known = True
        self.device_label.setText(label)

    def _device_detected(self, exit_code: int, exit_status) -> None:
        try:
            if not self._runtime_device_known:
                output = self._device_result.read_text(encoding="utf-8")
                self.device_label.setText(json.loads(output)["label"])
        except (OSError, ValueError, KeyError):
            self.device_label.setText("Compute · detected when model loads")
        finally:
            self._device_directory.cleanup()

    def _refresh(self) -> None:
        self.folders.clear()
        for folder in self.settings.folders:
            item = QListWidgetItem(Path(folder).name or folder)
            item.setToolTip(folder)
            item.setData(Qt.ItemDataRole.UserRole, folder)
            self.folders.addItem(item)
        ready = model_ready(self.paths.model)
        self.model_label.setText(
            "EmbeddingGemma 2 installed · offline ready"
            if ready
            else "EmbeddingGemma 2 not downloaded · keyword search available"
        )
        self.download_button.setText("Repair model…" if ready else "Download model…")
        self.download_button.setEnabled(self.task is None)
        self._update_stats()

    def _update_stats(self) -> None:
        stats = self.engine.stats()
        self.summary.setText(
            f"{stats['files']:,} files · {stats['passages']:,} passages · "
            f"{stats['embedded']:,} searchable by meaning"
        )

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to search")
        if not folder:
            return
        try:
            # Keep temporarily disconnected folders instead of failing to add another one.
            roots = [Path(root) for root in self.settings.folders]
            added = canonical_folders([folder])[0]
            if not any(added.is_relative_to(root) for root in roots):
                roots = [root for root in roots if not root.is_relative_to(added)] + [added]
            self.settings.folders = [str(root) for root in roots]
            self.settings.save(self.paths.settings)
            self._refresh()
            self.status.setText("Folder added. Click Index folders to make its files searchable.")
        except (OSError, ValueError) as error:
            self._show_error(str(error))

    def _remove_folder(self) -> None:
        item = self.folders.currentItem()
        if item is None:
            return
        self.settings.folders.remove(item.data(Qt.ItemDataRole.UserRole))
        try:
            self.settings.save(self.paths.settings)
            self.engine.retain_folders(self.settings.folders)
            self._show_results([])
            self._refresh()
        except (OSError, ValueError) as error:
            self._show_error(str(error))

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.add_folder,
            self.remove_folder,
            self.index_button,
            self.clear_button,
            self.search_button,
            self.keyword_only,
        ):
            widget.setEnabled(not busy)
        self.download_button.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        self.cancel_button.setVisible(busy and self.task_kind == "index")
        self.cancel_button.setEnabled(busy)
        self.cancel_button.setText("Stop indexing")

    def _start(self, kind: str, operation: Callable | None = None) -> None:
        if self.task is not None:
            return
        self.error.hide()
        self.task_kind = kind
        self.progress_bar.setRange(0, 0)
        self.indexing_status.setVisible(kind == "index")
        self.indexing_status.setText("Scanning folders…")
        if kind == "index":
            self.task = IndexTask(
                self.paths, self.settings.folders.copy(), not self.keyword_only.isChecked(), self
            )
        else:
            self.task = Task(operation, self)
        self.task.progress.connect(self.status.setText)
        self.task.indexing_progress.connect(self._index_progress)
        self.task.device_changed.connect(self._set_device)
        self.task.succeeded.connect(self._completed)
        self.task.failed.connect(self._failed)
        self.task.finished.connect(self._finished)
        self._set_busy(True)
        self.status.setText(
            {
                "index": "Scanning chosen folders…",
                "search": "Searching locally…",
                "download": "Starting model download…",
            }[kind]
        )
        self.task.start()

    def _index_progress(self, value: IndexProgress) -> None:
        if value.phase == "Scanning folders":
            self.indexing_status.setText("Scanning folders…")
            return
        self.progress_bar.setRange(0, max(1, value.total))
        self.progress_bar.setValue(value.completed)
        percent = int(100 * value.completed / value.total) if value.total else 100
        text = f"{value.completed} / {value.total} files · {percent}% · {value.phase}"
        if value.filename:
            text += f" · {value.filename}"
        if value.passages_total:
            unit = "clips" if value.phase in {"Embedding video", "Embedding audio"} else "passages"
            if value.phase == "Embedding image":
                unit = "images"
            text += f" · {value.passages_done} / {value.passages_total} {unit}"
        self.indexing_status.setText(text)

    def _index(self) -> None:
        self._start("index")

    def _search(self) -> None:
        query = self.query.text().strip()
        if not query or self.task is not None:
            return
        semantic = not self.keyword_only.isChecked()
        self._start(
            "search",
            lambda progress, cancel, indexing_progress: self.engine.search(
                query, semantic=semantic
            ),
        )

    def _download(self) -> None:
        answer = QMessageBox.question(
            self,
            "Download text model?",
            "Download Google EmbeddingGemma 2 (about 1.5 GB) from Hugging Face?\n\n"
            "This is the only step that needs the internet. No files or searches are uploaded. "
            "After it finishes, uncheck Exact words only and index your folders again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start(
                "download",
                lambda progress, cancel, indexing_progress: download_model(
                    self.paths.model, progress
                ),
            )

    def _completed(self, value: object) -> None:
        if self.task_kind == "search":
            self._show_results(value)
            self.status.setText(f"{len(value)} matching files · searched locally")
        elif self.task_kind == "index":
            self._last_report = value
            self.details_button.setVisible(bool(value.errors))
            self.status.setText(
                f"Finished · {value.indexed} indexed · "
                f"{value.unchanged} unchanged · {value.removed} removed · {value.skipped} skipped"
            )
            if not value.cancelled and not (value.indexed + value.unchanged + value.skipped):
                self.status.setText(
                    "No supported files found. Add images, audio, video or documents."
                )
            if value.cancelled:
                self.status.setText("Indexing stopped · completed files kept. Ready to resume.")
                self.indexing_status.setText(self.indexing_status.text() + " · Stopped")
            if value.errors:
                self._show_error("Some files could not be read. See Indexing details.")
            self._update_stats()
        else:
            self.engine = Engine(
                self.paths, LocalEmbedder(self.paths.model, on_device=self.device_changed.emit)
            )
            self.status.setText("Model saved. Uncheck Exact words only, then index your folders.")
            self._refresh()

    def _failed(self, message: str) -> None:
        self._show_error(message)
        self.status.setText("Operation stopped. You can retry; completed files remain indexed.")

    def _finished(self) -> None:
        self.task.deleteLater()
        self.task = None
        self._set_busy(False)
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _cancel(self) -> None:
        if self.task is not None:
            self.task.cancel.set()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("Stopping…")
            self.status.setText("Stopping indexing… Completed files will be kept.")

    def _show_results(self, results: list[SearchResult]) -> None:
        self.results = results
        self.result_list.clear()
        self.preview.clear()
        for result in results:
            snippet = " ".join(result.text.split())[:140]
            item = QListWidgetItem(
                f"{Path(result.path).name}\n{result.location} · {result.match}\n{snippet}"
            )
            item.setToolTip(result.path)
            self.result_list.addItem(item)
        self._select_result(-1)
        if results:
            self.result_list.setCurrentRow(0)
        else:
            self.preview.setPlaceholderText(
                "No matching files. Try different words or index your folders."
            )

    def _select_result(self, row: int) -> None:
        selected = 0 <= row < len(self.results)
        for button in (self.open_button, self.reveal_button, self.copy_button):
            button.setEnabled(selected)
        self.player.stop()
        self.player.setSource(QUrl())
        self.preview_stack.setCurrentWidget(self.preview)
        if selected:
            result = self.results[row]
            kind = media_kind(Path(result.path))
            self.preview.setPlainText(f"{result.path}\n{result.location}\n\n{result.text}")
            if kind == "image":
                reader = QImageReader(result.path)
                reader.setAutoTransform(True)
                size = reader.size()
                if size.isValid():
                    reader.setScaledSize(
                        size.scaled(QSize(800, 500), Qt.AspectRatioMode.KeepAspectRatio)
                    )
                image = reader.read()
                self.image_preview.set_image(image)
                self.image_preview.setToolTip(result.path)
                self.preview_stack.setCurrentWidget(self.image_preview)
            elif kind in {"audio", "video"}:
                self.media_caption.setText(f"{Path(result.path).name} · {result.location}")
                self.video_preview.setVisible(kind == "video")
                self.preview_stack.setCurrentWidget(self.media_preview)
                self._preview_start_ms = 0
                if " · " in result.location:
                    clock = result.location.split(" · ", 1)[1].split("–", 1)[0]
                    hours, minutes, seconds = map(int, clock.split(":"))
                    self._preview_start_ms = (hours * 3600 + minutes * 60 + seconds) * 1000
                self.player.setSource(QUrl.fromLocalFile(result.path))

    def _media_loaded(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.player.setPosition(self._preview_start_ms)

    def _playback_position(self, position: int) -> None:
        from .media import timestamp

        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(position)
        self.playback_time.setText(timestamp(position / 1000))

    def _toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _selected(self) -> SearchResult | None:
        row = self.result_list.currentRow()
        return self.results[row] if 0 <= row < len(self.results) else None

    def _open(self) -> None:
        result = self._selected()
        if result is not None:
            path = Path(result.path)
            if not path.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self._show_error("The file could not be opened. It may have moved or been deleted.")

    def _reveal(self) -> None:
        result = self._selected()
        if result is None:
            return
        try:
            if not reveal_file(
                Path(result.path),
                lambda folder: QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))),
            ):
                self._show_error("No file manager could open this folder. Use Copy path instead.")
        except OSError as error:
            self._show_error(str(error))

    def _copy(self) -> None:
        if result := self._selected():
            QApplication.clipboard().setText(result.path)
            self.status.setText("File path copied.")

    def _clear(self) -> None:
        if (
            QMessageBox.question(
                self,
                "Clear local index?",
                "Remove indexed text and embeddings? Your files and model stay intact.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            try:
                self.engine.clear()
                self._show_results([])
                self._update_stats()
            except (OSError, ValueError) as error:
                self._show_error(str(error))

    def _details(self) -> None:
        if self._last_report is not None:
            box = QMessageBox(self)
            box.setWindowTitle("Indexing details")
            box.setText(f"{len(self._last_report.errors)} file or folder errors")
            box.setDetailedText("\n".join(self._last_report.errors))
            box.exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.task is not None and self.task.isRunning():
            if self.task_kind == "download":
                self._show_error(
                    "Wait for the model download to finish before closing this window."
                )
            else:
                self._closing = True
                self._cancel()
            event.ignore()
            return
        self.player.stop()
        self.device_probe.kill()
        self.device_probe.waitForFinished(500)
        self._device_directory.cleanup()
        event.accept()


def main(paths: AppPaths | None = None) -> int:
    ensure_standard_streams()
    configure_native_libraries()
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("subject-to-change")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.svg"))))
    app.setOrganizationName("subject-to-change")
    app.setFont(QFont("Sans Serif", 10))
    app.setStyleSheet(STYLE)
    try:
        window = MainWindow(paths or AppPaths.default())
    except Exception as error:
        QMessageBox.critical(None, "Cannot start Subject to Change", str(error))
        return 1
    window.show()
    return app.exec()
