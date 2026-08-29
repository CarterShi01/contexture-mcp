"""The kernel: what a capability is, where it hangs, and what an agent may do.

Three kinds of node, one disclosure lifecycle, one forest, and the four system
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
# four entry points to do it, and every caller that wants them is a caller that
# is about to serve something.
from .channels import Channels
from .graph_context import current_graph
from .manager import ControllerManager, register_root
from .node import CompiledGraph, CompileLevel, ContextNode, View
from .role import Role
from .skill import Skill
from .telemetry import InMemoryTelemetry, NodeUsage, Telemetry, current_telemetry
from .tool import Tool

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
    "Role",
    "Skill",
    "Tool",
    "register_root",
]
