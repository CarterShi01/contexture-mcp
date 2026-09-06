"""The server `test_http_server.py` launches, in its own process.

Not a test module — the discovery pattern is `test*.py`, which is why this is
named the way it is. It exists as a file rather than as a string passed to
`python -c` so that what is being served can be read like ordinary code, and
so that it is exactly the shape the README tells a developer to write.

Two tools and one verifier, which is the smallest arrangement that can show
the whole claim: identity arrives from the wire, reaches a capability's own
code, and the capability — not the framework — decides what to do about it.
"""

from __future__ import annotations

import sys

from contexture import (
    Principal,
    Prompt,
    Resource,
    Role,
    Tool,
    current_principal,
)
from contexture.core.model.index import Index
from contexture.core.model.manager import ControllerManager
from contexture.server import (
    Auth,
    ContextureOptions,
    ContextureServer,
    HeaderSurfaceSelector,
)
from contexture.server.binding import TypeHintBinding

#: Two callers who differ in exactly one scope, so a refusal can only be about
#: that scope and never about which of them was authenticated.
CALLERS = {
    "reader": Principal(
        subject="alice",
        client_id="claude-code",
        issuer="https://idp.example",
        scopes={"ctx.read"},
        claims={"tid": "acme"},
    ),
    "writer": Principal(
        subject="bob",
        client_id="codex",
        issuer="https://idp.example",
        scopes={"ctx.read", "k8s.write"},
        claims={"tid": "acme"},
    ),
}


class Verifier:
    """What a business writes. Note the absence of any MCP import."""

    async def verify(self, token: str) -> Principal | None:
        return CALLERS.get(token)


class WhoAmI(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="whoami",
            description="Report the caller this server sees.",
            read_only=True,
        )

    async def invoke(self) -> str:
        who = current_principal()
        if who is None:
            return "anonymous"
        return (
            f"{who.subject}|{who.client_id}|{who.issuer}|"
            f"{','.join(sorted(who.scopes))}|{who.claims.get('tid')}"
        )


class RollBack(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="roll_back",
            description="Reverse a release, for a caller allowed to.",
            read_only=False,
        )

    async def invoke(self, deployment: str) -> str:
        who = current_principal()
        if who is None:
            raise PermissionError("roll_back needs an authenticated caller.")
        if "k8s.write" not in who.scopes:
            raise PermissionError(f"{who.subject} lacks k8s.write.")
        return f"rolled back {deployment} for {who.subject}"


class Ops(Role):
    def __init__(self) -> None:
        super().__init__(
            name="ops",
            description="Operate a platform.",
            instructions="Read before you write.",
            tools=[WhoAmI(), RollBack()],
        )


class AuditRead(Tool):
    def __init__(self) -> None:
        super().__init__(name="read", description="Read audit.", read_only=True)

    async def invoke(self) -> str:
        return "audit"


class Audit(Role):
    def __init__(self) -> None:
        super().__init__(
            name="audit",
            description="Inspect global audit records.",
            instructions="Review audit records without changing them.",
            tools=[AuditRead()],
        )


def build(port: int, *, secured: bool) -> tuple[ContextureServer, ContextureOptions]:
    manager = ControllerManager()
    manager.register_role(Ops)
    manager.register_role(Audit)
    index = Index.of(manager, bind=TypeHintBinding)
    server = ContextureServer(
        index,
        name="http-fixture",
        surface_selector=HeaderSurfaceSelector(),
        prompts=(
            Prompt(opens="ops", name="open-ops", description="Open operations."),
            Prompt(opens="audit", name="open-audit", description="Open audit."),
        ),
        resources=(
            Resource(
                opens="ops/whoami",
                uri="contexture://ops/whoami",
                description="Current request identity.",
            ),
            Resource(
                opens="audit/read",
                uri="contexture://audit/read",
                description="Audit records.",
            ),
        ),
    )
    options = ContextureOptions(
        transport="streamable-http",
        host="127.0.0.1",
        port=port,
        auth=(
            Auth(
                verifier=Verifier(),
                issuer="https://idp.example",
                resource=f"http://127.0.0.1:{port}/mcp",
            )
            if secured
            else None
        ),
    )
    return server, options


def main() -> None:
    port = int(sys.argv[1])
    secured = sys.argv[2] == "secured"
    server, options = build(port, secured=secured)
    server.start(options)


if __name__ == "__main__":
    main()
