"""Application compilation and the inbound MCP Host adapter.

This layer compiles a business application into one runtime. Agent hosts connect
to its native MCP server; human-facing hosts can pass that same runtime to
``contexture.web``.

**What** this server exposes is declared one layer down, in
`core.mcp_interface`, one module per MCP primitive. This package is **how**:
the SDK calls, the dispatch, and every sentence said to whoever is reading.

The responsibilities change at different rates, so they are separate modules
rather than one:

    surface/        the doors this server opens, one module per MCP primitive,
                    and the `Surface` that installs them. Moves when the SDK does.
    server          the container: one compiled index, built and served
    options         how to serve it — transport, address, who may knock
    binding         the one seam `core` opens: a tool's schema, and running it
                    with its arguments checked
    identity        who is calling: the socket a business plugs its token
                    verifier into, and the protocol facts around it. Moves
                    when the authorization specification does.
    messages        everything said *to* somebody: the bootstrap text, the
                    sentence a failed lookup becomes, what a person reads at
                    the top of a command. Moves when the way an agent is
                    taught to walk the tree changes.
    instructions    fitting that text into one host's budget. Moves when Claude
                    Code or Codex ships.

`launch` emits the one file a host still needs — the command that starts this
server — and belongs to none of them.

This facade resolves its exports lazily so that the modules which do not import
the SDK — `messages` and `launch` — stay importable, and testable, without a
wire in the room.
"""

# pyright: reportUnsupportedDunderAll=false

from __future__ import annotations

import importlib
from typing import Any

#: Exported name -> the submodule that defines it.
_EXPORTS = {
    "ApplicationRuntime": "..core.model.runtime",
    "InMemoryTelemetry": "..core.model.telemetry",
    "NodeUsage": "..core.model.telemetry",
    "Telemetry": "..core.model.telemetry",
    "CompiledApplication": ".application",
    "CompiledDisclosureApplication": ".application",
    "build_server": ".application",
    "compile_application": ".application",
    "compile_disclosure_application": ".application",
    "compile_disclosure_parts": ".application",
    "compile_parts": ".application",
    "serve": ".application",
    "ContextureServer": ".server",
    "ContextureOptions": ".options",
    "DEFAULT_HOST": ".options",
    "DEFAULT_PATH": ".options",
    "DEFAULT_PORT": ".options",
    "LOOPBACK": ".options",
    "ServeError": ".options",
    "Transport": ".options",
    "configure_logging": ".options",
    "Auth": ".identity",
    "TokenVerifier": ".identity",
    "FixedRootSelector": ".root_selector",
    "FixedSurfaceSelector": ".root_selector",
    "HeaderRootSelector": ".root_selector",
    "HeaderSurfaceSelector": ".root_selector",
    "ROOTS_HEADER": ".root_selector",
    "SELECT_HEADER": ".root_selector",
    "RootCeiling": ".root_selector",
    "RootSelector": ".root_selector",
    "RootSelection": "..core.model.root_selection",
    "SurfaceCeiling": ".root_selector",
    "SurfaceSelection": "..core.model.root_selection",
    "SurfaceSelector": ".root_selector",
    "Launch": ".launch",
    "claude_code_config": ".launch",
    "cli_commands": ".launch",
    "codex_config": ".launch",
    "cursor_config": ".launch",
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
