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
from ui.file_drop_list import FileDropList
from ui.animated_progress_bar import AnimatedProgressBar
from ui.custom_message_box import CustomMessageBox


class ConvertTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.convert_thread = None
        self.setup_ui()


    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        self.files_list = FileDropList()
        self.files_list.empty_clicked.connect(self.add_files_dialog)
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
        quality_label = QLabel("Resolution:")

        self.convert_quality_combo = QComboBox()
        self.convert_quality_combo.setAttribute(Qt.WA_StyledBackground)
        self.update_convert_quality_options(self.convert_format_combo.currentText())

        quality_layout.addWidget(quality_label)
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


    def update_convert_quality_options(self, format_type):
        self.convert_quality_combo.clear()
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


    def on_convert_finished(self, temp_dir):
        self.reset_convert_buttons()
        self.convert_progress.setValue(100)
        target_dir = QFileDialog.getExistingDirectory(self, "Choose folder for files!", "")

        if target_dir:
            try:
                for fname in os.listdir(temp_dir):
                    src = os.path.join(temp_dir, fname)
                    dst = os.path.join(target_dir, fname)
                    shutil.copy2(src, dst)

                shutil.rmtree(temp_dir, ignore_errors=True)
                CustomMessageBox.information(self, "Success", f"Files saved:\n{target_dir}")

            except Exception as e:
                CustomMessageBox.critical(self, "Error", f"Error while saving: {e}")

        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
            CustomMessageBox.information(self, "Warning", "Files not saved.")
            self.convert_progress.setValue(0)


    def on_convert_error(self, error_msg):
        self.reset_convert_buttons()
        self.convert_progress.setValue(0)
        CustomMessageBox.critical(self, "Error", error_msg)


    def on_convert_canceled(self):
        self.reset_convert_buttons()
        self.convert_progress.setValue(0)
        CustomMessageBox.information(self, "Cancelled", "Convertation cancelled.")


    def reset_convert_buttons(self):
        self.convert_btn.setVisible(True)
        self.cancel_convert_btn.setVisible(False)