"""A server whose capabilities reach something outside their own process.

Launched as a subprocess by `test_channels.py`. It is written the way an
application with downstream connections is meant to be written, and the order
is the point: the connection is built first, handed to the registry, and only
then is anything registered against it.

The "connection" here is a dictionary rather than a socket. What is being
tested is that a capability reaches *the object the application built*, and a
real socket would only add a second thing that can fail.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from contexture import (
    Channels,
    Prompt,
    Resource,
    Role,
    Skill,
    Tool,
)
from contexture.core.model.index import Index
from contexture.core.model.manager import ControllerManager
from contexture.server import (
    ContextureOptions,
    ContextureServer,
)
from contexture.server.binding import TypeHintBinding


@dataclass
class Gateway:
    """Stands in for a session against a remote MCP gateway."""

    endpoint: str
    calls: list[str] = field(default_factory=list)

    def call(self, name: str) -> str:
        self.calls.append(name)
        return f"{self.endpoint}:{name}#{len(self.calls)}"


@dataclass
class Downstream:
    """Everything this application reaches outside its own process.

    A plain value: it is simply constructed, so it needs no lifecycle and does
    not subclass `Channels`. Handed to the registry and stamped onto every
    controller, and never looked at by the framework again.
    """

    gateway: Gateway
    catalogue: dict[str, str]


class NotifySquad(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="notify_squad",
            description="Notify the squad that owns a service, through the gateway.",
            read_only=False,
        )

    async def invoke(self, squad: str) -> str:
        return self.channels.gateway.call(f"notify:{squad}")


class WhereAmI(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="where_am_i",
            description="Report this capability's own address and what it can reach.",
            read_only=True,
        )

    async def invoke(self) -> str:
        return f"{'/'.join(self.path)} -> {self.channels.gateway.endpoint}"


class Runbook(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="runbook",
            description="The escalation runbook, as the gateway currently publishes it.",
            read_only=True,
        )

    async def invoke(self) -> str:
        return self.channels.catalogue["runbook"]


class Escalate(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="escalate",
            description="Escalate an incident to the squad that owns the service.",
            instructions="1. Read the runbook. 2. Call notify_squad with the owner.",
        )


class Escalation(Role):
    def __init__(self) -> None:
        super().__init__(
            name="escalation",
            description="Reach the humans who own a failing service.",
            instructions="Read the runbook before paging anyone.",
            skills=[Escalate()],
            tools=[NotifySquad(), WhereAmI(), Runbook()],
        )


class Operations(Role):
    def __init__(self) -> None:
        super().__init__(
            name="operations",
            description="Operate the platform.",
            instructions="Route to the branch that owns the question.",
            children=[Escalation()],
        )


DOCUMENTS = (
    Resource(
        opens="operations/escalation/runbook",
        uri="contexture://runbooks/escalation",
        mime_type="text/markdown",
        description="How to escalate, as the gateway publishes it.",
    ),
)
COMMANDS = (
    Prompt(
        opens="operations/escalation/escalate",
        description="Escalate an incident to the owning squad.",
    ),
)


def _channels() -> Downstream:
    return Downstream(
        gateway=Gateway(endpoint="https://gateway.internal"),
        catalogue={"runbook": "Page the owner, then open an incident channel."},
    )


class OpenedChannels(Channels):
    """The same handle, for a deployment where it has to be opened.

    `LIFECYCLE_MARKS` names a file each step appends to, which is how a test
    outside this process can see that opening happened before the first request
    and that closing happened after the last. A real deployment would be
    opening a pool or a session here; the marks are what make the *order*
    observable, which is the whole claim.
    """

    def __init__(self, marks: Path) -> None:
        # No `super().__init__()`: the base keeps its exit stack as a class
        # attribute for exactly this reason.
        self.marks = marks
        self.gateway: Gateway | None = None
        self.catalogue: dict[str, str] = {}

    def _mark(self, step: str) -> None:
        with self.marks.open("a", encoding="utf-8") as handle:
            handle.write(f"{step}\n")

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Downstream]:
        self._mark("open")
        try:
            yield _channels()
        finally:
            # Closing is the half a process that is simply killed never gets
            # to do, which is why it is worth a mark of its own.
            self._mark("close")

    async def open(self) -> None:
        opened = await self.enter(self._session())
        self.gateway = opened.gateway
        self.catalogue = opened.catalogue

    async def close(self) -> None:
        # Cleared here rather than by the framework: a call arriving after
        # shutdown then meets `None`, which fails legibly, instead of a session
        # somebody already closed.
        self.gateway = None
        self.catalogue = {}


def build(*, channels: object | None = None) -> ContextureServer:
    """Build the server the way `main()` should read.

    Connections first, because a capability that cannot reach its downstream is
    not ready to be served; then the registry, which is what hands the handle to
    everything it registers; then the seal, then the server.
    """

    manager = ControllerManager(
        channels=_channels() if channels is None else channels
    )
    manager.register_role(Operations)

    index = Index.of(manager, bind=TypeHintBinding)
    return ContextureServer(
        index, name="channels-fixture", prompts=COMMANDS, resources=DOCUMENTS
    )


def main() -> None:
    marks = os.environ.get("LIFECYCLE_MARKS")
    server = build(channels=OpenedChannels(Path(marks)) if marks else None)
    server.start(ContextureOptions(transport="stdio"))


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    main()
