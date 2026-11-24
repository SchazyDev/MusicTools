"""Генерация latest.json — ассета-манифеста для автообновления.

Пример с подписью:
    python scripts/make_manifest.py --version 1.1.0 \
        --file dist/MusicTools_v1_1_0_setup.exe \
        --url https://github.com/SchazyDev/MusicTools/releases/download/v1.1.0/MusicTools_v1_1_0_setup.exe \
        --key keys/private.key --channel stable --out latest.json

Проверка подписи готового манифеста:
    python scripts/make_manifest.py --verify latest.json --public-key keys/public.key

Канонические байты подписи — манифест БЕЗ ключа "signature", повторно
сериализованный строго:
    json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
Это ровно та канонизация, что использует core.updater.verify_manifest_signature.
"""

import argparse
import base64
import hashlib
import json
import os
import sys

PLATFORM_KEY = "windows-x86_64"
HASH_CHUNK = 1024 * 1024
SIGNATURE_ALGORITHM = "ed25519"


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Канонические байты подписи (манифест без "signature")."""
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


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


def sign_manifest(manifest: dict, private_key_path: str) -> dict:
    """Дописать в манифест блок signature над каноническими байтам тела."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except Exception:  # noqa: BLE001
        raise RuntimeError(
            "Для подписи установите пакет cryptography: pip install cryptography"
        )

    with open(private_key_path, "r", encoding="utf-8") as f:
        seed = base64.b64decode(f.read().strip(), validate=True)

    if len(seed) != 32:
        raise ValueError("Приватный ключ должен быть raw 32-байтовым seed в base64")

    key = Ed25519PrivateKey.from_private_bytes(seed)
    payload = canonical_manifest_bytes(manifest)
    signature = key.sign(payload)

    # key_id — короткий отпечаток открытого ключа для удобства ротации.
    from cryptography.hazmat.primitives import serialization
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = hashlib.sha256(pub).hexdigest()[:16]

    signed = dict(manifest)
    signed["signature"] = {
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": key_id,
        "value": base64.b64encode(signature).decode("ascii"),
    }
    return signed


def verify_file(manifest_path: str, public_key_path: str) -> int:
    """Прочитать манифест и проверить подпись. 0 — валидна, 1 — нет."""
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        with open(public_key_path, "r", encoding="utf-8") as f:
            public_key = f.read().strip()
    except (OSError, ValueError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1

    if not isinstance(manifest, dict):
        print("Подпись недействительна: ожидался JSON-объект", file=sys.stderr)
        return 1

    signature = manifest.get("signature")
    if not isinstance(signature, dict):
        print("Подпись отсутствует: манифест не подписан", file=sys.stderr)
        return 1

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key_bytes = base64.b64decode(public_key, validate=True)
        sig_bytes = base64.b64decode(signature.get("value", ""), validate=True)
        pub = Ed25519PublicKey.from_public_bytes(key_bytes)
        pub.verify(sig_bytes, canonical_manifest_bytes(manifest))
    except InvalidSignature:
        print("Подпись недействительна", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"Ошибка проверки подписи: {e}", file=sys.stderr)
        return 1

    print("Подпись действительна")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Создать latest.json для релиза Music Tools")
    parser.add_argument("--version", help="Версия релиза, например 1.1.0")
    parser.add_argument("--file", help="Путь к собранному установщику")
    parser.add_argument("--url", help="Прямая ссылка на этот файл в релизе")
    parser.add_argument("--notes", default="", help="Краткое описание изменений")
    parser.add_argument("--channel", default="stable", help="Канал обновлений (по умолчанию stable)")
    parser.add_argument("--key", help="Путь к приватному ключу Ed25519 (seed base64)")
    parser.add_argument("--no-sign", action="store_true", help="Выпустить НЕподписанный манифест")
    parser.add_argument("--verify", metavar="MANIFEST", help="Проверить подпись существующего манифеста")
    parser.add_argument("--public-key", metavar="PATH", help="Путь к открытому ключу для --verify")
    parser.add_argument("--out", default="latest.json", help="Куда записать манифест")
    args = parser.parse_args(argv)

    if args.verify:
        if not args.public_key:
            print("Ошибка: для --verify требуется --public-key", file=sys.stderr)
            return 2
        return verify_file(args.verify, args.public_key)

    if not args.version or not args.file or not args.url:
        parser.error("требуются --version, --file и --url (либо --verify)")

    if not args.no_sign and not args.key:
        parser.error("укажите --key <private.key> для подписи либо --no-sign")

    if args.channel not in ("stable", "beta"):
        print(f"Ошибка: неизвестный канал {args.channel!r}", file=sys.stderr)
        return 1

    try:
        manifest = build_manifest(args.version, args.file, args.url, args.notes, args.channel)
        if args.no_sign:
            print(
                "\nВНИМАНИЕ: манифест НЕ подписан. Такой релиз НЕ будет предложен\n"
                "пользователям канала stable (fail-closed). Только для локальной отладки.\n",
                file=sys.stderr,
            )
        else:
            manifest = sign_manifest(manifest, args.key)
    except (OSError, ValueError, RuntimeError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1

    text = json.dumps(manifest, ensure_ascii=False, indent=2)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
