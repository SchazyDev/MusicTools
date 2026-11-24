"""Настройки Music Tools: канал обновлений и авто-проверка при запуске.

Модуль намеренно не зависит от Qt и от updater — его можно импортировать
в тестах и в скриптах. Хранение: ``%APPDATA%\\MusicTools\\settings.json``.

Поля:
    channel — канал обновлений, один из ``allowed_channels()``.
    check_on_startup — проверять обновления при старте приложения.

Загрузка никогда не бросает исключение: при отсутствии или порче файла
возвращаются значения по умолчанию. Неизвестные ключи игнорируются.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from utils.logger import setup_logger

logger = setup_logger(__name__)

APP_DIR_NAME = "MusicTools"
SETTINGS_FILE_NAME = "settings.json"
DEFAULT_CHANNEL = "stable"
_CHANNELS: Tuple[str, str] = ("stable", "beta")


def allowed_channels() -> Tuple[str, ...]:
    """Допустимые каналы обновлений."""
    return _CHANNELS


def _settings_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata)
    else:
        base = Path.home() / "AppData" / "Roaming"
    return base / APP_DIR_NAME


def _settings_path() -> Path:
    return _settings_dir() / SETTINGS_FILE_NAME


@dataclass
class Settings:
    """Персистентные настройки приложения."""

    channel: str = DEFAULT_CHANNEL
    check_on_startup: bool = True

    @classmethod
    def load(cls) -> "Settings":
        """Прочитать настройки; при отсутствии/порче файла вернуть defaults.

        Файл записывается при первом запуске, чтобы у пользователя был
        редактируемый шаблон. Метод никогда не поднимает исключение.
        """
        path = _settings_path()

        if not path.is_file():
            settings = cls()
            settings.save()
            return settings

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("Настройки повреждены, используются значения по умолчанию: %s", e)
            return cls()

        if not isinstance(raw, dict):
            logger.warning("Настройки: ожидался объект JSON, используются значения по умолчанию")
            return cls()

        channel = raw.get("channel", DEFAULT_CHANNEL)
        if channel not in _CHANNELS:
            logger.warning("Настройки: неизвестный канал %r, откат к %s", channel, DEFAULT_CHANNEL)
            channel = DEFAULT_CHANNEL

        check_on_startup = raw.get("check_on_startup", True)
        if not isinstance(check_on_startup, bool):
            check_on_startup = True

        return cls(channel=channel, check_on_startup=check_on_startup)

    def save(self) -> None:
        """Атомарно записать настройки в settings.json.

        Пишем во временный файл в том же каталоге и подменяем через os.replace,
        чтобы читатель никогда не увидел частично записанный JSON.
        """
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "channel": self.channel if self.channel in _CHANNELS else DEFAULT_CHANNEL,
            "check_on_startup": bool(self.check_on_startup),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

        fd, tmp_path = tempfile.mkstemp(
            prefix=".settings_", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(text)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
