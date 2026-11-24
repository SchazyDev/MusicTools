import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QAbstractItemView, QApplication

from ui.file_drop_list import FileDropList, collect_droppable_files

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ALLOWED = {".mp3", ".wav"}


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


# --- pure logic -----------------------------------------------------------


def test_allowed_file_accepted(tmp_path):
    audio = _touch(tmp_path / "song.mp3")
    accepted, rejected = collect_droppable_files([str(audio)], ALLOWED)
    assert accepted == [os.path.abspath(str(audio))]
    assert rejected == []


def test_unsupported_extension_rejected(tmp_path):
    doc = _touch(tmp_path / "notes.txt")
    accepted, rejected = collect_droppable_files([str(doc)], ALLOWED)
    assert accepted == []
    assert rejected == [os.path.abspath(str(doc))]


def test_missing_path_rejected(tmp_path):
    missing = tmp_path / "ghost.mp3"
    accepted, rejected = collect_droppable_files([str(missing)], ALLOWED)
    assert accepted == []
    assert rejected == [str(missing)]


def test_directory_expanded_recursively(tmp_path):
    root = tmp_path / "album"
    first = _touch(root / "a.mp3")
    second = _touch(root / "nested" / "deeper" / "b.wav")
    _touch(root / "readme.txt")

    accepted, rejected = collect_droppable_files([str(root)], ALLOWED)

    accepted_set = set(accepted)
    assert os.path.abspath(str(first)) in accepted_set
    assert os.path.abspath(str(second)) in accepted_set
    assert len(accepted) == 2
    assert len(rejected) == 1


def test_duplicates_and_case_variants_collapsed(tmp_path):
    audio = _touch(tmp_path / "song.mp3")
    messy = str(tmp_path / "." / "song.mp3")
    accepted, _ = collect_droppable_files(
        [str(audio), str(audio), messy], ALLOWED
    )
    assert len(accepted) == 1


def test_order_preserved(tmp_path):
    first = _touch(tmp_path / "one.mp3")
    second = _touch(tmp_path / "two.wav")
    third = _touch(tmp_path / "three.mp3")

    accepted, _ = collect_droppable_files(
        [str(third), str(first), str(second)], ALLOWED
    )
    assert accepted == [
        os.path.abspath(str(third)),
        os.path.abspath(str(first)),
        os.path.abspath(str(second)),
    ]


# --- widget ---------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_drop_configuration(qapp):
    lst = FileDropList()
    assert lst.acceptDrops() is True
    assert lst.viewport().acceptDrops() is True
    assert lst.dragDropMode() == QAbstractItemView.DropOnly
    assert lst.defaultDropAction() == Qt.CopyAction


def test_fresh_list_has_single_dummy_row(qapp):
    lst = FileDropList()
    assert lst.count() == 1
    assert lst.item(0) is lst.dummy_item


def test_add_file_item_dedupes_and_returns_bool(qapp, tmp_path):
    lst = FileDropList()
    audio = _touch(tmp_path / "song.mp3")

    assert lst.add_file_item(str(audio)) is True
    assert lst.add_file_item(str(audio)) is False
    assert lst.count() == 2
