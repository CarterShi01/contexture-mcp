"""The object a business application hands to its host.

A project registers its graph, compiles it, and serves it::

    def main() -> None:
        manager = ControllerManager(channels=channels)
        manager.register_role(KubernetesPlatform)

        index = Index.of(manager, bind=TypeHintBinding)

        server = ContextureServer(
            index,
            name="oc-goal",
            prompts=[RollBackARelease],
            resources=[CrashLoopRunbook, RollbackPolicy],
        )
        server.start(ContextureOptions(transport="stdio"))

Nothing above that last line mentions JSON-RPC, JSON Schema, stdio framing, or
any particular agent runtime. That is the whole claim the framework makes:
declare once, and Claude Code, Codex, and anything else that speaks MCP connect
to the same server.

**It takes a compiled index and has no way to register anything.** That is the
phase boundary, made structural rather than written down: you register into a
`ControllerManager`, you compile it once into an `Index`, and only then does a
server exist. There is no method here that could add a node to a graph that is
already being served, which is what the protocol forbids and what a run-time
flag could only complain about after the fact.

**It deliberately does not subclass the SDK's `MCPServer`.** The runtime owns
roles and disclosure; the SDK owns the wire. Keeping them as two objects that
compose is what lets the domain model stay testable without a transport, and
what keeps an SDK upgrade from reaching into the object model.

**Two objects, because they are fixed at two different times.**
`ContextureServer` is identity and topology — what is served, and what it is
called — settled before the process starts and unchanged after.
`ContextureOptions` is how to serve it: which transport, which address, who is
allowed to knock. Serving the same graph on a laptop and in a cluster changes
the second and none of the first.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import anyio
from mcp.server.mcpserver import MCPServer

from ..core.constants import PACKAGE_VERSION
from ..core.mcp_interface.tool import TOOLS, ToolPlane
from ..core.model.channels import Channels
from ..core.model.disclosure import Disclosure
from ..core.model.index import Index
from ..core.model.telemetry import InMemoryTelemetry, Telemetry
from . import instructions as instructions_module
from .identity import Auth
from .options import ContextureOptions, ServeError, Transport, configure_logging
from .surface import DisclosureSurface, Surface

LOG = logging.getLogger(__name__)


class ContextureServer:
    """One sealed graph, served over MCP."""

    __slots__ = (
        "_index", "_surface", "_telemetry", "name", "version",
        "instructions", "_built", "_auth",
    )

    def __init__(
        self,
        index: Index,
        *,
        name: str = "contexture",
        version: str = PACKAGE_VERSION,
        instructions: str | None = None,
        tools: ToolPlane = TOOLS,
        prompts: Any = (),
        resources: Any = (),
        telemetry: Telemetry | None = None,
        surface: Surface | DisclosureSurface | None = None,
    ) -> None:
        """Serve one compiled index, with what a person and a host may reach it by.

        `tools` takes exactly one value and cannot take another — the tool plane
        is not extensible (see `ToolPlane`). It is in the signature so the three
        planes read as a table: the one whose argument you cannot vary is the one
        you cannot add to. `prompts` and `resources` are the two you can.

        The surface is built here, not at `build`, so a bad declaration is
        refused while a caller still holds the traceback — the same moment the
        index itself was compiled.
        """

        if not isinstance(tools, ToolPlane):
            raise ServeError(
                "The tool plane is fixed: pass `tools=TOOLS` or leave it. A "
                "business capability reaches an agent inside a payload, not by "
                "being registered on this plane."
            )
        self._index = index
        self._telemetry = telemetry if telemetry is not None else InMemoryTelemetry()
        if surface is not None:
            if surface.tree.index is not index:
                raise ServeError(
                    "A prepared surface must disclose the same Index passed to "
                    "ContextureServer. Independent applications cannot share a "
                    "server container."
                )
            if prompts or resources:
                raise ServeError(
                    "A prepared surface already owns its prompts and resources; "
                    "do not pass them to ContextureServer again."
                )
            self._surface = surface
        else:
            disclosure = Disclosure(index)
            self._surface = Surface.of(
                disclosure,
                prompts=prompts,
                resources=resources,
                telemetry=self._telemetry,
            )
        self.name = name
        self.version = version
        #: What a host reads before it calls anything. Derived from the tree
        #: when nothing is stated, which is the ordinary case — see
        #: `server.instructions` for how it is fitted to a host's budget.
        self.instructions = instructions
        self._built: MCPServer | None = None
        self._auth: Auth | None = None

    @property
    def surface(self) -> Surface | DisclosureSurface:
        """What this server serves: its doors, and the view behind them."""

        return self._surface

    @property
    def index(self) -> Index:
        """The compiled forest this server serves. Frozen."""

        return self._index

    @property
    def telemetry(self) -> Telemetry:
        """Framework-owned node usage evidence, separate from disclosure."""

        return self._telemetry

    # ---- building --------------------------------------------------------

    def build(self, *, auth: Auth | None = None) -> MCPServer:
        """Install the surface on an SDK server, and return it.

        **Synchronous, and it stays that way.** Tests build a server and call
        into it directly, with no transport and no session, which is what lets
        the disclosure model be exercised without the wire. So a lifecycle wraps
        *serving* rather than *construction*, which is also what the SDK's own
        `lifespan` hook does — verified entered and exited on both the stdio and
        the streamable-HTTP path.

        **Idempotent.** `start` calls this, and a caller that built a server to
        look at it and then started it would otherwise be serving a second one.
        A different `auth` on a later call is refused rather than ignored,
        because only one of the two answers can be the one on the wire.

        The surface was already built and validated in `__init__`; all that is
        left here is to hang it on a fresh SDK server.
        """

        if self._built is not None:
            if auth is not None and auth is not self._auth:
                raise ServeError(
                    "This server was already built with a different auth. One "
                    "process serves one surface; build it once, or build two "
                    "servers if you genuinely mean two."
                )
            return self._built

        wire = self._wire(auth)
        self._surface.install(wire)

        self._auth = auth
        self._built = wire
        return wire

    def _wire(self, auth: Auth | None) -> MCPServer:
        """The SDK object, told this server's identity and its lifecycle."""

        return MCPServer(
            name=self.name,
            version=self.version,
            instructions=self.instructions
            or instructions_module.build(self._surface.tree),
            **({"lifespan": self._lifespan} if self._opens_channels else {}),
            **(
                {"auth": auth.settings(), "token_verifier": auth.sdk_verifier()}
                if auth is not None
                else {}
            ),
        )

    @property
    def _opens_channels(self) -> bool:
        """Whether anything has to be opened before the first request."""

        return isinstance(self._index.channels, Channels)

    @asynccontextmanager
    async def _lifespan(self, server: MCPServer) -> AsyncIterator[Any]:
        """Open this graph's handle for exactly as long as it is serving.

        What this yields reaches the SDK as `request_context.lifespan_context`,
        and nothing here reads it back: a capability finds its handle on itself,
        stamped by the registry, because half the doors into a capability carry
        no request context at all.
        """

        async with self._index.provisioned() as opened:
            yield opened

    # ---- serving ---------------------------------------------------------

    def start(
        self,
        options: ContextureOptions | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        """Serve the graph. Blocks until the host disconnects.

        `transport=` is kept as the shorthand for the one-word case, because
        `server.start(transport="stdio")` is what a reader expects to be able
        to write. Anything beyond a transport name needs the options object —
        there is deliberately no `**kwargs`, since the thing it did best was
        accept arguments the SDK would then discard.
        """

        anyio.run(lambda: self.start_async(options, transport=transport))

    async def start_async(
        self,
        options: ContextureOptions | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        """The same, for a process that already has an event loop.

        What this does *not* do is mount into somebody else's ASGI
        application: the HTTP branch runs the SDK's own uvicorn. A host that
        wants this graph inside its own Starlette app should take
        `build().streamable_http_app()` and route to it.
        """

        options = self._resolved(options, transport)
        configure_logging(options.log_level)
        surface = self.build(auth=options.auth)
        if options.transport == "stdio":
            await surface.run_stdio_async()
            return
        # Printed to the log rather than left to be inferred: the address is
        # assembled from four fields and a wrong one produces a server that
        # starts and answers nobody.
        LOG.info("Serving MCP on %s", options.url)
        await surface.run_streamable_http_async(**options.transport_kwargs())

    @staticmethod
    def _resolved(
        options: ContextureOptions | None,
        transport: Transport | None,
    ) -> ContextureOptions:
        if options is not None and transport is not None:
            raise ServeError(
                "Pass either options or transport=, not both: "
                f"options.transport is {options.transport!r} and transport= is "
                f"{transport!r}, and nothing here can tell which you meant."
            )
        if options is not None:
            return options
        return ContextureOptions(
            **({"transport": transport} if transport is not None else {})
        )


__all__ = ["ContextureServer"]
