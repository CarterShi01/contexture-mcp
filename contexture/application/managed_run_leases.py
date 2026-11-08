"""Bounded boot-local native RunLease port; NOT an accepted wire schema.

Only trusted SDK/Host code may construct envelopes or handle tokens. Neither
tokens nor resources are persisted. The service defines no wire representation
or durable Task provenance; transport adapters resolve explicit Host-issued
handles to its opaque envelopes. A token is lifetime routing,
never identity, approval or cached permission. Python privacy is not a sandbox.
"""

from __future__ import annotations

import asyncio
import math
import re
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from contexture.application.managed_generation import RetainedGeneration
from contexture.application.managed_serving import GenerationLease
from contexture.core.graph.errors import GraphError
from contexture.core.graph.policy import VerifiedContext

if TYPE_CHECKING:
    from contexture.application.managed_runtime import ManagedRuntime


def _denied() -> GraphError:
    return GraphError("permission_denied", "Run lease is unavailable or invalid")


class ExpiryHandle(Protocol):
    def cancel(self) -> None: ...


class ExpiryTimer(Protocol):
    """Trusted call_later-style port: enqueue, never invoke inline; cancel is safe."""

    def __call__(self, delay: float, callback: Callable[[], None]) -> ExpiryHandle: ...


@dataclass(frozen=True, slots=True, repr=False)
class RunLeaseContext:
    """Opaque trusted native envelope, never a business Tool argument.

    Construct only in a trusted SDK/Host adapter. The token must originate from
    this service's cryptographically secure issuer, not caller-chosen run IDs.
    Rehydrating an envelope does not validate its identity or boot association.
    """

    _service: RunLeaseService = field(repr=False)
    _token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (type(self._token) is not str or len(self._token) != 43
                or re.fullmatch(r"[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]", self._token) is None):
            raise _denied()

    def __repr__(self) -> str:
        return "<RunLeaseContext opaque>"


@dataclass(slots=True, repr=False)
class _Run:
    lease: GenerationLease
    keeper: RetainedGeneration
    identity: VerifiedContext
    coordinates: tuple[str, str, str]
    anchors: frozenset[str]
    ceiling: frozenset[str]
    authority_view: str
    expires: float
    timer: ExpiryHandle | None = None
    timer_version: int = 0


