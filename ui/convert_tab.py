import os
import shutil

from PyQt5.QtWidgets import (
    QWidget,        QVBoxLayout,
    QHBoxLayout,    QPushButton,
    QComboBox,      QLabel,
    QFileDialog
)

from PyQt5.QtCore import Qt

from core.converter import ConvertThread
from core.file_ops import CopyWorker
from ui.file_drop_list import FileDropList
from ui.animated_progress_bar import AnimatedProgressBar
from ui.custom_message_box import CustomMessageBox
from utils.constants import ALLOWED_EXTENSIONS


class ConvertTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.convert_thread = None
        self.copy_thread = None
        self._temp_dir = None
        self._target_dir = None
        self.setup_ui()


    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        self.files_list = FileDropList()
        self.files_list.empty_clicked.connect(self.add_files_dialog)
        self.files_list.drop_rejected.connect(self.on_files_drop_rejected)
        layout.addWidget(self.files_list)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.files_list.clear)
        layout.addWidget(clear_btn)

        format_layout = QHBoxLayout()
        format_label = QLabel("Format:")

        self.convert_format_combo = QComboBox()
        self.convert_format_combo.setAttribute(Qt.WA_StyledBackground)
        self.convert_format_combo.addItems(["mp3", "wav", "flac", "ogg"])
        self.convert_format_combo.currentTextChanged.connect(self.update_convert_quality_options)

        format_layout.addWidget(format_label)
        format_layout.addWidget(self.convert_format_combo)
        layout.addLayout(format_layout)

        quality_layout = QHBoxLayout()
        self.quality_label = QLabel(
            self._quality_label_for(self.convert_format_combo.currentText())
        )

        self.convert_quality_combo = QComboBox()
        self.convert_quality_combo.setAttribute(Qt.WA_StyledBackground)
        self.update_convert_quality_options(self.convert_format_combo.currentText())

        quality_layout.addWidget(self.quality_label)
        quality_layout.addWidget(self.convert_quality_combo)
        layout.addLayout(quality_layout)

        buttons_layout = QHBoxLayout()
        self.convert_btn = QPushButton("Convert")
        self.convert_btn.clicked.connect(self.start_conversion)

        self.cancel_convert_btn = QPushButton("Cancel")
        self.cancel_convert_btn.clicked.connect(self.cancel_conversion)
        self.cancel_convert_btn.setVisible(False)

        buttons_layout.addWidget(self.convert_btn)
        buttons_layout.addWidget(self.cancel_convert_btn)
        layout.addLayout(buttons_layout)

        self.convert_progress = AnimatedProgressBar()
        layout.addWidget(self.convert_progress)


    def on_files_drop_rejected(self, rejected_names):
        unsupported = all(
            not name.lower().endswith(tuple(ALLOWED_EXTENSIONS))
            for name in rejected_names
        )
        if unsupported:
            message = "Файлы не добавлены: неподдерживаемый формат."
        else:
            message = "Файлы не добавлены."

        CustomMessageBox.information(self, "Warning", message)

    def _quality_label_for(self, format_type):
        if format_type in ('wav', 'flac'):
            return "Bit depth:"

        return "Bitrate:"

    def update_convert_quality_options(self, format_type):
        self.convert_quality_combo.clear()
        if hasattr(self, 'quality_label'):
            self.quality_label.setText(self._quality_label_for(format_type))

        if format_type == 'mp3':
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
                self.convert_quality_combo.addItem(f"{br}kbps", br)

        elif format_type == 'wav':
            for depth in ["32", "24", "16"]:
                self.convert_quality_combo.addItem(f"{depth}bit", depth)

        elif format_type == 'flac':
            for depth in ["24", "16"]:
                for comp in range(13):
                    self.convert_quality_combo.addItem(f"{depth}bit, comp {comp}", f"{depth}:{comp}")

        elif format_type == 'ogg':
            bitrates = [
                "450", "350",
                "320", "256",
                "224", "192",
                "160", "128",
                "112", "96",
                "80", "64",
                "56", "48"
            ]

            for br in bitrates:
                self.convert_quality_combo.addItem(f"{br}kbps", br)


    def add_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose files",
            "",
            "Media files (*.mp4 *.avi *.mkv *.mov *.flv *.wmv *.webm *.mp3 *.wav *.flac *.aac *.ogg *.m4a);;All files (*.*)"
        )

        for f in files:
            self.files_list.add_file_item(f)


    def start_conversion(self):
        if self.files_list.count() == 1:
            CustomMessageBox.warning(self, "Error", "Choose files to convert!")
            return
        
        output_format = self.convert_format_combo.currentText()
        quality = self.convert_quality_combo.currentData()

        files = []
        for i in range(self.files_list.count()):
            item = self.files_list.item(i)

            if item is self.files_list.dummy_item:
                continue
            
            path = item.data(Qt.UserRole)
            if path and isinstance(path, str):
                files.append(path)

        if not files:
            CustomMessageBox.warning(self, "Error", "No valid files to convert!")
            return

        self.convert_btn.setVisible(False)
        self.cancel_convert_btn.setVisible(True)
        self.convert_progress.setValue(0)

        self.convert_thread = ConvertThread(files, output_format, quality)
        self.convert_thread.progress.connect(self.convert_progress.setValue)
        self.convert_thread.finished_ok.connect(self.on_convert_finished)
        self.convert_thread.error.connect(self.on_convert_error)
        self.convert_thread.canceled.connect(self.on_convert_canceled)
        self.convert_thread.start()


    def cancel_conversion(self):
        if self.convert_thread and self.convert_thread.isRunning():
            self.convert_thread.cancel()

        if self.copy_thread and self.copy_thread.isRunning():
            self.copy_thread.cancel()


    def on_convert_finished(self, temp_dir):
        self.reset_convert_buttons()
        self.convert_progress.setValue(100)
        target_dir = QFileDialog.getExistingDirectory(self, "Choose folder for files!", "")

        if not target_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            CustomMessageBox.information(self, "Warning", "Files not saved.")
            self.convert_progress.setValue(0)
            return

        sources = [
            os.path.join(temp_dir, fname)
            for fname in os.listdir(temp_dir)
            if os.path.isfile(os.path.join(temp_dir, fname))
        ]

        self._temp_dir = temp_dir
        self._target_dir = target_dir

        self.convert_btn.setVisible(False)
        self.cancel_convert_btn.setVisible(True)
        self.convert_progress.setValue(0)

        self.copy_thread = CopyWorker(sources, target_dir)
        self.copy_thread.progress.connect(self.convert_progress.setValue)
        self.copy_thread.status.connect(self._on_copy_status)
        self.copy_thread.finished_ok.connect(self.on_copy_finished)
        self.copy_thread.error.connect(self.on_copy_error)
        self.copy_thread.canceled.connect(self.on_copy_canceled)
        self.copy_thread.start()


    def _on_copy_status(self, message):
        status_label = getattr(self.main_window, "status_label", None)
        if status_label is not None:
            status_label.setText(message)


    def on_copy_finished(self, written):
        self.reset_convert_buttons()
        self.convert_progress.setValue(100)
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
        CustomMessageBox.information(self, "Success", f"Files saved:\n{self._target_dir}")


    def on_copy_error(self, error_msg):
        self.reset_convert_buttons()
        self.convert_progress.setValue(0)
        CustomMessageBox.critical(self, "Error", f"Error while saving: {error_msg}")


    def on_copy_canceled(self):
        self.reset_convert_buttons()
        self.convert_progress.setValue(0)
        CustomMessageBox.information(self, "Cancelled", "Copying cancelled.")


    def on_convert_error(self, error_msg):
        self.reset_convert_buttons()
        self.convert_progress.setValue(0)
        CustomMessageBox.critical(self, "Error", error_msg)


    def on_convert_canceled(self):
        self.reset_convert_buttons()
        self.convert_progress.setValue(0)
        CustomMessageBox.information(self, "Cancelled", "Conversion cancelled.")


    def reset_convert_buttons(self):
        self.convert_btn.setVisible(True)
        self.cancel_convert_btn.setVisible(False)
