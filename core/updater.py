import base64
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

from core.tools.base import ToolWorker
from utils.constants import VERSION, GITHUB_REPO
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Платформенный ключ, используемый в latest.json.
PLATFORM_KEY = "windows-x86_64"
MANIFEST_ASSET_NAME = "latest.json"

# Отдельный модуль настроек не зависит от Qt и от этого файла,
# поэтому циклический импорт невозможен.
from core.settings import allowed_channels  # noqa: E402

# ---------------------------------------------------------------------------
# Подпись манифеста (Ed25519)
#
# Канонические байты подписываемого payload — это манифест (JSON-объект)
# БЕЗ ключа "signature", повторно сериализованный строго так:
#
#     json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
#
# Скрипт подписи scripts/make_manifest.py и проверка ниже обязаны
# использовать ровно эту канонизацию — иначе подпись не сойдётся.
# ---------------------------------------------------------------------------

# Открытый ключ подписи релизных манифестов (Ed25519, base64).
#
# Сгенерирован scripts/gen_keys.py --out-dir keys. Закрытая половина лежит
# в keys/private.key и в репозиторий не попадает (.gitignore). Ключ должен
# совпадать с PUBLIC_KEY в src-tauri/src/update/mod.rs — иначе обновление
# работает только в одной из версий.
PUBLIC_KEY = "P8wubYo5/aYU2V6EhrbMfmmDnUdr5wh70ujfa02yapg="

# Явный аварийный обход проверки подписи. Тесты monkeypatch-ят этот флаг.
# В продакшене должен оставаться False.
ALLOW_UNSIGNED = False

_PLACEHOLDER_KEYS = {"", "PLACEHOLDER_REPLACE_ME"}


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Канонические байты подписи: манифест без "signature".

    Одна и та же функция используется и при подписи, и при проверке,
    поэтому расхождение канонизации исключено.
    """
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _has_usable_key(public_key: str) -> bool:
    """Ключ задан и не является плейсхолдером."""
    return isinstance(public_key, str) and public_key.strip() not in _PLACEHOLDER_KEYS


def verify_manifest_signature(manifest_bytes: bytes, signature: dict, public_key: str) -> bool:
    """Проверить Ed25519-подпись манифеста. Никогда не бросает исключение.

    "signature" — объект манифеста с полями algorithm/key_id/value, где value —
    base64-подпись над manifest_bytes. Возвращает True только при корректной
    подписи допустимым ключом.
    """
    try:
        if not isinstance(manifest_bytes, (bytes, bytearray)):
            return False
        if not isinstance(signature, dict):
            return False
        if not _has_usable_key(public_key):
            return False

        algorithm = signature.get("algorithm")
        if algorithm != "ed25519":
            # Поддерживаем только Ed25519; minisign-стиль отклоняем явно.
            if algorithm == "minisign":
                logger.error(
                    "Подпись minisign больше не поддерживается, требуется ed25519"
                )
            return False

        value = signature.get("value")
        if not isinstance(value, str) or not value:
            return False

        try:
            sig_bytes = base64.b64decode(value, validate=True)
        except (ValueError, Exception):
            return False

        try:
            key_bytes = base64.b64decode(public_key, validate=True)
        except (ValueError, Exception):
            return False

        if len(sig_bytes) != 64 or len(key_bytes) != 32:
            return False

        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        try:
            pub = Ed25519PublicKey.from_public_bytes(key_bytes)
            pub.verify(sig_bytes, bytes(manifest_bytes))
        except InvalidSignature:
            return False

        return True

    except Exception as e:  # noqa: BLE001
        logger.error("Ошибка проверки подписи манифеста: %s", e)
        return False

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


def fetch_latest_manifest(
    repo: str = GITHUB_REPO,
    timeout: int = API_TIMEOUT,
    channel: str = "stable",
) -> Optional[UpdateInfo]:
    """Получить UpdateInfo из ассета latest.json последнего релиза.

    Единственный источник URL/хэша/размера — latest.json. Если ассета нет,
    вернуть None (релиз не является автообновляемым).

    Порядок доверия: сначала ПОДПИСЬ над каноническими байтами манифеста,
    и только затем url/sha256/size. Подпись проверяется для channel=="stable"
    в режиме fail-closed, если PUBLIC_KEY — плейсхолдер и ALLOW_UNSIGNED=False.
    Манифест другого канала отклоняется (stable-пользователь не получает beta).
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

    if not isinstance(manifest, dict):
        logger.error("Манифест обновления: ожидался JSON-объект")
        return None

    # --- Проверка подписи ДО доверия url/sha256/size ---
    signature = manifest.get("signature")
    if isinstance(signature, dict):
        manifest_bytes = canonical_manifest_bytes(manifest)
        if not verify_manifest_signature(manifest_bytes, signature, PUBLIC_KEY):
            logger.error(
                "Манифест обновления: подпись недействительна — обновление отклонено"
            )
            return None
        logger.info("Манифест обновления: подпись проверена")
    else:
        # Нет подписи.
        # Пока PUBLIC_KEY — плейсхолдер, проверять нечего: подписанные релизы
        # ещё не выпускаются, и мы не блокируем существующие установки.
        # Как только задан реальный ключ, канал stable работает fail-closed.
        if channel == "stable" and not ALLOW_UNSIGNED and _has_usable_key(PUBLIC_KEY):
            logger.error(
                "Манифест обновления без подписи: канал stable fail-closed, "
                "обновление не будет предложено"
            )
            return None
        if not ALLOW_UNSIGNED:
            logger.warning(
                "Манифест обновления без подписи (канал %s, клиент не подписан) — пропуск",
                channel,
            )

    version = manifest.get("version")
    if not parse_version(version):
        logger.warning("Манифест обновления: некорректная версия %r", version)
        return None

    manifest_channel = manifest.get("channel")
    if manifest_channel is not None and str(manifest_channel) != channel:
        logger.warning(
            "Манифест обновления: канал %r не совпадает с запрошенным %r",
            manifest_channel,
            channel,
        )
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
        channel=str(manifest_channel if manifest_channel is not None else channel),
    )


def check_for_updates(repo: str = GITHUB_REPO, channel: str = "stable") -> Optional[UpdateInfo]:
    """Вернуть UpdateInfo, если доступна более новая версия, иначе None.

    Все сетевые и parse-ошибки логируются и проглатываются.
    """
    try:
        info = fetch_latest_manifest(repo, channel=channel)
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


class UpdateCheckWorker(ToolWorker):
    """Фоновая проверка обновлений без блокировки цикла событий."""

    def _execute(self) -> None:
        """Запустить check_for_updates() и отдать UpdateInfo | None."""
        self.finished_ok.emit(check_for_updates())


class UpdateDownloadWorker(ToolWorker):
    """Фоновая загрузка обновления с последующей отложенной установкой."""

    def __init__(self, info: UpdateInfo, current_exe_path: str) -> None:
        super().__init__()
        self.info = info
        self.current_exe_path = current_exe_path

    def _execute(self) -> None:
        """Скачать обновление и отдать (ok, message)."""
        ok, message = perform_update(
            self.info,
            self.current_exe_path,
            on_progress=self._forward_progress,
        )
        self.finished_ok.emit((ok, message))

    def _forward_progress(self, value: int) -> None:
        self.progress.emit(int(value))
