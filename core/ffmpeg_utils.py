"""ffmpeg invocation helpers.

Converts a single media file to an audio format through the bundled
``ffmpeg.exe`` and reports progress/errors in a structured way.  The module is
Qt-free so it can be unit-tested without an event loop.
"""

import os
import subprocess
import threading
import time

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, List, Optional

from utils.constants import FFMPEG_PATH
from utils.logger import setup_logger

logger = setup_logger(__name__)

#: Number of trailing stderr lines kept for diagnostics.
STDERR_TAIL_LINES = 50

#: Maximum length of the error message surfaced to the user.
MAX_ERROR_LENGTH = 400

_WAV_CODECS = {
    '16': 'pcm_s16le',
    '24': 'pcm_s24le',
    '32': 'pcm_s32le',
}


@dataclass(frozen=True)
class ConversionOutcome:
    """Result of a single conversion attempt."""

    path: Optional[str]
    canceled: bool
    error: Optional[str]
    duration_seconds: Optional[float]

    @property
    def ok(self) -> bool:
        return self.path is not None and not self.canceled and self.error is None


def build_conversion_args(
    input_path: str | os.PathLike,
    output_path: str | os.PathLike,
    output_format: str,
    quality: str,
) -> List[str]:
    """Build the ffmpeg argv for one conversion.

    ``quality`` is a bitrate for ``mp3``/``ogg`` (e.g. ``"320"``), a bit depth
    for ``wav`` (``"16"``, ``"24"``, ``"32"``) and ``"<depth>:<level>"`` for
    ``flac`` (e.g. ``"24:8"``).
    """
    cmd = [FFMPEG_PATH, '-i', str(input_path), '-vn']

    if output_format == 'mp3':
        cmd += [
            '-acodec',
            'libmp3lame',
            '-b:a',
            f'{quality}k'
        ]

    elif output_format == 'wav':
        cmd += [
            '-acodec',
            _WAV_CODECS[quality]
        ]

    elif output_format == 'flac':
        depth, comp = quality.split(':')
        comp = int(comp)

        if depth == '16':
            cmd += [
                '-acodec',
                'flac',
                '-sample_fmt',
                's16',
                '-compression_level',
                str(comp)
            ]

        elif depth == '24':
            cmd += [
                '-acodec',
                'flac',
                '-sample_fmt',
                's32',
                '-bits_per_raw_sample',
                '24',
                '-compression_level',
                str(comp)
            ]

    elif output_format == 'ogg':
        cmd += [
            '-acodec',
            'libvorbis',
            '-b:a',
            f'{quality}k'
        ]

    cmd += ['-y', str(output_path)]

    return cmd


def parse_progress_line(line: str, duration_seconds: Optional[float]) -> Optional[int]:
    """Translate one ``-progress`` output line into a percentage.

    Returns ``None`` when the line carries no usable position (or the
    duration is still unknown).  Values are clamped to ``0..99``: 100 is only
    reported by the caller on a successful exit.
    """
    key, _, value = line.strip().partition('=')
    if not _:
        return None

    if key not in ('out_time_us', 'out_time_ms'):
        return None

    try:
        position = int(value.strip()) / 1_000_000
    except ValueError:
        return None

    if duration_seconds is None or duration_seconds <= 0:
        return None

    percent = int(position / duration_seconds * 100)
    return max(0, min(99, percent))


def parse_duration(stderr_lines: List[str] | Deque[str]) -> Optional[float]:
    """Parse the first ``Duration: HH:MM:SS.ss`` found in stderr lines."""
    for line in stderr_lines:
        marker = line.find('Duration: ')

        if marker < 0:
            continue

        raw = line[marker + len('Duration: '):].split(',')[0].strip()

        if raw in ('N/A', ''):
            return None

        try:
            hours, minutes, seconds = raw.split(':')
            return (
                int(hours) * 3600
                + int(minutes) * 60
                + float(seconds)
            )

        except ValueError:
            return None

    return None


def format_error(returncode: int, stderr_tail: List[str] | Deque[str]) -> str:
    """Build a single-line, user-facing error message from the stderr tail."""
    meaningful = [
        line.strip()
        for line in stderr_tail
        if line.strip()
    ]

    if not meaningful:
        return f"ffmpeg завершился с кодом {returncode}"

    # Prefer the last lines: ffmpeg prints the actual reason at the end.
    tail = ' | '.join(meaningful[-5:])

    if len(tail) > MAX_ERROR_LENGTH:
        tail = tail[-MAX_ERROR_LENGTH:]

    return f"ffmpeg завершился с кодом {returncode}: {tail}"


