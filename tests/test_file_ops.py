import os

import pytest

from core.file_ops import CopyWorker, plan_copy, unique_destination


def _write(path, data=b"payload"):
    path.write_bytes(data)
    return str(path)


def test_unique_destination_free_name(tmp_path):
    assert unique_destination(str(tmp_path), "song.mp3") == str(tmp_path / "song.mp3")


def test_unique_destination_existing_name(tmp_path):
    (tmp_path / "song.mp3").write_bytes(b"x")

    assert unique_destination(str(tmp_path), "song.mp3") == str(
        tmp_path / "song (1).mp3"
    )


def test_unique_destination_progression(tmp_path):
    (tmp_path / "song.mp3").write_bytes(b"x")
    (tmp_path / "song (1).mp3").write_bytes(b"x")
    (tmp_path / "song (2).mp3").write_bytes(b"x")

    assert unique_destination(str(tmp_path), "song.mp3") == str(
        tmp_path / "song (3).mp3"
    )


def test_unique_destination_case_insensitive(tmp_path):
    (tmp_path / "Song.MP3").write_bytes(b"x")

    result = unique_destination(str(tmp_path), "song.mp3")

    assert os.path.normcase(result) != os.path.normcase(str(tmp_path / "Song.MP3"))
    assert result.endswith("song (1).mp3")


def test_unique_destination_extensionless(tmp_path):
    (tmp_path / "LICENSE").write_bytes(b"x")

    assert unique_destination(str(tmp_path), "LICENSE") == str(
        tmp_path / "LICENSE (1)"
    )


def test_plan_copy_skips_missing_sources(tmp_path):
    src = _write(tmp_path / "a.mp3")
    dest = tmp_path / "out"
    dest.mkdir()

    pairs = plan_copy([src, str(tmp_path / "missing.mp3")], str(dest))

    assert pairs == [(src, str(dest / "a.mp3"))]


def test_plan_copy_rejects_non_directory(tmp_path):
    src = _write(tmp_path / "a.mp3")
    not_dir = _write(tmp_path / "file.txt")

    with pytest.raises(ValueError):
        plan_copy([src], not_dir)


def test_plan_copy_resolves_same_named_sources(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    src_a = _write(first / "track.mp3", b"a")
    src_b = _write(second / "track.mp3", b"b")
    dest = tmp_path / "out"
    dest.mkdir()

    pairs = plan_copy([src_a, src_b], str(dest))
    destinations = [dst for _, dst in pairs]

    assert destinations == [
        str(dest / "track.mp3"),
        str(dest / "track (1).mp3"),
    ]
    assert len({os.path.normcase(d) for d in destinations}) == 2


def test_copy_worker_copies_content(tmp_path):
    src = _write(tmp_path / "a.bin", b"hello world")
    dest = tmp_path / "out"
    dest.mkdir()

    worker = CopyWorker([src], str(dest))
    plan = worker.build_plan()
    worker._copy_one(plan[0][0], plan[0][1], 0, 100)

    assert open(plan[0][1], "rb").read() == b"hello world"


def test_copy_worker_resolves_collision(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.bin").write_bytes(b"original")
    src = _write(tmp_path / "a.bin", b"new")

    worker = CopyWorker([src], str(dest))
    plan = worker.build_plan()
    worker._copy_one(plan[0][0], plan[0][1], 0, 100)

    assert (dest / "a.bin").read_bytes() == b"original"
    assert (dest / "a (1).bin").read_bytes() == b"new"


def test_copy_worker_cancel_before_start(tmp_path):
    src = _write(tmp_path / "a.bin", b"data")
    dest = tmp_path / "out"
    dest.mkdir()

    worker = CopyWorker([src], str(dest))
    plan = worker.build_plan()
    worker.cancel()

    assert worker.is_cancel_requested()
    assert plan == [(src, str(dest / "a.bin"))]
    assert not (dest / "a.bin").exists()
    assert os.listdir(str(dest)) == []


def test_copy_worker_cancel_removes_partial(tmp_path):
    src = _write(tmp_path / "big.bin", b"x" * (3 * 1024 * 1024))
    dest = tmp_path / "out"
    dest.mkdir()
    dst = str(dest / "big.bin")

    worker = CopyWorker([src], str(dest))
    worker.cancel()

    from utils.constants import CancelException

    with pytest.raises(CancelException):
        worker._copy_one(src, dst, 0, 100)

    worker._on_cleanup()
    assert os.listdir(str(dest)) == []


def test_copy_worker_destination_file_override(tmp_path):
    src = _write(tmp_path / "a.bin", b"data")
    dest = tmp_path / "out"
    dest.mkdir()
    target = str(dest / "custom_name.bin")

    worker = CopyWorker([src], str(dest))
    worker.set_destination_file(target)
    plan = worker.build_plan()
    worker._copy_one(plan[0][0], plan[0][1], 0, 100)

    assert plan == [(src, target)]
    assert os.path.exists(target)
