"""Ordinary graph protection and Host-verified admission values, without I/O.

Construct VerifiedContext/VerifiedApproval only in a trusted Host adapter after
authentication/proof verification. They are evidence ports, NOT wire credentials.
An Admission must consult current authority on every call; cached allow decisions
are not a revocation contract. The owner must fence admission and journal append.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Protocol

from .canonical import canonical_json, parse_counter, validate_id
from .errors import GraphDiagnostic, GraphError
from .events import RevisionState
from .index import GraphIndex
from .model import GraphDefinition, NodeRefPart, NodeSpec, PublicationContracts


def _deny(message: str, code: str = "mutation_protected", *, rule: str | None = None,
          node_id: str | None = None) -> GraphError:
    """Stable code plus one structured diagnostic naming the rule and object.

    `rule` is a cross-language stable diagnostic code; messages are prose only.
    """
    diagnostics = () if rule is None else (GraphDiagnostic(rule, message=message, node_id=node_id),)
    if node_id is not None:
        message = f"{message}: {node_id}"
    return GraphError(code, message, diagnostics=diagnostics)


@dataclass(frozen=True, slots=True)
class VerifiedContext:
    actor_id: str
    tenant_id: str
    authority_revision: str
    host_permissions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        validate_id(self.actor_id)
        validate_id(self.tenant_id)
        parse_counter(self.authority_revision)
        permissions = frozenset(self.host_permissions)
        for permission in permissions:
            validate_id(permission)
        object.__setattr__(self, "host_permissions", permissions)


@dataclass(frozen=True, slots=True)
class VerifiedApproval:
    """Host verified proof, scoped to actor, tenant, authority and exact preview."""

    actor_id: str
    tenant_id: str
    authority_revision: str
    subject_digest: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    context: VerifiedContext
    graph_id: str
    operation: str
    operations: tuple[str, ...]
    base_revision: str
    base_digest: str
    candidate_digest: str
    impact: tuple[str, ...]
    reference_targets: tuple[str, ...]
    approval_subject_digest: str | None
    approval: VerifiedApproval | None


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    allowed: bool
    authority_revision: str
    scope_ids: frozenset[str]
    approval_required: bool = False

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool or type(self.approval_required) is not bool:
            raise _deny("Admission booleans must be exact", "schema_invalid")
        parse_counter(self.authority_revision)
        scope = frozenset(self.scope_ids)
        for identity in scope:
            validate_id(identity)
        object.__setattr__(self, "scope_ids", scope)


class Admission(Protocol):
    def decide(self, request: AdmissionRequest) -> AdmissionDecision:
        """Decide current graph operation and separately referenceable targets."""
        ...


def check_admission(request: AdmissionRequest, admission: Admission) -> AdmissionDecision:
    decision = admission.decide(request)
    if not isinstance(decision, AdmissionDecision):
        raise _deny("Invalid admission evidence", "permission_denied")
    if decision.authority_revision != request.context.authority_revision:
        raise _deny("Authority changed during admission", "approval_stale")
    if not decision.allowed or not set((*request.impact, *request.reference_targets)) <= decision.scope_ids:
        raise _deny("Current admission denied or incomplete scope", "permission_denied")
    if decision.approval_required and request.approval is None:
        raise _deny("Verified approval required", "approval_stale")
    return decision


@dataclass(frozen=True, slots=True)
class ProtectionReport:
    """Admission input derived from the final base/candidate diff.

    `protected_dependents` lists locked nodes whose own definition is unchanged
    but whose effective agent view changes because a mutable member, use or
    template target changed. They are part of `impact` so admission/approval
    sees them; they are reported separately so previews can explain why.
    """

    impact: tuple[str, ...]
    reference_targets: tuple[str, ...]
    roots_changed: bool
    protected_dependents: tuple[str, ...] = ()


def _sequence(index: GraphIndex, parent: str | None) -> tuple[str, ...]:
    if parent is None:
        return tuple(r.node_id for r in index.definition.roots)
    members = index.definition.nodes[parent].members
    return tuple(n for _, _, n in members.placements()) if members else ()


def _locked_order(before: tuple[str, ...], after: tuple[str, ...], locked: set[str]) -> None:
    """Linear inversion test, ignoring inserted/removed members and ordinals."""
    ranks = {n: i for i, n in enumerate(after)}
    common = [n for n in before if n in ranks]
    maximum = -1
    for identity in common:
        rank = ranks[identity]
        if identity in locked and maximum > rank:
            raise _deny("Existing locked member changed relative order", rule="locked_order",
                        node_id=identity)
        maximum = max(maximum, rank)
    minimum = len(after)
    for identity in reversed(common):
        rank = ranks[identity]
        if identity in locked and minimum < rank:
            raise _deny("Existing member crossed a locked member", rule="locked_order",
                        node_id=identity)
        minimum = min(minimum, rank)


def _locked_members(node: NodeSpec, locked: AbstractSet[str]) -> object:
    """A locked Role owns only its locked members' presence; mutable ones are free."""
    assert node.members is not None
    m = node.members
    value: dict[str, object] = {slot: [n for n in getattr(m, slot) if n in locked]
                                for slot in ("children", "skills", "tools")}
    for slot in ("pre_process", "post_process"):
        member = getattr(m, slot)
        if member is not None and member in locked:
            value[slot] = member
    return value


