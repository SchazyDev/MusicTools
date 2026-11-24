"""Tests for the tool registry scaffold. No threads are started, no widgets shown."""

import pytest

from core.converter import ConvertThread
from core.downloader import DownloadThread
from core.preview import PreviewThread
from core.tools.base import ToolDescriptor, ToolError, ToolWorker
from core.tools.registry import ToolRegistry, registry as global_registry
from ui.tool_setup import register_default_tools


def _make_descriptor(tool_id="alpha", title="Alpha", order=100, enabled=True):
    return ToolDescriptor(
        id=tool_id,
        title=title,
        factory=lambda: None,
        order=order,
        enabled=enabled,
    )


def test_registry_register_and_get():
    reg = ToolRegistry()
    descriptor = _make_descriptor()

    assert reg.get("alpha") is None
    reg.register(descriptor)

    assert reg.get("alpha") is descriptor
    assert reg.all() == [descriptor]
    assert len(reg) == 1
    assert "alpha" in reg


def test_registry_all_sorted_by_order_then_title():
    reg = ToolRegistry()
    reg.register(_make_descriptor("c", "Zulu", order=30))
    reg.register(_make_descriptor("a", "Alpha", order=30))
    reg.register(_make_descriptor("b", "Bravo", order=10))

    assert [d.id for d in reg.all()] == ["b", "a", "c"]


def test_registry_duplicate_id_rejected():
    reg = ToolRegistry()
    reg.register(_make_descriptor("alpha"))

    with pytest.raises(ValueError, match="alpha"):
        reg.register(_make_descriptor("alpha", "Other"))


def test_registry_unregister():
    reg = ToolRegistry()
    reg.register(_make_descriptor("alpha"))

    reg.unregister("alpha")
    assert reg.get("alpha") is None
    assert reg.all() == []

    reg.unregister("missing")


def test_registry_enabled_filtering():
    reg = ToolRegistry()
    reg.register(_make_descriptor("on", "On", order=10, enabled=True))
    reg.register(_make_descriptor("off", "Off", order=5, enabled=False))

    assert [d.id for d in reg.enabled()] == ["on"]
    assert [d.id for d in reg.all()] == ["off", "on"]


def test_descriptor_rejects_bad_id():
    for bad_id in ("", "Upper", "with space", "under_score"):
        with pytest.raises(ValueError):
            _make_descriptor(tool_id=bad_id)


def test_descriptor_rejects_empty_title():
    with pytest.raises(ValueError):
        _make_descriptor(title="")

    with pytest.raises(ValueError):
        _make_descriptor(title="   ")


def test_descriptor_defaults():
    descriptor = _make_descriptor()

    assert descriptor.order == 100
    assert descriptor.enabled is True
    assert descriptor.icon_name is None


class _DummyWorker(ToolWorker):
    def _execute(self):
        return


def test_tool_worker_exposes_signals():
    worker = _DummyWorker()

    for name in ("progress", "status", "finished_ok", "error", "canceled"):
        assert hasattr(worker, name)

    assert worker.is_cancel_requested() is False
    assert worker.cancel_event.is_set() is False


def test_tool_worker_cancel_is_idempotent():
    worker = _DummyWorker()

    worker.cancel()
    worker.cancel()

    assert worker.is_cancel_requested() is True
    assert worker.cancel_event.is_set() is True
    assert worker._cancel_requested is True


def test_tool_worker_base_execute_not_implemented():
    worker = ToolWorker()

    with pytest.raises(NotImplementedError):
        worker._execute()

    assert worker._on_cleanup() is None


def test_tool_error_is_exception():
    assert isinstance(ToolError("boom"), Exception)


@pytest.mark.parametrize(
    "worker_cls, args, extra_signals",
    [
        (DownloadThread, ("https://example.com/v", "mp4", "best"), ()),
        (ConvertThread, ([], "mp3", "192"), ()),
        (PreviewThread, ("https://example.com/v",), ("preview_ready",)),
    ],
)
def test_workers_subclass_tool_worker(worker_cls, args, extra_signals):
    assert issubclass(worker_cls, ToolWorker)

    worker = worker_cls(*args)

    for name in ("progress", "status", "finished_ok", "error", "canceled"):
        assert hasattr(worker, name)

    for name in extra_signals:
        assert hasattr(worker, name)

    assert worker.is_cancel_requested() is False
    worker.cancel()
    assert worker.is_cancel_requested() is True
    assert worker.cancel_event.is_set() is True


def test_register_default_tools_order():
    reg = ToolRegistry()
    register_default_tools(reg)

    assert [d.id for d in reg.enabled()] == ["download", "convert", "about"]
    assert [d.title for d in reg.enabled()] == ["Download", "Convert", "About"]
    assert [d.order for d in reg.enabled()] == [10, 20, 30]


def test_register_default_tools_is_idempotent():
    reg = ToolRegistry()
    register_default_tools(reg)
    register_default_tools(reg)

    assert [d.id for d in reg.all()] == ["download", "convert", "about"]


def test_register_default_tools_into_global_registry():
    register_default_tools(global_registry)

    assert [d.id for d in global_registry.enabled()] == ["download", "convert", "about"]

