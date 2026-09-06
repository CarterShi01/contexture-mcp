"""Immutable root-level capability projections over one compiled Index.

A selection names complete root trees.  It never selects a descendant: once a
root is present, progressive disclosure below it behaves exactly as it does on
the unselected application.  The value is transport-neutral; HTTP headers and
authenticated caller policy are adapters in :mod:`contexture.server`.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Iterator

from ..constants import SEPARATOR
from ..errors import ContextureError
from .node import ContextNode

if TYPE_CHECKING:  # pragma: no cover - imports used only by type checkers
    from .index import Index


class RootSelectionError(ContextureError, ValueError):
    """A requested root selection cannot be applied to this application."""


class RootOutsideSelectionError(ContextureError):
    """A reference names a root outside the current request surface."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(
            f"Reference {ref!r} is outside this request's root surface. "
            "Call contexture_discover and use a ref from its result."
        )


@dataclass(frozen=True, slots=True)
class RootSelection:
    """All roots, or an exact immutable allowlist of complete root trees.

    ``names is None`` is the compatibility value and means every root in the
    Index.  A concrete set is deliberately unordered: discovery continues to
    use declaration order from the Index rather than caller input order.
    """

    names: frozenset[str] | None = None

    @classmethod
    def all(cls) -> "RootSelection":
        return cls()

    @classmethod
    def only(cls, names: Iterable[str] | str) -> "RootSelection":
        values = (names,) if isinstance(names, str) else names
        normalized = frozenset(str(name).strip() for name in values)
        if not normalized or "" in normalized:
            raise RootSelectionError("A root selection must name at least one root.")
        invalid = sorted(name for name in normalized if SEPARATOR in name)
        if invalid:
            raise RootSelectionError(
                "Root selection accepts root refs only, not descendant refs: "
                + ", ".join(repr(name) for name in invalid)
            )
        return cls(normalized)

    def resolve(self, index: "Index") -> "RootSelection":
        """Validate this value against one compiled Index."""

        if self.names is None:
            return self
        roots = frozenset(index.ref_of(root) for root in index.roots)
        unknown = sorted(self.names - roots)
        if unknown:
            # Do not enumerate the application's other roots in a request
            # error: the projection itself is meant to keep them undisclosed.
            raise RootSelectionError(
                "Unknown Contexture root selection: "
                + ", ".join(repr(name) for name in unknown)
            )
        return self

    def contains_ref(self, ref: str) -> bool:
        if self.names is None:
            return True
        segments = tuple(part for part in ref.split(SEPARATOR) if part)
        # Let the Index produce its established empty-reference diagnostic.
        return not segments or segments[0] in self.names

    def require_ref(self, ref: str) -> None:
        if not self.contains_ref(ref):
            raise RootOutsideSelectionError(ref)

    def intersect(self, other: "RootSelection") -> "RootSelection":
        """Return the monotonic intersection of two capability surfaces."""

        if self.names is None:
            return other
        if other.names is None:
            return self
        names = self.names & other.names
        if not names:
            raise RootSelectionError("The effective root selection is empty.")
        return RootSelection(names)


_CURRENT: ContextVar[RootSelection] = ContextVar(
    "contexture_root_selection", default=RootSelection.all()
)


def current_root_selection() -> RootSelection:
    """Return the request-local selection, or the compatibility all-roots view."""

    return _CURRENT.get()


@contextmanager
def bound_root_selection(selection: RootSelection) -> Iterator[None]:
    """Bind one resolved selection to the current request task."""

    token = _CURRENT.set(selection)
    try:
        yield
    finally:
        _CURRENT.reset(token)


@dataclass(frozen=True, slots=True)
class SelectedGraph:
    """Read-only graph facade that cannot enumerate an excluded root."""

    index: "Index"
    selection: RootSelection

    def __post_init__(self) -> None:
        object.__setattr__(self, "selection", self.selection.resolve(self.index))

    @property
    def roots(self) -> tuple[ContextNode, ...]:
        return tuple(
            root
            for root in self.index.roots
            if self.selection.contains_ref(self.index.ref_of(root))
        )

    def walk(self) -> Iterator[tuple[str, ContextNode]]:
        for ref, node in self.index.walk():
            if self.selection.contains_ref(ref):
                yield ref, node

    nodes_with_refs = walk

    def find(self, ref: str) -> ContextNode:
        self.selection.require_ref(ref)
        return self.index.find(ref)

    def ref_of(self, node: ContextNode) -> str:
        ref = self.index.ref_of(node)
        self.selection.require_ref(ref)
        return ref

    def parent_of(self, node: ContextNode) -> ContextNode | None:
        self.ref_of(node)
        return self.index.parent_of(node)

    def children_of(self, node: ContextNode) -> tuple[ContextNode, ...]:
        self.ref_of(node)
        return self.index.children_of(node)

    def uses_of(self, ref: str) -> tuple[str, ...]:
        self.selection.require_ref(ref)
        return tuple(
            target
            for target in self.index.uses_of(ref)
            if self.selection.contains_ref(target)
        )

    def dependents_of(self, ref: str) -> tuple[str, ...]:
        self.selection.require_ref(ref)
        return tuple(
            source
            for source in self.index.dependents_of(ref)
            if self.selection.contains_ref(source)
        )

    def matching_refs(self, value: str, *, limit: int) -> tuple[tuple[str, ...], int]:
        """Rank matching refs using Index semantics, after root projection."""

        wanted = value.strip().lower()
        scored: list[tuple[int, int, str]] = []
        for ref, _ in self.walk():
            lowered = ref.lower()
            if not wanted or lowered.startswith(wanted):
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


__all__ = [
    "RootOutsideSelectionError",
    "RootSelection",
    "RootSelectionError",
    "SelectedGraph",
    "bound_root_selection",
    "current_root_selection",
]
