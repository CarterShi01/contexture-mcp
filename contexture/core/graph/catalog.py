"""Pure bounded ROUTE-level catalog material, not the legacy Node Catalog.

Authority and snapshot lifetime belong to application adapters. This module
never compiles, stores graphs, emits global coordinates, or executes bindings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from .canonical import canonical_digest, canonical_json
from .errors import GraphError
from .index import GraphIndex


@dataclass(frozen=True, slots=True)
class CatalogMaterial:
    nodes: tuple[bytes, ...]
    containment: tuple[bytes, ...]
    uses: tuple[bytes, ...]

    @property
    def size(self) -> int:
        return sum(len(b) for stream in (self.nodes, self.containment, self.uses) for b in stream)

    def digest(self, descriptor: dict[str, Any]) -> str:
        return canonical_digest({"view_descriptor": descriptor, "catalog_payload": {
            name: [json.loads(b) for b in stream] for name, stream in (
                ("nodes", self.nodes), ("containment", self.containment), ("uses", self.uses))}},
            "contexture.view/v1")


def metadata_catalog(index: GraphIndex, visible: frozenset[str], *,
                     kinds: frozenset[str], max_items: int, max_bytes: int,
                     card: Callable[[str], dict[str, Any]] | None = None) -> CatalogMaterial:
    """Stable preorder nodes and ordered relations with BOTH endpoints visible.

    Projected roots are explicit; no hidden parent/path/depth/raw ordinal or
    relation count is exposed. Members/uses are independent complete streams,
    never unbounded fields inside a node card. Filters attenuate all streams.
    """
    selected = frozenset(n for n in index.preorder
                         if n in visible and index.get(n).kind in kinds)
    streams: list[list[bytes]] = [[], [], []]
    count = size = 0

    def emit(stream: int, item: dict[str, Any]) -> None:
        nonlocal count, size
        encoded = canonical_json(item)
        count, size = count + 1, size + len(encoded)
        if count > max_items or size > max_bytes:
            raise GraphError("too_large", "Catalog projection exceeds material budget")
        streams[stream].append(encoded)

    for identity in index.preorder:
        if identity not in selected:
            continue
        node = index.get(identity)
        value = ({"kind": node.kind, "name": node.name, "description": node.description}
                 if card is None else card(identity))
        emit(0, dict(value, node_id=identity,
                     projected_root=index.locations[identity].parent_id not in selected))
        for slot, _, target in node.members.placements() if node.members else ():
            if target in selected:
                emit(1, {"source_id": identity, "slot": slot, "target_id": target})
        for target in node.uses:
            if target in selected:
                emit(2, {"source_id": identity, "target_id": target})
    return CatalogMaterial(*(tuple(stream) for stream in streams))