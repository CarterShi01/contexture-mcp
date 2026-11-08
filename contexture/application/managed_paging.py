"""Candidate INTERNAL native WP10 API, NOT Q02 wire/Host acceptance.

Explicit Host-owned HMAC key + random server-side handles keep all coordinates,
scope, counts, revisions and filters confidential. No plaintext metadata token.
This seam uses real RunLeaseService and fresh ManagedRuntime.request admission;
it never retains a ManagedRequest or treats cached material as permission.

start(routes/members/uses/open) and continuation return native dictionaries, not
ManagedPageResponse: no schema, digest, counts, dependents, watch, blob fetch or
negotiated capability claim. Open is Role-only; its program and authorized
process cards accompany EVERY independent member/uses continuation. Members
are ROUTE cards in declared placement order; routes use projected preorder.
Filters affect relation cards only, never remove required process cards.
Only the existing RunLease model-openable ceiling is supported (conservative
relative to ordinary routing visibility for person-reserved model nodes).

One loop, bounded snapshots/pending creates/material bytes/items/cursor handles.
Byte quota measures retained UTF-8 material, not Python RSS or shared compiler
plans/resources; those are separately bounded by serving and RunLease capacity.
Default TTL cleanup uses loop callbacks; injected clocks require sweep unless a
timer is supplied. Shutdown paging BEFORE RunLeaseService BEFORE coordinator.
Private native runtime/disclosure ports are intentional parent-integration seams.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from contexture.application.managed_run_leases import (
    ExpiryHandle, ExpiryTimer, RunLeaseContext, RunLeaseService,
)
from contexture.application.managed_runtime import ManagedRuntime
from contexture.core.graph.disclosure import ManagedDisclosure
from contexture.core.graph.errors import GraphError
from contexture.core.graph.paging import slice_items
from contexture.core.graph.policy import VerifiedContext

Operation = Literal["routes", "members", "uses", "open"]
Relation = Literal["routes", "members", "uses"]


def _resync() -> GraphError:
    return GraphError("resync_required", "Native paging view is unavailable; resync required")


def _encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


class CursorSigner:
    """Host-owned rotatable key, never generated implicitly or persisted here.

    Rotation invalidates old handles; services clear them on their next sweep,
    operation, or TTL callback. The Host must keep this object/key private.
    """

    def __init__(self, key: bytes) -> None:
        self.rotate(key)

    def rotate(self, key: bytes) -> None:
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("Host-owned cursor key must contain at least 32 bytes")
        self._key = key
        self.version = secrets.token_hex(32)

    def issue(self) -> str:
        handle = secrets.token_hex(32)
        return handle + "." + hmac.new(self._key, b"native-paging-v1\0" +
                                      handle.encode("ascii"), hashlib.sha256).hexdigest()

    def valid(self, token: object) -> bool:
        if type(token) is not str or re.fullmatch(r"[0-9a-f]{64}\.[0-9a-f]{64}", token) is None:
            return False
        handle, signature = token.split(".")
        expected = hmac.new(self._key, b"native-paging-v1\0" + handle.encode("ascii"),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)


@dataclass(frozen=True, slots=True)
class PageProfile:
    """Exact caller profile on every continuation; no implicit new snapshot."""

    limit: int = 200
    kinds: frozenset[str] = frozenset({"role", "skill", "tool"})
    name: str = "native-route-v1"

    def __post_init__(self) -> None:
        if (type(self.limit) is not int or not 1 <= self.limit <= 1000
                or not isinstance(self.kinds, frozenset)
                or not self.kinds <= {"role", "skill", "tool"}
                or type(self.name) is not str or not 1 <= len(self.name) <= 128):
            raise GraphError("schema_invalid", "Invalid native page profile")


@dataclass(slots=True, repr=False)
class _Snapshot:
    run: RunLeaseContext
    identity: VerifiedContext
    coordinates: tuple[object, ...]
    ceiling: frozenset[str]
    selection: frozenset[str] | None
    profile: PageProfile
    operation: Operation
    reference: str | None
    generation: str
    revision: str
    locale: str
    expires: float
    relations: dict[str, tuple[bytes, ...]]
    owner: bytes
    processes: bytes
    size: int
    timer: ExpiryHandle | None = None
    cursors: dict[tuple[str, int], str] = field(default_factory=dict)


class ManagedPagingService:
    """Bounded local serving-snapshot store, never a parallel graph owner.

    A service owns its created runs exclusively; callers must not renew/release
    them. A continuation always re-enters the exact retained generation through
    runtime.request, then rechecks disclosure/current context and fixed ceiling.
    Serving swaps alone preserve old snapshots; changed authority/context or
    deleted current stable IDs invalidate and release the entire snapshot.
    No await separates the final freshness check from synchronous rendering.
    """

    def __init__(self, runtime: ManagedRuntime, *, runs: RunLeaseService,
                 signer: CursorSigner, max_snapshots: int = 64,
                 max_bytes: int = 16 * 1024 * 1024, max_items: int = 10000,
                 max_cursors: int = 20000, response_bytes: int = 8 * 1024 * 1024,
                 max_ttl: float = 60, clock: Callable[[], float] | None = None,
                 timer: ExpiryTimer | None = None) -> None:
        if runs._runtime is not runtime:
            raise ValueError("Run service must belong to this runtime")
        if any(type(n) is not int or n < 1 for n in
               (max_snapshots, max_bytes, max_items, max_cursors, response_bytes)):
            raise ValueError("Positive native paging budgets required")
        if (type(max_ttl) not in (int, float) or not math.isfinite(max_ttl)
                or not 0 < max_ttl <= runs.max_ttl):
            raise ValueError("Paging TTL must fit run TTL")
        self._runtime, self._runs, self._signer = runtime, runs, signer
        self._loop = asyncio.get_running_loop()
        self._clock = self._loop.time if clock is None else clock
        self._timer = (self._loop.call_later if clock is None else None) if timer is None else timer
        self._key_version = signer.version
        self._snapshots: dict[str, _Snapshot] = {}
        self._tokens: dict[str, tuple[str, str, int]] = {}
        self._pending, self._bytes, self._closed = 0, 0, False
        self.max_snapshots, self.max_bytes, self.max_items = max_snapshots, max_bytes, max_items
        self.max_cursors, self.response_bytes, self.max_ttl = max_cursors, response_bytes, max_ttl
        self._origin = self._coordinates()

    def _coordinates(self) -> tuple[object, ...]:
        c = self._runtime._coordinator
        return (c._store_origin, c.graph_id, c.target_id, c.instance_id, c.boot_id)

    def _check(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise _resync()

    @property
    def counts(self) -> dict[str, int]:
        """Trusted Host diagnostics ONLY; never include in a page response."""
        self._check()
        return {"snapshots": len(self._snapshots), "pending": self._pending,
                "bytes": self._bytes, "cursors": len(self._tokens)}

    @property
    def requires_explicit_sweep(self) -> bool:
        return self._timer is None

    def _drop(self, identity: str) -> None:
        snapshot = self._snapshots.pop(identity, None)
        if snapshot is not None:
            if snapshot.timer is not None:
                snapshot.timer.cancel()
            for token in snapshot.cursors.values():
                self._tokens.pop(token, None)
            self._bytes -= snapshot.size
            # Service-owned cleanup must also work after actor/tenant changed.
            self._runs._drop(snapshot.run._token)

    def sweep(self) -> int:
        self._check()
        invalid_key = self._key_version != self._signer.version
        expired = [sid for sid, s in self._snapshots.items()
                   if invalid_key or self._clock() >= s.expires]
        for sid in expired:
            self._drop(sid)
        self._key_version = self._signer.version
        return len(expired)

    def _arm(self, sid: str, snapshot: _Snapshot) -> None:
        if self._timer is not None:
            def expire() -> None:
                if self._snapshots.get(sid) is not snapshot:
                    return
                self.sweep()
                if sid in self._snapshots:
                    self._arm(sid, snapshot)
            snapshot.timer = self._timer(max(0, snapshot.expires - self._clock()), expire)

    def _cursor(self, sid: str, s: _Snapshot, relation: str, offset: int) -> str:
        key = (relation, offset)
        if key not in s.cursors:
            if len(self._tokens) >= self.max_cursors:
                raise GraphError("resource_busy", "Native page capacity unavailable")
            token = self._signer.issue()
            while token in self._tokens:
                token = self._signer.issue()
            s.cursors[key] = token
            self._tokens[token] = (sid, relation, offset)
        return s.cursors[key]

    def _page(self, sid: str, s: _Snapshot, relation: str, offset: int) -> dict[str, Any]:
        page = slice_items(s.relations[relation], offset=offset, limit=s.profile.limit,
                           byte_limit=self.response_bytes)
        value: dict[str, Any] = {"items": [json.loads(b) for b in page.items],
                                 "complete": page.next_offset is None}
        if page.next_offset is not None:
            value["next_cursor"] = self._cursor(sid, s, relation, page.next_offset)
        return value

    def _response(self, sid: str, s: _Snapshot, offsets: dict[str, int]) -> dict[str, Any]:
        result: dict[str, Any] = {"snapshot": sid}
        if s.operation == "open":
            result.update(owner_active=json.loads(s.owner), process_cards=json.loads(s.processes))
        for relation, offset in offsets.items():
            result[relation + "_page"] = self._page(sid, s, relation, offset)
        if len(_encode(result)) > self.response_bytes:
            # Envelope/program/process cards are atomic. Never silently omit.
            raise GraphError("too_large", "Native page response exceeds byte budget")
        return result

    def _material(self, d: ManagedDisclosure, operation: Operation, reference: str | None,
                  profile: PageProfile) -> tuple[dict[str, tuple[bytes, ...]], bytes, bytes]:
        index = d.plan.index
        node = None if reference is None else d._resolve(reference, active=operation == "open")
        if operation != "routes" and node is None:
            raise GraphError("schema_invalid", "Native relation requires a reference")
        if operation == "open" and index.get(str(node)).kind != "role":
            raise GraphError("schema_invalid", "Native paged open is Role-only")
        owner, processes = b"{}", b"[]"
        if operation == "open":
            assert node is not None
            process = d.plan.processes[node]
            cards = []
            roles = []
            for designation, target in (("pre_process", process.pre_process),
                                        ("post_process", process.post_process)):
                if target is not None:
                    # OPEN verifies active process visibility, not just routing.
                    d._resolve(d._ref(target))
                    card = d.route(d._ref(target))
                    roles.append(card)
                    cards.append({"designation": designation, "card": card})
            active = {"card": d.route(d._ref(node)), "instructions": d._instructions(node),
                      "roles": roles}
            d._processes(node, active)
            active.pop("roles")
            owner, processes = _encode(active), _encode(cards)
        relations: dict[str, tuple[bytes, ...]] = {}
        names = ("members", "uses") if operation == "open" else (operation,)
        count, size = 0, len(owner) + len(processes)
        for relation in names:
            if relation == "routes":
                ids = (n for n in index.preorder if n in d._visible
                       and index.locations[n].parent_id not in d._visible)
            elif relation == "members":
                members = index.get(str(node)).members
                ids = (n for _, _, n in members.placements()) if members else iter(())
            else:
                ids = iter(index.get(str(node)).uses)
            data = []
            for n in ids:
                if n not in d._visible or index.get(n).kind not in profile.kinds:
                    continue
                encoded = _encode(d.route(d._ref(n)))
                count, size = count + 1, size + len(encoded)
                if count > self.max_items or size > self.max_bytes:
                    raise GraphError("too_large", "Native snapshot exceeds material budget")
                data.append(encoded)
            relations[relation] = tuple(data)
        if size > self.max_bytes:
            raise GraphError("too_large", "Native snapshot exceeds material budget")
        return relations, owner, processes

    async def start(self, operation: Operation, *, reference: str | None = None,
                    selection: frozenset[str] | None = None, profile: PageProfile = PageProfile(),
                    ttl: float = 30) -> dict[str, Any]:
        self._check()
        self.sweep()
        if operation not in ("routes", "members", "uses", "open"):
            raise GraphError("schema_invalid", "Unsupported native paging operation")
        if ((operation == "routes") != (reference is None)
                or type(profile) is not PageProfile
                or (selection is not None and type(selection) is not frozenset)
                or type(ttl) not in (int, float) or not math.isfinite(ttl)
                or not 0 < ttl <= self.max_ttl):
            raise GraphError("schema_invalid", "Invalid native snapshot request")
        if (self._closed or len(self._snapshots) + self._pending >= self.max_snapshots):
            raise GraphError("resource_busy", "Native page capacity unavailable")
        if self._coordinates() != self._origin:
            raise _resync()
        self._pending += 1
        run = None
        sid = None
        expires = self._clock() + ttl
        version = self._signer.version
        try:
            identity = self._runtime._context()
            run = await self._runs.create(ttl=ttl, selection=selection)
            async with self._runtime.request(selection=selection, run_context=run) as request:
                d = await request._disclosure("open")
                if (self._closed or self._clock() >= expires or version != self._signer.version
                        or identity != self._runtime._context() or self._coordinates() != self._origin):
                    raise _resync()
                relations, owner, processes = self._material(d, operation, reference, profile)
                size = len(owner) + len(processes) + sum(len(b) for v in relations.values() for b in v)
                if self._bytes + size > self.max_bytes:
                    raise GraphError("resource_busy", "Native page capacity unavailable")
                sid = secrets.token_hex(32)
                s = _Snapshot(run, identity, self._origin, d._visible, selection, profile,
                    operation, reference, request.generation_uuid, request._lease.release.snapshot.revision,
                    d.plan.selected_locale, expires, relations, owner, processes, size)
                self._snapshots[sid] = s
                self._bytes += size
                result = self._response(sid, s, dict.fromkeys(relations, 0))
                self._arm(sid, s)
                return result
        except BaseException:
            if sid is not None:
                self._drop(sid)
            if run is not None:
                self._runs._drop(run._token)
            raise
        finally:
            self._pending -= 1

    async def continuation(self, token: str, *, relation: Relation,
                           reference: str | None = None,
                           selection: frozenset[str] | None = None,
                           profile: PageProfile = PageProfile()) -> dict[str, Any]:
        self._check()
        self.sweep()
        if self._closed or not self._signer.valid(token):
            raise _resync()
        entry = self._tokens.get(token)
        if entry is None:
            raise _resync()
        sid, expected_relation, offset = entry
        s = self._snapshots[sid]
        try:
            if (relation != expected_relation or reference != s.reference or selection != s.selection
                    or (selection is not None and type(selection) is not frozenset)
                    or profile != s.profile or type(profile) is not PageProfile
                    or self._runtime._context() != s.identity or self._coordinates() != s.coordinates):
                raise _resync()
            async with self._runtime.request(selection=s.selection, run_context=s.run) as request:
                d = await request._disclosure("open")
                if (self._snapshots.get(sid) is not s or self._clock() >= s.expires
                        or self._signer.version != self._key_version
                        or self._runtime._context() != s.identity or d._visible != s.ceiling
                        or d.generation_uuid != s.generation or d.plan.selected_locale != s.locale
                        or request._lease.release.snapshot.revision != s.revision
                        or self._coordinates() != s.coordinates):
                    raise _resync()
                return self._response(sid, s, {relation: offset})
        except BaseException as error:
            self._drop(sid)
            if isinstance(error, GraphError) and error.code not in ("too_large", "resource_busy"):
                raise _resync() from None
            raise

    def shutdown(self) -> None:
        self._check()
        self._closed = True
        for sid in tuple(self._snapshots):
            self._drop(sid)