"""Bounded ordinary transaction kernel; candidate design API, not a wire release.

No filesystem/schema-file reads, registry resolution, eval, clock or durable store.
prepare -> preview -> admit_transition returns proposed facts only. A journal
owner MUST serialize, query authorized idempotent receipts first, CAS the head,
fence current authority, append facts+receipt atomically, and confirm durability
before publishing a RevisionState. No method here reports 'committed'.

Closed shape checks mirror the candidate managed schema in code. Scoped payloads
are complete candidates, with exact unchanged-outside projections, not patches.
Native JSON loses numeric lexemes; raw input must be UTF-8 bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from typing import Protocol, cast

from .canonical import (
    FrozenJSON, MAX_COUNTER, canonical_digest, canonical_json, decode_json,
    freeze_json, parse_counter, thaw_json, validate_id,
)
from .diff import DefinitionDiff, definition_diff
from .errors import GraphError
from .events import ResolvedChange, RevisionState, resolve_facts
from .index import GraphIndex
from .model import (
    BindingDescriptor, GraphDefinition, InstructionTemplate, NodeSpec, PublicationContract, RootEntry,
    _extensions, _name, _publication, normalize_locale,
)
from .policy import (
    Admission, AdmissionRequest, ProtectionReport, VerifiedApproval, VerifiedContext,
    check_admission, protect_change, read_snapshot,
)

Wire = dict[str, object]
FrozenObject = Mapping[str, FrozenJSON]
SLOTS = frozenset({"pre_process", "children", "post_process", "skills", "tools"})
SINGLE = frozenset({"pre_process", "post_process"})
UPDATES = frozenset({"description", "instructions", "instruction_template", "extensions",
                     "binding", "input_contract"})
UNSET = frozenset({"instructions", "instruction_template", "binding", "input_contract"})


def _fail(message: str, code: str = "schema_invalid") -> GraphError:
    return GraphError(code, message)


def _obj(value: object, required: str, optional: str = "") -> Wire:
    if not isinstance(value, Mapping) or any(type(k) is not str for k in value):
        raise _fail("Expected a closed object")
    keys = set(value)
    if not set(required.split()) <= keys or keys - set((required + " " + optional).split()):
        raise _fail("Missing required or unknown fields")
    return dict(value)


def _arr(value: object) -> list[object]:
    if not isinstance(value, (tuple, list)):
        raise _fail("Expected an array")
    return list(value)


def _ids(value: object) -> list[str]:
    ids = [validate_id(n) for n in _arr(value)]
    if len(set(ids)) != len(ids):
        raise _fail("Duplicate identity")
    return ids


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise _fail("Expected nonempty text")
    return value


def _frozen(value: object) -> FrozenObject:
    result = freeze_json(value)
    if not isinstance(result, Mapping):
        raise _fail("Expected JSON object")
    return result


def _timestamp(value: object) -> datetime:
    text = _text(value)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z", text) is None:
        raise _fail("Expected UTC timestamp ending Z")
    try:
        result = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise _fail("Invalid UTC timestamp") from None
    return result


@dataclass(frozen=True, slots=True)
class Limits:
    max_commands: int = 10000
    max_transaction_bytes: int = 16 * 1024 * 1024
    max_definition_bytes: int = 256 * 1024 * 1024
    max_resolved_bytes: int = 64 * 1024 * 1024
    max_nodes: int = 100000
    max_edges: int = 1000000
    max_depth: int = 1024
    max_node_bytes: int = 1024 * 1024
    max_expanded_operations: int = 2000000

    def __post_init__(self) -> None:
        for value in (self.max_commands, self.max_transaction_bytes, self.max_definition_bytes,
                      self.max_resolved_bytes, self.max_nodes, self.max_expanded_operations):
            if type(value) is not int or value <= 0:
                raise _fail("Limits must be positive integers")
        for value in (self.max_edges, self.max_depth, self.max_node_bytes):
            if type(value) is not int or value <= 0:
                raise _fail("Graph budgets must be positive integers")


class HistoryProvider(Protocol):
    def at(self, revision: str) -> RevisionState:
        """Host-authorized retained history, e.g. history.History.at; no invented IDs."""
        ...


class PayloadProvider(Protocol):
    def read(self, reference: FrozenObject) -> bytes:
        """Return exact immutable staged bytes; caller handles authorized blob access."""
        ...


@dataclass(frozen=True, slots=True)
class TransactionEnvelope:
    value: FrozenObject

    @classmethod
    def from_json(cls, value: object, *, limits: Limits = Limits()) -> TransactionEnvelope:
        if isinstance(value, bytes):
            if len(value) > limits.max_transaction_bytes:
                raise _fail("Inline transaction budget exceeded", "limit_exceeded")
            value = decode_json(value)
        value = thaw_json(value)
        obj = _obj(value, "schema graph_id base_revision idempotency_key commands reason correlation_id",
                   "approval_ref")
        if obj["schema"] != "contexture.transaction/v1":
            raise _fail("Ordinary transaction schema required; trusted envelopes are separate")
        for key in ("graph_id", "idempotency_key", "correlation_id"):
            validate_id(obj[key])
        if "approval_ref" in obj:
            validate_id(obj["approval_ref"])
        parse_counter(obj["base_revision"])
        _text(obj["reason"])
        commands = _arr(obj["commands"])
        if not 1 <= len(commands) <= limits.max_commands:
            raise _fail("Command count outside budget", "limit_exceeded")
        obj["commands"] = [_normalize_command(c) for c in commands]
        if len(canonical_json(obj)) > limits.max_transaction_bytes:
            raise _fail("Inline transaction budget exceeded", "limit_exceeded")
        return cls(_frozen(obj))

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _frozen(self.value))

    @property
    def command_digest(self) -> str:
        return canonical_digest({k: self.value[k] for k in
            ("schema", "graph_id", "base_revision", "commands", "reason")}, "contexture.command/v1")


def _normalize_command(value: object) -> Wire:
    if not isinstance(value, Mapping):
        raise _fail("Expected typed command")
    op = value.get("op")
    shapes = {
        "create_node": ("node", ""),
        "update_node": ("node_id set_fields unset_fields", ""),
        "rename_node": ("node_id new_name", ""),
        "attach_node": ("node_id target", ""), "move_node": ("node_id target", ""),
        "detach_node": ("node_id", ""), "remove_root": ("node_id", ""),
        "reorder_members": ("parent_id slot node_ids", ""),
        "add_root": ("node_id plane position", ""),
        "add_use": ("source_id target_id", "position"),
        "remove_use": ("source_id target_id", ""),
        "delete_nodes": ("targets policy deleted_ids disconnections", ""),
        "restore_nodes": ("revision node_ids", ""),
        "replace_subtree": ("anchor_id id_mapping payload", ""),
        "import": ("scope_ids id_mapping payload", ""),
        "reconcile": ("scope_ids id_mapping payload", ""),
        "put_publication": ("publication", ""),
        "remove_publication": ("publication_id", ""),
        "put_presentation": ("locale presentation", ""),
        "remove_presentation": ("locale node_id", ""),
    }
    if type(op) is not str or op not in shapes:
        raise _fail("Unknown ordinary command", "unsupported_contract")
    required, optional = shapes[op]
    obj = _obj(value, "op " + required, optional)
    for key in ("node_id", "parent_id", "source_id", "target_id", "anchor_id", "publication_id"):
        if key in obj:
            validate_id(obj[key])
    for key in ("node_ids", "scope_ids", "targets", "deleted_ids"):
        if key in obj:
            obj[key] = _ids(obj[key])
    if "revision" in obj and parse_counter(obj["revision"]) == 0:
        raise _fail("Restore requires a nonzero historical revision")
    if "position" in obj:
        _position(obj["position"], 2**53 - 1)
    if "target" in obj:
        target = obj["target"]
        if isinstance(target, Mapping) and "parent_id" in target:
            t = _obj(target, "parent_id slot position")
            validate_id(t["parent_id"])
            _slot(t["slot"])
        else:
            t = _obj(target, "plane position")
            _plane(t["plane"])
        _position(t["position"], 2**53 - 1)
        obj["target"] = t
    if "plane" in obj:
        _plane(obj["plane"])
    if "slot" in obj:
        _slot(obj["slot"])
    if op == "create_node":
        node = NodeSpec.from_json(obj["node"])
        if node.uses or (node.members and node.members.placements()):
            raise _fail("Create relations must be empty; use ordered relation commands")
        obj["node"] = node.as_json()
    if op == "update_node":
        fields = _obj(obj["set_fields"], "", " ".join(UPDATES))
        unset = _ids(obj["unset_fields"])
        if set(unset) - UNSET or set(fields) & set(unset):
            raise _fail("Invalid or conflicting update fields")
        # Strict local shape at decode; only cross-field/forest invariants may
        # remain temporarily invalid until the final candidate is validated.
        for field, raw in fields.items():
            if field == "description":
                _text(raw)
            elif field == "instructions" and type(raw) is not str:
                raise _fail("Instructions must be text")
            elif field == "instruction_template":
                fields[field] = InstructionTemplate.from_json(raw).as_json()
            elif field == "extensions":
                fields[field] = thaw_json(_extensions(raw))
            elif field == "binding":
                fields[field] = BindingDescriptor.from_json(raw).as_json()
            elif field == "input_contract":
                checked = NodeSpec.from_json({"node_id": "shape-check", "kind": "tool", "name": "shape-check",
                    "description": "shape-check", "input_contract": raw})
                fields[field] = thaw_json(checked.input_contract)
        obj["set_fields"] = fields
    if op == "rename_node":
        _name(obj["new_name"])
    if op == "put_publication":
        obj["publication"] = _publication(obj["publication"]).as_json()
    if "locale" in obj:
        obj["locale"] = normalize_locale(obj["locale"])
    if op == "put_presentation":
        p = _obj(obj["presentation"], "node_id", "title description")
        validate_id(p["node_id"])
        if len(p) == 1:
            raise _fail("Presentation requires human fields")
        for key in ("title", "description"):
            if key in p:
                _text(p[key])
    if "id_mapping" in obj:
        mapping = _obj(obj["id_mapping"], "retained_ids new_ids deleted_ids")
        obj["id_mapping"] = {key: _ids(ids) for key, ids in mapping.items()}
        payload = _obj(obj["payload"], "", "inline blob")
        if len(payload) != 1:
            raise _fail("Exactly one inline/blob payload required")
        if "blob" in payload:
            ref = _obj(payload["blob"], "digest size media_type")
            parse_counter(ref["size"])
            if ref["media_type"] != "application/json":
                raise _fail("Definition blob must contain canonical JSON")
            digest = _text(ref["digest"])
            if len(digest) != 71 or not digest.startswith("sha256:") or any(
                    c not in "0123456789abcdef" for c in digest[7:]):
                raise _fail("Invalid blob digest")
    if op == "delete_nodes":
        if not obj["targets"] or obj["policy"] not in ("reject_references", "explicit_cascade"):
            raise _fail("Explicit nonempty delete targets and policy required")
        obj["disconnections"] = [_disconnection(d) for d in _arr(obj["disconnections"])]
        encoded = [canonical_json(d) for d in cast(list[Wire], obj["disconnections"])]
        if len(set(encoded)) != len(encoded):
            raise _fail("Duplicate delete disconnection")
    if op == "restore_nodes" and not obj["node_ids"]:
        raise _fail("Restore scope cannot be empty")
    return obj


def _position(value: object, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise _fail("Insertion position is outside resulting sequence")
    return value


def _slot(value: object) -> str:
    if type(value) is not str or value not in SLOTS:
        raise _fail("Invalid typed member slot")
    return value


def _plane(value: object) -> str:
    if value not in ("model", "prompt"):
        raise _fail("Invalid root plane")
    return cast(str, value)


def _disconnection(value: object) -> Wire:
    obj = _obj(value, "source_id target_id relation")
    validate_id(obj["source_id"])
    validate_id(obj["target_id"])
    if obj["relation"] not in ("uses", "containment", "instruction_template", "publication", "presentation"):
        raise _fail("Invalid delete relation")
    return obj


def _closure(graph: GraphDefinition, seeds: list[str]) -> set[str]:
    result: set[str] = set()
    stack = [n for n in seeds if n in graph.nodes]
    while stack:
        identity = stack.pop()
        if identity in result:
            continue
        result.add(identity)
        members = graph.nodes[identity].members
        if members:
            stack.extend(n for _, _, n in members.placements())
    return result


def _check_scope(before: GraphDefinition, after: GraphDefinition,
                 seeds: list[str]) -> tuple[set[str], set[str]]:
    """Complete-candidate scope: exact before/after closure and outside equality.

    Boundary owners may change only containment edges incident to the scope.
    Their fields/uses/programs and outside-member relative order stay exact.
    Existing identities cannot enter/leave a scope by being moved across it;
    expand the explicit anchors or issue a separate ordered move/repair first.
    """
    if not seeds or any(n not in before.nodes and n not in after.nodes for n in seeds):
        raise _fail("Scope anchors must exist in at least one endpoint", "scope_escape")
    a, b = _closure(before, seeds), _closure(after, seeds)
    if ((b - a) & before.nodes.keys()) or ((a - b) & after.nodes.keys()):
        raise _fail("Existing identity crosses the explicit scope boundary", "scope_escape")
    whole = a == set(before.nodes) and b == set(after.nodes)
    old, new = before.as_json(), after.as_json()
    if before.application_name != after.application_name:
        raise _fail("Ordinary payload cannot rename application identity", "identity_conflict")
    for field in ("schema", "semantic_version", "graph_id", "mode", "extensions"):
        if canonical_json(old[field]) != canonical_json(new[field]) and (not whole or field in (
                "schema", "semantic_version", "graph_id")):
            raise _fail("Graph metadata lies outside the declared scope", "scope_escape")

    def outside_node(graph: GraphDefinition, identity: str, scope: set[str]) -> Wire:
        wire = graph.nodes[identity].as_json()
        if graph.nodes[identity].members:
            members = cast(Wire, wire["members"])
            for slot in SLOTS:
                if slot in SINGLE:
                    if members.get(slot) in scope:
                        members.pop(slot)
                else:
                    members[slot] = [n for n in cast(list[str], members[slot]) if n not in scope]
        return wire

    if before.nodes.keys() - a != after.nodes.keys() - b:
        raise _fail("Candidate changes identities outside scope", "scope_escape")
    for identity in before.nodes.keys() - a:
        if canonical_json(outside_node(before, identity, a)) != canonical_json(outside_node(after, identity, b)):
            raise _fail("Candidate changes an outside node or boundary reference", "scope_escape")
    for field, target in (("roots", "node_id"), ("publications", "opens_id")):
        left = [v for v in cast(list[Wire], old[field]) if v[target] not in a]
        right = [v for v in cast(list[Wire], new[field]) if v[target] not in b]
        if canonical_json(left) != canonical_json(right):
            raise _fail("Candidate changes outside placement/publication", "scope_escape")
    # A publication ID cannot be rebound from an outside target into the scope.
    old_pubs = {p.publication_id: p for p in before.publications}
    for pub in after.publications:
        previous = old_pubs.get(pub.publication_id)
        if previous and (previous.opens_id in a) != (pub.opens_id in b):
            raise _fail("Publication identity crosses scope", "scope_escape")
    if not whole:
        if before.presentations.default_locale != after.presentations.default_locale:
            raise _fail("Default locale lies outside partial node scope", "scope_escape")

        existing = {c.locale for c in before.presentations.catalogs}
        left = [{"locale": c.locale, "entries": [e.as_json() for e in c.entries if e.node_id not in a]}
            for c in before.presentations.catalogs]
        right = [{"locale": c.locale, "entries": [e.as_json() for e in c.entries if e.node_id not in b]}
             for c in after.presentations.catalogs if c.locale in existing]
        additions = [c for c in after.presentations.catalogs if c.locale not in existing]
        # New catalogs must contain only scoped entries; existing empty catalogs
        # and their relative order remain graph metadata, not a node grant.
        if (canonical_json(left) != canonical_json(right) or any(
            not c.entries or any(e.node_id not in b for e in c.entries) for c in additions)):
            raise _fail("Candidate changes outside presentation catalogs", "scope_escape")
    return a, b


class _Draft:
    def __init__(self, base: RevisionState, contracts: Mapping[str, PublicationContract] | None,
                 limits: Limits, history: HistoryProvider | None, payloads: PayloadProvider | None):
        self.base = base
        # Relation pools below own the detached draft. Do not retain a second
        # full serialized node pool merely to copy the graph-level metadata.
        graph = base.definition
        self.wire: Wire = {"schema": graph.schema, "semantic_version": graph.semantic_version,
            "graph_id": graph.graph_id, "application_name": graph.application_name,
            "mode": graph.mode, "extensions": graph.extensions}
        self.nodes = {n: v.as_json() for n, v in base.definition.nodes.items()}
        self.roots = [r.as_json() for r in base.definition.roots]
        self.pubs = [p.as_json() for p in base.definition.publications]
        self.catalogs = {c.locale: [e.as_json() for e in c.entries]
                         for c in base.definition.presentations.catalogs}
        self.default_locale = base.definition.presentations.default_locale
        self.deleted: set[str] = set()
        self.restored: set[str] = set()
        self.root_operations = False
        self.contracts, self.limits, self.history, self.payloads = contracts, limits, history, payloads

    def node(self, identity: object) -> Wire:
        name = validate_id(identity)
        if name not in self.nodes:
            raise _fail("Command target must already exist", "reference_not_found")
        return self.nodes[name]

    def template_targets(self, value: object) -> None:
        if not isinstance(value, Mapping):
            raise _fail("Expected instruction template")
        template = _obj(value, "parts")
        for raw in _arr(template["parts"]):
            if not isinstance(raw, Mapping):
                raise _fail("Expected typed instruction part")
            if raw.get("kind") == "NodeRef":
                part = _obj(raw, "kind target_id", "label")
                self.node(part["target_id"])
            elif raw.get("kind") == "Text":
                _obj(raw, "kind text")
            else:
                raise _fail("Unknown template part")

    def members(self, parent: object) -> Wire:
        node = self.node(parent)
        if node["kind"] != "role":
            raise _fail("Containment parent must be a Role")
        return cast(Wire, node["members"])

    def sequence(self, parent: object, slot: str) -> list[str]:
        members = self.members(parent)
        if slot in SINGLE:
            return [cast(str, members[slot])] if slot in members else []
        return cast(list[str], members[slot])

    def assign(self, parent: object, slot: str, ids: list[str]) -> None:
        members = self.members(parent)
        if slot in SINGLE:
            if len(ids) > 1:
                raise _fail("Singleton member slot is occupied")
            members.pop(slot, None)
            if ids:
                members[slot] = ids[0]
        else:
            members[slot] = ids

    def placements(self, identity: str) -> list[tuple[str | None, str, int]]:
        found: list[tuple[str | None, str, int]] = [
            (None, "roots", i) for i, r in enumerate(self.roots) if r["node_id"] == identity]
        for parent, node in self.nodes.items():
            if node["kind"] == "role":
                for slot in SLOTS:
                    found.extend((parent, slot, i) for i, n in enumerate(self.sequence(parent, slot))
                                 if n == identity)
        if len(found) > 1:
            raise _fail("Draft has multiple containment owners")
        return found

    def detach(self, identity: str, *, required: bool = True, root_only: bool = False) -> None:
        self.node(identity)
        placements = self.placements(identity)
        if not placements:
            if required:
                raise _fail("Node has no placement")
            return
        parent, slot, position = placements[0]
        if root_only and parent is not None:
            raise _fail("remove_root target is not a root")
        if parent is None:
            self.roots.pop(position)
            self.root_operations = True
        else:
            ids = list(self.sequence(parent, slot))
            ids.pop(position)
            self.assign(parent, slot, ids)

    def attach(self, identity: str, target: Wire) -> None:
        self.node(identity)
        if self.placements(identity):
            raise _fail("attach requires an unowned draft object")
        if "parent_id" not in target:
            RootEntry.from_json({"node_id": identity, "plane": target["plane"]})
            position = _position(target["position"], len(self.roots))
            self.roots.insert(position, {"node_id": identity, "plane": target["plane"]})
            self.root_operations = True
        else:
            parent, slot = target["parent_id"], _slot(target["slot"])
            ids = list(self.sequence(parent, slot))
            position = _position(target["position"], len(ids))
            ids.insert(position, identity)
            self.assign(parent, slot, ids)

    def definition(self) -> GraphDefinition:
        wire = dict(self.wire)
        wire.update(nodes=list(self.nodes.values()), roots=self.roots, publications=self.pubs,
                    presentations={"default_locale": self.default_locale,
                                   "catalogs": [{"locale": k, "entries": v}
                                                for k, v in self.catalogs.items()]})
        if len(self.nodes) > self.limits.max_nodes or len(canonical_json(wire)) > self.limits.max_definition_bytes:
            raise _fail("Candidate exceeds graph budget", "limit_exceeded")
        definition = GraphDefinition.from_json(wire, publication_contracts=self.contracts)
        index = GraphIndex.build(definition, publication_contracts=self.contracts)
        edges = len(definition.roots) + len(definition.publications)
        for identity, node in definition.nodes.items():
            edges += len(node.uses) + (len(node.members.placements()) if node.members else 0)
            edges += len(node.instruction_template.parts) if node.instruction_template else 0
            if (index.locations[identity].depth > self.limits.max_depth or
                    len(canonical_json(node.as_json())) > self.limits.max_node_bytes):
                raise _fail("Node size/depth exceeds transaction validation budget", "limit_exceeded")
        if edges > self.limits.max_edges:
            raise _fail("Graph relation budget exceeded", "limit_exceeded")
        return definition

    def apply(self, command: Wire) -> None:
        op = command["op"]
        identity = cast(str, command.get("node_id"))
        if op == "create_node":
            node = NodeSpec.from_json(command["node"])
            retained = {cast(str, t["node_id"]) for t in self.base.tombstones}
            if node.node_id in self.nodes or node.node_id in self.deleted or node.node_id in retained:
                raise _fail("Identity reuse requires explicit historical restore", "identity_conflict")
            if node.instruction_template:
                self.template_targets(node.instruction_template.as_json())
            self.nodes[node.node_id] = node.as_json()
        elif op in ("update_node", "rename_node"):
            node = self.node(identity)
            if op == "rename_node":
                node["name"] = command["new_name"]
            else:
                fields = cast(Wire, command["set_fields"])
                if "instruction_template" in fields:
                    self.template_targets(fields["instruction_template"])
                for key in cast(list[str], command["unset_fields"]):
                    node.pop(key, None)
                node.update(fields)
        elif op in ("attach_node", "move_node", "add_root"):
            if op == "move_node":
                self.detach(identity)
            target = ({"plane": command["plane"], "position": command["position"]}
                      if op == "add_root" else cast(Wire, command["target"]))
            self.attach(identity, target)
        elif op in ("detach_node", "remove_root"):
            self.detach(identity, root_only=op == "remove_root")
        elif op == "reorder_members":
            parent, slot = command["parent_id"], _slot(command["slot"])
            current, ids = self.sequence(parent, slot), _ids(command["node_ids"])
            if set(current) != set(ids):
                raise _fail("Reorder requires exactly the existing member set")
            self.assign(parent, slot, ids)
        elif op in ("add_use", "remove_use"):
            source = self.node(command["source_id"])
            self.node(command["target_id"])
            ids = cast(list[str], source["uses"])
            target = validate_id(command["target_id"])
            if op == "add_use":
                if target in ids or command["source_id"] == target:
                    raise _fail("Duplicate or self use")
                ids.insert(_position(command.get("position", len(ids)), len(ids)), target)
            else:
                if target not in ids:
                    raise _fail("Use does not exist")
                ids.remove(target)
        elif op == "delete_nodes":
            self.delete(command)
        elif op == "restore_nodes":
            self.restore(command)
        elif op in ("replace_subtree", "import", "reconcile"):
            self.replace(command)
        elif op == "put_publication":
            pub = _publication(command["publication"]).as_json()
            self.node(pub["opens_id"])
            found = next((i for i, p in enumerate(self.pubs) if p["publication_id"] == pub["publication_id"]), None)
            if found is None:
                self.pubs.append(pub)
            else:
                self.pubs[found] = pub
        elif op == "remove_publication":
            found = next((i for i, p in enumerate(self.pubs) if p["publication_id"] == command["publication_id"]), None)
            if found is None:
                raise _fail("Publication does not exist")
            self.pubs.pop(found)
        elif op in ("put_presentation", "remove_presentation"):
            locale = cast(str, command["locale"])
            if locale not in self.catalogs:
                if op == "remove_presentation":
                    raise _fail("Presentation does not exist")
                self.catalogs[locale] = []
            entries = self.catalogs[locale]
            entry: Wire = cast(Wire, command["presentation"]) if op == "put_presentation" else {"node_id": identity}
            self.node(entry["node_id"])
            found = next((i for i, e in enumerate(entries) if e["node_id"] == entry["node_id"]), None)
            if op == "remove_presentation":
                if found is None:
                    raise _fail("Presentation does not exist")
                entries.pop(found)
            elif found is None:
                entries.append(entry)
                # Preserve retained order; appended entries are fact-normalized below.
            else:
                entries[found] = entry
        if len(self.nodes) > self.limits.max_nodes:
            raise _fail("Node budget exceeded", "limit_exceeded")

    def delete(self, command: Wire) -> None:
        targets, deleted = set(_ids(command["targets"])), set(_ids(command["deleted_ids"]))
        closure: set[str] = set()
        stack = list(targets)
        while stack:
            identity = stack.pop()
            if identity in closure:
                continue
            node = self.node(identity)
            closure.add(identity)
            if node["kind"] == "role":
                stack.extend(n for slot in SLOTS for n in self.sequence(identity, slot))
        if deleted != closure:
            raise _fail("deleted_ids must exactly equal the target subtree closure")
        disconnections: list[Wire] = []

        def relation(source: str, target: str, kind: str) -> None:
            disconnections.append({"source_id": source, "target_id": target, "relation": kind})

        for identity in sorted(deleted):
            for parent, _, _ in self.placements(identity):
                if parent not in deleted:
                    relation(parent if parent is not None else self.base.definition.graph_id,
                             identity, "containment")
        for source, node in self.nodes.items():
            if source in deleted:
                continue
            for target in cast(list[str], node["uses"]):
                if target in deleted:
                    relation(source, target, "uses")
            template = node.get("instruction_template")
            if isinstance(template, Mapping):
                for part in _arr(template["parts"]):
                    if isinstance(part, Mapping) and part.get("kind") == "NodeRef" and part["target_id"] in deleted:
                        # Do not silently rewrite programs. Repair explicitly before delete.
                        raise _fail("Repair external NodeRef programs explicitly before delete")
        for pub in self.pubs:
            if pub["opens_id"] in deleted:
                relation(cast(str, pub["publication_id"]), cast(str, pub["opens_id"]), "publication")
        for locale, entries in self.catalogs.items():
            if any(e["node_id"] in deleted for e in entries):
                # Schema Disconnection cannot distinguish locale catalogs. D25
                # requires explicit translation removal, not implicit cascade.
                raise _fail("Remove target presentations explicitly before delete: " + locale)
        expected = {canonical_json(d) for d in disconnections}
        supplied = {canonical_json(d) for d in _arr(command["disconnections"])}
        if expected != supplied:
            raise _fail("Disconnections must exactly match all boundary references")
        if command["policy"] == "reject_references" and any(
                d["relation"] != "containment" for d in disconnections):
            raise _fail("Delete target has external references")
        for identity in sorted(deleted):
            self.detach(identity, required=False)
        for node in self.nodes.values():
            node["uses"] = [n for n in cast(list[str], node["uses"]) if n not in deleted]
        self.pubs = [p for p in self.pubs if p["opens_id"] not in deleted]
        for identity in deleted:
            del self.nodes[identity]
        self.deleted.update(deleted)

    def restore(self, command: Wire) -> None:
        if self.history is None:
            raise _fail("Authorized retained history provider required", "history_unavailable")
        revision = cast(str, command["revision"])
        if parse_counter(revision) > parse_counter(self.base.revision):
            raise _fail("Restore requires a past accepted revision", "history_unavailable")
        past = self.history.at(revision)
        if (past.revision != revision or past.definition.graph_id != self.base.definition.graph_id):
            raise _fail("History provider returned a different graph or revision", "history_unavailable")
        retained = {cast(str, t["node_id"]): t for t in self.base.tombstones}
        for identity in _ids(command["node_ids"]):
            historical = past.definition.nodes.get(identity)
            if identity in self.nodes or historical is None:
                raise _fail("Restore requires absent current and present historical identity", "identity_conflict")
            evidence = retained.get(identity)
            original = self.base.definition.nodes.get(identity) if identity in self.deleted else None
            if evidence is None and original is None:
                raise _fail("Restore identity has no retained tombstone", "history_unavailable")
            kind = original.kind if original else evidence["kind"] if evidence else None
            mutable = original.mutable if original else evidence["mutable"] if evidence else None
            if (historical.kind, historical.mutable) != (kind, mutable):
                raise _fail("Historical identity creation policy mismatch", "identity_conflict")
            self.nodes[identity] = historical.as_json()
            self.restored.add(identity)
        # Historical relationships are exact, not automatic publication/locale
        # restoration. All missing members/uses must be restored in this batch.

    def replace(self, command: Wire) -> None:
        payload = cast(Wire, command["payload"])
        if "blob" in payload:
            ref = _frozen(payload["blob"])
            size = parse_counter(ref["size"])
            if size > self.limits.max_definition_bytes:
                raise _fail("Staged definition exceeds budget", "limit_exceeded")
            if self.payloads is None:
                raise _fail("Authorized immutable payload provider required", "unsupported_contract")
            data = self.payloads.read(ref)
            if not isinstance(data, bytes) or len(data) != size or (
                    "sha256:" + hashlib.sha256(data).hexdigest() != ref["digest"]):
                raise _fail("Payload bytes disagree with immutable reference")
            raw = decode_json(data)
            if canonical_json(raw) != data:
                raise _fail("Definition blob must contain exact canonical bytes")
        else:
            raw = payload["inline"]
        if len(canonical_json(raw)) > self.limits.max_definition_bytes:
            raise _fail("Definition payload exceeds budget", "limit_exceeded")
        candidate = GraphDefinition.from_json(raw, publication_contracts=self.contracts)
        if candidate.graph_id != self.base.definition.graph_id:
            raise _fail("Cross-graph replacement is not an ordinary transaction")
        # Compare with this command's draft, not the transaction's initial head.
        # Earlier explicit boundary repairs remain legal; this payload itself
        # cannot silently repair or overwrite an unrelated relation owner.
        before = self.definition()
        seeds = ([validate_id(command["anchor_id"])] if command["op"] == "replace_subtree"
                 else _ids(command["scope_ids"]))
        if command["op"] == "replace_subtree":
            self.node(seeds[0])
        old_scope, new_scope = _check_scope(before, candidate, seeds)
        old_ids, new_ids = set(self.nodes), set(candidate.nodes)
        mapping = cast(Wire, command["id_mapping"])
        expected = {"retained_ids": old_scope & new_scope, "new_ids": new_scope - old_scope,
                    "deleted_ids": old_scope - new_scope}
        if any(set(_ids(mapping[key])) != ids for key, ids in expected.items()):
            raise _fail("ID mapping is not the exact retained/new/deleted partition")
        if len(old_scope | new_scope) > self.limits.max_expanded_operations:
            raise _fail("Replacement expansion budget exceeded", "limit_exceeded")
        retained = {cast(str, t["node_id"]) for t in self.base.tombstones}
        if (new_ids - old_ids) & (retained | self.deleted):
            raise _fail("Replacement cannot resurrect deleted identities", "identity_conflict")
        for identity in old_ids & new_ids:
            old = NodeSpec.from_json(self.nodes[identity])
            new = candidate.nodes[identity]
            if (old.kind, old.mutable, old.role_variant) != (new.kind, new.mutable, new.role_variant):
                raise _fail("Replacement cannot reset creation identity", "identity_conflict")
        self.deleted.update(old_ids - new_ids)
        self.root_operations |= self.roots != [r.as_json() for r in candidate.roots]
        self.wire = candidate.as_json()
        self.nodes = {n: v.as_json() for n, v in candidate.nodes.items()}
        self.roots = [r.as_json() for r in candidate.roots]
        self.pubs = [p.as_json() for p in candidate.publications]
        self.catalogs = {c.locale: [e.as_json() for e in c.entries]
                         for c in candidate.presentations.catalogs}
        self.default_locale = candidate.presentations.default_locale


@dataclass(frozen=True, slots=True)
class PreparedTransaction:
    context: VerifiedContext
    envelope: TransactionEnvelope
    base: RevisionState
    change: ResolvedChange
    definition_diff: DefinitionDiff
    protection: ProtectionReport
    operations: tuple[str, ...]
    root_operations: bool
    revert_revision: str | None = None
    revert_scope: tuple[str, ...] | None = None

    @property
    def approval_subject(self) -> FrozenObject:
        return _frozen({"schema": "contexture.preview/v1", "graph_id": self.base.definition.graph_id,
            "base_revision": self.base.revision, "base_digest": self.base.definition_digest,
            "candidate_digest": self.change.candidate.digest(), "command_digest": self.envelope.command_digest,
            "resolved_fact_digest": self.change.facts_digest})

    @property
    def approval_subject_digest(self) -> str:
        return canonical_digest(self.approval_subject, "contexture.preview/v1")


@dataclass(frozen=True, slots=True)
class Preview:
    prepared: PreparedTransaction
    context: VerifiedContext
    expires_at: str

    def as_json(self) -> Wire:
        p = self.prepared
        subject = p.approval_subject
        return {"schema": "contexture.preview-receipt/v1", **{key: subject[key] for key in (
            "base_revision", "base_digest", "candidate_digest", "command_digest", "resolved_fact_digest")},
            "definition_diff": p.definition_diff.as_json(), "impact": list(p.protection.impact),
            "protected_dependents": list(p.protection.protected_dependents),
            "validation_report": [], "approval_subject": thaw_json(subject), "expires_at": self.expires_at}


@dataclass(frozen=True, slots=True)
class AdmittedTransition:
    """Proposed journal input, never a committed state or durable receipt.

    Owner must retain context/authority fencing until append and revalidate if
    head or authority changes. No-op needs durable CommandCompleted + receipt.
    """

    preview: Preview
    context: VerifiedContext
    result_revision: str
    status: str = "admitted"

    @property
    def change(self) -> ResolvedChange:
        return self.preview.prepared.change


def prepare(base: RevisionState, envelope: object, *, context: VerifiedContext,
            admission: Admission, history: HistoryProvider | None = None,
            payloads: PayloadProvider | None = None,
            publication_contracts: Mapping[str, PublicationContract] | None = None,
            limits: Limits = Limits()) -> PreparedTransaction:
    """Authorize snapshot, construct detached draft, validate final and protect.

    Output is prepared, not admission to append. Reconstructs public envelope
    values, so manually constructing TransactionEnvelope cannot bypass decoding.
    """
    read_snapshot(base, context=context, admission=admission)
    request = TransactionEnvelope.from_json(envelope.value if isinstance(envelope, TransactionEnvelope)
                                            else envelope, limits=limits)
    if request.value["graph_id"] != base.definition.graph_id or request.value["base_revision"] != base.revision:
        raise _fail("Transaction does not match accepted base", "revision_conflict")
    draft = _Draft(base, publication_contracts, limits, history, payloads)
    commands = cast(tuple[FrozenObject, ...], request.value["commands"])
    for command in commands:
        draft.apply(cast(Wire, thaw_json(command)))
    candidate = draft.definition()
    tombstones = {cast(str, t["node_id"]) for t in base.tombstones}
    additions = candidate.nodes.keys() - base.definition.nodes.keys()
    if (additions & tombstones) - draft.restored:
        raise _fail("Only history-backed restore may reuse retained identity", "identity_conflict")
    protection = protect_change(base, candidate, publication_contracts=publication_contracts)
    if (draft.root_operations or protection.roots_changed) and "graph.roots" not in context.host_permissions:
        raise _fail("Host graph.roots authority required", "permission_denied")
    change = resolve_facts(base, candidate)
    if len(canonical_json(change.facts)) > limits.max_resolved_bytes:
        raise _fail("Resolved facts exceed atomic batch budget", "limit_exceeded")
    return PreparedTransaction(context, request, base, change, definition_diff(base.definition, candidate),
        protection, tuple(cast(str, command["op"]) for command in commands), draft.root_operations)


def prepare_revert(base: RevisionState, target_revision: str, *, context: VerifiedContext,
                   admission: Admission, history: HistoryProvider,
                   node_ids: tuple[str, ...] | None = None, idempotency_key: str,
                   reason: str, correlation_id: str,
                   publication_contracts: Mapping[str, PublicationContract] | None = None,
                   limits: Limits = Limits()) -> PreparedTransaction:
    """Prepare an ordinary historical candidate, never append or claim commit.

    Explicit node scope follows history.revert_candidate: include all relation
    owners needed for a valid candidate; unrelated current work stays current.
    Historical access must be authorized by the supplied Host history port.
    Admission re-reads this evidence, retaining current tombstones, not target's.
    """
    from .history import revert_candidate

    read_snapshot(base, context=context, admission=admission)
    target = history.at(target_revision)
    if target.revision != target_revision:
        raise _fail("History provider returned a different revision", "history_unavailable")
    proposed = revert_candidate(base, target, node_ids=node_ids,
                                publication_contracts=publication_contracts)
    candidate = proposed.change.candidate
    seeds = list(node_ids) if node_ids is not None else sorted(base.definition.nodes.keys() | candidate.nodes.keys())
    a, b = _check_scope(base.definition, candidate, seeds)
    mapping = {"retained_ids": sorted(a & b), "new_ids": sorted(b - a), "deleted_ids": sorted(a - b)}
    request = TransactionEnvelope.from_json({"schema": "contexture.transaction/v1",
        "graph_id": base.definition.graph_id, "base_revision": base.revision,
        "idempotency_key": idempotency_key, "reason": reason, "correlation_id": correlation_id,
        "commands": [{"op": "reconcile", "scope_ids": seeds, "id_mapping": mapping,
                      "payload": {"inline": candidate.as_json()}}]}, limits=limits)
    # Reuse the full graph budget validator without bypassing the ordinary
    # protection gate. Only history-backed restoration can reuse tombstones.
    draft = _Draft(base, publication_contracts, limits, history, None)
    draft.wire = candidate.as_json()
    draft.nodes = {n: v.as_json() for n, v in candidate.nodes.items()}
    draft.roots = [r.as_json() for r in candidate.roots]
    draft.pubs = [p.as_json() for p in candidate.publications]
    draft.catalogs = {c.locale: [e.as_json() for e in c.entries] for c in candidate.presentations.catalogs}
    draft.default_locale = candidate.presentations.default_locale
    candidate = draft.definition()
    if len(a | b) > limits.max_expanded_operations:
        raise _fail("Revert expansion budget exceeded", "limit_exceeded")
    protection = protect_change(base, candidate, publication_contracts=publication_contracts)
    if protection.roots_changed and "graph.roots" not in context.host_permissions:
        raise _fail("Host graph.roots authority required", "permission_denied")
    change = resolve_facts(base, candidate)
    if len(canonical_json(change.facts)) > limits.max_resolved_bytes:
        raise _fail("Resolved facts exceed atomic batch budget", "limit_exceeded")
    return PreparedTransaction(context, request, base, change, definition_diff(base.definition, candidate),
        protection, ("revert",), protection.roots_changed, target_revision, node_ids)


def _approval(proof: VerifiedApproval | None, p: PreparedTransaction,
              context: VerifiedContext, now: str) -> None:
    if proof is not None and (not isinstance(proof, VerifiedApproval) or
            (proof.actor_id, proof.tenant_id, proof.authority_revision, proof.subject_digest) != (
                context.actor_id, context.tenant_id, context.authority_revision, p.approval_subject_digest)
            or _timestamp(proof.expires_at) <= _timestamp(now)):
        raise _fail("Approval does not bind current actor/authority/exact preview", "approval_stale")


def _request(p: PreparedTransaction, context: VerifiedContext, operation: str,
             proof: VerifiedApproval | None) -> AdmissionRequest:
    if (p.root_operations or p.protection.roots_changed) and "graph.roots" not in context.host_permissions:
        raise _fail("Current Host graph.roots permission required", "permission_denied")
    return AdmissionRequest(context, p.base.definition.graph_id, operation, p.operations,
        p.base.revision, p.base.definition_digest, p.change.candidate.digest(), p.protection.impact,
        p.protection.reference_targets, p.approval_subject_digest, proof)


def preview(prepared: PreparedTransaction, *, context: VerifiedContext, admission: Admission,
            now: str, expires_at: str, approval: VerifiedApproval | None = None) -> Preview:
    if not isinstance(context, VerifiedContext) or context != prepared.context:
        raise _fail("Prepared snapshot belongs to another verified context", "permission_denied")
    read_snapshot(prepared.base, context=context, admission=admission)
    if _timestamp(expires_at) <= _timestamp(now):
        raise _fail("Preview expiry must be in the future", "approval_stale")
    _approval(approval, prepared, context, now)
    check_admission(_request(prepared, context, "graph.preview", approval), admission)
    return Preview(prepared, context, expires_at)


def admit_transition(receipt: Preview, current: RevisionState, *, context: VerifiedContext,
                     admission: Admission, now: str, approval: VerifiedApproval | None = None,
                     history: HistoryProvider | None = None, payloads: PayloadProvider | None = None,
                     publication_contracts: Mapping[str, PublicationContract] | None = None,
                     limits: Limits = Limits()) -> AdmittedTransition:
    """Owner-serialized pre-append recheck. Does NOT write or advance any head."""
    if not isinstance(context, VerifiedContext) or context != receipt.context:
        raise _fail("Actor, authority or verified context changed; re-preview required", "approval_stale")
    if _timestamp(receipt.expires_at) <= _timestamp(now):
        raise _fail("Preview expired", "approval_stale")
    old = receipt.prepared
    if (current.revision != old.base.revision or current.definition_digest != old.base.definition_digest
            or canonical_json(current.tombstones) != canonical_json(old.base.tombstones)):
        raise _fail("Current revision/definition/identity history changed", "revision_conflict")
    # Rebuild from current accepted head rather than trust editable/forged receipt
    # internals. Re-resolve historical and staged evidence under current Host.
    if old.revert_revision is not None:
        if history is None:
            raise _fail("Authorized retained history provider required", "history_unavailable")
        fresh = prepare_revert(current, old.revert_revision, context=context, admission=admission,
            history=history, node_ids=old.revert_scope,
            idempotency_key=cast(str, old.envelope.value["idempotency_key"]),
            reason=cast(str, old.envelope.value["reason"]),
            correlation_id=cast(str, old.envelope.value["correlation_id"]),
            publication_contracts=publication_contracts, limits=limits)
    else:
        fresh = prepare(current, old.envelope, context=context, admission=admission, history=history,
                        payloads=payloads, publication_contracts=publication_contracts, limits=limits)
    if (fresh.approval_subject != old.approval_subject or fresh.protection != old.protection
            or fresh.definition_diff != old.definition_diff or fresh.operations != old.operations
            or fresh.root_operations != old.root_operations):
        raise _fail("Preview no longer describes the complete transaction", "approval_stale")
    _approval(approval, fresh, context, now)
    check_admission(_request(fresh, context, "graph.transition", approval), admission)
    number = parse_counter(current.revision) + (not fresh.change.noop)
    if number > MAX_COUNTER:
        raise _fail("Revision counter exhausted", "limit_exceeded")
    return AdmittedTransition(Preview(fresh, context, receipt.expires_at), context, str(number))