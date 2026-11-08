"""Synchronous managed graph owner; storage and authority are external ports.

This module intentionally imports no server adapter. The application package
preserves lazy declaration exports; managed orchestration is an explicit import.
One RLock serializes graph and registered release appends. Async adapters must
dispatch complete calls, not hold this thread lock across await boundaries.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime
import re
from threading import RLock, get_ident
from typing import Any, Protocol, cast
import weakref

from contexture.core.constants import PACKAGE_VERSION
from contexture.core.graph.canonical import (
    FrozenJSON, canonical_digest, canonical_json, decode_json, freeze_json,
    parse_counter, thaw_json, validate_id,
)
from contexture.core.graph.errors import GraphError
from contexture.core.graph.events import (
    RevisionState, UnseededState, bootstrap_event, change_event,
)
from contexture.core.graph.bundles import (
    GraphBundle, HistoryBundle, export_bundle, export_history, plan_import,
)
from contexture.core.graph.history import History, apply_graph_event as _apply_graph_event
from contexture.core.graph.merge import MergeResult, merge
from contexture.core.graph.index import GraphIndex
from contexture.core.graph.model import GraphDefinition, PublicationContracts, contract_lookup
from contexture.core.graph.policy import (
    Admission, AdmissionRequest, VerifiedApproval, VerifiedContext,
    check_admission, read_snapshot,
)
from contexture.core.graph.transactions import (
    Limits, PayloadProvider, Preview, TransactionEnvelope, admit_transition,
    prepare, prepare_revert, preview as make_preview,
)

_LOG = logging.getLogger("contexture.managed.owner")


Wire = dict[str, Any]
FrozenObject = Mapping[str, FrozenJSON]


class JournalPort(Protocol):
    """An exclusively opened, durable journal, structurally met by FileJournal."""

    durability: str

    @property
    def watermark(self) -> str: ...

    def records(self, *, after: str = "0") -> Sequence[Mapping[str, Any]]: ...

    def get_receipt(self, actor_ref: str, idempotency_key: str) -> Wire | None: ...

    def append(self, events: Sequence[Mapping[str, Any]], *, actor_ref: str,
               command_receipt: Mapping[str, Any] | None = None,
               correlation_id: str | None = None, event_batch_id: str | None = None,
               written_at: str | None = None) -> Wire: ...


class IdentityProvider(Protocol):
    def current(self) -> VerifiedContext:
        """Obtain the current Host-authenticated identity, never request payload."""
        ...

    def actor_ref(self, context: VerifiedContext) -> str:
        """Stable store-wide tenant+actor reference, not authority version/token."""
        ...


class AuthorityFence(Protocol):
    def hold(self, context: VerifiedContext) -> AbstractContextManager[None]:
        """Hold the Host's independent authority fence through decision+append.

        Admission.decide alone is NOT an atomic policy/storage transaction.
        A remote decision-only adapter cannot satisfy this stronger contract.
        """
        ...


class ApprovalProvider(Protocol):
    def verify(self, context: VerifiedContext, reference: str,
               subject_digest: str, now: str) -> VerifiedApproval: ...


@dataclass(frozen=True, slots=True)
class EventProjection:
    """Registered pure reducer; replay must not authorize or perform effects.

    Shared release state can be registered under one name with multiple tags.
    State is deeply frozen before and after each call. Every accepted event is
    delivered in record/event order, including old-boot observations.
    """

    event_types: frozenset[str]
    initial: FrozenObject
    reduce: Callable[[FrozenObject, FrozenObject, FrozenObject], FrozenObject]


@dataclass(frozen=True, slots=True)
class BootstrapPreview:
    context: VerifiedContext
    request: FrozenObject
    definition: GraphDefinition
    request_digest: str
    expires_at: str


def _error(code: str, message: str) -> GraphError:
    return GraphError(code, message)


def _freeze(value: object) -> FrozenObject:
    result = freeze_json(value)
    if not isinstance(result, Mapping):
        raise _error("schema_invalid", "Expected JSON object")
    return result


def _time(value: str) -> datetime:
    if not isinstance(value, str) or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z", value) is None:
        raise _error("schema_invalid", "Expected UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise _error("schema_invalid", "Invalid UTC timestamp") from None


_SNAPSHOT = "owner-snapshot"


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """A verified, anchored owner state; disposable acceleration, not authority."""

    sequence: int
    histories: dict[str, History]
    external: dict[str, FrozenObject]


_bindings_lock = RLock()
_bindings: dict[int, weakref.ReferenceType[GraphOwner]] = {}
_GRAPH_EVENTS = frozenset({"GraphChangeCommitted", "CommandCompleted"})


class _AuthorizedHistory:
    def __init__(self, owner: GraphOwner, graph_id: str, context: VerifiedContext):
        self.owner, self.graph_id, self.context = owner, graph_id, context

    def at(self, revision: str) -> RevisionState:
        history = self.owner._history(self.graph_id, full=True)
        read_snapshot(history.head, context=self.context, admission=self.owner._admission)
        return read_snapshot(history.at(revision), context=self.context,
                             admission=self.owner._admission)


class GraphOwner:
    """Single owner over an external store, with immutable replayed projections.

    No filesystem access, memory-store fallback, clock default, identity payload
    or policy-fence default. The caller retains the journal lifetime/OS lock.
    close() detaches this manager only; reopen the journal after append failure.
    """

    def __init__(self, journal: JournalPort, *, identities: IdentityProvider,
                 admission: Admission, authority_fence: AuthorityFence,
                 clock: Callable[[], str], approvals: ApprovalProvider | None = None,
                 payloads: PayloadProvider | None = None,
                 publication_contracts: PublicationContracts | None = None,
                 limits: Limits = Limits(),
                 projections: Mapping[str, EventProjection] | None = None,
                 use_snapshots: bool = True):
        self._lock = RLock()
        self._operation_thread: int | None = None
        self._journal, self._identities, self._admission = journal, identities, admission
        self._fence, self._clock, self._approvals = authority_fence, clock, approvals
        self._payloads, self._limits = payloads, limits
        # Validated once; indexed by exact binding descriptor, not node identity.
        self._contracts = (None if publication_contracts is None
                           else contract_lookup(publication_contracts))
        self._projections = dict(projections or {})
        self._tags: dict[str, str] = {}
        for name, projection in self._projections.items():
            validate_id(name)
            for tag in projection.event_types:
                validate_id(tag)
                if tag in _GRAPH_EVENTS or tag in self._tags:
                    raise _error("schema_invalid", "Projection event ownership overlaps")
                self._tags[tag] = name
        self._closed = False
        self._stopped = False
        with _bindings_lock:
            previous = _bindings.get(id(journal))
            if previous is not None and previous() is not None:
                raise _error("store_in_use", "Journal already has a graph owner")
            # Replay has no policy, registry, approval or release effects. A
            # verified snapshot (see checkpoint()) lets startup apply only the
            # tail; older history is materialized from the journal on demand.
            records = journal.records()
            snapshot = self._load_snapshot(records) if use_snapshots else None
            self._partial: set[str] = set() if snapshot is None else set(snapshot.histories)
            self._snapshot_seq = 0 if snapshot is None else snapshot.sequence
            self._histories, self._external, self._receipts = self._replay(records, snapshot=snapshot)
            self._watermark = journal.watermark
            if len(records) != parse_counter(self._watermark):
                raise _error("journal_corrupt", "Journal changed during owner construction")
            self._store_id = records[0]["store_id"] if records else None
            self._first_record_hash = records[0]["record_hash"] if records else None
            self._last_record_hash = records[-1]["record_hash"] if records else None
            _bindings[id(journal)] = weakref.ref(self)

    @property
    def writes_stopped(self) -> bool:
        with self._lock:
            return self._stopped or self._closed

    def close(self) -> None:
        with self._lock, _bindings_lock:
            self._closed = True
            ref = _bindings.get(id(self._journal))
            if ref is not None and ref() is self:
                del _bindings[id(self._journal)]

    def _ready(self, *, write: bool = False) -> None:
        if self._closed:
            raise _error("resource_busy", "Graph owner is closed")
        if self._journal.watermark != self._watermark:
            self._stopped = True
            raise _error("commit_unknown", "Journal advanced outside owner serialization")
        if write and self._stopped:
            raise _error("commit_unknown", "Writes and release publication require recovery")

    @contextmanager
    def _authorized(self, *, write: bool = False) -> Iterator[VerifiedContext]:
        with self._lock:
            # RLock still serializes other threads. Reject callback reentry on
            # this owner before readiness, identity or any other external port.
            if self._operation_thread == get_ident():
                raise _error("resource_busy", "Graph owner operation is already active")
            self._operation_thread = get_ident()
            try:
                self._ready(write=write)
                context = self._identities.current()
                if not isinstance(context, VerifiedContext):
                    raise _error("permission_denied", "Current verified Host identity required")
                with self._fence.hold(context):
                    yield context
            finally:
                self._operation_thread = None

    def _history(self, graph_id: str, *, full: bool = False) -> History:
        validate_id(graph_id)
        if graph_id not in self._histories:
            raise _error("history_unavailable", "Graph has no accepted definition")
        if full and graph_id in self._partial:
            self._histories[graph_id] = self._full_history(graph_id)
            self._partial.discard(graph_id)
        return self._histories[graph_id]

    def _full_history(self, graph_id: str) -> History:
        """Rebuild a snapshot-seeded graph's complete history from the journal."""
        histories: dict[str, History] = {}
        for sequence, raw in enumerate(self._journal.records(), 1):
            if sequence > parse_counter(self._watermark):
                break
            record = _freeze(raw)
            for event in cast(tuple[FrozenObject, ...], record.get("events", ())):
                if event.get("type") in _GRAPH_EVENTS and event.get("graph_id") == graph_id:
                    _apply_graph_event(histories, event, sequence)
        rebuilt = histories.get(graph_id)
        current = self._histories[graph_id]
        if rebuilt is None or rebuilt.head.definition_digest != current.head.definition_digest:
            raise _error("journal_corrupt", "Full history disagrees with the snapshot-seeded head")
        return rebuilt

    def _load_snapshot(self, records: Sequence[Mapping[str, Any]]) -> _Snapshot | None:
        read = getattr(self._journal, "read_hints", None)
        if read is None or not records:
            return None
        for _, data in read(_SNAPSHOT):
            try:
                raw = decode_json(data)
                if not isinstance(raw, dict) or raw.get("schema") != "contexture.owner-snapshot/v1":
                    continue
                value = cast(dict[str, Any], raw)
                sequence = parse_counter(value["journal_seq"])
                if (not 1 <= sequence <= len(records) or value.get("package") != PACKAGE_VERSION
                        or records[sequence - 1]["record_hash"] != value["record_hash"]
                        or records[0]["store_id"] != value["store_id"]
                        or sorted(value["external"]) != sorted(self._projections)):
                    continue
                histories: dict[str, History] = {}
                for item in value["graphs"]:
                    definition = GraphDefinition.from_json(item["definition"], verify_contracts=False)
                    state = RevisionState(definition, item["revision"], tuple(item["tombstones"]))
                    if (state.definition_digest != item["definition_digest"]
                            or parse_counter(item["journal_seq"]) > sequence):
                        raise _error("journal_corrupt", "Snapshot graph state mismatch")
                    histories[definition.graph_id] = History.seed(state, journal_seq=item["journal_seq"])
                external = {name: _freeze(state) for name, state in value["external"].items()}
                return _Snapshot(sequence, histories, external)
            except (GraphError, KeyError, TypeError, ValueError) as exc:
                _LOG.warning("ignoring invalid owner snapshot; full replay",
                             extra={"error_code": getattr(exc, "code", type(exc).__name__)})
        return None

    def checkpoint(self) -> Wire:
        """Persist an anchored snapshot so the next startup replays only the tail.

        Disposable acceleration data (journal hints), verified against the
        record chain on load and ignored on any doubt; never authority. Requires
        current read admission of every graph it captures.
        """
        with self._authorized(write=True) as context:
            write = getattr(self._journal, "write_hint", None)
            if write is None:
                raise _error("unsupported_contract", "Journal does not support snapshots")
            if self._watermark == "0":
                raise _error("not_ready", "Nothing to snapshot")
            graphs = []
            for graph_id in sorted(self._histories):
                head = self._histories[graph_id].head
                read_snapshot(head, context=context, admission=self._admission)
                graphs.append({"graph_id": graph_id, "revision": head.revision,
                    "journal_seq": self._histories[graph_id].head_journal_seq,
                    "definition": head.definition.as_json(),
                    "definition_digest": head.definition_digest,
                    "tombstones": [thaw_json(t) for t in head.tombstones]})
            value = {"schema": "contexture.owner-snapshot/v1", "package": PACKAGE_VERSION,
                "store_id": self._store_id, "journal_seq": self._watermark,
                "record_hash": self._last_record_hash, "graphs": graphs,
                "external": {name: thaw_json(state) for name, state in sorted(self._external.items())}}
            data = canonical_json(value)
            write(_SNAPSHOT, self._watermark, data)
            self._snapshot_seq = parse_counter(self._watermark)
            _LOG.info("owner snapshot written", extra={"journal_seq": self._watermark,
                      "bytes": len(data)})
            return {"schema": "contexture.owner-snapshot-receipt/v1", "journal_seq": self._watermark,
                    "digest": canonical_digest(value, "contexture.command/v1")}

    @property
    def records_since_snapshot(self) -> int:
        """Accepted records not covered by the newest snapshot (0 = none needed)."""
        return parse_counter(self._watermark) - self._snapshot_seq

    def _replay(self, records: Sequence[Mapping[str, Any]], *, incremental: bool = False,
                snapshot: _Snapshot | None = None) -> tuple[
            dict[str, History], dict[str, FrozenObject], dict[tuple[str, str], FrozenObject]]:
        # Immutable graph/external values are staged in detached maps.
        # Incremental receipt output is only a delta, published under _lock.
        histories: dict[str, History] = (dict(self._histories) if incremental else
                                         {} if snapshot is None else dict(snapshot.histories))
        external = (dict(self._external) if incremental else
                    {name: _freeze(p.initial) for name, p in self._projections.items()}
                    if snapshot is None else dict(snapshot.external))
        covered = 0 if snapshot is None or incremental else snapshot.sequence
        receipts: dict[tuple[str, str], FrozenObject] = {}
        store_id: object = self._store_id if incremental else None
        previous: object = self._last_record_hash if incremental else None
        start = parse_counter(self._watermark) + 1 if incremental else 1
        for sequence, raw in enumerate(records, start):
            record = _freeze(raw)
            if (record.get("journal_seq") != str(sequence) or
                    record.get("prev_record_hash") != previous):
                raise _error("journal_corrupt", "Replay sequence or chain discontinuity")
            if sequence == 1:
                store_id = record.get("store_id")
            if record.get("store_id") != store_id:
                raise _error("journal_corrupt", "Replay store identity changed")
            previous = record.get("record_hash")
            events = record.get("events")
            if not isinstance(events, tuple) or not events:
                raise _error("journal_corrupt", "Complete event batch required")
            for value in events:
                if not isinstance(value, Mapping):
                    raise _error("schema_invalid", "Expected JSON object")
                event = cast(FrozenObject, value)
                tag = event.get("type")
                if tag in _GRAPH_EVENTS:
                    if sequence > covered:
                        _apply_graph_event(histories, event, sequence)
                elif isinstance(tag, str) and tag in self._tags:
                    if sequence > covered:
                        name = self._tags[tag]
                        external[name] = _freeze(self._projections[name].reduce(
                            external[name], event, record))
                else:
                    raise _error("unsupported_contract", "Unregistered required event projection")
            receipt = record.get("command_receipt")
            if receipt is not None:
                if not isinstance(receipt, Mapping):
                    raise _error("schema_invalid", "Expected JSON object")
                receipt = cast(FrozenObject, receipt)
                key = (validate_id(receipt.get("actor_ref")), validate_id(receipt.get("idempotency_key")))
                if (key in receipts or (incremental and key in self._receipts)
                        or receipt.get("journal_seq") != str(sequence) or key[0] != record["actor_ref"]):
                    raise _error("journal_corrupt", "Receipt ownership or sequence mismatch")
                receipts[key] = receipt
        return histories, external, receipts

    def _actor(self, context: VerifiedContext) -> str:
        return validate_id(self._identities.actor_ref(context))

    def _prior(self, context: VerifiedContext, graph_id: str, key: str,
               digest: str) -> Wire | None:
        receipt = self._receipts.get((self._actor(context), validate_id(key)))
        if receipt is None:
            return None
        # Current read permission, not historical mutation approval, controls
        # returning an already accepted result. This intentionally precedes CAS.
        read_snapshot(self._history(str(receipt["graph_id"])).head,
                      context=context, admission=self._admission)
        if receipt["request_digest"] != digest or receipt["graph_id"] != graph_id:
            raise _error("idempotency_conflict", "Actor/key already accepted another request")
        return cast(Wire, thaw_json(receipt))

    def _proof(self, context: VerifiedContext, reference: object,
               subject: str, now: str, *, required: bool = False) -> VerifiedApproval | None:
        if reference is None and not required:
            return None
        if self._approvals is None or reference is None:
            raise _error("approval_stale", "Host verified exact approval required")
        proof = self._approvals.verify(context, validate_id(reference), subject, now)
        if (not isinstance(proof, VerifiedApproval) or
                (proof.actor_id, proof.tenant_id, proof.authority_revision, proof.subject_digest) !=
                (context.actor_id, context.tenant_id, context.authority_revision, subject) or
                _time(proof.expires_at) <= _time(now)):
            raise _error("approval_stale", "Approval does not bind current actor/authority/subject")
        return proof

    def _append(self, event: FrozenObject, *, context: VerifiedContext,
                graph_id: str, key: str, digest: str, revision: str,
                correlation_id: str) -> Wire:
        sequence = str(parse_counter(self._watermark) + 1)
        status = "noop" if event["type"] == "CommandCompleted" else "committed"
        receipt = {"schema": "contexture.command-receipt/v1", "graph_id": graph_id,
            "actor_ref": self._actor(context), "idempotency_key": key,
            "request_digest": digest, "revision": revision, "journal_seq": sequence,
            "status": status, "durability": self._journal.durability,
            "operation_result": {"inline": {"graph_id": graph_id, "revision": revision,
                "definition_digest": event.get("after_digest",
                    self._histories[graph_id].head.definition_digest if graph_id in self._histories else None)}}}
        self._persist((event,), context=context, receipt=receipt, correlation_id=correlation_id)
        return cast(Wire, thaw_json(self._receipts[(self._actor(context), key)]))

    def _persist(self, events: Sequence[Mapping[str, Any]], *, context: VerifiedContext,
                 receipt: Mapping[str, Any] | None = None, correlation_id: str) -> None:
        self._ready(write=True)
        receipt_updates: dict[tuple[str, str], FrozenObject] = {}
        try:
            record = self._journal.append(events, actor_ref=self._actor(context),
                command_receipt=receipt, correlation_id=correlation_id, written_at=self._clock())
            if record["journal_seq"] != str(parse_counter(self._watermark) + 1):
                raise _error("commit_unknown", "Unexpected append result sequence")
            tail = self._journal.records(after=self._watermark)
            if len(tail) != 1 or canonical_json(tail[0]) != canonical_json(record):
                raise _error("commit_unknown", "Confirmed append differs from journal suffix")
            histories, external, receipt_updates = self._replay(tail, incremental=True)
            watermark = self._journal.watermark
            if watermark != record["journal_seq"]:
                raise _error("commit_unknown", "Unexpected append watermark")
            store_id = tail[0]["store_id"] if self._store_id is None else self._store_id
            first_hash = tail[0]["record_hash"] if self._store_id is None else self._first_record_hash
            last_hash = tail[0]["record_hash"]
            for key, value in receipt_updates.items():
                self._receipts[key] = value
        except BaseException as exc:
            self._stopped = True
            _LOG.error("graph owner writes stopped; close and reopen to recover",
                       extra={"error_code": getattr(exc, "code", type(exc).__name__)})
            # These keys were proven absent from the accepted prefix. Undo even
            # an index allocation failure after partial publication.
            for key in receipt_updates:
                dict.pop(self._receipts, key, None)
            # Conservative even for certain pre-publication errors: no blind
            # retry, release swap, or second sequence until close+recover.
            raise
        self._histories, self._external = histories, external
        self._watermark = watermark
        self._store_id, self._first_record_hash, self._last_record_hash = store_id, first_hash, last_hash

    def read(self, graph_id: str, revision: str | None = None) -> RevisionState:
        with self._authorized() as context:
            history = self._history(graph_id, full=revision is not None)
            read_snapshot(history.head, context=context, admission=self._admission)
            return read_snapshot(history.head if revision is None else history.at(revision),
                                 context=context, admission=self._admission)

    def export(self, graph_id: str, revision: str | None = None) -> bytes:
        return canonical_json(self.read(graph_id, revision).definition.as_json())

    def history(self, graph_id: str) -> History:
        with self._authorized() as context:
            history = self._history(graph_id, full=True)
            for record in history.records:
                read_snapshot(record.state, context=context, admission=self._admission)
            return history

    def receipt(self, graph_id: str, idempotency_key: str) -> Wire | None:
        with self._authorized() as context:
            read_snapshot(self._history(graph_id).head, context=context, admission=self._admission)
            value = self._receipts.get((self._actor(context), validate_id(idempotency_key)))
            if value is None or value["graph_id"] != graph_id:
                return None
            return cast(Wire, thaw_json(value))

    def preview(self, envelope: object, *, expires_at: str) -> Preview:
        with self._authorized(write=True) as context:
            request = TransactionEnvelope.from_json(envelope, limits=self._limits)
            graph_id = cast(str, request.value["graph_id"])
            prepared = prepare(self._history(graph_id).head, request, context=context,
                admission=self._admission, history=_AuthorizedHistory(self, graph_id, context),
                payloads=self._payloads, publication_contracts=self._contracts, limits=self._limits)
            now = self._clock()
            proof = self._proof(context, request.value.get("approval_ref"),
                                prepared.approval_subject_digest, now)
            return make_preview(prepared, context=context, admission=self._admission,
                                now=now, expires_at=expires_at, approval=proof)

    def preview_revert(self, graph_id: str, target_revision: str, *,
                       idempotency_key: str, reason: str, correlation_id: str,
                       expires_at: str, node_ids: tuple[str, ...] | None = None,
                       approval_ref: str | None = None) -> Preview:
        with self._authorized(write=True) as context:
            prepared = prepare_revert(self._history(graph_id).head, target_revision,
                context=context, admission=self._admission,
                history=_AuthorizedHistory(self, graph_id, context), node_ids=node_ids,
                idempotency_key=idempotency_key, reason=reason, correlation_id=correlation_id,
                publication_contracts=self._contracts, limits=self._limits)
            now = self._clock()
            proof = self._proof(context, approval_ref, prepared.approval_subject_digest, now)
            return make_preview(prepared, context=context, admission=self._admission,
                                now=now, expires_at=expires_at, approval=proof)

    def commit(self, receipt: Preview, *, approval_ref: str | None = None) -> Wire:
        with self._authorized(write=True) as context:
            if not isinstance(receipt, Preview):
                raise _error("schema_invalid", "Owner preview required")
            prepared = receipt.prepared
            # Reparse before idempotency lookup; don't trust constructed values.
            request = TransactionEnvelope.from_json(prepared.envelope.value, limits=self._limits)
            graph_id, key = str(request.value["graph_id"]), str(request.value["idempotency_key"])
            prior = self._prior(context, graph_id, key, request.command_digest)
            if prior is not None:
                return prior
            if self._actor(receipt.context) != self._actor(context):
                raise _error("permission_denied", "Preview belongs to another actor")
            now = self._clock()
            proof = self._proof(context, approval_ref or request.value.get("approval_ref"),
                                prepared.approval_subject_digest, now)
            transition = admit_transition(receipt, self._history(graph_id).head,
                context=context, admission=self._admission, now=now, approval=proof,
                history=_AuthorizedHistory(self, graph_id, context), payloads=self._payloads,
                publication_contracts=self._contracts, limits=self._limits)
            event = change_event(transition.preview.prepared.base, transition.change,
                command_digest=request.command_digest, reason=str(request.value["reason"]))
            return self._append(event, context=context, graph_id=graph_id, key=key,
                digest=request.command_digest, revision=transition.result_revision,
                correlation_id=str(request.value["correlation_id"]))

    def _bootstrap_request(self, envelope: object) -> tuple[FrozenObject, GraphDefinition, str]:
        if isinstance(envelope, bytes):
            if len(envelope) > self._limits.max_transaction_bytes:
                raise _error("limit_exceeded", "Bootstrap envelope exceeds budget")
            envelope = decode_json(envelope)
        request = _freeze(envelope)
        required = {"schema", "graph_id", "expected_revision", "seed", "approval_ref",
                    "idempotency_key", "reason", "correlation_id"}
        if set(request) != required or request["schema"] != "contexture.bootstrap/v1" or request["expected_revision"] != "0":
            raise _error("schema_invalid", "Closed trusted bootstrap envelope required")
        for key in ("graph_id", "approval_ref", "idempotency_key", "correlation_id"):
            validate_id(request[key])
        if not isinstance(request["reason"], str) or not request["reason"]:
            raise _error("schema_invalid", "Bootstrap reason required")
        if len(canonical_json(request)) > self._limits.max_transaction_bytes:
            raise _error("limit_exceeded", "Bootstrap envelope exceeds budget")
        payload = _freeze(request["seed"])
        if set(payload) == {"inline"}:
            raw = payload["inline"]
        elif set(payload) == {"blob"}:
            reference = _freeze(payload["blob"])
            if set(reference) != {"digest", "size", "media_type"} or reference["media_type"] != "application/json":
                raise _error("schema_invalid", "Canonical definition blob required")
            size = parse_counter(reference["size"])
            if size > self._limits.max_definition_bytes:
                raise _error("limit_exceeded", "Bootstrap definition exceeds budget")
            if self._payloads is None:
                raise _error("unsupported_contract", "Authorized immutable payload port required")
            data = self._payloads.read(reference)
            import hashlib
            if type(data) is not bytes or len(data) != size or "sha256:" + hashlib.sha256(data).hexdigest() != reference["digest"]:
                raise _error("schema_invalid", "Definition blob bytes disagree with reference")
            raw = decode_json(data)
            if canonical_json(raw) != data:
                raise _error("schema_invalid", "Definition blob must be canonical")
        else:
            raise _error("schema_invalid", "Exactly one definition payload required")
        definition = GraphDefinition.from_json(raw, publication_contracts=self._contracts)
        if definition.graph_id != request["graph_id"]:
            raise _error("identity_conflict", "Bootstrap graph identity mismatch")
        self._budget(definition)
        if len(canonical_json([{"type": "GraphSeeded", "before": None,
                                 "after": definition.as_json()}])) > self._limits.max_resolved_bytes:
            raise _error("limit_exceeded", "Bootstrap resolved facts exceed atomic budget")
        subject = {"schema": "contexture.bootstrap/v1", "graph_id": definition.graph_id,
                   "expected_revision": "0", "seed_digest": definition.digest(), "reason": request["reason"]}
        return request, definition, canonical_digest(subject, "contexture.command/v1")

    def _budget(self, definition: GraphDefinition) -> None:
        limits = self._limits
        if len(definition.nodes) > limits.max_nodes or len(canonical_json(definition.as_json())) > limits.max_definition_bytes:
            raise _error("limit_exceeded", "Definition exceeds graph budget")
        index = GraphIndex.build(definition, publication_contracts=self._contracts)
        edges = len(definition.roots) + len(definition.publications)
        for identity, node in definition.nodes.items():
            edges += len(node.uses) + (len(node.members.placements()) if node.members else 0)
            edges += len(node.instruction_template.parts) if node.instruction_template else 0
            if index.locations[identity].depth > limits.max_depth or len(canonical_json(node.as_json())) > limits.max_node_bytes:
                raise _error("limit_exceeded", "Definition exceeds node budget")
        if edges > limits.max_edges:
            raise _error("limit_exceeded", "Definition exceeds edge budget")

    def _bootstrap_admit(self, context: VerifiedContext, request: FrozenObject,
                         definition: GraphDefinition, digest: str, now: str,
                         operation: str) -> None:
        if "graph.bootstrap" not in context.host_permissions:
            raise _error("permission_denied", "Independent Host bootstrap permission required")
        proof = self._proof(context, request["approval_ref"], digest, now, required=True)
        check_admission(AdmissionRequest(context, definition.graph_id, operation, ("bootstrap",),
            "0", UnseededState(definition.graph_id).definition_digest, definition.digest(),
            tuple(sorted(definition.nodes)), (), digest, proof), self._admission)

    def preview_bootstrap(self, envelope: object, *, expires_at: str) -> BootstrapPreview:
        with self._authorized(write=True) as context:
            request, definition, digest = self._bootstrap_request(envelope)
            if definition.graph_id in self._histories:
                raise _error("revision_conflict", "Bootstrap requires unseeded graph")
            now = self._clock()
            if _time(expires_at) <= _time(now):
                raise _error("approval_stale", "Preview expiry must be in the future")
            self._bootstrap_admit(context, request, definition, digest, now, "graph.bootstrap.preview")
            return BootstrapPreview(context, request, definition, digest, expires_at)

    def commit_bootstrap(self, receipt: BootstrapPreview) -> Wire:
        with self._authorized(write=True) as context:
            if not isinstance(receipt, BootstrapPreview):
                raise _error("schema_invalid", "Bootstrap preview required")
            request, definition, digest = self._bootstrap_request(receipt.request)
            key = str(request["idempotency_key"])
            prior = self._prior(context, definition.graph_id, key, digest)
            if prior is not None:
                return prior
            if context != receipt.context or digest != receipt.request_digest or definition != receipt.definition:
                raise _error("approval_stale", "Bootstrap preview no longer matches")
            if definition.graph_id in self._histories:
                raise _error("revision_conflict", "Bootstrap requires unseeded graph")
            now = self._clock()
            if _time(receipt.expires_at) <= _time(now):
                raise _error("approval_stale", "Bootstrap preview expired")
            self._bootstrap_admit(context, request, definition, digest, now, "graph.bootstrap")
            event = bootstrap_event(UnseededState(definition.graph_id), definition,
                command_digest=digest, reason=str(request["reason"]), approval_ref=str(request["approval_ref"]))
            if len(canonical_json(event["resolved_facts"])) > self._limits.max_resolved_bytes:
                raise _error("limit_exceeded", "Bootstrap resolved facts exceed atomic budget")
            return self._append(event, context=context, graph_id=definition.graph_id, key=key,
                digest=digest, revision="1", correlation_id=str(request["correlation_id"]))

    # -- portable bundles and merge (no business semantics) -------------

    def export_bundle(self, graph_id: str, *, revision: str | None = None,
                      node_ids: Sequence[str] | None = None) -> GraphBundle:
        """Whole graph or containment subtrees of a retained revision."""
        state = self.read(graph_id, revision)
        return export_bundle(state, node_ids=node_ids, producer={"package": PACKAGE_VERSION})

    def export_history(self, graph_id: str) -> HistoryBundle:
        """Every accepted event of one graph from bootstrap (read admission per revision)."""
        return export_history(self.history(graph_id))

    def import_history(self, bundle: HistoryBundle | bytes | Mapping[str, Any], *,
                       approval_ref: str, correlation_id: str) -> Wire:
        """Recreate a graph with its complete history in this store.

        Host ``graph.bootstrap`` authority and an approval over the bundle
        digest are required; the graph must not exist. Events are verified by
        pure replay first and appended unchanged (one record per event; no
        original receipts). Interrupted imports leave a valid prefix: discard
        that store and retry rather than append to it.
        """
        verified = bundle if isinstance(bundle, HistoryBundle) else HistoryBundle.from_json(bundle)
        head = verified.replay().head
        graph_id = verified.graph_id
        digest = str(verified.value["bundle_digest"])
        with self._authorized(write=True) as context:
            if graph_id in self._histories:
                raise _error("revision_conflict", "History import requires an unseeded graph")
            if "graph.bootstrap" not in context.host_permissions:
                raise _error("permission_denied", "Independent Host bootstrap permission required")
            now = self._clock()
            proof = self._proof(context, approval_ref, digest, now, required=True)
            check_admission(AdmissionRequest(context, graph_id, "graph.import_history", ("import_history",),
                "0", UnseededState(graph_id).definition_digest, head.definition_digest,
                tuple(sorted(head.definition.nodes)), (), digest, proof), self._admission)
            for event in verified.events:
                self._persist((event,), context=context, correlation_id=validate_id(correlation_id))
            result = self._histories[graph_id].head
            if result.definition_digest != head.definition_digest:
                raise _error("journal_corrupt", "Imported history differs from its verified head")
            _LOG.info("history imported", extra={"graph_id": graph_id, "events": len(verified.events)})
            return {"graph_id": graph_id, "revision": result.revision,
                    "definition_digest": result.definition_digest, "events": len(verified.events)}

    def import_envelope(self, graph_id: str, bundle: GraphBundle, *, target: Mapping[str, object],
                        idempotency_key: str, reason: str, correlation_id: str,
                        identity: str = "preserve", remap: Mapping[str, str] | None = None,
                        prefix: str | None = None, external: Mapping[str, str] | None = None) -> Wire:
        """An ordinary transaction envelope importing a subtree bundle at the head.

        Preview and commit it like any other change: protection, admission,
        CAS and idempotency all apply.
        """
        head = self.read(graph_id)
        commands = plan_import(head, bundle, target=target, identity=cast(Any, identity),
                               remap=remap, prefix=prefix, external=external)
        return {"schema": "contexture.transaction/v1", "graph_id": graph_id,
                "base_revision": head.revision, "idempotency_key": idempotency_key,
                "commands": commands, "reason": reason, "correlation_id": correlation_id}

    def merge_envelope(self, graph_id: str, theirs: GraphDefinition | GraphBundle, *,
                       base_revision: str | None = None,
                       resolutions: Mapping[str, Any] | None = None,
                       idempotency_key: str, reason: str, correlation_id: str,
                       ) -> tuple[MergeResult, Wire | None]:
        """Three-way merge of ``theirs`` into the head against a retained base.

        ``base_revision`` defaults to a bundle's source revision. Returns the
        merge result and, when it is clean, an ordinary reconcile transaction
        envelope to preview and commit.
        """
        if isinstance(theirs, GraphBundle):
            if base_revision is None:
                base_revision = str(theirs.source["revision"])
            if theirs.source.get("graph_id") != graph_id:
                raise _error("schema_invalid", "Merge requires one graph lineage")
            theirs = theirs.definition()
        if base_revision is None:
            raise _error("schema_invalid", "Merge base revision required")
        head = self.read(graph_id)
        base = self.read(graph_id, base_revision)
        result = merge(base.definition, head.definition, theirs, resolutions=resolutions)
        if result.candidate is None:
            return result, None
        candidate = result.candidate
        old, new = set(head.definition.nodes), set(candidate.nodes)
        return result, {"schema": "contexture.transaction/v1", "graph_id": graph_id,
            "base_revision": head.revision, "idempotency_key": idempotency_key,
            "reason": reason, "correlation_id": correlation_id,
            "commands": [{"op": "reconcile", "scope_ids": sorted(old | new),
                "id_mapping": {"retained_ids": sorted(old & new), "new_ids": sorted(new - old),
                               "deleted_ids": sorted(old - new)},
                "payload": {"inline": candidate.as_json()}}]}

    def projection(self, name: str, *, graph_id: str) -> FrozenObject:
        """Trusted release integration read; authorizes every represented graph.

        Projection state may span graphs; require complete current read access
        rather than let graph_id act as an exfiltration scope selector.
        """
        with self._authorized() as context:
            self._history(graph_id)
            for history in self._histories.values():
                read_snapshot(history.head, context=context, admission=self._admission)
            if name not in self._external:
                raise _error("unsupported_contract", "Projection is not registered")
            return self._external[name]

    @contextmanager
    def release_boundary(self, *, write: bool = True) -> Iterator[ReleaseBoundary]:
        """Release manager shares owner lock/fence; no swap after unknown append.

        The manager must perform its current intent CAS and exact release
        approval inside this context, append via the yielded port, then publish
        only after append returns. No asynchronous suspension is supported.
        """
        with self._authorized(write=write) as context:
            boundary = ReleaseBoundary(self, context, write=write)
            try:
                yield boundary
            finally:
                boundary._active = False


