"""Contexture — one Controller framework for agent and human Views.

An application declares its roles, skills and tools once against this object
model. Contexture can publish that declaration through a native MCP server for
agents and explicit REST routes for human-facing dashboards.

It does not run an agent loop, choose tools, or talk to a model. Those belong
to the runtime that connects.

The package is layered, and the layering is the architecture:

    contexture.core.model          the kernel: what a capability is, where it
                                   hangs, and the four calls an agent makes
    contexture.core.mcp_interface  what each MCP primitive carries; still no SDK
    contexture.server              compilation and the native MCP Host adapter
    contexture.web                 the explicit REST/ASGI Host adapter

Each layer may import the ones below it and never the reverse. This facade
exports what a business developer *declares* with, and nothing the framework
*runs* with: importing it loads neither the forest, nor the four entry points,
nor `contexture.server`, nor the SDK, so a project that only models context
pays for only that.
"""

from .core.constants import PACKAGE_VERSION as __version__
from .core import (
    Channels,
    CompileLevel,
    ContextNode,
    ControllerManager,
    ContextureError,
    DeclarationError,
    DuplicateNameError,
    ModelValidationError,
    NodeNotFoundError,
    Principal,
    Role,
    Skill,
    Tool,
    bound,
    current_principal,
)

# The two planes a declaration writes on arrive through one import, because
# which of them a thing belongs to is a modelling decision and not a question
# about this package's directories. `Prompt` and `Resource` are *constructed*
# where the three above are *subclassed*; that difference is stated in their
# own docstrings and enforced when a subclass is attempted, not spelled out in
# an import path a reader has to decode.
#
# It costs nothing to put them here: `core.mcp_interface` stands only on the
# shared ground, so importing this facade still loads no disclosure layer and
# no SDK.
from .core.mcp_interface import Prompt, Resource


def __getattr__(name: str) -> object:
    """Resolve the Application facade only when a project asks for it."""

    if name != "Contexture":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .application import Contexture

    globals()[name] = Contexture
    return Contexture

__all__ = [
    "Channels",
    "CompileLevel",
    "Contexture",
    "ContextNode",
    "ControllerManager",
    "ContextureError",
    "DeclarationError",
    "DuplicateNameError",
    "ModelValidationError",
    "NodeNotFoundError",
    "Principal",
    "Prompt",
    "Resource",
    "Role",
    "Skill",
    "Tool",
    "__version__",
    "bound",
    "current_principal",
]
