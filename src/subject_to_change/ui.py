from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFont, QKeySequence, QShortcut
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
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .config import APP_NAME, AppPaths, Settings, canonical_folders
from .desktop import reveal_file
from .embeddings import download_model, model_ready
from .engine import Engine, IndexReport
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


class Task(QThread):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable, parent=None) -> None:
        super().__init__(parent)
        self.operation = operation
        self.cancel = Event()

    def run(self) -> None:
        try:
            self.succeeded.emit(self.operation(self.progress.emit, self.cancel))
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self.engine = Engine(paths)
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
            "Reads TXT, Markdown, PDF, DOCX, ODT, HTML and CSV.\n\n"
            "This first version searches text. Images, audio and video come later."
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

        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Describe a document, or enter keywords…")
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
        split.addWidget(self.result_list)
        split.addWidget(self.preview)
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

    def _refresh(self) -> None:
        self.folders.clear()
        for folder in self.settings.folders:
            item = QListWidgetItem(Path(folder).name or folder)
            item.setToolTip(folder)
            item.setData(Qt.ItemDataRole.UserRole, folder)
            self.folders.addItem(item)
        ready = model_ready(self.paths.model)
        self.model_label.setText(
            "Text model installed · offline ready"
            if ready
            else "No model downloaded · keyword search available"
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

    def _start(self, kind: str, operation: Callable) -> None:
        if self.task is not None:
            return
        self.error.hide()
        self.task_kind = kind
        self.task = Task(operation, self)
        self.task.progress.connect(self.status.setText)
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

    def _index(self) -> None:
        semantic = not self.keyword_only.isChecked()
        folders = self.settings.folders.copy()
        self._start(
            "index",
            lambda progress, cancel: self.engine.index(
                folders,
                semantic=semantic,
                progress=progress,
                cancel=cancel,
            ),
        )

    def _search(self) -> None:
        query = self.query.text().strip()
        if not query or self.task is not None:
            return
        semantic = not self.keyword_only.isChecked()
        self._start("search", lambda progress, cancel: self.engine.search(query, semantic=semantic))

    def _download(self) -> None:
        answer = QMessageBox.question(
            self,
            "Download text model?",
            "Download multilingual-e5-small (about 500 MB) from Hugging Face?\n\n"
            "This is the only step that needs the internet. No files or searches are uploaded. "
            "After it finishes, uncheck Exact words only and index your folders again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start(
                "download", lambda progress, cancel: download_model(self.paths.model, progress)
            )

    def _completed(self, value: object) -> None:
        if self.task_kind == "search":
            self._show_results(value)
            self.status.setText(f"{len(value)} matching files · searched locally")
        elif self.task_kind == "index":
            self._last_report = value
            self.details_button.setVisible(bool(value.errors))
            self.status.setText(
                f"{'Stopped' if value.cancelled else 'Finished'} · {value.indexed} indexed · "
                f"{value.unchanged} unchanged · {value.removed} removed · {value.skipped} skipped"
            )
            if value.errors:
                self._show_error("Some files could not be read. See Indexing details.")
            self._update_stats()
        else:
            self.engine = Engine(self.paths)
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
            self.status.setText("Stopping after the current step. Completed files will be kept.")

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
        if selected:
            result = self.results[row]
            self.preview.setPlainText(f"{result.path}\n{result.location}\n\n{result.text}")

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
        event.accept()


def main(paths: AppPaths | None = None) -> int:
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName(APP_NAME)
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
