from PyQt5.QtWidgets import (
    QMainWindow,    QTabWidget,
    QWidget,        QVBoxLayout,
    QHBoxLayout,    QPushButton,
    QLabel
)

from PyQt5.QtCore import (
    Qt, QPropertyAnimation,
    QPoint, QEasingCurve
)

from PyQt5.QtGui import (
    QMouseEvent
)

from PyQt5.QtNetwork import QNetworkAccessManager

from ui.download_tab import DownloadTab
from ui.convert_tab import ConvertTab
from ui.about_tab import AboutTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Music Tools")
        self.setFixedSize(500, 600)

        self.setWindowFlags(
            Qt.WindowStaysOnTopHint |
            Qt.FramelessWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        central_widget = QWidget()
        central_widget.setObjectName("centralWidget")
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.title_bar = QWidget()
        self.title_bar.setFixedHeight(56)
        self.title_bar.setObjectName("titleBar")

        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 0, 0, 0)
        title_layout.setSpacing(0)

        title_label = QLabel("Music Tools")
        title_label.setObjectName("titleLabel")
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.clicked.connect(self.close)
        title_layout.addWidget(self.close_btn)

        layout.addWidget(self.title_bar)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("tabs")
        layout.addWidget(self.tabs)

        self.download_tab = DownloadTab(self)
        self.tabs.addTab(self.download_tab, "Download")

        self.convert_tab = ConvertTab(self)
        self.tabs.addTab(self.convert_tab, "Convert")

        self.about_tab = AboutTab()
        self.tabs.addTab(self.about_tab, "About")

        self.network_manager = QNetworkAccessManager()
        self.network_manager.finished.connect(self.download_tab.on_thumbnail_downloaded)

        self.dragging = False
        self.drag_position = QPoint()

        self.setWindowOpacity(0)


    def mousePressEvent(self, event: QMouseEvent):
        if (event.button() == Qt.LeftButton
            and self.title_bar.geometry().contains(event.pos())):
                self.dragging = True
                self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
                event.accept()


    def mouseMoveEvent(self, event: QMouseEvent):
        if self.dragging and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
            event.accept()


    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            event.accept()


    def animate_show(self):
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self._opacity_anim.setDuration(300)
        self._opacity_anim.setStartValue(0)
        self._opacity_anim.setEndValue(1)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._opacity_anim.start()