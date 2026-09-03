import os
import tempfile
import shutil
import threading
import yt_dlp

from PyQt5.QtCore import QThread, pyqtSignal

from core.ffmpeg_utils import convert_audio_file_with_cancel
from utils.constants import (
    BASE_DIR,
    FFMPEG_PATH,
    CancelException
)

from utils.logger import setup_logger

logger = setup_logger(__name__)


class DownloadThread(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished_ok = pyqtSignal(object)
    error = pyqtSignal(str)
    canceled = pyqtSignal()


    def __init__(self, url, format_type, quality):
        super().__init__()

        self.url = url
        self.format_type = format_type
        self.quality = quality
        self._cancel_requested = False
        self._cancel_event = None


    def cancel(self):
        self._cancel_requested = True

        if self._cancel_event:
            self._cancel_event.set()


    def run(self):
        try:
            self.temp_dir = tempfile.mkdtemp(prefix='video_download_')

        except Exception as e:
            self.error.emit(f"Failed to create temp dir: {e}")
            return
        
        try:
            self.status.emit(f"Начинаю скачивание: {self.url}")
            ydl_opts = {
                'outtmpl': os.path.join(self.temp_dir, '%(title)s.%(ext)s'),
                'noplaylist': True,
                'ffmpeg_location': os.path.dirname(FFMPEG_PATH),
                'progress_hooks': [self._progress_hook],
            }

            if self.format_type == 'mp4':
                ydl_opts['format'] = self.quality
                ydl_opts['merge_output_format'] = 'mp4'

            else:
                ydl_opts['format'] = 'bestaudio/best'

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                downloaded_file = ydl.prepare_filename(info)

            final_file = downloaded_file

            if self.format_type in ('mp3', 'wav'):
                self.status.emit(f"Конвертация в {self.format_type}...")

                self._cancel_event = threading.Event()
                converted = convert_audio_file_with_cancel(
                    downloaded_file,
                    self.temp_dir,
                    self.format_type,
                    self.quality,
                    self._cancel_event
                )

                if converted is None:
                    if self._cancel_requested:
                        raise CancelException("Отменено пользователем")
                    
                    else:
                        raise Exception("Ошибка конвертации аудио")
                    
                os.remove(downloaded_file)
                final_file = converted

            self.finished_ok.emit((final_file, self.temp_dir))

        except CancelException:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)

            self.canceled.emit()

        except Exception as e:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)

            self.error.emit(str(e))


    def _progress_hook(self, d):
        try:
            if self._cancel_requested:
                raise CancelException("Отменено пользователем")

            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate')
                downloaded = d.get('downloaded_bytes', 0)

                if total:
                    percent = int(downloaded / total * 100)
                    self.progress.emit(percent)

            elif d['status'] == 'finished':
                self.progress.emit(100)

        except CancelException:
            raise

        except Exception as e:
            logger.error(f"Progress hook error: {e}")
