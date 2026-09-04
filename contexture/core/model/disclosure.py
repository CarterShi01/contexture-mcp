"""The multi-headed tree, disclosed lazily.

Below this module sits `ContextNode.compile()`, which every node answers for
itself. Above it sit the four system entry points `core.model.system_api` puts
in front of an agent. This module is the whole of what joins them, and the whole
of the navigation model.

**One call shows one level of siblings.** Choosing between siblings requires
seeing all of them — what cannot be seen together is guessed between rather than
chosen between — so a level always arrives whole. It does not follow that every
level should arrive at once, and until v0.2.0 this module drew that conclusion:
`skeleton()` walked the entire forest, which is affordable at the six roles the
argument was traced against and is 440,000 tokens at eleven thousand. `discover`
now answers with the roots, and each `open` answers with one more level of
sub-roles alongside everything else that role holds. The role axis is as lazy as
every other axis; see ADR 007.

**A reference is a path.**::

    kubernetes-incident-responder
    kubernetes-incident-responder/diagnose-crash-loop-backoff

No kind prefix, no second separator. The members of one role are uniquely named,
so resolution can simply look rather than be told where to look, and the address
reads like something a person could have written. An agent never has to write
one: every card carries the reference that opens it, because `card` cannot be
called without a view to take one from.

**Three objects, three questions.** `ControllerManager` owns what exists; `Index`
owns the facts derived from it — every address, every binding, what only the
whole forest can answer; and this owns *how much of that one call gives back*.
It is the disclosure strategy and nothing else: it holds an `Index`, and reaches
for the address and schema it cannot work out for a node from there. Registration
is additive and mutable, an index is compiled and frozen, and this is the view
served over one — the split is why a skill in the first registered root may name
a capability in the second. See ADR 014 and ADR 016.

**Nothing here is remembered.** Every method is a pure function of its argument,
which is what keeps traversal legal on a protocol that, since the 2026-07-28
revision, forbids a server to vary its surface per connection or as a consequence
of an earlier call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..constants import SEPARATOR
from ..errors import ModelValidationError
from ..types import CompiledContext, JsonObject
from .binding import Binding, PlainBinding
from .index import Index
from .manager import register_root
from .node import CompileLevel, ContextNode, group_cards
from .tool import Tool

__all__ = ["Disclosure", "SEPARATOR", "register_root"]


@dataclass(frozen=True, slots=True)
class Disclosure:
    """A disclosure strategy over one compiled index.

    Built from the declared roots::

        view = Disclosure.of(KubernetesPlatform(), bind=...)

    or, where the application had channels to hand out before anything was
    served, from the manager that already holds them::

        view = manager.sealed(bind=...)

    `bind` is how a tool's schema and the way to run it arrive without this
    module knowing what JSON Schema is; it is handed to `Index`, which derives
    one binding per tool while it compiles. The server layer passes one backed
    by the MCP SDK; nothing here imports it.

    It satisfies `View`, which is how a node reaches the two things it cannot
    work out for itself: the address that opens it, and the schema an agent
    needs in order to call it. Both come from the index, which is the one object
    that has seen the whole forest.
    """

    #: The compiled forest this view discloses. Every question about *what is
    #: there* — an address, a binding, the whole role axis — is the index's;
    #: this object's are all of the form *how much of it does one call give
    #: back*. Consumers that want to walk the address space reach the index
    #: directly rather than through here.
    index: Index

    #: Root references held for the user-controlled Prompt plane and omitted
    #: from the model-controlled navigation plane.  The nodes remain in the
    #: same compiled Index so a Prompt opens the canonical declaration rather
    #: than a projected copy.
    prompt_roots: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for ref in self.prompt_roots:
            node = self.index.find(ref)
            if self.index.parent_of(node) is not None:
                raise ModelValidationError(
                    f"Prompt-only reference {ref!r} is not a root. "
                    "Only a complete root tree can be removed from model navigation."
                )

    def unrestricted(self) -> "Disclosure":
        """Return the same compiled forest without model-plane exclusions."""

        return self if not self.prompt_roots else Disclosure(self.index)

    def model_can_see(self, ref: str) -> bool:
        """Whether ``ref`` belongs to the model-controlled root forest."""

        root = ref.split(SEPARATOR, 1)[0]
        return root not in self.prompt_roots

    @classmethod
    def of(
        cls,
        roots: Any,
        *,
        bind: Callable[[Tool], Binding] = PlainBinding,
    ) -> Disclosure:
        """Compile a manager, one root, or many, and return the sealed view."""

        return cls(index=roots if isinstance(roots, Index) else Index.of(roots, bind=bind))

    @property
    def roots(self) -> tuple[ContextNode, ...]:
        """Roots visible to model navigation, in registration order."""

        return tuple(
            root for root in self.index.roots
            if self.model_can_see(self.index.ref_of(root))
        )

    @property
    def registry(self) -> Index:
        """The compiled index this view is disclosed from.

        Named `registry` for the callers that reach past the view to the facts
        underneath it — where a handle is opened, where a node's channels were
        stamped. What it returns is the index, not the mutable manager: a view
        that could reach a registry still open to registration would be a view
        of something that can still change.
        """

        return self.index

    # ---- as a View -------------------------------------------------------

    def ref_of(self, node: ContextNode) -> str:
        """The address that opens `node`. The index knows; this asks it."""

        return self.index.ref_of(node)

    def card_of(self, node: ContextNode) -> CompiledContext:
        """One routing card for a node this view already holds."""

        return node.card(self)

    def card_for(self, ref: str) -> CompiledContext:
        """Render one capability a procedure names but does not own.

        A **card**, at ROUTE, and this is the invariant the whole reference
        overlay rests on: a card carries a kind, a sentence and a ref, and never
        the `uses` of the node it describes. So one level is rendered and that is
        the end of it, exactly as opening a role stops at its members, and a
        reference cycle is two cards pointing at each other rather than something
        to traverse.
        """

        if not self.model_can_see(ref):
            raise ModelValidationError(
                f"{ref!r} belongs to a Prompt-only root and has no model routing card."
            )
        return self.index.find(ref).card(self)

    def cards_of(self, nodes: Iterable[ContextNode]) -> CompiledContext:
        """Render one sibling set through this disclosure policy."""

        return group_cards(
            (node for node in nodes if self.model_can_see(self.index.ref_of(node))),
            self,
        )

    def cards_for(self, refs: Iterable[str]) -> list[CompiledContext]:
        """Render the structural dependency cards named by ``refs``."""

        return [self.card_for(ref) for ref in refs if self.model_can_see(ref)]

    def execution_of(self, tool: ContextNode) -> CompiledContext:
        """Disclose a Tool's callable facet only for a bound Index."""

        if not self.index.is_bound:
            return {}
        return {
            "read_only": bool(getattr(tool, "read_only", False)),
            "input_schema": self.schema_of(tool),
        }

    def schema_of(self, tool: ContextNode) -> JsonObject:
        """The input schema an agent needs in order to call `tool`."""

        return self.index.schema_of(tool)

    # ---- disclosure ------------------------------------------------------

    def skeleton(self) -> CompiledContext:
        """The roots, as cards. One level, like every other call.

        This is the top sibling set and nothing below it: a root's children
        arrive when that root is opened, which an agent must do anyway to get its
        instructions. The cost of entering this server is therefore the number
        of roots, not the size of the forest.
        """

        return self.cards_of(self.roots)

    def open(self, ref: str) -> CompiledContext:
        """Return one node's own detail, plus a card for each member it holds.

        Every kind-specific decision lives on the kind: opening a role delivers
        its members, opening a skill delivers its procedure and the cards of
        what it references, opening a tool delivers its complete card. Whole-
        graph relations such as reverse dependents belong behind an explicit
        architecture Tool; adding them here would reveal sibling branches the
        caller has not entered. Usage telemetry likewise never enters payloads.
        """

        return self.index.find(ref).compile(CompileLevel.ACTIVE, view=self)

    # ---- resolution the kernel drives ------------------------------------

    def find(self, ref: str) -> ContextNode:
        """Resolve a reference to the one node it addresses."""

        return self.index.find(ref)

    def tool(self, ref: str) -> Tool:
        """Resolve a reference that must name a tool."""

        return self.index.tool(ref)
