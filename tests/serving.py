"""Register, compile and serve — `main()`'s steps, for a test.

Not part of the framework. A business `main()` writes these lines out in full,
because every object in them is one it makes a decision about; a test makes the
same decisions the same way every time, so it says so once here.

    server = serve(Responder)                    # one root, nothing published
    server = serve(Responder, published=(DOC,))  # …and one document

`published` is a convenience this helper keeps: a test states a mixed tuple and
it is sorted into the two planes here, the way `cli` sorts a project's publish
table. The framework itself takes the two typed lists apart — `prompts=` and
`resources=` on `ContextureServer`.

`surface` and `tree` are reachable from what comes back, which is what the tests
that read `app.surface.tree` want.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from contexture.core.mcp_interface import published as _published
from contexture.core.mcp_interface.prompt import Prompt
from contexture.core.mcp_interface.resource import Resource
from contexture.core.model.disclosure import Disclosure
from contexture.core.model.index import Index
from contexture.core.model.manager import ControllerManager, register_root
from contexture.server import ContextureServer
from contexture.server.binding import TypeHintBinding
from contexture.server.surface import Surface


@dataclass(frozen=True, slots=True)
class Marked:
    """A binding whose schema says which tool it was derived for.

    For the tests asking whether a schema travelled from the binding to the
    card, rather than what a real derivation produces — those live in
    `test_binding.py` and use the SDK-backed one.
    """

    tool: Any

    @property
    def schema(self) -> dict[str, Any]:
        return {"tool": self.tool.name}

    async def call(self, arguments: Any, context: Any = None) -> Any:
        return await self.tool.invoke(**(arguments or {}))


def compiled(
    roots: Any,
    *,
    channels: Any = None,
    bind: Any = TypeHintBinding,
) -> Index:
    """Register one root or many — class or instance — and compile the index."""

    manager = ControllerManager(channels=channels)
    for root in _each(roots):
        register_root(manager, root)
    return Index.of(manager, bind=bind)


def assemble(
    roots: Any,
    *,
    published: Sequence[Any] = (),
    channels: Any = None,
    bind: Any = TypeHintBinding,
) -> Surface:
    """The surface behind a served graph, for a test that wants it without a wire."""

    prompts, resources = _split(published)
    return Surface.of(
        Disclosure(compiled(roots, channels=channels, bind=bind)),
        prompts=prompts,
        resources=resources,
    )


def serve(
    roots: Any,
    *,
    published: Sequence[Any] = (),
    channels: Any = None,
    bind: Any = TypeHintBinding,
    name: str = "test",
    **kwargs: Any,
) -> ContextureServer:
    """The same, wrapped in the server that would put it on a wire."""

    prompts, resources = _split(published)
    return ContextureServer(
        compiled(roots, channels=channels, bind=bind),
        name=name,
        prompts=prompts,
        resources=resources,
        **kwargs,
    )


def _split(published: Sequence[Any]) -> tuple[list[Any], list[Any]]:
    """One mixed tuple into the two typed lists the framework takes apart."""

    entries = [_published(entry) for entry in published]
    return (
        [entry for entry in entries if isinstance(entry, Prompt)],
        [entry for entry in entries if isinstance(entry, Resource)],
    )


def _each(given: Any) -> tuple[Any, ...]:
    """One root or many, told apart without making a caller say which."""

    if isinstance(given, type) or hasattr(given, "kind"):
        return (given,)
    return tuple(given)
