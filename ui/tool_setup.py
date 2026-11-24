"""UI wiring for the tool registry.

Tab modules are imported lazily inside the factories so that importing the
registry (or this module) never starts a widgets import cycle.
"""

from typing import Any, List, Tuple

from PyQt5.QtWidgets import QWidget

from core.tools.base import ToolDescriptor
from core.tools.registry import ToolRegistry

__all__ = ["register_default_tools", "build_tabs"]


def register_default_tools(
    target_registry: ToolRegistry,
    main_window: Any = None,
) -> ToolRegistry:
    """Register the built-in tools into ``target_registry``.

    ``main_window`` is captured by the factories because the tab widgets need
    access to shared services owned by the window (``network_manager``).
    Registration is idempotent: descriptors whose id is already present are
    left untouched.
    """

    def _create_download_tab() -> QWidget:
        from ui.download_tab import DownloadTab

        return DownloadTab(main_window)

    def _create_convert_tab() -> QWidget:
        from ui.convert_tab import ConvertTab

        return ConvertTab(main_window)

    def _create_about_tab() -> QWidget:
        from ui.about_tab import AboutTab

        return AboutTab()

    descriptors = [
        ToolDescriptor(id="download", title="Download", factory=_create_download_tab, order=10),
        ToolDescriptor(id="convert", title="Convert", factory=_create_convert_tab, order=20),
        ToolDescriptor(id="about", title="About", factory=_create_about_tab, order=30),
    ]

    for descriptor in descriptors:
        if target_registry.get(descriptor.id) is None:
            target_registry.register(descriptor)

    return target_registry


def build_tabs(
    target_registry: ToolRegistry,
) -> List[Tuple[ToolDescriptor, QWidget]]:
    """Instantiate one widget per enabled descriptor, in registry order."""
    return [(descriptor, descriptor.factory()) for descriptor in target_registry.enabled()]
