"""Everything true about a registered forest that no node can work out alone.

`ControllerManager` owns what exists. This owns the **facts derived from it**:
where each controller hangs, what sits beside it, what one of them costs to
call, and how a person's half-typed address is ranked. `Disclosure` owns a
third thing again — how much of that a single call answers with.

The three are not one object in three phases; they answer three different
questions:

    ControllerManager   what exists, what it can reach, when it opens
    Index               the facts about it: address, kind, parentage, binding
    Disclosure          how much of that one call gives back

**Building an index is a compilation.** The declared forest is the source; a
symbol table (every address), a type check (the two things only the whole
forest can answer) and a code-generation pass (one `Binding` per tool) run
once, here; and what comes out is frozen. That is why every question that needs
the *whole* forest is asked at this moment and never again.

**It does not hold the registry it was built from.** It walks it once and lets
go, which is what makes it a snapshot rather than a view. Registering another
root afterwards produces a different index the next time one is built, and
cannot change the one already being served — a protocol that forbids a server
to vary its surface as a consequence of an earlier call needs that to be
structural rather than a convention nobody has broken yet.

**One boundary, stated flatly.** This answers questions about *addresses and
entities*. Anything of the form "how much should this call give back, and
shaped how" belongs to `Disclosure` and never here.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Iterator

from ..constants import SEPARATOR
from ..errors import LookupFailure, ModelValidationError, NodeNotFoundError
from .binding import Binding, PlainBinding
from .channels import provisioned as _provisioned
from .manager import ControllerManager, register_root
from .node import ContextNode
from .role import Role
from .skill import Skill
from .tool import Tool


@dataclass(frozen=True, slots=True)
class Index:
    """One compiled forest: every address, and what sits at it."""

    #: Everything registered at the top, in registration order.
    roots: tuple[ContextNode, ...]

    #: The deployment handle every controller was stamped with, captured so the
    #: served path can open it without holding the registry. Whatever it is, the
    #: framework never inspects it — it only opens it, and only if it has a
    #: lifecycle. See `provisioned`.
    channels: Any = None

    #: Address to controller. The one table everything else here reads.
    _by_ref: dict[str, ContextNode] = field(default_factory=dict, repr=False)

    #: Controller to the controller holding it, or None for a root. Keyed by
    #: `id()`, and the node itself is held in `_by_ref`, so an id cannot be
    #: reused while it is still a key here.
    _parent_of: dict[int, ContextNode | None] = field(
        default_factory=dict, repr=False
    )

    #: Every controller of one kind, in registration order. The flat view: what
    #: an audit, a metric or a startup check wants, without walking a forest to
    #: ask a question about a kind.
    _by_kind: dict[str, tuple[ContextNode, ...]] = field(
        default_factory=dict, repr=False
    )

    #: One binding per tool, keyed by the address that opens it. Derived here
    #: and never again — an address is stable and unique, which is exactly what
    #: a cache keyed by object identity was not.
    _bindings: dict[str, Binding] = field(default_factory=dict, repr=False)

    #: Reverse dependency edges, derived once from every node's `uses`.  Values
    #: are source refs in declaration order so an impact query never has to walk
    #: the forest or reconstruct the graph.
    _dependents: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)

    # ---- building --------------------------------------------------------

    @classmethod
    def of(
        cls,
        registry: Any,
        *,
        bind: Callable[[Tool], Binding] = PlainBinding,
    ) -> "Index":
        """Compile a registry — or one root, or many — into a frozen index.

        A root arrives as a **class** in the ordinary case, and a class is only
        ever turned into a node by a `ControllerManager`, so one is built here
        when a caller passed roots directly. That keeps a single answer to
        "when does a node come into existence" whether a caller went through
        `main()`, through a test, or through `contexture list`.

        Registration has already assigned every address and refused a cycle,
        which is what makes the walk below safe. This re-walks the roots into
        tables of its own rather than borrowing the registry's, so that letting
        go of the registry costs nothing.
        """

        manager = _as_registry(registry)
        if not manager.roots:
            raise ModelValidationError("A context tree needs at least one root role.")

        index = cls(roots=manager.roots, channels=manager.channels)
        for root in manager.roots:
            index._absorb(root, (root.name,), parent=None)

        # Order matters. A name carrying the separator would make every address
        # below it a lie, so it is refused before anything is resolved against
        # one; `uses` is then checked against addresses known to be sound; and
        # only a forest that survived both is worth deriving bindings for.
        index._reject_ambiguous_names()
        index._reject_unresolvable_uses()
        index._derive_dependents()
        index._derive_bindings(bind)
        return index

    def _absorb(
        self,
        node: ContextNode,
        path: tuple[str, ...],
        *,
        parent: ContextNode | None,
    ) -> None:
        """Record one node and everything it holds, depth-first."""

        self._by_ref[SEPARATOR.join(path)] = node
        self._parent_of[id(node)] = parent
        self._by_kind[node.kind] = (*self._by_kind.get(node.kind, ()), node)
        if isinstance(node, Role):
            for member in node.members():
                self._absorb(member, path + (member.name,), parent=node)

    def _derive_bindings(self, bind: Callable[[Tool], Binding]) -> None:
        for node in self.of_kind(Tool.kind):
            assert isinstance(node, Tool)  # `of_kind` is keyed by `Tool.kind`
            self._bindings[self.ref_of(node)] = bind(node)

    def _derive_dependents(self) -> None:
        """Compile the reverse of the declared dependency overlay."""

        for source_ref, node in self.walk():
            for target_ref in node.uses:
                self._dependents[target_ref] = (
                    *self._dependents.get(target_ref, ()),
                    source_ref,
                )

    # ---- lifecycle -------------------------------------------------------

    @asynccontextmanager
    async def provisioned(self) -> AsyncIterator[Any]:
        """Open this forest's handle for exactly as long as it is serving.

        Over the handle captured at compile time, which is the same object every
        controller was stamped with, so opening it here makes every capability's
        own handle live. A handle that cannot be opened fails on the way up, in
        front of whoever started the server rather than the first caller who
        needed it.
        """

        async with _provisioned(self.channels) as opened:
            yield opened

    # ---- one node --------------------------------------------------------

    def ref_of(self, node: ContextNode) -> str:
        """The address that opens `node`, spelled.

        The node carries its position — registration is where it was told, and a
        capability that wants to report where it lives reads it there. This joins
        it, which is the whole of the division: **a node carries its position
        and never its spelling**, so changing the separator is one edit here
        rather than one per node.

        A node this index does not hold is refused rather than spelled. A card
        without a working address is worse than no card: it can be seen and not
        opened.
        """

        ref = SEPARATOR.join(node.path)
        if not node.path or self._by_ref.get(ref) is not node:
            raise ModelValidationError(
                f"{node.name!r} is not registered in this index, so nothing "
                "can say where it hangs. Compile an `Index` from the root that "
                "holds it and open it through that."
            )
        return ref

    def parent_of(self, node: ContextNode) -> ContextNode | None:
        """The controller holding `node`, or None for a root."""

        return self._parent_of.get(id(node))

    def children_of(self, node: ContextNode) -> tuple[ContextNode, ...]:
        """Everything `node` holds, one level down and in declared order."""

        if not isinstance(node, Role):
            return ()
        return tuple(node.members())

    def uses_of(self, ref: str) -> tuple[str, ...]:
        """The addresses this object declares as dependencies."""

        return self.find(ref).uses

    def dependents_of(self, ref: str) -> tuple[str, ...]:
        """The addresses of objects whose `uses` names `ref`."""

        self.find(ref)  # refuse an unknown target rather than reporting no impact
        return self._dependents.get(ref, ())

    def binding_of(self, ref: str) -> Binding:
        """How the tool at `ref` is described and run.

        Present for every registered tool, because every one of them was bound
        while this index was compiled. A ref naming anything else is a caller
        bug rather than a lookup failure — `tool()` is what turns a wrong ref
        into a sentence an agent can act on, and it runs first on every path
        that reaches here.
        """

        return self._bindings[ref]

    def schema_of(self, tool: ContextNode) -> Any:
        """The input schema an agent needs in order to call `tool`."""

        return self.binding_of(self.ref_of(tool)).schema

    # ---- resolution ------------------------------------------------------

    def find(self, ref: str) -> ContextNode:
        """Resolve a reference to the one controller it addresses.

        One dictionary lookup, and the walk that remains happens only on a miss
        — to say *where* it missed. A caller that reached for `a/b/c` is owed
        the segment that failed, what was holding it, and what that thing does
        hold: an agent reads this and tries something else, so an accurate list
        is the difference between a retry and a guess.
        """

        segments = tuple(part for part in ref.split(SEPARATOR) if part)
        if not segments:
            raise NodeNotFoundError(reason=LookupFailure.EMPTY_REF, ref=ref)
        found = self._by_ref.get(SEPARATOR.join(segments))
        if found is not None:
            return found
        raise self._diagnose(segments, ref)

    def _diagnose(self, segments: tuple[str, ...], ref: str) -> NodeNotFoundError:
        """Say which segment of a failed reference is the one that failed."""

        if segments[0] not in self._by_ref:
            return NodeNotFoundError(
                reason=LookupFailure.NO_SUCH_ROOT,
                ref=ref,
                segment=segments[0],
                scope=segments[0],
                known=sorted(root.name for root in self.roots),
            )
        for depth in range(2, len(segments) + 1):
            if SEPARATOR.join(segments[:depth]) in self._by_ref:
                continue
            held = self._by_ref[SEPARATOR.join(segments[: depth - 1])]
            if not isinstance(held, Role):
                return NodeNotFoundError(
                    reason=LookupFailure.NOT_A_CONTAINER,
                    ref=ref,
                    segment=segments[depth - 1],
                    scope=held.name,
                    kind=held.kind,
                )
            return NodeNotFoundError(
                reason=LookupFailure.NO_SUCH_MEMBER,
                ref=ref,
                segment=segments[depth - 1],
                scope=held.name,
                kind=held.kind,
                known=sorted(member.name for member in held.members()),
            )
        return NodeNotFoundError(  # pragma: no cover - the loop above is total
            reason=LookupFailure.NO_SUCH_MEMBER, ref=ref, segment=segments[-1]
        )

    def tool(self, ref: str) -> Tool:
        """Resolve a reference that must name a tool."""

        node = self.find(ref)
        if not isinstance(node, Tool):
            raise NodeNotFoundError(
                reason=LookupFailure.WRONG_KIND,
                ref=ref,
                kind=node.kind,
                wanted=Tool.kind,
            )
        return node

    # ---- walking ---------------------------------------------------------

    def of_kind(self, kind: str) -> tuple[ContextNode, ...]:
        """Every controller of one kind, in registration order."""

        return self._by_kind.get(kind, ())

    def walk(self) -> Iterator[tuple[str, ContextNode]]:
        """Every `(ref, controller)` pair, depth-first, in declared order.

        **This follows containment and never `uses`.** Not an oversight to be
        improved on later: the reference overlay may legally contain cycles
        (ADR 008), and this walk is on the startup path, so an enumerator that
        followed references would hang the server before it ever served
        anything. Containment is a forest by construction and cannot.
        """

        yield from self._by_ref.items()

    #: The role-axis walkers answer "what roles are there"; `walk` answers "what
    #: is addressable", which is what `uses` validation checks against and what
    #: argument completion offers a person. Named for the second audience where
    #: that is the one reading.
    nodes_with_refs = walk

    def skills(self) -> Iterator[tuple[str, Skill]]:
        """Every registered procedure, with the reference that opens it."""

        for node in self.of_kind(Skill.kind):
            assert isinstance(node, Skill)  # `of_kind` is keyed by `Skill.kind`
            yield self.ref_of(node), node

    def roles_with_refs(self) -> Iterator[tuple[str, Role]]:
        """The whole role axis depth-first, with each role's reference.

        For callers that legitimately want the entire forest at once —
        `contexture list` printing a tree to a terminal, or a test enumerating
        what a server can be asked. It is *not* what an agent is given.
        """

        for ref, node in self.walk():
            if isinstance(node, Role):
                yield ref, node

    def roles_by_level(self) -> Iterator[tuple[str, Role]]:
        """The role axis breadth-first: every root, then every child.

        Ordering matters wherever the walk is going to be *cut off*. A
        depth-first roster truncated to a budget spends it on one deep spine and
        never mentions the root's siblings, which is the worst possible answer
        for something whose only job is routing.
        """

        queue: list[tuple[str, Role]] = [
            (root.name, root) for root in self.roots if isinstance(root, Role)
        ]
        while queue:
            ref, role = queue.pop(0)
            yield ref, role
            queue.extend(
                (f"{ref}{SEPARATOR}{child.name}", child) for child in role.branches()
            )

    def matching_refs(self, value: str, *, limit: int) -> tuple[tuple[str, ...], int]:
        """Rank addressable refs against what a person has typed so far.

        Returns `(matches, total)` — the first `limit` in relevance order, and
        how many matched altogether.

        **This cuts by relevance, where the roster cuts in whole sibling groups,
        and the difference is deliberate.** The roster's rule exists because a
        model shown three of a role's eight sub-roles takes three for the whole
        choice. This is read by a person watching a menu narrow as they type,
        who will simply keep typing. Same protocol, different consumer, so the
        same cut would be the wrong one.
        """

        wanted = value.strip().lower()
        scored: list[tuple[int, int, str]] = []
        for ref, _ in self.walk():
            lowered = ref.lower()
            if not wanted:
                rank = 0
            elif lowered.startswith(wanted):
                rank = 0
            elif lowered.rsplit(SEPARATOR, 1)[-1].startswith(wanted):
                rank = 1
            elif any(part.startswith(wanted) for part in lowered.split(SEPARATOR)):
                rank = 2
            elif wanted in lowered:
                rank = 3
            else:
                continue
            scored.append((rank, len(ref), ref))
        scored.sort()
        return tuple(ref for _, _, ref in scored[:limit]), len(scored)

    def signpost(self, ref: str) -> tuple[tuple[str, int], ...]:
        """Name the path above a node, with how many sub-roles each level holds.

        `(ancestor_ref, sub_role_count)` per level, nearest root first.

        Reaching a node directly skips the calls that would have shown what sat
        beside it on the way down, and ADR 004's rule is that a choice made
        among a subset of the alternatives is a guess rather than a choice. This
        is what keeps that rule true at an entrance that has no way down: it
        reports **that** there are siblings and how many, and never their names
        — the same shape as the roster's truncation line, which names the call
        that restores what was cut instead of spending the budget on it.

        Costed before it was written. Replaying `open` at each level runs to
        +206% of the payload it decorates on the demo and +724% on a synthetic
        forest, which re-buys every level the direct hit just saved. This runs
        to +13%, and grows with **depth** rather than breadth: eight siblings
        and three siblings render the same line.
        """

        segments = [segment for segment in ref.split(SEPARATOR) if segment]
        levels: list[tuple[str, int]] = []
        for depth in range(1, len(segments)):
            ancestor = SEPARATOR.join(segments[:depth])
            levels.append((ancestor, len(self.find(ancestor).branches())))
        return tuple(levels)

    def crossings(self) -> Iterator[tuple[str, str, str]]:
        """Every reference that leaves the root branch it was made from.

        `(source_ref, target_ref, target_root)`. Nothing refuses these — a person
        composing two branches is an authorised act, and ADR 004's rule is about
        a model guessing. But a responsibility boundary that gets crossed
        silently is one nobody reviews, and the reason orchestration is a
        declared object here rather than prose in a host-side template is
        precisely that a crossing can be listed, tested and linted.
        """

        for ref, node in self.walk():
            home = ref.split(SEPARATOR, 1)[0]
            for target in node.uses:
                root = target.split(SEPARATOR, 1)[0]
                if root != home:
                    yield ref, target, root

    # ---- what only the whole forest can answer ---------------------------

    def _reject_ambiguous_names(self) -> None:
        """Refuse a name that would produce a reference nobody can resolve.

        A reference is a path and a node's name is one segment of it, so a name
        containing the separator silently splits into two. The card is still
        built — `card` takes the address the view gives it — and the ref on it
        addresses nothing. That is the exact failure the view's signature exists
        to prevent, arriving from the other side: not a card without a ref, but
        a ref without a node.

        Checked here rather than in registration because the separator is this
        module's decision. A node has no way to know what character will be used
        to join it to its neighbours; whatever does the joining does.
        """

        for node in self._by_ref.values():
            if SEPARATOR in node.name:
                raise ModelValidationError(
                    f"{node.kind} name {node.name!r} contains {SEPARATOR!r}, "
                    "which separates one segment of a reference from the next. "
                    "A card for it would carry a ref that resolves to nothing."
                )

    def _reject_unresolvable_uses(self) -> None:
        """Refuse an object that names something the forest cannot answer for.

        Checked while compiling, beside the separator check, because
        registration is additive: an object in the first registered root may
        legitimately name an object in a root that has not been registered
        yet, so this is the earliest moment the binding *can* be checked.

        Reference cycles are deliberately not checked. `diagnose -> remediate ->
        diagnose` is a real workflow shape, and it costs nothing here: cards
        render at ROUTE, so a cycle is two cards naming each other. See ADR 008.
        """

        for ref, node in self.walk():
            for target in node.uses:
                if target == ref:
                    raise ModelValidationError(
                        f"{node.kind.title()} {ref!r} names itself in `uses`. "
                        "An object does not depend on itself."
                    )
                try:
                    self.find(target)
                except NodeNotFoundError as failure:
                    raise ModelValidationError(
                        f"{node.kind.title()} {ref!r} names {target!r} in `uses`, which "
                        f"resolves to nothing ({failure.reason.value}). A "
                        "dependency that does not exist is a broken declaration, "
                        "and the reference is checked here so that "
                        "it fails on the way up rather than in front of a user."
                    ) from None

    # ---- size ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_ref)

    def __contains__(self, ref: object) -> bool:
        return isinstance(ref, str) and ref in self._by_ref


def _as_registry(given: Any) -> ControllerManager:
    """A manager as it is, or a fresh one holding whatever roots were passed."""

    if isinstance(given, ControllerManager):
        return given
    roots = (given,) if _is_one_root(given) else tuple(given)
    registry = ControllerManager()
    for root in roots:
        register_root(registry, root)
    return registry


def _is_one_root(given: Any) -> bool:
    """Whether this is a single root rather than a collection of them."""

    kinds = (Role, Skill, Tool)
    return isinstance(given, kinds) or (
        isinstance(given, type) and issubclass(given, kinds)
    )


__all__ = ["Index"]
