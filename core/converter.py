import os
import tempfile
import shutil
import threading

from PyQt5.QtCore import QThread, pyqtSignal

from core.ffmpeg_utils import convert_audio_file_with_cancel
from utils.constants import CancelException

class ConvertThread(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished_ok = pyqtSignal(object)
    error = pyqtSignal(str)
    canceled = pyqtSignal()


    def __init__(self, files, output_format, quality):
        super().__init__()
        self.files = files
        self.output_format = output_format
        self.quality = quality
        self._cancel_requested = False
        self._cancel_event = threading.Event()


    def cancel(self):
        self._cancel_requested = True
        self._cancel_event.set()


    def run(self):
        try:
            self.temp_dir = tempfile.mkdtemp(prefix='audio_convert_')

        except Exception as e:
            self.error.emit(f"Failed to create temp dir: {e}")
            return
        
        total_files = len(self.files)
        errors = []

        try:
            for idx, file_path in enumerate(self.files):
                if self._cancel_requested:
                    raise CancelException("Отменено пользователем")
                
                self.status.emit(f"Обработка файла {idx+1}/{total_files}: {os.path.basename(file_path)}")

                try:
                    output_path = convert_audio_file_with_cancel(
                        file_path,
                        self.temp_dir,
                        self.output_format,
                        self.quality,
                        self._cancel_event
                    )

                    if output_path is None:
                        if self._cancel_requested:
                            raise CancelException("Отменено пользователем")
                        
                        else:
                            errors.append(f"Не удалось конвертировать {os.path.basename(file_path)}")
                            continue

                    self.status.emit(f"Сохранено: {output_path}")
                    overall = int((idx + 1) / total_files * 100)
                    self.progress.emit(overall)

                except Exception as e:
                    errors.append(f"Ошибка при конвертации {os.path.basename(file_path)}: {e}")
                    continue

            if errors:
                self.error.emit("Некоторые файлы не были конвертированы:\n" + "\n".join(errors))
                
            else:
                self.finished_ok.emit(self.temp_dir)

        except CancelException:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)

            self.canceled.emit()

        except Exception as e:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                
            self.error.emit(str(e))