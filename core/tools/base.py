"""Shared scaffolding for pluggable tools.

Every long-running job in the app is a :class:`ToolWorker`. New tools are
described by a :class:`ToolDescriptor` and registered in
:mod:`core.tools.registry`; the UI side drills them into tabs (see
:mod:`ui.tool_setup`).
"""

import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QWidget

from utils.constants import CancelException
from utils.logger import setup_logger

logger = setup_logger(__name__)

__all__ = ["ToolError", "ToolWorker", "ToolDescriptor"]

_ID_PATTERN = re.compile(r"^[a-z0-9-]+$")


class ToolError(Exception):
    """Base error raised by tool infrastructure."""


class ToolWorker(QThread):
    """Single lifecycle contract for all long-running jobs.

    Subclasses implement :meth:`_execute`; the template :meth:`run` maps the
    outcome onto the signals below:

    * :class:`utils.constants.CancelException` -> ``canceled``
    * any other :class:`Exception` -> logged and emitted as ``error(str(exc))``
    * normal return -> ``_execute`` is responsible for emitting ``finished_ok``
    """

    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished_ok = pyqtSignal(object)
    error = pyqtSignal(str)
    canceled = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cancel_requested: bool = False
        self._cancel_event: threading.Event = threading.Event()

    @property
    def cancel_event(self) -> threading.Event:
        """Event that is set as soon as cancellation is requested."""
        return self._cancel_event

    def is_cancel_requested(self) -> bool:
        """Return True once :meth:`cancel` has been called."""
        return self._cancel_requested

    def cancel(self) -> None:
        """Request cancellation. Idempotent and safe to call from any thread."""
        self._cancel_requested = True
        self._cancel_event.set()

    def run(self) -> None:
        """Template method. Do not override; override :meth:`_execute` instead."""
        try:
            self._execute()

        except CancelException:
            self._on_cleanup()
            self.canceled.emit()

        except Exception as exc:
            logger.error(f"Tool worker {type(self).__name__} failed: {exc}")
            self._on_cleanup()
            self.error.emit(str(exc))

    def _execute(self) -> None:
        """Override point for the actual work.

        Must emit ``finished_ok`` when done and may raise
        :class:`CancelException` to signal cancellation.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement _execute()"
        )

    def _on_cleanup(self) -> None:
        """Hook invoked on the cancel and failure paths before the terminal signal.

        Subclasses use it to remove temporary artifacts so that an aborted or
        failed job never leaves files behind.
        """
        return


@dataclass(frozen=True)
class ToolDescriptor:
    """Immutable description of a tool that can be added to the UI."""

    id: str
    title: str
    factory: Callable[[], QWidget]
    order: int = 100
    enabled: bool = True
    icon_name: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ID_PATTERN.match(self.id):
            raise ValueError(
                f"Tool id must be a lowercase slug matching ^[a-z0-9-]+$, got {self.id!r}"
            )

        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError(f"Tool title must be a non-empty string, got {self.title!r}")

        if not callable(self.factory):
            raise ValueError(f"Tool factory must be callable for id {self.id!r}")
