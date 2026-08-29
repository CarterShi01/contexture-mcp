"""The compiled graph serving the current Tool invocation."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from .node import CompiledGraph


_CURRENT_GRAPH: ContextVar[CompiledGraph | None] = ContextVar(
    "contexture_compiled_graph", default=None
)


def current_graph() -> CompiledGraph:
    """Return the exact immutable graph serving this call.

    Introspection Tools use this instead of receiving a second architecture
    registry through Channels.  It is available while a Tool is invoked and
    deliberately unavailable at declaration time or during ordinary disclosure.
    """

    graph = _CURRENT_GRAPH.get()
    if graph is None:
        raise RuntimeError(
            "No compiled Contexture graph is active. Use current_graph() only "
            "inside Tool.invoke()."
        )
    return graph


@contextmanager
def bound_graph(graph: CompiledGraph) -> Iterator[None]:
    """Bind one compiled graph task-locally for a framework-owned call."""

    token = _CURRENT_GRAPH.set(graph)
    try:
        yield
    finally:
        _CURRENT_GRAPH.reset(token)


__all__ = ["bound_graph", "current_graph"]
