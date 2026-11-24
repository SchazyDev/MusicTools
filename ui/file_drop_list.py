import os

from PyQt5.QtCore import (
    Qt, pyqtSignal
)

from PyQt5.QtWidgets import (
    QListWidget,    QListWidgetItem,
    QAbstractItemView,
    QWidget,        QHBoxLayout,
    QLabel,         QPushButton,
    QSizePolicy
)

from PyQt5.QtGui import (
    QDragEnterEvent,    QDropEvent,
    QMouseEvent,        QColor
)

from utils.constants import ALLOWED_EXTENSIONS
from utils.logger import setup_logger

logger = setup_logger(__name__)

MAX_DROPPED_FILES = 500


def collect_droppable_files(paths, allowed_extensions):
    """Normalize dropped paths into accepted/rejected file lists.

    Returns ``(accepted, rejected)``. ``accepted`` holds real absolute paths of
    existing files with a supported extension, deduplicated by
    ``os.path.normcase(os.path.abspath(...))`` and preserving input order.
    Directories are expanded recursively via ``os.walk`` (capped at
    ``MAX_DROPPED_FILES``). Non-existent paths and unsupported extensions go to
    ``rejected``. Pure logic: imports no Qt and is unit-testable headless.
    """
    accepted = []
    rejected = []
    seen = set()

    allowed = {ext.lower() for ext in allowed_extensions}

    for path in paths:
        if not path:
            continue

        if not os.path.exists(path):
            rejected.append(path)
            continue

        if os.path.isdir(path):
            candidates = []
            for root, _dirs, files in os.walk(path):
                for name in files:
                    candidates.append(os.path.join(root, name))
                    if len(candidates) >= MAX_DROPPED_FILES:
                        break
                if len(candidates) >= MAX_DROPPED_FILES:
                    break
        else:
            candidates = [path]

        for candidate in candidates:
            absolute = os.path.abspath(candidate)

            if not os.path.isfile(absolute):
                rejected.append(absolute)
                continue

            if os.path.splitext(absolute)[1].lower() not in allowed:
                rejected.append(absolute)
                continue

            key = os.path.normcase(absolute)
            if key in seen:
                continue

            seen.add(key)
            accepted.append(absolute)

    return accepted, rejected


class FileDropList(QListWidget):
    empty_clicked = pyqtSignal()
    drop_rejected = pyqtSignal(list)


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDropIndicatorShown(True)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QListWidget.NoSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAttribute(Qt.WA_StyledBackground)
        self._files = set()

        self.dummy_item = QListWidgetItem("  Add Files...")
        self.dummy_item.setData(Qt.UserRole, None)
        self.dummy_item.setForeground(QColor("#f99b06"))
        self.addItem(self.dummy_item)


    @staticmethod
    def _has_local_urls(event) -> bool:
        mime_data = event.mimeData()

        if not mime_data.hasUrls():
            return False

        return any(url.isLocalFile() for url in mime_data.urls())


    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._has_local_urls(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()


    def dragMoveEvent(self, event):
        if self._has_local_urls(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()


    def dropEvent(self, event: QDropEvent):
        try:
            mime_data = event.mimeData()

            if not mime_data.hasUrls():
                event.ignore()
                return

            local_paths = [
                url.toLocalFile()
                for url in mime_data.urls()
                if url.isLocalFile()
            ]

            accepted, rejected = collect_droppable_files(
                local_paths, ALLOWED_EXTENSIONS
            )

            added = 0
            duplicates = 0

            for file_path in accepted:
                if self.add_file_item(file_path):
                    added += 1
                else:
                    duplicates += 1

            logger.info(
                "Drop processed: added=%d, rejected=%d, duplicates=%d",
                added, rejected.__len__(), duplicates
            )

            if added == 0 and rejected:
                self.drop_rejected.emit(
                    [os.path.basename(path) or path for path in rejected]
                )

            event.acceptProposedAction()

        except Exception:
            logger.exception("Unhandled error while processing dropped files")
            event.acceptProposedAction()


    def mousePressEvent(self, event: QMouseEvent):
        item = self.itemAt(event.pos())

        if item == self.dummy_item:
            self.empty_clicked.emit()
            return
        
        if item is None:
            self.empty_clicked.emit()

        else:
            super().mousePressEvent(event)


    def add_file_item(self, file_path):
        if not any(file_path.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
            return False

        absolute = os.path.abspath(file_path)
        key = os.path.normcase(absolute)

        if key in self._files:
            return False

        self._files.add(key)

        item = QListWidgetItem()
        item.setData(Qt.UserRole, absolute)

        widget = QWidget()
        widget.setMinimumHeight(40)

        layout = QHBoxLayout(widget)
        layout.setContentsMargins(9, 4, 4, 4)

        btn = QPushButton("✕")
        btn.setFixedSize(28, 28)
        btn.clicked.connect(lambda checked=False, it=item: self._delete_item(it))

        label = QLabel(os.path.basename(absolute))
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        label.setToolTip(absolute)

        layout.addWidget(btn)
        layout.addWidget(label)

        widget.setLayout(layout)
        item.setSizeHint(widget.sizeHint())

        index = self.count() - 1
        self.insertItem(index, item)
        self.setItemWidget(item, widget)

        return True


    def _delete_item(self, item):
        row = self.row(item)
        file_path = item.data(Qt.UserRole)

        if file_path:
            self._files.discard(os.path.normcase(os.path.abspath(file_path)))

        self.takeItem(row)


    def clear(self):
        self._files.clear()

        while self.count() > 1:
            item = self.item(0)

            if item != self.dummy_item:
                self.takeItem(0)
                
            else:
                break
