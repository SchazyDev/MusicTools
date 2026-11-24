import threading

from collections import deque
from pathlib import Path

import pytest

from core import ffmpeg_utils
from core.ffmpeg_utils import (
    ConversionOutcome,
    STDERR_TAIL_LINES,
    build_conversion_args,
    convert_audio_file,
    format_error,
    parse_duration,
    parse_progress_line,
)
from utils.constants import FFMPEG_PATH


def _args(tmp_path, fmt, quality):
    return build_conversion_args(
        tmp_path / 'input.wav',
        tmp_path / f'input.{fmt}',
        fmt,
        quality,
    )


def test_mp3_args(tmp_path):
    args = _args(tmp_path, 'mp3', '320')
    assert args[0] == FFMPEG_PATH
    assert args[1:4] == ['-i', str(tmp_path / 'input.wav'), '-vn']
    assert '-acodec' in args
    assert args[args.index('-acodec') + 1] == 'libmp3lame'
    assert args[args.index('-b:a') + 1] == '320k'
    assert args[-2:] == ['-y', str(tmp_path / 'input.mp3')]


@pytest.mark.parametrize('depth,codec', [('16', 'pcm_s16le'), ('24', 'pcm_s24le'), ('32', 'pcm_s32le')])
def test_wav_args(tmp_path, depth, codec):
    args = _args(tmp_path, 'wav', depth)
    assert args[args.index('-acodec') + 1] == codec
    assert '-vn' in args
    assert args[-2:] == ['-y', str(tmp_path / 'input.wav')]


def test_flac_16_args(tmp_path):
    args = _args(tmp_path, 'flac', '16:5')
    assert args[args.index('-acodec') + 1] == 'flac'
    assert args[args.index('-sample_fmt') + 1] == 's16'
    assert args[args.index('-compression_level') + 1] == '5'
    assert '-bits_per_raw_sample' not in args


def test_flac_24_args(tmp_path):
    args = _args(tmp_path, 'flac', '24:8')
    assert args[args.index('-sample_fmt') + 1] == 's32'
    assert args[args.index('-bits_per_raw_sample') + 1] == '24'
    assert args[args.index('-compression_level') + 1] == '8'


def test_ogg_args(tmp_path):
    args = _args(tmp_path, 'ogg', '192')
    assert args[args.index('-acodec') + 1] == 'libvorbis'
    assert args[args.index('-b:a') + 1] == '192k'
    assert args[-2:] == ['-y', str(tmp_path / 'input.ogg')]


def test_parse_duration():
    lines = [
        'ffmpeg version 6.1',
        '  Duration: 00:01:23.45, start: 0.000000, bitrate: 128 kb/s',
    ]
    assert parse_duration(lines) == pytest.approx(83.45)


def test_parse_duration_missing():
    assert parse_duration(['Stream #0:0: Audio: mp3', 'Stream #0:1: Video']) is None


def test_progress_accumulation_monotonic():
    duration = 100.0
    percents = []
    for second in range(0, 100, 10):
        value = parse_progress_line(f'out_time_us={second * 1_000_000}', duration)
        assert value is not None
        percents.append(value)

    assert percents == sorted(percents)
    assert percents[0] == 0
    assert all(0 <= p <= 99 for p in percents)


def test_progress_clamped_and_unknown_duration():
    assert parse_progress_line('out_time_ms=500000000', 10.0) == 99
    assert parse_progress_line('out_time_us=0', None) is None
    assert parse_progress_line('out_time_us=0', 0) is None
    assert parse_progress_line('progress=continue', 10.0) is None
    assert parse_progress_line('out_time_ms=abc', 10.0) is None


def test_stderr_tail_bounding():
    tail = deque(maxlen=STDERR_TAIL_LINES)
    for i in range(200):
        tail.append(f'line {i}')

    assert len(tail) == STDERR_TAIL_LINES
    assert tail[-1] == 'line 199'
    assert tail[0] == f'line {200 - STDERR_TAIL_LINES}'

    error = format_error(1, tail)
    assert 'line 199' in error
    assert '1' in error
    assert '\n' not in error


def test_format_error_empty():
    assert 'ffmpeg' in format_error(2, [])


class _FakeStream:
    def __init__(self, lines):
        self._lines = [line.encode('utf-8') for line in lines]

    def readline(self):
        if self._lines:
            return self._lines.pop(0)

        return b''

    def close(self):
        return None


class _FakeProcess:
    def __init__(self, returncode=None, stdout=None, stderr=None):
        self.returncode = returncode
        self.stdout = stdout if stdout is not None else _FakeStream([])
        self.stderr = stderr if stderr is not None else _FakeStream([])
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


def _patch_popen(monkeypatch, process):
    monkeypatch.setattr(ffmpeg_utils.subprocess, 'Popen', lambda *a, **k: process)


def test_cancel_path(tmp_path, monkeypatch):
    input_path = tmp_path / 'a.wav'
    input_path.write_bytes(b'x')
    process = _FakeProcess(returncode=None)
    _patch_popen(monkeypatch, process)

    cancel_event = threading.Event()
    cancel_event.set()

    outcome = convert_audio_file(input_path, tmp_path, 'mp3', '320', cancel_event)

    assert isinstance(outcome, ConversionOutcome)
    assert outcome.canceled is True
    assert outcome.path is None
    assert process.terminated is True


def test_failure_captures_stderr(tmp_path, monkeypatch):
    input_path = tmp_path / 'a.wav'
    input_path.write_bytes(b'x')
    process = _FakeProcess(
        returncode=1,
        stderr=_FakeStream([
            'Duration: 00:00:10.00, start: 0.000000',
            'Invalid data found when processing input',
        ]),
    )
    _patch_popen(monkeypatch, process)

    outcome = convert_audio_file(
        input_path, tmp_path, 'mp3', '320', threading.Event()
    )

    assert outcome.canceled is False
    assert outcome.path is None
    assert outcome.error is not None
    assert 'Invalid data found when processing input' in outcome.error
    assert outcome.duration_seconds == pytest.approx(10.0)


def test_happy_path(tmp_path, monkeypatch):
    input_path = tmp_path / 'a.wav'
    input_path.write_bytes(b'x')
    output_path = tmp_path / 'a.mp3'
    output_path.write_bytes(b'y')

    process = _FakeProcess(
        returncode=0,
        stdout=_FakeStream([
            'out_time_us=5000000',
            'progress=end',
        ]),
        stderr=_FakeStream(['Duration: 00:00:10.00, start: 0.000000']),
    )
    _patch_popen(monkeypatch, process)

    percents = []
    outcome = convert_audio_file(
        input_path,
        tmp_path,
        'mp3',
        '320',
        threading.Event(),
        on_progress=percents.append,
    )

    assert outcome.path == str(output_path)
    assert outcome.canceled is False
    assert outcome.error is None
    assert outcome.duration_seconds == pytest.approx(10.0)
    assert percents[-1] == 100
    assert all(0 <= p <= 100 for p in percents)


def test_missing_input(tmp_path):
    outcome = convert_audio_file(
        tmp_path / 'missing.wav', tmp_path, 'mp3', '320', threading.Event()
    )

    assert outcome.canceled is False
    assert outcome.path is None
    assert outcome.error is not None
