"""The kernel: what a capability is, where it hangs, and what an agent may do.

Three kinds of node, one disclosure lifecycle, one forest, and the five system
entry points an agent calls against it. Nothing here knows that MCP exists,
what JSON Schema looks like, or how a request reaches this process.

A node still cannot *work out* where it hangs — it is told, by the
`ControllerManager` that registered it, in segments rather than in an address.
What changed in ADR 014 is only who joins those segments up: `Disclosure`
spells the reference and decides which nodes one call answers with, and it sits
here rather than in a layer of its own, because the call that discloses a node
and the node that discloses itself are two halves of one mechanism.

A business subclasses these and states what each one is in its constructor,
which hands that identity to the base and builds whatever the node holds.
Nothing is inferred from a class name or a docstring, and nothing is built when
a declaration is imported: a class is a zero-argument factory, and a
`ControllerManager` is the one place every node comes into existence.
"""

# What a declaration is written against, and nothing else. `tree` and
# `system_api` are reached by their own module paths rather than re-exported
# here: a project that only declares context should not load the forest and the
# five entry points to do it, and every caller that wants them is a caller that
# is about to serve something.
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .channels import Channels as Channels
    from .graph_context import current_graph as current_graph
    from .manager import ControllerManager as ControllerManager, register_root as register_root
    from .node import (
        CompiledGraph as CompiledGraph, CompileLevel as CompileLevel,
        ContextNode as ContextNode, View as View,
    )
    from .role import PostProcess as PostProcess, PreProcess as PreProcess, Role as Role
    from .root_selection import RootSelection as RootSelection, SurfaceSelection as SurfaceSelection
    from .skill import Skill as Skill
    from .telemetry import (
        InMemoryTelemetry as InMemoryTelemetry, NodeUsage as NodeUsage,
        Telemetry as Telemetry, current_telemetry as current_telemetry,
    )
    from .tool import ExplicitSchemaTool as ExplicitSchemaTool, Tool as Tool

_EXPORTS = {
    "Channels": ".channels",
    "current_graph": ".graph_context",
    "ControllerManager": ".manager",
    "register_root": ".manager",
    "CompiledGraph": ".node",
    "CompileLevel": ".node",
    "ContextNode": ".node",
    "View": ".node",
    "PostProcess": ".role",
    "PreProcess": ".role",
    "Role": ".role",
    "RootSelection": ".root_selection",
    "SurfaceSelection": ".root_selection",
    "Skill": ".skill",
    "InMemoryTelemetry": ".telemetry",
    "NodeUsage": ".telemetry",
    "Telemetry": ".telemetry",
    "current_telemetry": ".telemetry",
    "ExplicitSchemaTool": ".tool",
    "Tool": ".tool",
}


def __getattr__(name: str) -> Any:
    """Keep the declaration facade without eagerly importing the static forest."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)

__all__ = [
    "Channels",
    "CompiledGraph",
    "CompileLevel",
    "ContextNode",
    "ControllerManager",
    "current_graph",
    "InMemoryTelemetry",
    "NodeUsage",
    "Telemetry",
    "current_telemetry",
    "View",
    "PostProcess",
    "PreProcess",
    "Role",
    "RootSelection",
    "SurfaceSelection",
    "Skill",
    "ExplicitSchemaTool",
    "Tool",
    "register_root",
]