class RunLeaseService:
    """Explicit create/renew/release/upgrade/sweep local API on one event loop.

    Default expiration uses loop.time and loop.call_later, with no background
    tasks or asynchronous cleanup to leak. Injected clocks MUST be monotonic;
    without an explicitly supplied timer they require explicit sweep(). Tokens
    and pending creates share max_runs capacity. Resource retirement remains
    owned by serving; shutdown this service before coordinator.shutdown().
    """

    def __init__(self, runtime: ManagedRuntime, *, max_runs: int = 128,
                 max_ttl: float = 300, clock: Callable[[], float] | None = None,
                 timer: ExpiryTimer | None = None) -> None:
        if type(max_runs) is not int or max_runs < 1:
            raise ValueError("Positive run capacity required")
        if type(max_ttl) not in (int, float) or not math.isfinite(max_ttl) or max_ttl <= 0:
            raise ValueError("Positive finite maximum TTL required")
        self._runtime = runtime
        self._loop = asyncio.get_running_loop()
        self._clock = self._loop.time if clock is None else clock
        self._timer = (self._loop.call_later if clock is None else None) if timer is None else timer
        self.max_runs, self.max_ttl = max_runs, max_ttl
        coordinator = runtime._coordinator
        self._coordinates = (coordinator.graph_id, coordinator.target_id, coordinator.boot_id)
        self._runs: dict[str, _Run] = {}
        self._pending = 0
        self._closed = False

    def _check(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise _denied()

    @property
    def requires_explicit_sweep(self) -> bool:
        return self._timer is None

    @property
    def counts(self) -> dict[str, int]:
        self._check()
        return {"runs": len(self._runs), "pending": self._pending,
                "timers": sum(run.timer is not None for run in self._runs.values())}

    def _ttl(self, ttl: float) -> float:
        if (type(ttl) not in (int, float) or not math.isfinite(ttl)
                or not 0 < ttl <= self.max_ttl):
            raise GraphError("schema_invalid", "Run TTL outside configured bounds")
        return float(ttl)

    def _drop(self, token: str) -> None:
        run = self._runs.pop(token, None)
        if run is not None:
            if run.timer is not None:
                run.timer.cancel()
                run.timer = None
            run.keeper.close()

    def sweep(self) -> int:
        """Expire keepers synchronously; never close in-flight child resources."""
        self._check()
        expired = [token for token, run in self._runs.items() if self._clock() >= run.expires]
        for token in expired:
            self._drop(token)
        return len(expired)

    def _arm(self, token: str, run: _Run) -> None:
        run.timer_version += 1
        version = run.timer_version
        if run.timer is not None:
            run.timer.cancel()
            run.timer = None
        if self._timer is not None:
            # One owned callback per live run; re-arm only if a trusted timer
            # fired early. No tasks, async exceptions, or token-bearing errors.
            def expire() -> None:
                if self._runs.get(token) is not run or run.timer_version != version:
                    return
                if self._clock() >= run.expires:
                    self._drop(token)
                else:
                    self._arm(token, run)
            run.timer = self._timer(max(0, run.expires - self._clock()), expire)

    def _validate(self, envelope: RunLeaseContext) -> _Run:
        self._check()
        if type(envelope) is not RunLeaseContext or envelope._service is not self:
            raise _denied()
        run = self._runs.get(envelope._token)
        coordinator = self._runtime._coordinator
        coordinates = (coordinator.graph_id, coordinator.target_id, coordinator.boot_id)
        identity = self._runtime._context()
        if (self._closed or coordinator._closed or run is None or coordinates != self._coordinates
                or run.coordinates != coordinates
                or (identity.actor_id, identity.tenant_id) !=
                   (run.identity.actor_id, run.identity.tenant_id)):
            raise _denied()
        if self._clock() >= run.expires:
            self._drop(envelope._token)
            raise _denied()
        return run

    async def create(self, *, ttl: float, selection: frozenset[str] | None = None
                     ) -> RunLeaseContext:
        self._check()
        duration = self._ttl(ttl)
        self.sweep()
        coordinator = self._runtime._coordinator
        if (coordinator.graph_id, coordinator.target_id, coordinator.boot_id) != self._coordinates:
            raise _denied()
        if self._closed or len(self._runs) + self._pending >= self.max_runs:
            raise GraphError("resource_busy", "Run lease capacity unavailable")
        self._pending += 1
        keeper: RetainedGeneration | None = None
        token: str | None = None
        try:
            identity = self._runtime._context()
            # Pin, exact adapter preflight and retain BEFORE external auth await.
            async with self._runtime._coordinator.request_lease() as lease:
                self._runtime._preflight(lease)
                anchors = (frozenset(lease.generation.plan.index.preorder)
                           if selection is None else frozenset(selection))
                selected = self._runtime._selection(lease, selection)
                keeper = lease.generation.retain()
                context, ceiling = await self._runtime._authorize(identity, "run.create", selected)
                if (self._closed or coordinator._closed or
                    (coordinator.graph_id, coordinator.target_id, coordinator.boot_id) != self._coordinates):
                    raise _denied()
                token = secrets.token_urlsafe(32)
                while token in self._runs:
                    token = secrets.token_urlsafe(32)
                envelope = RunLeaseContext(self, token)
                run = _Run(lease, keeper, identity, self._coordinates, anchors,
                           ceiling, context.authority_revision, self._clock() + duration)
                self._runs[token] = run
                self._arm(token, run)
                keeper = None  # mapping now owns the keeper
                return envelope
        except BaseException:
            if token is not None:
                self._drop(token)
            raise
        finally:
            if keeper is not None:
                keeper.close()
            self._pending -= 1

    async def renew(self, envelope: RunLeaseContext, *, ttl: float) -> RunLeaseContext:
        duration = self._ttl(ttl)
        run = self._validate(envelope)
        await self._runtime._authorize(run.identity, "run.renew", run.ceiling)
        self._validate(envelope)  # expiry/release during policy await cannot resurrect
        run.expires = self._clock() + duration
        try:
            self._arm(envelope._token, run)
        except BaseException:
            self._drop(envelope._token)
            raise
        return envelope

    def release(self, envelope: RunLeaseContext) -> None:
        self._validate(envelope)
        self._drop(envelope._token)

    async def upgrade(self, envelope: RunLeaseContext, *, ttl: float) -> RunLeaseContext:
        """Fresh active pin/current admission, no historical approval migration.

        Old lease remains valid if creation fails. Replacement needs spare
        capacity; no hidden over-capacity slot or destructive pre-release.
        """
        run = self._validate(envelope)
        replacement = await self.create(ttl=ttl, selection=run.anchors)
        try:
            self._validate(envelope)
        except BaseException:
            self._drop(replacement._token)
            raise
        self._drop(envelope._token)
        return replacement

    @asynccontextmanager
    async def _request(self, envelope: RunLeaseContext, runtime: ManagedRuntime
                       ) -> AsyncIterator[GenerationLease]:
        if runtime is not self._runtime:
            raise _denied()
        run = self._validate(envelope)
        async with run.keeper.lease() as handles:
            yield GenerationLease(run.lease.generation, handles, run.lease.release)

    def shutdown(self) -> None:
        self._check()
        self._closed = True
        for token in tuple(self._runs):
            self._drop(token)