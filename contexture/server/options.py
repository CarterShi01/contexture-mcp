"""How a graph is served: which transport, which address, who may knock.

Not *what* is served, and not what it is called — those are settled before the
process starts and unchanged after, and they live on `ContextureServer`. This
module is the other half, and the split is why there are two objects: serving
the same graph on a laptop and in a cluster changes everything here and nothing
there.

    server.start(ContextureOptions(
        transport="streamable-http",
        host="0.0.0.0",
        port=8080,
        auth=Auth(verifier=OktaVerifier(), issuer=..., resource=...),
        allowed_origins=["https://acme.example"],
    ))

`configure_logging` is here for the same reason: under stdio the protocol owns
stdout, so where log records go is a fact about serving.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Mapping

from mcp.server.transport_security import TransportSecuritySettings

from ..core.errors import ContextureError

if TYPE_CHECKING:
    from .identity import Auth

#: The transports this server offers. HTTP+SSE was the 2024-11-05 two-endpoint
#: transport; it was replaced by Streamable HTTP and is deprecated in the
#: revisions this server speaks, so it is not offered. A host that still needs
#: it can put a proxy in front.
Transport = Literal["stdio", "streamable-http"]

#: Where an HTTP server listens when nobody says otherwise. Loopback, because
#: a context server's tools are the interesting half of a machine and binding
#: wider should be something a person typed.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_PATH = "/mcp"

#: Addresses that reach this machine and nowhere else. The SDK turns on DNS
#: rebinding protection by itself for exactly these, and leaves it off for
#: everything else — which is the gap `ContextureOptions` closes.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: Fields that mean nothing under stdio. Naming them is what turns
#: `run(transport="stdio", port=9000)` from a silently discarded argument into
#: a sentence.
_HTTP_ONLY = (
    "host",
    "port",
    "path",
    "auth",
    "allowed_hosts",
    "allowed_origins",
    "allow_anonymous",
    "max_request_body_size",
)

#: SDK keyword arguments this object owns. Setting one through `sdk_overrides`
#: would leave two places deciding the same thing, and the loser would be
#: whichever this code happens to apply second.
_OWNED_BY_OPTIONS = frozenset(
    {
        "host",
        "port",
        "streamable_http_path",
        "transport_security",
        "max_request_body_size",
    }
)


class ServeError(ContextureError):
    """Raised when how-to-serve was stated in a way that cannot be honoured."""


@dataclass(slots=True, frozen=True, kw_only=True)
class ContextureOptions:
    """How to serve a graph. Not what to serve, and not what it is called.

    ::

        app.run()                                     # stdio, the default
        app.run(ContextureOptions(
            transport="streamable-http",
            host="0.0.0.0",
            port=8080,
            auth=Auth(verifier=OktaVerifier(), issuer=..., resource=...),
            allowed_origins=["https://acme.example"],
        ))

    Every field defaults to `None` rather than to its eventual value, so that
    "not stated" and "stated as the default" stay different things. That is
    what lets `transport="stdio", port=8000` be reported as the mistake it is
    instead of being quietly dropped, which is what a `**kwargs` passthrough
    did and could not help doing.
    """

    transport: Transport = "stdio"

    #: Interface to bind. Defaults to loopback; see `LOOPBACK`.
    host: str | None = None

    port: int | None = None

    #: Where the MCP endpoint lives. Defaults to `/mcp`.
    path: str | None = None

    #: How this server checks who is calling. `None` serves everybody as an
    #: unauthenticated caller, and `current_principal()` returns `None`
    #: throughout.
    auth: Auth | None = None

    #: `Host` and `Origin` headers this server will answer. Required once
    #: `host` is not loopback: the SDK enables DNS rebinding protection on its
    #: own for loopback addresses and silently leaves it off for the rest, so
    #: the address where it matters is the one where nothing would say so.
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    #: Serve a non-loopback address with no `auth`. It has to be typed, because
    #: everything the declared tools can reach becomes reachable by anyone who
    #: can route to this port.
    allow_anonymous: bool = False

    log_level: int = logging.INFO

    max_request_body_size: int | None = None

    #: The deliberate escape hatch, deliberately named as one. Anything the SDK
    #: accepts and this object has no opinion about — `json_response`, say —
    #: goes here, and the coupling shows up in the code that takes it rather
    #: than hiding inside `**kwargs`.
    sdk_overrides: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_hosts", tuple(self.allowed_hosts))
        object.__setattr__(self, "allowed_origins", tuple(self.allowed_origins))
        object.__setattr__(self, "sdk_overrides", dict(self.sdk_overrides))
        self._check()

    # ------------------------------------------------------------- validation

    def _check(self) -> None:
        if self.transport == "stdio":
            self._reject_http_fields()
        else:
            self._require_safe_bind()
        self._reject_owned_overrides()

    def _reject_http_fields(self) -> None:
        """Refuse the arguments stdio would throw away.

        The SDK's stdio branch is `anyio.run(self.run_stdio_async)` and takes
        no keyword arguments at all, so a port stated alongside it used to
        vanish without a word. A mistake that produces a working server on the
        wrong assumption is worse than one that produces no server.
        """

        stated = [
            name
            for name in _HTTP_ONLY
            if getattr(self, name) not in (None, (), False)
        ]
        if stated:
            raise ServeError(
                f"transport='stdio' cannot use {', '.join(stated)}: stdio has "
                "no address to bind and no request to authenticate — the host "
                "launched this process, and the operating system already "
                "decided who is running it. Use "
                "transport='streamable-http' if you meant to serve over HTTP."
            )

    def _require_safe_bind(self) -> None:
        """Two things that must be typed before this port faces anyone else."""

        if self.resolved_host in LOOPBACK:
            return
        if not (self.allowed_hosts or self.allowed_origins):
            raise ServeError(
                f"host={self.resolved_host!r} is not loopback, so DNS "
                "rebinding protection does not turn itself on. State "
                "allowed_hosts and/or allowed_origins — without them a page in "
                "somebody's browser can drive this server through their "
                "machine."
            )
        if self.auth is None and not self.allow_anonymous:
            raise ServeError(
                f"host={self.resolved_host!r} is not loopback and no auth was "
                "given, so everything the declared tools can reach becomes "
                "reachable by anyone who can route to port "
                f"{self.resolved_port}. Pass auth=Auth(...), or state "
                "allow_anonymous=True if that is genuinely what you want."
            )

    def _reject_owned_overrides(self) -> None:
        """Keep `sdk_overrides` an escape hatch rather than a second steering wheel."""

        if "stateless_http" in self.sdk_overrides:
            raise ServeError(
                "stateless_http is not a knob this framework offers. The "
                "server keeps no per-connection state and a ref resolves the "
                "same way for everyone, which is what makes progressive "
                "disclosure legal under a protocol that has no session; "
                "serving with sessions would make that documented design false "
                "without making anything fail. It is pinned True."
            )
        if "event_store" in self.sdk_overrides:
            raise ServeError(
                "event_store replays a stream back to a resumed session, and "
                "this server has no sessions to resume — see stateless_http."
            )
        clashing = sorted(_OWNED_BY_OPTIONS & set(self.sdk_overrides))
        if clashing:
            raise ServeError(
                f"sdk_overrides may not set {', '.join(clashing)}: "
                "ContextureOptions owns those, and two places deciding one "
                "value means the answer depends on which is applied last."
            )

    # --------------------------------------------------------------- resolved

    @property
    def resolved_host(self) -> str:
        return self.host if self.host is not None else DEFAULT_HOST

    @property
    def resolved_port(self) -> int:
        return self.port if self.port is not None else DEFAULT_PORT

    @property
    def resolved_path(self) -> str:
        return self.path if self.path is not None else DEFAULT_PATH

    @property
    def url(self) -> str:
        """Where a host should point, once this is serving."""

        return f"http://{self.resolved_host}:{self.resolved_port}{self.resolved_path}"

    def transport_kwargs(self) -> dict[str, Any]:
        """The keyword arguments the SDK's runner is given.

        `stateless_http=True` is here rather than in a field: see
        `_reject_owned_overrides` for why it is not offered as a choice.
        """

        if self.transport == "stdio":
            return {}
        security: TransportSecuritySettings | None = None
        if self.allowed_hosts or self.allowed_origins:
            security = TransportSecuritySettings(
                allowed_hosts=list(self.allowed_hosts),
                allowed_origins=list(self.allowed_origins),
            )
        kwargs: dict[str, Any] = {
            "host": self.resolved_host,
            "port": self.resolved_port,
            "streamable_http_path": self.resolved_path,
            "stateless_http": True,
            "transport_security": security,
        }
        if self.max_request_body_size is not None:
            kwargs["max_request_body_size"] = self.max_request_body_size
        kwargs.update(self.sdk_overrides)
        return kwargs


def configure_logging(level: int = logging.INFO) -> None:
    """Send every log record to stderr.

    Under stdio the protocol owns stdout exclusively — the specification says a
    server MUST NOT write anything there that is not a valid MCP message — so a
    single stray print or a default handler that happens to target stdout
    corrupts the stream for the whole session. Configuring this in one place
    means a business application never has to remember it.
    """

    root = logging.getLogger()
    for handler in list(root.handlers):
        stream = getattr(handler, "stream", None)
        if stream is None or stream is sys.stdout:
            root.removeHandler(handler)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    root.setLevel(level)


__all__ = [
    "ContextureOptions",
    "DEFAULT_HOST",
    "DEFAULT_PATH",
    "DEFAULT_PORT",
    "LOOPBACK",
    "ServeError",
    "Transport",
    "configure_logging",
]
