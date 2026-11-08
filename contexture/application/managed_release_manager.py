"""Bounded local WP08 scheduling over the real intent/serving coordinators.

This trusted Python adapter is not a wire API or fleet authority. Construction
never prepares history. Call request() or explicitly retry_desired() to opt in.
Only one preparation task and one replaceable pending slot exist. Exact work is
never silently coalesced; explicit accepted successor intents supersede it.

Candidate schema gap: start/pending/consumed records have no accepted Event
representation. Scheduling/cancellation/deadlines below are volatile, not
invented durable attempt events. Real coordinators own all durable facts, current
authority checks, late-ready fencing, resources, leases and observation recovery.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
import math
from typing import Literal, cast
from uuid import uuid4

from contexture.application.managed import GraphOwner
from contexture.application.managed_generation import _settle
from contexture.application.managed_releases import ReleaseIntentCoordinator
from contexture.application.managed_serving import (
    GenerationLease, LocalServingCoordinator, LocalServingResult, ServingPreparationProvider,
)
from contexture.core.graph.errors import GraphError
from contexture.core.graph.releases import ReleaseReceipt, ReleaseRequest


def _duration(value: float) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("A finite positive duration is required")
    return value


@dataclass(frozen=True, slots=True)
class ReleaseManagerStatus:
    """Current local pointer, not readiness inferred from historical observations."""

    instance_id: str
    boot_id: str
    desired_epoch: str | None
    serving_epoch: str | None
    generation_id: str | None
    readiness: Literal["not_ready", "ready", "lkg"]
    observed: bool
    running_epoch: str | None
    pending_epoch: str | None
    last_attempt: tuple[str, str] | None
    closed: bool


@dataclass(frozen=True, slots=True)
class ReleaseWaitResult:
    receipt: ReleaseReceipt
    status: Literal["observed", "pending", "superseded"]
    observation_reached: bool
    superseded_by: str | None
    local: ReleaseManagerStatus


@dataclass(slots=True)
class _Work:
    receipt: ReleaseReceipt
    request: ReleaseRequest

    @property
    def epoch(self) -> str:
        return cast(dict[str, str], self.receipt.as_json()["release"])["release_epoch"]


class ReleaseManager:
    """One loop/target, bounded admission; no unbounded task or retry queue.

    Waiting/caller cancellation never cancels durable intent. cancel() explicitly
    cancels local work only. Timeouts are cooperative: capacity stays reserved
    until actual adapter cleanup finishes, even if it exceeds the deadline.
    Existing serving resources are LKG, never silently loaded from history.
    """

    def __init__(self, owner: GraphOwner, *, graph_id: str, target_id: str,
                 instance_id: str, boot_id: str, provider: ServingPreparationProvider,
                 prepare_timeout: float = 30, max_retirements: int = 8):
        self.prepare_timeout = _duration(prepare_timeout)
        self._serving = LocalServingCoordinator(owner, graph_id=graph_id,
            target_id=target_id, instance_id=instance_id, boot_id=boot_id,
            provider=provider, max_preparations=1, max_retirements=max_retirements)
        self._intents = ReleaseIntentCoordinator(owner, manifests=provider)
        self._loop = asyncio.get_running_loop()
        self._running: _Work | None = None
        self._pending: _Work | None = None
        self._driver: asyncio.Task[None] | None = None
        self._attempt: asyncio.Task[LocalServingResult] | None = None
        self._active: LocalServingResult | None = None
        self._last: tuple[str, str] | None = None
        self._cancel_reason: str | None = None
        self._scheduled_epoch = 0
        self._changed = asyncio.Event()
        self._closed = False
        self._shutdown: asyncio.Task[None] | None = None

    def _check(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise GraphError("resource_busy", "Manager belongs to another event loop")

    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def _history(self) -> dict[str, object]:
        self._check()
        return self._intents.status(self._serving.graph_id, self._serving.target_id)

    @staticmethod
    def _desired(history: dict[str, object]) -> tuple[ReleaseRequest, str] | None:
        items = cast(list[dict[str, object]], history["desired_releases"])
        if not items:
            return None
        return ReleaseRequest.from_json(items[0]["request"]), cast(str, items[0]["release_epoch"])

    def request(self, value: object) -> ReleaseReceipt:
        """Persist via real coordinator, then schedule without an await gap.

        A pending exact slot backpressures acceptance of another request. A
        latest slot can be replaced only by explicit durable successor intent.
        Same-key retries return the original receipt without duplicate work.
        """
        self._check()
        if self._closed:
            raise GraphError("resource_busy", "Release manager is closed")
        request = ReleaseRequest.from_json(value)
        if (request.graph_id, request.target_id) != (self._serving.graph_id, self._serving.target_id):
            raise GraphError("contract_mismatch", "Release target differs")
        for work in (self._running, self._pending):
            if work is not None and work.request.idempotency_key == request.idempotency_key:
                return self._intents.request(value)
        if self._pending is not None and self._pending.request.policy == "exact":
            raise GraphError("resource_busy", "Exact pending slot is occupied")
        receipt = self._intents.request(value)
        desired = self._desired(self._history())
        work = _Work(receipt, request)
        # An old same-key durable receipt must never reschedule current desired.
        if desired is not None and desired[1] == work.epoch and int(work.epoch) > self._scheduled_epoch:
            if self._active is None or self._active.captured.epoch != work.epoch:
                self._schedule(work)
        self._notify()
        return receipt

    def _schedule(self, work: _Work) -> None:
        self._scheduled_epoch = max(self._scheduled_epoch, int(work.epoch))
        self._pending = work
        if self._driver is None or self._driver.done():
            self._driver = asyncio.create_task(self._run())

    def retry_desired(self) -> ReleaseReceipt:
        """Explicit fresh attempt, same durable epoch; never automatic startup."""
        self._check()
        if self._closed or self._running is not None or self._pending is not None:
            raise GraphError("resource_busy", "Release work is already scheduled or closed")
        history = self._history()
        desired = self._desired(history)
        if desired is None:
            raise GraphError("not_ready", "No desired release")
        request, epoch = desired
        operations = cast(list[dict[str, object]], history["release_operations"])
        receipt = next(ReleaseReceipt.from_json(item) for item in operations
            if item["status"] == "requested"
            and cast(dict[str, str], item["release"])["release_epoch"] == epoch)
        self._schedule(_Work(receipt, request))
        self._notify()
        return receipt

    async def _run(self) -> None:
        while self._pending is not None and not self._closed:
            work, self._pending = self._pending, None
            self._running, self._cancel_reason = work, None
            self._notify()
            timer: asyncio.TimerHandle | None = None
            try:
                desired = self._desired(self._history())
                if desired is None or desired[1] != work.epoch:
                    self._last = (work.epoch, "superseded")
                    continue
                self._attempt = asyncio.create_task(self._serving.prepare(
                    attempt_id=str(uuid4()), generation_id=str(uuid4())))
                def expired() -> None:
                    if (self._attempt is not None and not self._attempt.done()
                            and self._cancel_reason is None):
                        self._cancel_reason = "timed_out"
                        self._attempt.cancel()
                        self._notify()
                timer = self._loop.call_later(self.prepare_timeout, expired)
                result = await self._attempt
                if result.status in ("local_observed", "serving_unobserved"):
                    self._active = result
                self._last = (work.epoch, result.status)
            except asyncio.CancelledError:
                self._last = (work.epoch, self._cancel_reason or "cancelled")
            except Exception:
                # Pre-admission/capacity refusal is not durable failed evidence.
                self._last = (work.epoch, "refused")
            finally:
                if timer is not None:
                    timer.cancel()
                self._attempt = None
                self._running = None
                self._notify()

    def cancel(self, epoch: str) -> bool:
        """Cancel local pending/in-flight work; does not retract accepted intent."""
        self._check()
        if self._pending is not None and self._pending.epoch == epoch:
            self._pending = None
            self._last = (epoch, "cancelled")
            self._notify()
            return True
        if self._running is not None and self._running.epoch == epoch:
            if self._attempt is not None and not self._attempt.done():
                self._cancel_reason = "cancelled"
                self._attempt.cancel()
                self._notify()
                return True
        return False

    def status(self) -> ReleaseManagerStatus:
        """Authorized local diagnostic; LKG means retained actual serving only."""
        history = self._history()
        desired = self._desired(history)
        epoch = None if desired is None else desired[1]
        active = None if self._closed else self._active
        serving_epoch = None if active is None else active.captured.epoch
        return ReleaseManagerStatus(self._serving.instance_id, self._serving.boot_id,
            epoch, serving_epoch, None if active is None else active.captured.generation_id,
            "not_ready" if active is None else "ready" if serving_epoch == epoch else "lkg",
            active is not None and active.status == "local_observed",
            None if self._running is None else self._running.epoch,
            None if self._pending is None else self._pending.epoch, self._last, self._closed)

    async def wait(self, receipt: ReleaseReceipt, *, timeout: float) -> ReleaseWaitResult:
        """Wait only for this instance/current boot; timeout is pending+receipt.

        Historical observation is reported independently of supersession/current
        readiness. No fleet/all_of predicate is fabricated without an adapter.
        External durable changes are sampled at a bounded interval, not busy-spun.
        """
        self._check()
        duration = _duration(timeout)
        identity = cast(dict[str, str], receipt.as_json()["release"])
        if (identity["graph_id"], identity["target_id"]) != (
                self._serving.graph_id, self._serving.target_id):
            raise GraphError("contract_mismatch", "Wait target differs")
        deadline = self._loop.time() + duration
        while True:
            changed = self._changed
            history = self._history()
            operations = cast(list[dict[str, object]], history["release_operations"])
            if not any(item == receipt.as_json() for item in operations):
                raise GraphError("contract_mismatch", "Wait receipt is not accepted")
            observations = cast(list[dict[str, object]], history["observations_history"])
            reached = any(item["release"] == identity
                and item["instance_id"] == self._serving.instance_id
                and item["boot_id"] == self._serving.boot_id for item in observations)
            successor = next((cast(str, item["superseded_by"]) for item in operations
                if item["release"] == identity and item["status"] == "superseded"), None)
            local = self.status()
            remaining = deadline - self._loop.time()
            if successor is not None or reached or remaining <= 0 or self._closed:
                state = "superseded" if successor is not None else "observed" if reached else "pending"
                return ReleaseWaitResult(receipt, state, reached, successor, local)
            try:
                await asyncio.wait_for(changed.wait(), min(remaining, 0.1))
            except TimeoutError:
                pass

    def retry_observation(self, *, recovered_owner: GraphOwner | None = None) -> LocalServingResult:
        self._check()
        result = self._serving.retry_observation(recovered_owner=recovered_owner)
        if result.status == "local_observed":
            self._active = result
            self._intents = ReleaseIntentCoordinator(self._serving.owner, manifests=self._serving.provider)
        self._notify()
        return result

    def request_lease(self) -> AbstractAsyncContextManager[GenerationLease]:
        """Actual existing serving provider; not permission or handler dispatch."""
        self._check()
        if self._closed:
            raise GraphError("not_ready", "Release manager is closed")
        return self._serving.request_lease()

    @property
    def serving(self) -> LocalServingCoordinator:
        """Existing public provider for ManagedRuntime/RunLease deployment wiring.

        Trusted deployment must not independently prepare/shutdown this owned
        coordinator; lifecycle ownership remains with this manager.
        """
        self._check()
        return self._serving

    @property
    def cleanup_errors(self) -> tuple[BaseException, ...]:
        return self._serving.cleanup_errors

    async def shutdown(self) -> None:
        """Stop admission and join real cleanup, preserving existing leases.

        Caller cancellation cannot detach owned cleanup or force-close resources.
        A stuck adapter/lease keeps shutdown pending, rather than inventing close.
        """
        self._check()
        if self._shutdown is None:
            self._closed = True
            self._pending = None
            self._notify()
            if self._attempt is not None and not self._attempt.done():
                self._cancel_reason = "cancelled"
                self._attempt.cancel()
            async def drain() -> None:
                await self._serving.shutdown()
                if self._driver is not None:
                    await self._driver
            self._shutdown = asyncio.create_task(drain())
        await _settle(self._shutdown)