import sys
import subprocess

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import (
    QFontDatabase, QFont,
    QIcon
)

from ui.tray_application import TrayApplication
from ui.custom_message_box import CustomMessageBox

from core.updater import (
    UpdateCheckWorker,
    UpdateDownloadWorker,
    UpdateInfo
)

from utils.logger import setup_logger
from utils.constants import (
    FONT_PATH, FFMPEG_PATH,
    STYLE_PATH, ICON_PATH
)

logger = setup_logger(__name__)

# Держим ссылки на воркеры, чтобы их не собрал GC во время работы.
_update_workers = []


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


def on_update_checked(app, tray_app, update_info):
    if not isinstance(update_info, UpdateInfo):
        return

    tray_app.notify("New version!", f"New version released: v{update_info.version}")

    msg = f"New version released: v{update_info.version}\nUpdate?"

    if not CustomMessageBox.question(None, "New version!", msg):
        return

    download_worker = UpdateDownloadWorker(update_info, sys.executable)
    _update_workers.append(download_worker)
    tray_app.set_progress_hint("Downloading update...")

    def on_progress(value):
        tray_app.set_progress_hint(f"Downloading update... {value}%")

    def on_downloaded(result):
        ok, message = result

        if ok:
            tray_app.notify("New version!", message)
            app.quit()
            return

        tray_app.set_progress_hint("")
        CustomMessageBox.critical(None, "Error", message)

    download_worker.progress.connect(on_progress)
    download_worker.finished_ok.connect(on_downloaded)
    download_worker.start()


def on_update_check_failed(message):
    logger.warning(f"Update check failed: {message}")


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

        logger.error("FFmpeg not found or unusable; exiting.")
        sys.exit(1)

    tray_app = TrayApplication(app)

    check_worker = UpdateCheckWorker()
    _update_workers.append(check_worker)
    check_worker.finished_ok.connect(
        lambda info: on_update_checked(app, tray_app, info)
    )
    check_worker.error.connect(on_update_check_failed)
    check_worker.start()

    sys.exit(app.exec_())
