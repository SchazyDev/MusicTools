"""Генерация latest.json — ассета-манифеста для автообновления.

Пример:
    python scripts/make_manifest.py --version 1.1.0 \
        --file dist/MusicTools_v1_1_0_setup.exe \
        --url https://github.com/SchazyDev/MusicTools/releases/download/v1.1.0/MusicTools_v1_1_0_setup.exe
"""

import argparse
import hashlib
import json
import os
import sys

PLATFORM_KEY = "windows-x86_64"
HASH_CHUNK = 1024 * 1024


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(version: str, file_path: str, url: str, notes: str, channel: str) -> dict:
    version = version.strip().lstrip("vV")
    if not version:
        raise ValueError("Версия не может быть пустой")

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    return {
        "version": version,
        "channel": channel,
        "notes": notes,
        "platforms": {
            PLATFORM_KEY: {
                "url": url,
                "sha256": sha256_of(file_path),
                "size": os.path.getsize(file_path),
            }
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Создать latest.json для релиза Music Tools")
    parser.add_argument("--version", required=True, help="Версия релиза, например 1.1.0")
    parser.add_argument("--file", required=True, help="Путь к собранному установщику")
    parser.add_argument("--url", required=True, help="Прямая ссылка на этот файл в релизе")
    parser.add_argument("--notes", default="", help="Краткое описание изменений")
    parser.add_argument("--channel", default="stable", help="Канал обновлений (по умолчанию stable)")
    parser.add_argument("--out", default="latest.json", help="Куда записать манифест")
    args = parser.parse_args(argv)

    try:
        manifest = build_manifest(args.version, args.file, args.url, args.notes, args.channel)
    except (OSError, ValueError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1

    text = json.dumps(manifest, ensure_ascii=False, indent=2)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
