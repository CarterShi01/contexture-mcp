"""The doors this server opens, and the one object that installs them.

MCP splits its primitives by *who decides when one is used*, and this package
splits the same way — one door per primitive, each owning its own entries, its
own rules, and its own install:

    tools.py       the four fixed entries a model drives; a business adds none
    prompts.py     what a person triggers by name, plus `goto`
    resources.py   what a host may take up on its own

This replaced `projection/` hanging things off an `Assembly` data bag. The bag
held a mixed `published` list and split it by `isinstance` at every use; now the
two typed lists arrive already apart, and each door reads only its own.

**Constructed, then installed.** Every door validates in its constructor, and
`Surface.of` constructs all three before `install` writes any of them onto the
SDK server — so a declaration error is raised while that server does not yet
exist, instead of half way through hanging things on one. It is this package's
own idiom: a constructor is where a declaration is checked (ADR 013).

What is checked *here* rather than in `core` is what would stop being true if
MCP were replaced. That a published entry names a node the tree holds is about
addresses and is settled with the index; that two entries cannot share a name is
about a flat menu, and that a resource must be read-only and argument-free is
about a primitive that is fetched rather than called. Those are facts about this
protocol, so they live beside the code that speaks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ...core.errors import ContextureError, ModelValidationError, NodeNotFoundError
from ...core.mcp_interface import published as _published
from ...core.mcp_interface.prompt import Prompt
from ...core.mcp_interface.resource import Resource
from ...core.model.disclosure import SEPARATOR, Disclosure
from ...core.model.system_api import SystemAPI
from ...core.model.telemetry import InMemoryTelemetry, Telemetry


def published_name(entry: Prompt | Resource) -> str:
    """The name a host shows, defaulting to the last segment of the ref.

    A second name, independent of position — the same thing a URI has always
    been for a document, now the only kind of second name in the package.
    """

    return entry.name or entry.opens.rsplit(SEPARATOR, 1)[-1]


class translated:
    """Put a Contexture failure on the wire as the protocol's own error.

    One branch, and that is the point: every failure that reaches here already
    carries the sentence its audience needs. A `Refused` was composed by the
    kernel, which is the only layer that knows both what went wrong and the name
    of the call that recovers from it; anything else is a declaration-time
    failure whose audience is whoever wrote the declaration, and it carries its
    own sentence too.
    """

    __slots__ = ()

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc is None or not isinstance(exc, ContextureError):
            return False
        raise ToolError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class Surface:
    """Every door this server opens, and everything they were built from.

    The composite of the three doors. It sorts what a business published into
    two typed lists, resolves each against the forest, derives the one fact the
    kernel needs from them — which refs a person has claimed — and holds the
    kernel the doors share. `install` writes all three onto one SDK server.
    """

    #: The disclosure view every door reads the forest through.
    tree: Disclosure

    #: The four calls, bound to that view, with `reserved` already derived. The
    #: doors close over it; nothing downstream builds a second one.
    api: SystemAPI

    #: What a person may trigger by name, and what a host may take up on its
    #: own — already apart, so no door has to ask which kind an entry is.
    prompts: tuple[Prompt, ...] = ()
    resources: tuple[Resource, ...] = ()

    _doors: tuple[Any, ...] = field(default=(), repr=False)

    @classmethod
    def of(
        cls,
        tree: Any,
        *,
        prompts: Sequence[Any] = (),
        resources: Sequence[Any] = (),
        telemetry: Telemetry | None = None,
    ) -> "Surface":
        """Build every door over one view, validating as each is constructed.

        Entries are stated as classes, like everything else a business declares;
        already-built values are accepted too, and both arrive normalised. Each
        is resolved against the forest here — a failed lookup at build time has a
        different audience from one at request time: nobody is waiting on an
        answer, and the person who can fix it wrote the declaration.
        """

        from .prompts import Prompts
        from .resources import Resources
        from .tools import Tools

        prompt_entries = tuple(_as(Prompt, entry) for entry in prompts)
        resource_entries = tuple(_as(Resource, entry) for entry in resources)
        for entry in (*prompt_entries, *resource_entries):
            _require_resolvable(tree, entry.opens, entry.kind)

        reserved = frozenset(
            entry.opens for entry in prompt_entries if not entry.model_may_open
        )
        api = SystemAPI(
            tree=tree,
            reserved=reserved,
            telemetry=telemetry if telemetry is not None else InMemoryTelemetry(),
        )

        # Constructed here, all three, before anything is installed: a door
        # checks its own declarations in its constructor, so a refusal leaves
        # nothing half-registered on an SDK server that does not yet exist.
        doors = (
            Tools(api),
            Prompts(api, prompt_entries),
            Resources(api, resource_entries),
        )
        return cls(
            tree=tree,
            api=api,
            prompts=prompt_entries,
            resources=resource_entries,
            _doors=doors,
        )

    @property
    def published(self) -> tuple[Prompt | Resource, ...]:
        """Every published entry, prompts first. For a caller counting them."""

        return (*self.prompts, *self.resources)

    def install(self, wire: MCPServer) -> None:
        """Hang all three doors on one SDK server, in a fixed order."""

        for door in self._doors:
            door.install(wire)


def _as(kind: type, entry: Any) -> Any:
    """Normalise one entry and confirm it belongs to the plane it was listed on.

    A class is built; an instance passes through. Listing a `Resource` under
    `prompts=` is a wiring mistake the type catches here rather than three doors
    later.
    """

    built = _published(entry)
    if not isinstance(built, kind):
        raise ModelValidationError(
            f"{entry!r} was published as a {kind.__name__}, but it is a "
            f"{type(built).__name__}. Each plane takes its own kind."
        )
    return built


def _require_resolvable(tree: Disclosure, ref: str, kind: str) -> None:
    """Refuse a published entry naming a node that does not exist."""

    try:
        tree.find(ref)
    except NodeNotFoundError as failure:
        raise ModelValidationError(
            f"The {kind} for {ref!r} names a node that does not exist "
            f"({failure.reason.value}). A published entry is resolved when the "
            "server is built so that it fails on the way up rather than in "
            "front of whoever reached for it."
        ) from None


__all__ = ["Surface", "published_name", "translated"]
