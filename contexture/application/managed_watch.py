"""Candidate INTERNAL native WP10 pull watch, NOT accepted Q02 wire/Host API.

Definition-metadata only: ROUTE-level node fields and visible roots. No serving,
release, instructions, schema, raw facts, global revision/hash/count or handler.
Each payload atomically replaces a complete authorized projection. Descriptors
group ALL same-graph history events in one durable record, never individual facts.

The public read-only release boundary supplies owner serialization, fresh trusted
identity, authority fence, origin and durable watermark. The two private owner
ports in _capture are an explicit integration seam pending a public history and
scoped-read port; this service is not another journal owner or replay engine.
Host admission must support graph.watch with an empty impact and return the
current disclosure scope. Selection is an attenuation, never authentication.

No background work/queue. Retained handles and exact fetch bytes have hard caps;
TTL cleanup is lazy (or Host calls sweep). Pull continuations are checkpoints:
persist the returned cursor ONLY after applying/fetching every complete batch.
Retry the input cursor for at-least-once delivery with stable batch IDs. Cursor
and fetch handles are local, opaque, HMAC authenticated, expiring, and revocable.
Host owns the signing key; replacing the service/key requires a fresh snapshot.
Time limits are cooperative checks around synchronous ports/serialization, NOT
preemption or a wall-clock guarantee for an uncooperative admission callback.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import re
import secrets
from threading import RLock
from time import monotonic
from typing import Any

from contexture.application.managed import GraphOwner
from contexture.core.graph.canonical import canonical_digest, canonical_json, parse_counter, validate_id
from contexture.core.graph.errors import GraphError
from contexture.core.graph.events import RevisionState
from contexture.core.graph.history import History
from contexture.core.graph.policy import AdmissionRequest, VerifiedContext, check_admission


def _resync() -> GraphError:
    return GraphError("resync_required", "Native watch unavailable; take a new authorized snapshot")


@dataclass(frozen=True, slots=True)
class WatchProfile:
    max_batches: int = 64
    max_bytes: int = 1024 * 1024
    inline_bytes: int = 256 * 1024
    max_seconds: float = 1.0
    name: str = "native-definition-metadata-v1"

    def __post_init__(self) -> None:
        if (any(type(n) is not int or n < 1 for n in
                (self.max_batches, self.max_bytes, self.inline_bytes))
                or self.max_batches > 1024 or self.max_bytes > 16 * 1024 * 1024
                or self.inline_bytes > min(self.max_bytes, 1024 * 1024)
                or type(self.max_seconds) not in (int, float)
                or not math.isfinite(self.max_seconds) or not 0 < self.max_seconds <= 30
                or type(self.name) is not str or not 1 <= len(self.name) <= 128):
            raise ValueError("Invalid bounded native watch profile")


@dataclass(slots=True, repr=False)
class _Session:
    identity: VerifiedContext
    origin: tuple[str, str]
    graph_id: str
    scope: frozenset[str]
    selection: frozenset[str] | None
    profile: WatchProfile
    expires: float
    positions: dict[str, int] = field(default_factory=dict)
    fetches: dict[str, bytes] = field(default_factory=dict)
    size: int = 0


def _material(state: RevisionState, scope: frozenset[str],
              selection: frozenset[str] | None) -> bytes:
    """An explicit narrow projection; never hash/filter raw resolved facts."""
    visible = scope if selection is None else scope & selection
    graph = state.definition
    return canonical_json({"nodes": [
        {"node_id": n.node_id, "kind": n.kind, "name": n.name, "description": n.description}
        for identity, n in sorted(graph.nodes.items()) if identity in visible],
        "roots": [r.as_json() for r in graph.roots if r.node_id in visible]})


class ManagedWatchService:
    """Synchronous, fenced pull over GraphOwner's durable immutable History.

    No tail cache: each poll captures one immutable history under the owner
    boundary, so concurrent commits are either in this cut or the next poll.
    Per-poll max_batches also bounds examined durable graph batches (including
    invisible ones); no caught-up/remaining/scan count is disclosed. Unrelated
    store records are skipped by the owner's graph index, not queued here.
    Capacity/expiry/revocation invalidate the entire session, including fetches.
    """

    def __init__(self, owner: GraphOwner, *, key: bytes,
                 clock: Callable[[], float] = monotonic,
                 max_sessions: int = 64, max_handles: int = 4096,
                 max_retained_bytes: int = 16 * 1024 * 1024,
                 max_payload_bytes: int = 8 * 1024 * 1024,
                 max_ttl: float = 60) -> None:
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("Explicit Host-owned signing key of at least 32 bytes required")
        if any(type(n) is not int or n < 1 for n in
               (max_sessions, max_handles, max_retained_bytes, max_payload_bytes)):
            raise ValueError("Positive native watch capacities required")
        if (type(max_ttl) not in (int, float) or not math.isfinite(max_ttl)
                or not 0 < max_ttl <= 3600):
            raise ValueError("Finite bounded native watch TTL required")
        self._owner, self._key, self._clock = owner, key, clock
        self._lock = RLock()
        self._sessions: dict[str, _Session] = {}
        self._closed = False
        self.max_sessions, self.max_handles = max_sessions, max_handles
        self.max_retained_bytes, self.max_payload_bytes = max_retained_bytes, max_payload_bytes
        self.max_ttl = max_ttl

    def _token(self, sid: str, kind: str, position: object) -> str:
        handle = hmac.new(self._key, canonical_json([sid, kind, position]), hashlib.sha256).hexdigest()
        signature = hmac.new(self._key, b"native-watch-v1\0" + handle.encode(), hashlib.sha256).hexdigest()
        return handle + "." + signature

    def _valid(self, token: object) -> bool:
        if type(token) is not str or re.fullmatch(r"[0-9a-f]{64}\.[0-9a-f]{64}", token) is None:
            return False
        handle, signature = token.split(".")
        expected = hmac.new(self._key, b"native-watch-v1\0" + handle.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def _drop(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def sweep(self) -> None:
        """Trusted Host cleanup; no public counts or hidden lag diagnostics."""
        with self._lock:
            for sid, s in tuple(self._sessions.items()):
                if self._closed or self._clock() >= s.expires:
                    self._drop(sid)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._sessions.clear()

    def _capacity(self) -> None:
        if (sum(len(s.positions) + len(s.fetches) for s in self._sessions.values()) > self.max_handles
                or sum(s.size for s in self._sessions.values()) > self.max_retained_bytes):
            raise _resync()

    def _cursor(self, sid: str, s: _Session, position: int) -> str:
        token = self._token(sid, "cursor", str(position))
        s.positions[token] = position
        self._capacity()
        return token

    def _capture(self, graph_id: str) -> tuple[VerifiedContext, tuple[str, str], History, int, frozenset[str]]:
        # This is the ONLY private integration seam. Do not call public owner
        # reads from inside this boundary: callback reentry is rightly rejected.
        with self._owner.release_boundary(write=False) as boundary:
            if self._owner.writes_stopped:
                raise _resync()
            state = boundary.snapshot(graph_id)
            context = boundary.context
            decision = check_admission(AdmissionRequest(context, graph_id, "graph.watch", (),
                state.revision, state.definition_digest, state.definition_digest,
                (), (), None, None), self._owner._admission)
            history = self._owner._history(graph_id)
            boundary.check()
            if self._closed:
                raise _resync()
            return (context, boundary.store_origin, history,
                    parse_counter(boundary.next_sequence) - 1, decision.scope_ids)

    def _fresh(self, s: _Session) -> tuple[History, int]:
        identity, origin, history, watermark, scope = self._capture(s.graph_id)
        if (not any(retained is s for retained in self._sessions.values())
            or identity != s.identity or origin != s.origin or scope != s.scope
                or self._clock() >= s.expires
                or any(p < parse_counter(history.records[0].journal_seq)
                       for p in s.positions.values())):
            raise _resync()
        return history, watermark

    def _find(self, token: str, *, fetch: bool, graph_id: str,
              selection: frozenset[str] | None, profile: WatchProfile) -> tuple[str, _Session]:
        self.sweep()
        if self._closed or not self._valid(token):
            raise _resync()
        for sid, s in self._sessions.items():
            if token in (s.fetches if fetch else s.positions):
                if (graph_id != s.graph_id or selection != s.selection
                        or type(profile) is not WatchProfile or profile != s.profile
                        or (selection is not None and type(selection) is not frozenset)):
                    self._drop(sid)
                    raise _resync()
                return sid, s
        raise _resync()

    def _deadline(self, s: _Session, deadline: float) -> None:
        if self._clock() >= min(s.expires, deadline):
            raise _resync()

    def start(self, graph_id: str, *, selection: frozenset[str] | None = None,
              profile: WatchProfile = WatchProfile(), ttl: float = 30) -> dict[str, Any]:
        """Return a detached authorized snapshot and cursor at the SAME cut."""
        with self._lock:
            self.sweep()
            if (type(profile) is not WatchProfile
                    or (selection is not None and type(selection) is not frozenset)
                    or type(ttl) not in (int, float) or not math.isfinite(ttl)
                    or not 0 < ttl <= self.max_ttl):
                raise GraphError("schema_invalid", "Invalid native watch request")
            validate_id(graph_id)
            for identity in selection or ():
                validate_id(identity)
            if self._closed or len(self._sessions) >= self.max_sessions:
                raise _resync()
            began = self._clock()
            sid = secrets.token_hex(32)
            try:
                identity, origin, history, position, scope = self._capture(graph_id)
                s = _Session(identity, origin, graph_id, scope, selection, profile, began + ttl)
                material = _material(history.head, scope, selection)
                if len(material) > self.max_payload_bytes:
                    raise _resync()
                self._sessions[sid] = s
                result = {"snapshot": json.loads(material),
                          "view_digest": canonical_digest(json.loads(material), "contexture.view/v1"),
                          "cursor": self._cursor(sid, s, position)}
                if len(canonical_json(result)) > profile.max_bytes:
                    raise _resync()
                self._fresh(s)
                self._deadline(s, began + profile.max_seconds)
                return result
            except BaseException as error:
                self._drop(sid)
                if isinstance(error, GraphError):
                    raise _resync() from None
                raise

    def poll(self, cursor: str, *, graph_id: str,
             selection: frozenset[str] | None = None,
             profile: WatchProfile = WatchProfile()) -> dict[str, Any]:
        """Pull complete durable batches; retrying cursor never acknowledges it.

        Hidden/no-op batches advance only the opaque checkpoint. No heartbeat
        asserts caught-up: an empty result may simply have exhausted scan budget.
        An oversized atomic payload is retained as exact authorized fetch bytes;
        neither descriptor nor fetch contains the source journal hash/sequence.
        """
        with self._lock:
            sid, s = self._find(cursor, fetch=False, graph_id=graph_id,
                                 selection=selection, profile=profile)
            try:
                deadline = self._clock() + profile.max_seconds
                history, watermark = self._fresh(s)
                position = s.positions[cursor]
                if position > watermark:
                    raise _resync()
                # The retained seed must precede/equal the checkpoint. Without
                # its exact before view it is unsafe to silently skip a suffix.
                if position < parse_counter(history.records[0].journal_seq):
                    raise _resync()
                # Records are ordered by journal_seq: binary search instead of
                # scanning the whole retained history on every poll.
                records = history.records
                start = bisect_right(records, position, key=lambda r: parse_counter(r.journal_seq))
                before = records[max(start - 1, 0)].state
                previous = _material(before, s.scope, s.selection)
                batches: list[dict[str, Any]] = []
                examined = 0
                pending_seq: int | None = None
                pending_state: RevisionState | None = None

                def deliver(sequence: int, state: RevisionState) -> bool:
                    nonlocal previous, position, examined
                    self._deadline(s, deadline)
                    material = _material(state, s.scope, s.selection)
                    self._deadline(s, deadline)
                    if len(material) > self.max_payload_bytes:
                        raise _resync()
                    if material != previous:
                        payload = json.loads(material)
                        digest = canonical_digest(payload, "contexture.view/v1")
                        item: dict[str, Any] = {
                            "batch_id": self._token(sid, "batch", str(sequence)),
                            "facts_digest": digest, "size": len(material),
                            "seqcursor": self._token(sid, "cursor", str(sequence))}
                        if len(material) <= profile.inline_bytes:
                            item["payload"] = payload
                        else:
                            item["authorized_fetch_ref"] = self._token(sid, "fetch", str(sequence))
                        candidate = {"batches": [*batches, item], "cursor": item["seqcursor"]}
                        if len(canonical_json(candidate)) > profile.max_bytes:
                            if not batches:
                                raise _resync()
                            return False
                        self._cursor(sid, s, sequence)
                        if "authorized_fetch_ref" in item:
                            token = item["authorized_fetch_ref"]
                            retained = s.fetches.get(token)
                            if retained is not None and retained != material:
                                raise _resync()
                            if retained is None:
                                s.fetches[token] = material
                                s.size += len(material)
                            self._capacity()
                        batches.append(item)
                    previous, position = material, sequence
                    examined += 1
                    return True

                # History is immutable and already replayed from the owner's
                # verified journal. Group equal seqs defensively for a future
                # history port that supports multiple same-graph events/record.
                for record in records[start:]:
                    self._deadline(s, deadline)
                    sequence = parse_counter(record.journal_seq)
                    if sequence <= position:
                        continue
                    if record.event is None or record.event.get("type") not in (
                            "GraphChangeCommitted", "CommandCompleted"):
                        raise _resync()
                    if pending_seq is not None and sequence != pending_seq:
                        assert pending_state is not None
                        if not deliver(pending_seq, pending_state) or examined >= profile.max_batches:
                            break
                    pending_seq, pending_state = sequence, record.state
                else:
                    delivered = True
                    if pending_seq is not None:
                        assert pending_state is not None
                        delivered = deliver(pending_seq, pending_state)
                    # Interleaved records for other graphs/releases carry no
                    # definition material; checkpoint only, never disclose lag.
                    if delivered:
                        position = watermark
                result = {"batches": batches, "cursor": self._cursor(sid, s, position)}
                if len(canonical_json(result)) > profile.max_bytes:
                    raise _resync()
                self._fresh(s)
                self._deadline(s, deadline)
                return result
            except BaseException as error:
                self._drop(sid)
                if isinstance(error, GraphError):
                    raise _resync() from None
                raise

    def fetch(self, reference: str, *, graph_id: str,
              selection: frozenset[str] | None = None,
              profile: WatchProfile = WatchProfile()) -> dict[str, Any]:
        """Fetch one COMPLETE exact projected batch, never rebuild from head.

        Separate payload budget, not the inline/descriptor response budget. The
        caller must not apply a descriptor or advance its checkpoint before this
        succeeds. There is deliberately no partial/chunk application API.
        """
        with self._lock:
            sid, s = self._find(reference, fetch=True, graph_id=graph_id,
                                 selection=selection, profile=profile)
            try:
                deadline = self._clock() + profile.max_seconds
                self._fresh(s)
                payload = json.loads(s.fetches[reference])
                self._fresh(s)
                self._deadline(s, deadline)
                return payload
            except BaseException as error:
                self._drop(sid)
                if isinstance(error, GraphError):
                    raise _resync() from None
                raise