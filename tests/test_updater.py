import json
import sys

import pytest

from core import updater
from core.updater import (
    UpdateInfo,
    UpdateIntegrityError,
    download_update,
    fetch_latest_manifest,
    is_newer,
    parse_version,
    perform_update,
)

VERSION_110 = "1.1.0"


def _info(sha256: str, size: int = 4) -> UpdateInfo:
    return UpdateInfo(
        version=VERSION_110,
        url="https://example.com/file.exe",
        sha256=sha256,
        size=size,
        notes="",
        channel="stable",
    )


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._offset = 0
        self.headers = {}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = len(self._payload) - self._offset
        chunk = self._payload[self._offset:self._offset + n]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, mapping):
        self.mapping = mapping
        self.requested = []

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else request
        self.requested.append(url)
        return FakeResponse(self.mapping[url])


# --- version comparison ---------------------------------------------------

@pytest.mark.parametrize(
    "remote, local",
    [
        ("1.0.10", "1.0.9"),
        ("1.1.0", "1.0.9"),
        ("v1.1.0", "1.0.9"),
        ("1.2", "v1.1.9"),
    ],
)
def test_is_newer_true(remote, local):
    assert is_newer(remote, local) is True


@pytest.mark.parametrize(
    "remote, local",
    [
        ("1.0.9", "1.0.9"),
        ("1.0.8", "1.0.9"),
        ("garbage", "1.0.9"),
        ("1.0.9", "garbage"),
        ("", "1.0.9"),
    ],
)
def test_is_newer_false(remote, local):
    assert is_newer(remote, local) is False


def test_parse_version_strips_v_and_handles_invalid():
    assert parse_version("v1.2.3") is not None
    assert parse_version("1.2.3") == parse_version("v1.2.3")
    assert parse_version("not-a-version") is None
    assert parse_version("") is None


# --- manifest validation --------------------------------------------------

def _release_payload(*asset_names):
    return {
        "tag_name": "v1.1.0",
        "assets": [
            {"name": name, "browser_download_url": f"https://example.com/{name}"}
            for name in asset_names
        ],
    }


GOOD_SHA = "a" * 64

# Ключ для тестов подписи: детерминированный seed, чтобы тесты не зависели от
# содержимого keys/ (закрытый ключ в репозиторий не попадает).
_TEST_SEED = bytes(range(32))


