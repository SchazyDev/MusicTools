import os

from PyQt5.QtCore import (
    Qt, pyqtSignal
)

from PyQt5.QtWidgets import (
    QListWidget,    QListWidgetItem,
    QWidget,        QHBoxLayout,
    QLabel,         QPushButton,
    QSizePolicy
)

from PyQt5.QtGui import (
    QDragEnterEvent,    QDropEvent,
    QMouseEvent,        QColor
)

from utils.constants import ALLOWED_EXTENSIONS


class FileDropList(QListWidget):
    empty_clicked = pyqtSignal()


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
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


    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()


    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()


    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()

        for url in urls:
            local_file = url.toLocalFile()

            if local_file and os.path.isfile(local_file):
                self.add_file_item(local_file)

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
            return
        
        if file_path in self._files:
            return
        self._files.add(file_path)

        item = QListWidgetItem()
        item.setData(Qt.UserRole, file_path)

        widget = QWidget()
        widget.setMinimumHeight(40)

        layout = QHBoxLayout(widget)
        layout.setContentsMargins(9, 4, 4, 4)

        btn = QPushButton("✕")
        btn.setFixedSize(28, 28)
        btn.clicked.connect(lambda checked=False, it=item: self._delete_item(it))

        label = QLabel(os.path.basename(file_path))
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        label.setToolTip(file_path)

        layout.addWidget(btn)
        layout.addWidget(label)

        widget.setLayout(layout)
        item.setSizeHint(widget.sizeHint())

        index = self.count() - 1
        self.insertItem(index, item)
        self.setItemWidget(item, widget)


    def _delete_item(self, item):
        row = self.row(item)
        file_path = item.data(Qt.UserRole)
        self._files.discard(file_path)
        self.takeItem(row)


    def clear(self):
        self._files.clear()

        while self.count() > 1:
            item = self.item(0)

            if item != self.dummy_item:
                self.takeItem(0)
                
            else:
                break