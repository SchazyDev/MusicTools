import os
import sys
import shutil

APP_NAME = "Fauna Tools"
VERSION = "1.0.0"
STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

GITHUB_REPO = "SchazyDev/MusicTools"

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_meipass():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    
    return get_base_dir()


BASE_DIR = get_base_dir()
MEIPASS = get_meipass()

ICON_PATH = os.path.join(MEIPASS, "icons", "logo.png")
FONT_PATH = os.path.join(MEIPASS, "resources", "helvetica.otf")
STYLE_PATH = os.path.join(MEIPASS, "resources", "style.qss")


FFMPEG_FILE_NAME = "ffmpeg.exe"
FFMPEG_TARGET_PATH = os.path.join(BASE_DIR, FFMPEG_FILE_NAME)


if getattr(sys, 'frozen', False) and not os.path.exists(FFMPEG_TARGET_PATH):
    try:
        src = os.path.join(MEIPASS, FFMPEG_FILE_NAME)

        if os.path.exists(src):
            shutil.copy2(src, FFMPEG_TARGET_PATH)

    except Exception:
        pass


FFMPEG_PATH = FFMPEG_TARGET_PATH
if not os.path.exists(FFMPEG_PATH):
    FFMPEG_PATH = 'ffmpeg'


ABOUT_LOGO_URL = "https://vk.ru/faunamusic"
ABOUT_TEXT_URL = "https://vk.ru/schazyprod"
ABOUT_SUPPORT_URL = "https://t.me/schazyprod"
ABOUT_REPORT_URL = "https://t.me/schzdsh"

ALLOWED_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv', '.webm',
                      '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a'}


class CancelException(Exception):
    pass
