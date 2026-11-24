import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from packaging.version import InvalidVersion, parse as parse_version_str

from utils.constants import VERSION, GITHUB_REPO
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Платформенный ключ, используемый в latest.json.
PLATFORM_KEY = "windows-x86_64"
MANIFEST_ASSET_NAME = "latest.json"

USER_AGENT = "MusicTools-Updater"
API_TIMEOUT = 8
DOWNLOAD_TIMEOUT = 30
CHUNK_SIZE = 1024 * 256


class UpdateIntegrityError(Exception):
    """Скачанный файл не совпал по SHA-256 с манифестом."""


@dataclass
class UpdateInfo:
    version: str
    url: str
    sha256: str
    size: int
    notes: str
    channel: str


def parse_version(raw: Optional[str]) -> Optional[object]:
    """Убрать ведущую 'v' и распарсить через packaging.version.parse.

    Возвращает None для невалидных значений. Никогда не сравнивает сырые строки.
    """
    if not raw or not isinstance(raw, str):
        return None

    cleaned = raw.strip()
    if cleaned.startswith(("v", "V")):
        cleaned = cleaned[1:]

    try:
        return parse_version_str(cleaned)
    except InvalidVersion:
        return None


def is_newer(remote: str, local: str) -> bool:
    """True только если remote — валидная версия строго больше local."""
    remote_parsed = parse_version(remote)
    local_parsed = parse_version(local)

    if remote_parsed is None or local_parsed is None:
        return False

    return remote_parsed > local_parsed


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False

    try:
        int(value, 16)
    except ValueError:
        return False

    return True


def _request_json(url: str, timeout: int) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_latest_manifest(repo: str = GITHUB_REPO, timeout: int = API_TIMEOUT) -> Optional[UpdateInfo]:
    """Получить UpdateInfo из ассета latest.json последнего релиза.

    Единственный источник URL/хэша/размера — latest.json. Если ассета нет,
    вернуть None (релиз не является автообновляемым).
    """
    release = _request_json(
        f"https://api.github.com/repos/{repo}/releases/latest",
        timeout,
    )

    assets = release.get("assets", []) or []
    manifest_url = None

    for asset in assets:
        if asset.get("name") == MANIFEST_ASSET_NAME:
            manifest_url = asset.get("browser_download_url")
            break

    if not manifest_url:
        logger.warning(
            "Релиз %s не содержит ассет %s — обновление недоступно",
            release.get("tag_name", "?"),
            MANIFEST_ASSET_NAME,
        )
        return None

    manifest = _request_json(manifest_url, timeout)

    version = manifest.get("version")
    if not parse_version(version):
        logger.warning("Манифест обновления: некорректная версия %r", version)
        return None

    platform = (manifest.get("platforms") or {}).get(PLATFORM_KEY)
    if not isinstance(platform, dict):
        logger.warning("Манифест обновления: нет записи для %s", PLATFORM_KEY)
        return None

    url = platform.get("url")
    sha256 = platform.get("sha256")
    size = platform.get("size")

    if not isinstance(url, str) or not url.startswith("https://"):
        logger.warning("Манифест обновления: некорректный url")
        return None

    if not _is_sha256(sha256):
        logger.warning("Манифест обновления: некорректный sha256")
        return None

    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        logger.warning("Манифест обновления: некорректный size")
        return None

    return UpdateInfo(
        version=version.strip().lstrip("vV"),
        url=url,
        sha256=sha256.lower(),
        size=size,
        notes=str(manifest.get("notes", "")),
        channel=str(manifest.get("channel", "stable")),
    )


def check_for_updates(repo: str = GITHUB_REPO) -> Optional[UpdateInfo]:
    """Вернуть UpdateInfo, если доступна более новая версия, иначе None.

    Все сетевые и parse-ошибки логируются и проглатываются.
    """
    try:
        info = fetch_latest_manifest(repo)
        if info is None:
            return None

        if not is_newer(info.version, VERSION):
            logger.info("Обновление не требуется: %s <= %s", info.version, VERSION)
            return None

        logger.info("Доступна новая версия: %s", info.version)
        return info

    except Exception as e:
        logger.warning(f"GitHub update check failed: {e}")
        return None


def download_update(
    info: UpdateInfo,
    dest_path: str,
    on_progress: Optional[Callable[[int], None]] = None,
) -> str:
    """Скачать обновление в dest_path, проверив SHA-256.

    При несовпадении хэша файл удаляется и поднимается UpdateIntegrityError.
    """
    request = urllib.request.Request(info.url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    downloaded = 0

    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response, \
                open(dest_path, "wb") as out_file:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break

                out_file.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)

                if on_progress and info.size > 0:
                    on_progress(min(100, int(downloaded * 100 / info.size)))

    except Exception:
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        raise

    actual = digest.hexdigest()
    if actual.lower() != info.sha256.lower():
        try:
            os.remove(dest_path)
        except OSError:
            pass

        logger.error("SHA-256 не совпал: ожидался %s, получен %s", info.sha256, actual)
        raise UpdateIntegrityError("SHA-256 скачанного файла не совпадает с манифестом")

    logger.info("Обновление скачано и проверено: %s", dest_path)
    return dest_path


def _build_batch_script(current_exe: str, new_exe: str, temp_dir: str) -> str:
    """Сформировать bat-содержимое с ограниченным числом попыток и бэкапом."""
    return f'''@echo off
setlocal
set /a attempts=0

:wait
set /a attempts+=1
if %attempts% GTR 30 goto giveup
move /Y "{current_exe}" "{current_exe}.old" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)

move /Y "{new_exe}" "{current_exe}" >nul 2>&1
if errorlevel 1 (
    move /Y "{current_exe}.old" "{current_exe}" >nul 2>&1
    goto giveup
)

if exist "{current_exe}.old" del /f /q "{current_exe}.old" >nul 2>&1
start "" "{current_exe}"
rd /s /q "{temp_dir}" >nul 2>&1
del "%~f0" >nul 2>&1
exit /b 0

:giveup
exit /b 1
'''


def perform_update(
    info: UpdateInfo,
    current_exe_path: str,
    on_progress: Optional[Callable[[int], None]] = None,
):
    """Скачать проверенное обновление и запустить отложенную установку.

    Возвращает (ok, message). В dev-режиме (sys.frozen отсутствует) отказывает.
    """
    if not getattr(sys, "frozen", False):
        logger.warning("perform_update вызван вне собранного приложения — отказ")
        return False, "Обновление доступно только в собранной версии приложения."

    temp_dir = None

    try:
        temp_dir = tempfile.mkdtemp(prefix="music_tools_update_")
        new_exe_path = os.path.join(temp_dir, os.path.basename(current_exe_path) or "MusicTools_new.exe")

        download_update(info, new_exe_path, on_progress=on_progress)

        script_body = _build_batch_script(current_exe_path, new_exe_path, temp_dir)
        fd, script_path = tempfile.mkstemp(suffix=".bat", prefix="music_tools_update_", dir=temp_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as script_file:
            script_file.write(script_body)

        subprocess.Popen(
            [script_path],
            shell=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        logger.info("Скрипт обновления запущен: %s", script_path)
        return True, "Обновление загружено, приложение будет перезапущено."

    except UpdateIntegrityError as e:
        logger.error("Проверка целостности обновления не пройдена: %s", e)
        return False, "Загруженный файл повреждён, обновление отменено."

    except Exception as e:
        logger.error("Ошибка обновления: %s", e)
        if temp_dir and os.path.isdir(temp_dir):
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
        return False, "Не удалось выполнить обновление."
