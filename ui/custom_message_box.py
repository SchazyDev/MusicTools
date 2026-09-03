from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout,
    QLabel, QPushButton,
    QHBoxLayout, QWidget,
    QApplication
)

from PyQt5.QtCore import Qt


class CustomMessageBox(QDialog):
    def __init__(self, title, message, parent=None, buttons=('OK',)):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self._result = False

        main_widget = QWidget(self)
        main_widget.setObjectName("messageBoxMain")
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(20)

        title_label = QLabel(title)
        title_label.setObjectName("msgTitle")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        msg_label = QLabel(message)
        msg_label.setObjectName("msgText")
        msg_label.setWordWrap(True)
        msg_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(msg_label)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(20)
        btn_layout.addStretch()
        self.buttons = []

        for i, text in enumerate(buttons):
            btn = QPushButton(text)
            btn.setObjectName("msgButton")
            btn.clicked.connect(lambda checked, idx=i: self._button_clicked(idx))
            btn_layout.addWidget(btn)
            self.buttons.append(btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(main_widget)

        self.setMinimumSize(300, 150)
        self.adjustSize()


    def _button_clicked(self, idx):
        self._result = (idx == 0)
        self.accept()


    @staticmethod
    def information(parent, title, message):
        QApplication.beep()
        dlg = CustomMessageBox(title, message, parent, buttons=('OK',))
        dlg.exec_()
        return True


    @staticmethod
    def warning(parent, title, message):
        QApplication.beep()
        dlg = CustomMessageBox(title, message, parent, buttons=('OK',))
        dlg.exec_()
        return True


    @staticmethod
    def critical(parent, title, message):
        QApplication.beep()
        dlg = CustomMessageBox(title, message, parent, buttons=('OK',))
        dlg.exec_()
        return True


    @staticmethod
    def question(parent, title, message):
        dlg = CustomMessageBox(title, message, parent, buttons=('OK', 'Cancel'))
        dlg.exec_()
        return dlg._result