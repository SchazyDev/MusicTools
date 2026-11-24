import sys
import os
import winreg

from PyQt5.QtWidgets import (
    QMenu, QAction,
    QSystemTrayIcon
)

from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QPropertyAnimation, Qt

from utils.logger import setup_logger
from ui.main_window import MainWindow
from utils.constants import (
    APP_NAME,
    ICON_PATH,
    STARTUP_REG_KEY,
    VERSION
)

logger = setup_logger(__name__)

# ВНИМАНИЕ: имя значения (ValueName) в реестре для автозапуска берётся из
# единого источника APP_NAME (utils/constants.py) и ДОЛЖНО совпадать с
# ValueName в секции [Registry] файла installer.iss, иначе появятся два
# конкурирующих автозапуска. Значение пишется в кавычках: '"<sys.executable>"'.


class TrayApplication:
    def __init__(self, app):
        self.app = app
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setWindowIcon(QIcon(ICON_PATH))

        self.window = MainWindow()

        self._orphaned_workers = []

        self.tray_icon = QSystemTrayIcon(QIcon(ICON_PATH), self.app)
        self.tray_icon.setToolTip("Music Tools")

        self.tray_menu = QMenu()
        self.tray_menu.setAttribute(Qt.WA_StyledBackground)

        self.open_action = QAction("\tOpen\t", self.app)
        self.open_action.triggered.connect(self.show_window)
        self.tray_menu.addAction(self.open_action)
        self.tray_menu.addSeparator()

        self.startup_action = QAction("\tRun on startup\t", self.app)
        self.startup_action.setCheckable(True)
        self.startup_action.setChecked(self.is_in_startup())
        self.startup_action.triggered.connect(self.toggle_startup)
        self.tray_menu.addAction(self.startup_action)
        self.tray_menu.addSeparator()
        
        self.quit_action = QAction("\tQuit\t", self.app)
        self.quit_action.triggered.connect(self.quit_application)
        self.tray_menu.addAction(self.quit_action)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()

        self.window.closeEvent = self.close_event

        self.tray_icon.showMessage(
            APP_NAME,
            f"Application started (v{VERSION})",
            QSystemTrayIcon.Information,
            2000
        )


    def add_to_startup(self):
        if sys.platform != 'win32':
            logger.warning("Startup registration is only supported on Windows")
            return

        try:
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                STARTUP_REG_KEY,
                0,
                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
            )

            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
            winreg.CloseKey(key)

        except Exception as e:
            logger.error(f"Failed to add to startup: {e}")


    def is_in_startup(self):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_REG_KEY,
                0,
                winreg.KEY_QUERY_VALUE
            )
            
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        
        except FileNotFoundError:
            return False

        except OSError as e:
            logger.error(f"Failed to read startup state: {e}")
            return False


    def toggle_startup(self, checked):
        if checked:
            self.add_to_startup()

        else:
            self.remove_from_startup()

        self.startup_action.setChecked(checked)


    def remove_from_startup(self):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_REG_KEY,
                0,
                winreg.KEY_SET_VALUE
            )

            winreg.DeleteValue(key, APP_NAME)
            winreg.CloseKey(key)

        except Exception as e:
            logger.error(f"Failed to remove from startup: {e}")


    def show_window(self):
        self.window.show()
        
        screen = self.app.primaryScreen().availableGeometry()
        window_size = self.window.frameGeometry().size()

        x = screen.right() - window_size.width() - 10
        y = screen.bottom() - window_size.height() - 10

        self.window.move(x, y)
        self.window.animate_show()
        self.window.raise_()
        self.window.activateWindow()


    def hide_window(self):
        self.anim = QPropertyAnimation(self.window, b"windowOpacity")

        self.anim.setDuration(200)
        self.anim.setStartValue(1)
        self.anim.setEndValue(0)

        self.anim.finished.connect(self.window.hide)
        self.anim.start()


    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.window.isVisible():
                self.hide_window()

            else:
                self.show_window()

        elif reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.MiddleClick):
            self.show_window()


    def close_event(self, event):
        event.ignore()
        self.hide_window()


    def quit_application(self):
        self.stop_all_threads()
        self.tray_icon.hide()
        self.app.quit()


    def notify(self, title: str, message: str) -> None:
        """Показать всплывающее уведомление трея."""
        self.tray_icon.showMessage(
            title,
            message,
            QSystemTrayIcon.Information,
            5000
        )


    def set_progress_hint(self, text: str) -> None:
        """Обновить подсказку трея и короткую метку действия."""
        hint = text.strip()

        if hint:
            self.tray_icon.setToolTip(f"Music Tools — {hint}")
            self.open_action.setText("\tDownloading...\t")

        else:
            self.tray_icon.setToolTip("Music Tools")
            self.open_action.setText("\tOpen\t")


    def stop_all_threads(self):
        download_thread = getattr(getattr(self.window, 'download_tab', None), 'download_thread', None)
        preview_thread = getattr(getattr(self.window, 'download_tab', None), 'preview_thread', None)
        convert_thread = getattr(getattr(self.window, 'convert_tab', None), 'convert_thread', None)

        workers = [
            ("Download thread", download_thread),
            ("Preview thread", preview_thread),
            ("Convert thread", convert_thread),
            ("Update worker", getattr(self, 'update_worker', None)),
        ]

        for name, worker in workers:
            if worker is None or not worker.isRunning():
                continue

            worker.cancel()

            if not worker.wait(5000):
                # Не убиваем поток принудительно: terminate() может оставить
                # захваченные блокировки, временные файлы и битый вывод.
                # Ссылку сохраняем, чтобы процесс завершился штатно.
                logger.warning(f"{name} did not stop within timeout; leaving it alive")
                self._orphaned_workers.append(worker)