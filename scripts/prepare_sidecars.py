"""Подготовка сайдкаров к сборке установщика Music Tools.

Скачивает `yt-dlp.exe` и копирует `ffmpeg.exe` в `src-tauri/resources`,
чтобы `cargo tauri build` положил их в установщик. Без этих файлов
инструменты скачивания и конвертации в релизе не работают: приложение
ищет программы в каталоге ресурсов, рядом с exe и в `PATH`.

Использование:

    python scripts/prepare_sidecars.py            # скачать/скопировать всё
    python scripts/prepare_sidecars.py --check    # только проверить наличие
    python scripts/prepare_sidecars.py --force    # перезаписать существующие

Скрипт ничего не пишет вне `src-tauri/resources` и не требует прав
администратора.
"""

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "src-tauri" / "resources"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
USER_AGENT = "MusicTools-Sidecars"
REPO_ROOT = Path(__file__).resolve().parent.parent


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, open(destination, "wb") as out:
        shutil.copyfileobj(response, out)


def prepare_ytdlp(force: bool) -> bool:
    target = RESOURCES_DIR / "yt-dlp.exe"
    if target.exists() and not force:
        print(f"yt-dlp: уже есть ({target.stat().st_size} байт)")
        return True

    print("yt-dlp: скачивание...")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            part = Path(temp_dir) / "yt-dlp.exe"
            download(YTDLP_URL, part)
            shutil.move(str(part), target)
    except Exception as exc:  # noqa: BLE001
        print(f"yt-dlp: ошибка загрузки: {exc}", file=sys.stderr)
        return False

    print(f"yt-dlp: сохранён ({target.stat().st_size} байт)")
    return True


def prepare_ffmpeg(force: bool) -> bool:
    target = RESOURCES_DIR / "ffmpeg.exe"
    if target.exists() and not force:
        print(f"ffmpeg: уже есть ({target.stat().st_size} байт)")
        return True

    candidates = [
        REPO_ROOT / "ffmpeg.exe",
        Path(shutil.which("ffmpeg") or ""),
    ]
    for source in candidates:
        if source and source.is_file():
            shutil.copy2(source, target)
            print(f"ffmpeg: скопирован из {source} ({target.stat().st_size} байт)")
            return True

    print(
        "ffmpeg: не найден. Положите ffmpeg.exe в корень репозитория "
        "или установите его в PATH.",
        file=sys.stderr,
    )
    return False


def check() -> int:
    missing = []
    for name in ("yt-dlp.exe", "ffmpeg.exe"):
        path = RESOURCES_DIR / name
        if path.is_file() and path.stat().st_size > 0:
            print(f"{name}: есть ({path.stat().st_size} байт)")
        else:
            print(f"{name}: отсутствует")
            missing.append(name)
    return 1 if missing else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Подготовить сайдкары для установщика")
    parser.add_argument("--check", action="store_true", help="только проверить наличие")
    parser.add_argument("--force", action="store_true", help="перезаписать существующие файлы")
    args = parser.parse_args(argv)

    if args.check:
        return check()

    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    ok = prepare_ytdlp(args.force)
    ok = prepare_ffmpeg(args.force) and ok

    if not ok:
        print("Подготовка не завершена: сборка установщика даст нерабочее приложение.", file=sys.stderr)
        return 1

    print("Сайдкары готовы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
