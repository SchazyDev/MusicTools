"""Тесты core.settings."""

import json

import pytest

from core import settings as settings_mod
from core.settings import Settings


@pytest.fixture(autouse=True)
def _isolated_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    yield tmp_path


def test_default_load_creates_file(_isolated_appdata):
    s = Settings.load()
    assert s.channel == "stable"
    assert s.check_on_startup is True

    path = _isolated_appdata / "MusicTools" / "settings.json"
    assert path.is_file()


def test_corrupt_json_returns_defaults(_isolated_appdata):
    path = _isolated_appdata / "MusicTools" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not valid json", encoding="utf-8")

    s = Settings.load()
    assert s.channel == "stable"
    assert s.check_on_startup is True


def test_invalid_channel_falls_back(_isolated_appdata):
    path = _isolated_appdata / "MusicTools" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"channel": "nightly"}), encoding="utf-8")

    assert Settings.load().channel == "stable"


def test_save_load_roundtrip(_isolated_appdata):
    Settings(channel="beta", check_on_startup=False).save()
    loaded = Settings.load()
    assert loaded.channel == "beta"
    assert loaded.check_on_startup is False


def test_allowed_channels():
    assert settings_mod.allowed_channels() == ("stable", "beta")


def test_atomic_write_leaves_no_temp_files(_isolated_appdata):
    Settings(channel="beta").save()
    directory = _isolated_appdata / "MusicTools"
    leftovers = [p.name for p in directory.iterdir() if p.name != "settings.json"]
    assert leftovers == []
