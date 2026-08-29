"""The common progressive-disclosure lifecycle."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Iterable, Iterator, Protocol

from ..constants import SEPARATOR
from ..errors import ModelValidationError
from ..types import CompiledContext, JsonObject


class CompileLevel(str, Enum):
    """The two disclosure levels shared by all context nodes.

    Two, and there is no third, because an agent is only ever in one of two
    states with respect to a node: it has not chosen it yet, or it has. ROUTE
    serves the first and must stay cheap enough that a whole sibling set can be
    shown at once — a choice made among a subset of the alternatives is a
    guess. ACTIVE serves the second and can be as expensive as the work
    requires, because by then the agent has committed and nothing is being paid
    for speculatively.

    A middle level would have to answer "what does a node say to an agent that
    is halfway through choosing it", and there is no such moment.
    """

    ROUTE = "route"
    ACTIVE = "active"


class View(Protocol):
    """What a node asks for while it is compiling itself.

    A node knows what it is and what it holds. It does not know how an address
    is spelled, which of its neighbours are being rendered, or what JSON Schema
    looks like — it is constructed long before it is registered and knows
    nothing about the forest it will hang in. So it asks.

    This is the seam ADR 014 put here in place of the type switch the view used
    to carry. The view asked each node what kind it was and rendered it on the
    node's behalf, which meant a fifth kind of node would have been five edits
    in a file that owns none of them. Now each node renders itself and asks only
    for the two things the view alone can answer: an address, and a schema — and
    it sources both from the `Index` it discloses.

    `Disclosure` is the implementation that answers from a whole compiled
    forest. `_Alone` answers for a node nobody has registered.
    """

    def ref_of(self, node: ContextNode) -> str:
        """The address that opens `node`."""

    def card_of(self, node: ContextNode) -> CompiledContext:
        """One routing card for a node this view already holds."""

    def card_for(self, ref: str) -> CompiledContext:
        """One routing card for a node named by address rather than held."""

    def schema_of(self, tool: ContextNode) -> JsonObject:
        """The input schema an agent needs in order to call `tool`."""


class CompiledGraph(Protocol):
    """Read-only structural facts available to a compiled object.

    This is intentionally smaller than the concrete Index.  A Tool such as an
    architecture query may inspect the graph it belongs to without importing
    the compiler or receiving a second registry through application Channels.
    """

    @property
    def roots(self) -> tuple[ContextNode, ...]: ...

    def walk(self) -> Iterator[tuple[str, ContextNode]]: ...

    def find(self, ref: str) -> ContextNode: ...

    def ref_of(self, node: ContextNode) -> str: ...

    def parent_of(self, node: ContextNode) -> ContextNode | None: ...

    def children_of(self, node: ContextNode) -> tuple[ContextNode, ...]: ...

    def uses_of(self, ref: str) -> tuple[str, ...]: ...

    def dependents_of(self, ref: str) -> tuple[str, ...]: ...


@dataclass(slots=True, kw_only=True)
class ContextNode(ABC):
    """A node that can be progressively disclosed to an LLM context.

    The base class deliberately owns only the stable common contract:
    a machine-facing name, a routing description, and a compile lifecycle.
    Concrete node types decide what their active representation contains.
    """

    #: The machine-facing address. It is the last segment of the reference
    #: that opens this node, so it is chosen for uniqueness within its parent,
    #: not for readability.
    name: str

    #: The one sentence a model reads while deciding whether to open this node.
    #: It answers "should I go here", never "what will I find inside" — the
    #: inside is what opening delivers, and describing it twice is how the two
    #: copies start disagreeing.
    description: str

    #: Addresses of other Contexture objects this object depends on but does not
    #: contain.  Containment answers ownership and navigation; `uses` answers
    #: dependency and impact.  It is shared by Role, Skill and Tool so the same
    #: object graph describes both business execution and system operation.
    uses: tuple[str, ...] = ()

    #: The segments that reach this node, or `()` until a `ControllerManager`
    #: has registered it. A node still cannot *work out* where it hangs — it is
    #: told, once, by the one object that holds the whole graph.
    #:
    #: Segments and never a joined string: a tuple carries the position without
    #: committing to the spelling, and the spelling is the tree's to choose.
    path: tuple[str, ...] = field(default=(), compare=False, repr=False)

    #: The application's handle on whatever lives outside this process — a
    #: gateway session, a connection pool, a dataclass holding several.
    #:
    #: `Any` because the framework must never learn what is in it. Stamped by
    #: the manager at registration rather than passed to `__init__`, because a
    #: declared member is built by the declaration machinery and the
    #: application never gets to call its constructor.
    #:
    #: `None` is the ordinary case: a capability that reaches nothing outside
    #: this process needs no handle.
    channels: Any = field(default=None, compare=False, repr=False)

    kind: ClassVar[str] = "context_node"

    #: Which bucket this kind occupies in a rendered sibling set.
    #:
    #: Three buckets and no more, because the three kinds are closed: a payload
    #: carrying `roles`, `skills` and `tools` is three named fields in Go and in
    #: TypeScript, where an open map of kinds would be neither checkable nor
    #: translatable. Adding a fourth kind is a breaking change to the framework,
    #: which is the honest price and the reason this is a ClassVar rather than
    #: something a subclass is invited to invent.
    group: ClassVar[str] = "nodes"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ModelValidationError("Context node name must not be empty.")
        if not self.description.strip():
            raise ModelValidationError(
                f"Context node {self.name!r} must have a routing description."
            )
        self.uses = tuple(self.uses)
        for ref in self.uses:
            if not isinstance(ref, str) or not ref.strip():
                raise ModelValidationError(
                    f"{self.kind.title()} {self.name!r} names an empty reference in `uses`."
                )
        if len(set(self.uses)) != len(self.uses):
            raise ModelValidationError(
                f"{self.kind.title()} {self.name!r} names the same reference twice in "
                "`uses`; one dependency must have one edge."
            )

    def compile(
        self,
        level: CompileLevel | str = CompileLevel.ROUTE,
        *,
        view: View | None = None,
    ) -> CompiledContext:
        """Compile the node into the requested disclosure surface.

        `view` is how the node reaches what it cannot work out for itself. It
        is optional so that a node can still be compiled on its own — by a
        test, or by `contexture list` — and what fills in then answers from the
        node's own `path`; see `_Alone`.
        """

        normalized = CompileLevel(level)
        if normalized is CompileLevel.ROUTE:
            return self._compile_route()
        return self._compile_active(view if view is not None else _ALONE)

    def card(self, view: View) -> CompiledContext:
        """Render one routing card: what this node is, and how to open it.

        Taking a view rather than a bare reference is what makes the card
        impossible to build wrong: a card that can be seen can always be
        opened, because the address on it came from the same object that would
        resolve it.

        **This is the only place a `description` is produced.** Every active
        payload starts from a card and adds to it, so the rule that a
        description answers "should I go here" and never "what is inside" has
        one place to be true rather than three renderers to agree.
        """

        return {**self._compile_route(), "ref": view.ref_of(self)}

    def branches(self) -> tuple[ContextNode, ...]:
        """The sub-roles below this node: the choices a session picks between.

        Empty for everything but a `Role`. A caller that needs to say *how many
        ways there are on from here* — a signpost, a breadth-first roster —
        asks this rather than testing what kind of node it is holding.
        """

        return ()

    def members(self) -> Iterator[ContextNode]:
        """Everything this node holds, one level down and in declared order.

        Empty for everything but a `Role`. A leaf holds nothing, and saying so
        here is what lets a walk over the forest stay a walk rather than a
        chain of kind tests.
        """

        yield from ()

    def _compile_route(self) -> CompiledContext:
        """Return the minimal surface that is safe for broad routing."""

        return {
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
        }

    @abstractmethod
    def _compile_active(self, view: View) -> CompiledContext:
        """Return the detailed surface for an explicitly activated node."""

        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _Alone:
    """The view a node gets when nobody supplied one.

    `Role(...).compile("active")` has to answer something, and a node that has
    never been registered still knows its own name and what it holds. So this
    answers from `path` where there is one, and from the node's own name where
    there is not — a standalone node reads as its own root, which is exactly
    what it becomes the moment somebody registers it.

    Two things it cannot invent, and both fail in the direction that says so.
    A schema needs a JSON Schema library `core` does not import, so a tool card
    built this way carries an empty one. A reference needs a forest to resolve
    against, so `card_for` refuses rather than returning a card that addresses
    nothing. Both are why this is the fallback for a test and for `contexture
    list`, and never what a served payload is built from.
    """

    def ref_of(self, node: ContextNode) -> str:
        return SEPARATOR.join(node.path) if node.path else node.name

    def card_of(self, node: ContextNode) -> CompiledContext:
        return node.card(self)

    def card_for(self, ref: str) -> CompiledContext:
        raise ModelValidationError(
            f"Nothing here can resolve {ref!r}: this node is being compiled on "
            "its own, outside any forest. Build a `Disclosure` from the root "
            "that holds it and open it through that."
        )

    def schema_of(self, tool: ContextNode) -> JsonObject:
        return {}


#: One instance is enough: it holds nothing and answers from its argument.
_ALONE = _Alone()


def group_cards(
    nodes: Iterable[ContextNode],
    view: View,
) -> CompiledContext:
    """Render one sibling set, grouped by kind.

    The **one** payload shape in this package: what a `discover` call answers
    with and what opening a role puts under it are the same three keys, because
    they are the same question asked at two depths. One shape is one golden
    fixture per depth instead of two, which is what keeps three implementations
    saying the same thing.

    The three keys are always present, empty ones included. A role that holds
    no tools says so with `[]` rather than by omitting the key, so a consumer
    reads one shape whatever it opened.
    """

    grouped: CompiledContext = {"roles": [], "skills": [], "tools": []}
    for node in nodes:
        grouped[node.group].append(node.card(view))
    return grouped


__all__ = ["CompileLevel", "ContextNode", "View", "group_cards"]
