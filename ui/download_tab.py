import os
import shutil

from PyQt5.QtWidgets import (
    QWidget,        QVBoxLayout,
    QHBoxLayout,    QLineEdit,
    QComboBox,      QLabel,
    QFileDialog,    QPushButton,
    QSizePolicy
)

from PyQt5.QtCore import (
    Qt, QUrl, QTimer
)

from PyQt5.QtGui import QPixmap

from PyQt5.QtNetwork import (
    QNetworkRequest, QNetworkReply
)

from core.downloader import DownloadThread
from core.preview import PreviewThread

from ui.animated_progress_bar import AnimatedProgressBar
from ui.custom_message_box import CustomMessageBox


class DownloadTab(QWidget):
    def __init__(self, main_window):
        super().__init__()

        self.main_window = main_window
        self.available_heights = []

        self.download_thread = None
        self.preview_thread = None

        self._full_title = ""
        self._current_preview_url = ""

        self.preview_timer = QTimer()
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.load_preview)

        self.setup_ui()


    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.preview_container = QWidget()
        self.preview_container.setFixedHeight(240)

        policy = self.preview_container.sizePolicy()
        policy.setRetainSizeWhenHidden(True)

        self.preview_container.setSizePolicy(policy)
        self.preview_container.setVisible(False)

        preview_layout = QVBoxLayout(self.preview_container)
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(320, 180)

        self.preview_title = QLabel()
        self.preview_title.setAlignment(Qt.AlignCenter)
        self.preview_title.setWordWrap(False)
        self.preview_title.setMaximumHeight(35)
        self.preview_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        preview_layout.addWidget(self.preview_label)
        preview_layout.addWidget(self.preview_title)

        self.preview_container.setVisible(False)
        layout.addWidget(self.preview_container)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("URL")
        self.url_edit.textChanged.connect(self.on_url_changed)
        layout.addWidget(self.url_edit)

        format_layout = QHBoxLayout()
        format_label = QLabel("Format:")

        self.format_combo = QComboBox()
        self.format_combo.setAttribute(Qt.WA_StyledBackground)
        self.format_combo.addItems(["mp4", "wav", "mp3"])
        self.format_combo.currentTextChanged.connect(self.update_quality_options)

        format_layout.addWidget(format_label)
        format_layout.addWidget(self.format_combo)
        layout.addLayout(format_layout)

        quality_layout = QHBoxLayout()
        quality_label = QLabel("Resolution:")

        self.quality_combo = QComboBox()
        self.quality_combo.setAttribute(Qt.WA_StyledBackground)
        self.update_quality_options(self.format_combo.currentText())

        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(self.quality_combo)
        layout.addLayout(quality_layout)

        buttons_layout = QHBoxLayout()
        self.download_btn = QPushButton("Download")
        self.download_btn.clicked.connect(self.start_download)

        self.cancel_download_btn = QPushButton("Cancel")
        self.cancel_download_btn.clicked.connect(self.cancel_download)
        self.cancel_download_btn.setVisible(False)

        buttons_layout.addWidget(self.download_btn)
        buttons_layout.addWidget(self.cancel_download_btn)
        layout.addLayout(buttons_layout)

        self.download_progress = AnimatedProgressBar()
        layout.addWidget(self.download_progress)

        layout.addStretch(1)


    def on_url_changed(self, text):
        self.preview_container.setVisible(False)
        self.available_heights = []
        self._current_preview_url = text.strip()
        self.preview_timer.start(500)


    def load_preview(self):
        url = self.url_edit.text().strip()
        if not url:
            return

        self._current_preview_url = url

        if self.preview_thread and self.preview_thread.isRunning():
            self.preview_thread.quit()
            self.preview_thread.wait(500)

        self.preview_thread = PreviewThread(url)
        self.preview_thread.preview_ready.connect(self.on_preview_ready)
        self.preview_thread.error.connect(lambda e: None)
        self.preview_thread.start()


    def on_preview_ready(self, title, thumbnail_url, heights):
        self._full_title = title
        self._current_preview_url = self.url_edit.text().strip()
        self.preview_container.setVisible(True)
        self.available_heights = heights

        if self.format_combo.currentText() == 'mp4':
            self.update_quality_options('mp4')

        if thumbnail_url:
            request = QNetworkRequest(QUrl(thumbnail_url))
            self.main_window.network_manager.get(request)

        self._update_title()


    def on_thumbnail_downloaded(self, reply):
        if reply.error() != QNetworkReply.NoError:
            reply.deleteLater()
            return

        current_url = self.url_edit.text().strip()
        if (not current_url
            or current_url != self._current_preview_url):
                reply.deleteLater()
                return

        data = reply.readAll()
        if not data.isEmpty():
            pixmap = QPixmap()

            if pixmap.loadFromData(data):
                self.preview_label.setPixmap(
                    pixmap.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )

                self.preview_label.setGraphicsEffect(None)
                self.preview_container.update()

        reply.deleteLater()


    def update_quality_options(self, format_type):
        self.quality_combo.clear()
        if format_type == 'mp4':
            if self.available_heights:
                for h in self.available_heights:
                    self.quality_combo.addItem(
                        f"{h}p",
                        f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
                    )

            else:
                for h in [1080,720,480,360]:
                    self.quality_combo.addItem(
                        f"{h}p",
                        f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
                    )

        elif format_type == 'wav':
            for depth in ["16", "24", "32"]:
                self.quality_combo.addItem(f"{depth}bit", depth)

        elif format_type == 'mp3':
            bitrates = [
                "320", "256",
                "224", "192",
                "160", "128",
                "112", "96",
                "80", "64",
                "56", "48",
                "40", "32"
            ]

            for br in bitrates:
                self.quality_combo.addItem(f"{br}kbps", br)


    def start_download(self):
        url = self.url_edit.text().strip()
        if not url:
            CustomMessageBox.warning(self, "Error", "Enter URL!")
            return
        
        format_type = self.format_combo.currentText()
        quality = self.quality_combo.currentData()
        if quality is None:
            CustomMessageBox.warning(self, "Error", "Choose resolution!")
            return

        self.download_btn.setVisible(False)
        self.cancel_download_btn.setVisible(True)
        self.download_progress.setValue(0)

        self.download_thread = DownloadThread(url, format_type, quality)
        self.download_thread.progress.connect(self.download_progress.setValue)
        self.download_thread.finished_ok.connect(self.on_download_finished)
        self.download_thread.error.connect(self.on_download_error)
        self.download_thread.canceled.connect(self.on_download_canceled)
        self.download_thread.start()


    def cancel_download(self):
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.cancel()


    def on_download_finished(self, data):
        filename, temp_dir = data
        self.reset_download_buttons()
        self.download_progress.setValue(100)

        if not os.path.exists(filename):
            CustomMessageBox.critical(self, "Error", "Downloaded file not found.")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return
        
        suggested_name = os.path.basename(filename)

        ext = os.path.splitext(suggested_name)
        if not ext:
            if self.format_combo.currentText() == 'mp4':
                suggested_name += '.mp4'

            elif self.format_combo.currentText() == 'mp3':
                suggested_name += '.mp3'

            elif self.format_combo.currentText() == 'wav':
                suggested_name += '.wav'

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save file as", suggested_name, "All files (*.*)"
        )

        if save_path:
            try:
                shutil.copy2(filename, save_path)
                shutil.rmtree(temp_dir, ignore_errors=True)
                CustomMessageBox.information(self, "Success", f"File saved:\n{save_path}")

            except Exception as e:
                CustomMessageBox.critical(self, "Error", f"Error while saving: {e}")

        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
            CustomMessageBox.information(self, "Cancelled", "Save cancelled.")
            self.download_progress.setValue(0)


    def on_download_error(self, error_msg):
        self.reset_download_buttons()
        self.download_progress.setValue(0)
        CustomMessageBox.critical(self, "Error", error_msg)


    def on_download_canceled(self):
        self.reset_download_buttons()
        self.download_progress.setValue(0)
        CustomMessageBox.information(self, "Cancelled", "Download cancelled!")


    def reset_download_buttons(self):
        self.download_btn.setVisible(True)
        self.cancel_download_btn.setVisible(False)


    def _update_title(self):
        if (not self._full_title
            or not self.preview_title.isVisible()):
                return

        width = self.preview_title.width() - 10
        if width < 10:
            width = 10

        font_metrics = self.preview_title.fontMetrics()
        elided = font_metrics.elidedText(self._full_title, Qt.ElideRight, width)
        self.preview_title.setText(elided)


    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_title()