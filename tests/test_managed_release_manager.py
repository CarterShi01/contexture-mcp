"""Bounded lifecycle through real FileJournal/compiler/resource provider fixtures."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from contexture.application.managed_release_manager import ReleaseManager
from contexture.application.managed_releases import PROJECTION_NAME, release_projection
from contexture.core.graph.errors import GraphError
from contexture.server.managed_journal import FileJournal
from tests.test_managed_generation import Probe
from tests.test_managed_owner import SCHEMA, owner
from tests.test_managed_serving import events, system, uid  # noqa: F401


def manager(system: Any, **kwargs: Any) -> ReleaseManager:
    return ReleaseManager(system[3], graph_id="g", target_id="target",
        instance_id="replica", boot_id=uid(9000), provider=system[4], **kwargs)


def request(system: Any, epoch: int = 0, policy: str = "exact") -> dict[str, object]:
    snapshot = system[3].read("g")
    plan = system[4].compile(snapshot)
    return {"schema": "contexture.release-request/v1", "graph_id": "g",
        "target_id": "target", "revision": snapshot.revision,
        "definition_digest": snapshot.definition_digest,
        "manifest_digest": plan.manifest.manifest_digest,
        "expected_desired_epoch": str(epoch), "idempotency_key": f"release-{epoch}",
        "policy": policy, "reason": "bounded lifecycle", "approval_ref": "approved"}


async def idle(m: ReleaseManager) -> None:
    async with asyncio.timeout(3):
        while m.status().running_epoch is not None or m.status().pending_epoch is not None:
            await asyncio.sleep(0.001)


def block(system: Any) -> Probe:
    probe = Probe()
    probe.block_enter = "m"
    provider = system[4]
    original = provider.prepare
    async def prepare(captured):
        provider.probes[captured.attempt_id] = probe
        return await original(captured)
    provider.prepare = prepare
    return probe


@pytest.mark.asyncio
async def test_latest_coalesces_bounded_slot_exact_backpressure_and_late_ready(system):
    m = manager(system)
    probe = block(system)
    first = m.request(request(system, policy="latest"))
    await asyncio.wait_for(probe.entered.wait(), 2)
    second = m.request(request(system, 1, "latest"))
    third = m.request(request(system, 2, "exact"))
    assert m.status().running_epoch == "1" and m.status().pending_epoch == "3"
    assert system[4].prepare_calls == 1
    assert m.request(request(system, 2, "exact")) == third
    before = system[1].watermark
    with pytest.raises(GraphError, match="Exact pending"):
        m.request(request(system, 3))
    assert system[1].watermark == before
    probe.proceed.set()
    result = await m.wait(third, timeout=2)
    assert result.status == "observed" and result.local.readiness == "ready"
    assert system[4].prepare_calls == 2  # epoch 2 never acquires resources
    for receipt in (first, second):
        outcome = await m.wait(receipt, timeout=0.1)
        assert outcome.status == "superseded" and not outcome.observation_reached
    async with m.serving.request_lease() as lease:
        assert lease.release.epoch == "3"
    await m.shutdown()
    assert len(events(system, "ReleaseRequested")) == 3
    assert len(events(system, "ReleasePreparationSucceeded")) == 2
    assert len(events(system, "ReplicaObserved")) == 1
    assert system[4].handlers == dict.fromkeys(system[4].handlers, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_timeout_cancel_cleanup_explicit_retry_same_epoch(system, mode):
    m = manager(system, prepare_timeout=0.05 if mode == "timeout" else 10)
    probe = block(system)
    value = request(system)
    receipt = m.request(value)
    await asyncio.wait_for(probe.entered.wait(), 2)
    if mode == "cancel":
        assert m.cancel("1")
    await idle(m)
    assert probe.partial_released and probe.events[-2:] == ["close:a", "close:z"]
    status = m.status()
    assert status.readiness == "not_ready"
    assert status.last_attempt == ("1", "timed_out" if mode == "timeout" else "cancelled")
    calls = system[4].prepare_calls
    assert m.request(value) == receipt
    await asyncio.sleep(0)
    assert system[4].prepare_calls == calls  # retry is explicit, never an idempotency side effect
    # Nothing runs until an explicit retry: wait reports the failure at once.
    stopped = await m.wait(receipt, timeout=10)
    assert stopped.status == "failed" and stopped.receipt == receipt
    probe.proceed.set()
    assert m.retry_desired() == receipt
    assert (await m.wait(receipt, timeout=2)).status == "observed"
    assert len(events(system, "ReleaseRequested")) == 1
    assert len(events(system, "ReleasePreparationFailed")) == 1
    assert len(events(system, "ReleasePreparationSucceeded")) == 1
    await m.shutdown()


@pytest.mark.asyncio
async def test_failed_desired_keeps_actual_lkg_and_wait_cancel_does_not_cancel_work(system):
    m = manager(system)
    first = m.request(request(system))
    assert (await m.wait(first, timeout=2)).status == "observed"
    await idle(m)
    probe = block(system)
    second = m.request(request(system, 1))
    await asyncio.wait_for(probe.entered.wait(), 2)
    waiter = asyncio.create_task(m.wait(second, timeout=20))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert m.status().running_epoch == "2" and not probe.partial_released
    system[2].denied_operations.add("release.swap")
    probe.proceed.set()
    await idle(m)
    state = m.status()
    assert state.readiness == "lkg" and state.serving_epoch == "1" and state.desired_epoch == "2"
    assert state.observed and state.last_attempt == ("2", "failed")
    old = await m.wait(first, timeout=0.01)
    assert old.status == "superseded" and old.observation_reached and old.superseded_by == "2"
    failed = await m.wait(second, timeout=20)  # returns at once, not at the timeout
    assert failed.status == "failed" and failed.local.last_attempt == ("2", "failed")
    async with m.request_lease() as lease:
        assert lease.release.epoch == "1"
    await m.shutdown()


@pytest.mark.asyncio
async def test_new_boot_history_is_not_active_or_automatically_prepared(system):
    m = manager(system)
    receipt = m.request(request(system))
    await m.wait(receipt, timeout=2)
    await m.shutdown()
    calls = system[4].prepare_calls
    fresh = ReleaseManager(system[3], graph_id="g", target_id="target",
        instance_id="replica", boot_id=uid(9001), provider=system[4])
    assert fresh.status().readiness == "not_ready" and fresh.status().desired_epoch == "1"
    assert (await fresh.wait(receipt, timeout=0.01)).status == "pending"
    assert system[4].prepare_calls == calls
    with pytest.raises(GraphError):
        fresh.request_lease()
    assert fresh.retry_desired() == receipt
    assert (await fresh.wait(receipt, timeout=2)).status == "observed"
    await fresh.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("published", [False, True])
async def test_observation_unknown_keeps_pointer_and_recovers_real_journal(system, published):
    m = manager(system)
    receipt = m.request(request(system))
    count = 0
    def fault(step):
        nonlocal count
        if step == "record.before_rename":
            count += 1
            if count == 2 and not published:
                raise OSError("observation unpublished")
        if step == "record.renamed" and count == 2 and published:
            raise OSError("observation uncertain")
    system[1]._fault = fault
    await idle(m)
    state = m.status()
    assert state.readiness == "ready" and not state.observed
    assert state.last_attempt == ("1", "serving_unobserved")
    async with m.request_lease() as lease:
        assert lease.release.epoch == "1"
    system[3].close()
    system[1].close()
    with FileJournal.open(system[0], schema=SCHEMA) as reopened:
        recovered = owner(reopened, system[2], projections={PROJECTION_NAME: release_projection()})
        assert m.retry_observation(recovered_owner=recovered).status == "local_observed"
        assert (await m.wait(receipt, timeout=2)).status == "observed"
        assert len([e for r in reopened.records() for e in r["events"] if e["type"] == "ReplicaObserved"]) == 1
        await m.shutdown()
        recovered.close()


@pytest.mark.asyncio
async def test_shutdown_retains_real_leases_and_cancelled_join_does_not_lose_resource(system):
    m = manager(system)
    receipt = m.request(request(system))
    await m.wait(receipt, timeout=2)
    async with m.request_lease() as lease:
        shutdown = asyncio.create_task(m.shutdown())
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done() and len(lease.handles) == 3
        assert m.status().readiness == "not_ready"
        with pytest.raises(GraphError):
            m.request_lease()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    await m.shutdown()
    assert lease.generation.resource_state == "retired"
    assert not m.cleanup_errors


@pytest.mark.asyncio
async def test_pending_cancel_does_not_retract_durable_intent(system):
    m = manager(system)
    probe = block(system)
    m.request(request(system))
    await asyncio.wait_for(probe.entered.wait(), 2)
    second = m.request(request(system, 1))
    assert m.cancel("2") and not m.cancel("2")
    probe.proceed.set()
    await idle(m)
    assert m.status().readiness == "not_ready" and m.status().desired_epoch == "2"
    assert (await m.wait(second, timeout=0.01)).status == "pending"
    assert system[4].prepare_calls == 1
    await m.shutdown()


@pytest.mark.asyncio
async def test_timeout_keeps_capacity_until_real_cleanup_and_shutdown_joins(system):
    m = manager(system, prepare_timeout=0.05)
    probe = block(system)
    # Enter m blocks; cancellation must subsequently drain a's real exit.
    probe.block_close = "a"
    m.request(request(system))
    await asyncio.wait_for(probe.entered.wait(), 2)
    await asyncio.wait_for(probe.closing.wait(), 2)
    second = m.request(request(system, 1, "exact"))
    assert m.status().running_epoch == "1" and m.status().pending_epoch == "2"
    assert system[4].prepare_calls == 1
    shutdown = asyncio.create_task(m.shutdown())
    await asyncio.sleep(0)
    shutdown.cancel()
    await asyncio.sleep(0)
    assert not shutdown.done() and system[4].prepare_calls == 1
    probe.proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(shutdown, 2)
    await m.shutdown()
    assert probe.events[-2:] == ["close:a", "close:z"]
    assert (await m.wait(second, timeout=0.01)).status == "pending"
    assert m.serving.counts["preparing"] == m.serving.counts["retiring"] == 0


@pytest.mark.asyncio
async def test_unknown_during_ready_never_loses_lkg_and_refused_retry_is_truthful(system):
    m = manager(system)
    first = m.request(request(system))
    await m.wait(first, timeout=2)
    await idle(m)
    probe = block(system)
    second = m.request(request(system, 1))
    await asyncio.wait_for(probe.entered.wait(), 2)
    def fault(step):
        if step == "record.renamed":
            raise OSError("ready record uncertain")
    system[1]._fault = fault
    probe.proceed.set()
    await idle(m)
    assert system[3].writes_stopped and m.status().readiness == "lkg"
    assert m.status().serving_epoch == "1" and not (await m.wait(second, timeout=0.01)).observation_reached
    async with m.request_lease() as lease:
        assert lease.release.epoch == "1"
    m.retry_desired()
    await idle(m)
    assert m.status().last_attempt == ("2", "refused")
    # The cause stays observable: the journal needs recovery, not "busy".
    assert m.status().last_error == "commit_unknown"
    assert m.status().serving_epoch == "1"
    system[1]._fault = None
    await m.shutdown()
    assert probe.events[-3:] == ["close:m", "close:a", "close:z"]