def _test_public_key() -> str:
    """Открытая половина тестового ключа в base64."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(_TEST_SEED)
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(public).decode()


def _sign_manifest(manifest: dict) -> dict:
    """Возвращает манифест с корректной Ed25519-подписью тестового ключа."""
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    payload = updater.canonical_manifest_bytes(manifest)
    signature = Ed25519PrivateKey.from_private_bytes(_TEST_SEED).sign(payload)
    signed = dict(manifest)
    signed["signature"] = {
        "algorithm": "ed25519",
        "key_id": "test",
        "value": base64.b64encode(signature).decode(),
    }
    return signed


@pytest.fixture(autouse=True)
def _signed_manifests(monkeypatch):
    """В приложении закреплён настоящий ключ, поэтому тестовые манифесты
    подписываются тестовым ключом, а проверка берёт его открытую половину."""
    try:
        public = _test_public_key()
    except Exception:  # noqa: BLE001 - без cryptography тесты подписи не работают
        yield
        return
    monkeypatch.setattr(updater, "PUBLIC_KEY", public)
    yield


def _manifest_payload(**overrides):
    manifest = {
        "version": "1.1.0",
        "channel": "stable",
        "notes": "notes",
        "platforms": {
            "windows-x86_64": {
                "url": "https://example.com/file.exe",
                "sha256": GOOD_SHA,
                "size": 100,
            }
        },
    }
    manifest.update(overrides)
    return manifest


def _patch_opener(monkeypatch, release, manifest):
    mapping = {
        "https://api.github.com/repos/SchazyDev/MusicTools/releases/latest":
            json.dumps(release).encode(),
        "https://example.com/latest.json": json.dumps(manifest).encode(),
    }
    opener = FakeOpener(mapping)
    monkeypatch.setattr(updater.urllib.request, "urlopen", opener)
    return opener


def test_fetch_manifest_valid(monkeypatch):
    _patch_opener(
        monkeypatch,
        _release_payload("latest.json", "MusicTools_setup.exe"),
        _sign_manifest(_manifest_payload()),
    )
    info = fetch_latest_manifest()
    assert info is not None
    assert info.version == "1.1.0"
    assert info.url == "https://example.com/file.exe"
    assert info.sha256 == GOOD_SHA
    assert info.size == 100
    assert info.channel == "stable"


def test_fetch_manifest_unsigned_stable_is_rejected(monkeypatch):
    """Реальный ключ в приложении: манифест без подписи не принимается."""
    _patch_opener(
        monkeypatch,
        _release_payload("latest.json"),
        _manifest_payload(),
    )
    assert fetch_latest_manifest() is None


def test_fetch_manifest_tampered_signature_is_rejected(monkeypatch):
    signed = _sign_manifest(_manifest_payload())
    signed["version"] = "9.9.9"
    _patch_opener(monkeypatch, _release_payload("latest.json"), signed)
    assert fetch_latest_manifest() is None


def test_python_and_rust_share_the_same_public_key():
    """Ключ в core/updater.py и в Rust-ядре обязан совпадать: иначе
    подписанный манифест принимается только одной из версий."""
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "src-tauri" / "src" / "update" / "mod.rs"
    text = source.read_text(encoding="utf-8")
    match = re.search(r'pub const PUBLIC_KEY: &str = "([^"]+)"', text)
    assert match, "PUBLIC_KEY не найден в src-tauri/src/update/mod.rs"

    # Читаем ключ из самого файла: фикстура подменяет updater.PUBLIC_KEY
    # тестовым ключом.
    core = Path(__file__).resolve().parent.parent / "core" / "updater.py"
    python_text = core.read_text(encoding="utf-8")
    python_match = re.search(r'^PUBLIC_KEY = "([^"]+)"', python_text, re.MULTILINE)
    assert python_match, "PUBLIC_KEY не найден в core/updater.py"
    assert match.group(1) == python_match.group(1)


def test_fetch_manifest_missing_asset_returns_none(monkeypatch):
    _patch_opener(
        monkeypatch,
        _release_payload("MusicTools_setup.exe"),
        _manifest_payload(),
    )
    assert fetch_latest_manifest() is None


def test_fetch_manifest_missing_platform_returns_none(monkeypatch):
    _patch_opener(
        monkeypatch,
        _release_payload("latest.json"),
        _manifest_payload(platforms={}),
    )
    assert fetch_latest_manifest() is None


def test_fetch_manifest_bad_sha_returns_none(monkeypatch):
    _patch_opener(
        monkeypatch,
        _release_payload("latest.json"),
        _manifest_payload(platforms={
            "windows-x86_64": {"url": "https://example.com/file.exe", "sha256": "abc", "size": 10}
        }),
    )
    assert fetch_latest_manifest() is None


def test_fetch_manifest_negative_size_returns_none(monkeypatch):
    _patch_opener(
        monkeypatch,
        _release_payload("latest.json"),
        _manifest_payload(platforms={
            "windows-x86_64": {"url": "https://example.com/file.exe", "sha256": GOOD_SHA, "size": -1}
        }),
    )
    assert fetch_latest_manifest() is None


# --- download_update ------------------------------------------------------

import hashlib


def _patch_download(monkeypatch, payload: bytes):
    opener = FakeOpener({"https://example.com/file.exe": payload})
    monkeypatch.setattr(updater.urllib.request, "urlopen", opener)
    return opener


def test_download_update_keeps_file_on_match(tmp_path, monkeypatch):
    payload = b"hello updater"
    _patch_download(monkeypatch, payload)
    dest = tmp_path / "new.exe"
    progress = []

    download_update(
        _info(hashlib.sha256(payload).hexdigest(), len(payload)),
        str(dest),
        on_progress=progress.append,
    )

    assert dest.read_bytes() == payload
    assert progress and progress[-1] == 100


def test_download_update_raises_and_removes_on_mismatch(tmp_path, monkeypatch):
    payload = b"tampered payload"
    _patch_download(monkeypatch, payload)
    dest = tmp_path / "new.exe"

    with pytest.raises(UpdateIntegrityError):
        download_update(_info("b" * 64, len(payload)), str(dest))

    assert not dest.exists()


# --- perform_update guard -------------------------------------------------

def test_perform_update_refuses_when_not_frozen(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    called = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: called.append(a))

    ok, message = perform_update(_info(GOOD_SHA), str(tmp_path / "app.exe"))

    assert ok is False
    assert message
    assert called == []


# --- regression -----------------------------------------------------------

def test_no_html_url_usage():
    import inspect
    source = inspect.getsource(updater)
    assert "html_url" not in source
