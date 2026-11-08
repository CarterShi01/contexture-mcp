"""Portable, business-neutral graph bundles (export/import), without I/O.

Two bundle kinds, both canonical JSON with a self digest:

* ``contexture.graph-bundle/v1``: a whole definition, or disjoint subtrees with
  their publications and presentations. References leaving the exported scope
  are listed as ``external_refs``; nothing outside the scope is copied.
* ``contexture.history-bundle/v1``: every accepted graph event of one graph from
  its bootstrap, verifiable by pure replay.

Importing a graph bundle never bypasses governance: ``plan_import`` only
produces ordinary transaction commands, which the owner previews, admits and
commits like any other change (mutability, CAS, idempotency all apply).
Executable parts are binding references only; the target registry decides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from .canonical import FrozenJSON, canonical_digest, canonical_json, decode_json, freeze_json, thaw_json, validate_id
from .errors import GraphDiagnostic, GraphError
from .events import RevisionState
from .history import History, replay_events
from .index import GraphIndex
from .model import GraphDefinition, NodeRefPart, NodeSpec

GRAPH_BUNDLE = "contexture.graph-bundle/v1"
HISTORY_BUNDLE = "contexture.history-bundle/v1"
Wire = dict[str, object]
Identity = Literal["preserve", "remap"]


def _fail(message: str, code: str = "schema_invalid", node_id: str | None = None) -> GraphError:
    diagnostics = () if node_id is None else (GraphDiagnostic(code, message=message, node_id=node_id),)
    return GraphError(code, message if node_id is None else f"{message}: {node_id}",
                      diagnostics=diagnostics)


def _sealed(subject: Wire) -> Wire:
    return {**subject, "bundle_digest": canonical_digest(subject, "contexture.command/v1")}


def _open(raw: object, schema: str) -> Wire:
    value = decode_json(raw) if isinstance(raw, bytes) else thaw_json(freeze_json(raw))
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise _fail("Unsupported bundle schema")
    subject = {k: v for k, v in value.items() if k != "bundle_digest"}
    if value.get("bundle_digest") != canonical_digest(subject, "contexture.command/v1"):
        raise _fail("Bundle digest mismatch", "corrupt_facts")
    return cast(Wire, value)


@dataclass(frozen=True, slots=True)
class GraphBundle:
    """A verified graph bundle; construct with export_bundle or from_json."""

    value: Mapping[str, FrozenJSON]

    @classmethod
    def from_json(cls, raw: object) -> GraphBundle:
        value = _open(raw, GRAPH_BUNDLE)
        if value.get("scope") not in ("graph", "subtrees"):
            raise _fail("Unknown bundle scope")
        bundle = cls(cast(Mapping[str, FrozenJSON], freeze_json(value)))
        bundle._validate()
        return bundle

    def as_json(self) -> Wire:
        return cast(Wire, thaw_json(self.value))

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.value)

    @property
    def scope(self) -> str:
        return cast(str, self.value["scope"])

    @property
    def source(self) -> Mapping[str, FrozenJSON]:
        return cast(Mapping[str, FrozenJSON], self.value["source"])

    def definition(self) -> GraphDefinition:
        if self.scope != "graph":
            raise _fail("Only a whole-graph bundle carries a definition")
        return GraphDefinition.from_json(thaw_json(self.value["definition"]), verify_contracts=False)

    def _validate(self) -> None:
        if self.scope == "graph":
            definition = self.definition()
            if definition.digest() != self.source.get("definition_digest"):
                raise _fail("Bundle definition digest mismatch", "corrupt_facts")
            return
        # Subtrees: every node appears once; containment stays inside the
        # bundle; anchors are the only nodes without an in-bundle parent.
        nodes = {cast(str, n["node_id"]): n for n in cast(Sequence[Mapping[str, object]], self.value["nodes"])}
        specs = {i: NodeSpec.from_json(thaw_json(n)) for i, n in nodes.items()}
        anchors = [cast(str, a) for a in cast(Sequence[object], self.value["anchors"])]
        parents: dict[str, str] = {}
        for identity, spec in specs.items():
            if spec.members:
                for _, _, child in spec.members.placements():
                    if child not in specs or child in parents:
                        raise _fail("Subtree containment leaves the bundle", node_id=child)
                    parents[child] = identity
        if set(anchors) != set(specs) - set(parents) or len(anchors) != len(set(anchors)):
            raise _fail("Bundle anchors must be exactly the parentless nodes")


def _subtree(index: GraphIndex, anchor: str) -> list[str]:
    loc = index.locations[anchor]
    return list(index.preorder[loc.preorder:loc.subtree_end])


def export_bundle(state: RevisionState, *, node_ids: Sequence[str] | None = None,
                  producer: Mapping[str, str] | None = None) -> GraphBundle:
    """Export the whole graph, or the containment subtrees of ``node_ids``.

    ``producer`` is optional provenance (e.g. package version) added by the
    calling layer; it is informational, never authority.
    """
    definition = state.definition
    source: Wire = {"graph_id": definition.graph_id, "revision": state.revision,
                    "definition_digest": state.definition_digest, "mode": definition.mode}
    if producer:
        source["producer"] = dict(producer)
    if node_ids is None:
        return GraphBundle(cast(Mapping[str, FrozenJSON], freeze_json(_sealed({
            "schema": GRAPH_BUNDLE, "scope": "graph", "source": source,
            "definition": definition.as_json()}))))
    index = GraphIndex.build(definition, verify_contracts=False)
    anchors = [validate_id(n) for n in node_ids]
    if not anchors or len(set(anchors)) != len(anchors):
        raise _fail("Explicit distinct anchors required")
    included: list[str] = []
    for anchor in anchors:
        index.get(anchor)
        for identity in _subtree(index, anchor):
            if identity in included:
                raise _fail("Anchors must be disjoint subtrees", node_id=identity)
            included.append(identity)
    scope = set(included)
    external: set[str] = set()
    for identity in included:
        node = definition.nodes[identity]
        external.update(n for n in node.uses if n not in scope)
        if node.instruction_template:
            external.update(p.target_id for p in node.instruction_template.parts
                            if isinstance(p, NodeRefPart) and p.target_id not in scope)
    publications = [p.as_json() for p in definition.publications if p.opens_id in scope]
    presentations = [{"locale": c.locale, "entries": [e.as_json() for e in c.entries if e.node_id in scope]}
                     for c in definition.presentations.catalogs]
    return GraphBundle(cast(Mapping[str, FrozenJSON], freeze_json(_sealed({
        "schema": GRAPH_BUNDLE, "scope": "subtrees", "source": source, "anchors": anchors,
        "nodes": [definition.nodes[n].as_json() for n in included],
        "publications": publications,
        "presentations": [p for p in presentations if p["entries"]],
        "external_refs": sorted(external)}))))


def bootstrap_definition(bundle: GraphBundle, *, graph_id: str | None = None) -> GraphDefinition:
    """The seed definition for a NEW graph from a whole-graph bundle."""
    wire = cast(Wire, thaw_json(bundle.value["definition"])) if bundle.scope == "graph" else None
    if wire is None:
        raise _fail("A new graph is seeded from a whole-graph bundle")
    if graph_id is not None:
        wire["graph_id"] = validate_id(graph_id)
    return GraphDefinition.from_json(wire, verify_contracts=False)


def plan_import(base: RevisionState, bundle: GraphBundle, *, target: Mapping[str, object],
                identity: Identity = "preserve", remap: Mapping[str, str] | None = None,
                prefix: str | None = None,
                external: Mapping[str, str] | None = None) -> list[Wire]:
    """Ordinary transaction commands importing a subtree bundle into ``base``.

    ``target`` is ``{"parent_id": id, ["position": n]}`` (slot follows each
    anchor's kind) or ``{"plane": "model"|"prompt", ["position": n]}`` for roots.
    ``identity="preserve"`` keeps IDs and refuses any conflict with live or
    retained (tombstoned) identities; ``"remap"`` applies ``remap`` then
    ``prefix`` to every bundle ID (nodes and publications; with a prefix also
    publication public names, which must stay unique) and rewrites all
    in-bundle references. ``external`` maps each external reference to an
    existing target node (default: the same ID must exist).
    """
    if bundle.scope != "subtrees":
        raise _fail("Import into an existing graph requires a subtree bundle")
    if identity not in ("preserve", "remap"):
        raise _fail("Identity policy must be preserve or remap")
    if identity == "preserve" and (remap or prefix):
        raise _fail("Remapping requires identity='remap'")
    value = bundle.as_json()
    nodes = cast(list[Wire], value["nodes"])
    publications = cast(list[Wire], value["publications"])
    ids = [cast(str, n["node_id"]) for n in nodes]
    pub_ids = [cast(str, p["publication_id"]) for p in publications]

    def mapped(old: str) -> str:
        if identity == "preserve":
            return old
        new = (remap or {}).get(old)
        return validate_id(new if new is not None else (prefix or "") + old)

    node_map = {i: mapped(i) for i in ids}
    pub_map = {i: mapped(i) for i in pub_ids}
    refs = {r: validate_id((external or {}).get(r, r)) for r in cast(list[str], value["external_refs"])}
    live = base.definition.nodes
    retained = {cast(str, t["node_id"]) for t in base.tombstones}
    for old, new in node_map.items():
        if new in live or new in retained:
            raise _fail("Imported identity already exists in the target graph", "identity_conflict", new)
    if len(set(node_map.values())) != len(node_map):
        raise _fail("Remapping produced duplicate identities", "identity_conflict")
    existing_pubs = {p.publication_id for p in base.definition.publications}
    for new in pub_map.values():
        if new in existing_pubs:
            raise _fail("Imported publication identity already exists", "identity_conflict", new)
    for old, new in refs.items():
        if new not in live:
            raise _fail("External reference has no target node", "reference_not_found", old)
    resolve = {**node_map, **refs}

    commands: list[Wire] = []
    templates: list[tuple[str, Wire]] = []
    members: list[tuple[str, str, str]] = []
    uses: list[tuple[str, str]] = []
    for node in nodes:
        new_id = node_map[cast(str, node["node_id"])]
        created = {k: v for k, v in node.items() if k not in ("members", "uses")}
        created["node_id"] = new_id
        template = created.pop("instruction_template", None)
        if isinstance(template, dict):
            # Forward/cyclic NodeRefs: create with empty text, set the program
            # once every target exists (only the final graph is validated).
            created["instructions"] = ""
            templates.append((new_id, {"parts": [
                {**part, "target_id": resolve[cast(str, part["target_id"])]}
                if part.get("kind") == "NodeRef" else part
                for part in cast(list[dict[str, object]], template["parts"])]}))
        commands.append({"op": "create_node", "node": created})
        raw_members = node.get("members")
        if isinstance(raw_members, dict):
            for slot in ("pre_process", "children", "post_process", "skills", "tools"):
                entry = raw_members.get(slot)
                for child in ([entry] if isinstance(entry, str) else entry if isinstance(entry, list) else []):
                    members.append((new_id, slot, node_map[cast(str, child)]))
        uses.extend((new_id, resolve[cast(str, u)]) for u in cast(list[str], node.get("uses", [])))
    anchors = [node_map[cast(str, a)] for a in cast(list[str], value["anchors"])]
    kinds = {node_map[cast(str, n["node_id"])]: n for n in nodes}
    position = target.get("position")
    if "parent_id" in target:
        parent = validate_id(target["parent_id"])
        if parent not in live:
            raise _fail("Import parent does not exist", "reference_not_found", parent)
        spec = live[parent]
        for i, anchor in enumerate(anchors):
            node = kinds[anchor]
            slot = ("skills" if node["kind"] == "skill" else "tools" if node["kind"] == "tool"
                    else cast(str, node.get("role_variant")) if node.get("role_variant") in
                    ("pre_process", "post_process") else "children")
            current = [c for s, _, c in (spec.members.placements() if spec.members else ()) if s == slot]
            at = (len(current) + i) if position is None else cast(int, position) + i
            commands.append({"op": "attach_node", "node_id": anchor,
                             "target": {"parent_id": parent, "slot": slot, "position": at}})
    elif "plane" in target:
        start = len(base.definition.roots) if position is None else cast(int, position)
        for i, anchor in enumerate(anchors):
            commands.append({"op": "add_root", "node_id": anchor, "plane": target["plane"],
                             "position": start + i})
    else:
        raise _fail("Import target requires parent_id or plane")
    filled: dict[tuple[str, str], int] = {}
    for parent, slot, child in members:
        at = filled.get((parent, slot), 0)
        filled[(parent, slot)] = at + 1
        commands.append({"op": "attach_node", "node_id": child,
                         "target": {"parent_id": parent, "slot": slot, "position": at}})
    for source, used in uses:
        commands.append({"op": "add_use", "source_id": source, "target_id": used})
    for node_id, template in templates:
        commands.append({"op": "update_node", "node_id": node_id,
                         "set_fields": {"instruction_template": template}, "unset_fields": ["instructions"]})
    for publication in publications:
        commands.append({"op": "put_publication", "publication": {**publication,
            "publication_id": pub_map[cast(str, publication["publication_id"])],
            **({"name": prefix + cast(str, publication["name"])}
               if identity == "remap" and prefix and "name" in publication else {}),
            "opens_id": node_map[cast(str, publication["opens_id"])]}})
    for catalog in cast(list[Wire], value["presentations"]):
        for entry in cast(list[Wire], catalog["entries"]):
            commands.append({"op": "put_presentation", "locale": catalog["locale"],
                             "presentation": {**entry, "node_id": node_map[cast(str, entry["node_id"])]}})
    return commands


@dataclass(frozen=True, slots=True)
class HistoryBundle:
    value: Mapping[str, FrozenJSON]

    @classmethod
    def from_json(cls, raw: object) -> HistoryBundle:
        bundle = cls(cast(Mapping[str, FrozenJSON], freeze_json(_open(raw, HISTORY_BUNDLE))))
        bundle.replay()
        return bundle

    def as_json(self) -> Wire:
        return cast(Wire, thaw_json(self.value))

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.value)

    @property
    def graph_id(self) -> str:
        return cast(str, self.value["graph_id"])

    @property
    def events(self) -> tuple[Mapping[str, FrozenJSON], ...]:
        return cast(tuple[Mapping[str, FrozenJSON], ...], self.value["events"])

    def replay(self) -> History:
        """Pure verification: the events replay to the recorded head."""
        history = replay_events(self.graph_id, self.events)
        head = cast(Mapping[str, FrozenJSON], self.value["head"])
        if (history.head.revision != head["revision"]
                or history.head.definition_digest != head["definition_digest"]):
            raise _fail("History bundle does not replay to its head", "corrupt_facts")
        return history


def export_history(history: History) -> HistoryBundle:
    """All accepted graph events from bootstrap; refuses a partial history."""
    records = history.records
    if history.retained_lower_bound != "1" or records[0].event is None:
        raise _fail("Complete history from bootstrap required", "history_unavailable")
    head = history.head
    return HistoryBundle(cast(Mapping[str, FrozenJSON], freeze_json(_sealed({
        "schema": HISTORY_BUNDLE, "graph_id": head.definition.graph_id,
        "events": [thaw_json(r.event) for r in records],
        "head": {"revision": head.revision, "definition_digest": head.definition_digest}}))))

