"""Pure resolved facts and atomic replay; no commands, authorization or effects.

This is a candidate API, not a frozen wire release. UnseededState has no serving
definition; its before digest is the graph-domain hash of canonical JSON null.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import cast

from .canonical import (
    FrozenJSON, MAX_COUNTER, canonical_digest, freeze_json,
    parse_counter, thaw_json, validate_id,
)
from .errors import GraphError
from .canonical import canonical_equal
from .model import GraphDefinition, NodeSpec

Wire = dict[str, object]
Facts = tuple[Mapping[str, FrozenJSON], ...]
FIELD_NAMES = frozenset({
    "name", "description", "instructions", "instruction_template", "extensions",
    "binding", "input_contract",
})
GRAPH_FIELDS = frozenset({"application_name", "mode", "extensions"})


@dataclass(frozen=True, slots=True)
class UnseededState:
    """Revision-zero management identity, never an empty serving graph."""

    graph_id: str

    def __post_init__(self) -> None:
        validate_id(self.graph_id)

    @property
    def revision(self) -> str:
        return "0"

    @property
    def definition_digest(self) -> str:
        return canonical_digest(None, "contexture.graph/v1")

    @property
    def tombstones(self) -> tuple[Mapping[str, FrozenJSON], ...]:
        return ()


def _fail(message: str, code: str = "corrupt_facts") -> GraphError:
    return GraphError(code, message)


def _object(value: object, required: str, optional: str = "") -> Wire:
    if not isinstance(value, Mapping) or any(type(k) is not str for k in value):
        raise _fail("Expected a closed JSON object")
    if not set(required.split()) <= set(value) or set(value) - set((required + " " + optional).split()):
        raise _fail("Missing required or unknown fact fields")
    return dict(value)


def _equal(left: object, right: object) -> bool:
    return canonical_equal(left, right)


def _digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise _fail("Invalid digest")
    return value


def optional(value: Mapping[str, object], key: str) -> Wire:
    """Wire OptionalValue: absence is not JSON null."""
    return {"present": True, "value": value[key]} if key in value else {"present": False}


def _unwrap(value: object) -> tuple[bool, object]:
    obj = _object(value, "present", "value")
    if type(obj["present"]) is not bool or ("value" in obj) != obj["present"]:
        raise _fail("Invalid optional value")
    return obj["present"], obj.get("value")


def _tombstones(value: object, revision: str) -> dict[str, Wire]:
    if not isinstance(value, (list, tuple)):
        raise _fail("Tombstones must be an array")
    result: dict[str, Wire] = {}
    for item in value:
        t = _object(item, "node_id kind mutable deleted_revision")
        identity = validate_id(t["node_id"])
        deleted = parse_counter(t["deleted_revision"])
        if not 0 < deleted <= parse_counter(revision):
            raise _fail("Tombstone revision is outside retained history")
        if t["kind"] not in ("role", "skill", "tool") or (
            type(t["mutable"]) is not bool and t["mutable"] != "inherit"
        ):
            raise _fail("Invalid retained identity policy")
        if identity in result:
            raise _fail("Duplicate tombstone identity")
        result[identity] = t
    return result


def _frozen_object(value: object) -> Mapping[str, FrozenJSON]:
    return cast(Mapping[str, FrozenJSON], freeze_json(value))


@dataclass(frozen=True, slots=True)
class RevisionState:
    """Detached immutable projection of an already accepted, nonzero revision.

    Tombstones remain retained after restoration. They are identity evidence,
    not an assertion that the node is currently absent. Not a serving lease.
    """

    definition: GraphDefinition
    revision: str
    tombstones: tuple[Mapping[str, FrozenJSON], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.definition, GraphDefinition) or parse_counter(self.revision) == 0:
            raise _fail("Revision zero cannot own a serving definition", "bootstrap_contract_unfrozen")
        retained = _tombstones(self.tombstones, self.revision)
        for identity, t in retained.items():
            node = self.definition.nodes.get(identity)
            if node is not None and (node.kind != t["kind"] or node.mutable != t["mutable"]):
                raise _fail("Live identity conflicts with retained creation policy")
        object.__setattr__(self, "tombstones", tuple(
            _frozen_object(retained[n]) for n in sorted(retained)))

    @property
    def definition_digest(self) -> str:
        return self.definition.digest()


@dataclass(frozen=True, slots=True, init=False)
class BootstrapCandidate:
    """Owned seed only. No revision-zero digest, event or accepted state implied."""

    definition: GraphDefinition

    @classmethod
    def own(cls, seed: GraphDefinition) -> BootstrapCandidate:
        if not isinstance(seed, GraphDefinition):
            raise _fail("Bootstrap seed must be a validated definition")
        result = object.__new__(cls)
        object.__setattr__(result, "definition", seed)
        return result


@dataclass(frozen=True, slots=True)
class ResolvedChange:
    """Transaction input: complete facts, retained tombstones and candidate."""

    candidate: GraphDefinition
    facts: Facts
    tombstones: tuple[Mapping[str, FrozenJSON], ...]
    migration_id: str | None = None
    source_mapping: Mapping[str, FrozenJSON] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, GraphDefinition):
            raise _fail("Expected validated candidate definition")
        object.__setattr__(self, "facts", tuple(_frozen_object(f) for f in self.facts))
        object.__setattr__(self, "tombstones", tuple(_frozen_object(t) for t in self.tombstones))
        if self.migration_id is not None:
            validate_id(self.migration_id)
        if self.source_mapping is not None:
            object.__setattr__(self, "source_mapping", _frozen_object(self.source_mapping))

    @property
    def facts_digest(self) -> str:
        return facts_digest(self.facts)

    @property
    def noop(self) -> bool:
        return not self.facts


def facts_digest(facts: object) -> str:
    """D25: only the ordered complete fact array, never an event wrapper."""
    if not isinstance(facts, (list, tuple)):
        raise _fail("Facts must be an array")
    return canonical_digest(facts, "contexture.facts/v1")


def _mapping(base: RevisionState, candidate: GraphDefinition, value: object) -> Mapping[str, FrozenJSON]:
    a, b = set(base.definition.nodes), set(candidate.nodes)
    expected = {"retained_ids": sorted(a & b), "new_ids": sorted(b - a), "deleted_ids": sorted(a - b)}
    if not _equal(value, expected):
        raise _fail("Migration source mapping must exactly partition endpoint identities")
    return _frozen_object(expected)


def resolve_migration(base: RevisionState, candidate: GraphDefinition, *, migration_id: str,
                      exact_diff: object, source_mapping: object) -> ResolvedChange:
    """Propose trusted semantic/policy facts; does NOT authorize or verify approval.

    The current decoder supports semantic version 1 only. A 1→1 semantic
    migration may change meaning/policy; unsupported version/kind rewrites still
    refuse rather than manufacture a future decoder or reuse logical identity.
    """
    from .diff import definition_diff

    validate_id(migration_id)
    if not isinstance(base, RevisionState) or not isinstance(candidate, GraphDefinition):
        raise _fail("Migration requires validated endpoints")
    if not _equal(exact_diff, definition_diff(base.definition, candidate).as_json()):
        raise _fail("Trusted migration exact diff does not match endpoints")
    mapping = _mapping(base, candidate, source_mapping)
    return _resolve(base, candidate, migration_id=migration_id, source_mapping=mapping)


def _provenance(value: object, kind: str, source_digest: str) -> Wire:
    required = "kind approval_ref source_digest"
    if kind == "migration":
        required += " migration_id exact_diff"
    result = _object(value, required)
    if result["kind"] != kind or _digest(result["source_digest"]) != source_digest:
        raise _fail("Trusted provenance kind/source mismatch")
    validate_id(result["approval_ref"])
    if kind == "migration":
        validate_id(result["migration_id"])
    return result


def bootstrap_event(base: UnseededState, seed: GraphDefinition | BootstrapCandidate, *,
                    command_digest: str, reason: str, approval_ref: str) -> Mapping[str, FrozenJSON]:
    """Construct an exact trusted seed event, not an admission/durability receipt."""
    if not isinstance(base, UnseededState):
        raise _fail("Bootstrap requires an unseeded management state")
    definition = seed.definition if isinstance(seed, BootstrapCandidate) else seed
    if not isinstance(definition, GraphDefinition) or definition.graph_id != base.graph_id:
        raise _fail("Seed must be validated and match management identity")
    _digest(command_digest)
    validate_id(approval_ref)
    if type(reason) is not str or not reason:
        raise _fail("Event reason must be nonempty text")
    return _frozen_object({"type": "GraphChangeCommitted", "graph_id": base.graph_id,
        "semantic_version": "1", "base_revision": "0", "result_revision": "1",
        "before_digest": base.definition_digest, "after_digest": definition.digest(),
        "resolved_facts": [{"type": "GraphSeeded", "before": None, "after": definition.as_json()}],
        "command_digest": command_digest, "reason": reason,
        "migration": {"kind": "bootstrap", "approval_ref": approval_ref,
                      "source_digest": base.definition_digest}})


def _apply_bootstrap(base: UnseededState, event: Mapping[str, object], tombstones: object) -> RevisionState:
    e = _object(event, "type graph_id semantic_version base_revision result_revision before_digest "
                "after_digest resolved_facts command_digest reason migration")
    _provenance(e["migration"], "bootstrap", base.definition_digest)
    facts = e["resolved_facts"]
    if not isinstance(facts, list) or len(facts) != 1:
        raise _fail("Bootstrap requires exactly one complete GraphSeeded fact")
    fact = _object(facts[0], "type before after")
    if fact["type"] != "GraphSeeded" or fact["before"] is not None or not _equal(tombstones, []):
        raise _fail("Bootstrap has no prior definition or retained identities")
    definition = GraphDefinition.from_json(fact["after"], verify_contracts=False)
    provenance = cast(Mapping[str, object], e["migration"])
    expected = bootstrap_event(base, definition, command_digest=cast(str, e["command_digest"]),
        reason=cast(str, e["reason"]), approval_ref=cast(str, provenance["approval_ref"]))
    if not _equal(expected, e):
        raise _fail("Bootstrap event is not the exact normalized seed transition")
    return RevisionState(definition, "1")


def resolve_facts(base: RevisionState, candidate: GraphDefinition) -> ResolvedChange:
    """Normalize base→final candidate, independent of transient commands.

    Ordinary facts never migrate kinds, variants or creation policy.
    Deletions mint tombstones at base+1; never-committed IDs leave no evidence.
    """
    if not isinstance(base, RevisionState) or not isinstance(candidate, GraphDefinition):
        raise _fail("Expected revision state and validated candidate")
    return _resolve(base, candidate)


def _resolve(base: RevisionState, candidate: GraphDefinition, *, migration_id: str | None = None,
             source_mapping: Mapping[str, FrozenJSON] | None = None) -> ResolvedChange:
    old, new = base.definition.as_json(), candidate.as_json()
    for field in ("schema", "semantic_version", "graph_id"):
        if not _equal(old[field], new[field]):
            raise _fail("Unsupported graph identity/schema/version transition", "unsupported_contract")
    retained = _tombstones(base.tombstones, base.revision)
    facts: list[Wire] = []
    a = {n: v.as_json() for n, v in base.definition.nodes.items()}
    b = {n: v.as_json() for n, v in candidate.nodes.items()}
    for identity in sorted(b.keys() - a.keys()):
        node = b[identity]
        if identity in retained:
            t = retained[identity]
            if node["kind"] != t["kind"] or node["mutable"] != t["mutable"]:
                raise _fail("Restoration changes retained identity policy")
            facts.append({"type": "NodeRestored", "node": node, "tombstone": t})
        else:
            facts.append({"type": "NodeCreated", "node": node})
    for identity in sorted(a.keys() & b.keys()):
        for field in ("kind", "role_variant"):
            if not _equal(optional(a[identity], field), optional(b[identity], field)):
                raise _fail("Ordinary facts cannot change identity, variant or creation policy")
        if a[identity]["mutable"] != b[identity]["mutable"]:
            if migration_id is None:
                raise _fail("Ordinary facts cannot change creation policy")
            facts.append({"type": "CreationPolicyMigrated", "node_id": identity,
                "before": a[identity]["mutable"], "after": b[identity]["mutable"],
                "migration_id": migration_id})
            if identity in retained:
                retained[identity] = dict(retained[identity], mutable=b[identity]["mutable"])
        changed = sorted(k for k in FIELD_NAMES
            if not _equal(optional(a[identity], k), optional(b[identity], k)))
        if changed:
            facts.append({"type": "NodeFieldsSet", "node_id": identity,
                "before": {k: optional(a[identity], k) for k in changed},
                "after": {k: optional(b[identity], k) for k in changed}})
    changed_graph = sorted(k for k in GRAPH_FIELDS if not _equal(old[k], new[k]))
    if changed_graph:
        facts.append({"type": "GraphFieldsSet", "before": {k: old[k] for k in changed_graph},
                      "after": {k: new[k] for k in changed_graph}})
    if not _equal(old["roots"], new["roots"]):
        facts.append({"type": "RootsAssigned", "before": old["roots"], "after": new["roots"]})
    for field, tag in (("members", "MembersAssigned"), ("uses", "UsesAssigned")):
        for identity in sorted(a.keys() & b.keys()):
            if field in a[identity] and not _equal(a[identity][field], b[identity][field]):
                facts.append({"type": tag, "node_id": identity,
                              "before": a[identity][field], "after": b[identity][field]})
    ap = {p.publication_id: (i, p.as_json()) for i, p in enumerate(base.definition.publications)}
    bp = {p.publication_id: (i, p.as_json()) for i, p in enumerate(candidate.publications)}
    for identity in sorted(ap.keys() | bp.keys()):
        if identity not in bp:
            position, pub = ap[identity]
            facts.append({"type": "PublicationRemoved", "before": pub, "position": position})
        elif identity not in ap or not _equal(ap[identity], bp[identity]):
            position, pub = bp[identity]
            facts.append({"type": "PublicationPut", "before":
                {"present": True, "value": ap[identity][1]} if identity in ap else {"present": False},
                "after": pub, "position": position})
    if not _equal(old["presentations"], new["presentations"]):
        facts.append({"type": "PresentationsAssigned", "before": old["presentations"],
                      "after": new["presentations"]})
    for identity in sorted(a.keys() - b.keys()):
        revision = parse_counter(base.revision) + 1
        if revision > MAX_COUNTER:
            raise _fail("Revision counter exhausted", "limit_exceeded")
        t = {"node_id": identity, "kind": a[identity]["kind"], "mutable": a[identity]["mutable"],
             "deleted_revision": str(revision)}
        retained[identity] = t
        facts.append({"type": "NodeDeleted", "node_id": identity, "before": a[identity], "tombstone": t})
    if migration_id is not None and facts:
        facts.insert(0, {"type": "SemanticMigrated", "before": old["semantic_version"],
            "after": new["semantic_version"], "migration_id": migration_id,
            "source_mapping": source_mapping})
    return ResolvedChange(candidate, tuple(_frozen_object(f) for f in facts),
        tuple(_frozen_object(retained[n]) for n in sorted(retained)), migration_id, source_mapping)


def apply_facts(
    base: RevisionState, facts: Sequence[Mapping[str, object]], *,
    tombstones: object, revision: str, before_digest: str, after_digest: str,
    resolved_fact_digest: str | None = None,
    migration: object = None,
) -> RevisionState:
    """Replay a complete normalized patch, validating the final graph only.

    Replay is a pure function of accepted journal facts: it never consults a
    binding registry. Registry evidence was checked when the candidate was
    admitted and is re-resolved per binding by the compiler before serving.

    All before values are against base, never a transient intermediate graph.
    Re-normalizing the complete candidate checks closed shapes, preconditions,
    duplicates, omissions, canonical fact order and every retained tombstone.
    Pinned publication evidence is caller-supplied data, never registry lookup.
    Replay does not authenticate provenance; journal integrity is a separate layer.
    """
    if not isinstance(base, RevisionState) or not isinstance(facts, (list, tuple)):
        raise _fail("Expected revision state and complete fact array")
    number = parse_counter(revision)
    if number != parse_counter(base.revision) + 1 or number > MAX_COUNTER:
        raise _fail("Result revision must be the next nonoverflowing counter")
    if _digest(before_digest) != base.definition_digest:
        raise _fail("Stale base definition digest", "revision_conflict")
    _digest(after_digest)
    # Validate/detach every nested value before reading it; no caller mutation.
    raw_facts = cast(list[Wire], thaw_json(facts))
    if not raw_facts:
        raise _fail("No-op must not produce a graph revision")
    if resolved_fact_digest is not None and _digest(resolved_fact_digest) != facts_digest(raw_facts):
        raise _fail("Resolved fact digest mismatch")
    wire = base.definition.as_json()
    nodes = {n: v.as_json() for n, v in base.definition.nodes.items()}
    publications = {p.publication_id: p.as_json() for p in base.definition.publications}
    positions = {p.publication_id: i for i, p in enumerate(base.definition.publications)}
    presentations: object = None
    migration_id: str | None = None
    source_mapping: Mapping[str, FrozenJSON] | None = None
    if migration is not None:
        provenance = _provenance(migration, "migration", base.definition_digest)
        migration_id = cast(str, provenance["migration_id"])
    deleted: set[str] = set()
    for raw in raw_facts:
        f = _object(raw, "type", "node node_id before after tombstone position locale migration_id source_mapping")
        tag = f["type"]
        if tag == "SemanticMigrated":
            if migration_id is None or f.get("migration_id") != migration_id:
                raise _fail("Semantic migration requires matching provenance")
            source_mapping = _frozen_object(f.get("source_mapping"))
        elif tag == "CreationPolicyMigrated":
            identity = validate_id(f.get("node_id"))
            if migration_id is None or f.get("migration_id") != migration_id or identity not in nodes:
                raise _fail("Policy migration requires matching provenance and base identity")
            nodes[identity]["mutable"] = f.get("after")
        elif tag == "GraphFieldsSet":
            wire.update(_object(f.get("after"), "", " ".join(GRAPH_FIELDS)))
        elif tag == "PresentationsAssigned":
            presentations = f.get("after")
        elif tag in ("NodeCreated", "NodeRestored"):
            node = NodeSpec.from_json(f.get("node")).as_json()
            identity = cast(str, node["node_id"])
            if identity in nodes:
                raise _fail("Creation/restoration identity already exists")
            nodes[identity] = node
        elif tag == "NodeFieldsSet":
            identity = validate_id(f.get("node_id"))
            if identity not in nodes:
                raise _fail("Field target does not exist in base")
            fields = _object(f.get("after"), "", " ".join(FIELD_NAMES))
            for key, value in fields.items():
                present, item = _unwrap(value)
                if present:
                    nodes[identity][key] = item
                else:
                    nodes[identity].pop(key, None)
        elif tag in ("MembersAssigned", "UsesAssigned"):
            identity = validate_id(f.get("node_id"))
            if identity not in nodes:
                raise _fail("Relation target does not exist")
            nodes[identity]["members" if tag == "MembersAssigned" else "uses"] = f.get("after")
        elif tag == "RootsAssigned":
            wire["roots"] = f.get("after")
        elif tag == "NodeDeleted":
            identity = validate_id(f.get("node_id"))
            if identity not in nodes or identity in deleted:
                raise _fail("Deletion target does not exist or is duplicated")
            deleted.add(identity)
        elif tag in ("PublicationPut", "PublicationRemoved"):
            pub = _object(f.get("after" if tag == "PublicationPut" else "before"),
                          "publication_id", "kind name description opens_id model_may_open uri mime_type method path")
            identity = validate_id(pub["publication_id"])
            if tag == "PublicationPut":
                position = f.get("position")
                if type(position) is not int or position < 0:
                    raise _fail("Invalid publication position")
                publications[identity] = pub
                positions[identity] = position
            else:
                if identity not in publications:
                    raise _fail("Publication removal target missing")
                del publications[identity]
                del positions[identity]
        else:
            raise _fail("Unknown or unsupported required fact type", "unsupported_contract")
    wire["nodes"] = [nodes[n] for n in sorted(nodes) if n not in deleted]
    # Every unchanged publication whose index shifts is emitted as a Put, so
    # final positions must be a complete permutation, never sequential inserts.
    if sorted(positions.values()) != list(range(len(positions))):
        raise _fail("Publication positions are not a complete final permutation")
    wire["publications"] = [publications[n] for n in sorted(publications, key=lambda n: positions[n])]
    wire["presentations"] = (presentations if presentations is not None
                             else base.definition.presentations.as_json())
    candidate = GraphDefinition.from_json(wire, verify_contracts=False)
    if migration_id is not None:
        expected = resolve_migration(base, candidate, migration_id=migration_id,
            exact_diff=_provenance(migration, "migration", base.definition_digest)["exact_diff"],
            source_mapping=source_mapping)
    else:
        expected = resolve_facts(base, candidate)
    expected_facts: object = expected.facts
    if not _equal(expected_facts, raw_facts):
        raise _fail("Facts are not the complete normalized base-to-candidate patch")
    supplied = _tombstones(tombstones, revision)
    if not _equal(expected.tombstones, [supplied[n] for n in sorted(supplied)]):
        raise _fail("Retained tombstone set differs from accepted facts")
    if candidate.digest() != after_digest:
        raise _fail("Final definition digest mismatch")
    return RevisionState(candidate, revision, expected.tombstones)


def change_event(base: RevisionState, change: ResolvedChange, *, command_digest: str,
                 reason: str, migration: object = None) -> Mapping[str, FrozenJSON]:
    """Construct the schema Event shape; no-op uses CommandCompleted instead."""
    _digest(command_digest)
    if type(reason) is not str or not reason:
        raise _fail("Event reason must be nonempty text")
    if change.migration_id is not None:
        provenance = _provenance(migration, "migration", base.definition_digest)
        if provenance["migration_id"] != change.migration_id:
            raise _fail("Change/provenance migration mismatch")
        expected = resolve_migration(base, change.candidate, migration_id=change.migration_id,
            source_mapping=change.source_mapping, exact_diff=provenance["exact_diff"])
    else:
        if migration is not None:
            raise _fail("Ordinary change cannot carry trusted provenance")
        expected = resolve_facts(base, change.candidate)
    if not _equal(expected.facts, change.facts) or not _equal(expected.tombstones, change.tombstones):
        raise _fail("Change does not belong to this base")
    if change.noop:
        return _frozen_object({"type": "CommandCompleted", "graph_id": base.definition.graph_id,
            "revision": base.revision, "command_digest": command_digest, "status": "noop"})
    revision = parse_counter(base.revision) + 1
    if revision > MAX_COUNTER:
        raise _fail("Revision counter exhausted", "limit_exceeded")
    return _frozen_object({"type": "GraphChangeCommitted", "graph_id": base.definition.graph_id,
        "semantic_version": "1", "base_revision": base.revision, "result_revision": str(revision),
        "before_digest": base.definition_digest, "after_digest": change.candidate.digest(),
        "resolved_facts": change.facts, "command_digest": command_digest, "reason": reason,
        **({"migration": migration} if migration is not None else {})})


def apply_event(base: RevisionState | UnseededState, event: object, *, tombstones: object) -> RevisionState:
    """Reduce a graph event only; release/receipt/journal projections are separate."""
    event = thaw_json(event)
    if not isinstance(event, dict):
        raise _fail("Expected graph event")
    if isinstance(base, UnseededState):
        return _apply_bootstrap(base, event, tombstones)
    if event.get("type") == "CommandCompleted":
        e = _object(event, "type graph_id revision command_digest status")
        _digest(e["command_digest"])
        if (e["graph_id"] != base.definition.graph_id or e["revision"] != base.revision
                or e["status"] != "noop" or not _equal(base.tombstones, tombstones)):
            raise _fail("No-op event disagrees with base")
        return base
    e = _object(event, "type graph_id semantic_version base_revision result_revision before_digest "
                "after_digest resolved_facts command_digest reason", "migration")
    if (e["type"] != "GraphChangeCommitted" or e["graph_id"] != base.definition.graph_id
            or e["semantic_version"] != "1" or e["base_revision"] != base.revision):
        raise _fail("Graph event identity, schema or revision mismatch")
    _digest(e["command_digest"])
    if type(e["reason"]) is not str or not e["reason"]:
        raise _fail("Invalid event reason")
    if not isinstance(e["resolved_facts"], list):
        raise _fail("Expected fact array")
    return apply_facts(base, cast(list[Mapping[str, object]], e["resolved_facts"]),
        tombstones=tombstones, revision=cast(str, e["result_revision"]),
        before_digest=cast(str, e["before_digest"]), after_digest=cast(str, e["after_digest"]),
        migration=e.get("migration"))