import sys
import subprocess

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QUrl
from PyQt5.QtGui import (
    QFontDatabase, QFont,
    QIcon, QDesktopServices
)

from ui.tray_application import TrayApplication
from ui.custom_message_box import CustomMessageBox

from core.updater import check_for_updates, perform_update

from utils.logger import setup_logger
from utils.constants import (
    FONT_PATH, FFMPEG_PATH,
    STYLE_PATH, ICON_PATH
)

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

    app.setWindowIcon(QIcon(ICON_PATH))

    font_id = QFontDatabase.addApplicationFont(FONT_PATH)
    if font_id != -1:
        families = QFontDatabase.applicationFontFamilies(font_id)

        if families:
            app.setFont(QFont(families[0], 11))

        else:
            logger.warning("Не удалось получить имя загруженного шрифта")

    else:
        logger.warning("Не удалось загрузить Helvetica.otf, используется системный шрифт")

    try:
        with open(STYLE_PATH, "r", encoding="utf-8") as file:
            app.setStyleSheet(file.read())

    except FileNotFoundError:
        logger.warning("Warning: style.qss not found, using default style.")

    if not check_ffmpeg():
        CustomMessageBox.critical(None, "FFmpeg not found",
            "FFmpeg is required for conversion and audio extraction.\n"
            "Please place ffmpeg.exe in the application folder or add it to PATH.")

    try:
        latest_ver, download_url = check_for_updates()
        if latest_ver:
            msg = f"New version released: v{latest_ver}\nUpdate?"
            if CustomMessageBox.question(None, "New version!", msg):
                if perform_update(download_url, sys.executable):
                    app.quit()
                    sys.exit(0)

                else:
                    CustomMessageBox.critical(None, "Error", "Update failed. Try manually.")
                    
    except Exception as e:
        logger.warning(f"Update check failed: {e}")

    tray_app = TrayApplication(app)
    sys.exit(app.exec_())