class ReleaseBoundary:
    """Trusted in-process integration port, not a model-facing capability."""

    def __init__(self, owner: GraphOwner, context: VerifiedContext, *, write: bool = True):
        self._owner, self.context, self._active = owner, context, True
        self._write = write
        self._thread = get_ident()

    def check(self) -> None:
        if not self._active or self._thread != get_ident():
            raise _error("resource_busy", "Release boundary is no longer active")
        self._owner._ready(write=self._write)

    @property
    def actor_ref(self) -> str:
        self.check()
        return self._owner._actor(self.context)

    @property
    def durability(self) -> str:
        self.check()
        return self._owner._journal.durability

    @property
    def store_origin(self) -> tuple[str, str]:
        """Logical origin from the journal port's verified retained first record.

        A seeded graph requires this record. Cloned backups retaining both
        values are the same logical store; deployment must isolate live writers.
        No filesystem path or unverified caller identity is consulted here.
        """
        self.check()
        if self._owner._store_id is None:
            raise _error("not_ready", "Store origin requires a retained first record")
        store_id, first_hash = self._owner._store_id, self._owner._first_record_hash
        if not isinstance(store_id, str) or not store_id or not isinstance(first_hash, str) or not first_hash:
            raise _error("journal_corrupt", "Verified first record identity required")
        return store_id, first_hash

    @property
    def next_sequence(self) -> str:
        self.check()
        value = str(parse_counter(self._owner._watermark) + 1)
        parse_counter(value)
        return value

    def snapshot(self, graph_id: str, revision: str | None = None) -> RevisionState:
        """Trusted pinned history; caller must admit release before exposing it.

        This is not a read capability. Release admission may authorize a historical
        definition without granting blanket graph.read on this or other graphs.
        """
        self.check()
        history = self._owner._history(graph_id, full=revision is not None)
        return history.head if revision is None else history.at(revision)

    def authorize_read(self, graph_id: str) -> None:
        self.check()
        read_snapshot(self.snapshot(graph_id), context=self.context, admission=self._owner._admission)

    def projection(self, name: str) -> FrozenObject:
        """Trusted full projection; filter scope and authorize before returning it."""
        self.check()
        if name not in self._owner._external:
            raise _error("unsupported_contract", "Projection is not registered")
        return self._owner._external[name]

    def prior_receipt(self, graph_id: str, key: str, digest: str) -> Wire | None:
        """Common actor/key namespace, current same-graph read, before any CAS.

        Never authorize or disclose the other graph when a key collides across
        graphs. Graph commands and releases deliberately share this namespace.
        """
        self.check()
        validate_id(graph_id)
        value = self._owner._receipts.get((self.actor_ref, validate_id(key)))
        if value is None:
            return None
        if value["graph_id"] != graph_id:
            raise _error("idempotency_conflict", "Actor/key already accepted another request")
        self.authorize_read(graph_id)
        if value["request_digest"] != digest:
            raise _error("idempotency_conflict", "Actor/key already accepted another request")
        return cast(Wire, thaw_json(value))

    def verify_approval(self, reference: str | None, subject_digest: str, *,
                        required: bool = False) -> VerifiedApproval | None:
        self.check()
        return self._owner._proof(self.context, reference, subject_digest,
                                  self._owner._clock(), required=required)

    def admit(self, request: AdmissionRequest) -> None:
        self.check()
        if request.context != self.context or not request.operation.startswith("release."):
            raise _error("permission_denied", "Current release admission request required")
        check_admission(request, self._owner._admission)

    def append(self, events: Sequence[Mapping[str, Any]], *, request: AdmissionRequest,
               correlation_id: str, command_receipt: Mapping[str, Any] | None = None,
               read_current: bool = True,
               projection_checks: Mapping[str, Callable[[FrozenObject], object]] | None = None) -> None:
        self.check()
        if not self._write:
            raise _error("permission_denied", "Read-only release boundary cannot append")
        if read_current:
            self.authorize_read(request.graph_id)
        self.admit(request)
        values = tuple(_freeze(event) for event in events)
        if not values or any(event.get("type") not in self._owner._tags for event in values):
            raise _error("unsupported_contract", "Every release event requires a registered reducer")
        # Reuse only the immutable accepted prefix; reduce all candidate facts
        # before publication, then independently reduce the confirmed suffix.
        candidate = {"journal_seq": self.next_sequence,
            "prev_record_hash": self._owner._last_record_hash,
            "store_id": self._owner._store_id,
            "record_hash": None, "events": values, "actor_ref": self._owner._actor(self.context)}
        if command_receipt is not None:
            candidate["command_receipt"] = _freeze(command_receipt)
        _, projected, _ = self._owner._replay([candidate], incremental=True)
        for name, validate in (projection_checks or {}).items():
            if name not in projected:
                raise _error("unsupported_contract", "Projection is not registered")
            validate(projected[name])
        self._owner._persist(values, context=self.context, receipt=command_receipt,
                             correlation_id=validate_id(correlation_id))