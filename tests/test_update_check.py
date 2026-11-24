"""No-network tests for the update workers and the non-blocking startup wiring."""

from pathlib import Path

import core.updater as updater
from core.updater import (
    UpdateCheckWorker,
    UpdateDownloadWorker,
    UpdateInfo,
)


ROOT = Path(__file__).resolve().parent.parent


def _fake_info() -> UpdateInfo:
    return UpdateInfo(
        version="9.9.9",
        url="https://example.invalid/app.exe",
        sha256="0" * 64,
        size=123,
        notes="",
        channel="stable",
    )


def _collect(signal) -> list:
    received = []
    signal.connect(received.append)
    return received


def test_update_check_worker_emits_info(monkeypatch):
    info = _fake_info()
    monkeypatch.setattr(updater, "check_for_updates", lambda *a, **kw: info)

    worker = UpdateCheckWorker()
    finished = _collect(worker.finished_ok)
    errors = _collect(worker.error)

    worker.run()

    assert finished == [info]
    assert errors == []


def test_update_check_worker_emits_none(monkeypatch):
    monkeypatch.setattr(updater, "check_for_updates", lambda *a, **kw: None)

    worker = UpdateCheckWorker()
    finished = _collect(worker.finished_ok)

    worker.run()

    assert finished == [None]


def test_update_download_worker_forwards_progress(monkeypatch):
    def fake_perform_update(info, current_exe_path, on_progress=None):
        on_progress(42)
        return True, "ok"

    monkeypatch.setattr(updater, "perform_update", fake_perform_update)

    worker = UpdateDownloadWorker(_fake_info(), "C:/app/MusicTools.exe")
    progress = _collect(worker.progress)
    finished = _collect(worker.finished_ok)
    errors = _collect(worker.error)

    worker.run()

    assert progress == [42]
    assert finished == [(True, "ok")]
    assert errors == []


def test_update_download_worker_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        updater,
        "perform_update",
        lambda info, current_exe_path, on_progress=None: (False, "boom"),
    )

    worker = UpdateDownloadWorker(_fake_info(), "C:/app/MusicTools.exe")
    finished = _collect(worker.finished_ok)
    errors = _collect(worker.error)

    worker.run()

    assert finished == [(False, "boom")]
    assert errors == []


def test_tray_application_has_no_terminate_call():
    source = (ROOT / "ui" / "tray_application.py").read_text(encoding="utf-8")
    assert ".terminate()" not in source


def test_main_starts_update_worker_after_tray_application():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tray_index = source.index("TrayApplication(")
    worker_index = source.index("UpdateCheckWorker(")
    exec_index = source.index("app.exec_()")

    assert tray_index < worker_index < exec_index
