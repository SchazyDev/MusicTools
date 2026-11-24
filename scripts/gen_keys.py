"""Генерация ключей Ed25519 для подписи манифеста обновления.

Пример:
    python scripts/gen_keys.py --out-dir keys

Записывает:
    keys/private.key — raw 32-байтовый seed в base64. НИКОГДА не коммить.
    keys/public.key  — 32-байтовый открытый ключ в base64.

Печатает готовую строку PUBLIC_KEY для вставки в core/updater.py.
"""

import argparse
import base64
import os
import sys

try:  # cryptography предпочтителен, если установлен
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    _HAS_CRYPTOGRAPHY = True
except Exception:  # noqa: BLE001
    _HAS_CRYPTOGRAPHY = False


def _generate() -> tuple[bytes, bytes]:
    """Вернуть (seed32, pub32) в raw-байтах."""
    if _HAS_CRYPTOGRAPHY:
        private_key = Ed25519PrivateKey.generate()
        seed = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return seed, public

    # Фолбэк: стандартная библиотека (Python 3.13 не содержит Ed25519,
    # поэтому без cryptography генерация недоступна).
    raise RuntimeError(
        "Для генерации ключей установите пакет cryptography: pip install cryptography"
    )


def _write(path: str, data: bytes, force: bool) -> None:
    if os.path.exists(path) and not force:
        raise FileExistsError(f"Файл уже существует: {path} (используйте --force)")
    with open(path, "wb") as f:
        f.write(data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Сгенерировать ключи Ed25519 для подписи манифеста")
    parser.add_argument("--out-dir", default="keys", help="Каталог для ключей (по умолчанию keys)")
    parser.add_argument("--force", action="store_true", help="Перезаписать существующие файлы")
    args = parser.parse_args(argv)

    try:
        seed, public = _generate()
    except RuntimeError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1

    os.makedirs(args.out_dir, exist_ok=True)
    private_path = os.path.join(args.out_dir, "private.key")
    public_path = os.path.join(args.out_dir, "public.key")

    try:
        _write(private_path, base64.b64encode(seed), args.force)
        _write(public_path, base64.b64encode(public), args.force)
    except FileExistsError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1

    print()
    print("ВНИМАНИЕ: файл private.key хранит секрет и НЕ ДОЛЖЕН попадать в репозиторий.")
    print("Добавьте keys/private.key в .gitignore и храните реальный ключ только в секретах CI.")
    print()
    print("Вставьте в core/updater.py:")
    print()
    print(f'PUBLIC_KEY = "{base64.b64encode(public).decode("ascii")}"')
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
