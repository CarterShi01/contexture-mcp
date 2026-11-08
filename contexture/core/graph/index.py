"""Linear, iterative forest validation and immutable snapshot-local indexes.

No complete-path cache: a chain would consume quadratic memory. Path lookup is
O(depth), path rendering O(path bytes), subtree iteration O(returned nodes).
Build is O(nodes + edges + template parts + publications + presentations), apart
from caller JSON copying and output/digest node sorting in the model.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .errors import GraphError
from .model import (
    GraphDefinition, NodeRefPart, NodeSpec, Plane, Presentation, PromptPublication,
    PublicationContracts, ResourcePublication, RoutePublication, Slot, _Value, _new,
    contract_lookup, normalize_locale,
)
from .canonical import canonical_json


def _fail(message: str, location: str = "", code: str = "schema_invalid") -> GraphError:
    return GraphError(code, message, location=location)


@dataclass(frozen=True, slots=True)
class NodeLocation:
    parent_id: str | None
    slot: Slot | None
    ordinal: int
    member_ordinal: int
    root_ordinal: int
    plane: Plane
    depth: int
    preorder: int
    subtree_end: int
    effective_mutable: bool
    false_barrier: bool


@dataclass(frozen=True, slots=True)
class TemplateReference:
    source_id: str
    part_ordinal: int


@dataclass(frozen=True, slots=True, init=False)
class GraphIndex(_Value):
    definition: GraphDefinition
    locations: Mapping[str, NodeLocation]
    preorder: tuple[str, ...]
    reverse_uses: Mapping[str, tuple[str, ...]]
    template_refs: Mapping[str, tuple[TemplateReference, ...]]
    publications: Mapping[str, tuple[str, ...]]
    presentation_refs: Mapping[str, tuple[str, ...]]
    child_names: Mapping[str | None, Mapping[str, str]]
    _catalogs: Mapping[str, Mapping[str, Presentation]]
    _model_reserved: frozenset[str]

    @classmethod
    def build(
        cls, definition: GraphDefinition, *,
        publication_contracts: PublicationContracts | None = None,
        verify_contracts: bool = True,
    ) -> GraphIndex:
        """Validate one whole forest. See GraphDefinition.from_json for evidence modes."""
        if not isinstance(definition, GraphDefinition):
            raise _fail("Expected GraphDefinition")
        nodes = definition.nodes
        if not nodes or not definition.roots or not any(r.plane == "model" for r in definition.roots):
            raise _fail("Committed forest requires at least one model root", "/roots")
        parents: dict[str, tuple[str | None, Slot | None, int, int]] = {}
        children: dict[str, tuple[tuple[Slot, int, str], ...]] = {}
        names: dict[str | None, dict[str, str]] = {None: {}}
        # Accumulate only nonempty relations; the public maps below still have
        # every node key and immutable empty tuples, preserving lookup semantics.
        reverse_uses: dict[str, list[str]] = {}
        templates: dict[str, list[TemplateReference]] = {}
        pubs: dict[str, list[str]] = {}
        presentations: dict[str, list[str]] = {}

        def target(node_id: str) -> NodeSpec:
            if node_id not in nodes:
                raise _fail("Reference target does not exist", code="reference_not_found")
            return nodes[node_id]

        def place(node_id: str, parent: str | None, slot: Slot | None,
                  ordinal: int, member_ordinal: int) -> None:
            node = target(node_id)
            if node_id in parents:
                raise _fail("Duplicate containment, alias or multiple parent")
            if node.name in names[parent]:
                raise _fail("Names must be unique across sibling slots or roots")
            parents[node_id] = (parent, slot, ordinal, member_ordinal)
            names[parent][node.name] = node_id

        for i, root in enumerate(definition.roots):
            place(root.node_id, None, None, i, i)
        for node in nodes.values():
            if node.kind == "tool":
                if definition.mode == "runtime" and node.binding is None:
                    raise _fail("Runtime Tool requires binding")
                if definition.mode == "disclosure_only" and (
                        node.binding is not None or node.input_contract is not None):
                    raise _fail("Disclosure-only Tools cannot carry execution contracts")
            if node.members is not None:
                names[node.node_id] = {}
                placements = node.members.placements()
                children[node.node_id] = placements
                for member_ordinal, (slot, ordinal, child_id) in enumerate(placements):
                    child = target(child_id)
                    expected = "skill" if slot == "skills" else "tool" if slot == "tools" else "role"
                    if child.kind != expected:
                        raise _fail("Member kind does not match typed slot")
                    if slot in ("pre_process", "post_process") and child.role_variant != slot:
                        raise _fail("Process slot requires matching Role variant")
                    place(child_id, node.node_id, slot, ordinal, member_ordinal)
            for used in node.uses:
                target(used)
                if used == node.node_id:
                    raise _fail("Self uses is forbidden")
            if node.instruction_template is not None:
                for part in node.instruction_template.parts:
                    if isinstance(part, NodeRefPart):
                        target(part.target_id)
        if len(parents) != len(nodes):
            raise _fail("Every node must have exactly one root or parent placement")

        # Enter/exit stack; parent links and compact intervals replace recursive
        # traversal and copied ancestor/path arrays, including for depth 5000+.
        order: list[str] = []
        locations: dict[str, NodeLocation] = {}
        entered: set[str] = set()
        stack: list[tuple[str, Plane, int, int, bool, bool, bool]] = []
        for i in range(len(definition.roots) - 1, -1, -1):
            root = definition.roots[i]
            stack.append((root.node_id, root.plane, i, 0, False, False, False))
        while stack:
            node_id, plane, root_i, depth, inherited, blocked, exiting = stack.pop()
            node = nodes[node_id]
            if exiting:
                old = locations[node_id]
                locations[node_id] = NodeLocation(old.parent_id, old.slot, old.ordinal,
                    old.member_ordinal, root_i, plane, depth, old.preorder, len(order),
                    old.effective_mutable, old.false_barrier)
                continue
            if node_id in entered:
                raise _fail("Containment cycle or alias")
            entered.add(node_id)
            barrier = blocked or node.mutable is False
            effective = not barrier and (node.mutable is True or inherited)
            parent, slot, ordinal, member_ordinal = parents[node_id]
            locations[node_id] = NodeLocation(parent, slot, ordinal, member_ordinal,
                root_i, plane, depth, len(order), -1, effective, barrier)
            order.append(node_id)
            stack.append((node_id, plane, root_i, depth, effective, barrier, True))
            for _, _, child_id in reversed(children.get(node_id, ())):
                stack.append((child_id, plane, root_i, depth + 1, effective, barrier, False))
        if len(entered) != len(nodes):
            raise _fail("Unreachable containment component or cycle")

        # Node-array order is not semantic. Reverse lists use forest preorder,
        # preserving uses/part order within each source without sorting nodes.
        for node_id in order:
            node = nodes[node_id]
            for used in node.uses:
                reverse_uses.setdefault(used, []).append(node_id)
            if node.instruction_template is not None:
                for i, part in enumerate(node.instruction_template.parts):
                    if isinstance(part, NodeRefPart):
                        templates.setdefault(part.target_id, []).append(TemplateReference(node_id, i))

        evidence = contract_lookup(publication_contracts) if verify_contracts else {}
        publication_ids: set[str] = set()
        public_names: set[str] = set()
        uris: set[str] = set()
        routes: set[tuple[str, str]] = set()
        for i, publication in enumerate(definition.publications):
            loc = f"/publications/{i}"
            node = target(publication.opens_id)
            if publication.publication_id in publication_ids:
                raise _fail("Duplicate publication identity", loc)
            publication_ids.add(publication.publication_id)
            pubs.setdefault(node.node_id, []).append(publication.publication_id)
            if isinstance(publication, (PromptPublication, ResourcePublication)):
                if publication.name in public_names:
                    raise _fail("Duplicate public name", loc)
                public_names.add(publication.name)
            if isinstance(publication, PromptPublication):
                continue
            if definition.mode != "runtime" or node.kind != "tool" or node.binding is None:
                raise _fail("Executable publication requires a runtime Tool", loc)
            if not verify_contracts:
                # Accepted history: effect/argument checks were admission-time
                # registry evidence and are re-resolved by the compiler.
                if isinstance(publication, ResourcePublication):
                    if publication.uri in uris:
                        raise _fail("Duplicate Resource URI", loc)
                    uris.add(publication.uri)
                elif isinstance(publication, RoutePublication):
                    route = (publication.method, publication.path)
                    if route in routes:
                        raise _fail("Duplicate Route method/path", loc)
                    routes.add(route)
                continue
            contract = evidence.get(canonical_json(node.binding.as_json()))
            if contract is None:
                raise _fail("Pinned registry contract required for executable publication", loc,
                            "binding_contract_required")
            if isinstance(publication, ResourcePublication):
                if contract.effect != "read_only" or not contract.argument_free:
                    raise _fail("Resource requires an argument-free read-only Tool", loc)
                if publication.uri in uris:
                    raise _fail("Duplicate Resource URI", loc)
                uris.add(publication.uri)
            elif isinstance(publication, RoutePublication):
                expected_effect = "read_only" if publication.method in ("GET", "HEAD") else "write"
                if contract.effect != expected_effect:
                    raise _fail("Route method and registry effect mismatch", loc)
                route = (publication.method, publication.path)
                if route in routes:
                    raise _fail("Duplicate Route method/path", loc)
                routes.add(route)

        catalogs: dict[str, Mapping[str, Presentation]] = {}
        for catalog in definition.presentations.catalogs:
            entries: dict[str, Presentation] = {}
            for entry in catalog.entries:
                target(entry.node_id)
                entries[entry.node_id] = entry
                presentations.setdefault(entry.node_id, []).append(catalog.locale)
            catalogs[catalog.locale.lower()] = MappingProxyType(entries)
        return _new(cls, definition=definition, locations=MappingProxyType(locations),
            preorder=tuple(order),
            reverse_uses=MappingProxyType({n: tuple(reverse_uses.get(n, ())) for n in nodes}),
            template_refs=MappingProxyType({n: tuple(templates.get(n, ())) for n in nodes}),
            publications=MappingProxyType({n: tuple(pubs.get(n, ())) for n in nodes}),
            presentation_refs=MappingProxyType({n: tuple(presentations.get(n, ())) for n in nodes}),
            child_names=MappingProxyType({n: MappingProxyType(v) for n, v in names.items()}),
            _catalogs=MappingProxyType(catalogs),
            _model_reserved=frozenset(p.opens_id for p in definition.publications
                if isinstance(p, PromptPublication) and not p.model_may_open))

    def get(self, identity: str, *, by: str = "id") -> NodeSpec:
        """Resolve explicit id (default) or absolute author path, never a runtime ref.

        Explicit selector avoids ambiguity: opaque IDs themselves may contain '/'.
        No selection widening, permission checks or managed-reference fallback.
        """
        if by == "path":
            if not isinstance(identity, str) or not identity.startswith("/"):
                raise _fail("Expected absolute author path")
            parent: str | None = None
            for name in identity[1:].split("/"):
                found = self.child_names.get(parent, {}).get(name)
                if found is None:
                    raise _fail("Author path not found", code="reference_not_found")
                parent = found
            if parent is None:
                raise _fail("Author path not found", code="reference_not_found")
            return self.definition.nodes[parent]
        if by != "id":
            raise _fail("Lookup selector must be id or path")
        try:
            return self.definition.nodes[identity]
        except (KeyError, TypeError):
            raise _fail("Node identity not found", code="reference_not_found") from None

    def path(self, node_id: str) -> str:
        self.get(node_id)
        segments: list[str] = []
        current: str | None = node_id
        while current is not None:
            segments.append(self.definition.nodes[current].name)
            current = self.locations[current].parent_id
        return "/" + "/".join(reversed(segments))

    def walk(self, *, plane: Plane | None = None) -> Iterator[NodeSpec]:
        if plane not in (None, "model", "prompt"):
            raise _fail("Invalid plane")
        for node_id in self.preorder:
            if plane is None or self.locations[node_id].plane == plane:
                yield self.definition.nodes[node_id]

    def subtree(self, node_id: str, *, include_self: bool = True) -> Iterator[NodeSpec]:
        self.get(node_id)
        loc = self.locations[node_id]
        for i in range(loc.preorder + (not include_self), loc.subtree_end):
            yield self.definition.nodes[self.preorder[i]]

    def model_may_open(self, node_id: str) -> bool:
        """Structural reservation only; never invocation or current authority."""
        self.get(node_id)
        return self.locations[node_id].plane == "model" and node_id not in self._model_reserved

    def presentation(self, node_id: str, locale: str) -> Presentation | None:
        """Exact, RFC4647-style parent lookup, then explicit default locale."""
        self.get(node_id)
        candidate = normalize_locale(locale).lower()
        while candidate:
            entry = self._catalogs.get(candidate, {}).get(node_id)
            if entry is not None:
                return entry
            candidate = candidate.rpartition("-")[0]
            if candidate and len(candidate.rpartition("-")[2]) == 1:
                candidate = candidate.rpartition("-")[0]
        return self._catalogs.get(self.definition.presentations.default_locale.lower(), {}).get(node_id)