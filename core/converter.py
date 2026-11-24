import os
import tempfile
import shutil

from core.ffmpeg_utils import convert_audio_file
from core.tools.base import ToolWorker
from utils.constants import CancelException


class ConvertThread(ToolWorker):
    def __init__(self, files, output_format, quality):
        super().__init__()
        self.files = files
        self.output_format = output_format
        self.quality = quality
        self.temp_dir = None

    def _execute(self):
        try:
            self.temp_dir = tempfile.mkdtemp(prefix='audio_convert_')

        except Exception as e:
            self.error.emit(f"Failed to create temp dir: {e}")
            return

        total_files = len(self.files)
        errors = []

        for idx, file_path in enumerate(self.files):
            if self.is_cancel_requested():
                raise CancelException("Отменено пользователем")

            self.status.emit(f"Обработка файла {idx+1}/{total_files}: {os.path.basename(file_path)}")

            def on_progress(percent, idx=idx):
                overall = int(((idx + percent / 100) / total_files) * 100)
                self.progress.emit(overall)

            try:
                outcome = convert_audio_file(
                    file_path,
                    self.temp_dir,
                    self.output_format,
                    self.quality,
                    self.cancel_event,
                    on_progress=on_progress
                )

                if outcome.canceled:
                    raise CancelException("Отменено пользователем")

                if outcome.error is not None:
                    errors.append(
                        f"Не удалось конвертировать {os.path.basename(file_path)}: {outcome.error}"
                    )
                    continue

                self.status.emit(f"Сохранено: {outcome.path}")
                overall = int((idx + 1) / total_files * 100)
                self.progress.emit(overall)

            except CancelException:
                raise

            except Exception as e:
                errors.append(f"Ошибка при конвертации {os.path.basename(file_path)}: {e}")
                continue

        if errors:
            self.error.emit("Некоторые файлы не были конвертированы:\n" + "\n".join(errors))

        else:
            self.finished_ok.emit(self.temp_dir)

    def _on_cleanup(self):
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
