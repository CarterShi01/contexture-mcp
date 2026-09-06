"""Immutable path-selected capability surfaces over one compiled Index.

A surface selection names one or more exact Contexture refs, or direct-child
patterns ending in ``/*``. Every resolved ref becomes a surface root and
brings its complete containment subtree. A selected descendant is therefore
disclosed directly; its unselected ancestors and siblings do not become part
of the surface merely because they occur in its canonical address.

The value is transport-neutral. HTTP headers and authenticated caller policy
are adapters in :mod:`contexture.server`.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Iterator

from ..constants import SEPARATOR
from ..errors import ContextureError, NodeNotFoundError
from .node import ContextNode

if TYPE_CHECKING:  # pragma: no cover - imports used only by type checkers
    from .index import Index


class SurfaceSelectionError(ContextureError, ValueError):
    """A requested path selection cannot be applied to this application."""


class OutsideSelectionError(ContextureError):
    """A reference is outside the current selected capability surface."""

    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(
            f"Reference {ref!r} is outside this request's selected surface. "
            "Call contexture_discover and use a ref from its result."
        )


def _is_descendant_or_self(ref: str, ancestor: str) -> bool:
    return ref == ancestor or ref.startswith(f"{ancestor}{SEPARATOR}")


def _canonical(refs: Iterable[str]) -> frozenset[str]:
    """Return the minimal antichain representing the same selected forest."""

    ordered = sorted(set(refs), key=lambda ref: (ref.count(SEPARATOR), ref))
    kept: list[str] = []
    for ref in ordered:
        if not any(_is_descendant_or_self(ref, ancestor) for ancestor in kept):
            kept.append(ref)
    return frozenset(kept)


def _validate_selector(selector: str) -> None:
    segments = selector.split(SEPARATOR)
    if not selector or any(not segment for segment in segments):
        raise SurfaceSelectionError(
            f"Invalid Contexture selector {selector!r}: refs contain no empty segments."
        )
    wildcard_segments = [index for index, segment in enumerate(segments) if "*" in segment]
    if not wildcard_segments:
        return
    if wildcard_segments != [len(segments) - 1] or segments[-1] != "*":
        raise SurfaceSelectionError(
            f"Invalid Contexture selector {selector!r}: '*' is allowed only as the "
            "complete final segment, for example 'team/*'."
        )


@dataclass(frozen=True, slots=True)
class SurfaceSelection:
    """All capabilities, or an immutable allowlist of complete subtrees.

    ``names is None`` is the compatibility value and means the whole Index.
    Before :meth:`resolve`, concrete names may include direct-child patterns;
    a resolved value contains only exact refs and is reduced to an antichain.
    Declaration order always comes from the Index, never caller input order.

    ``names`` is retained as the field name so code written against the 0.11
    ``RootSelection`` value continues to read its selected refs unchanged.
    """

    names: frozenset[str] | None = None

    @classmethod
    def all(cls) -> "SurfaceSelection":
        return cls()

    @classmethod
    def only(cls, names: Iterable[str] | str) -> "SurfaceSelection":
        values = (names,) if isinstance(names, str) else names
        normalized = frozenset(str(name).strip() for name in values)
        if not normalized or "" in normalized:
            raise SurfaceSelectionError(
                "A surface selection must name at least one ref or direct-child pattern."
            )
        for selector in sorted(normalized):
            _validate_selector(selector)
        return cls(normalized)

    @property
    def selectors(self) -> frozenset[str] | None:
        """The selectors represented by this value (exact refs after resolution)."""

        return self.names

    def resolve(self, index: "Index") -> "SurfaceSelection":
        """Expand patterns, validate exact refs, and canonicalize surface roots."""

        if self.names is None:
            return self

        refs = tuple(ref for ref, _ in index.walk())
        resolved: set[str] = set()
        unknown: list[str] = []
        for selector in sorted(self.names):
            if selector == "*":
                matches = tuple(ref for ref in refs if SEPARATOR not in ref)
            elif selector.endswith(f"{SEPARATOR}*"):
                parent = selector[: -len(f"{SEPARATOR}*")]
                prefix = f"{parent}{SEPARATOR}"
                matches = tuple(
                    ref
                    for ref in refs
                    if ref.startswith(prefix)
                    and SEPARATOR not in ref[len(prefix) :]
                )
            else:
                try:
                    index.find(selector)
                except NodeNotFoundError:
                    matches = ()
                else:
                    matches = (selector,)
            if not matches:
                unknown.append(selector)
            resolved.update(matches)

        if unknown:
            # Do not enumerate the application's other refs: selection is a
            # disclosure boundary and its diagnostics must not defeat it.
            raise SurfaceSelectionError(
                "Unknown or empty Contexture selector: "
                + ", ".join(repr(selector) for selector in unknown)
            )
        return SurfaceSelection(_canonical(resolved))

    def contains_ref(self, ref: str) -> bool:
        """Whether ``ref`` is inside one selected complete subtree."""

        if self.names is None:
            return True
        return any(_is_descendant_or_self(ref, anchor) for anchor in self.names)

    def require_ref(self, ref: str) -> None:
        if not self.contains_ref(ref):
            raise OutsideSelectionError(ref)

    def roots_in(self, index: "Index") -> tuple[ContextNode, ...]:
        """Return selected surface roots in canonical declaration order."""

        if self.names is None:
            return index.roots
        resolved = self.resolve(index)
        assert resolved.names is not None
        return tuple(node for ref, node in index.walk() if ref in resolved.names)

    def intersect(self, other: "SurfaceSelection") -> "SurfaceSelection":
        """Return the monotonic intersection of two resolved subtree surfaces."""

        if self.names is None:
            return other
        if other.names is None:
            return self
        if any("*" in selector for selector in (*self.names, *other.names)):
            raise SurfaceSelectionError(
                "Resolve wildcard selectors against an Index before intersecting them."
            )

        overlaps: set[str] = set()
        for left in self.names:
            for right in other.names:
                if _is_descendant_or_self(left, right):
                    overlaps.add(left)
                elif _is_descendant_or_self(right, left):
                    overlaps.add(right)
        if not overlaps:
            raise SurfaceSelectionError("The effective surface selection is empty.")
        return SurfaceSelection(_canonical(overlaps))


_CURRENT: ContextVar[SurfaceSelection] = ContextVar(
    "contexture_surface_selection", default=SurfaceSelection.all()
)


def current_surface_selection() -> SurfaceSelection:
    """Return the request-local selection, or the compatibility all view."""

    return _CURRENT.get()


@contextmanager
def bound_surface_selection(selection: SurfaceSelection) -> Iterator[None]:
    """Bind one resolved selection to the current request task."""

    token = _CURRENT.set(selection)
    try:
        yield
    finally:
        _CURRENT.reset(token)


@dataclass(frozen=True, slots=True)
class SelectedGraph:
    """Read-only graph facade containing only selected complete subtrees."""

    index: "Index"
    selection: SurfaceSelection

    def __post_init__(self) -> None:
        object.__setattr__(self, "selection", self.selection.resolve(self.index))

    @property
    def roots(self) -> tuple[ContextNode, ...]:
        return self.selection.roots_in(self.index)

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
        ref = self.ref_of(node)
        if self.selection.names is not None and ref in self.selection.names:
            return None
        parent = self.index.parent_of(node)
        if parent is not None:
            self.selection.require_ref(self.index.ref_of(parent))
        return parent

    def children_of(self, node: ContextNode) -> tuple[ContextNode, ...]:
        self.ref_of(node)
        return tuple(
            child
            for child in self.index.children_of(node)
            if self.selection.contains_ref(self.index.ref_of(child))
        )

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
        """Rank matching refs using Index semantics, after path projection."""

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


# Compatibility names from 0.11/0.12. They intentionally alias the generalized
# values so existing applications gain path semantics without a second model.
RootSelection = SurfaceSelection
RootSelectionError = SurfaceSelectionError
RootOutsideSelectionError = OutsideSelectionError
current_root_selection = current_surface_selection
bound_root_selection = bound_surface_selection


__all__ = [
    "OutsideSelectionError",
    "RootOutsideSelectionError",
    "RootSelection",
    "RootSelectionError",
    "SelectedGraph",
    "SurfaceSelection",
    "SurfaceSelectionError",
    "bound_root_selection",
    "bound_surface_selection",
    "current_root_selection",
    "current_surface_selection",
]
