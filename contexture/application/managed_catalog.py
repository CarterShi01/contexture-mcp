"""Internal native metadata Catalog, not a negotiated wire/Host profile.

Definition reads use the owner's read-only lock/authority fence. Serving reads
retain the actual runtime generation, but consult CURRENT runtime authority on
every page. No graph owner, compiler, journal replay, or execution lives here.
Snapshots retain bounded immutable bytes, not permission. Shutdown before the
serving coordinator. Injected clocks without a timer require explicit sweep.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import json
import math
import secrets
from typing import Any, Literal

from contexture.application.managed import GraphOwner
from contexture.application.managed_generation import RetainedGeneration
from contexture.application.managed_paging import CursorSigner
from contexture.application.managed_run_leases import ExpiryHandle, ExpiryTimer
from contexture.application.managed_runtime import ManagedRuntime
from contexture.core.graph.canonical import canonical_json, parse_counter, validate_id
from contexture.core.graph.catalog import CatalogMaterial, metadata_catalog
from contexture.core.graph.errors import GraphError
from contexture.core.graph.index import GraphIndex
from contexture.core.graph.paging import slice_items
from contexture.core.graph.policy import AdmissionRequest, VerifiedContext, check_admission

Source = Literal["head", "revision", "serving"]
Stream = Literal["nodes", "containment", "uses"]
STREAMS: tuple[Stream, ...] = ("nodes", "containment", "uses")


def _resync() -> GraphError:
    return GraphError("resync_required", "Catalog snapshot unavailable; resync required")


@dataclass(frozen=True, slots=True)
class CatalogProfile:
    limit: int = 200
    kinds: frozenset[str] = frozenset({"role", "skill", "tool"})
    name: str = "native-metadata-catalog-v1"

    def __post_init__(self) -> None:
        if (type(self.limit) is not int or not 1 <= self.limit <= 1000
                or type(self.kinds) is not frozenset
                or not self.kinds <= {"role", "skill", "tool"}
                or type(self.name) is not str or not 1 <= len(self.name) <= 128):
            raise GraphError("schema_invalid", "Invalid catalog profile")


@dataclass(slots=True, repr=False)
class _Snapshot:
    identity: VerifiedContext
    coordinates: tuple[object, ...]
    source: Source
    graph_id: str
    revision: str
    selection: frozenset[str] | None
    profile: CatalogProfile
    index: GraphIndex
    ceiling: frozenset[str]
    material: CatalogMaterial
    envelope: bytes
    expires: float
    keeper: RetainedGeneration | None = None
    timer: ExpiryHandle | None = None
    cursors: dict[tuple[str, int], str] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return self.material.size + len(self.envelope)


class ManagedCatalogService:
    """One-loop bounded snapshot/material store, with independent page streams.

    selection contains subtree anchors in the requested snapshot. Revision reads
    intersect historical IDs with current IDs AND current graph.catalog scope;
    old permissions never authorize history. Serving uses runtime routing scope
    (including route-visible, person-reserved model nodes), not open permission.
    Any effective scope/context/origin change invalidates all snapshot handles.
    Head advances and serving swaps alone do not invalidate retained snapshots.
    Coordinates are explicit; the digest covers ONLY projected metadata plus
    source/graph/profile, never a global definition digest, revision, or journal.
    Material byte quotas measure serialized projections/envelopes, not Python
    RSS or owner/compiler-owned immutable indices and retained runtime resources.
    Those lifetimes/capacities remain governed by owner and serving. This native
    ROUTE-level profile excludes programs, schemas, publications and raw facts;
    it is not an executable/open Catalog or a portable wire-schema acceptance.
    """

    def __init__(self, owner: GraphOwner, *, signer: CursorSigner,
                 runtime: ManagedRuntime | None = None,
                 max_snapshots: int = 64, max_bytes: int = 16 * 1024 * 1024,
                 max_items: int = 10000, max_cursors: int = 20000,
                 response_bytes: int = 1024 * 1024, max_ttl: float = 60,
                 clock: Callable[[], float] | None = None,
                 timer: ExpiryTimer | None = None) -> None:
        if runtime is not None and runtime._coordinator.owner is not owner:
            raise ValueError("Catalog runtime must share its graph owner")
        if any(type(n) is not int or n < 1 for n in
               (max_snapshots, max_bytes, max_items, max_cursors, response_bytes)):
            raise ValueError("Positive catalog capacities required")
        if (type(max_ttl) not in (int, float) or not math.isfinite(max_ttl)
                or not 0 < max_ttl <= 3600):
            raise ValueError("Finite bounded catalog TTL required")
        self._owner, self._runtime, self._signer = owner, runtime, signer
        self._loop = asyncio.get_running_loop()
        self._clock = self._loop.time if clock is None else clock
        self._timer = (self._loop.call_later if clock is None else None) if timer is None else timer
        self._version = signer.version
        self._snapshots: dict[str, _Snapshot] = {}
        self._tokens: dict[str, tuple[str, Stream, int]] = {}
        self._bytes, self._pending, self._closed = 0, 0, False
        self.max_snapshots, self.max_bytes, self.max_items = max_snapshots, max_bytes, max_items
        self.max_cursors, self.response_bytes, self.max_ttl = max_cursors, response_bytes, max_ttl

    def _check(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise _resync()

    @property
    def counts(self) -> dict[str, int]:
        """Trusted Host diagnostics, never emitted in Catalog responses."""
        self._check()
        return {"snapshots": len(self._snapshots), "pending": self._pending,
                "bytes": self._bytes, "cursors": len(self._tokens)}

    @property
    def requires_explicit_sweep(self) -> bool:
        return self._timer is None

    def _drop(self, sid: str) -> None:
        s = self._snapshots.pop(sid, None)
        if s is not None:
            for token in s.cursors.values():
                self._tokens.pop(token, None)
            self._bytes -= s.size
            if s.timer is not None:
                s.timer.cancel()
            if s.keeper is not None:
                s.keeper.close()

    def sweep(self) -> int:
        self._check()
        expired = [sid for sid, s in self._snapshots.items()
                   if self._closed or self._clock() >= s.expires
                   or self._version != self._signer.version]
        for sid in expired:
            self._drop(sid)
        self._version = self._signer.version
        return len(expired)

    def _arm(self, sid: str, s: _Snapshot) -> None:
        if self._timer is not None:
            def expire() -> None:
                if self._snapshots.get(sid) is s:
                    self.sweep()
                    if sid in self._snapshots:
                        self._arm(sid, s)
            s.timer = self._timer(max(0, s.expires - self._clock()), expire)

    @staticmethod
    def _selected(index: GraphIndex, selection: frozenset[str] | None) -> frozenset[str]:
        if selection is None:
            return frozenset(index.preorder)
        if not selection <= index.locations.keys():
            raise _resync()
        return frozenset(n.node_id for anchor in selection for n in index.subtree(anchor))

    def _definition(self, graph_id: str, revision: str | None,
                    pinned: GraphIndex | None, selection: frozenset[str] | None
                    ) -> tuple[VerifiedContext, tuple[object, ...], GraphIndex, str, frozenset[str]]:
        with self._owner.release_boundary(write=False) as boundary:
            if self._owner.writes_stopped:
                raise _resync()
            current = boundary.snapshot(graph_id)
            decision = check_admission(AdmissionRequest(boundary.context, graph_id,
                "graph.catalog", (), current.revision, current.definition_digest,
                current.definition_digest, (), (), None, None), self._owner._admission)
            state = boundary.snapshot(graph_id, revision)
            index = GraphIndex.build(state.definition) if pinned is None else pinned
            visible = (decision.scope_ids & current.definition.nodes.keys()
                       & self._selected(index, selection))
            boundary.check()
            return boundary.context, (boundary.store_origin, graph_id), index, state.revision, frozenset(visible)

    def _serving_coordinates(self) -> tuple[object, ...]:
        runtime = self._runtime
        if runtime is None:
            raise GraphError("not_ready", "Catalog serving runtime is not configured")
        c = runtime._coordinator
        with self._owner.release_boundary(write=False) as boundary:
            if self._owner.writes_stopped or c._closed or boundary.store_origin != c._store_origin:
                raise _resync()
            return boundary.store_origin, c.graph_id, c.target_id, c.instance_id, c.boot_id

    def _cursor(self, sid: str, s: _Snapshot, stream: Stream, offset: int) -> str:
        key = (stream, offset)
        if key not in s.cursors:
            if len(self._tokens) >= self.max_cursors:
                raise GraphError("resource_busy", "Catalog cursor capacity unavailable")
            token = self._signer.issue()
            while token in self._tokens:
                token = self._signer.issue()
            s.cursors[key] = token
            self._tokens[token] = sid, stream, offset
        return s.cursors[key]

    def _response(self, sid: str, s: _Snapshot, stream: Stream, offset: int) -> dict[str, Any]:
        result = json.loads(s.envelope)
        result["snapshot"] = sid
        # Reserve the exact worst-case continuation envelope before slicing.
        shell = dict(result, stream=stream, items=[], complete=False, next_cursor="x" * 129)
        budget = self.response_bytes - len(canonical_json(shell)) + 2
        if budget < 2:
            raise GraphError("too_large", "Catalog envelope exceeds response budget")
        page = slice_items(getattr(s.material, stream), offset=offset,
                           limit=s.profile.limit, byte_limit=budget)
        result.update(stream=stream, items=[json.loads(b) for b in page.items],
                      complete=page.next_offset is None)
        if page.next_offset is not None:
            result["next_cursor"] = self._cursor(sid, s, stream, page.next_offset)
        # Stream starts are independent opaque handles, not raw offsets.
        return result

    def _alive(self, expires: float, version: str) -> None:
        if self._closed or self._clock() >= expires or version != self._signer.version:
            raise _resync()

    async def start(self, graph_id: str, *, source: Source = "head", revision: str | None = None,
                    selection: frozenset[str] | None = None,
                    profile: CatalogProfile = CatalogProfile(), ttl: float = 30) -> dict[str, Any]:
        """Capture one snapshot; return envelope and three stream-start cursors.

        Read each stream using continuation, including its initial cursor. No
        unbounded inline first page and no silent oversized-node truncation.
        """
        self._check()
        self.sweep()
        validate_id(graph_id)
        if (source not in ("head", "revision", "serving")
                or (source == "revision") != (revision is not None)
                or type(profile) is not CatalogProfile
                or (selection is not None and type(selection) is not frozenset)
                or type(ttl) not in (int, float) or not math.isfinite(ttl)
                or not 0 < ttl <= self.max_ttl):
            raise GraphError("schema_invalid", "Invalid catalog request")
        if revision is not None:
            parse_counter(revision)
        for anchor in selection or ():
            validate_id(anchor)
        if self._closed or len(self._snapshots) + self._pending >= self.max_snapshots:
            raise GraphError("resource_busy", "Catalog snapshot capacity unavailable")
        self._pending += 1
        keeper, sid = None, None
        expires, version = self._clock() + ttl, self._signer.version
        try:
            generation: str | None = None
            locale: str | None = None
            card: Callable[[str], dict[str, Any]] | None = None
            if source == "serving":
                runtime = self._runtime
                coordinates = self._serving_coordinates()
                if runtime is None or graph_id != runtime._coordinator.graph_id:
                    raise _resync()
                identity = runtime._context()
                async with runtime._coordinator.request_lease() as lease:
                    runtime._preflight(lease)
                    plan = lease.generation.plan
                    index = plan.index
                    locale = plan.selected_locale

                    def serving_card(node_id: str) -> dict[str, Any]:
                        node, presentation = index.get(node_id), plan.presentations[node_id]
                        value: dict[str, Any] = {"kind": node.kind, "name": node.name,
                                                 "description": presentation.description}
                        if index.presentation(node_id, plan.selected_locale) is not None:
                            value["title"] = presentation.title
                        return value

                    card = serving_card
                    selected = runtime._selection(lease, selection, visibility_only=True)
                    keeper = lease.generation.retain()
                    _, visible = await runtime._authorize(identity, "route", selected, visibility_only=True)
                    if runtime._context() != identity or self._serving_coordinates() != coordinates:
                        raise _resync()
                    revision = lease.release.snapshot.revision
                    generation = lease.generation.context.generation_uuid
            else:
                identity, coordinates, index, revision, visible = self._definition(
                    graph_id, revision, None, selection)
            self._alive(expires, version)
            material = metadata_catalog(index, visible, kinds=profile.kinds,
                                        max_items=self.max_items, max_bytes=self.max_bytes, card=card)
            descriptor = {"source": source, "graph_id": graph_id, "detail": "metadata_only",
                          "profile": {"name": profile.name, "kinds": sorted(profile.kinds)}}
            if locale is not None:
                descriptor["locale"] = locale
            envelope: dict[str, Any] = {"source": source, "graph_id": graph_id,
                "revision": revision, "detail": "metadata_only",
                "profile": descriptor["profile"],
                "view_digest": material.digest(descriptor)}
            if source == "serving":
                envelope.update(target_id=coordinates[2], instance_id=coordinates[3],
                                boot_id=coordinates[4], generation_uuid=generation, locale=locale)
            assert revision is not None
            s = _Snapshot(identity, coordinates, source, graph_id, revision, selection,
                          profile, index, visible, material, canonical_json(envelope), expires, keeper)
            if self._bytes + s.size > self.max_bytes:
                raise GraphError("resource_busy", "Catalog retained material capacity unavailable")
            sid = secrets.token_hex(32)
            self._snapshots[sid] = s
            self._bytes += s.size
            keeper = None
            # Preflight EVERY atomic item before publishing a usable snapshot.
            for stream in STREAMS:
                for item in getattr(material, stream):
                    shell = dict(envelope, snapshot=sid, stream=stream, items=[json.loads(item)],
                                 complete=False, next_cursor="x" * 129)
                    if len(canonical_json(shell)) > self.response_bytes:
                        raise GraphError("too_large", "One catalog item exceeds response budget")
            result = dict(envelope, snapshot=sid, streams={
                stream: self._cursor(sid, s, stream, 0) for stream in STREAMS})
            if len(canonical_json(result)) > self.response_bytes:
                raise GraphError("too_large", "Catalog envelope exceeds response budget")
            self._alive(expires, version)
            self._arm(sid, s)
            return result
        except BaseException:
            if sid is not None:
                self._drop(sid)
            if keeper is not None:
                keeper.close()
            raise
        finally:
            self._pending -= 1

    async def continuation(self, token: str, *, graph_id: str, source: Source,
                           stream: Stream, revision: str | None = None,
                           selection: frozenset[str] | None = None,
                           profile: CatalogProfile = CatalogProfile()) -> dict[str, Any]:
        """Retry-stable exact bytes; any freshness failure revokes all streams."""
        self._check()
        self.sweep()
        if self._closed or not self._signer.valid(token) or token not in self._tokens:
            raise _resync()
        sid, expected_stream, offset = self._tokens[token]
        s = self._snapshots[sid]
        try:
            if (graph_id != s.graph_id or source != s.source or stream != expected_stream
                    or revision != (s.revision if source == "revision" else None)
                    or selection != s.selection or type(profile) is not CatalogProfile
                    or profile != s.profile
                    or (selection is not None and type(selection) is not frozenset)):
                raise _resync()
            if s.source == "serving":
                runtime = self._runtime
                if runtime is None or s.keeper is None:
                    raise _resync()
                if runtime._context() != s.identity or self._serving_coordinates() != s.coordinates:
                    raise _resync()
                async with s.keeper.lease():
                    _, visible = await runtime._authorize(s.identity, "route", s.ceiling, visibility_only=True)
                    if (visible != s.ceiling or runtime._context() != s.identity
                            or self._serving_coordinates() != s.coordinates):
                        raise _resync()
            else:
                identity, coordinates, _, _, visible = self._definition(
                    s.graph_id, None, s.index, s.selection)
                if identity != s.identity or coordinates != s.coordinates or visible != s.ceiling:
                    raise _resync()
            self._alive(s.expires, self._version)
            if self._snapshots.get(sid) is not s:
                raise _resync()
            return self._response(sid, s, stream, offset)
        except BaseException as error:
            self._drop(sid)
            if isinstance(error, GraphError) and error.code not in ("too_large", "resource_busy"):
                raise _resync() from None
            raise

    def shutdown(self) -> None:
        self._check()
        self._closed = True
        self.sweep()