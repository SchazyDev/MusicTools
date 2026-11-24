"""Off-thread file copying with collision-safe destinations and progress.

:func:`unique_destination` and :func:`plan_copy` are pure helpers;
:class:`CopyWorker` performs the actual copy on a background thread so the UI
never blocks on large media files.
"""

import os
import shutil
from typing import Iterable, List, Optional, Tuple

from core.tools.base import ToolWorker
from utils.constants import CancelException

__all__ = ["unique_destination", "plan_copy", "CopyWorker"]

CHUNK_SIZE = 1024 * 1024  # 1 MiB - copy granularity for responsive cancellation


def _exists(path: str) -> bool:
    """Case-insensitive existence check (Windows/``normcase`` semantics)."""
    return os.path.exists(path) or os.path.exists(os.path.normcase(path))


def unique_destination(dest_dir: str, file_name: str) -> str:
    """Return a path inside ``dest_dir`` that does not collide with anything.

    ``name.ext`` when free, otherwise ``name (1).ext``, ``name (2).ext``, ...
    """
    stem, ext = os.path.splitext(file_name)
    candidate = os.path.join(dest_dir, file_name)
    index = 1

    while _exists(candidate):
        candidate = os.path.join(dest_dir, f"{stem} ({index}){ext}")
        index += 1

    return candidate


def plan_copy(
    src_paths: Iterable[str],
    dest_dir: str,
    resolve_collisions: bool = True,
) -> List[Tuple[str, str]]:
    """Build ``(src, dst)`` pairs for copying ``src_paths`` into ``dest_dir``.

    Missing sources are skipped. Raises :class:`ValueError` when ``dest_dir`` is
    not an existing directory.
    """
    if not os.path.isdir(dest_dir):
        raise ValueError(f"Destination is not a directory: {dest_dir}")

    pairs: List[Tuple[str, str]] = []
    reserved = set()

    for src in src_paths:
        if not os.path.isfile(src):
            continue

        name = os.path.basename(src)

        if resolve_collisions:
            dst = unique_destination(dest_dir, name)
            index = 1
            stem, ext = os.path.splitext(name)

            while os.path.normcase(dst) in reserved:
                dst = os.path.join(dest_dir, f"{stem} ({index}){ext}")
                index += 1

        else:
            dst = os.path.join(dest_dir, name)

        reserved.add(os.path.normcase(dst))
        pairs.append((src, dst))

    return pairs


class CopyWorker(ToolWorker):
    """Copy a list of files into a directory on a background thread.

    Emits ``status("Копирование {i}/{n}: {name}")`` before each file and
    ``progress`` as an overall 0..100 percentage. ``finished_ok`` carries the
    list of written destination paths.
    """

    def __init__(
        self,
        src_paths: Iterable[str],
        dest_dir: str,
        resolve_collisions: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._src_paths: List[str] = list(src_paths)
        self._dest_dir: str = dest_dir
        self._dst_path: Optional[str] = None
        self._resolve_collisions: bool = resolve_collisions
        self._plan: List[Tuple[str, str]] = []
        self._partial_dst: Optional[str] = None

    # ------------------------------------------------------------------ plan

    def set_destination_file(self, dst_path: str) -> None:
        """Copy a single source to exactly ``dst_path`` (used by "Save as...")."""
        if len(self._src_paths) != 1:
            raise ValueError(
                "set_destination_file() requires exactly one source file"
            )

        self._dst_path = dst_path
        self._dest_dir = os.path.dirname(os.path.abspath(dst_path))

    def build_plan(self) -> List[Tuple[str, str]]:
        """Return the ``(src, dst)`` pairs this worker will copy."""
        if self._dst_path is not None:
            self._plan = [(self._src_paths[0], self._dst_path)]
            return list(self._plan)

        self._plan = plan_copy(
            self._src_paths, self._dest_dir, self._resolve_collisions
        )
        return list(self._plan)

    # --------------------------------------------------------------- copying

    def _copy_one(self, src: str, dst: str, base_percent: int, span: int) -> int:
        """Copy ``src`` to ``dst`` in 1 MiB chunks, reporting progress.

        Returns the overall percentage reached at the end of this file.
        """
        total = os.path.getsize(src) or 1
        copied = 0
        self._partial_dst = dst

        try:
            with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
                while True:
                    if self.is_cancel_requested():
                        raise CancelException()

                    chunk = fsrc.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    fdst.write(chunk)
                    copied += len(chunk)
                    self.progress.emit(
                        min(100, base_percent + int(span * copied / total))
                    )

        except Exception:
            self._remove_partial()
            raise

        shutil.copystat(src, dst)
        self._partial_dst = None
        return min(100, base_percent + span)

    def _remove_partial(self) -> None:
        dst = self._partial_dst
        self._partial_dst = None

        if dst and os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass

    def _execute(self) -> None:
        plan = self.build_plan()
        total = len(plan)
        written: List[str] = []

        if not total:
            self.progress.emit(100)
            self.finished_ok.emit(written)
            return

        for index, (src, dst) in enumerate(plan, start=1):
            if self.is_cancel_requested():
                raise CancelException()

            self.status.emit(
                f"Копирование {index}/{total}: {os.path.basename(src)}"
            )

            span = 100 // total
            base = span * (index - 1)
            self._copy_one(src, dst, base, span)
            written.append(dst)

        self.progress.emit(100)
        self.finished_ok.emit(written)

    def _on_cleanup(self) -> None:
        self._remove_partial()