def _read_stdout(
    stream,
    stop_event: threading.Event,
    state: dict,
    on_progress: Optional[Callable[[int], None]],
) -> None:
    """Daemon thread body: parse ``-progress`` lines and report percent."""
    try:
        for raw in iter(stream.readline, b''):
            if stop_event.is_set():
                break

            line = raw.decode('utf-8', errors='replace')

            if line.startswith('out_time_'):
                state['last_position'] = line

            percent = parse_progress_line(line, state.get('duration'))
            if percent is not None and percent != state.get('reported'):
                state['reported'] = percent
                _safe_progress(on_progress, percent)

    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"ffmpeg progress reader failed: {exc}")

    finally:
        try:
            stream.close()
        except Exception:
            pass


def _read_stderr(
    stream,
    stop_event: threading.Event,
    tail: Deque[str],
) -> None:
    """Daemon thread body: keep the trailing stderr lines for diagnostics."""
    try:
        for raw in iter(stream.readline, b''):
            if stop_event.is_set():
                break

            line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
            tail.append(line)

    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"ffmpeg stderr reader failed: {exc}")

    finally:
        try:
            stream.close()
        except Exception:
            pass


def _safe_progress(on_progress: Optional[Callable[[int], None]], percent: int) -> None:
    if on_progress is None:
        return

    try:
        on_progress(percent)

    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"ffmpeg progress callback failed: {exc}")


def _terminate(process) -> None:
    """Terminate the process, escalating to kill if it lingers."""
    try:
        process.terminate()

    except Exception as exc:
        logger.error(f"ffmpeg terminate failed: {exc}")

    try:
        process.wait(timeout=2)

    except Exception:
        try:
            process.kill()
            process.wait(timeout=2)

        except Exception as exc:
            logger.error(f"ffmpeg kill failed: {exc}")


def convert_audio_file(
    input_path,
    output_dir,
    output_format,
    quality,
    cancel_event,
    on_progress: Optional[Callable[[int], None]] = None,
) -> ConversionOutcome:
    """Convert ``input_path`` into ``output_dir`` as ``output_format``.

    ``on_progress`` receives an integer percentage in ``0..100``; 100 is only
    emitted once ffmpeg exits successfully.
    """
    input_path = Path(input_path)

    if not os.path.exists(input_path):
        logger.error(f"Input file not found: {input_path}")
        return ConversionOutcome(
            path=None,
            canceled=False,
            error=f"Файл не найден: {input_path}",
            duration_seconds=None,
        )

    output_name = input_path.stem + f'.{output_format}'
    output_path = Path(output_dir) / output_name

    cmd = build_conversion_args(input_path, output_path, output_format, quality)
    # Progress and diagnostics go through pipes; keep the argv tail stable.
    cmd = [cmd[0], '-nostdin', '-hide_banner', '-progress', 'pipe:1', '-nostats'] + cmd[1:]

    state: dict = {'duration': None, 'last_position': None, 'reported': None}
    tail: Deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    stop_event = threading.Event()
    readers: List[threading.Thread] = []

    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    except Exception as exc:
        logger.error(f"Unexpected error: {exc}")
        return ConversionOutcome(
            path=None,
            canceled=False,
            error=f"Не удалось запустить ffmpeg: {exc}",
            duration_seconds=None,
        )

    try:
        try:

            if process.stdout is not None:
                reader = threading.Thread(
                    target=_read_stdout,
                    args=(process.stdout, stop_event, state, on_progress),
                    daemon=True,
                )
                reader.start()
                readers.append(reader)

            if process.stderr is not None:
                reader = threading.Thread(
                    target=_read_stderr,
                    args=(process.stderr, stop_event, tail),
                    daemon=True,
                )
                reader.start()
                readers.append(reader)

            canceled = False

            while True:
                if cancel_event is not None and cancel_event.is_set():
                    canceled = True
                    _terminate(process)
                    break

                state['duration'] = state['duration'] or parse_duration(tail)

                retcode = process.poll()
                if retcode is not None:
                    break

                time.sleep(0.1)

        finally:
            stop_event.set()

            for reader in readers:
                reader.join(timeout=2)

    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    duration_seconds = state['duration'] or parse_duration(tail)

    if canceled:
        logger.info(f"ffmpeg conversion canceled: {input_path}")
        return ConversionOutcome(
            path=None,
            canceled=True,
            error=None,
            duration_seconds=duration_seconds,
        )

    if process.returncode != 0:
        error = format_error(process.returncode, tail)
        logger.error(f"ffmpeg failed for {input_path}: {error}")
        return ConversionOutcome(
            path=None,
            canceled=False,
            error=error,
            duration_seconds=duration_seconds,
        )

    _safe_progress(on_progress, 100)
    return ConversionOutcome(
        path=str(output_path),
        canceled=False,
        error=None,
        duration_seconds=duration_seconds,
    )
