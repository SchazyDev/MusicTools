"""Regression tests for the converter temp-dir leak on partial failure.

A partially failed batch must not leak the temporary directory and must still
surface the same Russian summary to the user. The failure is now raised as a
:class:`ToolError`, which the base :meth:`ToolWorker.run` maps to
:meth:`~ToolWorker._on_cleanup` + ``error(str(exc))``, instead of emitting
``error`` directly from ``_execute`` and returning normally (which skipped the
cleanup hook entirely).
"""

import os
from pathlib import Path

from core import converter as converter_module
from core.converter import ConvertThread
from core.ffmpeg_utils import ConversionOutcome
from core.tools.base import ToolError
from utils.constants import CancelException

CONVERTER_SOURCE_PATH = Path(converter_module.__file__)
PARTIAL_FAILURE_PREFIX = "Некоторые файлы не были конвертированы"


def _read_converter_source() -> str:
    return CONVERTER_SOURCE_PATH.read_text(encoding="utf-8")


def test_partial_failure_raises_tool_error_in_source():
    """The partial-failure branch must raise ToolError, not emit error."""
    source = _read_converter_source()

    assert "ToolError" in source, "converter.py must import and use ToolError"
    assert PARTIAL_FAILURE_PREFIX in source, (
        "the Russian partial-failure summary must still be present verbatim"
    )

    # No direct emit for the partial-failure message.
    direct_emit = f'self.error.emit("{PARTIAL_FAILURE_PREFIX}'
    assert direct_emit not in source, (
        "partial failure must not emit error directly from _execute"
    )

    raise_call = f"raise ToolError(\"{PARTIAL_FAILURE_PREFIX}"
    assert raise_call in source, (
        "partial failure must raise ToolError with the same message"
    )


def test_tool_error_is_not_cancel_exception():
    """ToolError must not be a CancelException, or the wrong signal fires."""
    assert issubclass(ToolError, Exception)
    assert not issubclass(ToolError, CancelException)


def test_partial_failure_removes_temp_dir(tmp_path, monkeypatch):
    """A failed batch emits the summary, skips finished_ok, and cleans up."""
    input_file = tmp_path / "input.mp3"
    input_file.write_bytes(b"not really audio")

    def fake_convert_audio_file(file_path, temp_dir, output_format, quality,
                                cancel_event, on_progress=None):
        return ConversionOutcome(
            path=None,
            canceled=False,
            error="boom",
            duration_seconds=None,
        )

    monkeypatch.setattr(
        converter_module, "convert_audio_file", fake_convert_audio_file
    )

    worker = ConvertThread([str(input_file)], "mp3", "192")

    errors = []
    finished = []
    canceled = []

    worker.error.connect(errors.append)
    worker.finished_ok.connect(finished.append)
    worker.canceled.connect(lambda: canceled.append(True))

    # Direct call: no QThread.start(), no event loop needed for direct
    # connections between plain Python callables.
    worker.run()

    assert len(errors) == 1, "exactly one error must be emitted"
    assert errors[0].startswith(PARTIAL_FAILURE_PREFIX)
    assert "boom" in errors[0]
    assert finished == [], "finished_ok must not fire on partial failure"
    assert canceled == [], "canceled must not fire on partial failure"

    assert worker.temp_dir is not None
    assert not os.path.exists(worker.temp_dir), (
        "the temporary directory must be removed on partial failure"
    )
