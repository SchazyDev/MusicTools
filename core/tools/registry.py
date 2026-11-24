"""Registry of available tools.

The registry knows nothing about the UI; descriptors carry a ``factory``
callable that the UI layer invokes lazily.
"""

from typing import Dict, List, Optional

from core.tools.base import ToolDescriptor

__all__ = ["ToolRegistry", "registry"]


class ToolRegistry:
    """Mutable collection of :class:`ToolDescriptor` objects keyed by id."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDescriptor] = {}

    def register(self, descriptor: ToolDescriptor) -> ToolDescriptor:
        """Register a descriptor; ids must be unique."""
        if descriptor.id in self._tools:
            raise ValueError(f"Tool with id {descriptor.id!r} is already registered")

        self._tools[descriptor.id] = descriptor
        return descriptor

    def unregister(self, tool_id: str) -> None:
        """Remove a tool by id. Missing ids are ignored."""
        self._tools.pop(tool_id, None)

    def get(self, tool_id: str) -> Optional[ToolDescriptor]:
        """Return the descriptor for ``tool_id`` or None."""
        return self._tools.get(tool_id)

    def all(self) -> List[ToolDescriptor]:
        """Return every descriptor sorted by (order, title)."""
        return sorted(self._tools.values(), key=lambda d: (d.order, d.title))

    def enabled(self) -> List[ToolDescriptor]:
        """Return enabled descriptors sorted by (order, title)."""
        return [d for d in self.all() if d.enabled]

    def __contains__(self, tool_id: object) -> bool:
        return tool_id in self._tools

    def __len__(self) -> int:
        return len(self._tools)


registry = ToolRegistry()
