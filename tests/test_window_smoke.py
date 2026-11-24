"""Smoke tests: the main window must build every registered tool tab.

These tests instantiate real widgets, which the registry unit tests
deliberately avoid. They exist because a factory that cannot construct its
widget (for example a tab requiring a constructor argument that the factory
never passes) still passes every non-instantiating registry test.
"""

import os

# Must be set before QApplication is created; harmless if already set.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication

from core.tools.registry import ToolRegistry
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qt_app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def window(qt_app):
    main_window = MainWindow()
    yield main_window
    main_window.close()
    main_window.deleteLater()


def test_all_tabs_are_built_from_registry(window):
    titles = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert titles == ["Download", "Convert", "About"]


def test_tab_widgets_are_reachable_by_tool_id(window):
    assert window.download_tab is window.tabs.widget(0)
    assert window.convert_tab is window.tabs.widget(1)
    assert window.about_tab is window.tabs.widget(2)


def test_tabs_receive_the_main_window(window):
    assert window.download_tab.main_window is window
    assert window.convert_tab.main_window is window


def test_network_manager_exists_before_tabs_use_it(window):
    assert window.network_manager is not None
    assert window.download_tab.main_window.network_manager is window.network_manager


def test_explicit_registry_instance_is_used_and_populated(qt_app):
    registry = ToolRegistry()
    main_window = MainWindow(tool_registry=registry)
    try:
        assert main_window.tool_registry is registry
        assert sorted(tool.id for tool in registry.all()) == ["about", "convert", "download"]
        assert main_window.tabs.count() == 3
    finally:
        main_window.close()
        main_window.deleteLater()
