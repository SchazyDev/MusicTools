import os
import sys

APP_NAME = "Fauna Tools"
VERSION = "1.0.0"
STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)

else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ICON_PATH = os.path.join(BASE_DIR, "icons/logo.png")
FONT_PATH = os.path.join(BASE_DIR, "resources/helvetica.otf")

FFMPEG_PATH = os.path.join(BASE_DIR, 'ffmpeg.exe')
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
