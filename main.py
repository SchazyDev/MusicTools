import sys
import subprocess

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFontDatabase, QFont

from ui.tray_application import TrayApplication
from ui.custom_message_box import CustomMessageBox

from utils.logger import setup_logger
from utils.constants import FONT_PATH, FFMPEG_PATH

logger = setup_logger(__name__)


def check_ffmpeg():
    try:
        subprocess.run(
            [FFMPEG_PATH, '-version'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        return True
    
    except Exception:
        return False


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    font_id = QFontDatabase.addApplicationFont(FONT_PATH)

    if font_id != -1:
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            font_family = families[0]
            default_font = QFont(font_family, 11)
            app.setFont(default_font)

        else:
            logger.warning("Не удалось получить имя загруженного шрифта")

    else:
        logger.warning("Не удалось загрузить Helvetica.ttf, используется системный шрифт")

    try:
        with open("resources/style.qss", "r", encoding="utf-8") as file:
            app.setStyleSheet(file.read())

    except FileNotFoundError:
        logger.warning("Warning: style.qss not found, using default style.")

    if not check_ffmpeg():
        CustomMessageBox.critical(None, "FFmpeg not found",
            "FFmpeg is required for conversion and audio extraction.\n"
            "Please place ffmpeg.exe in the application folder or add it to PATH.")

    else:
        tray_app = TrayApplication(app)
        sys.exit(app.exec_())