@dataclass(frozen=True, slots=True)
class _Surface:
    """Per-node endpoint comparison values; equal bytes mean an unchanged surface.

    `own` is what a locked node protects: its own definition (with only locked
    members), path, plane, graph metadata, publications and presentations.
    `full` additionally includes mutable members, and `view` each direct
    member/use/template target's card, definition and publications, i.e. the
    agent-visible context reachable in one hop.
    """

    own: tuple[object, ...]
    full: bytes
    view: tuple[object, ...]


def _surfaces(index: GraphIndex, paths: dict[tuple[int, str], int],
              locked: AbstractSet[str]) -> dict[str, _Surface]:
    """Exact endpoint surfaces without repeated path rendering or global scans.

    Shared, call-local path tokens identify equal name sequences across both
    forests, not equal ancestors/IDs. They use no path strings or digest shortcut.
    Canonical bytes retain JCS numeric/object semantics; Python JSON equality
    would incorrectly equate e.g. True and 1. No authority decision is cached.
    """
    graph = index.definition
    tokens: dict[str, int] = {}
    cards: dict[str, tuple[object, ...]] = {}
    for identity in index.preorder:
        node = graph.nodes[identity]
        loc = index.locations[identity]
        key = (tokens[loc.parent_id] if loc.parent_id is not None else 0, node.name)
        token = paths.setdefault(key, len(paths) + 1)
        tokens[identity] = token
        cards[identity] = (identity, node.name, token, loc.plane, node.description, node.kind)
    publications: dict[str, list[bytes]] = {}
    for publication in graph.publications:
        publications.setdefault(publication.opens_id, []).append(canonical_json(publication.as_json()))
    presentations: dict[str, list[tuple[str, bytes]]] = {}
    for catalog in graph.presentations.catalogs:
        for entry in catalog.entries:
            presentations.setdefault(entry.node_id, []).append((catalog.locale, canonical_json(entry.as_json())))
    metadata = (graph.presentations.default_locale, graph.mode, canonical_json(graph.extensions))
    full = {identity: canonical_json(node.as_json()) for identity, node in graph.nodes.items()}
    result: dict[str, _Surface] = {}
    for identity, node in graph.nodes.items():
        refs = list(node.uses)
        if node.members:
            refs.extend(n for _, _, n in node.members.placements())
        if node.instruction_template:
            refs.extend(p.target_id for p in node.instruction_template.parts if isinstance(p, NodeRefPart))
        own = full[identity]
        if node.members is not None and identity in locked:
            own = canonical_json({**node.as_json(), "members": _locked_members(node, locked)})
        loc = index.locations[identity]
        own_surface = (own, tokens[identity], loc.parent_id, loc.slot, loc.plane,
            metadata, tuple(publications.get(identity, ())), tuple(presentations.get(identity, ())))
        # One hop of reachable context: each direct target's card, definition
        # and publications. Not transitive, so impact stays local.
        view = tuple((cards[n], full[n], tuple(publications.get(n, ()))) for n in refs)
        result[identity] = _Surface(own_surface, full[identity], view)
    return result


