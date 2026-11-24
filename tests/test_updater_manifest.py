"""Тесты подписи манифеста и каналов обновления (без сети)."""

import base64
import copy
import hashlib
import json

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.updater as updater


PLATFORM = updater.PLATFORM_KEY


@pytest.fixture
def keypair():
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return key, base64.b64encode(seed).decode(), base64.b64encode(pub).decode()


def _base_manifest(channel="stable", version="9.9.9"):
    return {
        "version": version,
        "channel": channel,
        "notes": "test",
        "platforms": {
            PLATFORM: {
                "url": "https://example.com/setup.exe",
                "sha256": "a" * 64,
                "size": 1234,
            }
        },
    }


def _sign(manifest, private_key):
    payload = updater.canonical_manifest_bytes(manifest)
    sig = private_key.sign(payload)
    signed = copy.deepcopy(manifest)
    signed["signature"] = {
        "algorithm": "ed25519",
        "key_id": "test",
        "value": base64.b64encode(sig).decode(),
    }
    return signed


def _patch_network(monkeypatch, manifest):
    release = {
        "tag_name": "v9.9.9",
        "assets": [
            {
                "name": updater.MANIFEST_ASSET_NAME,
                "browser_download_url": "https://example.com/latest.json",
            }
        ],
    }

    def fake_request_json(url, timeout):
        if url.endswith(updater.MANIFEST_ASSET_NAME):
            return copy.deepcopy(manifest)
        return copy.deepcopy(release)

    monkeypatch.setattr(updater, "_request_json", fake_request_json)


def test_valid_signed_manifest_accepted(monkeypatch, keypair):
    private_key, _, pub = keypair
    signed = _sign(_base_manifest(), private_key)
    _patch_network(monkeypatch, signed)
    monkeypatch.setattr(updater, "PUBLIC_KEY", pub)

    info = updater.fetch_latest_manifest("owner/repo", channel="stable")
    assert info is not None
    assert info.version == "9.9.9"
    assert info.sha256 == "a" * 64


def test_allow_unsigned_accepted(monkeypatch):
    _patch_network(monkeypatch, _base_manifest())
    monkeypatch.setattr(updater, "ALLOW_UNSIGNED", True)

    info = updater.fetch_latest_manifest("owner/repo", channel="stable")
    assert info is not None


def test_tampered_manifest_rejected(monkeypatch, keypair):
    private_key, _, pub = keypair
    signed = _sign(_base_manifest(), private_key)
    # Подменяем sha256 после подписи — подпись больше не сходится.
    signed["platforms"][PLATFORM]["sha256"] = "b" * 64
    _patch_network(monkeypatch, signed)
    monkeypatch.setattr(updater, "PUBLIC_KEY", pub)

    assert updater.fetch_latest_manifest("owner/repo", channel="stable") is None


def test_wrong_signature_rejected(monkeypatch, keypair):
    _, _, pub = keypair
    signed = _sign(_base_manifest(), Ed25519PrivateKey.generate())
    signed["signature"]["value"] = base64.b64encode(b"\x00" * 64).decode()
    _patch_network(monkeypatch, signed)
    monkeypatch.setattr(updater, "PUBLIC_KEY", pub)

    assert updater.fetch_latest_manifest("owner/repo", channel="stable") is None


def test_missing_signature_stable_rejected(monkeypatch, keypair):
    _, _, pub = keypair
    _patch_network(monkeypatch, _base_manifest())
    monkeypatch.setattr(updater, "PUBLIC_KEY", pub)
    monkeypatch.setattr(updater, "ALLOW_UNSIGNED", False)

    assert updater.fetch_latest_manifest("owner/repo", channel="stable") is None


def test_placeholder_key_does_not_block_unsigned(monkeypatch):
    # Пока PUBLIC_KEY — плейсхолдер, проверять подпись нечем: неподписанный
    # манифест не блокируется (обратная совместимость с невыпущенными релизами).
    _patch_network(monkeypatch, _base_manifest())
    monkeypatch.setattr(updater, "PUBLIC_KEY", "PLACEHOLDER_REPLACE_ME")
    monkeypatch.setattr(updater, "ALLOW_UNSIGNED", False)

    assert updater.fetch_latest_manifest("owner/repo", channel="stable") is not None


def test_real_key_missing_signature_fails_closed(monkeypatch, keypair):
    # С реальным ключом неподписанный stable-манифест отклоняется.
    _, _, pub = keypair
    _patch_network(monkeypatch, _base_manifest())
    monkeypatch.setattr(updater, "PUBLIC_KEY", pub)
    monkeypatch.setattr(updater, "ALLOW_UNSIGNED", False)

    assert updater.fetch_latest_manifest("owner/repo", channel="stable") is None


def test_channel_mismatch_returns_none(monkeypatch, keypair):
    private_key, _, pub = keypair
    signed = _sign(_base_manifest(channel="beta"), private_key)
    _patch_network(monkeypatch, signed)
    monkeypatch.setattr(updater, "PUBLIC_KEY", pub)

    assert updater.fetch_latest_manifest("owner/repo", channel="stable") is None


def test_beta_channel_accepted(monkeypatch, keypair):
    private_key, _, pub = keypair
    signed = _sign(_base_manifest(channel="beta"), private_key)
    _patch_network(monkeypatch, signed)
    monkeypatch.setattr(updater, "PUBLIC_KEY", pub)

    info = updater.fetch_latest_manifest("owner/repo", channel="beta")
    assert info is not None
    assert info.channel == "beta"


def test_canonical_payload_stable_across_key_order():
    first = _base_manifest()
    # Тот же объект, ключи вставлены в обратном порядке.
    second = {}
    for k in reversed(list(first.keys())):
        second[k] = first[k]

    assert updater.canonical_manifest_bytes(first) == updater.canonical_manifest_bytes(second)
    # И одинаково при наличии puzzle-порядка внутри platforms.
    third = copy.deepcopy(first)
    third["platforms"] = {PLATFORM: dict(reversed(list(first["platforms"][PLATFORM].items())))}
    assert updater.canonical_manifest_bytes(first) == updater.canonical_manifest_bytes(third)


def test_verify_manifest_signature_never_raises():
    assert updater.verify_manifest_signature(b"x", None, "bad") is False
    assert updater.verify_manifest_signature(b"x", {"value": 1}, "bad") is False
