"""Immutable in-memory historical projection, never authoritative persistence.

Start from an accepted seed/checkpoint. Journal verification, read authorization,
retention/quotas and transaction admission remain owner responsibilities. Revert
only proposes a new candidate; it never appends or rewinds an accepted head.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from rpds import List as PersistentList

from .canonical import FrozenJSON, freeze_json, parse_counter, validate_id
from .diff import DefinitionDiff, definition_diff
from .errors import GraphError
from .events import ResolvedChange, RevisionState, UnseededState, apply_event, resolve_facts
from .model import GraphDefinition, PublicationContract


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    journal_seq: str
    state: RevisionState
    event: Mapping[str, FrozenJSON] | None = None

    def __post_init__(self) -> None:
        if parse_counter(self.journal_seq) == 0 or not isinstance(self.state, RevisionState):
            raise GraphError("schema_invalid", "Historical record requires accepted sequence/state")
        if self.event is not None:
            object.__setattr__(self, "event", cast(Mapping[str, FrozenJSON], freeze_json(self.event)))


@dataclass(frozen=True, slots=True, init=False)
class History:
    """Persistent value: append returns a new projection, retaining every record.

    Sequence need only increase: intervening records may concern other graphs or
    releases. Contiguous store-wide chain checks belong to the journal reducer.
    A seed at revision N is an explicit retained lower bound, not invented N-1.
    """

    _entries: PersistentList[RevisionRecord] = field(repr=False)
    _seed: RevisionRecord = field(repr=False, compare=False)
    _cached_records: tuple[RevisionRecord, ...] | None = field(repr=False, compare=False)

    def __init__(self, records: tuple[RevisionRecord, ...]) -> None:
        normalized = tuple(records)
        if not normalized or any(not isinstance(r, RevisionRecord) for r in normalized):
            raise GraphError("schema_invalid", "History requires an accepted seed")
        for before, after in zip(normalized, normalized[1:]):
            if (parse_counter(after.journal_seq) <= parse_counter(before.journal_seq)
                    or after.state.definition.graph_id != before.state.definition.graph_id):
                raise GraphError("corrupt_facts", "Historical sequence or graph mismatch")
            if after.event is None:
                raise GraphError("corrupt_facts", "Only the retained seed may omit its event")
        object.__setattr__(self, "_entries", PersistentList(normalized[::-1]))
        object.__setattr__(self, "_seed", normalized[0])
        object.__setattr__(self, "_cached_records", normalized)

    @property
    def records(self) -> tuple[RevisionRecord, ...]:
        result = self._cached_records
        if result is None:
            result = tuple(reversed(tuple(self._entries)))
            object.__setattr__(self, "_cached_records", result)
        return result

    def __repr__(self) -> str:
        return f"History(records={self.records!r})"

    @classmethod
    def seed(cls, state: RevisionState, *, journal_seq: str) -> History:
        return cls((RevisionRecord(journal_seq, state),))

    @classmethod
    def bootstrap(cls, base: UnseededState, event: object, *, journal_seq: str,
                  publication_contracts: Mapping[str, PublicationContract] | None = None) -> History:
        """Retain the verified initial event, not an invented revision-zero forest."""
        state = apply_event(base, event, tombstones=[], publication_contracts=publication_contracts)
        frozen = freeze_json(event)
        if not isinstance(frozen, Mapping):
            raise GraphError("schema_invalid", "Expected bootstrap event object")
        return cls((RevisionRecord(journal_seq, state, frozen),))

    @property
    def head(self) -> RevisionState:
        return next(iter(self._entries)).state

    @property
    def retained_lower_bound(self) -> str:
        return self._seed.state.revision

    def append(self, event: object, *, journal_seq: str, tombstones: object,
               publication_contracts: Mapping[str, PublicationContract] | None = None) -> History:
        if parse_counter(journal_seq) <= parse_counter(next(iter(self._entries)).journal_seq):
            raise GraphError("corrupt_facts", "Historical sequence must increase")
        state = apply_event(self.head, event, tombstones=tombstones,
                            publication_contracts=publication_contracts)
        frozen = freeze_json(event)
        if not isinstance(frozen, Mapping):
            raise GraphError("schema_invalid", "Expected event object")
        record = RevisionRecord(journal_seq, state, frozen)
        if state.definition.graph_id != self.head.definition.graph_id:
            raise GraphError("corrupt_facts", "Historical graph mismatch")
        # The retained prefix is already validated and deeply immutable.
        # Public construction still validates arbitrary complete histories.
        result = object.__new__(History)
        object.__setattr__(result, "_entries", self._entries.push_front(record))
        object.__setattr__(result, "_seed", self._seed)
        object.__setattr__(result, "_cached_records", None)
        return result

    def at(self, revision: str) -> RevisionState:
        parse_counter(revision)
        for record in self._entries:
            if record.state.revision == revision:
                return record.state
        raise GraphError("history_unavailable", "Revision is outside retained verified history")

    def diff(self, before: str, after: str) -> DefinitionDiff:
        return definition_diff(self.at(before).definition, self.at(after).definition)

    def for_node(self, node_id: str) -> tuple[RevisionRecord, ...]:
        validate_id(node_id)
        result: list[RevisionRecord] = []
        for previous, record in zip(self.records, self.records[1:]):
            if _node_projection(previous.state, node_id) != _node_projection(record.state, node_id):
                result.append(record)
        return tuple(result)

    def for_publication(self, publication_id: str) -> tuple[RevisionRecord, ...]:
        validate_id(publication_id)
        def projection(state: RevisionState) -> object:
            return tuple((i, p.as_json()) for i, p in enumerate(state.definition.publications)
                         if p.publication_id == publication_id)
        return tuple(record for previous, record in zip(self.records, self.records[1:])
                     if projection(previous.state) != projection(record.state))


def _node_projection(state: RevisionState, identity: str) -> object:
    definition = state.definition
    node = definition.nodes.get(identity)
    placements: list[object] = [(None, i, r.plane) for i, r in enumerate(definition.roots)
                              if r.node_id == identity]
    for parent in definition.nodes.values():
        if parent.members is not None:
            placements.extend((parent.node_id, slot, i) for slot, i, child in parent.members.placements()
                              if child == identity)
    return (node.as_json() if node else None, placements,
        [(i, p.as_json()) for i, p in enumerate(definition.publications) if p.opens_id == identity],
        [(ci, c.locale, ei, e.as_json()) for ci, c in enumerate(definition.presentations.catalogs)
         for ei, e in enumerate(c.entries) if e.node_id == identity],
        definition.presentations.default_locale)


@dataclass(frozen=True, slots=True)
class RevertCandidate:
    base_revision: str
    target_revision: str
    change: ResolvedChange


def revert_candidate(base: RevisionState, target: RevisionState, *,
                     node_ids: tuple[str, ...] | None = None,
                     publication_contracts: Mapping[str, PublicationContract] | None = None) -> RevertCandidate:
    """Propose full or precisely node-scoped restoration, never authorize it.

    Scoped restoration copies exactly those nodes; it does not overwrite roots,
    parents, publications, locales or unrelated concurrent work. The caller must
    include relation owners explicitly. An incomplete closure is rejected by
    final validation. Global historical tombstones are never copied over base.
    """
    if base.definition.graph_id != target.definition.graph_id:
        raise GraphError("schema_invalid", "Revert target belongs to another graph")
    if parse_counter(target.revision) > parse_counter(base.revision):
        raise GraphError("history_unavailable", "Revert requires a retained past revision")
    wire = target.definition.as_json() if node_ids is None else base.definition.as_json()
    if node_ids is not None:
        if len(set(node_ids)) != len(node_ids):
            raise GraphError("schema_invalid", "Duplicate revert scope identity")
        nodes = {n: v.as_json() for n, v in base.definition.nodes.items()}
        for identity in node_ids:
            validate_id(identity)
            if identity in target.definition.nodes:
                nodes[identity] = target.definition.nodes[identity].as_json()
            elif identity in nodes:
                del nodes[identity]
            else:
                raise GraphError("reference_not_found", "Revert scope identity is absent in both revisions")
        wire["nodes"] = [nodes[n] for n in sorted(nodes)]
    definition = GraphDefinition.from_json(wire, publication_contracts=publication_contracts)
    retained = {cast(str, t["node_id"]) for t in base.tombstones}
    if any(n not in base.definition.nodes and n not in retained for n in definition.nodes):
        raise GraphError("history_unavailable", "Historical restoration requires retained identity tombstones")
    return RevertCandidate(base.revision, target.revision, resolve_facts(base, definition))