def _reordered(before: tuple[str, ...], after: tuple[str, ...]) -> set[str]:
    """Both endpoints of inversions; insertion/deletion ordinal shifts excluded."""
    ranks = {n: i for i, n in enumerate(after)}
    common = [n for n in before if n in ranks]
    result: set[str] = set()
    maximum = -1
    for identity in common:
        if ranks[identity] < maximum:
            result.add(identity)
        maximum = max(maximum, ranks[identity])
    minimum = len(after)
    for identity in reversed(common):
        if ranks[identity] > minimum:
            result.add(identity)
        minimum = min(minimum, ranks[identity])
    return result


def protect_change(base: RevisionState, candidate: GraphDefinition, *,
                   publication_contracts: PublicationContracts | None = None
                   ) -> ProtectionReport:
    """One final-diff gate shared by every ordinary mutation/lowering path."""
    # The base is accepted history; only the candidate must satisfy current evidence.
    old = GraphIndex.build(base.definition, verify_contracts=False)
    new = GraphIndex.build(candidate, publication_contracts=publication_contracts)
    a, b = base.definition.nodes, candidate.nodes
    # Locked = effectively immutable in the base. A locked node protects its own
    # definition, path, plane, publications/presentations and its locked
    # members' presence/order. Its effective agent view may change through
    # mutable members/uses/template targets; that is reported, not refused.
    locked = {n for n, loc in old.locations.items() if not loc.effective_mutable}
    paths: dict[tuple[int, str], int] = {}
    old_surfaces, new_surfaces = _surfaces(old, paths, locked), _surfaces(new, paths, locked)
    impact: set[str] = set(a.keys() ^ b.keys())
    refs: set[str] = set()
    dependents: set[str] = set()
    # Root promotion/demotion is host-gated (`graph.roots`) bootstrap authority
    # and legitimately re-derives inherited mutability of that whole subtree.
    rerooted: set[str] = set()
    for index, others in ((old, candidate.roots), (new, base.definition.roots)):
        other_ids = {r.node_id for r in others}
        for root in index.definition.roots:
            if root.node_id not in other_ids:
                loc = index.locations[root.node_id]
                rerooted.update(index.preorder[loc.preorder:loc.subtree_end])
    # Fixed check order and sorted identities give deterministic, most specific
    # diagnostics across implementations.
    deleted_locked = sorted(locked & (a.keys() - b.keys()))
    if deleted_locked:
        raise _deny("Deletion closure contains locked objects", rule="locked_deletion",
                    node_id=deleted_locked[0])
    for identity in sorted(a.keys() & b.keys()):
        before, after = a[identity], b[identity]
        if (before.kind, before.mutable, before.role_variant) != (
                after.kind, after.mutable, after.role_variant):
            raise _deny("Identity kind, variant and creation policy are immutable", "identity_conflict",
                        rule="creation_policy", node_id=identity)
        x, y = old.locations[identity], new.locations[identity]
        if (x.parent_id, x.slot) != (y.parent_id, y.slot):
            for index, parent in ((old, x.parent_id), (new, y.parent_id)):
                if parent is not None and index.locations[parent].false_barrier:
                    raise _deny("Move crosses a mutable=false barrier", rule="barrier_move",
                                node_id=identity)
    for identity in sorted(a.keys() & b.keys()):
        before, after = a[identity], b[identity]
        x, y = old.locations[identity], new.locations[identity]
        s0, s1 = old_surfaces[identity], new_surfaces[identity]
        own_changed = s0.own != s1.own
        changed = own_changed or s0.full != s1.full or s0.view != s1.view
        # A reorder changes existing members' relative positions even when
        # their node fields/path/plane are identical (handled below). Insertion
        # ordinal shifts alone do not add every following sibling to impact.
        if changed:
            impact.add(identity)
        if identity in locked:
            if own_changed:
                raise _deny("Change alters a locked object, its path, plane or surface",
                            rule="locked_surface", node_id=identity)
            if changed:
                dependents.add(identity)
        if before.uses != after.uses:
            refs.update(set(before.uses) | set(after.uses))
    for identity in sorted(a.keys() & b.keys()):
        if (old.locations[identity].effective_mutable != new.locations[identity].effective_mutable
                and identity not in rerooted):
            raise _deny("Change alters an existing object's effective mutability",
                        rule="effective_mutability", node_id=identity)
    for identity in sorted(b.keys() - a.keys()):
        parent = new.locations[identity].parent_id
        if parent is None or new.locations[parent].effective_mutable:
            continue
        if new.locations[parent].false_barrier:
            raise _deny("Creation under a mutable=false barrier", rule="barrier_create",
                        node_id=identity)
        if not new.locations[identity].effective_mutable:
            # Otherwise ordinary mutation could add a member nobody may remove.
            raise _deny("New member of a locked parent must be mutable", rule="locked_create",
                        node_id=identity)
    for parent in (None, *a.keys()):
        if parent is not None and parent not in b:
            continue
        before_seq, after_seq = _sequence(old, parent), _sequence(new, parent)
        _locked_order(before_seq, after_seq, locked)
        common = set(before_seq) & set(after_seq)
        before_order = [n for n in before_seq if n in common]
        after_order = [n for n in after_seq if n in common]
        # Mark both endpoints of every inversion in linear time.
        ranks = {n: i for i, n in enumerate(after_order)}
        maximum = -1
        for n in before_order:
            if ranks[n] < maximum:
                impact.add(n)
            maximum = max(maximum, ranks[n])
        minimum = len(after_order)
        for n in reversed(before_order):
            if ranks[n] > minimum:
                impact.add(n)
            minimum = min(minimum, ranks[n])
    # Publication positions are semantic. Ignore insertion ordinal shifts but
    # do not let an ordinary payload cross an existing locked entry.
    old_pubs = {p.publication_id: p.opens_id for p in base.definition.publications}
    new_pubs = {p.publication_id: p.opens_id for p in candidate.publications}
    _locked_order(tuple(old_pubs), tuple(new_pubs),
                  {p for p, target in old_pubs.items() if target in locked})
    reordered = _reordered(tuple(old_pubs), tuple(new_pubs))
    impact.update(old_pubs[p] for p in reordered)
    impact.update(new_pubs[p] for p in reordered)
    after_catalogs = {c.locale: c for c in candidate.presentations.catalogs}
    for locale in base.definition.presentations.catalogs:
        after_catalog = after_catalogs.get(locale.locale)
        if after_catalog is not None:
            _locked_order(tuple(e.node_id for e in locale.entries),
                          tuple(e.node_id for e in after_catalog.entries), locked)
            impact.update(_reordered(tuple(e.node_id for e in locale.entries),
                                     tuple(e.node_id for e in after_catalog.entries)))
    old_catalogs = {c.locale: {e.node_id for e in c.entries} for c in base.definition.presentations.catalogs}
    new_catalogs = {c.locale: {e.node_id for e in c.entries} for c in candidate.presentations.catalogs}
    _locked_order(tuple(old_catalogs), tuple(new_catalogs),
                  {locale for locale, ids in old_catalogs.items() if ids & locked})
    for locale in _reordered(tuple(old_catalogs), tuple(new_catalogs)):
        impact.update(old_catalogs[locale] | new_catalogs[locale])
    for graph in (base.definition, candidate):
        for identity, node in graph.nodes.items():
            if identity in impact:
                refs.update(node.uses)
                if node.instruction_template:
                    refs.update(p.target_id for p in node.instruction_template.parts
                                if isinstance(p, NodeRefPart))
        for publication in graph.publications:
            if publication.opens_id in impact:
                refs.add(publication.opens_id)
    roots_changed = base.definition.roots != candidate.roots
    return ProtectionReport(tuple(sorted(impact)), tuple(sorted(refs)), roots_changed,
                            tuple(sorted(dependents)))


def read_snapshot(state: RevisionState, *, context: VerifiedContext,
                  admission: Admission) -> RevisionState:
    """Return the immutable caller snapshot only after current full-read admission."""
    if not isinstance(context, VerifiedContext):
        raise _deny("Host verified context required", "permission_denied")
    # One immutable snapshot, one full hash. This is call-local, never an
    # authority/admission cache and never reused for a different endpoint.
    digest = state.definition_digest
    check_admission(AdmissionRequest(context, state.definition.graph_id, "graph.read", (),
        state.revision, digest, digest,
        tuple(sorted(state.definition.nodes)), (), None, None), admission)
    return state