"""The Contexture kernel.

No wire protocol, no agent runtime, and no knowledge that MCP exists. It owns
what a capability is, where it hangs, how much of it arrives at a time, and the
four calls an agent may make against it. Everything above depends on this
package, and this package depends on none of them.

It does own a lifecycle: a handle is opened before the first request and closed
after the last. A node can only be told where it hangs and what it may reach at
the moment it is registered, so registration and provisioning are one event.
What `core` does not have is a *wire*.

Two directories live here, because they answer two different questions:

    model            what a capability is, where it hangs, what may be done to it
    mcp_interface    what this server exposes on each of MCP's primitives

`errors`, `types`, `constants` and `principal` sit directly here as shared
ground: both directories may stand on them, and they stand on nothing. That is
what lets the two stay independent of *each other* without each growing its own
copy of an exception hierarchy — and it is why the separator and the four entry
point names live there too.

This facade re-exports the object model, which is what a business developer
declares against, and resolves each name on first use. Eager re-exports would
make this file import its own sub-layers, which is the one dependency the
shared ground is not allowed to have — and would quietly load the forest for a
project that only wanted to declare a Role.
"""

from __future__ import annotations

import importlib
from typing import Any

#: Exported name -> the submodule that defines it.
_EXPORTS = {
    "ContextureError": ".errors",
    "DeclarationError": ".errors",
    "DuplicateNameError": ".errors",
    "ModelValidationError": ".errors",
    "NodeNotFoundError": ".errors",
    "WrongDoorError": ".errors",
    "Principal": ".principal",
    "bound": ".principal",
    "current_principal": ".principal",
    "current_graph": ".model",
    "Channels": ".model",
    "CompileLevel": ".model",
    "ContextNode": ".model",
    "ControllerManager": ".model",
    "InMemoryTelemetry": ".model",
    "NodeUsage": ".model",
    "Telemetry": ".model",
    "current_telemetry": ".model",
    "Role": ".model",
    "Skill": ".model",
    "Tool": ".model",
}


def __getattr__(name: str) -> Any:
    """Resolve an export on first use, then cache it in the module globals."""

    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = sorted(_EXPORTS)
