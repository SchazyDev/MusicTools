import os

from PyQt5.QtCore import (
    Qt, QUrl
)

from PyQt5.QtGui import (
    QPixmap, QDesktopServices
)

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout,
    QHBoxLayout, QLabel,
    QPushButton, QSizePolicy
)

from utils.constants import (
    ICON_PATH,
    ABOUT_LOGO_URL,
    ABOUT_TEXT_URL,
    ABOUT_SUPPORT_URL,
    ABOUT_REPORT_URL,
    VERSION
)


class AboutTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()


    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 30, 10, 10)
        main_layout.setSpacing(5)
        main_layout.addStretch()

        self.logo_label = QLabel()
        self.logo_label.setCursor(Qt.PointingHandCursor)
        self.logo_label.setAlignment(Qt.AlignCenter)
        self.logo_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.logo_label.setScaledContents(False)

        if os.path.exists(ICON_PATH):
            pixmap = QPixmap(ICON_PATH)

            if not pixmap.isNull():
                max_size = 220

                if (pixmap.width() > max_size
                    or pixmap.height() > max_size):
                        pixmap = pixmap.scaled(
                            max_size, max_size,
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation
                        )

                self.logo_label.setPixmap(pixmap)

            else:
                self.logo_label.setText("Logo")

        else:
            self.logo_label.setText("Logo")

        self.logo_label.mousePressEvent = self.on_logo_clicked
        main_layout.addWidget(self.logo_label)

        bottom_widget = QWidget()
        bottom_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)

        self.support_label = QLabel()
        self.support_label.setAlignment(Qt.AlignCenter)
        self.support_label.setCursor(Qt.PointingHandCursor)
        self.support_label.setText(
            f'<br />Support by: <a href="{ABOUT_LOGO_URL}" style="color:#f99b06; text-decoration:none;">FAUNA MUSIC</a>'
        )
        self.support_label.setOpenExternalLinks(True)
        bottom_layout.addWidget(self.support_label)

        self.love_label = QLabel()
        self.love_label.setAlignment(Qt.AlignCenter)
        self.love_label.setCursor(Qt.PointingHandCursor)
        self.love_label.setText(
            f'Made by: <a href="{ABOUT_TEXT_URL}" style="color:#f99b06; text-decoration:none;">Schazy</a>'
        )
        self.love_label.setOpenExternalLinks(True)
        bottom_layout.addWidget(self.love_label)

        self.version_label = QLabel()
        self.version_label.setAlignment(Qt.AlignCenter)
        self.version_label.setText(
            f'Version: <span style="color:#f99b06;">{VERSION}</span><br />'
        )
        self.version_label.setTextFormat(Qt.RichText)
        bottom_layout.addWidget(self.version_label)

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)

        self.support_btn = QPushButton("Support")
        self.support_btn.clicked.connect(self.on_support_clicked)
        buttons_layout.addWidget(self.support_btn)

        self.report_btn = QPushButton("Report")
        self.report_btn.clicked.connect(self.on_report_clicked)
        buttons_layout.addWidget(self.report_btn)

        bottom_layout.addLayout(buttons_layout)
        main_layout.addWidget(bottom_widget)


    def open_url(self, url):
        QDesktopServices.openUrl(QUrl(url))


    def on_logo_clicked(self, event):
        self.open_url(ABOUT_LOGO_URL)


    def on_support_clicked(self):
        self.open_url(ABOUT_SUPPORT_URL)


    def on_report_clicked(self):
        self.open_url(ABOUT_REPORT_URL)